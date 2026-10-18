from __future__ import annotations

import unittest

from featurevisor import create_featurevisor


def feature(**values):
    return {"bucketBy": "userId", "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}], **values}


class LiveModuleTests(unittest.TestCase):
    def test_membership_refreshes_between_phases_for_feature_and_global_roots(self):
        for global_root in (False, True):
            for removal, disable_gate in ((False, False), (False, True), (True, False), (True, True)):
                with self.subTest(global_root=global_root, removal=removal, disable_gate=disable_gate):
                    trace = []
                    condition = {"or": [{"feature": "broken"}, {"feature": "gate"}]}

                    def key(options):
                        return options.get("featureKey") or options.get("variableKey")

                    def before(options):
                        trace.append("before:" + key(options))
                        return options

                    def after(result, options):
                        trace.append("after:" + key(options))
                        return {**result, "enabled": False} if disable_gate and key(options) == "gate" else result

                    module = {"name": "late", "beforeEvaluation": before, "afterEvaluation": after}

                    def on_diagnostic(diagnostic):
                        if diagnostic["code"] == "conditions_parse_error":
                            if removal:
                                sdk.remove_module("late")
                            else:
                                sdk.add_module(module)

                    sdk = create_featurevisor({"logLevel": "error", "onDiagnostic": on_diagnostic,
                        "modules": [module] if removal else [], "datafile": {
                            "schemaVersion": "4", "revision": "live", "segments": {}, "features": {
                                "broken": feature(conditions="[broken"), "gate": feature(),
                                "subject": feature(conditions=condition)}, "variables": {
                                    "subject": {"type": "string", "defaultValue": "matched", "conditions": condition}}}})
                    try:
                        evaluation = sdk.evaluate_variable("subject") if global_root else sdk.evaluate_flag("subject")
                        if disable_gate and not removal:
                            self.assertEqual(evaluation["reason"], "conditions_unmet")
                            if global_root:
                                self.assertIsNone(evaluation.get("variableValue"))
                            else:
                                self.assertFalse(evaluation.get("enabled"))
                        else:
                            self.assertEqual(evaluation.get("variableValue") if global_root else evaluation.get("enabled"),
                                             "matched" if global_root else True)
                        self.assertEqual(trace, ["before:subject", "before:broken"] if removal else [
                            "after:broken", "before:gate", "after:gate", "after:subject"])
                    finally:
                        sdk.close()

    def test_after_phase_uses_one_membership_snapshot(self):
        trace = []

        def first(result, options):
            trace.append("first")
            sdk.add_module({"name": "added", "afterEvaluation": added})
            sdk.remove_module("second")
            return result

        def second(result, options):
            trace.append("second")
            return result

        def added(result, options):
            trace.append("added")
            return result

        sdk = create_featurevisor({"logLevel": "fatal", "modules": [
            {"name": "first", "afterEvaluation": first}, {"name": "second", "afterEvaluation": second}]})
        self.addCleanup(sdk.close)
        sdk.evaluate_flag("missing")
        self.assertEqual(trace, ["first", "second"])


if __name__ == "__main__":
    unittest.main()
