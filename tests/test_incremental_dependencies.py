from __future__ import annotations

import gc
import copy
import json
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from featurevisor import create_featurevisor
from featurevisor.dependencies import extract_entity_dependencies
from featurevisor._json import strict_json_loads


def datafile(revision="initial", **values):
    return {"schemaVersion": "4", "revision": revision, "segments": {}, "features": {}, "variables": {}, **values}


def feature(**values):
    return {"hash": "stable", "bucketBy": "userId",
            "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}], **values}


class IncrementalDependencyTests(unittest.TestCase):
    def test_equal_actual_definitions_reuse_edges_in_both_schemas_and_modes(self):
        for schema in ("2", "4"):
            for replace in (False, True):
                with self.subTest(schema=schema, replace=replace):
                    initial = datafile(schemaVersion=schema,
                        segments={"gate": {"conditions": '{"variable":"source","operator":"notNull"}'}},
                        features={"root": feature(conditions="gate")},
                        variables={"source": {"hash": "one", "type": "integer", "defaultValue": 1},
                                   "alias": {"hash": "stable", "compose": "source"},
                                   "hashless": {"type": "integer", "defaultValue": 0}})
                    sdk = create_featurevisor({"datafile": initial, "logLevel": "fatal"})
                    self.addCleanup(sdk.close)
                    self.assertTrue(sdk.set_datafile(copy.deepcopy(initial)))
                    events = []
                    sdk.on("datafile_set", events.append)
                    with patch("featurevisor.dependencies.extract_entity_dependencies", wraps=extract_entity_dependencies) as extract:
                        incoming = copy.deepcopy(initial)
                        incoming["revision"] = "equal"
                        self.assertTrue(sdk.set_datafile(incoming, replace))
                        extract.assert_not_called()
                        self.assertEqual(events[-1]["variables"], ["hashless"])
                        self.assertEqual(events[-1]["features"], [])
                        incoming = copy.deepcopy(initial)
                        incoming["variables"]["alias"]["compose"] = "hashless"
                        self.assertTrue(sdk.set_datafile(incoming, replace))
                        self.assertEqual(extract.call_count, 1)
                        self.assertEqual(sdk.datafile.dependency_index.owners[("variable", "alias")],
                                         (("variable", "hashless"),))
                        # Payload changes require comparison of the whole definition too.
                        incoming = copy.deepcopy(incoming)
                        incoming["variables"]["alias"]["metadata"] = {"conditions": "ignored"}
                        self.assertTrue(sdk.set_datafile(incoming, replace))
                        self.assertEqual(extract.call_count, 2)
                        self.assertNotIn(("segment", "ignored"), sdk.datafile.dependency_index.reverse)

    def test_edge_reuse_does_not_retain_diagnostics_or_removed_normalized_entities(self):
        for schema in ("2", "4"):
            for replace in (False, True):
                with self.subTest(schema=schema, replace=replace):
                    diagnostics = []
                    initial = datafile(schemaVersion=schema, segments={"broken": {"conditions": "{bad"}},
                        features={"removed": feature()}, variables={"removed": {"type": "integer", "defaultValue": 1}})
                    sdk = create_featurevisor({"datafile": initial, "logLevel": "error", "onDiagnostic": diagnostics.append})
                    self.addCleanup(sdk.close)
                    sdk.datafile.get_segment("broken")
                    sdk.datafile.get_feature("removed")
                    sdk.datafile.get_global_variable("removed")
                    self.assertEqual(len(diagnostics), 1)
                    self.assertTrue(sdk.set_datafile(copy.deepcopy(initial), replace))
                    sdk.datafile.get_segment("broken")
                    sdk.datafile.get_segment("broken")
                    self.assertEqual([d["code"] for d in diagnostics], ["conditions_parse_error"] * 2)
                    self.assertTrue(sdk.set_datafile(datafile("empty", schemaVersion=schema), True))
                    self.assertEqual(sdk.datafile.entity_cache, {})
                    self.assertEqual(sdk.datafile._entity_slots, {})
                    self.assertEqual(sdk.datafile.dependency_index.owners, {})
                    self.assertEqual(sdk.datafile.dependency_index.reverse, {})
                    self.assertIsNone(sdk.datafile.get_feature("removed"))
                    self.assertIsNone(sdk.datafile.get_global_variable("removed"))

    def test_dependency_extraction_preserves_selector_order_and_ignores_values(self):
        definition = {
            "conditions": '{"and":["segment",{"variable":"global"},'
                          '{"feature":"flag","variable":"value"}]}',
            "force": [{"conditions": {"or": ["segment", {"feature": "forced"}]}}],
            "traffic": [{"conditions": "traffic", "variableOverrides": {
                "value": [{"conditions": {"not": [{"variable": "rule"}]}}]}}],
            "variations": [{"variableOverrides": {"value": [
                {"conditions": '"variation"'}, {"conditions": "{"}]}}],
            "overrides": [{"conditions": {"variable": "override"}}],
            "overrideProgram": [{"conditions": {"feature": "program"}}],
            "variableOverrides": {"value": [{"conditions": {"variable": "last"}}]},
            "compose": [{"feature": "flag", "variable": "value"}, {"variable": "composed"}],
            "defaultValue": {"conditions": "not-a-dependency"},
            "metadata": {"conditions": "also-not-a-dependency"},
        }
        self.assertEqual(extract_entity_dependencies(definition), (
            ("segment", "segment"), ("variable", "global"), ("feature", "flag"),
            ("feature", "forced"), ("segment", "traffic"), ("variable", "rule"),
            ("segment", "variation"), ("variable", "override"), ("feature", "program"),
            ("variable", "last"), ("variable", "composed"),
        ))
        self.assertEqual(extract_entity_dependencies({"compose": "alias"}), (("variable", "alias"),))
        self.assertEqual(extract_entity_dependencies({"traffic": None, "variableOverrides": None}), ())

    def test_full_merge_and_replace_refresh_same_hash_edges(self):
        for replace in (False, True):
            with self.subTest(replace=replace):
                initial = datafile(features={"root": feature(conditions={"variable": "old"})},
                    variables={"old": {"type": "integer", "defaultValue": 1, "hash": "1"},
                               "new": {"type": "integer", "defaultValue": 2, "hash": "2"}})
                sdk = create_featurevisor({"datafile": initial, "logLevel": "fatal"})
                self.addCleanup(sdk.close)
                events = []
                sdk.on("datafile_set", events.append)
                incoming = {**initial, "revision": "edges",
                            "features": {"root": feature(conditions={"variable": "new"})}}
                self.assertTrue(sdk.set_datafile(incoming, replace))
                self.assertEqual(events[-1]["features"], [])
                self.assertEqual(events[-1]["variables"], [])
                self.assertEqual(events[-1]["replaced"], replace)
                self.assertTrue(sdk.set_datafile(datafile("old", variables={
                    "old": {"type": "integer", "defaultValue": 3, "hash": "3"}})))
                self.assertEqual(events[-1]["features"], [])
                self.assertEqual(events[-1]["variables"], ["old"])
                self.assertTrue(sdk.set_datafile(datafile("new", variables={
                    "new": {"type": "integer", "defaultValue": 4, "hash": "4"}})))
                self.assertEqual(events[-1]["features"], ["root"])
                self.assertEqual(events[-1]["variables"], ["new"])

    def test_unrelated_entities_do_not_increase_cached_graph_traversal(self):
        for size in (16, 512):
            with self.subTest(size=size):
                sdk = create_featurevisor({"logLevel": "fatal", "datafile": datafile(
                    segments={f"s{i}": {"conditions": '{"attribute":"country","operator":"notNull"}'} for i in range(size)},
                    features={f"f{i}": feature(conditions=f"s{i}") for i in range(size)},
                    variables={"source": {"type": "integer", "defaultValue": 1, "hash": "1"}})})
                self.addCleanup(sdk.close)
                self.assertTrue(sdk.set_datafile(datafile("warm")))
                index = sdk.datafile.dependency_index
                with patch.object(index, "get", wraps=index.get) as visit, patch(
                    "featurevisor.dependencies.extract_entity_dependencies", wraps=extract_entity_dependencies
                ) as extract:
                    self.assertTrue(sdk.set_datafile(datafile("next", variables={"source": {
                        "type": "integer", "defaultValue": 2, "hash": "2"}})))
                    self.assertEqual(visit.call_count, 2)
                    self.assertEqual(extract.call_count, 1)

    def test_only_incoming_entities_are_extracted_and_parsed_after_the_first_update(self):
        for size in (8, 128, 1024):
            with self.subTest(size=size):
                condition = json.dumps({"variable": "source", "operator": "notNull"})
                source = datafile(
                    segments={f"s{i}": {"conditions": condition} for i in range(size)},
                    features={f"f{i}": feature(conditions=f"s{i}") for i in range(size)},
                    variables={"source": {"type": "integer", "defaultValue": 1, "hash": "1"},
                               **{f"v{i}": {"type": "integer", "compose": "source", "hash": "stable"} for i in range(size)}})
                with patch("featurevisor.dependencies.extract_entity_dependencies", wraps=extract_entity_dependencies) as extract, patch(
                    "featurevisor.dependencies.strict_json_loads", wraps=strict_json_loads
                ) as parse:
                    sdk = create_featurevisor({"datafile": source, "logLevel": "fatal"})
                    self.addCleanup(sdk.close)
                    extract.assert_not_called()
                    parse.assert_not_called()
                    events = []
                    sdk.on("datafile_set", events.append)
                    self.assertTrue(sdk.set_datafile(datafile("cold", variables={"source": {
                        "type": "integer", "defaultValue": 2, "hash": "2"}})))
                    self.assertEqual(extract.call_count, size * 3 + 2)
                    self.assertEqual(parse.call_count, size)
                    index = sdk.datafile.dependency_index
                    extract.reset_mock()
                    parse.reset_mock()
                    for revision in range(3):
                        self.assertTrue(sdk.set_datafile(datafile(str(revision), variables={"source": {
                            "type": "integer", "defaultValue": revision, "hash": str(revision), "conditions": condition}})))
                        self.assertIs(sdk.datafile.dependency_index, index)
                        self.assertEqual(events[-1]["features"], [f"f{i}" for i in range(size)])
                        self.assertEqual(events[-1]["variables"], ["source"] + [f"v{i}" for i in range(size)])
                    self.assertEqual(extract.call_count, 3)
                    self.assertEqual(parse.call_count, 3)

    def test_retained_segments_are_not_deep_compared(self):
        class RetainedSegment(dict):
            def __eq__(self, other):
                raise AssertionError("retained segment compared")

            __ne__ = __eq__

        sdk = create_featurevisor({"datafile": datafile(segments={"retained": RetainedSegment(conditions="*")}), "logLevel": "fatal"})
        self.addCleanup(sdk.close)
        self.assertTrue(sdk.set_datafile(datafile("next")))

    def test_old_then_new_graph_does_not_walk_new_then_old_paths(self):
        sdk = create_featurevisor({"datafile": datafile(variables={
            "source": {"type": "integer", "defaultValue": 1, "hash": "1"},
            "bridge": {"type": "integer", "defaultValue": 0, "hash": "same"},
            "unreachable": {"type": "integer", "compose": "bridge", "hash": "same"}}), "logLevel": "fatal"})
        self.addCleanup(sdk.close)
        events = []
        sdk.on("datafile_set", events.append)
        self.assertTrue(sdk.set_datafile(datafile("next", variables={
            "source": {"type": "integer", "defaultValue": 2, "hash": "2"},
            "bridge": {"type": "integer", "compose": "source", "hash": "same"},
            "unreachable": {"type": "integer", "defaultValue": 0, "hash": "same"}})))
        self.assertEqual(events[-1]["variables"], ["source", "bridge"])

    def test_segment_chains_aliases_compositions_overrides_removals_and_replacement(self):
        source = datafile(segments={"leaf": {"conditions": {"variable": "source", "operator": "notNull"}},
                                   "chain": {"conditions": "leaf"}}, features={
            "root": feature(conditions="chain"),
            "override": feature(traffic=[{"key": "all", "conditions": "*", "percentage": 100000,
                "variableOverrides": {"message": [{"conditions": {"variable": "alias", "operator": "notNull"}, "value": "yes"}]}}])},
            variables={"source": {"type": "integer", "defaultValue": 1, "hash": "1"},
                       "alias": {"type": "integer", "compose": "source", "hash": "same"},
                       "view": {"type": "object", "compose": [{"key": "flag", "feature": "root"}], "hash": "same"}})
        sdk = create_featurevisor({"datafile": source, "logLevel": "fatal"})
        self.addCleanup(sdk.close)
        events = []
        sdk.on("datafile_set", events.append)
        for revision in ("2", "3"):
            self.assertTrue(sdk.set_datafile(datafile(revision, variables={"source": {
                "type": "integer", "defaultValue": 2, "hash": revision}})))
            self.assertEqual(set(events[-1]["features"]), {"root", "override"})
            self.assertEqual(set(events[-1]["variables"]), {"source", "alias", "view"})
        self.assertTrue(sdk.set_datafile(datafile("segments", segments={"chain": {"conditions": "*"}})))
        self.assertEqual(events[-1]["features"], ["root"])
        self.assertEqual(events[-1]["variables"], ["view"])
        self.assertTrue(sdk.set_datafile(datafile("removed"), True))
        self.assertEqual(sdk.datafile.dependency_index.owners, {})
        self.assertEqual(sdk.datafile.dependency_index.reverse, {})
        self.assertTrue(sdk.set_datafile(datafile("fresh", variables={"source": {"type": "integer", "defaultValue": 3, "hash": "3"}})))
        self.assertEqual(events[-1]["features"], [])
        self.assertEqual(events[-1]["variables"], ["source"])

    def test_concurrent_partial_writers_cannot_lose_accepted_updates(self):
        sdk = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        self.addCleanup(sdk.close)
        entered, release = threading.Event(), threading.Event()
        original = sdk._merge_datafiles

        def blocked_merge(previous, incoming):
            if incoming["revision"] == "first":
                entered.set()
                self.assertTrue(release.wait(5))
            return original(previous, incoming)

        with patch.object(sdk, "_merge_datafiles", blocked_merge), ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(sdk.set_datafile, datafile("first", variables={"one": {"type": "integer", "defaultValue": 1, "hash": "1"}}))
            self.assertTrue(entered.wait(5))
            second = pool.submit(sdk.set_datafile, datafile("second", variables={"two": {"type": "integer", "compose": "one", "hash": "2"}}))
            release.set()
            self.assertTrue(first.result(timeout=5))
            self.assertTrue(second.result(timeout=5))
        self.assertEqual(sdk.get_variable("two"), 1)
        self.assertEqual(set(sdk.get_variable_keys()), {"one", "two"})

    def test_event_callbacks_run_outside_writer_lock_and_history_is_not_retained(self):
        sdk = create_featurevisor({"datafile": datafile(variables={"source": {"type": "integer", "defaultValue": 1, "hash": "1"}}), "logLevel": "fatal"})
        self.addCleanup(sdk.close)
        old = weakref.ref(sdk.datafile)

        def on_event(event):
            if event["revision"] == "first":
                with ThreadPoolExecutor(max_workers=1) as pool:
                    self.assertTrue(pool.submit(sdk.set_datafile, datafile("callback")).result(timeout=5))

        sdk.on("datafile_set", on_event)
        self.assertTrue(sdk.set_datafile(datafile("first")))
        self.assertEqual(sdk.get_revision(), "callback")
        gc.collect()
        self.assertIsNone(old())
        sdk.close()
        self.assertIsNone(sdk.datafile.dependency_index)


if __name__ == "__main__":
    unittest.main()
