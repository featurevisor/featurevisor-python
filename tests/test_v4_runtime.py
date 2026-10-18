from __future__ import annotations

import json
import math
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from featurevisor import create_featurevisor
from featurevisor.conditions import MISSING, value_is_matched
from featurevisor.mutations import apply_variable_operations


def feature(**values):
    return {"bucketBy": "userId", "traffic": [], **values}


class SDKV4RuntimeTests(unittest.TestCase):
    def test_empty_operators_work_for_conditions_and_required_variables(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "empty-operators", "segments": {},
            "features": {
                "emptyContext": feature(force=[{
                    "conditions": [{"attribute": "profile.items", "operator": "empty"}],
                    "enabled": True,
                }]),
                "nonemptyContext": feature(force=[{
                    "conditions": [{"attribute": "profile.items", "operator": "notEmpty"}],
                    "enabled": True,
                }]),
            },
            "variables": {
                "emptyArray": {"type": "array", "defaultValue": []},
                "nonemptyObject": {"type": "object", "defaultValue": {"value": None}},
                "noneValue": {"type": "json", "defaultValue": None},
                "emptyGate": {
                    "type": "boolean", "defaultValue": True, "disabledValue": False,
                    "conditions": [{"variable": "emptyArray", "operator": "empty"}],
                },
                "nonemptyGate": {
                    "type": "boolean", "defaultValue": True, "disabledValue": False,
                    "conditions": [{"variable": "nonemptyObject", "operator": "notEmpty"}],
                },
                "noneGate": {
                    "type": "boolean", "defaultValue": True, "disabledValue": False,
                    "conditions": [{"variable": "noneValue", "operator": "notEmpty"}],
                },
            },
        }
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
        self.assertTrue(sdk.is_enabled("emptyContext", {"profile": {"items": []}}))
        self.assertTrue(sdk.is_enabled("nonemptyContext", {"profile": {"items": [None]}}))
        self.assertTrue(sdk.get_variable("emptyGate"))
        self.assertTrue(sdk.get_variable("nonemptyGate"))
        self.assertFalse(sdk.get_variable("noneGate"))

    def test_empty_and_malformed_override_programs_do_not_fall_back(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "programs", "segments": {}, "features": {},
            "variables": {
                "subject": {
                    "type": "object", "defaultValue": {"default": True}, "overrideProgram": [],
                    "overrides": [{"key": "must-not-run", "segments": "*", "value": {"legacy": True}}],
                },
                "broken": {
                    "type": "object", "defaultValue": {},
                    "overrideProgram": [{"key": "bad", "segments": "*", "operations": [], "onMatch": 0, "onMiss": 1}],
                },
            },
        }
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
        self.assertEqual(sdk.get_variable("subject"), {"default": True})
        self.assertEqual(sdk.evaluate_variable("subject")["reason"], "variable_default")
        self.assertIsNone(sdk.get_variable("broken"))
        self.assertEqual(sdk.evaluate_variable("broken")["reason"], "error")

    def test_numeric_equality_never_coerces_booleans_and_missing_is_not_null(self) -> None:
        get_regex = lambda pattern, _flags="": __import__("re").compile(pattern)
        self.assertTrue(value_is_matched(1, {"operator": "equals", "value": 1.0}, get_regex))
        self.assertFalse(value_is_matched(True, {"operator": "equals", "value": 1}, get_regex))
        self.assertFalse(value_is_matched(MISSING, {"operator": "isNull"}, get_regex))
        self.assertFalse(value_is_matched(MISSING, {"operator": "notNull"}, get_regex))
        self.assertTrue(value_is_matched(None, {"operator": "isNull"}, get_regex))
        self.assertFalse(value_is_matched(None, {"operator": "notNull"}, get_regex))

        selected = apply_variable_operations(
            [{"id": True}, {"id": 1}],
            [{"op": "set", "path": [{"select": {"property": "id", "values": [1.0]}}], "value": {"id": 2}}],
        )
        self.assertEqual(selected, [{"id": True}, {"id": 2}])
        self.assertEqual(
            apply_variable_operations([True, 1], [{"op": "removeValue", "path": [], "value": 1.0}]),
            [True],
        )

    def test_safe_integral_numeric_path_components(self) -> None:
        self.assertEqual(
            apply_variable_operations(
                ["zero", "one"],
                [{"op": "set", "path": [1.0], "value": "changed"}],
            ),
            ["zero", "changed"],
        )
        self.assertEqual(
            apply_variable_operations(
                ["zero"],
                [{"op": "set", "path": [9_007_199_254_740_991.0], "value": "ignored"}],
            ),
            ["zero"],
        )
        for part in (-1, 1.5, 9_007_199_254_740_992, 9_007_199_254_740_992.0, True, math.nan, math.inf):
            with self.subTest(mutation_path=part):
                with self.assertRaises(ValueError):
                    apply_variable_operations(
                        ["zero", "one"],
                        [{"op": "set", "path": [part], "value": "invalid"}],
                    )

        datafile = {
            "schemaVersion": "2", "revision": "numeric-paths", "segments": {}, "features": {},
            "variables": {
                "source": {"type": "array", "defaultValue": ["zero", "one"]},
                "mutated": {
                    "type": "array", "defaultValue": ["zero", "one"],
                    "overrideProgram": [{
                        "key": "raw", "segments": "*",
                        "operations": [{"op": "set", "path": [1.0], "value": "changed"}],
                        "onMatch": 1, "onMiss": 1,
                    }],
                },
                "gate": {
                    "type": "string", "defaultValue": "matched", "disabledValue": "miss",
                    "conditions": [{
                        "variable": "source", "path": [1.0], "operator": "equals", "value": "one",
                    }],
                },
            },
        }
        raw = json.dumps(datafile)
        self.assertIn('"path": [1.0]', raw)
        sdk = create_featurevisor({"datafile": raw, "logLevel": "fatal"})
        self.assertEqual(sdk.get_variable("mutated"), ["zero", "changed"])
        self.assertEqual(sdk.get_variable("gate"), "matched")

        for part in (-1, 1.5, 9_007_199_254_740_992, 9_007_199_254_740_992.0, True, math.nan, math.inf):
            with self.subTest(requirement_path=part):
                invalid = {**datafile, "variables": {**datafile["variables"]}}
                invalid["variables"]["gate"] = {
                    **datafile["variables"]["gate"],
                    "conditions": [{
                        "variable": "source", "path": [part], "operator": "equals", "value": "one",
                    }],
                }
                invalid_sdk = create_featurevisor({"datafile": invalid, "logLevel": "fatal"})
                self.assertEqual(invalid_sdk.evaluate_variable("gate")["reason"], "error")

    def test_json_boundaries_reject_non_finite_numbers_without_rejecting_native_values(self) -> None:
        diagnostics = []
        sdk = create_featurevisor({
            "datafile": json.dumps({
                "schemaVersion": "2", "revision": "stable", "segments": {}, "features": {},
                "variables": {"healthy": {"type": "string", "defaultValue": "ok"}},
            }),
            "logLevel": "debug",
            "onDiagnostic": diagnostics.append,
        })

        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(raw_datafile_constant=constant):
                sdk.set_datafile(
                    '{"schemaVersion":"2","revision":"invalid","segments":{},"features":{},'
                    '"variables":{"bad":{"type":"number","defaultValue":'
                    + constant
                    + '}}}',
                    True,
                )
                self.assertEqual(sdk.get_revision(), "stable")
                self.assertEqual(sdk.get_variable("healthy"), "ok")

        invalid_datafile_diagnostics = [
            item for item in diagnostics if item.get("code") == "invalid_datafile"
        ]
        self.assertEqual(len(invalid_datafile_diagnostics), 3)

        native_nan = math.nan
        sdk.set_datafile({
            "schemaVersion": "2", "revision": "native", "segments": {}, "features": {},
            "variables": {
                "nativeNumber": {"type": "double", "defaultValue": native_nan},
                "nativeJson": {"type": "json", "defaultValue": native_nan},
                "encodedJson": {"type": "json", "defaultValue": "NaN"},
            },
        }, True)
        self.assertEqual(sdk.get_revision(), "native")
        self.assertTrue(math.isnan(sdk.get_variable("nativeNumber")))
        self.assertTrue(math.isnan(sdk.get_variable("nativeJson")))
        self.assertIsNone(sdk.get_variable("encodedJson"))

        diagnostics.clear()
        sdk.set_datafile({
            "schemaVersion": "2", "revision": "expressions",
            "segments": {
                "known": {"key": "known", "conditions": "*"},
            },
            "features": {
                "condition": feature(force=[{
                    "conditions": '{"attribute":"score","operator":"equals","value":NaN}',
                    "enabled": True,
                }]),
                "segment": feature(traffic=[{
                    "key": "bad", "segments": '["known",Infinity]', "percentage": 100000,
                }]),
            },
            "variables": {},
        }, True)
        self.assertFalse(sdk.is_enabled("condition", {"score": 1}))
        self.assertEqual(sdk.evaluate_flag("segment")["reason"], "no_match")
        codes = [item.get("code") for item in diagnostics]
        self.assertIn("conditions_parse_error", codes)
        self.assertNotIn("evaluation_error", codes)

    def test_accumulation_publishes_one_final_callback_with_metadata(self) -> None:
        observed = []
        datafile = {
            "schemaVersion": "2",
            "revision": "callbacks",
            "segments": {},
            "features": {},
            "variables": {
                "subject": {
                    "type": "object",
                    "defaultValue": {"trace": []},
                    "overrideProgram": [
                        {"key": "parent", "keyPath": ["parent"], "segments": "*", "operations": [{"op": "append", "path": ["trace"], "value": "parent"}], "onMatch": 1, "onMiss": 2},
                        {"key": "child", "keyPath": ["parent", "child"], "segments": "*", "operations": [{"op": "append", "path": ["trace"], "value": "child"}], "onMatch": 2, "onMiss": 2},
                    ],
                }
            },
        }
        sdk = create_featurevisor({
            "datafile": datafile,
            "modules": [{
                "name": "observer",
                "afterEvaluation": lambda evaluation, _options: observed.append(
                    (evaluation.get("variableValue"), evaluation.get("variableOverridePaths"))
                ) or evaluation,
            }],
            "logLevel": "fatal",
        })
        self.assertEqual(sdk.get_variable("subject"), {"trace": ["parent", "child"]})
        self.assertEqual(observed, [({"trace": ["parent", "child"]}, [["parent"], ["parent", "child"]])])
        self.assertEqual(datafile["variables"]["subject"]["defaultValue"], {"trace": []})

    def test_child_sticky_sources_are_isolated_for_variable_requirements(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "children", "segments": {},
            "features": {
                "source": feature(variablesSchema={"plan": {"type": "string", "defaultValue": "free"}}),
                "subject": feature(
                    conditions=[{"feature": "source", "variable": "plan", "operator": "equals", "value": "pro"}],
                    traffic=[{"key": "all", "segments": "*", "percentage": 100000}],
                ),
            },
            "variables": {},
        }
        sdk = create_featurevisor({"datafile": datafile, "stickyFeatures": {"source": {"enabled": True, "variables": {"plan": "pro"}}}, "logLevel": "fatal"})
        child = sdk.spawn({}, {"stickyFeatures": {}})
        self.assertTrue(sdk.is_enabled("subject"))
        self.assertFalse(child.is_enabled("subject"))
        child.set_sticky_features({"source": {"enabled": True, "variables": {"plan": "pro"}}})
        self.assertTrue(child.is_enabled("subject"))

    def test_mixed_dependency_updates_reach_a_fixed_point(self) -> None:
        initial = {
            "schemaVersion": "2", "revision": "one", "segments": {},
            "features": {
                "checkout": feature(conditions=[{"variable": "currency", "operator": "equals", "value": "EUR"}], hash="checkout"),
                "receipt": feature(requiredFeatures=["checkout"], hash="receipt"),
            },
            "variables": {
                "currency": {"type": "string", "defaultValue": "EUR", "hash": "one"},
                "summary": {"type": "string", "defaultValue": "", "conditions": [{"feature": "receipt", "variable": "title", "operator": "equals", "value": "Receipt"}], "hash": "summary"},
                "final": {"type": "string", "defaultValue": "", "conditions": [{"variable": "summary", "operator": "equals", "value": ""}], "hash": "final"},
            },
        }
        updated = {"schemaVersion": "2", "revision": "two", "segments": {}, "features": {}, "variables": {"currency": {"type": "string", "defaultValue": "USD", "hash": "two"}}}
        events = []
        sdk = create_featurevisor({"datafile": initial, "logLevel": "fatal"})
        sdk.on("datafile_set", events.append)
        sdk.set_datafile(updated)
        self.assertEqual(events[-1]["features"], ["checkout", "receipt"])
        self.assertEqual(events[-1]["variables"], ["currency", "summary", "final"])

    def test_required_variable_source_module_errors_fall_back_but_comparator_errors_do_not(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "requirement-errors", "segments": {}, "features": {},
            "variables": {
                "source": {"type": "object", "defaultValue": {"items": [None]}},
                "sourceString": {"type": "string", "defaultValue": "value"},
                "subject": {
                    "type": "string", "defaultValue": "matched",
                    "conditions": [{"variable": "source", "operator": "isNull"}],
                },
                "badComparator": {
                    "type": "string", "defaultValue": "bad",
                    "conditions": [{"variable": "sourceString", "operator": "matches", "value": "["}],
                },
            },
        }
        sdk = create_featurevisor({
            "datafile": datafile,
            "modules": [{
                "name": "throw-source-only",
                "beforeEvaluation": lambda options: (
                    (_ for _ in ()).throw(RuntimeError("source failed"))
                    if options.get("variableKey") == "source"
                    else options
                ),
            }],
            "logLevel": "fatal",
        })
        evaluation = sdk.evaluate_variable("subject")
        self.assertEqual(evaluation["variableValue"], "matched")
        self.assertEqual(evaluation["reason"], "variable_default")

        plain = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
        self.assertEqual(plain.evaluate_variable("badComparator")["reason"], "error")

    def test_variable_requirement_cycles_recover_and_thread_state_is_isolated(self) -> None:
        cycle_sdk = create_featurevisor({"datafile": {
            "schemaVersion": "2", "revision": "cycle", "segments": {}, "features": {},
            "variables": {
                "a": {"type": "string", "defaultValue": "a", "conditions": [{"variable": "b", "operator": "equals", "value": "b"}]},
                "b": {"type": "string", "defaultValue": "b", "conditions": [{"variable": "a", "operator": "equals", "value": "a"}]},
                "healthy": {"type": "string", "defaultValue": "ok"},
            },
        }, "logLevel": "fatal"})
        self.assertEqual(cycle_sdk.evaluate_variable("a")["reason"], "error")
        self.assertEqual(cycle_sdk.get_variable("healthy"), "ok")

        rendezvous = Barrier(2)
        datafile = {
            "schemaVersion": "2", "revision": "threads", "segments": {}, "features": {},
            "variables": {
                "source": {"type": "string", "defaultValue": "none", "overrides": [
                    {"key": "a", "conditions": [{"attribute": "worker", "operator": "equals", "value": "a"}], "value": "a"},
                    {"key": "b", "conditions": [{"attribute": "worker", "operator": "equals", "value": "b"}], "value": "b"},
                ]},
                "subjectA": {"type": "boolean", "defaultValue": True, "conditions": [{"variable": "source", "operator": "equals", "value": "a"}]},
                "subjectB": {"type": "boolean", "defaultValue": True, "conditions": [{"variable": "source", "operator": "equals", "value": "b"}]},
            },
        }
        sdk = create_featurevisor({
            "datafile": datafile,
            "modules": [{
                "name": "rendezvous",
                "beforeEvaluation": lambda options: (
                    (rendezvous.wait(timeout=2), options)[1]
                    if options.get("variableKey") == "source"
                    else options
                ),
            }],
            "logLevel": "fatal",
        })
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda item: sdk.get_variable(item[0], {"worker": item[1]}), [("subjectA", "a"), ("subjectB", "b")]))
        self.assertEqual(results, [True, True])

    def test_required_variable_paths_follow_javascript_own_array_properties(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "requirement-paths", "segments": {}, "features": {},
            "variables": {
                "array": {"type": "array", "defaultValue": ["first"]},
                "object": {"type": "object", "defaultValue": {"0": "object-first"}},
                "stringIndex": {"type": "boolean", "defaultValue": True, "conditions": [{"variable": "array", "path": ["0"], "operator": "equals", "value": "first"}]},
                "numericIndex": {"type": "boolean", "defaultValue": True, "conditions": [{"variable": "array", "path": [0], "operator": "equals", "value": "first"}]},
                "length": {"type": "boolean", "defaultValue": True, "conditions": [{"variable": "array", "path": ["length"], "operator": "equals", "value": 1.0}]},
                "numericObject": {"type": "boolean", "defaultValue": True, "conditions": [{"variable": "object", "path": [0], "operator": "notExists"}]},
            },
        }
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
        for key in ("stringIndex", "numericIndex", "length", "numericObject"):
            with self.subTest(key=key):
                self.assertTrue(sdk.get_variable(key))

    def test_legacy_required_field_is_ignored(self) -> None:
        sdk = create_featurevisor({"datafile": {
            "schemaVersion": "2", "revision": "legacy", "segments": {},
            "features": {
                "disabled": feature(force=[{"segments": "*", "enabled": False}]),
                "subject": feature(required=["disabled"], traffic=[{"key": "all", "segments": "*", "percentage": 100000}]),
            },
        }, "logLevel": "fatal"})
        self.assertTrue(sdk.is_enabled("subject"))


if __name__ == "__main__":
    unittest.main()
