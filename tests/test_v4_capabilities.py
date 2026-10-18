from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from featurevisor import create_featurevisor


def json_equal(actual, expected):
    if expected is None or isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, (int, float)):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and actual == expected
    if isinstance(expected, dict):
        return isinstance(actual, dict) and actual.keys() == expected.keys() and all(
            json_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            json_equal(left, right) for left, right in zip(actual, expected))
    return isinstance(expected, str) and isinstance(actual, str) and actual == expected


class V4Capabilities(unittest.TestCase):
    def assert_json_equal(self, actual, expected):
        self.assertTrue(json_equal(actual, expected), f"{actual!r} != {expected!r} (JSON types included)")

    def test_json_comparator_preserves_types_recursively(self):
        for left, right in ((False, 0), (True, 1), (None, False), ({}, []), ([], ()), (1, "1"),
                            ({"x": [True]}, {"x": [1]}), ([None], [False]), ({"x": None}, {})):
            with self.subTest(left=left, right=right):
                self.assertFalse(json_equal(left, right))
                self.assertFalse(json_equal(right, left))
        for value in (None, False, True, 0, 1, "1", {}, [], {"x": [None, False, 1.0, {}]}):
            self.assertTrue(json_equal(value, copy.deepcopy(value)))
        self.assertTrue(json_equal({"x": [1.0, 0]}, {"x": [1, 0.0]}))

    def test_conditions_fixture(self):
        fixture = json.loads(Path("conformance/sdk-conditions.json").read_text())
        self.assertEqual(fixture["capability"], "unified-conditions")
        self.assertTrue(fixture["cases"])
        executed = []
        for case in fixture["cases"]:
            dependencies = []

            def before(options):
                if options.get("dependencyPurpose"):
                    dependencies.append({**({"feature": options["featureKey"]} if options.get("featureKey") else {}),
                                         **({"variable": options["variableKey"]} if options.get("variableKey") else {}),
                                         "dependencyPurpose": options["dependencyPurpose"]})
                return options

            datafile = copy.deepcopy(case["datafile"])
            original = copy.deepcopy(datafile)
            f = create_featurevisor({"datafile": datafile, "logLevel": "fatal", **case.get("options", {}),
                                     "modules": [{"beforeEvaluation": before}]})
            try:
                for run in range(2):
                    with self.subTest(case=case["name"], run=run):
                        dependencies.clear()
                        evaluation = (f.evaluate_flag(case["feature"], case["context"]) if case["type"] == "flag"
                                      else f.evaluate_variable(case["variable"], case["context"]))
                        for key, value in case["expected"].items():
                            if key != "value":
                                self.assertIn(key, evaluation)
                                self.assert_json_equal(evaluation[key], value)
                        if "dependencies" in case:
                            self.assert_json_equal(dependencies, case["dependencies"])
                        if "value" in case["expected"]:
                            self.assert_json_equal(f.get_variable(case["variable"], case["context"]), case["expected"]["value"])
                        if case["type"] == "flag":
                            self.assertIs(f.is_enabled(case["feature"], case["context"]), case["expected"].get("enabled") is True)
                self.assert_json_equal(datafile, original)
            finally:
                f.close()
            executed.append(case["name"])
        self.assertEqual(executed, [case["name"] for case in fixture["cases"]])

    def test_composition_fixture(self):
        fixture = json.loads(Path("conformance/sdk-composition.json").read_text())
        self.assertEqual(fixture["capability"], "composition-and-overrides")
        self.assertTrue(fixture["cases"])
        executed = []
        for case in fixture["cases"]:
            datafile = copy.deepcopy(fixture["datafile"])
            original = copy.deepcopy(datafile)
            f = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                     "stickyFeatures": case.get("stickyFeatures"),
                                     "stickyVariables": case.get("stickyVariables")})
            try:
                for run in range(2):
                    with self.subTest(case=case["name"], run=run):
                        self.assert_json_equal(f.get_variable(case["variable"], case["context"]), case["expectedValue"])
                        evaluation = f.evaluate_variable(case["variable"], case["context"])
                        for key, value in case["expectedEvaluation"].items():
                            self.assertIn(key, evaluation)
                            self.assert_json_equal(evaluation[key], value)
                self.assert_json_equal(f.get_variable("policy"), None if case.get("stickyVariables", {}).get("policy", False) is None
                                       else {"limit": 50, "methods": ["card"]})
                self.assert_json_equal(datafile, original)
            finally:
                f.close()
            executed.append(case["name"])
        self.assertEqual(executed, [case["name"] for case in fixture["cases"]])
