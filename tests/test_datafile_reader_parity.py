from __future__ import annotations

import sys
import unittest

sys.path.insert(0, "src")

from featurevisor import DatafileReader, createLogger


class DatafileReaderParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.logger = createLogger()

    def test_should_be_a_function(self) -> None:
        self.assertTrue(callable(DatafileReader))

    def test_v2_datafile_schema_should_return_requested_entities(self) -> None:
        datafile_json = {
            "schemaVersion": "2",
            "revision": "1",
            "segments": {
                "netherlands": {"key": "netherlands", "conditions": [{"attribute": "country", "operator": "equals", "value": "nl"}]},
                "germany": {"key": "germany", "conditions": '[{"attribute":"country","operator":"equals","value":"de"}]'},
            },
            "features": {
                "test": {
                    "key": "test",
                    "bucketBy": "userId",
                    "variations": [{"value": "control"}, {"value": "treatment", "variables": {"showSidebar": True}}],
                    "traffic": [{"key": "1", "segments": "*", "percentage": 100000, "allocation": [{"variation": "control", "range": [0, 0]}, {"variation": "treatment", "range": [0, 100000]}]}],
                },
                "testWithNoVariations": {"key": "testWithNoVariations", "bucketBy": "userId", "traffic": [{"key": "1", "segments": "*", "percentage": 100000}]},
            },
        }
        reader = DatafileReader(datafile=datafile_json, logger=self.logger)
        self.assertEqual(reader.getRevision(), "1")
        self.assertEqual(reader.getSchemaVersion(), "2")
        self.assertEqual(reader.getSegment("netherlands"), datafile_json["segments"]["netherlands"])
        self.assertEqual(reader.getSegment("germany")["conditions"][0]["value"], "de")
        self.assertIsNone(reader.getSegment("belgium"))
        self.assertEqual(reader.getFeature("test"), datafile_json["features"]["test"])
        self.assertEqual(reader.getVariableKeys("test"), [])
        self.assertIsNone(reader.getFeature("test2"))
        self.assertEqual(reader.getVariableKeys("test2"), [])
        self.assertFalse(reader.hasVariations("testWithNoVariations"))
        self.assertFalse(reader.hasVariations("unknownFeature"))
        self.assertTrue(reader.allSegmentsAreMatched("*", {}))
        self.assertFalse(reader.allSegmentsAreMatched("unknownSegment", {}))
        self.assertFalse(reader.allSegmentsAreMatched({"and": ["unknownSegment"]}, {}))

    def test_segments_cases(self) -> None:
        groups = {
            "*": "*",
            "dutchMobileUsers": ["mobileUsers", "netherlands"],
            "dutchMobileUsers2": {"and": ["mobileUsers", "netherlands"]},
            "dutchMobileOrDesktopUsers": ["netherlands", {"or": ["mobileUsers", "desktopUsers"]}],
            "dutchMobileOrDesktopUsers2": {"and": ["netherlands", {"or": ["mobileUsers", "desktopUsers"]}]},
            "germanMobileUsers": [{"and": ["mobileUsers", "germany"]}],
            "germanNonMobileUsers": [{"and": ["germany", {"not": ["mobileUsers"]}]}],
            "notVersion5.5": [{"not": ["version_5.5"]}],
        }
        datafile = {
            "schemaVersion": "2",
            "revision": "1",
            "features": {},
            "segments": {
                "mobileUsers": {"key": "mobileUsers", "conditions": [{"attribute": "deviceType", "operator": "equals", "value": "mobile"}]},
                "desktopUsers": {"key": "desktopUsers", "conditions": [{"attribute": "deviceType", "operator": "equals", "value": "desktop"}]},
                "chromeBrowser": {"key": "chromeBrowser", "conditions": [{"attribute": "browser", "operator": "equals", "value": "chrome"}]},
                "firefoxBrowser": {"key": "firefoxBrowser", "conditions": [{"attribute": "browser", "operator": "equals", "value": "firefox"}]},
                "netherlands": {"key": "netherlands", "conditions": [{"attribute": "country", "operator": "equals", "value": "nl"}]},
                "germany": {"key": "germany", "conditions": [{"attribute": "country", "operator": "equals", "value": "de"}]},
                "version_5.5": {"key": "version_5.5", "conditions": [{"or": [{"attribute": "version", "operator": "equals", "value": "5.5"}, {"attribute": "version", "operator": "equals", "value": 5.5}]}]},
            },
        }
        reader = DatafileReader(datafile=datafile, logger=self.logger)
        matches = [
            ("*", {}, True),
            ("*", {"foo": "foo"}, True),
            ("dutchMobileUsers", {"country": "nl", "deviceType": "mobile"}, True),
            ("dutchMobileUsers", {"country": "de", "deviceType": "mobile"}, False),
            ("dutchMobileUsers2", {"country": "nl", "deviceType": "mobile", "browser": "chrome"}, True),
            ("dutchMobileOrDesktopUsers", {"country": "nl", "deviceType": "desktop"}, True),
            ("dutchMobileOrDesktopUsers2", {"country": "de", "deviceType": "desktop"}, False),
            ("germanMobileUsers", {"country": "de", "deviceType": "mobile"}, True),
            ("germanNonMobileUsers", {"country": "de", "deviceType": "desktop"}, True),
            ("notVersion5.5", {"version": "5.5"}, False),
            ("notVersion5.5", {"version": 5.6}, True),
        ]
        for key, context, expected in matches:
            with self.subTest(key=key, context=context):
                self.assertEqual(reader.allSegmentsAreMatched(groups[key], context), expected)


if __name__ == "__main__":
    unittest.main()
