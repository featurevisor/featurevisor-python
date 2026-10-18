from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from featurevisor import create_featurevisor


class DiagnosticsConformanceTests(unittest.TestCase):
    def test_shared_diagnostic_trace(self):
        fixture = json.loads(Path("conformance/sdk-diagnostics.json").read_text())
        self.assertEqual(fixture["kind"], "featurevisor.sdk-diagnostics")
        self.assertEqual(fixture["version"], 1)
        self.assertTrue(fixture["cases"])
        for case in fixture["cases"] * 2:
            captured = []
            reentries = []
            reentering = False
            f = create_featurevisor({"logLevel": "error"})

            def diagnostic(item):
                nonlocal reentering
                if item["code"] != "conditions_parse_error":
                    return
                captured.append({"code": item["code"], "level": item["level"],
                                 "details": copy.deepcopy(item["details"]), "revision": f.get_revision()})
                if item["details"]["entityType"] == "feature" and not reentering:
                    reentering = True
                    try:
                        start = len(captured)
                        f.is_enabled(item["details"]["entityKey"])
                        reentries.append(len(captured) - start)
                    finally:
                        reentering = False

            f.on_diagnostic = diagnostic
            try:
                for index, operation in enumerate(case["operations"]):
                    with self.subTest(case=case["name"], operation=index):
                        start = len(captured)
                        if operation["action"] == "setDatafile":
                            result = f.set_datafile(operation["datafile"], operation["replace"])
                        elif operation["action"] == "isEnabled":
                            result = f.is_enabled(operation["feature"])
                        else:
                            self.fail(f"Unsupported action: {operation['action']}")
                        self.assertIs(result, operation["expectedResult"])
                        self.assertCountEqual(captured[start:], operation["diagnostics"])
                self.assertTrue(reentries)
                self.assertEqual(reentries, [0] * len(reentries))
            finally:
                f.close()
