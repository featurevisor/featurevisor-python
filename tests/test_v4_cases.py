from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from featurevisor import create_featurevisor


class SDKV4PortableCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(Path("conformance/sdk-v4-cases.json").read_text())

    def test_all_portable_cases_twice(self) -> None:
        self.assertEqual(self.fixture["version"], 1)
        self.assertEqual(len(self.fixture["cases"]), 255)
        for case in self.fixture["cases"]:
            datafile = copy.deepcopy(case["datafile"])
            context = copy.deepcopy(case.get("context", {}))
            options = copy.deepcopy(case.get("options", {}))
            original_datafile = copy.deepcopy(datafile)
            original_context = copy.deepcopy(context)
            original_options = copy.deepcopy(options)
            sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
            try:
                for run in range(2):
                    with self.subTest(case=case["name"], run=run):
                        value = sdk.get_variable("subject", context, options)
                        evaluation = sdk.evaluate_variable("subject", context, options)
                        expected = case["expected"]
                        self.assertEqual(value, expected["value"])
                        for key, expected_value in expected.items():
                            if key == "value":
                                continue
                            self.assertEqual(evaluation.get(key), expected_value, key)
                self.assertEqual(datafile, original_datafile)
                self.assertEqual(context, original_context)
                self.assertEqual(options, original_options)
            finally:
                sdk.close()

    def test_precomputed_and_runtime_programs_stay_equivalent_across_child_state_and_updates(self) -> None:
        datafiles = {
            case["name"]: case["datafile"]
            for case in self.fixture["cases"]
            if case["name"] in {
                "first match precomputed stringified: child",
                "first match runtime stringified: child",
            }
        }
        self.assertEqual(len(datafiles), 2)
        expected_child = {
            "stage": "parent",
            "rows": [{"id": 0, "tags": []}, {"id": 1, "tags": ["parent", "child"]}],
            "flags": ["a"],
        }
        expected_parent = {
            "stage": "parent", "rows": [{"id": 1, "tags": ["parent"]}], "flags": ["a"],
        }
        expected_reset = {"stage": "reset", "rows": [], "flags": []}
        expected_default = {
            "stage": "default", "rows": [{"id": 1, "tags": []}], "flags": [None, "a"],
        }

        pairs = [
            ("first match precomputed stringified: child", "first match runtime stringified: child"),
            ("first match runtime stringified: child", "first match precomputed stringified: child"),
        ]
        for initial, replacement in pairs:
            with self.subTest(initial=initial):
                root = create_featurevisor({
                    "datafile": copy.deepcopy(datafiles[initial]),
                    "context": {"region": "eu", "eligible": True},
                    "logLevel": "fatal",
                })
                child = root.spawn({"country": "nl"})
                sticky_child = root.spawn(
                    {"country": "nl"}, {"stickyVariables": {"permission": False}}
                )
                try:
                    self.assertEqual(child.get_variable("subject"), expected_child)
                    self.assertEqual(sticky_child.get_variable("subject"), expected_parent)
                    child.set_context({"reset": True})
                    self.assertEqual(child.get_variable("subject"), expected_reset)

                    root.set_datafile({
                        "schemaVersion": "2",
                        "revision": "cache-update",
                        "segments": {
                            "europe": {
                                "conditions": '[{"attribute":"region","operator":"equals","value":"asia"}]'
                            }
                        },
                        "features": {},
                        "variables": {},
                    })
                    self.assertEqual(child.get_variable("subject"), expected_default)

                    child.set_context({"reset": False})
                    root.set_datafile(copy.deepcopy(datafiles[replacement]), True)
                    self.assertEqual(child.get_variable("subject"), expected_child)
                finally:
                    sticky_child.close()
                    child.close()
                    root.close()


if __name__ == "__main__":
    unittest.main()
