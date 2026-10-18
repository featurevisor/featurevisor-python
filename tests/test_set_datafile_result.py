import copy
import io
import json
import unittest
from contextlib import redirect_stdout

from featurevisor import create_featurevisor


def datafile(key="original", revision="1"):
    return {
        "schemaVersion": "2", "revision": revision,
        "segments": {key: {"conditions": "*"}},
        "features": {key: {"hash": key, "bucketBy": "userId", "traffic": [
            {"key": "all", "segments": "*", "percentage": 100000}
        ]}},
        "variables": {key: {"hash": key, "type": "string", "defaultValue": key}},
    }


class SetDatafileResultTest(unittest.TestCase):
    def test_accepts_empty_datafile_and_changed_content_with_the_same_revision(self):
        sdk = create_featurevisor({"logLevel": "fatal"})
        self.addCleanup(sdk.close)
        empty = {"schemaVersion": "2", "revision": "1", "segments": {}, "features": {}}
        self.assertIs(sdk.set_datafile(empty), True)
        self.assertIs(sdk.set_datafile(json.dumps(empty)), True)
        self.assertIs(sdk.set_datafile(datafile(), True), True)
        self.assertTrue(sdk.is_enabled("original"))
        self.assertIs(sdk.set_datafile(empty, True), True)
        self.assertEqual(sdk.get_feature_keys(), [])
        self.assertEqual(sdk.get_variable_keys(), [])
        self.assertEqual(sdk.get_revision(), "1")

    def test_accepts_native_and_json_merge_replacement_and_unchanged_for_both_names(self):
        for method in ("set_datafile", "setDatafile"):
            for encode in (lambda value: value, json.dumps):
                for replace in (False, True):
                    with self.subTest(method=method, encode=encode, replace=replace):
                        sdk = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
                        self.addCleanup(sdk.close)
                        child = sdk.spawn()
                        self.addCleanup(child.close)
                        events = []
                        sdk.on("datafile_set", events.append)
                        incoming = datafile("next", "2")
                        original = copy.deepcopy(incoming)
                        setter = getattr(sdk, method)
                        self.assertIs(setter(encode(incoming), True) if replace else setter(encode(incoming)), True)
                        self.assertEqual(incoming, original)
                        self.assertEqual(sdk.get_revision(), "2")
                        self.assertEqual(sdk.get_feature_keys(), ["next"] if replace else ["original", "next"])
                        self.assertEqual(sdk.get_variable_keys(), ["next"] if replace else ["original", "next"])
                        self.assertEqual(sdk.get_segment("original") is None, replace)
                        self.assertTrue(child.is_enabled("next"))
                        self.assertEqual(child.get_variable("next"), "next")
                        self.assertEqual(events[0], {
                            "revision": "2", "previousRevision": "1", "revisionChanged": True,
                            "features": ["original", "next"] if replace else ["next"],
                            "variables": ["original", "next"] if replace else ["next"], "replaced": replace,
                        })
                        self.assertIs(setter(encode(incoming), replace), True)
                        self.assertEqual(len(events), 2)
                        self.assertEqual(events[1], {
                            "revision": "2", "previousRevision": "2", "revisionChanged": False,
                            "features": [], "variables": [], "replaced": replace,
                        })

    def test_rejection_preserves_state_and_reports_once_at_each_log_level(self):
        invalid = ["{", "null", "[]", None, False, 1, [], {},
                   {**datafile(), "schemaVersion": 2}, {**datafile(), "revision": 2},
                   {**datafile(), "segments": []}, {**datafile(), "features": []},
                   {**datafile(), "variables": []}]
        for level in ("info", "fatal"):
            diagnostics, events, errors = [], [], []
            sdk = create_featurevisor({"datafile": datafile(), "logLevel": level, "onDiagnostic": diagnostics.append})
            self.addCleanup(sdk.close)
            child = sdk.spawn()
            self.addCleanup(child.close)
            diagnostics.clear()
            sdk.on("datafile_set", events.append)
            sdk.on("error", errors.append)
            for value in invalid:
                for replace in (False, True):
                    with self.subTest(level=level, value=value, replace=replace):
                        self.assertIs(sdk.set_datafile(value, replace), False)
                        self.assertEqual(sdk.get_revision(), "1")
                        self.assertEqual(sdk.get_feature_keys(), ["original"])
                        self.assertIsNotNone(sdk.get_segment("original"))
                        self.assertTrue(child.is_enabled("original"))
                        self.assertEqual(child.get_variable("original"), "original")
                        self.assertEqual(errors[-1]["diagnostic"]["code"], "invalid_datafile")
                        self.assertEqual(errors[-1]["diagnostic"]["message"], "Could not parse datafile")
                        self.assertIsInstance(errors[-1]["diagnostic"]["originalError"], Exception)
            self.assertEqual(events, [])
            self.assertEqual(len(errors), len(invalid) * 2)
            self.assertEqual(len(diagnostics), len(invalid) * 2 if level == "info" else 0)
            self.assertIs(sdk.set_datafile(datafile(), True), True)

    def test_closed_returns_false_without_parsing_diagnostics_or_events(self):
        diagnostics = []
        sdk = create_featurevisor({"datafile": datafile(), "onDiagnostic": diagnostics.append})
        sdk.close()
        diagnostics.clear()
        for method in (sdk.set_datafile, sdk.setDatafile):
            for value in (datafile("next", "2"), "{"):
                self.assertIs(method(value), False)
        self.assertEqual(sdk.get_revision(), "1")
        self.assertEqual(diagnostics, [])

    def test_throwing_callbacks_do_not_change_acceptance_or_rejection(self):
        diagnostics, events, errors = [], [], []

        def fail(value):
            raise RuntimeError("callback failed")

        def diagnostic(value):
            diagnostics.append(value["code"])
            fail(value)

        with redirect_stdout(io.StringIO()):
            sdk = create_featurevisor({
                "onDiagnostic": diagnostic,
                "modules": [{"name": "throwing", "setup": lambda api: api["onDiagnostic"](fail)}],
            })
            self.addCleanup(sdk.close)
            diagnostics.clear()
            sdk.on("datafile_set", fail)
            sdk.on("datafile_set", events.append)
            sdk.on("error", fail)
            sdk.on("error", errors.append)
            self.assertIs(sdk.set_datafile(datafile()), True)
            self.assertIs(sdk.set_datafile(datafile()), True)
            self.assertIs(sdk.set_datafile("{"), False)
        self.assertEqual(diagnostics, ["datafile_set", "datafile_set", "invalid_datafile"])
        self.assertEqual(len(events), 2)
        self.assertEqual(len(errors), 1)
        self.assertEqual(sdk.get_revision(), "1")
