from __future__ import annotations

import sys
import time
import unittest

sys.path.insert(0, "src")

from featurevisor import createInstance, createLogger


class InstanceParityTests(unittest.TestCase):
    def test_should_be_a_function(self) -> None:
        self.assertTrue(callable(createInstance))

    def test_should_create_instance_with_datafile_content(self) -> None:
        sdk = createInstance({"datafile": {"schemaVersion": "2", "revision": "1.0", "features": {}, "segments": {}}})
        self.assertTrue(callable(sdk.getVariation))

    def test_should_configure_plain_and_and_or_bucket_by(self) -> None:
        cases = [
            ("plain", "userId", {"userId": "123"}, "123.test"),
            ("and", ["userId", "organizationId"], {"userId": "123", "organizationId": "456"}, "123.456.test"),
            ("or", {"or": ["userId", "deviceId"]}, {"deviceId": "456"}, "456.test"),
        ]
        for _, bucket_by, context, expected_bucket in cases:
            captured = {"bucket": ""}
            sdk = createInstance(
                {
                    "datafile": {
                        "schemaVersion": "2",
                        "revision": "1.0",
                        "features": {
                            "test": {
                                "key": "test",
                                "bucketBy": bucket_by,
                                "variations": [{"value": "control"}, {"value": "treatment"}],
                                "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 100000]}, {"variation": "treatment", "range": [0, 0]}]}],
                            }
                        },
                        "segments": {},
                    },
                    "hooks": [{"name": "unit-test", "bucketKey": lambda options, captured=captured: captured.update(bucket=options["bucketKey"]) or options["bucketKey"]}],
                    "logLevel": "fatal",
                }
            )
            self.assertEqual(sdk.getVariation("test", context), "control")
            self.assertEqual(captured["bucket"], expected_bucket)

    def test_should_intercept_before_and_after_hooks(self) -> None:
        before = {"called": False, "featureKey": "", "variableKey": None}
        sdk = createInstance(
            {
                "datafile": {
                    "schemaVersion": "2",
                    "revision": "1.0",
                    "features": {"test": {"key": "test", "bucketBy": "userId", "variations": [{"value": "control"}, {"value": "treatment"}], "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 100000]}, {"variation": "treatment", "range": [0, 0]}]}]}},
                    "segments": {},
                },
                "hooks": [
                    {"name": "before", "before": lambda options: before.update(called=True, featureKey=options["featureKey"], variableKey=options.get("variableKey")) or options},
                    {"name": "after", "after": lambda evaluation, options: {**evaluation, "variationValue": "control_intercepted"}},
                ],
                "logLevel": "fatal",
            }
        )
        self.assertEqual(sdk.getVariation("test", {"userId": "123"}), "control_intercepted")
        self.assertTrue(before["called"])
        self.assertEqual(before["featureKey"], "test")
        self.assertIsNone(before["variableKey"])

    def test_should_initialize_with_sticky_features(self) -> None:
        datafile = {
            "schemaVersion": "2",
            "revision": "1.0",
            "features": {
                "test": {
                    "key": "test",
                    "bucketBy": "userId",
                    "variations": [{"value": "control"}, {"value": "treatment"}],
                    "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 0]}, {"variation": "treatment", "range": [0, 100000]}]}],
                }
            },
            "segments": {},
        }
        sdk = createInstance({"sticky": {"test": {"enabled": True, "variation": "control", "variables": {"color": "red"}}}, "logLevel": "fatal"})
        self.assertEqual(sdk.getVariation("test", {"userId": "123"}), "control")
        self.assertEqual(sdk.getVariable("test", "color", {"userId": "123"}), "red")
        sdk.setDatafile(datafile)
        time.sleep(0.08)
        self.assertEqual(sdk.getVariation("test", {"userId": "123"}), "control")
        sdk.setSticky({}, True)
        self.assertEqual(sdk.getVariation("test", {"userId": "123"}), "treatment")

    def test_required_deprecated_and_rule_override_cases(self) -> None:
        logger_calls = []
        sdk = createInstance(
            {
                "datafile": {
                    "schemaVersion": "2",
                    "revision": "1.0",
                    "features": {
                        "requiredKey": {"key": "requiredKey", "bucketBy": "userId", "variations": [{"value": "control"}, {"value": "treatment"}], "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 0]}, {"variation": "treatment", "range": [0, 100000]}]}]},
                        "myKey": {"key": "myKey", "bucketBy": "userId", "required": [{"key": "requiredKey", "variation": "treatment"}], "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": []}]},
                        "deprecatedTest": {"key": "deprecatedTest", "deprecated": True, "bucketBy": "userId", "variations": [{"value": "control"}, {"value": "treatment"}], "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 100000]}, {"variation": "treatment", "range": [0, 0]}]}]},
                        "flagOverride": {"key": "flagOverride", "bucketBy": "userId", "traffic": [{"key": "2", "segments": ["netherlands"], "percentage": 100000, "enabled": False, "allocation": []}, {"key": "1", "segments": "*", "percentage": 100000, "allocation": []}]},
                    },
                    "segments": {"netherlands": {"key": "netherlands", "conditions": '[{"attribute":"country","operator":"equals","value":"nl"}]'}},
                },
                "logger": createLogger({"handler": lambda level, message, details=None: logger_calls.append((level, message))}),
            }
        )
        self.assertTrue(sdk.isEnabled("myKey"))
        self.assertEqual(sdk.getVariation("deprecatedTest", {"userId": "123"}), "control")
        self.assertTrue(any(level == "warn" and "deprecated" in message for level, message in logger_calls))
        self.assertTrue(sdk.isEnabled("flagOverride", {"userId": "user-123", "country": "de"}))
        self.assertFalse(sdk.isEnabled("flagOverride", {"userId": "user-123", "country": "nl"}))

    def test_mutually_exclusive_variation_and_variable_cases(self) -> None:
        bucket = {"value": 10000}
        sdk = createInstance(
            {
                "hooks": [{"name": "unit-test", "bucketValue": lambda options, bucket=bucket: bucket["value"]}],
                "datafile": {
                    "schemaVersion": "2",
                    "revision": "1.0",
                    "segments": {
                        "netherlands": {"key": "netherlands", "conditions": '[{"attribute":"country","operator":"equals","value":"nl"}]'},
                        "belgium": {"key": "belgium", "conditions": '[{"attribute":"country","operator":"equals","value":"be"}]'},
                        "germany": {"key": "germany", "conditions": '[{"attribute":"country","operator":"equals","value":"de"}]'},
                        "mobile": {"key": "mobile", "conditions": '[{"attribute":"device","operator":"equals","value":"mobile"}]'},
                    },
                    "features": {
                        "mutex": {"key": "mutex", "bucketBy": "userId", "ranges": [[0, 50000]], "traffic": [{"key": "1", "segments": "*", "percentage": 50000, "allocation": []}]},
                        "test": {
                            "key": "test",
                            "bucketBy": "userId",
                            "variablesSchema": {
                                "color": {"key": "color", "type": "string", "defaultValue": "red"},
                                "showSidebar": {"key": "showSidebar", "type": "boolean", "defaultValue": False},
                                "sidebarTitle": {"key": "sidebarTitle", "type": "string", "defaultValue": "sidebar title"},
                                "count": {"key": "count", "type": "integer", "defaultValue": 0},
                                "price": {"key": "price", "type": "double", "defaultValue": 9.99},
                                "paymentMethods": {"key": "paymentMethods", "type": "array", "defaultValue": ["paypal", "creditcard"]},
                                "flatConfig": {"key": "flatConfig", "type": "object", "defaultValue": {"key": "value"}},
                                "nestedConfig": {"key": "nestedConfig", "type": "json", "defaultValue": '{"key":{"nested":"value"}}'},
                            },
                            "variations": [{"value": "control"}, {"value": "treatment", "variables": {"showSidebar": True, "sidebarTitle": "sidebar title from variation"}, "variableOverrides": {"showSidebar": [{"segments": ["netherlands"], "value": False}, {"conditions": [{"attribute": "country", "operator": "equals", "value": "de"}], "value": False}], "sidebarTitle": [{"segments": ["netherlands"], "value": "Dutch title"}, {"conditions": [{"attribute": "country", "operator": "equals", "value": "de"}], "value": "German title"}]}}],
                            "force": [{"conditions": [{"attribute": "userId", "operator": "equals", "value": "user-ch"}], "enabled": True, "variation": "control", "variables": {"color": "red and white"}}, {"conditions": [{"attribute": "userId", "operator": "equals", "value": "user-gb"}], "enabled": False}, {"conditions": [{"attribute": "userId", "operator": "equals", "value": "user-forced-variation"}], "enabled": True, "variation": "treatment"}],
                            "traffic": [{"key": "2", "segments": ["belgium"], "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 0]}, {"variation": "treatment", "range": [0, 100000]}], "variation": "control", "variables": {"color": "black"}}, {"key": "1", "segments": "*", "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 0]}, {"variation": "treatment", "range": [0, 100000]}]}],
                        },
                        "testWithNoVariation": {"key": "testWithNoVariation", "bucketBy": "userId", "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": []}]},
                    },
                },
                "logLevel": "fatal",
            }
        )
        bucket["value"] = 40000
        self.assertTrue(sdk.isEnabled("mutex", {"userId": "123"}))
        bucket["value"] = 60000
        self.assertFalse(sdk.isEnabled("mutex", {"userId": "123"}))
        self.assertEqual(sdk.getVariation("test", {"userId": "123"}), "treatment")
        self.assertIsNone(sdk.getVariation("nonExistingFeature", {"userId": "123"}))
        self.assertIsNone(sdk.getVariation("test", {"userId": "user-gb"}))
        self.assertIsNone(sdk.getVariation("testWithNoVariation", {"userId": "123"}))
        self.assertEqual(sdk.getVariable("test", "color", {"userId": "123"}), "red")
        self.assertEqual(sdk.getVariable("test", "color", {"userId": "123", "country": "be"}), "black")
        self.assertEqual(sdk.getVariable("test", "color", {"userId": "user-ch"}), "red and white")
        self.assertEqual(sdk.getVariableBoolean("test", "showSidebar", {"userId": "123"}), True)
        self.assertEqual(sdk.getVariableBoolean("test", "showSidebar", {"userId": "123", "country": "nl"}), False)
        self.assertEqual(sdk.getVariableString("test", "sidebarTitle", {"userId": "user-forced-variation", "country": "de"}), "German title")
        self.assertEqual(sdk.getVariableInteger("test", "count", {"userId": "123"}), 0)
        self.assertEqual(sdk.getVariableDouble("test", "price", {"userId": "123"}), 9.99)
        self.assertEqual(sdk.getVariableArray("test", "paymentMethods", {"userId": "123"}), ["paypal", "creditcard"])
        self.assertEqual(sdk.getVariableObject("test", "flatConfig", {"userId": "123"}), {"key": "value"})
        self.assertEqual(sdk.getVariableJSON("test", "nestedConfig", {"userId": "123"}), {"key": {"nested": "value"}})
        self.assertIsNone(sdk.getVariable("test", "nonExisting", {"userId": "123"}))
        self.assertIsNone(sdk.getVariable("test", "color", {"userId": "user-gb"}))
        all_evaluations = sdk.getAllEvaluations({"userId": "123"})
        self.assertEqual(all_evaluations["test"]["variation"], "treatment")

    def test_variables_without_variations_rule_overrides_arrays_objects_and_individual_segments(self) -> None:
        sdk = createInstance(
            {
                "datafile": {
                    "schemaVersion": "2",
                    "revision": "1.0",
                    "segments": {
                        "netherlands": {"key": "netherlands", "conditions": '[{"attribute":"country","operator":"equals","value":"nl"}]'},
                        "germany": {"key": "germany", "conditions": '[{"attribute":"country","operator":"equals","value":"de"}]'},
                        "mobile": {"key": "mobile", "conditions": '[{"attribute":"device","operator":"equals","value":"mobile"}]'},
                        "iphone": {"key": "iphone", "conditions": '[{"attribute":"device","operator":"equals","value":"iphone"}]'},
                        "unitedStates": {"key": "unitedStates", "conditions": '[{"attribute":"country","operator":"equals","value":"us"}]'},
                    },
                    "features": {
                        "test": {
                            "key": "test",
                            "bucketBy": "userId",
                            "variablesSchema": {"color": {"key": "color", "type": "string", "defaultValue": "red"}},
                            "traffic": [{"key": "netherlands", "segments": "netherlands", "percentage": 100000, "variables": {"color": "orange"}, "allocation": []}, {"key": "everyone", "segments": "*", "percentage": 100000, "allocation": []}],
                        },
                        "ruleOverrideTest": {
                            "key": "ruleOverrideTest",
                            "bucketBy": "userId",
                            "variablesSchema": {"config": {"key": "config", "type": "object", "defaultValue": {"source": "default", "nested": {"value": 0}}}},
                            "traffic": [{"key": "germany", "segments": "germany", "percentage": 100000, "variables": {"config": {"source": "rule", "nested": {"value": 10}, "flag": True}}, "variableOverrides": {"config": [{"segments": "mobile", "value": {"source": "rule", "nested": {"value": 20}, "flag": True}}, {"conditions": [{"attribute": "country", "operator": "equals", "value": "de"}], "value": {"source": "rule", "nested": {"value": 30}, "flag": True}}]}}, {"key": "everyone", "segments": "*", "percentage": 100000, "variables": {"config": {"source": "everyone", "nested": {"value": 1}}}, "allocation": []}],
                        },
                        "withArray": {"key": "withArray", "bucketBy": "userId", "variablesSchema": {"simpleArray": {"key": "simpleArray", "type": "array", "defaultValue": ["red", "blue", "green"]}, "simpleStringArray": {"key": "simpleStringArray", "type": "array", "defaultValue": ["red", "blue", "green"]}, "objectArray": {"key": "objectArray", "type": "array", "defaultValue": [{"color": "red", "opacity": 100}, {"color": "blue", "opacity": 90}, {"color": "green", "opacity": 95}]}}, "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": []}]},
                        "withObject": {"key": "withObject", "bucketBy": "userId", "variablesSchema": {"themeConfig": {"key": "themeConfig", "type": "object", "defaultValue": {"theme": "light", "darkMode": False}}, "headerConfig": {"key": "headerConfig", "type": "object", "defaultValue": {"style": {"fontSize": 18, "bold": True}, "title": "Welcome"}}, "mixedConfig": {"key": "mixedConfig", "type": "object", "defaultValue": {"name": "mixed", "enabled": True, "meta": {"score": 0.95, "items": ["a", "b"]}}}}, "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": []}]},
                        "flagTest": {"key": "flagTest", "bucketBy": "userId", "traffic": [{"key": "1", "segments": "netherlands", "percentage": 100000, "allocation": []}, {"key": "2", "segments": '["iphone","unitedStates"]', "percentage": 100000, "allocation": []}]},
                    },
                },
                "logLevel": "fatal",
            }
        )
        self.assertEqual(sdk.getVariable("test", "color", {"userId": "123"}), "red")
        self.assertEqual(sdk.getVariable("test", "color", {"userId": "123", "country": "nl"}), "orange")
        self.assertEqual(sdk.getVariableObject("ruleOverrideTest", "config", {"userId": "user-1", "country": "de"}), {"source": "rule", "nested": {"value": 30}, "flag": True})
        self.assertEqual(sdk.getVariableObject("ruleOverrideTest", "config", {"userId": "user-1", "country": "de", "device": "mobile"}), {"source": "rule", "nested": {"value": 20}, "flag": True})
        self.assertEqual(sdk.getVariableObject("ruleOverrideTest", "config", {"userId": "user-1", "country": "nl"}), {"source": "everyone", "nested": {"value": 1}})
        self.assertEqual(sdk.getVariableArray("withArray", "simpleArray", {"userId": "user-1"}), ["red", "blue", "green"])
        self.assertEqual(sdk.getVariableArray("withArray", "objectArray", {"userId": "user-1"}), [{"color": "red", "opacity": 100}, {"color": "blue", "opacity": 90}, {"color": "green", "opacity": 95}])
        self.assertEqual(sdk.getVariableObject("withObject", "themeConfig", {"userId": "user-1"}), {"theme": "light", "darkMode": False})
        self.assertIsNone(sdk.getVariableArray("withArray", "nonExisting", {"userId": "user-1"}))
        all_evaluations = sdk.getAllEvaluations({"userId": "user-1"})
        self.assertEqual(all_evaluations["withObject"]["variables"]["mixedConfig"], {"name": "mixed", "enabled": True, "meta": {"score": 0.95, "items": ["a", "b"]}})
        self.assertFalse(sdk.isEnabled("flagTest"))
        self.assertFalse(sdk.isEnabled("flagTest", {"userId": "123"}))
        self.assertTrue(sdk.isEnabled("flagTest", {"userId": "123", "country": "nl"}))
        self.assertTrue(sdk.isEnabled("flagTest", {"userId": "123", "country": "us", "device": "iphone"}))


if __name__ == "__main__":
    unittest.main()
