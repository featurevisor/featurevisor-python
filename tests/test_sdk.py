from __future__ import annotations

import sys
import unittest

sys.path.insert(0, "src")

from featurevisor import DatafileReader, create_instance
from featurevisor.bucketer import MAX_BUCKETED_NUMBER, get_bucket_key, get_bucketed_number
from featurevisor.compare_versions import compare_versions
from featurevisor.conditions import condition_is_matched
from featurevisor.logger import create_logger


class SDKTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
