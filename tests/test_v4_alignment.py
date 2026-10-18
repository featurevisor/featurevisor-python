from __future__ import annotations

import copy
import json
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from featurevisor import create_featurevisor
from featurevisor.project import FeaturevisorProject
from featurevisor.tester import _run_test_project_set
from featurevisor.segment_tests import assert_segment_references


def feature(**values):
    return {"bucketBy": "userId", "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}], **values}


def datafile(**values):
    return {"schemaVersion": "4", "revision": "alignment", "segments": {}, "features": {}, "variables": {}, **values}


class V4AlignmentTests(unittest.TestCase):
    def test_malformed_selectors_all_sites_and_load_timing(self):
        for schema in ("2", "4"):
            for raw in ('{"not":[', '[{"feature":', '"unterminated', ' \n {"or": [oops] }'):
                for site in ("feature", "variable", "segment", "force", "traffic", "ruleOverride", "variationOverride", "overrides", "overrideProgram"):
                    with self.subTest(schema=schema, site=site, raw=raw):
                        d = datafile(schemaVersion=schema, features={"healthy": feature()},
                                     variables={"healthy": {"type": "integer", "defaultValue": 9}})
                        selected = feature(variablesSchema={"copy": {"type": "string", "defaultValue": "fallback"}})
                        d["features"]["subject"] = selected
                        kind, key = "feature", "subject"
                        if site == "feature":
                            selected["conditions"] = raw
                            read = lambda f: f.evaluate_flag("subject")["reason"]
                            expected = "conditions_unmet"
                        elif site == "segment":
                            d["segments"]["bad"] = {"conditions": raw}
                            d["segments"]["alias"] = {"conditions": {"not": ["bad"]}}
                            selected["conditions"] = {"not": ["alias"]}
                            kind, key = "segment", "bad"
                            read = lambda f: f.evaluate_flag("subject")["reason"]
                            expected = "conditions_unmet"
                        elif site in ("variable", "overrides", "overrideProgram"):
                            variable = {"type": "string", "defaultValue": "fallback", "disabledValue": "disabled"}
                            d["variables"]["subject"] = variable
                            kind = "variable"
                            if site == "variable":
                                variable["conditions"] = raw
                                expected = "disabled"
                            elif site == "overrides":
                                variable[site] = [{"conditions": raw, "value": "bad"}]
                                expected = "fallback"
                            else:
                                variable[site] = [{"key": "bad", "conditions": raw,
                                                   "operations": [{"op": "set", "path": [], "value": "bad"}],
                                                   "onMatch": 1, "onMiss": 1}]
                                expected = "fallback"
                            read = lambda f: f.get_variable("subject")
                        else:
                            expected = "fallback"
                            read = lambda f: f.get_variable("subject", "copy")
                            if site == "force":
                                selected["force"] = [{"conditions": raw, "variables": {"copy": "bad"}}]
                            elif site == "traffic":
                                selected["traffic"].insert(0, {"key": "bad", "conditions": raw, "percentage": 100000,
                                                               "variables": {"copy": "bad"}})
                            elif site == "ruleOverride":
                                selected["traffic"][0]["variableOverrides"] = {"copy": [{"conditions": raw, "value": "bad"}]}
                            else:
                                selected["variations"] = [{"value": "on", "variableOverrides": {"copy": [{"conditions": raw, "value": "bad"}]}}]
                                selected["traffic"][0]["variation"] = "on"
                        original = copy.deepcopy(d)
                        diagnostics = []
                        f = create_featurevisor({"logLevel": "error", "onDiagnostic": diagnostics.append})
                        self.addCleanup(f.close)
                        self.assertTrue(f.set_datafile(d, True))
                        errors = lambda: [item for item in diagnostics if item["code"] == "conditions_parse_error"]
                        self.assertEqual(len(errors()), 1 if schema == "2" else 0)
                        self.assertTrue(f.is_enabled("healthy"))
                        self.assertEqual(f.get_variable("healthy"), 9)
                        for _ in range(2):
                            self.assertEqual(read(f), expected)
                        self.assertEqual(len(errors()), 1)
                        self.assertEqual(errors()[0]["details"], {"conditions": raw, "entityType": kind, "entityKey": key})
                        self.assertFalse(any(item["code"] == "invalid_datafile" for item in diagnostics))
                        f.set_datafile(datafile(revision="unrelated"))
                        self.assertEqual(read(f), expected)
                        self.assertEqual(len(errors()), 1)
                        f.set_datafile(d, True)
                        self.assertEqual(read(f), expected)
                        self.assertEqual(len(errors()), 2)
                        self.assertEqual(d, original)

    def test_diagnostics_reenter_cached_or_accepted_entities(self):
        for schema in ("2", "4"):
            for kind in ("feature", "segment", "variable"):
                with self.subTest(schema=schema, kind=kind):
                    observations = []
                    f = create_featurevisor({"logLevel": "error"})
                    self.addCleanup(f.close)
                    d = datafile(schemaVersion=schema, revision="accepted")
                    if kind == "feature":
                        d["features"]["bad"] = feature(conditions="{")
                        read = lambda: f.evaluate_flag("bad")["reason"]
                    elif kind == "segment":
                        d["segments"]["bad"] = {"conditions": "{"}
                        d["features"]["bad"] = feature(conditions={"not": ["bad"]})
                        read = lambda: f.evaluate_flag("bad")["reason"]
                    else:
                        d["variables"]["bad"] = {"type": "string", "conditions": "{"}
                        read = lambda: f.evaluate_variable("bad")["reason"]

                    def diagnostic(item):
                        if item["code"] == "conditions_parse_error":
                            observations.append((f.get_revision(), read()))

                    f.on_diagnostic = diagnostic
                    self.assertTrue(f.set_datafile(d))
                    self.assertEqual(read(), "conditions_unmet")
                    self.assertEqual(observations, [("accepted", "conditions_unmet")])

    def test_mixed_versions_preserve_unread_legacy_empty_selectors(self):
        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(
            schemaVersion="2", segments={"empty": {"conditions": {"and": []}}},
            features={"legacy": feature(force=[{"conditions": [], "enabled": True}]),
                      "segment": feature(conditions="empty"), "newRoot": feature(conditions=[])})})
        self.addCleanup(f.close)
        f.set_datafile(datafile(variables={"new": {"type": "integer", "defaultValue": 1}}))
        self.assertEqual(f.get_schema_version(), "4")
        self.assertTrue(f.is_enabled("legacy"))
        self.assertTrue(f.is_enabled("segment"))
        self.assertFalse(f.is_enabled("newRoot"))
        f.set_datafile(datafile(schemaVersion="2", features={"mixed": feature(conditions=[], segments="*")}))
        self.assertFalse(f.is_enabled("mixed"))
        f.set_datafile(datafile(features={"legacy": feature(conditions=[])}))
        self.assertFalse(f.is_enabled("legacy"))

    def test_public_conditions_shapes_match_normalization(self):
        country = {"attribute": "country", "operator": "equals", "value": "nl"}
        d = datafile(features={
            "mixed": feature(conditions=[country], segments=["missing"], requiredFeatures=["gate"]),
            "empty": feature(conditions=[], segments="*"),
            "broken": feature(conditions="{"),
        }, variables={"broken": {"type": "object", "conditions": "{"}})
        f = create_featurevisor({"datafile": d, "logLevel": "fatal"})
        self.addCleanup(f.close)
        self.assertEqual(f.evaluate_flag("mixed")["conditions"],
                         [country, "missing", {"feature": "gate"}])
        self.assertEqual(f.evaluate_flag("empty")["conditions"], [{"and": []}, "*"])
        self.assertEqual(json.loads(json.dumps(f.evaluate_flag("broken")["conditions"])), {"or": []})
        self.assertEqual(json.loads(json.dumps(f.evaluate_variable("broken")["conditions"])), {"or": []})

    def test_removed_unreleased_selectors_are_ignored(self):
        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(
            features={"subject": feature(required=["missing"], requiredVariables=[{"variable": "missing", "operator": "notNull"}])},
            variables={"subject": {"type": "integer", "defaultValue": 7,
                                   "requiredVariables": [{"variable": "missing", "operator": "notNull"}]}})})
        self.addCleanup(f.close)
        self.assertTrue(f.is_enabled("subject"))
        self.assertEqual(f.get_variable("subject"), 7)

    def test_composition_memo_preserves_json_once_and_borrowed_values(self):
        value = {"items": list(range(10000))}
        visits = []
        d = datafile(variables={
            "source": {"type": "object", "defaultValue": value},
            "text": {"type": "json", "defaultValue": '"null"'},
            "alias": {"type": "json", "compose": "text"},
            "root": {"type": "object", "compose": [{"key": "a", "variable": "source"},
                                                        {"key": "b", "variable": "source"},
                                                        {"key": "text1", "variable": "alias"},
                                                        {"key": "text2", "variable": "alias"}]},
        })
        f = create_featurevisor({"logLevel": "fatal", "datafile": d,
                                 "modules": [{"beforeEvaluation": lambda options: visits.append(
                                     (options.get("variableKey"), options.get("dependencyPurpose"))) or options}]})
        self.addCleanup(f.close)
        with patch("featurevisor._evaluation_session.copy.deepcopy", side_effect=AssertionError("composition copied")):
            result = f.get_variable("root")
        self.assertIs(result["a"], value)
        self.assertIs(result["b"], value)
        self.assertEqual((result["text1"], result["text2"]), ("null", "null"))
        self.assertEqual(visits.count(("source", "composition")), 1)
        self.assertEqual(visits.count(("alias", "composition")), 1)

    def test_purpose_separation_and_owned_condition_snapshots(self):
        source = {"limit": 50}
        d = datafile(variables={
            "source": {"type": "object", "defaultValue": source},
            "mutator": {"type": "boolean", "defaultValue": True},
            "root": {"type": "object", "conditions": [
                {"variable": "source", "path": ["limit"], "operator": "equals", "value": 50},
                {"variable": "mutator", "operator": "equals", "value": True},
                {"variable": "source", "path": ["limit"], "operator": "equals", "value": 50}],
                "compose": [{"key": "first", "variable": "source"}, {"key": "last", "variable": "source"}]},
        })
        purposes = []
        retained = []

        def before(options):
            retained.append(options)
            if options.get("variableKey") == "source":
                purposes.append(options["dependencyPurpose"])
            if options.get("variableKey") == "mutator":
                source["limit"] = 80
            return options

        f = create_featurevisor({"datafile": d, "logLevel": "fatal", "modules": [{"beforeEvaluation": before}]})
        self.addCleanup(f.close)
        self.assertEqual(f.get_variable("root"), {"first": {"limit": 80}, "last": {"limit": 80}})
        self.assertEqual(purposes, ["condition", "composition"])
        for options in retained:
            self.assertNotIn("_evaluationSession", options)
            self.assertFalse(any(key.startswith("__featurevisor_") for key in options))

    def test_composition_child_context_sticky_modules_and_immutable_overrides(self):
        source = {"items": ["card"]}
        original = copy.deepcopy(source)
        seen = []
        d = datafile(variables={"source": {"type": "object", "defaultValue": source},
                               "root": {"type": "object", "compose": [{"key": "a", "variable": "source"}, {"key": "b", "variable": "source"}],
                                        "overrideProgram": [{"key": "add", "keyPath": ["add"], "conditions": "*",
                                                             "operations": [{"op": "append", "path": ["a", "items"], "value": "ideal"}],
                                                             "onMatch": 1, "onMiss": 1}]}})

        def before(options):
            if options.get("dependencyOf"):
                seen.append((options["context"], options["dependencyPurpose"], options["dependencyOf"]))
            else:
                return {**options, "context": {"country": "effective"}}
            return options

        f = create_featurevisor({"datafile": d, "context": {"stored": True}, "logLevel": "fatal",
                                 "modules": [{"beforeEvaluation": before}]})
        self.addCleanup(f.close)
        child = f.spawn({"country": "nl"}, {"stickyVariables": {"source": source}})
        self.addCleanup(child.close)
        result = child.get_variable("root", {}, {"defaultVariableValue": "outer"})
        self.assertEqual(result, {"a": {"items": ["card", "ideal"]}, "b": {"items": ["card"]}})
        self.assertEqual(source, original)
        self.assertEqual(seen, [({"country": "effective"}, "composition", {"type": "variable", "variableKey": "root"})])
        child.set_sticky_variables({"root": {"whole": True}})
        seen.clear()
        self.assertEqual(child.get_variable("root"), {"whole": True})
        self.assertEqual(seen, [])

    def test_combined_depth_bound_cycles_and_recovery(self):
        # Exercise the SDK's logical bound independently of the host stack budget.
        previous_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(previous_limit, 8192))
        self.addCleanup(sys.setrecursionlimit, previous_limit)
        for depth in (256, 257):
            with self.subTest(depth=depth):
                variables = {str(i): {"type": "integer", "compose": str(i + 1)} for i in range(depth)}
                variables[str(depth)] = {"type": "integer", "defaultValue": 7}
                f = create_featurevisor({"datafile": datafile(variables=variables), "logLevel": "fatal"})
                self.addCleanup(f.close)
                evaluation = f.evaluate_variable("0")
                self.assertEqual(evaluation["reason"], "variable_composed" if depth == 256 else "error")
                self.assertEqual(f.get_variable("0"), 7 if depth == 256 else None)
                self.assertEqual(f.get_variable(str(depth)), 7)
        d = datafile(features={"subject": feature(conditions="s0")},
                     segments={f"s{i}": {"conditions": f"s{i + 1}"} for i in range(200)},
                     variables={str(i): {"type": "integer", "compose": str(i + 1)} for i in range(60)})
        d["segments"]["s200"] = {"conditions": {"variable": "0", "operator": "notNull"}}
        d["variables"]["60"] = {"type": "integer", "defaultValue": 7}
        f = create_featurevisor({"datafile": d, "logLevel": "fatal"})
        self.addCleanup(f.close)
        self.assertEqual(f.evaluate_flag("subject")["reason"], "error")
        f.set_datafile(datafile(segments={"s200": {"conditions": "*"}}))
        self.assertTrue(f.is_enabled("subject"))
        f.set_datafile(datafile(segments={"s200": {"conditions": {"not": [{"feature": "subject"}]}}}))
        self.assertEqual(f.evaluate_flag("subject")["reason"], "error")
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(lambda _: f.get_variable("60"), range(20))), [7] * 20)

    def test_preserves_the_host_recursion_budget_and_recovers_after_exhaustion(self):
        previous_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(1000)
        self.addCleanup(sys.setrecursionlimit, previous_limit)
        variables = {str(i): {"type": "integer", "compose": str(i + 1)} for i in range(256)}
        variables["256"] = {"type": "integer", "defaultValue": 7}
        f = create_featurevisor({"datafile": datafile(variables=variables), "logLevel": "fatal"})
        self.addCleanup(f.close)
        with patch("sys.setrecursionlimit", side_effect=AssertionError("SDK changed host settings")):
            self.assertEqual(f.evaluate_variable("0")["reason"], "error")
            self.assertIsNone(f.get_variable("0"))
            self.assertEqual(f.get_variable("256"), 7)
            self.assertEqual(sys.getrecursionlimit(), 1000)

    def test_mixed_dependency_events_without_warming_lazy_entities(self):
        d = datafile(segments={"sourceSegment": {"conditions": '{"variable":"source","operator":"notNull"}'},
                               "aliasSegment": {"conditions": "sourceSegment"}, "bad": {"conditions": "{"}},
                     features={"gate": feature(conditions="aliasSegment", hash="same")},
                     variables={"source": {"type": "integer", "defaultValue": 1, "hash": "one"},
                                "map": {"type": "object", "compose": [{"key": "enabled", "feature": "gate"}], "hash": "same"},
                                "alias": {"type": "object", "compose": "map", "hash": "same"}})
        diagnostics = []
        f = create_featurevisor({"datafile": d, "logLevel": "error", "onDiagnostic": diagnostics.append})
        self.addCleanup(f.close)
        self.assertEqual(f.datafile.entity_cache, {})
        events = []
        f.on("datafile_set", events.append)
        f.set_datafile(datafile(variables={"source": {"type": "integer", "defaultValue": 2, "hash": "two"}}))
        self.assertEqual(set(events[-1]["variables"]), {"source", "map", "alias"})
        self.assertEqual(events[-1]["features"], ["gate"])
        self.assertEqual(f.datafile.entity_cache, {})
        self.assertEqual(diagnostics, [])
        replacement = copy.deepcopy(d)
        del replacement["variables"]["source"]
        f.set_datafile(replacement, True)
        self.assertIn("alias", events[-1]["variables"])
        self.assertIn("gate", events[-1]["features"])

    def test_sdk_backed_segment_owner_and_target_fallback(self):
        project = Mock(spec=FeaturevisorProject)
        project.set_name = None
        project.get_config.return_value = {"environments": ["production"]}
        project.list_targets.return_value = ["web"]
        project.list_features.return_value = []
        project.list_segments.return_value = []
        fallback = {"audience": {"conditions": [{"variable": "limit", "path": ["value"], "operator": "equals", "value": 50},
                                                {"feature": "gate"}]}}
        d = datafile(features={"gate": feature()}, variables={"limit": {"type": "object", "defaultValue": {"value": 50}}})
        target = {**d, "segments": {"audience": {"conditions": {"attribute": "country", "operator": "equals", "value": "de"}}}}
        project.build_datafile_json.side_effect = lambda **kwargs: target if kwargs.get("target") else d
        assertions = [{"environment": "production", "at": 0, "expectedToMatch": True},
                      {"environment": "production", "target": "web", "context": {"country": "nl"}, "expectedToMatch": False},
                      {"environment": "production", "stickyFeatures": {"gate": {"enabled": False}}, "expectedToMatch": False}]
        project.list_tests.return_value = [{"key": "audience", "segment": "audience", "_segments": fallback, "assertions": assertions}]
        result = _run_test_project_set(project, json_output=True)[0]
        self.assertTrue(result["passed"], result)
        self.assertEqual(len(result["cases"]), 3)
        project.list_tests.return_value[0]["assertions"] = [{"expectedToMatch": False}]
        with patch("featurevisor.tester.create_featurevisor") as create:
            result = _run_test_project_set(project, json_output=True)[0]
            create.assert_not_called()
        self.assertFalse(result["passed"])

    def test_segment_preflight_prefers_protocol_closure_over_target_specialization(self):
        project = Mock(spec=FeaturevisorProject)
        project.set_name = None
        project.get_config.return_value = {"environments": False}
        project.list_targets.return_value = []
        project.list_features.return_value = []
        project.list_segments.return_value = []
        project.build_datafile_json.return_value = datafile(segments={"audience": {"conditions": "*"}})
        project.list_tests.return_value = [{"segment": "audience", "key": "audience",
                                           "_segments": {"audience": {"conditions": {"variable": "missing", "operator": "isNull"}}},
                                           "assertions": [{"expectedToMatch": True}]}]
        with patch("featurevisor.tester.create_featurevisor") as create:
            result = _run_test_project_set(project, json_output=True)[0]
            create.assert_not_called()
        self.assertFalse(result["passed"])
        self.assertIn('variable "missing"', result["cases"][0]["errors"][0]["message"])

    def test_preflight_walks_only_the_selected_feature_variable_overrides(self):
        d = datafile(features={"gate": feature(variablesSchema={"copy": {"type": "string", "defaultValue": "ok"}})})
        d["features"]["gate"]["traffic"][0]["variableOverrides"] = {
            "copy": [{"conditions": {"variable": "missing", "operator": "notNull"}, "value": "bad"}]}
        segments = {"flag": {"conditions": {"feature": "gate"}},
                    "copy": {"conditions": {"feature": "gate", "variable": "copy", "operator": "notNull"}}}
        assert_segment_references("flag", d, segments)
        with self.assertRaisesRegex(ValueError, 'variable "missing"'):
            assert_segment_references("copy", d, segments)
