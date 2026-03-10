from __future__ import annotations

import argparse
import json
import os
import sys

from .tester import run_assess_distribution, run_benchmark, run_test_project


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="featurevisor")
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--projectDirectoryPath", default=os.getcwd())
    common.add_argument("--environment")
    common.add_argument("--feature")
    common.add_argument("--context")
    common.add_argument("--keyPattern")
    common.add_argument("--assertionPattern")
    common.add_argument("--onlyFailures", action="store_true")
    common.add_argument("--quiet", action="store_true")
    common.add_argument("--verbose", action="store_true")
    common.add_argument("--showDatafile", action="store_true")
    common.add_argument("--variation", action="store_true")
    common.add_argument("--variable")
    common.add_argument("--n", type=int, default=1000)
    common.add_argument("--inflate", type=int, default=0)
    common.add_argument("--with-scopes", dest="with_scopes", action="store_true")
    common.add_argument("--with-tags", dest="with_tags", action="store_true")
    common.add_argument("--schemaVersion")
    common.add_argument("--populateUuid", action="append", default=[])
    subparsers.add_parser("test", parents=[common])
    subparsers.add_parser("benchmark", parents=[common])
    subparsers.add_parser("assess-distribution", parents=[common])
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    context = json.loads(args.context) if args.context else {}
    if args.command == "test":
        ok = run_test_project(
            args.projectDirectoryPath,
            key_pattern=args.keyPattern,
            assertion_pattern=args.assertionPattern,
            verbose=args.verbose,
            quiet=args.quiet,
            show_datafile=args.showDatafile,
            only_failures=args.onlyFailures,
            schema_version=args.schemaVersion,
            inflate=args.inflate,
            with_scopes=args.with_scopes,
            with_tags=args.with_tags,
        )
        return 0 if ok else 1
    if args.command == "benchmark":
        if not args.environment or not args.feature:
            parser.error("benchmark requires --environment and --feature")
        return run_benchmark(
            args.projectDirectoryPath,
            environment=args.environment,
            feature=args.feature,
            context=context,
            n=args.n,
            variation=args.variation,
            variable=args.variable,
            schema_version=args.schemaVersion,
            inflate=args.inflate,
            verbose=args.verbose,
            quiet=args.quiet,
        )
    if args.command == "assess-distribution":
        if not args.environment or not args.feature:
            parser.error("assess-distribution requires --environment and --feature")
        return run_assess_distribution(
            args.projectDirectoryPath,
            environment=args.environment,
            feature=args.feature,
            context=context,
            n=args.n,
            populate_uuid=args.populateUuid,
            schema_version=args.schemaVersion,
            inflate=args.inflate,
            verbose=args.verbose,
            quiet=args.quiet,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
