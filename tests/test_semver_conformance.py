from __future__ import annotations

import json
from pathlib import Path
import unittest

from featurevisor.compare_versions import compare_versions
from featurevisor.instance import create_featurevisor
from featurevisor.test_checks import evaluate_checks_result


class SemverConformanceTests(unittest.TestCase):
    def test_portable_comparisons(self):
        fixture = json.loads((Path(__file__).parent.parent / "conformance/semver.json").read_text())
        self.assertEqual((fixture["kind"], fixture["version"], len(fixture["cases"])), ("featurevisor.semver", 1, 44))
        operators = ["semverEquals", "semverNotEquals", "semverGreaterThan", "semverGreaterThanOrEquals", "semverLessThan", "semverLessThanOrEquals"]
        for case in fixture["cases"]:
            for reverse in [False, True]:
                a, b = (case["right"], case["left"]) if reverse else (case["left"], case["right"])
                order = case["order"]
                if reverse and order is not None:
                    order = -order
                with self.subTest(case=case["name"], reverse=reverse):
                    if order is None:
                        with self.assertRaises(ValueError):
                            compare_versions(a, b)
                    else:
                        self.assertEqual(compare_versions(a, b), order)
                for index, operator in enumerate(operators):
                    expected = False if order is None else [order == 0, order != 0, order > 0, order >= 0, order < 0, order <= 0][index]
                    with self.subTest(case=case["name"], reverse=reverse, operator=operator):
                        requirement = {"variable": "source", "operator": operator, "value": b}
                        traffic = [{"key": "all", "segments": "*", "percentage": 100000, "allocation": []}]
                        datafile = {
                            "schemaVersion": "2", "revision": "semver",
                            "segments": {"version": {"conditions": {"attribute": "version", "operator": operator, "value": b}}},
                            "features": {
                                "condition": {"bucketBy": "userId", "traffic": [{**traffic[0], "segments": ["version"]}]},
                                "sourceFeature": {"bucketBy": "userId", "traffic": traffic, "variablesSchema": {"source": {"type": "string", "defaultValue": a}}},
                            },
                            "variables": {
                                "source": {"type": "string", "defaultValue": a},
                                "other": {"type": "string", "defaultValue": b},
                            },
                        }
                        for feature_source in [False, True]:
                            selected = {**requirement, **({"feature": "sourceFeature"} if feature_source else {})}
                            for negated in [False, True]:
                                subject = f"subject{feature_source}{negated}"
                                datafile["variables"][subject] = {
                                    "type": "boolean", "defaultValue": True, "disabledValue": False,
                                    "conditions": [{"not": [selected]}] if negated else [selected],
                                }
                        diagnostics = []
                        f = create_featurevisor({"datafile": datafile, "logLevel": "fatal", "onDiagnostic": diagnostics.append})
                        try:
                            self.assertEqual(f.is_enabled("condition", {"version": a}), expected)
                            for feature_source in [False, True]:
                                for negated in [False, True]:
                                    subject = f"subject{feature_source}{negated}"
                                    self.assertEqual(f.get_variable(subject), None if order is None else expected != negated)
                                    if order is None:
                                        self.assertEqual(f.evaluate_variable(subject)["reason"], "error")
                                selected = {**requirement, **({"feature": "sourceFeature"} if feature_source else {})}
                                for compare_to in [False, True]:
                                    check = dict(selected)
                                    if compare_to:
                                        del check["value"]
                                        check["compareTo"] = {"variable": "other"}
                                    for negated in [False, True]:
                                        checks = [{"not": [check]}] if negated else [check]
                                        report = evaluate_checks_result(f, datafile, checks, owner_variable=None, diagnostics=diagnostics)
                                        self.assertEqual(report["passed"], order is not None and expected != negated)
                                        if order is None:
                                            self.assertTrue(report["errors"])
                                            self.assertEqual(report["errors"][0]["type"], "check")
                                            self.assertIn(report["errors"][0]["expected"], ["valid checks", "successful comparison"])
                        finally:
                            f.close()
