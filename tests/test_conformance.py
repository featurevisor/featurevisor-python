from __future__ import annotations

import json
import unittest
from pathlib import Path

from featurevisor.evaluation_data_provider import _InstanceEvaluationDataProvider
from featurevisor.evaluate import EvaluationReason
from featurevisor.helpers import get_value_by_type
from featurevisor.diagnostics import _create_evaluation_diagnostics
from featurevisor.bucketer import get_bucket_key
from featurevisor import create_featurevisor


class SDKV3ConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(Path("conformance/sdk-v3.json").read_text())

    def test_evaluation_reason_is_python_310_compatible_and_string_like(self) -> None:
        reason = EvaluationReason.FEATURE_NOT_FOUND
        self.assertIsInstance(reason, str)
        self.assertEqual(str(reason), "feature_not_found")
        self.assertEqual(json.dumps({"reason": reason}), '{"reason": "feature_not_found"}')

    def test_allocation_and_typed_value_contracts(self) -> None:
        self.assertEqual(self.fixture["version"], 5)
        reader = _InstanceEvaluationDataProvider(
            datafile={"schemaVersion": "2", "revision": "conformance", "segments": {}, "features": {}},
            diagnostics=_create_evaluation_diagnostics(),
        )
        traffic = {"allocation": self.fixture["bucketing"]["allocations"]}

        for bucket, expected in self.fixture["bucketing"]["allocationExpectations"].items():
            allocation = reader.get_matched_allocation(traffic, int(bucket))
            self.assertIsNotNone(allocation)
            self.assertEqual(allocation["variation"], expected)

        for item in self.fixture["typedVariables"]:
            actual = get_value_by_type(item["value"], item["type"])
            self.assertEqual(actual is not None, item["valid"])

        for item in self.fixture["numericBucketKeys"]:
            actual = get_bucket_key(
                featureKey="feature",
                bucketBy="value",
                context={"value": item["value"]},
                diagnostics=_create_evaluation_diagnostics(),
            )
            self.assertEqual(actual, f'{item["expected"]}.feature')

        for item in self.fixture["regularExpressions"]["portableCases"]:
            actual = reader.all_conditions_are_matched(
                {
                    "attribute": "value",
                    "operator": "matches",
                    "value": item["pattern"],
                    "regexFlags": item["flags"],
                },
                {"value": item["value"]},
            )
            self.assertEqual(
                actual,
                item["expected"],
                f'pattern {item["pattern"]}, flags {item["flags"]}',
            )

        for item in self.fixture["conditionCases"]:
            self.assertEqual(
                reader.all_conditions_are_matched(item["condition"], item["context"]),
                item["expected"],
                item["name"],
            )

        aggregate_case = self.fixture["defaults"]["aggregateCase"]
        featurevisor = create_featurevisor({"datafile": aggregate_case["datafile"]})
        evaluated = featurevisor.get_all_evaluations(
            {},
            [],
            {"defaultVariationValue": aggregate_case["defaultVariationValue"]},
        )["experiment"]
        self.assertEqual(evaluated["enabled"], aggregate_case["expected"]["enabled"])
        self.assertEqual(evaluated["variation"], aggregate_case["expected"]["variation"])

    def test_global_variables_and_overloaded_api(self) -> None:
        fixture = self.fixture["globalVariables"]
        for case in fixture["cases"]:
            f = create_featurevisor({
                "datafile": fixture["datafile"],
                "stickyVariables": case.get("stickyVariables", {}),
                "logLevel": "fatal",
            })
            options = {}
            if "defaultVariableValue" in case:
                options["defaultVariableValue"] = case["defaultVariableValue"]
            evaluation = f.evaluate_variable(case["key"], case.get("context", {}), options)
            self.assertEqual(evaluation.get("variableValue"), case.get("expectedValue"), case["name"])
            self.assertEqual(str(evaluation["reason"]), case["expectedReason"], case["name"])
            self.assertEqual(evaluation.get("variableOverrideIndex"), case.get("expectedOverrideIndex"), case["name"])
            self.assertEqual(evaluation.get("variableOverrideKey"), case.get("expectedOverrideKey"), case["name"])
            self.assertEqual(evaluation.get("variableOverridePath"), case.get("expectedOverridePath"), case["name"])

        boundary = fixture["overloadCase"]
        f = create_featurevisor({"datafile": fixture["datafile"], "logLevel": "fatal"})
        self.assertEqual(f.get_variable(boundary["sharedKey"]), boundary["expectedGlobalValue"])
        self.assertEqual(
            f.get_variable(boundary["sharedKey"], boundary["featureVariableKey"]),
            boundary["expectedFeatureValue"],
        )
        self.assertIn(boundary["sharedKey"], f.get_variable_keys())

    def test_canonical_required_features(self) -> None:
        fixture = self.fixture["requiredFeatures"]
        f = create_featurevisor({"datafile": fixture["datafile"], "logLevel": "fatal"})
        for case in fixture["cases"]:
            self.assertEqual(f.is_enabled(case["feature"]), case["expectedEnabled"], case["name"])
        case = fixture["featureVariableCase"]
        evaluation = f.evaluate_variable(case["feature"], case["variable"])
        self.assertEqual(evaluation.get("variableValue"), case["expectedValue"])
        self.assertEqual(evaluation.get("variableOverrideKey"), case["expectedOverrideKey"])

    def test_datafile_dependency_events(self) -> None:
        fixture = self.fixture["globalVariables"]
        update = fixture["datafileUpdateCase"]
        f = create_featurevisor({"datafile": update["initial"], "logLevel": "fatal"})
        events = []
        f.on("datafile_set", events.append)
        f.set_datafile(update["merge"])
        self.assertEqual(sorted(f.get_feature_keys()), update["expectedAfterMerge"]["features"])
        self.assertEqual(sorted(f.get_variable_keys()), update["expectedAfterMerge"]["variables"])
        self.assertEqual(sorted(events[-1]["features"]), sorted(update["expectedAfterMerge"]["changedFeatures"]))
        self.assertEqual(sorted(events[-1]["variables"]), sorted(update["expectedAfterMerge"]["changedVariables"]))
        f.set_datafile(update["replacement"], True)
        self.assertEqual(sorted(events[-1]["features"]), sorted(update["expectedAfterReplacement"]["changedFeatures"]))
        self.assertEqual(sorted(events[-1]["variables"]), sorted(update["expectedAfterReplacement"]["changedVariables"]))

        dependencies = fixture["dependencyUpdateCase"]
        for mode in dependencies["modes"]:
            f = create_featurevisor({"datafile": dependencies["initial"], "logLevel": "fatal"})
            events = []
            f.on("datafile_set", events.append)
            f.set_datafile(dependencies["updated"], mode["replace"])
            self.assertEqual(sorted(events[-1]["features"]), dependencies["expectedChangedFeatures"], mode["name"])
            self.assertEqual(sorted(events[-1]["variables"]), dependencies["expectedChangedVariables"], mode["name"])

        f = create_featurevisor({"datafile": dependencies["initial"], "logLevel": "fatal"})
        events = []
        f.on("datafile_set", events.append)
        f.set_datafile(dependencies["withoutSegment"], True)
        self.assertEqual(sorted(events[-1]["features"]), dependencies["expectedRemovedSegmentFeatures"])
        self.assertEqual(sorted(events[-1]["variables"]), dependencies["expectedRemovedSegmentVariables"])

    def test_global_modules_sticky_children_and_aggregates(self) -> None:
        fixture = self.fixture["globalVariables"]
        observed = []
        f = create_featurevisor({
            "datafile": fixture["datafile"],
            "stickyVariables": {"stringValue": "parent-sticky"},
            "modules": [{
                "name": "unified",
                "beforeEvaluation": lambda options: observed.append(("before", options["variableKey"])) or options,
                "afterEvaluation": lambda evaluation, options: observed.append(("after", evaluation["reason"])) or evaluation,
            }],
            "logLevel": "fatal",
        })
        self.assertEqual(f.get_variable("stringValue"), "parent-sticky")
        self.assertEqual(observed, [("before", "stringValue"), ("after", "sticky")])
        self.assertIn("integerValue", f.get_variable_evaluations({}, ["integerValue"]))

        child = f.spawn({}, {"stickyVariables": {"stringValue": "child-sticky"}})
        self.assertEqual(child.get_variable("stringValue"), "child-sticky")
        child.set_sticky_variables({"integerValue": 99})
        self.assertEqual(child.get_variable("integerValue"), 99)
        self.assertEqual(f.get_variable("integerValue"), 1)


if __name__ == "__main__":
    unittest.main()
