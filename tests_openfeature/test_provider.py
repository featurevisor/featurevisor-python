import unittest

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode

from featurevisor.openfeature import FeaturevisorOpenFeatureProvider
from featurevisor import create_featurevisor


DATAFILE = {
    "schemaVersion": "2",
    "revision": "openfeature-test",
    "segments": {},
    "features": {
        "checkout": {
            "bucketBy": "userId",
            "variations": [{
                "value": "on",
                "variables": {
                    "title": "Hello", "count": 3, "ratio": 1.5, "visible": True,
                    "items": ["a"], "config": {"color": "blue"}, "json": '{"nested":true}',
                },
            }],
            "variablesSchema": {
                "title": {"type": "string", "defaultValue": "Default"},
                "count": {"type": "integer", "defaultValue": 0},
                "ratio": {"type": "double", "defaultValue": 0},
                "visible": {"type": "boolean", "defaultValue": False},
                "items": {"type": "array", "defaultValue": []},
                "config": {"type": "object", "defaultValue": {}},
                "json": {"type": "json", "defaultValue": "{}"},
            },
            "force": [{"conditions": {"attribute": "userId", "operator": "equals", "value": "forced-user"}, "enabled": True, "variation": "on"}],
            "traffic": [{"key": "all", "segments": "*", "percentage": 100000, "variation": "on"}],
        }
    },
}


class OpenFeatureProviderTest(unittest.TestCase):
    def provider(self, **kwargs):
        return FeaturevisorOpenFeatureProvider({"datafile": DATAFILE, "logLevel": "fatal"}, **kwargs)

    def test_resolves_every_type_and_maps_targeting_key(self):
        provider = self.provider()
        context = EvaluationContext(targeting_key="forced-user")
        self.assertTrue(provider.resolve_boolean_details("checkout", False, context).value)
        self.assertEqual(provider.resolve_string_details("checkout:variation", "fallback", context).value, "on")
        self.assertEqual(provider.resolve_string_details("checkout:title", "fallback", context).value, "Hello")
        self.assertEqual(provider.resolve_integer_details("checkout:count", 0, context).value, 3)
        self.assertEqual(provider.resolve_float_details("checkout:ratio", 0, context).value, 1.5)
        self.assertTrue(provider.resolve_boolean_details("checkout:visible", False, context).value)
        self.assertEqual(provider.resolve_object_details("checkout:items", [], context).value, ["a"])
        self.assertEqual(provider.resolve_object_details("checkout:config", {}, context).value, {"color": "blue"})
        self.assertEqual(provider.resolve_object_details("checkout:json", {}, context).value, {"nested": True})

    def test_errors_custom_grammar_tracking_and_shutdown(self):
        tracked = []
        provider = self.provider(key_separator="/", variation_key="$variation", on_track=lambda *args: tracked.append(args))
        self.assertEqual(provider.resolve_string_details("checkout/$variation", "fallback").value, "on")
        self.assertEqual(provider.resolve_string_details("missing", "fallback").error_code, ErrorCode.TYPE_MISMATCH)
        missing = provider.resolve_boolean_details("missing", True)
        self.assertTrue(missing.value)
        self.assertEqual(missing.error_code, ErrorCode.FLAG_NOT_FOUND)
        provider.track("purchase", EvaluationContext(targeting_key="u"), None)
        self.assertEqual(tracked[0][0], "purchase")
        provider.shutdown()

    def test_malformed_datafile(self):
        provider = FeaturevisorOpenFeatureProvider({"datafile": "{", "logLevel": "fatal"})
        result = provider.resolve_boolean_details("checkout", False)
        self.assertEqual(result.error_code, ErrorCode.PARSE_ERROR)
        self.assertEqual(result.error_message, "Could not parse datafile")
        provider.featurevisor.set_datafile(DATAFILE, replace=True)
        self.assertTrue(provider.resolve_boolean_details("checkout", False, EvaluationContext(targeting_key="forced-user")).value)

    def test_works_through_openfeature_api(self):
        api.set_provider(self.provider())
        client = api.get_client()
        self.assertTrue(client.get_boolean_value("checkout", False, EvaluationContext(targeting_key="forced-user")))

    def test_borrows_existing_featurevisor(self):
        closed = []
        featurevisor = create_featurevisor({
            "datafile": DATAFILE,
            "logLevel": "fatal",
            "modules": [{"name": "owner", "close": lambda: closed.append(True)}],
        })
        provider = FeaturevisorOpenFeatureProvider(featurevisor=featurevisor)

        self.assertIs(provider.featurevisor, featurevisor)
        provider.shutdown()
        self.assertEqual(closed, [])

        featurevisor.close()
        self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
