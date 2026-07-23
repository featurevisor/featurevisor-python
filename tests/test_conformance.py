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
        self.assertEqual(self.fixture["version"], 2)
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


if __name__ == "__main__":
    unittest.main()
