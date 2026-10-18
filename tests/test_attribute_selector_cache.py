from __future__ import annotations

import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from featurevisor import create_featurevisor
from featurevisor.evaluation_data_provider import _InstanceEvaluationDataProvider


def feature(conditions):
    return {"bucketBy": "userId", "conditions": conditions,
            "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}]}


def datafile(expression, revision="initial"):
    return {"schemaVersion": "4", "revision": revision,
            "segments": {"audience": {"conditions": expression}},
            "features": {"segment": feature("audience"), "direct": feature(expression)}, "variables": {}}


class AttributeSelectorCacheTests(unittest.TestCase):
    def test_cached_syntax_does_not_cache_context_or_change_public_entities(self):
        expression = [{"attribute": "country", "operator": "equals", "value": "nl"},
                      {"attribute": "device", "operator": "equals", "value": "mobile"}]
        fixture = datafile(expression)
        original = copy.deepcopy(fixture)
        f = create_featurevisor({"logLevel": "fatal", "datafile": fixture})
        self.addCleanup(f.close)
        self.assertEqual(f.datafile.entity_cache, {})
        self.assertEqual(f.datafile._attribute_selectors, {})
        with patch.object(_InstanceEvaluationDataProvider, "_prepare_attribute_selector",
                          wraps=_InstanceEvaluationDataProvider._prepare_attribute_selector) as prepare:
            for _ in range(3):
                self.assertTrue(f.is_enabled("segment", {"country": "nl", "device": "mobile"}))
                self.assertFalse(f.is_enabled("segment", {"country": "de", "device": "mobile"}))
                self.assertFalse(f.is_enabled("segment", {"country": "nl", "device": "desktop"}))
            self.assertEqual(prepare.call_count, 3)
        original_lock = f.datafile._entity_lock
        class UnexpectedLock:
            def __enter__(self):
                raise AssertionError("Warm selector read acquired the normalization lock")
            def __exit__(self, *args):
                pass
        f.datafile._entity_lock = UnexpectedLock()
        try:
            self.assertTrue(f.is_enabled("segment", {"country": "nl", "device": "mobile"}))
        finally:
            f.datafile._entity_lock = original_lock
        self.assertEqual(f.get_segment("audience"), {"conditions": expression})
        self.assertEqual(f.evaluate_flag("direct", {})["conditions"], expression)
        self.assertEqual(fixture, original)
        old = f.datafile
        self.assertTrue(f.set_datafile(datafile({"or": []}, "replacement")))
        self.assertEqual(f.datafile._attribute_selectors, {})
        self.assertFalse(f.is_enabled("segment", {"country": "nl", "device": "mobile"}))
        self.assertTrue(old.all_conditions_are_matched(old.get_attribute_selector(expression),
                                                      {"country": "nl", "device": "mobile"}))
        f.close()
        self.assertEqual(f.datafile._attribute_selectors, {})

    def test_empty_and_nested_groups_keep_unified_selector_semantics(self):
        leaf = {"attribute": "country", "operator": "equals", "value": "nl"}
        expressions = [leaf, [], {"and": []}, {"or": []}, {"not": []}]
        for operator in ("and", "or", "not"):
            expressions.extend({operator: [empty, leaf]} for empty in ([], {"and": []}, {"or": []}, {"not": []}))
        expressions += [{"not": [{"or": [leaf, {"and": []}]}]}, [leaf, {"not": [{"not": [leaf]}]}]]
        for expression in expressions:
            with self.subTest(expression=expression):
                f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(expression)})
                try:
                    for country in ("nl", "de"):
                        for _ in range(2):
                            self.assertEqual(f.is_enabled("segment", {"country": country}),
                                             f.is_enabled("direct", {"country": country}))
                finally:
                    f.close()

    def test_mixed_dependencies_and_malformed_selectors_stay_on_dependency_path(self):
        for expression in ([{"attribute": "country", "operator": "equals", "value": "nl"}, {"feature": "direct"}],
                           {"not": ["audience"]}, "{"):
            with self.subTest(expression=expression):
                f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(expression)})
                self.addCleanup(f.close)
                result = f.evaluate_flag("segment", {"country": "nl"})
                self.assertEqual(result["reason"], "conditions_unmet" if expression == "{" else "error")
                normalized = f.get_segment("audience")["conditions"]
                self.assertIsNone(f.datafile.get_attribute_selector(normalized))

    def test_concurrent_preparation_is_published_once(self):
        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile([])})
        self.addCleanup(f.close)
        provider = f.datafile
        expression = [{"attribute": "country", "operator": "equals", "value": "nl"}]
        original_lock = provider._entity_lock
        barrier = threading.Barrier(8, timeout=5)

        class ContendedLock:
            def __enter__(self):
                barrier.wait()
                original_lock.acquire()

            def __exit__(self, *args):
                original_lock.release()

        provider._entity_lock = ContendedLock()
        try:
            with patch.object(_InstanceEvaluationDataProvider, "_prepare_attribute_selector",
                              wraps=_InstanceEvaluationDataProvider._prepare_attribute_selector) as prepare:
                with ThreadPoolExecutor(max_workers=8) as pool:
                    futures = [pool.submit(provider.get_attribute_selector, expression) for _ in range(8)]
                    results = [future.result(timeout=10) for future in futures]
                self.assertTrue(all(result is results[0] for result in results))
                self.assertEqual(prepare.call_count, 2)
        finally:
            provider._entity_lock = original_lock

    def test_predicate_diagnostics_run_outside_lock_and_later_reads_refresh(self):
        invalid_regex = {"attribute": "text", "operator": "matches", "value": "["}
        expression = {"or": [invalid_regex, {"attribute": "country", "operator": "equals", "value": "nl"}]}
        f = create_featurevisor({"logLevel": "warn", "datafile": datafile(expression)})
        self.addCleanup(f.close)
        diagnostics = []
        context = {"text": "hello", "country": "de"}
        with ThreadPoolExecutor(max_workers=1) as pool:
            def diagnostic(item):
                if item["code"] == "condition_match_error":
                    diagnostics.append(item)
                    item["details"]["context"]["country"] = "nl"
                    self.assertTrue(pool.submit(f.set_datafile, datafile({"or": []}, "callback")).result(timeout=5))
            f.on_diagnostic = diagnostic
            self.assertTrue(f.is_enabled("segment", context))
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["details"]["condition"], invalid_regex)
        self.assertFalse(f.is_enabled("segment", context))
        self.assertEqual(f.get_revision(), "callback")
