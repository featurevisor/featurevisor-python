from __future__ import annotations

import sys
import unittest

sys.path.insert(0, "src")

from featurevisor.cli import build_parser


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


if __name__ == "__main__":
    unittest.main()
