from __future__ import annotations

import sys
import unittest

sys.path.insert(0, "src")

from featurevisor.helpers import get_value_by_type


class HelpersParityTests(unittest.TestCase):
    def test_should_return_null_for_type_mismatch(self) -> None:
        self.assertIsNone(get_value_by_type(1, "string"))

    def test_should_return_the_value_as_is_if_it_is_a_string(self) -> None:
        self.assertEqual(get_value_by_type("1", "string"), "1")

    def test_should_return_the_value_as_is_if_it_is_a_boolean(self) -> None:
        self.assertEqual(get_value_by_type(True, "boolean"), True)

    def test_should_return_the_value_as_is_if_it_is_an_object(self) -> None:
        self.assertEqual(get_value_by_type({"a": 1, "b": 2}, "object"), {"a": 1, "b": 2})

    def test_should_return_the_value_as_is_if_it_is_a_json(self) -> None:
        self.assertEqual(get_value_by_type('{"a": 1, "b": 2}', "json"), '{"a": 1, "b": 2}')

    def test_should_return_array_if_the_value_is_an_array(self) -> None:
        self.assertEqual(get_value_by_type(["1", "2", "3"], "array"), ["1", "2", "3"])

    def test_should_return_integer_if_the_value_is_an_integer(self) -> None:
        self.assertEqual(get_value_by_type("1", "integer"), 1)

    def test_should_return_double_if_the_value_is_a_double(self) -> None:
        self.assertEqual(get_value_by_type("1.1", "double"), 1.1)

    def test_should_return_null_if_the_value_is_undefined(self) -> None:
        self.assertIsNone(get_value_by_type(None, "string"))

    def test_should_return_null_if_the_value_is_null(self) -> None:
        self.assertIsNone(get_value_by_type(None, "string"))

    def test_should_return_null_when_a_function_is_passed(self) -> None:
        self.assertIsNone(get_value_by_type(lambda: None, "string"))


if __name__ == "__main__":
    unittest.main()
