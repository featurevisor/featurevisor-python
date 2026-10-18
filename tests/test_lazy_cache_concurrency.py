from __future__ import annotations

import gc
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from featurevisor import create_featurevisor
from featurevisor.normalize import normalize_entity


def datafile(revision="initial", **values):
    return {"schemaVersion": "4", "revision": revision, "segments": {}, "features": {}, "variables": {}, **values}


def feature(**values):
    return {"bucketBy": "userId", "hash": "stable",
            "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}], **values}


class ContendedLock:
    """Hold all first readers at the lock boundary until everyone arrives."""

    def __init__(self, lock, readers):
        self.lock = lock
        self.barrier = threading.Barrier(readers, timeout=5)
        self.entries = 0
        self.count_lock = threading.Lock()

    def __enter__(self):
        with self.count_lock:
            self.entries += 1
        self.barrier.wait()
        self.lock.acquire()

    def __exit__(self, *args):
        self.lock.release()


class LazyCacheConcurrencyTests(unittest.TestCase):
    def test_warm_entity_reads_do_not_acquire_normalization_lock(self):
        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(
            features={"warm": feature()}, segments={"warm": {"conditions": "*"}},
            variables={"warm": {"type": "integer", "defaultValue": 1}})})
        self.addCleanup(f.close)
        provider = f.datafile
        reads = (provider.get_feature, provider.get_segment, provider.get_global_variable)
        expected = [read("warm") for read in reads]
        original_lock = provider._entity_lock

        class UnexpectedLock:
            def __enter__(self):
                raise AssertionError("Warm entity read acquired the normalization lock")

            def __exit__(self, *args):
                pass

        provider._entity_lock = UnexpectedLock()
        try:
            def reader():
                for _ in range(1000):
                    for read, value in zip(reads, expected):
                        self.assertIs(read("warm"), value)
            with ThreadPoolExecutor(max_workers=10) as pool:
                futures = [pool.submit(reader) for _ in range(10)]
                for future in futures:
                    future.result(timeout=10)
        finally:
            provider._entity_lock = original_lock

    def test_concurrent_cold_reads_normalize_and_diagnose_once_at_every_entity_kind(self):
        for kind in ("feature", "segment", "variable"):
            with self.subTest(kind=kind):
                diagnostics = []
                d = datafile(**{{"feature": "features", "segment": "segments", "variable": "variables"}[kind]:
                               {"broken": {"conditions": "{"}}})
                f = create_featurevisor({"datafile": d, "logLevel": "error", "onDiagnostic": diagnostics.append})
                self.addCleanup(f.close)
                provider = f.datafile
                read = {"feature": provider.get_feature, "segment": provider.get_segment,
                        "variable": provider.get_global_variable}[kind]
                original_lock = provider._entity_lock
                provider._entity_lock = ContendedLock(original_lock, 8)
                try:
                    with patch("featurevisor.evaluation_data_provider.normalize_entity", wraps=normalize_entity) as normalize:
                        with ThreadPoolExecutor(max_workers=8) as pool:
                            futures = [pool.submit(read, "broken") for _ in range(8)]
                            values = [future.result(timeout=10) for future in futures]
                        self.assertEqual(normalize.call_count, 1)
                    self.assertEqual(provider._entity_lock.entries, 8)
                    self.assertTrue(all(value is values[0] for value in values))
                    self.assertEqual(len(diagnostics), 1)
                    self.assertEqual(diagnostics[0]["code"], "conditions_parse_error")
                    self.assertEqual(diagnostics[0]["details"],
                                     {"conditions": "{", "entityType": kind, "entityKey": "broken"})
                finally:
                    provider._entity_lock = original_lock

    def test_cold_slots_are_shared_without_retaining_previous_providers(self):
        diagnostics = []
        f = create_featurevisor({"logLevel": "error", "onDiagnostic": diagnostics.append,
            "datafile": datafile(features={"broken": feature(conditions="{")},
                                 segments={"broken": {"conditions": "{"}},
                                 variables={"broken": {"type": "integer", "conditions": "{"}})})
        self.addCleanup(f.close)
        old = f.datafile
        self.assertTrue(f.set_datafile(datafile("partial")))
        new = f.datafile
        self.assertEqual(old.entity_cache, {})
        self.assertEqual(new.entity_cache, {})
        self.assertEqual(diagnostics, [])
        original_lock = old._entity_lock
        lock = ContendedLock(original_lock, 2)
        old._entity_lock = new._entity_lock = lock
        try:
            for method in ("get_feature", "get_segment", "get_global_variable"):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(getattr(old, method), "broken")
                    second = pool.submit(getattr(new, method), "broken")
                    self.assertIs(first.result(timeout=10), second.result(timeout=10))
        finally:
            old._entity_lock = new._entity_lock = original_lock
        self.assertEqual(len(diagnostics), 3)
        old_ref = weakref.ref(old)
        del old, first, second
        gc.collect()
        self.assertIsNone(old_ref())
        retained = new.get_feature("broken")
        self.assertTrue(f.set_datafile(datafile("replace", features={"broken": feature(conditions="{")})))
        self.assertFalse(f.is_enabled("broken"))
        self.assertIs(new.get_feature("broken"), retained)
        self.assertIsNot(f.get_feature("broken"), retained)
        self.assertEqual(len(diagnostics), 4)

    def test_diagnostic_reentry_and_refresh_do_not_hold_cache_lock(self):
        f = create_featurevisor({"logLevel": "error", "datafile": datafile(features={"broken": feature(conditions="{")})})
        self.addCleanup(f.close)
        observed = []
        with ThreadPoolExecutor(max_workers=1) as pool:
            def diagnostic(item):
                if item["code"] == "conditions_parse_error":
                    provider = f.datafile
                    def reenter():
                        value = provider.get_feature("broken")
                        accepted = f.set_datafile(datafile("reentrant"))
                        return value is not None, accepted
                    observed.append(pool.submit(reenter).result(timeout=5))
            f.on_diagnostic = diagnostic
            self.assertFalse(f.is_enabled("broken"))
        self.assertEqual(observed, [(True, True)])
        self.assertEqual(f.get_revision(), "reentrant")

    def test_refresh_snapshot_blocks_cold_insert_until_iteration_finishes(self):
        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(
            features={"warm": feature(), "cold": feature()})})
        self.addCleanup(f.close)
        old = f.datafile
        old.get_feature("warm")
        snapshot_started = threading.Event()
        reader_at_lock = threading.Event()
        original_lock = old._entity_lock

        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name == "cold-reader":
                    reader_at_lock.set()
                original_lock.acquire()

            def __exit__(self, *args):
                original_lock.release()

        class PausedCache(dict):
            def items(self):
                for item in super().items():
                    snapshot_started.set()
                    if not reader_at_lock.wait(5):
                        raise AssertionError("Reader did not reach cache lock")
                    yield item

        old._entity_lock = ObservedLock()
        old.entity_cache = PausedCache(old.entity_cache)
        reads = []
        def read():
            if snapshot_started.wait(5):
                reads.append(old.get_feature("cold"))
        reader = threading.Thread(target=read, name="cold-reader", daemon=True)
        reader.start()
        try:
            self.assertTrue(f.set_datafile(datafile("partial")))
            reader.join(5)
            self.assertFalse(reader.is_alive())
            self.assertEqual(len(reads), 1)
            self.assertIs(f.get_feature("cold"), reads[0])
        finally:
            old._entity_lock = f.datafile._entity_lock = original_lock

    def test_four_cold_readers_and_300_valid_partial_updates_have_zero_failures(self):
        rounds = 300
        diagnostics = []
        f = create_featurevisor({"logLevel": "error", "onDiagnostic": diagnostics.append,
            "datafile": datafile(features={f"f{i}": feature() for i in range(4 * rounds)})})
        self.addCleanup(f.close)
        barrier = threading.Barrier(5, timeout=20)

        def reader(index):
            failures = []
            try:
                for step in range(rounds):
                    barrier.wait()
                    result = f.evaluate_flag(f"f{step * 4 + index}")
                    if result.get("enabled") is not True or result.get("reason") != "rule":
                        failures.append(result)
                    barrier.wait()
                return failures
            except BaseException:
                barrier.abort()
                raise

        with ThreadPoolExecutor(max_workers=4) as pool:
            readers = [pool.submit(reader, index) for index in range(4)]
            accepted = []
            try:
                for step in range(rounds):
                    barrier.wait()
                    accepted.append(f.set_datafile(datafile(str(step), variables={
                        "updated": {"type": "integer", "defaultValue": step, "hash": str(step)}})))
                    barrier.wait()
                    self.assertEqual(f.get_variable("updated"), step)
            finally:
                if len(accepted) != rounds:
                    barrier.abort()
            self.assertEqual([future.result(timeout=20) for future in readers], [[], [], [], []])
        self.assertEqual(accepted, [True] * rounds)
        self.assertEqual(diagnostics, [])
