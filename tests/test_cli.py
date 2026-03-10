from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, "src")

from featurevisor.cli import build_parser, main


class CLITests(unittest.TestCase):
    def test_parse_test_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["test", "--with-scopes", "--with-tags", "--projectDirectoryPath=/tmp/project"])
        self.assertTrue(args.with_scopes)
        self.assertTrue(args.with_tags)
        self.assertEqual(args.projectDirectoryPath, "/tmp/project")

    def test_parse_benchmark_flags(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["benchmark", "--environment=production", "--feature=foo", "--n=20", "--variation"])
        self.assertEqual(args.command, "benchmark")
        self.assertEqual(args.environment, "production")
        self.assertEqual(args.feature, "foo")
        self.assertEqual(args.n, 20)
        self.assertTrue(args.variation)

    def test_main_returns_non_zero_on_failed_tests(self) -> None:
        with patch("featurevisor.cli.run_test_project", return_value=False):
            self.assertEqual(main(["test"]), 1)


if __name__ == "__main__":
    unittest.main()
