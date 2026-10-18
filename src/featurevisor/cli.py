from __future__ import annotations

import argparse
import json
import os
import sys
import subprocess

from .tester import run_assess_distribution, run_benchmark, run_test_project


def positive_inflate(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--inflate must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("--inflate must be a positive integer")
    return parsed


def positive_iterations(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--n must be a positive integer") from error
    if parsed < 1:
        raise argparse.ArgumentTypeError("--n must be a positive integer")
    return parsed


def nonempty_value(value: str) -> str:
    if not value:
        raise argparse.ArgumentTypeError("value must not be empty")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="featurevisor", allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project-directory-path", "--projectDirectoryPath", "--root-directory-path", "--rootDirectoryPath", dest="projectDirectoryPath", type=nonempty_value)
    common.add_argument("--environment")
    common.add_argument("--feature", type=nonempty_value)
    common.add_argument("--context")
    common.add_argument("--key-pattern", "--keyPattern", dest="keyPattern")
    common.add_argument("--assertion-pattern", "--assertionPattern", dest="assertionPattern")
    common.add_argument("--only-failures", "--onlyFailures", dest="onlyFailures", action="store_true")
    common.add_argument("--quiet", action="store_true")
    common.add_argument("--verbose", action="store_true")
    common.add_argument("--show-datafile", "--showDatafile", dest="showDatafile", action="store_true")
    common.add_argument("--variation", action="store_true")
    common.add_argument("--variable", type=nonempty_value)
    common.add_argument("--n", "-n", type=positive_iterations, default=1000)
    common.add_argument("--inflate", type=positive_inflate, default=0)
    common.add_argument("--with-scopes", "--withScopes", dest="with_scopes", action="store_true")
    common.add_argument("--with-tags", "--withTags", dest="with_tags", action="store_true")
    common.add_argument("--schemaVersion", "--schema-version", dest="schema_version")
    common.add_argument("--populate-uuid", "--populateUuid", dest="populateUuid", type=nonempty_value, action="append", default=[])
    common.add_argument("--target", type=nonempty_value, action="append", default=[])
    common.add_argument("--set")
    test_parser = subparsers.add_parser("test", parents=[common], allow_abbrev=False)
    test_parser.add_argument("--entity-type", "--entityType", dest="entityType", choices=("feature", "segment", "variable", "scenario"))
    test_parser.add_argument("--json", action="store_true")
    test_parser.add_argument("--pretty", action="store_true")
    test_parser.add_argument("--case", dest="case_id")
    test_parser.add_argument("--coverage", action="store_true")
    test_parser.add_argument("--uncovered", action="store_true")
    subparsers.add_parser("benchmark", parents=[common], allow_abbrev=False)
    subparsers.add_parser("assess-distribution", parents=[common], allow_abbrev=False)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except subprocess.CalledProcessError as error:
        print(error.stderr or str(error), file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1


def _main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    context = json.loads(args.context) if args.context is not None else {}
    if not isinstance(context, dict):
        parser.error("--context must be a JSON object")
    if args.command != "test":
        if args.variation and args.variable:
            parser.error("--variation and --variable cannot be combined")
    if args.command == "test":
        ok = run_test_project(
            args.projectDirectoryPath or os.getcwd(),
            key_pattern=args.keyPattern,
            assertion_pattern=args.assertionPattern,
            entity_type=args.entityType,
            verbose=args.verbose,
            quiet=args.quiet,
            show_datafile=args.showDatafile,
            only_failures=args.onlyFailures,
            schema_version=args.schema_version,
            inflate=args.inflate,
            with_scopes=args.with_scopes,
            with_tags=args.with_tags,
            targets=args.target,
            set_name=args.set,
            json_output=args.json,
            pretty=args.pretty,
            case_id=args.case_id,
            coverage=args.coverage,
            uncovered=args.uncovered,
            rerun_project_path=args.projectDirectoryPath,
        )
        return 0 if ok else 1
    if args.command == "benchmark":
        if (not args.feature and not args.variable) or (args.variation and not args.feature):
            parser.error("benchmark requires --feature or --variable; --variation requires --feature")
        return run_benchmark(
            args.projectDirectoryPath or os.getcwd(),
            environment=args.environment,
            feature=args.feature,
            context=context,
            n=args.n,
            variation=args.variation,
            variable=args.variable,
            inflate=args.inflate,
            verbose=args.verbose,
            quiet=args.quiet,
            targets=args.target,
            set_name=args.set,
        )
    if args.command == "assess-distribution":
        if not args.feature:
            parser.error("assess-distribution requires --feature")
        return run_assess_distribution(
            args.projectDirectoryPath or os.getcwd(),
            environment=args.environment,
            feature=args.feature,
            context=context,
            n=args.n,
            populate_uuid=args.populateUuid,
            inflate=args.inflate,
            verbose=args.verbose,
            quiet=args.quiet,
            targets=args.target,
            set_name=args.set,
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
