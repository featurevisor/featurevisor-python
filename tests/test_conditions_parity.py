from __future__ import annotations

import datetime as dt
import sys
import unittest

sys.path.insert(0, "src")

from featurevisor import DatafileReader, createLogger


class ConditionsParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.reader = DatafileReader(
            datafile={"schemaVersion": "2.0", "revision": "1", "segments": {}, "features": {}},
            logger=createLogger(),
        )

    def test_should_be_a_function(self) -> None:
        self.assertTrue(callable(self.reader.allConditionsAreMatched))

    def test_should_match_all_via_star(self) -> None:
        self.assertTrue(self.reader.allConditionsAreMatched("*", {"browser_type": "chrome"}))
        self.assertFalse(self.reader.allConditionsAreMatched("blah", {"browser_type": "chrome"}))

    def test_operator_cases(self) -> None:
        cases = [
            ([{"attribute": "browser_type", "operator": "equals", "value": "chrome"}], {"browser_type": "chrome"}, True),
            ([{"attribute": "browser_type", "operator": "equals", "value": "chrome"}], {"browser_type": "firefox"}, False),
            ([{"attribute": "browser.type", "operator": "equals", "value": "chrome"}], {"browser": {"type": "chrome"}}, True),
            ([{"attribute": "browser.type", "operator": "equals", "value": "chrome"}], {"browser": {"type": "firefox"}}, False),
            ([{"attribute": "browser_type", "operator": "notEquals", "value": "chrome"}], {"browser_type": "firefox"}, True),
            ([{"attribute": "browser_type", "operator": "notEquals", "value": "chrome"}], {"browser_type": "chrome"}, False),
            ([{"attribute": "browser_type", "operator": "exists"}], {"browser_type": "firefox"}, True),
            ([{"attribute": "browser_type", "operator": "exists"}], {"not_browser_type": "chrome"}, False),
            ([{"attribute": "browser.name", "operator": "exists"}], {"browser": {"name": "chrome"}}, True),
            ([{"attribute": "browser.name", "operator": "exists"}], {"browser": "chrome"}, False),
            ([{"attribute": "name", "operator": "notExists"}], {"not_name": "Hello World"}, True),
            ([{"attribute": "name", "operator": "notExists"}], {"name": "Hi World"}, False),
            ([{"attribute": "name", "operator": "endsWith", "value": "World"}], {"name": "Hello World"}, True),
            ([{"attribute": "name", "operator": "endsWith", "value": "World"}], {"name": "Hi Universe"}, False),
            ([{"attribute": "permissions", "operator": "includes", "value": "write"}], {"permissions": ["read", "write"]}, True),
            ([{"attribute": "permissions", "operator": "includes", "value": "write"}], {"permissions": ["read"]}, False),
            ([{"attribute": "permissions", "operator": "notIncludes", "value": "write"}], {"permissions": ["read", "admin"]}, True),
            ([{"attribute": "permissions", "operator": "notIncludes", "value": "write"}], {"permissions": ["read", "write", "admin"]}, False),
            ([{"attribute": "name", "operator": "contains", "value": "Hello"}], {"name": "Hello World"}, True),
            ([{"attribute": "name", "operator": "contains", "value": "Hello"}], {"name": "Hi World"}, False),
            ([{"attribute": "name", "operator": "notContains", "value": "Hello"}], {"name": "Hi World"}, True),
            ([{"attribute": "name", "operator": "notContains", "value": "Hello"}], {"name": "Hello World"}, False),
            ([{"attribute": "name", "operator": "matches", "value": "^[a-zA-Z]{2,}$"}], {"name": "Hello"}, True),
            ([{"attribute": "name", "operator": "matches", "value": "^[a-zA-Z]{2,}$"}], {"name": "Hello World"}, False),
            ([{"attribute": "name", "operator": "matches", "value": "^[a-zA-Z]{2,}$", "regexFlags": "i"}], {"name": "Hello"}, True),
            ([{"attribute": "name", "operator": "notMatches", "value": "^[a-zA-Z]{2,}$"}], {"name": "Hi World"}, True),
            ([{"attribute": "name", "operator": "notMatches", "value": "^[a-zA-Z]{2,}$"}], {"name": "Hello"}, False),
            ([{"attribute": "browser_type", "operator": "in", "value": ["chrome", "firefox"]}], {"browser_type": "chrome"}, True),
            ([{"attribute": "browser_type", "operator": "in", "value": ["chrome", "firefox"]}], {"browser_type": "edge"}, False),
            ([{"attribute": "browser_type", "operator": "notIn", "value": ["chrome", "firefox"]}], {"browser_type": "edge"}, True),
            ([{"attribute": "browser_type", "operator": "notIn", "value": ["chrome", "firefox"]}], {"browser_type": "chrome"}, False),
            ([{"attribute": "age", "operator": "greaterThan", "value": 18}], {"age": 19}, True),
            ([{"attribute": "age", "operator": "greaterThan", "value": 18}], {"age": 17}, False),
            ([{"attribute": "age", "operator": "greaterThanOrEquals", "value": 18}], {"age": 18}, True),
            ([{"attribute": "age", "operator": "greaterThanOrEquals", "value": 18}], {"age": 17}, False),
            ([{"attribute": "age", "operator": "lessThan", "value": 18}], {"age": 17}, True),
            ([{"attribute": "age", "operator": "lessThan", "value": 18}], {"age": 19}, False),
            ([{"attribute": "age", "operator": "lessThanOrEquals", "value": 18}], {"age": 18}, True),
            ([{"attribute": "age", "operator": "lessThanOrEquals", "value": 18}], {"age": 19}, False),
            ([{"attribute": "version", "operator": "semverEquals", "value": "1.0.0"}], {"version": "1.0.0"}, True),
            ([{"attribute": "version", "operator": "semverEquals", "value": "1.0.0"}], {"version": "2.0.0"}, False),
            ([{"attribute": "version", "operator": "semverNotEquals", "value": "1.0.0"}], {"version": "2.0.0"}, True),
            ([{"attribute": "version", "operator": "semverGreaterThan", "value": "1.0.0"}], {"version": "2.0.0"}, True),
            ([{"attribute": "version", "operator": "semverGreaterThanOrEquals", "value": "1.0.0"}], {"version": "1.0.0"}, True),
            ([{"attribute": "version", "operator": "semverLessThan", "value": "1.0.0"}], {"version": "0.9.0"}, True),
            ([{"attribute": "version", "operator": "semverLessThanOrEquals", "value": "1.0.0"}], {"version": "1.1.0"}, False),
            ([{"attribute": "date", "operator": "before", "value": "2023-05-13T16:23:59Z"}], {"date": "2023-05-12T00:00:00Z"}, True),
            ([{"attribute": "date", "operator": "before", "value": "2023-05-13T16:23:59Z"}], {"date": dt.datetime.fromisoformat("2023-05-14T00:00:00+00:00")}, False),
            ([{"attribute": "date", "operator": "after", "value": "2023-05-13T16:23:59Z"}], {"date": "2023-05-14T00:00:00Z"}, True),
            ([{"attribute": "date", "operator": "after", "value": "2023-05-13T16:23:59Z"}], {"date": "2023-05-12T00:00:00Z"}, False),
        ]
        for conditions, context, expected in cases:
            with self.subTest(conditions=conditions, context=context):
                self.assertEqual(self.reader.allConditionsAreMatched(conditions, context), expected)

    def test_simple_and_nested_conditions(self) -> None:
        cases = [
            ({"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"browser_type": "chrome"}, True),
            ([], {"browser_type": "chrome"}, True),
            ([{"attribute": "browser_type", "operator": "equals", "value": "chrome"}], {"browser_type": "chrome", "browser_version": "1.0"}, True),
            (
                [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"attribute": "browser_version", "operator": "equals", "value": "1.0"}],
                {"browser_type": "chrome", "browser_version": "1.0", "foo": "bar"},
                True,
            ),
            ([{"and": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}]}], {"browser_type": "chrome"}, True),
            ([{"and": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"attribute": "browser_version", "operator": "equals", "value": "1.0"}]}], {"browser_type": "chrome"}, False),
            ([{"or": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"attribute": "browser_version", "operator": "equals", "value": "1.0"}]}], {"browser_version": "1.0"}, True),
            ([{"not": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}]}], {"browser_type": "firefox"}, True),
            ([{"not": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"attribute": "browser_version", "operator": "equals", "value": "1.0"}]}], {"browser_type": "chrome", "browser_version": "1.0"}, False),
            ([{"and": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"or": [{"attribute": "browser_version", "operator": "equals", "value": "1.0"}, {"attribute": "browser_version", "operator": "equals", "value": "2.0"}]}]}], {"browser_type": "chrome", "browser_version": "1.0"}, True),
            (
                [
                    {"attribute": "country", "operator": "equals", "value": "nl"},
                    {"and": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"or": [{"attribute": "browser_version", "operator": "equals", "value": "1.0"}, {"attribute": "browser_version", "operator": "equals", "value": "2.0"}]}]},
                ],
                {"country": "nl", "browser_type": "chrome", "browser_version": "2.0"},
                True,
            ),
            ([{"or": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"and": [{"attribute": "device_type", "operator": "equals", "value": "mobile"}, {"attribute": "orientation", "operator": "equals", "value": "portrait"}]}]}], {"browser_type": "firefox", "device_type": "mobile", "orientation": "portrait"}, True),
            (
                [
                    {"attribute": "country", "operator": "equals", "value": "nl"},
                    {"or": [{"attribute": "browser_type", "operator": "equals", "value": "chrome"}, {"and": [{"attribute": "device_type", "operator": "equals", "value": "mobile"}, {"attribute": "orientation", "operator": "equals", "value": "portrait"}]}]},
                ],
                {"country": "de", "browser_type": "firefox", "device_type": "desktop"},
                False,
            ),
        ]
        for conditions, context, expected in cases:
            with self.subTest(conditions=conditions, context=context):
                self.assertEqual(self.reader.allConditionsAreMatched(conditions, context), expected)


if __name__ == "__main__":
    unittest.main()
