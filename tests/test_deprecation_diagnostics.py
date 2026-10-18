from __future__ import annotations

import json
import unittest

from featurevisor import create_featurevisor

DATAFILE = json.loads(
    '{"schemaVersion":"4","revision":"1","segments":{},"features":{"legacy":{"deprecated":true,'
    '"bucketBy":"userId","variablesSchema":{"old":{"type":"string","defaultValue":"x","deprecated":true}},'
    '"traffic":[{"key":"all","conditions":"*","percentage":100000,"allocation":[]}]}},'
    '"variables":{"oldGlobal":{"type":"string","defaultValue":"y","deprecated":true}}}'
)


class DeprecationDiagnosticsTests(unittest.TestCase):
    def test_deprecated_features_and_variables_use_one_code_per_kind(self) -> None:
        captured: list[str] = []
        f = create_featurevisor({
            "datafile": DATAFILE,
            "logLevel": "warn",
            "onDiagnostic": lambda d: captured.append(d["code"]) if "deprecated" in d["code"] else None,
        })
        try:
            f.is_enabled("legacy")
            self.assertEqual(set(captured), {"deprecated_feature"})

            captured.clear()
            f.get_variable("legacy", "old")
            self.assertEqual(set(captured), {"deprecated_feature", "deprecated_variable"})

            captured.clear()
            f.get_variable("oldGlobal")
            self.assertEqual(captured, ["deprecated_variable"])
        finally:
            f.close()


if __name__ == "__main__":
    unittest.main()
