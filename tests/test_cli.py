from __future__ import annotations

import sys
import json
import os
import shlex
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import Mock, patch

sys.path.insert(0, "src")

from featurevisor.cli import build_parser, main
from featurevisor.instance import create_featurevisor
from featurevisor.project import FeaturevisorProject, FeaturevisorProjectError
from featurevisor.test_checks import CheckError, _date, evaluate_checks, evaluate_checks_result, preflight_checks
from featurevisor.tester import (
    _assert_global_variable,
    _assert_scenario,
    _case_result,
    _get_datafile_for_assertion,
    _get_base_datafile_key,
    _get_target_datafile_key,
    _preflight_assertion,
    _run_test_project_set,
    _print_report,
    _empty_report,
)


class CLITests(unittest.TestCase):
    def test_runner_preflight_preserves_syntax_and_reference_failure_details(self) -> None:
        datafile = {"schemaVersion": "2", "revision": "preflight", "features": {}, "segments": {},
                    "variables": {"version": {"type": "string", "defaultValue": "1.0.0"}}}
        valid = {"variable": "version", "operator": "semverEquals", "value": "1.0.0"}
        project = Mock(spec=FeaturevisorProject)
        project.set_name = None
        project.get_config.return_value = {"environments": False}
        project.list_targets.return_value = []
        project.list_features.return_value = []
        project.list_segments.return_value = []
        project.build_datafile_json.return_value = datafile
        for child in [False, True]:
            for entity in [False, True]:
                for invalid in ["", "1.0.0\n", "١.0.0", None]:
                    with self.subTest(child=child, entity=entity, invalid=invalid):
                        check = {**valid, **({"variable": "missing"} if invalid is None else {"value": invalid})}
                        checks = [valid, {"not": [check]}]
                        assertion = {"children": [{"checks": [valid]}, {"checks": checks}]} if child else {"checks": checks}
                        assertion["_case"] = {"assertionIndex": 7}
                        spec = {"variable": "version"} if entity else {"scenario": "preflight"}
                        project.list_tests.return_value = [{**spec, "assertions": [assertion]}]
                        with patch("featurevisor.tester.create_featurevisor") as create:
                            report = _run_test_project_set(project, json_output=True)[0]
                            create.assert_not_called()
                        self.assertFalse(report["passed"])
                        errors = report["cases"][0]["errors"]
                        self.assertEqual(len(errors), 1)
                        error = errors[0]
                        self.assertEqual(error["type"], "check")
                        self.assertEqual(error["expected"], "valid check references" if invalid is None else "valid checks")
                        self.assertIsInstance(error["actual"], str)
                        self.assertTrue(error["actual"])
                        prefix = ["assertions", 7] if entity else []
                        if child:
                            prefix.extend(["children", 1])
                        self.assertEqual(error["path"], [*prefix, "checks", 1])

    def test_ordinary_failures_preserve_authored_paths_values_and_children(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "failures", "segments": {},
            "features": {"flag": {"bucketBy": "userId", "traffic": [{"key": "all", "segments": "*", "percentage": 100000, "allocation": []}],
                "variablesSchema": {"text": {"type": "string", "defaultValue": "actual"}}}},
            "variables": {"text": {"type": "string", "defaultValue": "actual"}},
        }
        feature = {
            "expectedToBeEnabled": False, "expectedVariation": "missing",
            "expectedVariables": {"text": "wrong"},
            "expectedEvaluations": {"flag": {"enabled": False}, "variation": {"featureKey": "wrong"}, "variables": {"text": {"variableValue": "wrong"}}},
        }
        variable = {"expectedValue": "wrong", "expectedEvaluation": {"variableValue": "wrong"}}
        project = Mock(spec=FeaturevisorProject)
        project.set_name = None
        project.get_config.return_value = {"environments": False}
        project.list_targets.return_value = []
        project.list_features.return_value = [{"key": "flag"}]
        project.list_segments.return_value = [{"key": "all", "conditions": "*"}]
        project.build_datafile_json.return_value = datafile
        project.list_tests.return_value = [
            {"feature": "flag", "assertions": [{**feature, "children": [feature], "_case": {"assertionIndex": 7}}]},
            {"variable": "text", "assertions": [{**variable, "children": [variable], "_case": {"assertionIndex": 7}}]},
            {"segment": "all", "assertions": [{"expectedToMatch": False, "_case": {"assertionIndex": 7}}]},
        ]
        reports = _run_test_project_set(project, json_output=True)
        expected = [
            [("flag", ["expectedToBeEnabled"], False, True),
             ("variation", ["expectedVariation"], "missing", None),
             ("variable", ["expectedVariables", "text"], "wrong", "actual"),
             ("evaluation", ["expectedEvaluations", "flag", "enabled"], False, True),
             ("evaluation", ["expectedEvaluations", "variation", "featureKey"], "wrong", "flag"),
             ("evaluation", ["expectedEvaluations", "variables", "text", "variableValue"], "wrong", "actual")],
            [("variable", ["expectedValue"], "wrong", "actual"),
             ("evaluation", ["expectedEvaluation", "variableValue"], "wrong", "actual")],
            [("segment", ["expectedToMatch"], False, True)],
        ]
        for report, rows in zip(reports, expected):
            self.assertFalse(report["passed"])
            case = report["cases"][0]
            prefixes = [[], ["children", 0]] if report["type"] != "segment" else [[]]
            self.assertEqual(len(case["errors"]), len(rows) * len(prefixes))
            for child, prefix in enumerate(prefixes):
                for index, (kind, suffix, wanted, actual) in enumerate(rows):
                    error = case["errors"][child * len(rows) + index]
                    self.assertEqual((error["type"], error["path"], error["expected"], error["actual"]),
                                     (kind, ["assertions", 7, *prefix, *suffix], wanted, actual))
                    if prefix:
                        self.assertEqual(error["details"]["childIndex"], 0)

    def test_inflate_requires_an_explicit_positive_integer(self) -> None:
        for command in ("test", "benchmark", "assess-distribution"):
            parser = build_parser()
            self.assertEqual(parser.parse_args([command]).inflate, 0)
            self.assertEqual(parser.parse_args([command, "--inflate=1"]).inflate, 1)
            for value in ("0", "-5", "1.5", "invalid", "", "1foo"):
                with self.subTest(command=command, value=value):
                    with self.assertRaises(SystemExit) as error:
                        parser.parse_args([command, f"--inflate={value}"])
                    self.assertNotEqual(error.exception.code, 0)

    def test_portable_check_date_fractional_precision(self) -> None:
        expected = _date("2024-01-01T00:00:00.123Z")
        for digits in ("1239", "123999", "1239999", "123999999"):
            with self.subTest(digits=digits):
                self.assertEqual(_date(f"2024-01-01T00:00:00.{digits}Z"), expected)
        with self.assertRaises(CheckError):
            _date("2024-01-01T00:00:00.1234567890Z")
        self.assertEqual(_date("1969-12-31T23:59:59.999999999Z"), _date("1969-12-31T23:59:59.999Z"))

    def test_human_report_uses_one_based_paths_named_shell_and_readable_coverage(self) -> None:
        report = _empty_report()
        report["summary"]["assertions"]["failed"] = 1
        report["summary"]["scenarioCases"]["failed"] = 1
        report["results"] = [{
            "type": "scenario", "key": "checkout", "source": "tests/scenarios/checkout.spec.yml",
            "passed": False, "cases": [{
                "description": "checkout", "target": "web", "matrixValues": {"country": "nl"},
                "passed": False, "errors": [{
                    "type": "check", "message": "Check did not match", "path": ["checks", 0],
                    "details": {"checkKey": "price", "evaluated": [{
                        "path": ["checks", 0], "reference": {"variable": "price"},
                        "operator": "equals", "actual": 1, "expected": 2,
                    }]},
                }, {
                    "type": "variable", "message": "variable expectation did not match",
                    "path": ["assertions", 0, "children", 0, "expectedValue"],
                    "details": {"childIndex": 0}, "expected": 2, "actual": 1,
                }, {
                    "type": "check", "message": "Check did not match",
                    "path": ["children", 0, "checks", 0],
                    "details": {"childIndex": 0, "checkIndex": 0},
                }],
                "rerun": {"command": "python -m featurevisor", "args": ["test", "--case=opaque id"]},
            }],
        }]
        report["coverage"] = {"mode": "observed-selections", "selections": [{
            "featureKey": "checkout", "variableKey": "price", "variation": "blue",
            "overrideIndex": 0,
        }]}
        output = StringIO()
        with redirect_stdout(output):
            _print_report(report, json_output=False, pretty=False, only_failures=False)
        human = output.getvalue()
        self.assertIn("matrix country=\"nl\"; target web", human)
        self.assertIn("checks.1", human)
        self.assertIn("Child 1, expectedValue", human)
        self.assertIn("Child 1, check", human)
        self.assertIn("Rerun (POSIX shell)", human)
        self.assertIn("'--case=opaque id'", human)
        self.assertIn("Total cases: 0 passed, 1 failed", human)
        self.assertIn("feature checkout variable price", human)
        self.assertIn("variation \"blue\", override 1", human)

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

    def test_parse_scenario_filter(self) -> None:
        args = build_parser().parse_args([
            "test", "--entityType=scenario", "--json", "--pretty", "--case=opaque",
            "--coverage", "--uncovered",
        ])
        self.assertEqual(args.entityType, "scenario")
        self.assertTrue(args.json)
        self.assertTrue(args.pretty)
        self.assertEqual(args.case_id, "opaque")
        self.assertTrue(args.coverage)
        self.assertTrue(args.uncovered)

    def test_main_returns_non_zero_on_failed_tests(self) -> None:
        with patch("featurevisor.cli.run_test_project", return_value=False):
            self.assertEqual(main(["test"]), 1)

    def test_execution_protocol_envelope_is_strictly_validated(self) -> None:
        project = FeaturevisorProject("/tmp/project")
        valid = {
            "kind": "featurevisor.test-execution",
            "schemaVersion": 1,
            "tests": [{"scenario": "ready", "assertions": []}],
        }
        malformed = [
            [],
            {},
            {**valid, "kind": "other"},
            {**valid, "schemaVersion": True},
            {**valid, "schemaVersion": "1"},
            {**valid, "schemaVersion": 2},
            {**valid, "tests": None},
            {**valid, "tests": [None]},
        ]
        for payload in malformed:
            with self.subTest(payload=payload), patch.object(
                project,
                "run",
                return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
            ):
                with self.assertRaises(FeaturevisorProjectError):
                    project.list_tests()

        with patch.object(
            project,
            "run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(valid), ""),
        ) as run:
            self.assertEqual(project.list_tests(key_pattern="ready"), valid["tests"])
            self.assertEqual(
                run.call_args.args,
                ("list", "--tests", "--execution-protocol", "--json", "--keyPattern=ready"),
            )

    def test_target_datafile_cache_key(self) -> None:
        self.assertEqual(_get_target_datafile_key(False, "checkout"), (None, "checkout"))
        self.assertEqual(_get_target_datafile_key(None, "checkout"), (None, "checkout"))
        self.assertEqual(_get_target_datafile_key("production", "checkout"), ("production", "checkout"))
        self.assertEqual(_get_base_datafile_key({}), (None, None))

    def test_target_assertion_selects_target_datafile(self) -> None:
        cache = {
            ("production", None): {"kind": "base"},
            ("production", "checkout"): {"kind": "target"},
        }
        datafile = _get_datafile_for_assertion({"environment": "production", "target": "checkout"}, cache)
        self.assertEqual(datafile["kind"], "target")

    def test_target_assertion_does_not_fall_back_to_base_datafile(self) -> None:
        cache = {("production", None): {"kind": "base"}}
        datafile = _get_datafile_for_assertion({"environment": "production", "target": "checkout"}, cache)
        self.assertIsNone(datafile)

    def test_datafile_cache_keys_preserve_every_environment_target_pair(self) -> None:
        environments = [None, "false", "prod", "prod-target-web", "prod-target-a", 'café"\\\n東京']
        targets = [None, "web", "a-target-b", "b", 'web"\\\n東京']
        cache = {}
        for environment in environments:
            for target in targets:
                key = _get_base_datafile_key({"environment": environment}) if target is None else _get_target_datafile_key(environment, target)
                cache[key] = {"route": (environment, target)}
        self.assertEqual(len(cache), len(environments) * len(targets))
        for environment in environments:
            for target in targets:
                assertion = {"environment": environment, "target": target}
                self.assertEqual(_get_datafile_for_assertion(assertion, cache)["route"], (environment, target))
        self.assertIs(_get_datafile_for_assertion({}, cache), cache[(None, None)])
        self.assertIs(_get_datafile_for_assertion({"environment": False, "target": "web"}, cache), cache[(None, "web")])
        self.assertIsNone(_get_datafile_for_assertion({"environment": "prod", "target": "missing"}, cache))

    def test_runner_builds_and_evaluates_collision_free_datafiles(self) -> None:
        for environments in (["prod", "prod-target-web", "prod-target-a", "false", 'café"\\東京'], [None]):
            with self.subTest(environments=environments):
                targets = ["web", "a-target-b", "b", 'web"\\東京']
                project = Mock(spec=FeaturevisorProject)
                project.set_name = None
                project.get_config.return_value = {"environments": environments if environments != [None] else False}
                project.list_features.return_value = []
                project.list_segments.return_value = []
                project.list_targets.return_value = targets
                def build(environment=None, inflate=None, target=None):
                    return {"schemaVersion": "2", "revision": "cache-test", "features": {}, "segments": {},
                            "variables": {"route": {"type": "string", "defaultValue": json.dumps([environment, target])}}}
                project.build_datafile_json.side_effect = build
                assertions = [{"environment": environment, **({"target": target} if target else {}),
                               "expectedValue": json.dumps([environment, target]), "_case": {"id": f"case-{index}"}}
                              for index, (environment, target) in enumerate((e, t) for e in environments for t in [None, *targets])]
                project.list_tests.return_value = [{"key": "route", "variable": "route", "assertions": assertions}]
                result = _run_test_project_set(project, quiet=True, json_output=True)[0]
                self.assertTrue(result["passed"], result)
                self.assertEqual(project.build_datafile_json.call_count, len(assertions))
                self.assertEqual([case["id"] for case in result["cases"]], [a["_case"]["id"] for a in assertions])

    def test_segment_checks_use_authored_definitions_and_effective_context(self) -> None:
        segments = {
            "adult": {"conditions": [{"attribute": "age", "operator": "greaterThanOrEquals", "value": 18}]},
            "netherlands": {"conditions": {"attribute": "country", "operator": "equals", "value": "nl"}},
            "not-netherlands": {"conditions": {"not": [{"attribute": "country", "operator": "equals", "value": "nl"}]}},
            "everyone": {"conditions": "*"},
        }
        datafile = {"schemaVersion": "2", "revision": "target", "segments": {}, "features": {}, "variables": {}}
        checks = [{"and": [
            {"segment": "adult", "matches": True},
            {"or": [
                {"segment": "netherlands", "matches": True},
                {"segment": "everyone", "matches": False},
            ]},
            {"not": [{"segment": "not-netherlands", "matches": True}]},
        ]}]
        result = evaluate_checks_result(
            create_featurevisor({"datafile": datafile, "logLevel": "fatal"}), datafile, checks, owner_variable="unused", diagnostics=[],
            segments=segments, context={"country": "nl", "age": 20},
        )
        self.assertTrue(result["passed"], result["failures"])

        child = evaluate_checks_result(
            create_featurevisor({"datafile": datafile, "logLevel": "fatal"}), datafile, [{"segment": "adult", "matches": False}],
            owner_variable=None, diagnostics=[], segments=segments,
            context={**{"country": "nl", "age": 20}, **{"age": 17}},
        )
        self.assertTrue(child["passed"], child["failures"])

    def test_segment_checks_reject_invalid_shapes_and_missing_definitions(self) -> None:
        datafile = {"segments": {}, "features": {}, "variables": {}}
        segments = {"everyone": {"conditions": "*"}}
        invalid = [
            {"matches": True},
            {"segment": "", "matches": True},
            {"segment": "everyone"},
            {"segment": "everyone", "matches": "true"},
            {"segment": "everyone", "matches": True, "variable": "value"},
        ]
        for leaf in invalid:
            with self.subTest(leaf=leaf):
                result = evaluate_checks_result(
                    object(), datafile, [leaf], owner_variable=None, diagnostics=[],
                    segments=segments, context={},
                )
                self.assertTrue(result["error"])
                self.assertIn("segment", result["failures"][0])

        skipped = evaluate_checks_result(
            object(), datafile,
            [{"or": [{"segment": "everyone", "matches": True}, {"segment": "everyone", "matches": "yes"}]}],
            owner_variable=None, diagnostics=[], segments=segments, context={},
        )
        self.assertTrue(skipped["error"])

        missing = evaluate_checks_result(
            object(), datafile, [{"segment": "missing", "matches": False}],
            owner_variable=None, diagnostics=[], segments=segments, context={},
        )
        self.assertTrue(missing["error"])
        self.assertIn('segment "missing"', missing["failures"][0])

    def test_segment_check_failure_has_typed_leaf_evidence(self) -> None:
        result = evaluate_checks_result(
            create_featurevisor({"logLevel": "fatal"}), {"schemaVersion": "4", "revision": "test", "segments": {}, "features": {}, "variables": {}},
            [{"key": "adult-check", "segment": "adult", "matches": False}],
            owner_variable=None, diagnostics=[],
            segments={"adult": {"conditions": {"attribute": "age", "operator": "greaterThanOrEquals", "value": 18}}},
            context={"age": 20},
        )
        self.assertFalse(result["passed"])
        self.assertIn('segment "adult"', result["failures"][0])
        self.assertEqual(result["errors"][0]["details"]["evaluated"][0], {
            "path": [0],
            "key": "adult-check",
            "reference": {"segment": "adult"},
            "property": "matches",
            "operator": "equals",
            "expected": False,
            "actual": True,
        })

    def test_scenario_children_and_global_variable_checks_receive_segment_context(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "runner", "segments": {}, "features": {},
            "variables": {"message": {"type": "string", "defaultValue": "ready"}},
        }
        segments = {
            "netherlands": {"conditions": {"attribute": "country", "operator": "equals", "value": "nl"}},
            "young-dutch": {"conditions": [
                {"attribute": "country", "operator": "equals", "value": "nl"},
                {"attribute": "age", "operator": "lessThan", "value": 18},
            ]},
        }
        diagnostics: list[dict] = []
        scenario = {
            "context": {"country": "nl", "age": 30},
            "checks": [{"segment": "netherlands", "matches": True}],
            "children": [{"context": {"age": 17}, "checks": [{"segment": "young-dutch", "matches": True}]}],
        }
        sdk = create_featurevisor({"datafile": datafile, "context": scenario["context"], "logLevel": "fatal"})
        result = {"passed": True}
        assertion_result = {"passed": True, "errors": []}
        try:
            _assert_scenario(sdk, scenario, datafile, diagnostics, result, assertion_result, segments=segments)
        finally:
            sdk.close()
        self.assertTrue(result["passed"], assertion_result["errors"])

        variable_assertion = {
            "context": {"country": "nl"},
            "checks": [{"segment": "netherlands", "matches": True}],
        }
        sdk = create_featurevisor({"datafile": datafile, "context": variable_assertion["context"], "logLevel": "fatal"})
        result = {"passed": True}
        assertion_result = {"passed": True, "errors": []}
        try:
            _assert_global_variable(
                sdk, "message", variable_assertion, datafile, diagnostics, result, assertion_result,
                segments=segments, context=variable_assertion["context"],
            )
        finally:
            sdk.close()
        self.assertTrue(result["passed"], assertion_result["errors"])

    def test_native_check_semantics(self) -> None:
        datafile = {
            "schemaVersion": "2",
            "revision": "checks",
            "segments": {},
            "features": {
                "account": {
                    "bucketBy": "userId",
                    "variablesSchema": {"plan": {"type": "string", "defaultValue": "pro"}},
                    "force": [{"segments": "*", "enabled": True}],
                    "traffic": [],
                }
            },
            "variables": {
                "profile": {"type": "object", "defaultValue": {"name": "Ada", "a.b": "literal"}},
                "count": {"type": "integer", "defaultValue": 2},
                "numbers": {"type": "array", "defaultValue": [1, 2, 3]},
                "nothing": {"type": "json", "defaultValue": None},
                "version": {"type": "semver", "defaultValue": "2.1.0"},
            },
        }
        diagnostics: list[dict] = []
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal", "onDiagnostic": diagnostics.append})
        try:
            checks = [
                {"path": "name", "operator": "equals", "value": "Ada"},
                {"path": ["a.b"], "operator": "equals", "value": "literal"},
                {"variable": "numbers", "path": "1", "operator": "equals", "compareTo": {"variable": "count"}},
                {"variable": "numbers", "operator": "includes", "value": 3},
                {"variable": "nothing", "operator": "isNull"},
                {"variable": "profile", "path": "name", "operator": "notNull"},
                {"feature": "account", "variable": "plan", "operator": "equals", "value": "pro"},
                {"variable": "version", "operator": "semverGreaterThan", "value": "2.0.0"},
                {"or": [
                    {"variable": "count", "operator": "equals", "value": 1},
                    {"not": [{"variable": "count", "operator": "lessThan", "value": 2}]},
                ]},
            ]
            self.assertEqual(evaluate_checks(sdk, datafile, checks, owner_variable="profile", diagnostics=diagnostics), [])
            errors = evaluate_checks(
                sdk,
                datafile,
                [{"variable": "count", "operator": "notContains", "value": "2"}],
                owner_variable="profile",
                diagnostics=diagnostics,
            )
            self.assertIn("requires string operands", errors[0])
            errors = evaluate_checks(
                sdk,
                datafile,
                [{"variable": "missing", "operator": "notEquals", "value": "anything"}],
                owner_variable="profile",
                diagnostics=diagnostics,
            )
            self.assertIn("absent from the selected datafile", errors[0])
        finally:
            sdk.close()

    def test_feature_check_getter_errors_and_diagnostics_are_errors(self) -> None:
        datafile = {"features": {"experiment": {}}}

        class ThrowingVariation:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def is_enabled(self, feature: str, context: dict) -> bool:
                self.calls.append(f"enabled:{feature}")
                return False

            def get_variation(self, feature: str, context: dict) -> str:
                self.calls.append(f"variation:{feature}")
                raise RuntimeError("variation exploded")

        throwing = ThrowingVariation()
        result = evaluate_checks_result(
            throwing,
            datafile,
            [{"feature": "experiment", "enabled": True, "variation": "treatment"}],
            owner_variable=None,
            diagnostics=[],
        )
        self.assertTrue(result["error"])
        self.assertEqual(throwing.calls, ["enabled:experiment", "variation:experiment"])
        self.assertIn("variation getter failed", result["failures"][0])
        self.assertIn('feature "experiment"', result["failures"][0])

        diagnostics: list[dict] = []

        class DiagnosticEnabled:
            def is_enabled(self, feature: str, context: dict) -> bool:
                diagnostics.append({"level": "error", "code": "evaluation_error", "message": "flag failed"})
                return False

        result = evaluate_checks_result(
            DiagnosticEnabled(),
            datafile,
            [{"feature": "experiment", "enabled": False}],
            owner_variable=None,
            diagnostics=diagnostics,
        )
        self.assertTrue(result["error"])
        self.assertIn("enabled getter reported evaluation_error", result["failures"][0])
        self.assertIn('feature "experiment"', result["failures"][0])

        class MismatchingFeature:
            def is_enabled(self, feature: str, context: dict) -> bool:
                return False

            def get_variation(self, feature: str, context: dict) -> str:
                return "treatment"

        result = evaluate_checks_result(
            MismatchingFeature(),
            datafile,
            [{"feature": "experiment", "enabled": True, "variation": "control"}],
            owner_variable=None,
            diagnostics=[],
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["error"])
        self.assertIn('feature "experiment"', result["failures"][0])
        self.assertIn("expected enabled=true, actual enabled=false", result["failures"][0])
        self.assertIn('expected variation="control", actual variation="treatment"', result["failures"][0])

    def test_check_syntax_is_fully_validated_before_getters_run(self) -> None:
        class TrackingEvaluator:
            def __init__(self) -> None:
                self.calls = 0

            def is_enabled(self, feature: str, context: dict) -> bool:
                self.calls += 1
                return True

        evaluator = TrackingEvaluator()
        result = evaluate_checks_result(
            evaluator,
            {"features": {"checkout": {}}},
            [
                {"feature": "checkout", "enabled": True},
                {"feature": "checkout", "enabled": "true"},
            ],
            owner_variable=None,
            diagnostics=[],
        )
        self.assertTrue(result["error"])
        self.assertEqual(evaluator.calls, 0)

    def test_preflight_validates_all_references_and_children_without_getters(self) -> None:
        class TrackingEvaluator:
            def __init__(self) -> None:
                self.calls = 0

            def get_variable(self, *args) -> str:
                self.calls += 1
                return "value"

            def is_enabled(self, *args) -> bool:
                self.calls += 1
                return True

            def get_variation(self, *args) -> str:
                self.calls += 1
                return "control"

        datafile = {
            "features": {
                "checkout": {
                    "variablesSchema": {"message": {"type": "string", "defaultValue": "ready"}}
                }
            },
            "variables": {"message": {"type": "string", "defaultValue": "ready"}},
        }
        invalid_checks = [
            [{"or": [
                {"variable": "message", "operator": "exists"},
                {"feature": "missing", "enabled": True},
            ]}],
            [{"and": [
                {"feature": "checkout", "enabled": True},
                {"variable": "missing", "operator": "exists"},
            ]}],
            [{"not": [{"feature": "checkout", "variable": "missing", "operator": "exists"}]}],
            [{
                "variable": "message",
                "operator": "equals",
                "compareTo": {"variable": "missing"},
            }],
            [{"segment": "missing", "matches": False}],
            [{"or": [
                {"variable": "message", "operator": "exists"},
                {"variable": "message", "operator": "unknown", "value": "x"},
            ]}],
        ]
        for checks in invalid_checks:
            evaluator = TrackingEvaluator()
            result = evaluate_checks_result(
                evaluator,
                datafile,
                checks,
                owner_variable=None,
                diagnostics=[],
                segments={"known": {"conditions": "*"}},
            )
            self.assertTrue(result["error"], checks)
            self.assertEqual(evaluator.calls, 0, checks)

        preflight_checks(
            [{"segment": "known", "matches": True}],
            datafile,
            owner_variable=None,
            segments={"known": {"conditions": {"attribute": "never-called"}}},
        )
        with self.assertRaises(CheckError):
            _preflight_assertion(
                {"variable": "missing"},
                {"checks": [{"operator": "isNull"}]},
                datafile,
                {},
            )
        with self.assertRaises(CheckError):
            _preflight_assertion(
                {"scenario": "child"},
                {
                    "checks": [{"variable": "message", "operator": "exists"}],
                    "children": [{"checks": [{"variable": "missing", "operator": "exists"}]}],
                },
                datafile,
                {},
            )

    def test_excluded_owner_expectations_allow_missing_but_child_checks_preflight(self) -> None:
        project = Mock(spec=FeaturevisorProject)
        project.set_name = None
        project.get_config.return_value = {"environments": ["production"]}
        project.list_features.return_value = []
        project.list_segments.return_value = []
        project.list_targets.return_value = ["filtered"]
        project.build_datafile_json.return_value = {
            "schemaVersion": "2", "revision": "target", "segments": {}, "features": {}, "variables": {},
        }
        routing = {"environment": "production", "target": "filtered"}
        project.list_tests.return_value = [
            {
                entity: "excluded",
                "assertions": [{**routing, **expectation, "children": [expectation]}],
            }
            for entity, expectation in [
                ("feature", {"expectedToBeEnabled": False}),
                ("variable", {"expectedEvaluation": {"reason": "variable_not_found"}}),
            ]
        ]
        results = _run_test_project_set(project, json_output=True)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["passed"] for result in results), results)

        project.list_tests.return_value = [{
            "variable": "excluded",
            "assertions": [{
                **routing, "expectedValue": None,
                "children": [{"checks": [{"operator": "isNull"}]}],
            }],
        }]
        with patch("featurevisor.tester.create_featurevisor") as create:
            results = _run_test_project_set(project, json_output=True)
            create.assert_not_called()
        self.assertFalse(results[0]["passed"])
        self.assertIn('global variable "excluded"', results[0]["cases"][0]["errors"][0]["message"])

    def test_named_check_metadata_is_validated_and_keeps_operand_evidence(self) -> None:
        class Evaluator:
            def get_variable(self, variable: str, context: dict) -> str:
                return "actual"

        datafile = {"variables": {"message": {"type": "string", "defaultValue": "actual"}}}
        result = evaluate_checks_result(
            Evaluator(),
            datafile,
            [{
                "key": "message-ready", "description": "Message is ready",
                "variable": "message", "operator": "equals", "value": "expected",
            }],
            owner_variable=None,
            diagnostics=[],
        )
        self.assertFalse(result["passed"])
        self.assertIn('key="message-ready"', result["errors"][0]["message"])
        self.assertIn('description="Message is ready"', result["errors"][0]["message"])
        self.assertIn('left="actual"', result["errors"][0]["message"])
        self.assertIn('right="expected"', result["errors"][0]["message"])
        self.assertEqual(result["errors"][0]["type"], "check")
        self.assertIs(result["errors"][0]["expected"], True)
        self.assertIs(result["errors"][0]["actual"], False)
        self.assertEqual(result["errors"][0]["details"]["checkIndex"], 0)
        self.assertEqual(result["errors"][0]["details"]["checkKey"], "message-ready")
        self.assertEqual(result["errors"][0]["details"]["evaluated"][0], {
            "path": [0],
            "key": "message-ready",
            "description": "Message is ready",
            "reference": {"variable": "message"},
            "operator": "equals",
            "actual": "actual",
            "expected": "expected",
        })

        duplicate = evaluate_checks_result(
            Evaluator(),
            datafile,
            [
                {"key": "same", "variable": "message", "operator": "exists"},
                {"key": "same", "variable": "message", "operator": "exists"},
            ],
            owner_variable=None,
            diagnostics=[],
        )
        self.assertTrue(duplicate["error"])
        self.assertIn('duplicate sibling key "same"', duplicate["failures"][0])

    def test_structured_check_evidence_preserves_paths_operands_and_missing_state(self) -> None:
        class Evaluator:
            def get_variable(self, variable: str, context: dict) -> object:
                return {"minimum": 12, "maximum": 10, "settings": {"nullable": None}}[variable]

        datafile = {"variables": {
            "minimum": {"type": "integer", "defaultValue": 12},
            "maximum": {"type": "integer", "defaultValue": 10},
            "settings": {"type": "object", "defaultValue": {"nullable": None}},
        }}
        result = evaluate_checks_result(
            Evaluator(),
            datafile,
            [{"key": "bounds", "and": [{
                "key": "checks[1]", "variable": "minimum", "operator": "lessThan",
                "compareTo": {"variable": "maximum"},
            }]}],
            owner_variable=None,
            diagnostics=[],
            child_index=2,
        )
        error = result["errors"][0]
        self.assertEqual((error["type"], error["expected"], error["actual"]), ("check", True, False))
        self.assertEqual(error["details"]["checkKey"], "bounds")
        self.assertEqual(error["details"]["childIndex"], 2)
        self.assertEqual(error["details"]["evaluated"][0], {
            "path": [0, "and", 0], "key": "checks[1]",
            "reference": {"variable": "minimum"}, "operator": "lessThan",
            "actual": 12, "expected": 10, "compareTo": {"variable": "maximum"},
        })

        nullable = evaluate_checks_result(
            Evaluator(), datafile,
            [{"variable": "settings", "path": "nullable", "operator": "isNull"}],
            owner_variable=None, diagnostics=[],
        )
        self.assertTrue(nullable["passed"])
        missing = evaluate_checks_result(
            Evaluator(), datafile,
            [{"variable": "settings", "path": "absent", "operator": "equals", "value": None}],
            owner_variable=None, diagnostics=[],
        )
        leaf = missing["errors"][0]["details"]["evaluated"][0]
        self.assertEqual(leaf["actual"], {"$type": "undefined"})
        self.assertTrue(leaf["actualMissing"])
        self.assertIsNone(leaf["expected"])
        self.assertNotIn("expectedMissing", leaf)

    def test_report_expectations_keep_typed_values_and_authored_paths(self) -> None:
        assertion = {
            "description": "typed expectations",
            "_case": {"id": "case", "assertionIndex": 4, "matrixValues": {}},
        }
        assertion_result = {
            "passed": False,
            "errors": [
                {"type": "flag", "expected": True, "actual": False},
                {
                    "type": "variation", "expected": "control", "actual": None,
                    "details": {"childIndex": 2},
                },
                {
                    "type": "variable", "expected": 10, "actual": 12,
                    "details": {"variableKey": "limit"},
                },
            ],
        }
        case = _case_result(assertion, assertion_result, "feature", None, [], None)

        self.assertEqual(case["rerun"]["command"], f"{shlex.quote(sys.executable)} -m featurevisor")
        self.assertEqual(case["errors"][0]["path"], ["assertions", 4, "expectedToBeEnabled"])
        self.assertIs(case["errors"][0]["expected"], True)
        self.assertIs(case["errors"][0]["actual"], False)
        self.assertEqual(
            case["errors"][1]["path"],
            ["assertions", 4, "children", 2, "expectedVariation"],
        )
        self.assertIsNone(case["errors"][1]["actual"])
        self.assertEqual(
            case["errors"][2]["path"],
            ["assertions", 4, "expectedVariables", "limit"],
        )
        self.assertEqual(case["errors"][2]["expected"], 10)
        self.assertEqual(case["errors"][2]["actual"], 12)

    def test_json_report_exact_rerun_coverage_and_uncovered(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "report", "segments": {}, "features": {},
            "variables": {
                "message": {
                    "type": "string", "defaultValue": "default",
                    "overrides": [{"key": "selected", "keyPath": "selected", "segments": "*", "value": "actual"}],
                }
            },
        }
        tests = [{
            "key": "tests/scenarios/checkout.spec.yml", "scenario": "checkout",
            "_test": {"source": "tests/scenarios/checkout.spec.yml", "set": None, "entityType": "scenario"},
            "assertions": [{
                "description": "Checkout is ready", "environment": "production", "target": "checkout", "context": {},
                "checks": [{"key": "message", "variable": "message", "operator": "equals", "value": "expected"}],
                "_case": {"id": "opaque case", "assertionIndex": 0, "matrixIndex": None, "matrixValues": {}},
            }],
        }]
        with tempfile.TemporaryDirectory() as directory:
            log_path = os.path.join(directory, "calls.log")
            script = os.path.join(directory, "npx")
            source = f'''#!{sys.executable}
import json, os, sys
with open(os.environ["FAKE_NPX_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
args = sys.argv[2:]
if args[:2] == ["config", "--json"]: value = {{"environments": ["production"]}}
elif args[:2] == ["list", "--targets"]: value = ["checkout"]
elif args[:2] == ["list", "--segments"]: value = []
elif args[:2] == ["list", "--features"]: value = [{{"key": "untestedFeature"}}]
elif args[:2] == ["list", "--variables"]: value = [{{"key": "untestedVariable"}}]
elif args[:2] == ["list", "--tests"]: value = {{"kind": "featurevisor.test-execution", "schemaVersion": 1, "tests": json.loads({json.dumps(json.dumps(tests))})}}
elif args and args[0] == "build": value = json.loads({json.dumps(json.dumps(datafile))})
else: raise SystemExit("unexpected arguments: " + repr(args))
print(json.dumps(value))
'''
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(source)
            os.chmod(script, 0o755)
            env = dict(os.environ)
            env.update({"PATH": directory + os.pathsep + env["PATH"], "PYTHONPATH": "src", "FAKE_NPX_LOG": log_path})
            result = subprocess.run(
                [
                    sys.executable, "-m", "featurevisor", "test", "--json", "--pretty",
                    f"--projectDirectoryPath={os.path.dirname(os.path.dirname(__file__))}",
                    "--case=opaque case", "--target=checkout", "--coverage", "--uncovered",
                ],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env, text=True, capture_output=True,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["schemaVersion"], 1)
            self.assertEqual(report["runner"], "python")
            self.assertFalse(report["passed"])
            self.assertFalse(report["errors"], report)
            self.assertEqual(report["summary"]["scenarioCases"], {"passed": 0, "failed": 1})
            case = report["results"][0]["cases"][0]
            self.assertEqual(case["rerun"]["args"], [
                "test", "--case=opaque case",
                f"--projectDirectoryPath={os.path.dirname(os.path.dirname(__file__))}",
                "--target=checkout",
            ])
            check_error = case["errors"][0]
            self.assertEqual(check_error["type"], "check")
            self.assertEqual(check_error["message"], "Check did not match")
            self.assertIs(check_error["expected"], True)
            self.assertIs(check_error["actual"], False)
            self.assertEqual(check_error["path"], ["checks", 0])
            self.assertEqual(check_error["details"]["checkKey"], "message")
            self.assertEqual(check_error["details"]["evaluated"][0], {
                "path": ["checks", 0], "key": "message",
                "reference": {"variable": "message"}, "operator": "equals",
                "actual": "actual", "expected": "expected",
            })
            self.assertEqual(report["coverage"]["mode"], "observed-selections")
            self.assertEqual(report["coverage"]["selections"][0]["overridePath"], "selected")
            self.assertEqual(report["uncovered"], [{
                "set": None, "features": ["untestedFeature"], "variables": ["untestedVariable"],
            }])
            with open(log_path, encoding="utf-8") as handle:
                self.assertIn(
                    "list --tests --execution-protocol --json --case=opaque case",
                    handle.read(),
                )

    def test_shared_check_fixture_runs_every_case_with_a_fresh_native_instance(self) -> None:
        fixture_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "conformance", "test-checks.json")
        with open(fixture_path, encoding="utf-8") as handle:
            fixture = json.load(handle)
        self.assertEqual(fixture["version"], 3)
        self.assertEqual(len(fixture["cases"]), 255)
        self.assertIsInstance(fixture["datafile"], dict)
        executed_any = 0
        for case in fixture["cases"]:
            self.assertIsInstance(case.get("name"), str)
            self.assertIn("checks", case)
            self.assertIsInstance(case.get("passed"), bool)
            diagnostics: list[dict] = []
            sdk = create_featurevisor({"datafile": fixture["datafile"], "logLevel": "fatal", "onDiagnostic": diagnostics.append})
            try:
                result = evaluate_checks_result(
                    sdk,
                    fixture["datafile"],
                    case["checks"],
                    owner_variable=case.get("owner"),
                    diagnostics=diagnostics,
                )
            finally:
                sdk.close()
            preflight_error = case["name"] in {
                "or skips unreached missing feature",
                "and skips unreached missing feature",
                "short circuit skips unresolved ref",
                "AND short circuit",
            }
            self.assertEqual(
                result["passed"], False if preflight_error else case["passed"],
                case["name"] + ": " + "; ".join(result["failures"]),
            )
            self.assertEqual(
                result["error"], True if preflight_error else case.get("error", False),
                case["name"] + ": " + "; ".join(result["failures"]),
            )
            if "includesAny" in json.dumps(case["checks"]) or "notIncludesAny" in json.dumps(case["checks"]):
                executed_any += 1
        self.assertEqual(executed_any, 36)

    def test_quiet_check_collection_rejects_malformed_json_getter(self) -> None:
        datafile = {
            "schemaVersion": "2", "revision": "bad-json", "segments": {}, "features": {},
            "variables": {"broken": {"type": "json", "defaultValue": "["}},
        }
        diagnostics: list[dict] = []
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "error", "onDiagnostic": diagnostics.append})
        try:
            result = evaluate_checks_result(
                sdk,
                datafile,
                [{"variable": "broken", "operator": "isNull"}],
                owner_variable=None,
                diagnostics=diagnostics,
            )
        finally:
            sdk.close()
        self.assertFalse(result["passed"])
        self.assertTrue(result["error"])
        self.assertIn("variable getter reported", result["failures"][0])

    def test_native_executable_runs_scenario_children_filter_and_failure_exit(self) -> None:
        datafile = {
            "schemaVersion": "2",
            "revision": "runner",
            "segments": {},
            "features": {},
            "variables": {
                "message": {
                    "type": "string",
                    "defaultValue": "parent",
                    "overrides": [{
                        "key": "child",
                        "segments": "*",
                        "conditions": {"attribute": "mode", "operator": "equals", "value": "child"},
                        "value": "child",
                    }],
                }
            },
        }
        passing = [{
            "key": "scenarios/context.spec",
            "scenario": "context",
            "assertions": [{
                "description": "matrix expanded scenario",
                "checks": [{"variable": "message", "operator": "equals", "value": "parent"}],
                "children": [{
                    "context": {"mode": "child"},
                    "checks": [{"variable": "message", "operator": "equals", "value": "child"}],
                }],
            }],
        }]
        failing = [{
            "key": "scenarios/failure.spec",
            "scenario": "failure",
            "assertions": [{
                "description": "failing scenario",
                "checks": [{"variable": "message", "operator": "equals", "value": "wrong"}],
            }],
        }]
        malformed = [
            {"key": "scenarios/empty.spec", "scenario": "empty", "assertions": []},
            {
                "key": "scenarios/hybrid.spec", "scenario": "hybrid", "variable": "message",
                "assertions": [{"description": "hybrid", "checks": [{"variable": "message", "operator": "exists"}]}],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            log_path = os.path.join(directory, "calls.log")
            script = os.path.join(directory, "npx")
            source = f'''#!{sys.executable}
import json, os, sys
with open(os.environ["FAKE_NPX_LOG"], "a", encoding="utf-8") as log:
    log.write(" ".join(sys.argv[1:]) + "\\n")
args = sys.argv[2:]
if args[:2] == ["config", "--json"]: value = {{"environments": False}}
elif args[:2] == ["list", "--targets"]: value = []
elif args[:2] == ["list", "--segments"]: value = []
elif args[:2] == ["list", "--features"]: value = []
elif args[:2] == ["list", "--tests"]:
    if os.environ.get("FAKE_MODE") == "protocol":
        value = []
    else:
        tests = {json.dumps(passing)} if os.environ.get("FAKE_MODE") == "pass" else ({json.dumps(malformed)} if os.environ.get("FAKE_MODE") == "malformed" else {json.dumps(failing)})
        value = {{"kind": "featurevisor.test-execution", "schemaVersion": 1, "tests": tests}}
elif args and args[0] == "build": value = {json.dumps(datafile)}
else: raise SystemExit("unexpected arguments: " + repr(args))
print(json.dumps(value))
'''
            with open(script, "w", encoding="utf-8") as handle:
                handle.write(source)
            os.chmod(script, 0o755)
            env = dict(os.environ)
            env.update({"PATH": directory + os.pathsep + env["PATH"], "PYTHONPATH": "src", "FAKE_NPX_LOG": log_path})
            env["FAKE_MODE"] = "pass"
            passed = subprocess.run(
                [sys.executable, "-m", "featurevisor", "test", "--set=staging", "--entityType=scenario", "--onlyFailures"],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env, text=True, capture_output=True,
            )
            self.assertEqual(passed.returncode, 0, passed.stdout + passed.stderr)
            self.assertIn("Scenario specs: 1 passed, 0 failed", passed.stdout)
            self.assertIn("Total cases: 1 passed, 0 failed", passed.stdout)
            with open(log_path, encoding="utf-8") as log:
                calls = log.read()
                self.assertIn("--entityType=scenario", calls)
                self.assertIn("--set=staging", calls)
            env["FAKE_MODE"] = "fail"
            failed = subprocess.run(
                [sys.executable, "-m", "featurevisor", "test", "--set=staging", "--entityType=scenario", "--onlyFailures"],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env, text=True, capture_output=True,
            )
            self.assertEqual(failed.returncode, 1, failed.stdout + failed.stderr)
            self.assertIn("checks.1", failed.stdout)
            self.assertIn("left=\"parent\"", failed.stdout)
            env["FAKE_MODE"] = "malformed"
            malformed_result = subprocess.run(
                [sys.executable, "-m", "featurevisor", "test", "--set=staging", "--entityType=scenario", "--onlyFailures"],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env, text=True, capture_output=True,
            )
            self.assertEqual(malformed_result.returncode, 1, malformed_result.stdout + malformed_result.stderr)
            self.assertIn("scenario spec requires assertions", malformed_result.stdout)
            self.assertIn("requires exactly one of feature, variable, segment or scenario", malformed_result.stdout)

            with open(log_path, "w", encoding="utf-8"):
                pass
            env["FAKE_MODE"] = "protocol"
            protocol_result = subprocess.run(
                [sys.executable, "-m", "featurevisor", "test", "--set=staging", "--entityType=scenario", "--onlyFailures"],
                cwd=os.path.dirname(os.path.dirname(__file__)), env=env, text=True, capture_output=True,
            )
            self.assertEqual(protocol_result.returncode, 1, protocol_result.stdout + protocol_result.stderr)
            self.assertIn("test execution protocol", (protocol_result.stdout + protocol_result.stderr).lower())
            with open(log_path, encoding="utf-8") as log:
                calls = log.read()
            self.assertIn("list --tests --execution-protocol --json", calls)
            self.assertNotIn("featurevisor build", calls)


if __name__ == "__main__":
    unittest.main()
