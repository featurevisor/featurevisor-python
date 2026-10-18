from __future__ import annotations

import unittest

from featurevisor import create_featurevisor


class ModulePhaseSnapshotTests(unittest.TestCase):
    def test_membership_matrix(self):
        phases = ("beforeEvaluation", "bucketKey", "bucketValue", "afterEvaluation")
        for global_root in (False, True):
            for changed_phase in phases:
                for action in ("add", "remove_add", "remove", "unsubscribe"):
                    with self.subTest(global_root=global_root, phase=changed_phase, action=action):
                        trace = []
                        closed = []
                        changed = False

                        def hook(name, phase):
                            def call(*args):
                                nonlocal changed
                                options = args[-1]
                                active = phase in ("bucketKey", "bucketValue") or (
                                    options.get("variableKey") == "subject" if global_root
                                    else options.get("featureKey") == "gate"
                                )
                                if active:
                                    trace.append(f"{phase}:{name}")
                                    if name == "first" and phase == changed_phase and not changed:
                                        changed = True
                                        if action == "unsubscribe":
                                            unsubscribe()
                                        elif action != "add":
                                            sdk.remove_module("other")
                                        if action in ("add", "remove_add"):
                                            sdk.add_module(module("late"))
                                if phase in ("bucketKey", "bucketValue"):
                                    return options[phase]
                                return args[0]
                            return call

                        def module(name):
                            return {"name": name, "close": lambda: closed.append(name),
                                    **{phase: hook(name, phase) for phase in phases}}

                        sdk = create_featurevisor({"logLevel": "fatal", "datafile": {
                            "schemaVersion": "4", "revision": "phases", "segments": {},
                            "features": {"gate": {"bucketBy": "userId", "traffic": [
                                {"key": "all", "conditions": "*", "percentage": 100000}]}},
                            "variables": {"subject": {"type": "string", "defaultValue": "matched",
                                                       "conditions": {"feature": "gate"}}}}})
                        sdk.add_module(module("first"))
                        unsubscribe = sdk.add_module(module("other"))
                        try:
                            def evaluate():
                                result = sdk.evaluate_variable("subject") if global_root else sdk.evaluate_flag("gate")
                                self.assertEqual(result.get("variableValue") if global_root else result.get("enabled"),
                                                 "matched" if global_root else True)

                            evaluate()
                            after = ["first"] + (["other"] if action == "add" else []) + (
                                ["late"] if action in ("add", "remove_add") else [])
                            expected = [f"{phase}:{name}" for index, phase in enumerate(phases)
                                        for name in (["first", "other"] if index <= phases.index(changed_phase) else after)]
                            self.assertEqual(trace, expected)
                            self.assertEqual(closed, [] if action == "add" else ["other"])
                            trace.clear()
                            evaluate()
                            self.assertEqual(trace, [f"{phase}:{name}" for phase in phases for name in after])
                        finally:
                            sdk.close()


if __name__ == "__main__":
    unittest.main()
