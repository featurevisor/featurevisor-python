import copy
import json
import unittest

from featurevisor import create_featurevisor


def flag(key, percentage=100000):
    return {
        "key": key,
        "bucketBy": "userId",
        "traffic": [{"key": "1", "segments": "*", "percentage": percentage, "allocation": []}],
    }


def datafile(features):
    return {"schemaVersion": "2", "revision": "1", "segments": {}, "features": features}


class FeatureAllocationTests(unittest.TestCase):
    def test_legacy_ranges_are_unknown_fields_without_warnings(self):
        for ranges in (None, [], [[50000, 100000]], [[0, 100000]], "ignored"):
            for percentage in (0, 50000, 100000):
                for enabled in (None, False, True):
                    with self.subTest(ranges=ranges, percentage=percentage, enabled=enabled):
                        feature = flag("test", percentage)
                        if enabled is not None:
                            feature["traffic"][0]["enabled"] = enabled
                        baseline = create_featurevisor({"datafile": datafile({"test": feature}), "logLevel": "warn"})
                        legacy = copy.deepcopy(feature)
                        legacy["ranges"] = ranges
                        diagnostics = []
                        sdk = create_featurevisor({
                            "datafile": json.dumps(datafile({"test": legacy})),
                            "logLevel": "warn",
                            "onDiagnostic": diagnostics.append,
                        })
                        for user in range(32):
                            context = {"userId": f"user-{user}"}
                            self.assertEqual(sdk.evaluate_flag("test", context), baseline.evaluate_flag("test", context))
                        self.assertEqual(diagnostics, [])
                        sdk.close()
                        baseline.close()

    def test_required_variations_and_priority_exclusion_use_normal_hashing(self):
        features = {key: flag(key) for key in ("cohort", "experimentA", "experimentB", "priority", "fallback")}
        features["cohort"]["variations"] = [{"value": value} for value in ("a", "b", "holdout")]
        features["cohort"]["traffic"][0]["allocation"] = [
            {"variation": "a", "range": [0, 40000]},
            {"variation": "b", "range": [40000, 80000]},
            {"variation": "holdout", "range": [80000, 100000]},
        ]
        features["experimentA"]["requiredFeatures"] = [{"feature": "cohort", "variation": "a"}]
        features["experimentB"]["requiredFeatures"] = [{"feature": "cohort", "variation": "b"}]
        features["priority"]["traffic"][0]["percentage"] = 50000
        features["fallback"]["requiredFeatures"] = [{"feature": "priority", "enabled": False}]
        sdk = create_featurevisor({"datafile": datafile(features), "logLevel": "warn"})
        self.addCleanup(sdk.close)
        counts = {"a": 0, "b": 0, "holdout": 0, "priority": 0, "fallback": 0}
        cohort_overlap = priority_overlap = 0
        for user in range(20000):
            context = {"userId": f"user-{user}"}
            variation = sdk.get_variation("cohort", context)
            self.assertIn(variation, ("a", "b", "holdout"))
            counts[variation] += 1
            a = sdk.is_enabled("experimentA", context)
            b = sdk.is_enabled("experimentB", context)
            self.assertEqual(a, variation == "a")
            self.assertEqual(b, variation == "b")
            cohort_overlap += int(a and b)
            self.assertEqual(not a and not b, variation == "holdout")
            priority = sdk.is_enabled("priority", context)
            fallback = sdk.is_enabled("fallback", context)
            self.assertNotEqual(priority, fallback)
            priority_overlap += int(priority and fallback)
            counts["priority" if priority else "fallback"] += 1
        self.assertEqual(cohort_overlap, 0)
        self.assertEqual(priority_overlap, 0)
        self.assertEqual(counts["a"] + counts["b"] + counts["holdout"], 20000)
        self.assertEqual(counts["priority"] + counts["fallback"], 20000)
        # Allow two percentage points around each intended population share.
        for key, expected in {"a": 8000, "b": 8000, "holdout": 4000, "priority": 10000, "fallback": 10000}.items():
            self.assertLessEqual(abs(counts[key] - expected), 400, counts)
