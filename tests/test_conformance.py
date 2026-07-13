from __future__ import annotations

import json
import unittest
from pathlib import Path

from featurevisor.datafile_reader import _DatafileReader
from featurevisor.evaluate import EvaluationReason
from featurevisor.helpers import get_value_by_type
from featurevisor.logger import _Logger


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
        self.assertEqual(self.fixture["version"], 1)
        reader = _DatafileReader(
            datafile={"schemaVersion": "2", "revision": "conformance", "segments": {}, "features": {}},
            logger=_Logger(level="fatal"),
        )
        traffic = {"allocation": self.fixture["bucketing"]["allocations"]}

        for bucket, expected in self.fixture["bucketing"]["allocationExpectations"].items():
            allocation = reader.get_matched_allocation(traffic, int(bucket))
            self.assertIsNotNone(allocation)
            self.assertEqual(allocation["variation"], expected)

        for item in self.fixture["typedVariables"]:
            actual = get_value_by_type(item["value"], item["type"])
            self.assertEqual(actual is not None, item["valid"])


if __name__ == "__main__":
    unittest.main()
