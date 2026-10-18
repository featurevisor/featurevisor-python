import json
import unittest

from featurevisor import create_featurevisor


def chain(length):
    features = {}
    for index in range(length):
        conditions = "*" if index == 0 else json.dumps([{"feature": f"f{index - 1}"}])
        features[f"f{index}"] = {
            "bucketBy": "userId",
            "traffic": [{"key": "all", "conditions": conditions, "percentage": 100000}],
        }
    return {"schemaVersion": "4", "revision": "depth", "segments": {}, "features": features}


class DependencyErrorDiagnosticsTest(unittest.TestCase):
    def test_dependency_failures_report_once_for_each_public_call(self):
        errors = []
        f = create_featurevisor({
            "datafile": chain(300),
            "logLevel": "error",
            "onDiagnostic": lambda diagnostic: errors.append(diagnostic["code"]) if diagnostic["level"] == "error" else None,
        })
        try:
            self.assertFalse(f.is_enabled("f299", {"userId": "u"}))
            self.assertIsNone(f.get_variation("f299", {"userId": "u"}))
            self.assertEqual(errors, ["evaluation_error", "evaluation_error"])
            self.assertTrue(f.is_enabled("f20", {"userId": "u"}))
            self.assertEqual(len(errors), 2)
        finally:
            f.close()


if __name__ == "__main__":
    unittest.main()
