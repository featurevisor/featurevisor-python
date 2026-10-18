from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from featurevisor import create_featurevisor


class SDKStatefulConformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(Path("conformance/sdk-stateful.json").read_text(encoding="utf-8"))

    @staticmethod
    def _normalize_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized = []
        for event in events:
            copy = {**event, "details": dict(event["details"])}
            for key in ("features", "variables"):
                if key in copy["details"]:
                    copy["details"][key] = sorted(copy["details"][key])
            normalized.append(copy)
        return normalized

    def test_all_stateful_cases(self) -> None:
        self.assertEqual(self.fixture["kind"], "featurevisor.sdk-stateful")
        self.assertEqual(self.fixture["schemaVersion"], 1)
        self.assertEqual(len(self.fixture["cases"]), 8)

        for case in self.fixture["cases"]:
            with self.subTest(case=case["name"]):
                diagnostics: list[dict[str, Any]] = []
                events: list[dict[str, Any]] = []
                targets: dict[str, Any] = {
                    "root": create_featurevisor({
                        "datafile": case["initial"]["datafile"],
                        "context": case["initial"].get("context", {}),
                        "logLevel": "error",
                        "onDiagnostic": diagnostics.append,
                    })
                }

                def watch(target_name: str) -> None:
                    target = targets[target_name]
                    for event_name in ("datafile_set", "sticky_features_set", "sticky_variables_set"):
                        target.on(
                            event_name,
                            lambda details, name=target_name, event=event_name: events.append({
                                "target": name,
                                "name": event,
                                "details": details,
                            }),
                        )

                watch("root")
                try:
                    for operation in case["operations"]:
                        events.clear()
                        diagnostics.clear()
                        error: Exception | None = None
                        try:
                            action = operation["action"]
                            if action == "setDatafile":
                                targets[operation["target"]].set_datafile(
                                    operation["datafile"], operation["replace"]
                                )
                            elif action == "spawn":
                                targets[operation["target"]] = targets[operation["parent"]].spawn(
                                    operation.get("context", {}), operation.get("options", {})
                                )
                                watch(operation["target"])
                            elif action == "setStickyFeatures":
                                targets[operation["target"]].set_sticky_features(
                                    operation["stickyFeatures"], operation["replace"]
                                )
                            elif action == "setStickyVariables":
                                targets[operation["target"]].set_sticky_variables(
                                    operation["stickyVariables"], operation["replace"]
                                )
                            else:
                                self.fail(f"Unsupported fixture action: {action}")
                        except Exception as caught:
                            error = caught

                        expected = operation["expect"]
                        self.assertEqual(
                            self._normalize_events(events),
                            self._normalize_events(expected["events"]),
                            case["name"],
                        )
                        if "error" in expected:
                            matching = [
                                diagnostic for diagnostic in diagnostics
                                if diagnostic.get("code") == expected["error"]["code"]
                            ]
                            self.assertTrue(matching, case["name"])
                            self.assertIsNone(error, case["name"])
                        else:
                            self.assertIsNone(error, case["name"])

                        for result in expected["results"]:
                            target = targets[result["target"]]
                            if result["method"] == "getRevision":
                                actual = target.get_revision()
                            elif result["method"] == "isEnabled":
                                actual = target.is_enabled(*result["args"])
                            elif result["method"] == "getVariable":
                                actual = target.get_variable(*result["args"])
                            else:
                                self.fail(f'Unsupported fixture method: {result["method"]}')
                            self.assertEqual(actual, result["value"], case["name"])
                finally:
                    for name, target in reversed(list(targets.items())):
                        if name != "root":
                            target.close()
                    targets["root"].close()


if __name__ == "__main__":
    unittest.main()
