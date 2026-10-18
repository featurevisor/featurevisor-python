from __future__ import annotations

import gc
import sys
import unittest
import weakref
from unittest.mock import Mock, patch

sys.path.insert(0, "src")

from featurevisor import create_featurevisor
from featurevisor._evaluation_session import EvaluationSession, refresh_evaluation_state
from featurevisor.dependencies import create_datafile_dependency_index


def datafile(revision: str = "1", conditions: str | None = None) -> dict:
    segment_conditions = conditions or '[{"attribute":"country","operator":"equals","value":"nl"}]'
    return {
        "schemaVersion": "2",
        "revision": revision,
        "segments": {
            "country": {"key": "country", "conditions": segment_conditions},
        },
        "features": {
            "enabled": {
                "key": "enabled",
                "bucketBy": "userId",
                "traffic": [
                    {
                        "key": "country-rule",
                        "segments": '["country"]',
                        "percentage": 100000,
                    }
                ],
            },
            "secondary": {
                "key": "secondary",
                "bucketBy": "userId",
                "traffic": [{"key": "all", "segments": "*", "percentage": 100000}],
            },
        },
        "variables": {
            "message": {"type": "string", "defaultValue": "hello"},
        },
    }


class EvaluationPerformanceContractTests(unittest.TestCase):
    def test_outer_global_reads_do_not_probe_dependency_memo_even_on_error(self):
        sdk = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        self.addCleanup(sdk.close)
        with patch.object(EvaluationSession, "key", side_effect=AssertionError("outer memo probe")):
            self.assertEqual(sdk.get_variable("message"), "hello")
            self.assertEqual(sdk.get_variable("missing", {}, {"defaultVariableValue": "fallback"}), "fallback")
            sdk.add_module({"beforeEvaluation": lambda options: {**options, "variableKey": "missing"}})
            self.assertEqual(sdk.evaluate_variable("message")["reason"], "variable_not_found")
            sdk.set_datafile({"schemaVersion": "4", "revision": "error", "segments": {}, "features": {},
                             "variables": {"missing": {"type": "string", "conditions": {"attribute": "x", "operator": "equals", "value": 1}}}})
            failure = ValueError("evaluation failed")
            with patch.object(sdk, "_conditions_are_matched", side_effect=failure):
                evaluation = sdk.evaluate_variable("message")
            self.assertEqual(evaluation["reason"], "error")
            self.assertIs(evaluation["error"], failure)

    def test_unchanged_state_skips_sources_but_preserves_module_inputs(self):
        generation = [0]
        maps = [{}, {}]
        provider = [object()]
        sticky_source = Mock(side_effect=lambda: tuple(maps))
        datafile_source = Mock(side_effect=lambda: provider[0])
        options = {"__featurevisor_state_version": lambda: generation[0],
                   "__featurevisor_child_sticky_source": sticky_source,
                   "__featurevisor_child_initial_sticky": tuple(maps),
                   "__featurevisor_datafile_source": datafile_source,
                   "__featurevisor_initial_datafile": provider[0],
                   "datafile": provider[0], "stickyFeatures": maps[0], "stickyVariables": maps[1],
                   "context": {"nested": {"value": 1}}}
        refresh_evaluation_state(options)
        for _ in range(10):
            refresh_evaluation_state(options)
        self.assertEqual(sticky_source.call_count, 1)
        self.assertEqual(datafile_source.call_count, 1)
        replacement = {"module": True}
        options["stickyVariables"] = replacement
        options["context"]["nested"]["value"] = 2
        refresh_evaluation_state(options)
        self.assertIs(options["stickyVariables"], replacement)
        self.assertEqual(options["context"]["nested"]["value"], 2)
        self.assertEqual(sticky_source.call_count, 1)
        generation[0] += 1
        maps[:] = [{"updated": True}, {"owner": True}]
        provider[0] = object()
        refresh_evaluation_state(options)
        self.assertIs(options["datafile"], provider[0])
        self.assertIs(options["stickyFeatures"], maps[0])
        self.assertIs(options["stickyVariables"], replacement)
        self.assertEqual(sticky_source.call_count, 2)
        self.assertEqual(datafile_source.call_count, 2)

    def test_initial_load_skips_dependency_indexes_without_losing_future_propagation(self):
        for construct in (False, True):
            with self.subTest(construct=construct):
                initial = {"schemaVersion": "4", "revision": "1", "segments": {
                    "gate": {"conditions": '{"variable":"source","operator":"notNull"}'}},
                    "features": {"root": {"conditions": "gate", "hash": "same"}},
                    "variables": {"source": {"type": "integer", "defaultValue": 1, "hash": "1"},
                                  "alias": {"type": "integer", "compose": "source", "hash": "same"}}}
                with patch("featurevisor.events.create_datafile_dependency_index", wraps=create_datafile_dependency_index) as index:
                    sdk = create_featurevisor({"logLevel": "fatal", **({"datafile": initial} if construct else {})})
                    self.addCleanup(sdk.close)
                    events = []
                    sdk.on("datafile_set", events.append)
                    if not construct:
                        self.assertTrue(sdk.set_datafile(initial))
                        self.assertEqual(events[-1]["features"], ["root"])
                        self.assertEqual(events[-1]["variables"], ["source", "alias"])
                    index.assert_not_called()
                    self.assertEqual(sdk.datafile.entity_cache, {})
                    self.assertTrue(sdk.set_datafile({"schemaVersion": "4", "revision": "2", "segments": {},
                        "features": {}, "variables": {"source": {"type": "integer", "defaultValue": 2, "hash": "2"}}}))
                    self.assertEqual(index.call_count, 1)
                    self.assertEqual(events[-1]["features"], ["root"])
                    self.assertEqual(set(events[-1]["variables"]), {"source", "alias"})
                    self.assertEqual(sdk.datafile.entity_cache, {})

    def test_empty_module_dispatch_and_module_added_during_evaluation(self) -> None:
        sdk = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        with patch.object(sdk.modules_manager, "run_before_evaluation_modules") as before, patch.object(
            sdk.modules_manager, "run_after_evaluation_modules"
        ) as after:
            self.assertTrue(sdk.is_enabled("enabled", {"country": "nl", "userId": "one"}))
            self.assertEqual(sdk.get_variable("message"), "hello")
            before.assert_not_called()
            after.assert_not_called()
        sdk.close()

        received = []
        holder = {}

        def on_diagnostic(diagnostic: dict) -> None:
            if diagnostic["message"] == "using sticky enabled" and not received:
                holder["sdk"].add_module({
                    "afterEvaluation": lambda evaluation, options: (
                        received.append(options) or evaluation
                    ),
                })

        sdk = create_featurevisor({
            "datafile": datafile(), "logLevel": "debug",
            "stickyFeatures": {"enabled": {"enabled": True}},
            "onDiagnostic": on_diagnostic,
        })
        holder["sdk"] = sdk
        self.assertTrue(sdk.is_enabled("enabled"))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["featureKey"], "enabled")
        self.assertNotIn("_evaluationSession", received[0])
        self.assertFalse(any(key.startswith("__featurevisor_") for key in received[0]))
        sdk.close()

        fixture = datafile()
        fixture["variables"]["message"]["deprecated"] = True
        received.clear()
        holder.clear()

        def on_variable_diagnostic(diagnostic: dict) -> None:
            if diagnostic["code"] == "deprecated_variable":
                holder["sdk"].add_module({
                    "afterEvaluation": lambda evaluation, options: (
                        received.append(options) or evaluation
                    ),
                })

        sdk = create_featurevisor({
            "datafile": fixture, "logLevel": "warn", "onDiagnostic": on_variable_diagnostic,
        })
        holder["sdk"] = sdk
        self.assertEqual(sdk.get_variable("message"), "hello")
        self.assertEqual(len(received), 1)
        self.assertNotIn("__featurevisor_state_version", received[0])
        sdk.close()

    def test_expression_cache_follows_datafile_update_and_close_lifecycle(self) -> None:
        sdk = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        first = sdk.datafile
        self.assertTrue(sdk.is_enabled("enabled", {"country": "nl", "userId": "one"}))
        self.assertIn(("feature", "enabled"), first.entity_cache)
        self.assertIn(("segment", "country"), first.entity_cache)
        sdk.set_datafile("{")
        self.assertIs(sdk.datafile, first)
        normalized = first.entity_cache[("feature", "enabled")]
        sdk.set_datafile({"schemaVersion": "4", "revision": "partial", "segments": {}, "features": {},
                          "variables": {"new": {"type": "integer", "defaultValue": 1}}})
        self.assertIs(sdk.datafile.entity_cache[("feature", "enabled")], normalized)
        sdk.set_datafile(datafile("2"), True)
        self.assertEqual(sdk.datafile.entity_cache, {})
        self.assertTrue(sdk.is_enabled("enabled", {"country": "nl", "userId": "three"}))
        sdk.close()
        self.assertEqual(sdk.datafile.entity_cache, {})
        self.assertEqual(sdk.datafile.prepared_conditions, {})
        self.assertEqual(sdk.datafile.regex_cache, {})

    def test_feature_override_preparation_caches_syntax_but_not_context_decisions(self) -> None:
        conditions = '[{"attribute":"country","operator":"equals","value":"nl"}]'
        fixture = datafile()
        fixture["features"]["enabled"]["variablesSchema"] = {
            "message": {
                "type": "string",
                "defaultValue": "default",
            }
        }
        fixture["features"]["enabled"]["traffic"][0]["segments"] = "*"
        fixture["features"]["enabled"]["traffic"][0]["variableOverrides"] = {
            "message": [{"conditions": conditions, "value": "matched"}]
        }
        sdk = create_featurevisor({"datafile": fixture, "logLevel": "fatal"})

        self.assertEqual(
            sdk.get_variable("enabled", "message", {"country": "nl", "userId": "one"}),
            "matched",
        )
        self.assertIn(("feature", "enabled"), sdk.datafile.entity_cache)
        self.assertTrue(sdk.datafile.prepared_conditions)
        self.assertEqual(
            sdk.get_variable("enabled", "message", {"country": "de", "userId": "two"}),
            "default",
        )

        broken = datafile("broken")
        broken["features"]["enabled"]["variablesSchema"] = {
            "message": {
                "type": "string",
                "defaultValue": "default",
            }
        }
        broken["features"]["enabled"]["traffic"][0]["segments"] = "*"
        broken["features"]["enabled"]["traffic"][0]["variableOverrides"] = {
            "message": [{"conditions": "{", "value": "matched"}]
        }
        diagnostics = []
        sdk = create_featurevisor(
            {"datafile": broken, "logLevel": "fatal", "onDiagnostic": diagnostics.append}
        )
        sdk.on("error", lambda event: diagnostics.append(event["diagnostic"]))
        self.assertEqual(sdk.get_variable("enabled", "message", {"userId": "one"}), "default")
        self.assertEqual(sdk.get_variable("enabled", "message", {"userId": "two"}), "default")
        self.assertNotIn("{", sdk.datafile.expression_cache)
        self.assertEqual([item["code"] for item in diagnostics].count("evaluation_error"), 0)

    def test_closed_instance_with_provider_caches_is_released(self) -> None:
        gc.collect()
        sdk = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        self.assertTrue(sdk.is_enabled("enabled", {"country": "nl", "userId": "warm"}))

        replacement = datafile("replacement")
        replacement["features"]["enabled"]["traffic"][0]["segments"] = "*"
        sdk.set_datafile(replacement, True)
        sdk.add_module({
            "name": "dynamic",
            "beforeEvaluation": lambda options: options,
        })
        self.assertTrue(sdk.is_enabled("enabled", {"userId": "after"}))

        reference = weakref.ref(sdk)
        sdk.close()
        del sdk
        gc.collect()

        self.assertIsNone(reference())

    def test_private_cache_does_not_alias_public_segments_with_identical_definitions(self) -> None:
        shared_conditions = '[{"attribute":"country","operator":"equals","value":"nl"}]'
        fixture = datafile()
        fixture["schemaVersion"] = "4"
        fixture["segments"] = {
            "first": {"conditions": shared_conditions},
            "second": {"conditions": shared_conditions},
        }
        fixture["features"]["enabled"]["traffic"][0]["segments"] = '["second"]'
        sdk = create_featurevisor({"datafile": fixture, "logLevel": "fatal"})
        self.assertTrue(sdk.is_enabled("enabled", {"country": "nl", "userId": "one"}))
        self.assertIsInstance(sdk.datafile.segments["first"]["conditions"], str)
        self.assertIsInstance(sdk.datafile.segments["second"]["conditions"], str)
        first = sdk.get_segment("first")
        second = sdk.get_segment("second")
        self.assertIsNot(first["conditions"], second["conditions"])
        self.assertEqual(fixture["segments"]["first"]["conditions"], shared_conditions)
        self.assertEqual(fixture["segments"]["second"]["conditions"], shared_conditions)

    def test_diagnostic_callback_cannot_poison_nested_cached_conditions(self) -> None:
        cached_conditions = (
            '{"and":[{"attribute":"name","operator":"matches","value":"[",'
            '"meta":{"nested":{"poisoned":false}}}]}'
        )
        fixture = datafile()
        fixture["features"]["enabled"]["force"] = [
            {"conditions": cached_conditions, "enabled": True}
        ]
        fixture["features"]["enabled"]["traffic"] = []
        diagnostics = []

        def on_diagnostic(diagnostic: dict) -> None:
            if diagnostic["code"] != "condition_match_error":
                return
            diagnostics.append(diagnostic)
            condition = diagnostic["details"]["condition"]
            condition["value"] = ".*"
            condition["meta"]["nested"]["poisoned"] = True

        sdk = create_featurevisor(
            {
                "datafile": fixture,
                "logLevel": "warn",
                "onDiagnostic": on_diagnostic,
            }
        )
        context = {"name": "anything", "userId": "one"}
        self.assertFalse(sdk.is_enabled("enabled", context))
        self.assertFalse(sdk.is_enabled("enabled", context))
        self.assertEqual(len(diagnostics), 2)

        cached = sdk.get_feature("enabled")["force"][0]["conditions"]
        self.assertEqual(cached["and"][0]["value"], "[")
        self.assertFalse(cached["and"][0]["meta"]["nested"]["poisoned"])

    def test_parse_failures_report_once_per_load_and_allow_reentrant_evaluation(self) -> None:
        sdk = create_featurevisor({"logLevel": "error"})
        diagnostics = []
        reentered = []

        def on_error(event: dict) -> None:
            diagnostic = event["diagnostic"]
            diagnostics.append(diagnostic)
            if diagnostic["code"] == "conditions_parse_error" and not reentered:
                reentered.append(sdk.is_enabled("secondary", {"userId": "nested"}))

        sdk.on("error", on_error)
        sdk.set_datafile(datafile(conditions="{"))
        self.assertFalse(sdk.is_enabled("enabled", {"country": "nl", "userId": "one"}))
        self.assertFalse(sdk.is_enabled("enabled", {"country": "nl", "userId": "two"}))
        self.assertEqual(reentered, [True])
        errors = [item for item in diagnostics if item["code"] == "conditions_parse_error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["details"], {"conditions": "{", "entityType": "segment", "entityKey": "country"})

    def test_callback_changes_to_threshold_and_subscriptions_are_live(self) -> None:
        instance_diagnostics = []
        module_diagnostics = []
        sdk_holder = {}
        subscribe_holder = {}
        subscribed = False

        def setup(api: dict) -> None:
            subscribe_holder["subscribe"] = api["onDiagnostic"]

        def before(options: dict) -> dict:
            nonlocal subscribed
            sdk_holder["sdk"].set_log_level("debug")
            if not subscribed:
                subscribe_holder["subscribe"](
                    module_diagnostics.append,
                    {"logLevel": "debug"},
                )
                subscribed = True
            return options

        sdk = create_featurevisor(
            {
                "datafile": datafile(),
                "logLevel": "fatal",
                "onDiagnostic": instance_diagnostics.append,
                "modules": [{"name": "live", "setup": setup, "beforeEvaluation": before}],
            }
        )
        sdk_holder["sdk"] = sdk

        evaluation = sdk.evaluate_flag("enabled", {"country": "nl", "userId": "one"})
        self.assertEqual(evaluation["reason"], "rule")
        self.assertEqual(instance_diagnostics[-1]["code"], "rule")
        self.assertEqual(module_diagnostics[-1]["code"], "rule")
        self.assertEqual(instance_diagnostics[-1]["details"]["evaluation"], evaluation)
        self.assertEqual(module_diagnostics[-1], instance_diagnostics[-1])

    def test_after_callback_threshold_change_applies_to_global_diagnostic(self) -> None:
        diagnostics = []
        sdk_holder = {}

        def after(evaluation: dict, _options: dict) -> dict:
            sdk_holder["sdk"].set_log_level("debug")
            return evaluation

        sdk = create_featurevisor(
            {
                "datafile": datafile(),
                "logLevel": "fatal",
                "onDiagnostic": diagnostics.append,
                "modules": [{"name": "live", "afterEvaluation": after}],
            }
        )
        sdk_holder["sdk"] = sdk

        evaluation = sdk.evaluate_variable("message")
        self.assertEqual(diagnostics[-1]["code"], "variable_default")
        self.assertEqual(diagnostics[-1]["details"], evaluation)


if __name__ == "__main__":
    unittest.main()
