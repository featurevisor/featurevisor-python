from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "src")

from featurevisor.cli import build_parser, main
from featurevisor.tester import _get_datafile_for_assertion, _get_target_datafile_key


class CLITests(unittest.TestCase):
    def test_parse_test_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["test", "--withScopes", "--withTags", "--schema-version=2", "--projectDirectoryPath=/tmp/project"])
        self.assertTrue(args.with_scopes)
        self.assertTrue(args.with_tags)
        self.assertEqual(args.schema_version, "2")
        self.assertEqual(args.projectDirectoryPath, "/tmp/project")

    def test_parse_legacy_schema_camel_alias(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["test", "--schemaVersion=2"])
        self.assertEqual(args.schema_version, "2")

    def test_parse_benchmark_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmark", "--environment=production", "--feature=foo", "--n=20", "--variation"])
        self.assertEqual(args.command, "benchmark")
        self.assertEqual(args.environment, "production")
        self.assertEqual(args.feature, "foo")
        self.assertEqual(args.n, 20)
        self.assertTrue(args.variation)

    def test_parse_repeated_targets(self) -> None:
        args = build_parser().parse_args(["benchmark", "--target=web", "--target=mobile"])
        self.assertEqual(args.target, ["web", "mobile"])

    def test_main_returns_non_zero_on_failed_tests(self) -> None:
        with patch("featurevisor.cli.run_test_project", return_value=False):
            self.assertEqual(main(["test"]), 1)

    def test_target_datafile_cache_key(self) -> None:
        self.assertEqual(_get_target_datafile_key(False, "checkout"), "false-target-checkout")
        self.assertEqual(_get_target_datafile_key("production", "checkout"), "production-target-checkout")

    def test_target_assertion_selects_target_datafile(self) -> None:
        cache = {
            "production": {"kind": "base"},
            "production-target-checkout": {"kind": "target"},
        }
        datafile = _get_datafile_for_assertion({"environment": "production", "target": "checkout"}, cache)
        self.assertEqual(datafile["kind"], "target")


if __name__ == "__main__":
    unittest.main()
