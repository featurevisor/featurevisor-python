import json
import unittest

from featurevisor import create_featurevisor
from featurevisor.evaluate import EvaluationReason


class RequiredFeaturesReasonTests(unittest.TestCase):
    def test_unmet_feature_reason_in_evaluation_and_callbacks(self):
        observed = []

        def after(evaluation, options):
            if evaluation.get("featureKey") == "subject":
                observed.append(evaluation["reason"])
            return evaluation

        f = create_featurevisor({
            "datafile": {
                "schemaVersion": "2", "revision": "reason", "segments": {},
                "features": {"subject": {
                    "bucketBy": "userId", "requiredFeatures": ["missing"], "traffic": [],
                }},
            },
            "logLevel": "fatal",
            "modules": [{"name": "observe", "afterEvaluation": after}],
        })
        self.addCleanup(f.close)
        evaluation = f.evaluate_flag("subject")
        self.assertEqual(evaluation["reason"], EvaluationReason.CONDITIONS_UNMET)
        self.assertIs(evaluation["enabled"], False)
        self.assertEqual(observed, ["conditions_unmet"])
        wire = json.loads(json.dumps(evaluation))
        self.assertEqual(wire["reason"], "conditions_unmet")
        self.assertEqual(wire["conditions"], [{"feature": "missing"}])
        self.assertFalse(hasattr(EvaluationReason, "REQUIRED"))
        with self.assertRaises(ValueError):
            EvaluationReason("required")
