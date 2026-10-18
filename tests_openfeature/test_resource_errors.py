from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import Reason

from featurevisor import create_featurevisor
from featurevisor._json import strict_json_loads
from featurevisor.openfeature import FeaturevisorOpenFeatureProvider


DEEP_JSON = "[" * 10000 + "0" + "]" * 10000


def exhausted_on_deep_json(source):
    # Python 3.12 and later can decode this depth, so the decoder's exhaustion is simulated.
    if source == DEEP_JSON:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
    return strict_json_loads(source)


class ProviderResourceErrorTests(unittest.TestCase):
    def setUp(self):
        previous = sys.getrecursionlimit()
        sys.setrecursionlimit(1000)
        self.addCleanup(sys.setrecursionlimit, previous)
        setter = patch("sys.setrecursionlimit", side_effect=AssertionError("SDK changed the host budget"))
        setter.start()
        self.addCleanup(setter.stop)

    def provider(self):
        variables = {str(i): {"type": "integer", "compose": str(i + 1)} for i in range(256)}
        variables.update({
            "256": {"type": "integer", "defaultValue": 7},
            "map": {"type": "object", "compose": [{"key": "good", "variable": "256"},
                                                        {"key": "bad", "variable": "0"}]},
            "json": {"type": "json", "defaultValue": DEEP_JSON},
        })
        f = create_featurevisor({"logLevel": "fatal", "datafile": {
            "schemaVersion": "4", "revision": "resource", "segments": {}, "variables": variables,
            "features": {"root": {
                "bucketBy": "userId", "conditions": {"not": [{"variable": "0", "operator": "notNull"}]},
                "traffic": [{"key": "all", "conditions": "*", "percentage": 100000, "variation": "on"}],
                "variations": [{"value": "on"}],
                "variablesSchema": {"copy": {"type": "integer", "defaultValue": 7}},
            }, "json": {
                "bucketBy": "userId", "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}],
                "variablesSchema": {"copy": variables["json"]},
            }},
        }})
        self.addCleanup(f.close)
        provider = FeaturevisorOpenFeatureProvider(featurevisor=f)
        self.addCleanup(provider.shutdown)
        return provider

    def assert_general(self, details, fallback):
        self.assertEqual(details.value, fallback)
        self.assertEqual(details.reason, Reason.ERROR)
        self.assertEqual(details.error_code, ErrorCode.GENERAL)
        self.assertEqual(details.flag_metadata["featurevisorReason"], "error")
        self.assertTrue(details.error_message)
        self.assertEqual(sys.getrecursionlimit(), 1000)

    def test_dependency_exhaustion_returns_general_and_preserves_later_calls(self):
        provider = self.provider()
        for resolve, key, fallback in ((provider.resolve_integer_details, "variable:0", 99),
                                       (provider.resolve_object_details, "variable:map", {"fallback": True}),
                                       (provider.resolve_boolean_details, "root", True),
                                       (provider.resolve_string_details, "root:variation", "fallback"),
                                       (provider.resolve_integer_details, "root:copy", 99)):
            with self.subTest(key=key):
                self.assert_general(resolve(key, fallback), fallback)
                healthy = provider.resolve_integer_details("variable:256", 99)
                self.assertEqual(healthy.value, 7)
                self.assertIsNone(healthy.error_code)

    @patch("featurevisor.openfeature.strict_json_loads", exhausted_on_deep_json)
    def test_json_resource_errors_return_general_for_both_variable_kinds(self):
        provider = self.provider()
        for key in ("variable:json", "json:copy"):
            with self.subTest(key=key):
                self.assert_general(provider.resolve_object_details(key, {"fallback": True}), {"fallback": True})
                self.assertEqual(provider.resolve_integer_details("variable:256", 99).value, 7)


if __name__ == "__main__":
    unittest.main()
