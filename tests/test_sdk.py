from __future__ import annotations

import sys
import unittest
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, "src")

from featurevisor import DatafileReader, Emitter, FeaturevisorChildInstance, create_instance
from featurevisor.bucketer import MAX_BUCKETED_NUMBER, get_bucket_key, get_bucketed_number
from featurevisor.compare_versions import compare_versions
from featurevisor.conditions import condition_is_matched
from featurevisor.events import get_params_for_datafile_set_event, get_params_for_sticky_set_event
from featurevisor.logger import create_logger


class SDKTests(unittest.TestCase):
    def test_known_bucket_numbers(self) -> None:
        expected = {
            "foo": 20602,
            "bar": 89144,
            "123.foo": 3151,
            "123.bar": 9710,
            "123.456.foo": 14432,
            "123.456.bar": 1982,
        }
        for key, value in expected.items():
            self.assertEqual(get_bucketed_number(key), value)

    def test_compare_versions(self) -> None:
        self.assertEqual(compare_versions("1.2.3", "1.2.3"), 0)
        self.assertEqual(compare_versions("1.2.4", "1.2.3"), 1)
        self.assertEqual(compare_versions("1.2.2", "1.2.3"), -1)

    def test_condition_exists_and_not_exists(self) -> None:
        get_regex = lambda value, flags="": __import__("re").compile(value)
        self.assertTrue(condition_is_matched({"attribute": "country", "operator": "exists"}, {"country": None}, get_regex))
        self.assertTrue(condition_is_matched({"attribute": "country", "operator": "notExists"}, {}, get_regex))

    def test_bucket_key_keeps_none_values(self) -> None:
        logger = create_logger({"level": "fatal"})
        bucket_key = get_bucket_key(featureKey="my_feature", bucketBy="userId", context={"userId": None}, logger=logger)
        self.assertEqual(bucket_key, "None.my_feature")

    def test_bucketed_number_range(self) -> None:
        value = get_bucketed_number("foo.bar")
        self.assertGreaterEqual(value, 0)
        self.assertLess(value, MAX_BUCKETED_NUMBER)

    def test_bucket_key_variants(self) -> None:
        logger = create_logger({"level": "fatal"})
        self.assertEqual(get_bucket_key(featureKey="test-feature", bucketBy="userId", context={"userId": "123"}, logger=logger), "123.test-feature")
        self.assertEqual(get_bucket_key(featureKey="test-feature", bucketBy=["organizationId", "user.id"], context={"organizationId": "123", "user": {"id": "234"}}, logger=logger), "123.234.test-feature")
        self.assertEqual(get_bucket_key(featureKey="test-feature", bucketBy={"or": ["userId", "deviceId"]}, context={"deviceId": "deviceIdHere"}, logger=logger), "deviceIdHere.test-feature")

    def test_instance_basic_flow(self) -> None:
        datafile = {
            "schemaVersion": "2",
            "revision": "1",
            "segments": {"everyone": {"conditions": "*"}},
            "features": {
                "my_feature": {
                    "bucketBy": "userId",
                    "traffic": [{"key": "everyone", "segments": "everyone", "percentage": 100000}],
                    "variablesSchema": {"title": {"type": "string", "defaultValue": "Hello"}},
                }
            },
        }
        instance = create_instance({"datafile": datafile, "context": {"userId": "123"}})
        self.assertTrue(instance.is_enabled("my_feature"))
        self.assertEqual(instance.get_variable("my_feature", "title"), "Hello")
        self.assertEqual(instance.get_all_evaluations()["my_feature"]["variables"]["title"], "Hello")

    def test_datafile_reader_parses_stringified_conditions(self) -> None:
        reader = DatafileReader(
            datafile={
                "schemaVersion": "2",
                "revision": "1",
                "segments": {"eu": {"conditions": '[{"attribute":"country","operator":"equals","value":"nl"}]'}},
                "features": {},
            },
            logger=create_logger({"level": "fatal"}),
        )
        segment = reader.get_segment("eu")
        self.assertTrue(reader.segment_is_matched(segment, {"country": "nl"}))

    def test_events_helpers(self) -> None:
        self.assertEqual(
            get_params_for_sticky_set_event({"feature1": {"enabled": True}}, {"feature2": {"enabled": True}}, True),
            {"features": ["feature1", "feature2"], "replaced": True},
        )
        logger = create_logger({"level": "fatal"})
        previous = DatafileReader(datafile={"schemaVersion": "2", "revision": "1", "segments": {}, "features": {"feature1": {"bucketBy": "userId", "hash": "hash1", "traffic": []}}}, logger=logger)
        current = DatafileReader(datafile={"schemaVersion": "2", "revision": "2", "segments": {}, "features": {"feature1": {"bucketBy": "userId", "hash": "hash2", "traffic": []}, "feature2": {"bucketBy": "userId", "hash": "hash3", "traffic": []}}}, logger=logger)
        self.assertEqual(
            get_params_for_datafile_set_event(previous, current),
            {"revision": "2", "previousRevision": "1", "revisionChanged": True, "features": ["feature1", "feature2"]},
        )

    def test_emitter_subscribe_unsubscribe(self) -> None:
        emitter = Emitter()
        handled = []
        unsubscribe = emitter.on("datafile_set", lambda details: handled.append(details))
        emitter.trigger("datafile_set", {"key": "value"})
        emitter.trigger("sticky_set", {"key": "value2"})
        self.assertEqual(handled, [{"key": "value"}])
        unsubscribe()
        self.assertEqual(len(emitter.listeners["datafile_set"]), 0)
        emitter.clear_all()
        self.assertEqual(dict(emitter.listeners), {})

    def test_logger_handler_and_filtering(self) -> None:
        calls = []
        logger = create_logger({"level": "warn", "handler": lambda level, message, details=None: calls.append((level, message, details))})
        logger.info("nope")
        logger.warn("yes")
        logger.error("yep")
        self.assertEqual(calls, [("warn", "yes", None), ("error", "yep", None)])

    def test_child_instance_flow(self) -> None:
        datafile = {
            "schemaVersion": "2",
            "revision": "1",
            "segments": {
                "belgium": {"conditions": [{"attribute": "country", "operator": "equals", "value": "be"}]},
            },
            "features": {
                "test": {
                    "bucketBy": "userId",
                    "variablesSchema": {
                        "color": {"type": "string", "defaultValue": "red"},
                        "nestedConfig": {"type": "json", "defaultValue": '{"key":{"nested":"value"}}'},
                    },
                    "variations": [{"value": "control"}],
                    "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "variation": "control", "variables": {"color": "black"}}],
                }
            },
        }
        instance = create_instance({"datafile": datafile, "context": {"appVersion": "1.0.0"}, "logLevel": "fatal"})
        child = instance.spawn({"userId": "123", "country": "be"})
        changes = []
        unsubscribe = child.on("context_set", lambda details: changes.append(details))
        child.set_context({"country": "be"})
        self.assertEqual(child.get_context(), {"appVersion": "1.0.0", "userId": "123", "country": "be"})
        self.assertTrue(child.is_enabled("test"))
        self.assertEqual(child.get_variation("test"), "control")
        self.assertEqual(child.get_variable("test", "color"), "black")
        self.assertEqual(child.get_variable_json("test", "nestedConfig"), {"key": {"nested": "value"}})
        unsubscribe()
        self.assertTrue(changes)

    def test_default_variable_json_parsing(self) -> None:
        datafile = {
            "schemaVersion": "2",
            "revision": "1",
            "segments": {},
            "features": {
                "test": {
                    "bucketBy": "userId",
                    "traffic": [{"key": "1", "segments": "*", "percentage": 100000}],
                    "variablesSchema": {"config": {"type": "json", "defaultValue": '{"enabled":true}'}},
                }
            },
        }
        instance = create_instance({"datafile": datafile, "context": {"userId": "123"}, "logLevel": "fatal"})
        self.assertEqual(instance.get_variable("test", "config"), {"enabled": True})


if __name__ == "__main__":
    unittest.main()
