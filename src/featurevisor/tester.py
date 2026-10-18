from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from collections.abc import Sequence
from typing import Any

from .segment_tests import assert_segment_references, prepare_segment_datafile
from .instance import create_featurevisor
from .project import FeaturevisorProject, FeaturevisorProjectError, pretty_duration, timed_build
from .test_checks import CheckError, CheckPreflightError, evaluate_checks_result, preflight_checks


def _stringify_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _print_test_result(result: dict[str, Any], test_key: str) -> None:
    print("")
    print(f"Testing: {test_key}.yml ({pretty_duration(result['duration'] / 1000)})")
    if result.get("notFound"):
        print(f"  => {result['type']} {result['key']} not found")
        return
    print(f'  {result["type"]} "{result["key"]}":')
    for assertion in result["assertions"]:
        marker = "✔" if assertion["passed"] else "✘"
        print(f"  {marker} {assertion['description']} ({pretty_duration(assertion['duration'] / 1000)})")
        if assertion["passed"]:
            continue
        for error in assertion.get("errors", []):
            if error.get("message"):
                print(f"    => {error['message']}")
                continue
            section = error["type"]
            if error["type"] == "flag":
                section = "expectedToBeEnabled"
            elif error["type"] == "variation":
                section = "expectedVariation"
            elif error["type"] == "variable":
                section = "expectedValue" if result["type"] == "variable" else "expectedVariables"
            details = error.get("details") or {}
            if details.get("childIndex") is not None:
                section = f"children[{details['childIndex']}].{section}"
            if error["type"] == "variable" and result["type"] == "variable":
                print(f"    => {section}:")
                print(f"       => expected: {_stringify_value(error.get('expected'))}")
                print(f"       => received: {_stringify_value(error.get('actual'))}")
            elif error["type"] == "variable":
                variable_key = details.get("variableKey")
                print(f"    => {section}.{variable_key}:")
                print(f"       => expected: {_stringify_value(error.get('expected'))}")
                print(f"       => received: {_stringify_value(error.get('actual'))}")
            else:
                if error["type"] == "evaluation":
                    if result["type"] == "variable":
                        section = f"expectedEvaluation.{details['evaluationKey']}"
                        if details.get("childIndex") is not None:
                            section = f"children[{details['childIndex']}].{section}"
                    elif details.get("variableKey"):
                        section = f"{section}.variables.{details['variableKey']}.{details['evaluationKey']}"
                    elif details.get("evaluationType"):
                        section = f"{section}.{details['evaluationType']}.{details['evaluationKey']}"
                print(
                    f'    => {section}: expected "{_stringify_value(error.get("expected"))}", received "{_stringify_value(error.get("actual"))}"'
                )


def _compare_jsonish(expected: Any, actual: Any) -> bool:
    return json.dumps(expected, sort_keys=True) == json.dumps(actual, sort_keys=True)


def _get_evaluation_value(evaluation: dict[str, Any], key: str) -> Any:
    return evaluation.get(key)


def _log_level(verbose: bool = False, quiet: bool = False) -> str:
    if verbose:
        return "debug"
    if quiet:
        return "fatal"
    return "warn"


def _check_log_level(assertion: dict[str, Any], verbose: bool, quiet: bool) -> str:
    return "error" if "checks" in assertion or assertion.get("children") else _log_level(verbose, quiet)


def _get_base_datafile_key(assertion: dict[str, Any]) -> tuple[str | None, None]:
    environment = assertion.get("environment")
    return (environment if isinstance(environment, str) else None, None)


def _get_target_datafile_key(environment: str | bool | None, target: str) -> tuple[str | None, str]:
    return (environment if isinstance(environment, str) else None, target)


def _get_datafile_for_assertion(assertion: dict[str, Any], cache: dict[tuple[str | None, str | None], dict[str, Any]]) -> dict[str, Any] | None:
    key = _get_base_datafile_key(assertion)
    target_key = _get_target_datafile_key(assertion.get("environment"), assertion["target"]) if assertion.get("target") else None
    return cache.get(target_key if target_key else key)


def _get_authored_segments(test: dict[str, Any]) -> dict[str, Any]:
    segments = test.get("_segments")
    return segments if isinstance(segments, dict) else {}


def _assert_feature(sdk, feature_key: str, assertion: dict[str, Any], datafile: dict[str, Any], result: dict[str, Any], assertion_result: dict[str, Any], *, child_index: int | None = None) -> None:
    context = assertion.get("context", {})
    details_base = {}
    if child_index is not None:
        details_base["childIndex"] = child_index
    if "expectedToBeEnabled" in assertion:
        actual = sdk.is_enabled(feature_key, context)
        expected = assertion["expectedToBeEnabled"]
        if actual != expected:
            result["passed"] = False
            assertion_result["passed"] = False
            assertion_result["errors"].append({"type": "flag", "expected": expected, "actual": actual, "details": details_base or None})
    if "expectedVariation" in assertion:
        override_options = {}
        if "defaultVariationValue" in assertion:
            override_options["defaultVariationValue"] = assertion["defaultVariationValue"]
        actual = sdk.get_variation(feature_key, context, override_options)
        expected = assertion["expectedVariation"]
        if actual != expected:
            result["passed"] = False
            assertion_result["passed"] = False
            assertion_result["errors"].append({"type": "variation", "expected": expected, "actual": actual, "details": details_base or None})
    if assertion.get("expectedVariables"):
        feature_from_datafile = datafile.get("features", {}).get(feature_key, {})
        variables_schema = feature_from_datafile.get("variablesSchema", {})
        for variable_key, expected in assertion["expectedVariables"].items():
            override_options = {}
            if variable_key in assertion.get("defaultVariableValues", {}):
                override_options["defaultVariableValue"] = assertion["defaultVariableValues"][variable_key]
            actual = sdk.get_variable(feature_key, variable_key, context, override_options)
            variable_schema = variables_schema.get(variable_key)
            if not variable_schema:
                result["passed"] = False
                assertion_result["passed"] = False
                assertion_result["errors"].append({"type": "variable", "expected": expected, "actual": None, "details": {**details_base, "variableKey": variable_key}, "message": f'schema for variable "{variable_key}" not found in feature'})
                continue
            passed = _compare_jsonish(json.loads(expected) if variable_schema.get("type") == "json" and isinstance(expected, str) else expected, actual)
            if not passed:
                result["passed"] = False
                assertion_result["passed"] = False
                assertion_result["errors"].append({"type": "variable", "expected": expected, "actual": actual, "details": {**details_base, "variableKey": variable_key}})
    if assertion.get("expectedEvaluations"):
        for evaluation_type in ("flag", "variation"):
            expected_eval = assertion["expectedEvaluations"].get(evaluation_type)
            if expected_eval:
                evaluation = getattr(sdk, f"evaluate_{evaluation_type}")(feature_key, context)
                for key, expected in expected_eval.items():
                    actual = _get_evaluation_value(evaluation, key)
                    if actual != expected:
                        result["passed"] = False
                        assertion_result["passed"] = False
                        assertion_result["errors"].append({"type": "evaluation", "expected": expected, "actual": actual, "details": {**details_base, "evaluationType": evaluation_type, "evaluationKey": key}})
        for variable_key, expected_eval in assertion["expectedEvaluations"].get("variables", {}).items():
            evaluation = sdk.evaluate_variable(feature_key, variable_key, context)
            for key, expected in expected_eval.items():
                actual = _get_evaluation_value(evaluation, key)
                if actual != expected:
                    result["passed"] = False
                    assertion_result["passed"] = False
                    assertion_result["errors"].append({"type": "evaluation", "expected": expected, "actual": actual, "details": {**details_base, "evaluationType": "variable", "variableKey": variable_key, "evaluationKey": key}})


def _append_check_errors(result: dict[str, Any], assertion_result: dict[str, Any], errors: list[dict[str, Any]]) -> None:
    if errors:
        result["passed"] = assertion_result["passed"] = False
        assertion_result["errors"].extend(errors)


def _assert_global_variable(sdk, variable_key: str, assertion: dict[str, Any], datafile: dict[str, Any], diagnostics: list[dict[str, Any]], result: dict[str, Any], assertion_result: dict[str, Any], *, child_index: int | None = None, segments: dict[str, Any] | None = None, context: dict[str, Any] | None = None) -> None:
    options = {}
    if "defaultVariableValue" in assertion:
        options["defaultVariableValue"] = assertion["defaultVariableValue"]
    evaluation = sdk.evaluate_variable(variable_key, {}, options)
    details: dict[str, Any] = {"variableKey": variable_key}
    if child_index is not None:
        details["childIndex"] = child_index
    if "expectedValue" in assertion:
        actual = evaluation.get("variableValue")
        if not _compare_jsonish(assertion["expectedValue"], actual):
            result["passed"] = assertion_result["passed"] = False
            assertion_result["errors"].append({"type": "variable", "expected": assertion["expectedValue"], "actual": actual, "details": details})
    for key, expected in assertion.get("expectedEvaluation", {}).items():
        actual = _get_evaluation_value(evaluation, key)
        if not _compare_jsonish(expected, actual):
            result["passed"] = assertion_result["passed"] = False
            assertion_result["errors"].append({"type": "evaluation", "expected": expected, "actual": actual, "details": {**details, "evaluationType": "variable", "evaluationKey": key}})
    if "checks" in assertion:
        _append_check_errors(
            result,
            assertion_result,
            evaluate_checks_result(
                sdk,
                datafile,
                assertion["checks"],
                owner_variable=variable_key,
                diagnostics=diagnostics,
                child_index=child_index,
                segments=segments,
                context=context,
            )["errors"],
        )


def _assert_scenario(sdk, assertion: dict[str, Any], datafile: dict[str, Any], diagnostics: list[dict[str, Any]], result: dict[str, Any], assertion_result: dict[str, Any], *, segments: dict[str, Any] | None = None) -> None:
    root_context = dict(assertion.get("context", {}))
    if "checks" in assertion:
        _append_check_errors(
            result,
            assertion_result,
            evaluate_checks_result(
                sdk, datafile, assertion["checks"], owner_variable=None, diagnostics=diagnostics,
                segments=segments, context=root_context,
            )["errors"],
        )
    elif not assertion.get("children"):
        _append_check_errors(
            result,
            assertion_result,
            [{"type": "check", "message": "scenario assertion requires checks or children"}],
        )
    for index, child_assertion in enumerate(assertion.get("children", [])):
        child = sdk.spawn(child_assertion.get("context", {}), {
            "stickyFeatures": child_assertion.get("stickyFeatures", {}),
            "stickyVariables": child_assertion.get("stickyVariables", {}),
        })
        try:
            _append_check_errors(
                result,
                assertion_result,
                evaluate_checks_result(
                    child,
                    datafile,
                    child_assertion.get("checks"),
                    owner_variable=None,
                    diagnostics=diagnostics,
                    child_index=index,
                    segments=segments,
                    context={**root_context, **child_assertion.get("context", {})},
                )["errors"],
            )
        finally:
            child.close()


def _missing_datafile_assertion(assertion: dict[str, Any]) -> dict[str, Any]:
    environment = assertion.get("environment") or "none"
    target = f' and target "{assertion["target"]}"' if assertion.get("target") else ""
    return {
        "description": assertion.get("description", ""),
        "duration": 0,
        "passed": False,
        "errors": [{"type": "variable", "message": f'datafile not found for environment "{environment}"{target}'}],
    }


def _preflight_assertion(
    test: dict[str, Any],
    assertion: dict[str, Any],
    datafile: dict[str, Any],
    segments: dict[str, Any],
) -> None:
    feature_key = test.get("feature")
    variable_key = test.get("variable")
    children = assertion.get("children", [])
    if not isinstance(children, list):
        raise CheckError("children must be an array")
    for index, child in enumerate(children):
        if not isinstance(child, dict):
            raise CheckError(f"children[{index}] must be an object")

    if feature_key:
        feature = (datafile.get("features") or {}).get(feature_key)
        variable_schema = (feature.get("variablesSchema") or {}) if isinstance(feature, dict) else {}
        for current in [assertion, *children]:
            expected_variables = current.get("expectedVariables") or {}
            if not isinstance(expected_variables, dict):
                raise CheckError("expectedVariables must be an object")
            for expected_variable in expected_variables:
                if expected_variable not in variable_schema:
                    raise CheckError(
                        f'variable "{expected_variable}" is absent from feature "{feature_key}" in the selected datafile'
                    )
    owner_variable = variable_key if isinstance(variable_key, str) else None
    if "checks" in assertion:
        preflight_checks(
            assertion["checks"], datafile, owner_variable=owner_variable, segments=segments
        )
    for index, child in enumerate(children):
        if "checks" in child or test.get("scenario"):
            preflight_checks(
                child.get("checks"), datafile, owner_variable=owner_variable, segments=segments, child_index=index
            )


def _preflight_failure(assertion: dict[str, Any], error: CheckError) -> dict[str, Any]:
    return {
        "description": assertion.get("description", ""),
        "duration": 0,
        "passed": False,
        "errors": [{
            "type": "check", "message": str(error),
            "expected": error.expected if isinstance(error, CheckPreflightError) else "valid check references",
            "actual": str(error),
            "details": error.details if isinstance(error, CheckPreflightError) else {},
        }],
    }


def _resolve_targets(project: FeaturevisorProject, requested: list[str] | None) -> list[str]:
    available = project.list_targets()
    selected = list(dict.fromkeys(requested or []))
    unknown = next((target for target in selected if target not in available), None)
    if unknown:
        raise ValueError(f'Unknown target "{unknown}". Available targets: {", ".join(available) or "none"}.')
    return selected


def _empty_report() -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "runner": "python",
        "passed": True,
        "summary": {
            "specs": {"passed": 0, "failed": 0},
            "assertions": {"passed": 0, "failed": 0},
            "entitySpecs": {"passed": 0, "failed": 0},
            "entityAssertions": {"passed": 0, "failed": 0},
            "scenarioSpecs": {"passed": 0, "failed": 0},
            "scenarioCases": {"passed": 0, "failed": 0},
        },
        "results": [],
        "errors": [],
    }


def _selected_sets(project_directory_path: str, config: dict[str, Any], set_name: str | None) -> Sequence[str | None]:
    if set_name is not None:
        return [set_name]
    if not config.get("sets"):
        return [None]
    directory = config.get("setsDirectoryPath") or "sets"
    path = os.path.join(project_directory_path, directory)
    sets = sorted(item for item in os.listdir(path) if os.path.isdir(os.path.join(path, item)))
    if not sets:
        raise ValueError(f"No sets found in {directory}.")
    return sets


def _coverage_module(assertion: dict[str, Any], set_name: str | None, selections: list[dict[str, Any]]) -> dict[str, Any]:
    def after_evaluation(evaluation: dict[str, Any], _options: dict[str, Any]) -> dict[str, Any]:
        if evaluation.get("featureKey") in getattr(_options.get("datafile"), "synthetic_keys", set()):
            return evaluation
        rule_key = evaluation.get("ruleKey")
        paths = list(evaluation.get("variableOverridePaths") or [])
        if not paths and evaluation.get("variableOverridePath") is not None:
            paths = [evaluation["variableOverridePath"]]
        if not paths and evaluation.get("variableOverrideKey") is not None:
            paths = [evaluation["variableOverrideKey"]]
        if not paths and rule_key is not None:
            paths = [None]
        for path in paths:
            if rule_key is None and path is None:
                continue
            item: dict[str, Any] = {
                "set": set_name,
                "environment": assertion.get("environment"),
                "target": assertion.get("target"),
            }
            for source, destination in (("featureKey", "featureKey"), ("variableKey", "variableKey"), ("ruleKey", "ruleKey")):
                if evaluation.get(source) is not None:
                    item[destination] = evaluation[source]
            if path is not None:
                item["overridePath"] = path
            selections.append(item)
        return evaluation

    return {
        "name": "tester",
        "bucketValue": lambda opts, at=assertion.get("at"): int(at * 1000) if at is not None else opts["bucketValue"],
        "afterEvaluation": after_evaluation,
    }


def _case_result(
    assertion: dict[str, Any],
    assertion_result: dict[str, Any],
    result_type: str,
    set_name: str | None,
    targets: list[str],
    project_directory_path: str | None,
) -> dict[str, Any]:
    case_metadata = assertion.get("_case") or {}
    args = ["test"]
    if case_metadata.get("id") is not None:
        args.append(f'--case={case_metadata["id"]}')
    if project_directory_path is not None:
        args.append(f"--projectDirectoryPath={project_directory_path}")
    if set_name is not None:
        args.append(f"--set={set_name}")
    args.extend(f"--target={target}" for target in targets)
    errors = []
    for error in assertion_result.get("errors", []):
        item = dict(error)
        if item.get("type") == "check" and item.get("expected") is True and item.get("actual") is False:
            item["message"] = "Check did not match"
        elif not item.get("message"):
            item["message"] = (
                f'expected {_stringify_value(error.get("expected"))}, '
                f'received {_stringify_value(error.get("actual"))}'
            )
        if item.get("type") == "check" and isinstance(item.get("details"), dict):
            details = dict(item["details"])
            base: list[Any] = [] if result_type == "scenario" else [
                "assertions", case_metadata.get("assertionIndex", 0)
            ]
            if "childIndex" in details:
                base.extend(["children", details["childIndex"]])
            collection = "checks"
            item["path"] = [*base, collection]
            if "checkIndex" in details:
                item["path"].append(details["checkIndex"])
            if isinstance(details.get("evaluated"), list):
                details["evaluated"] = [
                    {**leaf, "path": [*base, collection, *leaf.get("path", [])]}
                    for leaf in details["evaluated"]
                ]
            item["details"] = details
        elif item.get("type") in {"flag", "variation", "variable", "evaluation", "segment"}:
            raw_details = item.get("details")
            details = raw_details if isinstance(raw_details, dict) else {}
            base = ["assertions", case_metadata.get("assertionIndex", 0)]
            if "childIndex" in details:
                base.extend(["children", details["childIndex"]])
            if item["type"] == "flag":
                suffix: list[Any] = ["expectedToBeEnabled"]
            elif item["type"] == "variation":
                suffix = ["expectedVariation"]
            elif item["type"] == "segment":
                suffix = ["expectedToMatch"]
            elif item["type"] == "variable":
                suffix = ["expectedValue"] if result_type == "variable" else [
                    "expectedVariables", details.get("variableKey")
                ]
            elif result_type == "variable":
                suffix = ["expectedEvaluation", details.get("evaluationKey")]
            elif "variableKey" in details:
                suffix = [
                    "expectedEvaluations", "variables", details["variableKey"], details.get("evaluationKey")
                ]
            else:
                suffix = ["expectedEvaluations", details.get("evaluationType"), details.get("evaluationKey")]
            item["path"] = [*base, *(part for part in suffix if part is not None)]
        errors.append(item)
    return {
        "id": case_metadata.get("id"),
        "description": assertion.get("description"),
        "environment": assertion.get("environment"),
        "target": assertion.get("target"),
        "context": assertion.get("context") if isinstance(assertion.get("context"), dict) else {},
        "matrixValues": case_metadata.get("matrixValues") or {},
        "passed": assertion_result["passed"],
        "errors": errors,
        "rerun": {"command": f"{shlex.quote(sys.executable)} -m featurevisor", "args": args},
    }


def _finalize_report(report: dict[str, Any]) -> None:
    summary = _empty_report()["summary"]
    for result in report["results"]:
        spec_bucket = "scenarioSpecs" if result["type"] == "scenario" else "entitySpecs"
        case_bucket = "scenarioCases" if result["type"] == "scenario" else "entityAssertions"
        spec_state = "passed" if result["passed"] else "failed"
        summary["specs"][spec_state] += 1
        summary[spec_bucket][spec_state] += 1
        for case in result["cases"]:
            case_state = "passed" if case["passed"] else "failed"
            summary["assertions"][case_state] += 1
            summary[case_bucket][case_state] += 1
    report["summary"] = summary
    report["passed"] = not report["errors"] and summary["specs"]["failed"] == 0 and summary["assertions"]["failed"] == 0


def _human_path(path: Any) -> str:
    return ".".join(str(part + 1 if isinstance(part, int) else part) for part in (path or []))


def _human_reference(reference: Any) -> str:
    if not isinstance(reference, dict):
        return "<unknown>"
    if reference.get("segment"):
        return f'segment {reference["segment"]}'
    if reference.get("property"):
        return f'feature {reference.get("feature")}, {reference["property"]}'
    variable = reference.get("variable")
    root = (f'feature {reference["feature"]}, ' if reference.get("feature") else "global ") + f"variable {variable}"
    if "path" in reference:
        return f'{root} at {json.dumps(reference["path"], separators=(",", ":"))}'
    return root


def _human_error_lines(error: dict[str, Any]) -> list[str]:
    details = error.get("details")
    if error.get("type") != "check" or not isinstance(details, dict):
        path = error.get("path") or []
        if "children" in path:
            child_at = path.index("children")
            child = path[child_at + 1]
            section = _human_path(path[child_at + 2:])
            label = f"Child {child + 1}, {section}"
        else:
            label = _human_path(path[2:] if path[:1] == ["assertions"] else path)
        message = error.get("message") or f'expected {_stringify_value(error.get("expected"))}, received {_stringify_value(error.get("actual"))}'
        return [f"{label}: {message}" if label else message]
    named = f' "{details["checkKey"]}"' if details.get("checkKey") else ""
    description = f' ({details["checkDescription"]})' if details.get("checkDescription") else ""
    child = f'Child {details["childIndex"] + 1}, ' if isinstance(details.get("childIndex"), int) else ""
    lines = [f'{child}check{named}{description}: {error.get("message", "Check did not match")} at {_human_path(error.get("path"))}']
    for leaf in details.get("evaluated") or []:
        reference = _human_reference(leaf.get("reference"))
        if leaf.get("property"):
            reference = f'{reference}.{leaf["property"]}'
        comparison = _human_reference(leaf.get("compareTo")) if leaf.get("compareTo") else "literal"
        lines.append(
            f'{_human_path(leaf.get("path"))}: {reference} {leaf.get("operator")} {comparison} '
            f'(left={json.dumps(leaf.get("actual"), separators=(",", ":"))}, '
            f'right={json.dumps(leaf.get("expected"), separators=(",", ":"))})'
        )
    return lines


def _human_case_label(case: dict[str, Any], index: int) -> str:
    label = case.get("description") or f"assertion {index + 1}"
    details = []
    if case.get("matrixValues"):
        values = ", ".join(f"{key}={json.dumps(value, separators=(',', ':'))}" for key, value in case["matrixValues"].items())
        details.append(f"matrix {values}")
    if case.get("target"):
        details.append(f'target {case["target"]}')
    return f'{label} ({"; ".join(details)})' if details else label


def _human_rerun(rerun: dict[str, Any]) -> tuple[str, str]:
    if os.name == "nt":
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"
        return "PowerShell", rerun["command"] + " " + " ".join(quote(arg) for arg in rerun["args"])
    quote = lambda value: "'" + str(value).replace("'", "'\\''") + "'"
    return "POSIX shell", rerun["command"] + " " + " ".join(quote(arg) for arg in rerun["args"])


def _print_report(report: dict[str, Any], *, json_output: bool, pretty: bool, only_failures: bool) -> None:
    if json_output:
        print(json.dumps(report, indent=2 if pretty else None, separators=None if pretty else (",", ":")))
        return
    if not report["results"] and not report["errors"]:
        print("No tests found")
    for result in report["results"]:
        if only_failures and result["passed"]:
            continue
        print(f'\nTesting: {result["source"]}')
        print(f'  {result["type"]} "{result["key"]}":')
        for index, case in enumerate(result["cases"]):
            if only_failures and case["passed"]:
                continue
            print(f'  {"✔" if case["passed"] else "✘"} {_human_case_label(case, index)}')
            for error in case["errors"]:
                for line in _human_error_lines(error):
                    print(f"      ✘ {line}")
            if not case["passed"]:
                shell, rendered = _human_rerun(case["rerun"])
                print(f'      Rerun ({shell}): {rendered}')
    for error in report["errors"]:
        print(f'Error: {error["message"]}')
    summary = report["summary"]
    print("")
    print(f'Entity specs: {summary["entitySpecs"]["passed"]} passed, {summary["entitySpecs"]["failed"]} failed')
    print(f'Entity assertions: {summary["entityAssertions"]["passed"]} passed, {summary["entityAssertions"]["failed"]} failed')
    print(f'Scenario specs: {summary["scenarioSpecs"]["passed"]} passed, {summary["scenarioSpecs"]["failed"]} failed')
    print(f'Scenario cases: {summary["scenarioCases"]["passed"]} passed, {summary["scenarioCases"]["failed"]} failed')
    print(f'Total cases: {summary["assertions"]["passed"]} passed, {summary["assertions"]["failed"]} failed')
    if "uncovered" in report:
        print("\nDefinitions without associated specs (not evaluation coverage):")
    for item in report.get("uncovered", []):
        if item["set"]:
            print(f'  Set: {item["set"]}')
        print(f'  Features: {", ".join(item["features"]) or "none"}')
        print(f'  Global variables: {", ".join(item["variables"]) or "none"}')
    if "coverage" in report:
        print("\nObserved rule and override selections (not branch coverage):")
        for selection in report["coverage"]["selections"]:
            owner = (f'feature {selection["featureKey"]}' + (f' variable {selection["variableKey"]}' if selection.get("variableKey") else "")) if selection.get("featureKey") else f'global variable {selection.get("variableKey")}'
            scope = ", ".join(f'{key} {selection[key]}' for key in ("set", "environment", "target") if selection.get(key))
            parts = []
            if selection.get("ruleKey"):
                parts.append(f'rule {selection["ruleKey"]}')
            if "variation" in selection:
                parts.append(f'variation {json.dumps(selection["variation"])}')
            if selection.get("overridePath"):
                parts.append("override " + " > ".join(map(str, selection["overridePath"])))
            elif selection.get("overrideIndex") is not None:
                parts.append(f'override {selection["overrideIndex"] + 1}')
            print(f'  {owner}{f" ({scope})" if scope else ""}: {", ".join(parts)}')
    print("")


def run_test_project(project_directory_path: str, *, key_pattern: str | None = None, assertion_pattern: str | None = None, entity_type: str | None = None, verbose: bool = False, quiet: bool = False, show_datafile: bool = False, only_failures: bool = False, schema_version: str | None = None, inflate: int = 0, with_scopes: bool = False, with_tags: bool = False, targets: list[str] | None = None, set_name: str | None = None, json_output: bool = False, pretty: bool = False, case_id: str | None = None, coverage: bool = False, uncovered: bool = False, rerun_project_path: str | None = None) -> bool:
    report = _empty_report()
    selections: list[dict[str, Any]] = []
    try:
        root_config = FeaturevisorProject(project_directory_path).get_config()
        matched_case = case_id is None
        for current_set in _selected_sets(project_directory_path, root_config, set_name):
            project = FeaturevisorProject(project_directory_path, set_name=current_set)
            if uncovered:
                report.setdefault("uncovered", []).append({
                    "set": current_set,
                    "features": project.list_uncovered("features"),
                    "variables": project.list_uncovered("variables"),
                })
            try:
                results = _run_test_project_set(
                    project,
                    key_pattern=key_pattern,
                    assertion_pattern=assertion_pattern,
                    entity_type=entity_type,
                    verbose=verbose,
                    quiet=quiet,
                    show_datafile=show_datafile,
                    inflate=inflate,
                    targets=targets,
                    case_id=case_id,
                    coverage=coverage,
                    coverage_selections=selections,
                    json_output=json_output,
                    rerun_project_path=rerun_project_path,
                )
            except FeaturevisorProjectError as error:
                if case_id is not None and set_name is None and error.code == "test_case_not_found":
                    continue
                raise
            matched_case = True
            report["results"].extend(results)
        if not matched_case:
            raise ValueError("No test case matches --case and the selected filters")
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        report["errors"].append({"message": str(error)})
    if coverage:
        unique = {json.dumps(item, sort_keys=True, separators=(",", ":")): item for item in selections}
        report["coverage"] = {"mode": "observed-selections", "selections": [unique[key] for key in sorted(unique)]}
    if uncovered:
        report.setdefault("uncovered", []).sort(key=lambda item: item["set"] or "")
    _finalize_report(report)
    _print_report(report, json_output=json_output, pretty=pretty, only_failures=only_failures)
    return bool(report["passed"])


def _run_test_project_set(project: FeaturevisorProject, *, key_pattern: str | None = None, assertion_pattern: str | None = None, entity_type: str | None = None, verbose: bool = False, quiet: bool = False, show_datafile: bool = False, inflate: int = 0, targets: list[str] | None = None, case_id: str | None = None, coverage: bool = False, coverage_selections: list[dict[str, Any]] | None = None, json_output: bool = False, rerun_project_path: str | None = None) -> list[dict[str, Any]]:
    coverage_selections = coverage_selections if coverage_selections is not None else []
    config = project.get_config()
    tests = project.list_tests(key_pattern=key_pattern, assertion_pattern=assertion_pattern, entity_type=entity_type, case_id=case_id)
    features_by_key = {item["key"]: item for item in project.list_features()}
    segments_by_key = {item["key"]: item for item in project.list_segments()}
    selected_targets = _resolve_targets(project, targets)
    targets_to_build = selected_targets or project.list_targets()
    datafile_cache: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    report_results: list[dict[str, Any]] = []

    environments = config.get("environments")
    if environments is False:
        environments = [None]
    for environment in environments or [None]:
        base_key = _get_base_datafile_key({"environment": environment})
        datafile_cache[base_key] = project.build_datafile_json(environment=environment, inflate=inflate or None)
        for target in targets_to_build:
            datafile_cache[_get_target_datafile_key(environment, target)] = project.build_datafile_json(
                environment=environment,
                inflate=inflate or None,
                target=target,
            )

    passed = True
    for test in tests:
        executed_assertions = list(test.get("assertions") or [])
        discriminators = [key for key in ("feature", "variable", "segment", "scenario") if key in test]
        if len(discriminators) != 1:
            executed_assertions = [{"description": "malformed test spec"}]
            result = {
                "type": "test", "key": test.get("key", "malformed"), "notFound": False, "passed": False, "duration": 0,
                "assertions": [{"description": "malformed test spec", "duration": 0, "passed": False, "errors": [{"type": "check", "message": "test spec requires exactly one of feature, variable, segment or scenario"}]}],
            }
        elif "scenario" in test and not test.get("assertions"):
            executed_assertions = [{"description": "malformed scenario spec"}]
            result = {
                "type": "scenario", "key": test["scenario"], "notFound": False, "passed": False, "duration": 0,
                "assertions": [{"description": "malformed scenario spec", "duration": 0, "passed": False, "errors": [{"type": "check", "message": "scenario spec requires assertions"}]}],
            }
        elif test.get("variable"):
            assertions = [
                assertion for assertion in test["assertions"]
                if not selected_targets or not assertion.get("target") or assertion.get("target") in selected_targets
            ]
            if not assertions:
                continue
            executed_assertions = assertions
            variable_key = test["variable"]
            test_segments = _get_authored_segments(test)
            result = {"type": "variable", "key": variable_key, "notFound": False, "passed": True, "duration": 0, "assertions": []}
            variable_start = time.perf_counter()
            for assertion in assertions:
                assertion_start = time.perf_counter()
                datafile = _get_datafile_for_assertion(assertion, datafile_cache)
                if datafile is not None:
                    datafile = prepare_segment_datafile(datafile, _get_authored_segments(test))
                if datafile is None:
                    result["passed"] = False
                    result["assertions"].append(_missing_datafile_assertion(assertion))
                    continue
                try:
                    _preflight_assertion(test, assertion, datafile, test_segments)
                except CheckError as error:
                    result["passed"] = False
                    result["assertions"].append(_preflight_failure(assertion, error))
                    continue
                if show_datafile:
                    print(json.dumps(datafile, indent=2), file=sys.stderr if json_output else sys.stdout)
                diagnostics: list[dict[str, Any]] = []
                sdk = create_featurevisor({
                    "datafile": datafile,
                    "context": assertion.get("context", {}),
                    "stickyFeatures": assertion.get("stickyFeatures", {}),
                    "stickyVariables": assertion.get("stickyVariables", {}),
                    "modules": [_coverage_module(assertion, project.set_name, coverage_selections)] if coverage else [{
                        "name": "tester",
                        "bucketValue": lambda opts, at=assertion.get("at"): int(at * 1000) if at is not None else opts["bucketValue"],
                    }],
                    "logLevel": _check_log_level(assertion, verbose, quiet),
                    "onDiagnostic": diagnostics.append,
                })
                assertion_result = {"description": assertion.get("description", ""), "duration": 0, "passed": True, "errors": []}
                try:
                    root_context = dict(assertion.get("context", {}))
                    _assert_global_variable(
                        sdk, variable_key, assertion, datafile, diagnostics, result, assertion_result,
                        segments=test_segments, context=root_context,
                    )
                    for index, child_assertion in enumerate(assertion.get("children", [])):
                        child = sdk.spawn(child_assertion.get("context", {}), {
                            "stickyFeatures": child_assertion.get("stickyFeatures", {}),
                            "stickyVariables": child_assertion.get("stickyVariables", {}),
                        })
                        try:
                            _assert_global_variable(
                                child, variable_key, child_assertion, datafile, diagnostics, result, assertion_result,
                                child_index=index, segments=test_segments,
                                context={**root_context, **child_assertion.get("context", {})},
                            )
                        finally:
                            child.close()
                finally:
                    sdk.close()
                assertion_result["duration"] = int((time.perf_counter() - assertion_start) * 1000)
                result["assertions"].append(assertion_result)
            result["duration"] = int((time.perf_counter() - variable_start) * 1000)
        elif test.get("scenario"):
            assertions = [
                assertion for assertion in test["assertions"]
                if not selected_targets or not assertion.get("target") or assertion.get("target") in selected_targets
            ]
            if not assertions:
                continue
            executed_assertions = assertions
            scenario_key = test["scenario"]
            test_segments = _get_authored_segments(test)
            result = {"type": "scenario", "key": scenario_key, "notFound": False, "passed": True, "duration": 0, "assertions": []}
            scenario_start = time.perf_counter()
            for assertion in assertions:
                assertion_start = time.perf_counter()
                datafile = _get_datafile_for_assertion(assertion, datafile_cache)
                if datafile is not None:
                    datafile = prepare_segment_datafile(datafile, _get_authored_segments(test))
                if datafile is None:
                    result["passed"] = False
                    result["assertions"].append(_missing_datafile_assertion(assertion))
                    continue
                try:
                    _preflight_assertion(test, assertion, datafile, test_segments)
                except CheckError as error:
                    result["passed"] = False
                    result["assertions"].append(_preflight_failure(assertion, error))
                    continue
                if show_datafile:
                    print(json.dumps(datafile, indent=2), file=sys.stderr if json_output else sys.stdout)
                diagnostics = []
                sdk = create_featurevisor({
                    "datafile": datafile,
                    "context": assertion.get("context", {}),
                    "stickyFeatures": assertion.get("stickyFeatures", {}),
                    "stickyVariables": assertion.get("stickyVariables", {}),
                    "modules": [_coverage_module(assertion, project.set_name, coverage_selections)] if coverage else [{
                        "name": "tester",
                        "bucketValue": lambda opts, at=assertion.get("at"): int(at * 1000) if at is not None else opts["bucketValue"],
                    }],
                    "logLevel": _check_log_level(assertion, verbose, quiet),
                    "onDiagnostic": diagnostics.append,
                })
                assertion_result = {"description": assertion.get("description", ""), "duration": 0, "passed": True, "errors": []}
                try:
                    _assert_scenario(sdk, assertion, datafile, diagnostics, result, assertion_result, segments=test_segments)
                finally:
                    sdk.close()
                assertion_result["duration"] = int((time.perf_counter() - assertion_start) * 1000)
                result["assertions"].append(assertion_result)
            result["duration"] = int((time.perf_counter() - scenario_start) * 1000)
        elif test.get("feature"):
            assertions = [
                assertion for assertion in test["assertions"]
                if not selected_targets or not assertion.get("target") or assertion.get("target") in selected_targets
            ]
            if not assertions:
                continue
            executed_assertions = assertions
            feature_key = test["feature"]
            result = {"type": "feature", "key": feature_key, "notFound": False, "passed": True, "duration": 0, "assertions": []}
            feature_start = time.perf_counter()
            if not any(item.get("feature") == feature_key for item in tests):
                result["notFound"] = True
                result["passed"] = False
            for assertion in assertions:
                assertion_start = time.perf_counter()
                datafile = _get_datafile_for_assertion(assertion, datafile_cache)
                if datafile is not None:
                    datafile = prepare_segment_datafile(datafile, _get_authored_segments(test))
                if datafile is None:
                    result["passed"] = False
                    result["assertions"].append(_missing_datafile_assertion(assertion))
                    continue
                try:
                    _preflight_assertion(test, assertion, datafile, {})
                except CheckError as error:
                    result["passed"] = False
                    result["assertions"].append(_preflight_failure(assertion, error))
                    continue
                if show_datafile:
                    print(json.dumps(datafile, indent=2), file=sys.stderr if json_output else sys.stdout)
                sdk = create_featurevisor({
                    "datafile": datafile,
                    "stickyFeatures": assertion.get("stickyFeatures", {}),
                    "stickyVariables": assertion.get("stickyVariables", {}),
                    "modules": [_coverage_module(assertion, project.set_name, coverage_selections)] if coverage else [
                        {
                            "name": "tester",
                            "bucketValue": lambda opts, at=assertion.get("at"): int(at * 1000) if at is not None else opts["bucketValue"],
                        }
                    ],
                    "logLevel": "fatal" if json_output else _log_level(verbose, quiet),
                })
                context = dict(assertion.get("context", {}))
                if context:
                    sdk.set_context(context)
                assertion_result = {"description": assertion.get("description", ""), "duration": 0, "passed": True, "errors": []}
                try:
                    _assert_feature(sdk, feature_key, {**assertion, "context": context}, datafile, result, assertion_result)
                    for index, child in enumerate(assertion.get("children", [])):
                        child_instance = sdk.spawn(child.get("context", {}), {
                            "stickyFeatures": child.get("stickyFeatures", {}),
                            "stickyVariables": child.get("stickyVariables", {}),
                        })
                        try:
                            _assert_feature(child_instance, feature_key, child, datafile, result, assertion_result, child_index=index)
                        finally:
                            child_instance.close()
                finally:
                    sdk.close()
                assertion_result["duration"] = int((time.perf_counter() - assertion_start) * 1000)
                result["assertions"].append(assertion_result)
            result["duration"] = int((time.perf_counter() - feature_start) * 1000)
        else:
            segment_key = test["segment"]
            test_segments = _get_authored_segments(test)
            if segment_key not in test_segments:
                source = segments_by_key.get(segment_key)
                if source is not None:
                    test_segments = {**test_segments, segment_key: source}
            assertions = [assertion for assertion in test["assertions"]
                          if not selected_targets or not assertion.get("target") or assertion["target"] in selected_targets]
            if not assertions:
                continue
            executed_assertions = assertions
            result = {"type": "segment", "key": segment_key, "notFound": segment_key not in test_segments,
                      "passed": True, "duration": 0, "assertions": []}
            for assertion in assertions:
                datafile = _get_datafile_for_assertion(assertion, datafile_cache)
                if datafile is None and not assertion.get("environment") and not assertion.get("target"):
                    datafile = {"schemaVersion": "4", "revision": "segment-tests", "segments": {}, "features": {}}
                if datafile is None:
                    result["passed"] = False
                    result["assertions"].append(_missing_datafile_assertion(assertion))
                    continue
                datafile = prepare_segment_datafile(datafile, test_segments)
                try:
                    assert_segment_references(segment_key, datafile, test_segments)
                    _preflight_assertion(test, assertion, datafile, test_segments)
                except ValueError as error:
                    result["passed"] = False
                    result["assertions"].append(_preflight_failure(assertion, CheckError(str(error))))
                    continue
                diagnostics = []
                sdk = create_featurevisor({
                    "datafile": datafile, "context": assertion.get("context", {}),
                    "stickyFeatures": assertion.get("stickyFeatures", {}),
                    "stickyVariables": assertion.get("stickyVariables", {}),
                    "logLevel": "error", "onDiagnostic": diagnostics.append,
                    "modules": [{"name": "tester", "bucketValue":
                                 lambda opts, at=assertion.get("at"): int(at * 1000) if at is not None else opts["bucketValue"]}],
                })
                assertion_result = {"description": assertion.get("description", ""), "duration": 0, "passed": True, "errors": []}
                try:
                    helper = datafile["_segment_test_keys"].get(segment_key)
                    if helper is None:
                        raise CheckError(f'segment "{segment_key}" definition is missing')
                    for index, current in enumerate([assertion, *assertion.get("children", [])]):
                        segment_instance = sdk if index == 0 else sdk.spawn(current.get("context", {}), {
                            "stickyFeatures": current.get("stickyFeatures", {}),
                            "stickyVariables": current.get("stickyVariables", {}),
                        })
                        try:
                            evaluation = segment_instance.evaluate_flag(helper)
                            actual = evaluation.get("enabled") is True
                            if evaluation.get("reason") == "error":
                                raise CheckError(f'segment "{segment_key}" evaluation failed: {evaluation.get("error")}')
                            if "expectedToMatch" in current and actual != current["expectedToMatch"]:
                                assertion_result["errors"].append({"type": "segment", "expected": current["expectedToMatch"], "actual": actual})
                            if "checks" in current:
                                assertion_result["errors"].extend(evaluate_checks_result(
                                    segment_instance, datafile, current["checks"], diagnostics=diagnostics, owner_variable=None,
                                    segments=test_segments, context=segment_instance.get_context()).get("errors", []))
                        finally:
                            if segment_instance is not sdk:
                                segment_instance.close()
                except CheckError as error:
                    assertion_result["errors"].append({"type": "check", "message": str(error)})
                finally:
                    sdk.close()
                assertion_result["passed"] = not assertion_result["errors"]
                result["passed"] = result["passed"] and assertion_result["passed"]
                result["assertions"].append(assertion_result)
        metadata = test.get("_test") or {}
        current_set = metadata.get("set") if "set" in metadata else project.set_name
        report_results.append({
            "type": result["type"],
            "key": result["key"],
            "source": metadata.get("source") or test.get("key"),
            "set": current_set,
            "passed": result["passed"],
            "cases": [
                _case_result(
                    assertion,
                    assertion_result,
                    result["type"],
                    current_set,
                    selected_targets,
                    rerun_project_path,
                )
                for assertion, assertion_result in zip(executed_assertions, result["assertions"])
            ],
        })
    return report_results


def _runtime_selections(project_directory_path: str, environment: str | None, set_name: str | None, targets: list[str] | None) -> list[tuple[FeaturevisorProject, str | None]]:
    config = FeaturevisorProject(project_directory_path).get_config()
    environments = config.get("environments")
    if isinstance(environments, list):
        if not environment:
            raise ValueError("Pass --environment=<environment> when selecting one datafile.")
        if environment not in environments:
            raise ValueError(f'Unknown environment "{environment}". Available environments: {", ".join(environments)}.')
    elif environment is not None:
        raise ValueError("This project does not configure environments. Omit --environment.")
    selections: list[tuple[FeaturevisorProject, str | None]] = []
    for current_set in _selected_sets(project_directory_path, config, set_name):
        project = FeaturevisorProject(project_directory_path, set_name=current_set)
        selected_targets = _resolve_targets(project, targets)
        if selected_targets:
            selections.extend((project, target) for target in selected_targets)
        else:
            selections.append((project, None))
    return selections


def run_benchmark(project_directory_path: str, *, environment: str | None = None, feature: str | None = None, context: dict[str, Any] | None = None, n: int = 1000, variation: bool = False, variable: str | None = None, schema_version: str | None = None, inflate: int = 0, verbose: bool = False, quiet: bool = False, targets: list[str] | None = None, set_name: str | None = None) -> int:
    for project, target in _runtime_selections(project_directory_path, environment, set_name, targets):
        if project.set_name:
            print(f'Set "{project.set_name}"')
        datafile, build_duration = timed_build(project, environment=environment, inflate=inflate or None, target=target)
        _run_benchmark_datafile(datafile, build_duration, environment=environment, target=target, feature=feature, context=context, n=n, variation=variation, variable=variable, verbose=verbose, quiet=quiet)
    return 0


def _run_benchmark_datafile(datafile: dict[str, Any], build_duration: float, *, environment: str | None, target: str | None, feature: str | None, context: dict[str, Any] | None, n: int, variation: bool, variable: str | None, verbose: bool, quiet: bool) -> None:
    level = _log_level(verbose, quiet)
    instance = create_featurevisor({"datafile": datafile, "logLevel": level})
    context = context or {}
    total_duration_ns = 0
    min_duration_ns: int | None = None
    max_duration_ns = 0
    value = None
    for _ in range(n):
        evaluation_start = time.perf_counter_ns()
        if variable and not feature:
            value = instance.get_variable(variable, context)
        else:
            assert feature is not None
            if variation:
                value = instance.get_variation(feature, context)
            elif variable:
                value = instance.get_variable(feature, variable, context)
            else:
                value = instance.is_enabled(feature, context)
        evaluation_duration_ns = time.perf_counter_ns() - evaluation_start
        total_duration_ns += evaluation_duration_ns
        min_duration_ns = evaluation_duration_ns if min_duration_ns is None else min(min_duration_ns, evaluation_duration_ns)
        max_duration_ns = max(max_duration_ns, evaluation_duration_ns)
    duration = total_duration_ns / 1_000_000_000
    average_duration_ns = total_duration_ns / n if n else 0
    print("")
    print("Benchmark Featurevisor global variable" if variable and not feature else "Benchmark Featurevisor feature")
    if feature:
        print(f"  Feature: {feature}")
    if variable:
        print(f"  Variable: {variable}")
    if environment is not None:
        print(f"  Environment: {environment}")
    if target:
        print(f"  Target: {target}")
    print(f"  Iterations: {n}")
    print("")
    print(f'Datafile build duration: {int(build_duration * 1000)}ms')
    print(f"Datafile size: {len(json.dumps(datafile).encode()) / 1024:.2f} kB")
    print(f"Against context: {json.dumps(context)}")
    print(f"Evaluated value : {json.dumps(value)}")
    print(f"Total duration  : {pretty_duration(duration)}")
    print(f"Minimum duration: {(min_duration_ns or 0) / 1_000_000:.6f}ms")
    print(f"Average duration: {average_duration_ns / 1_000_000:.6f}ms")
    print(f"Maximum duration: {max_duration_ns / 1_000_000:.6f}ms")


def run_assess_distribution(project_directory_path: str, *, environment: str | None = None, feature: str, context: dict[str, Any] | None = None, n: int = 1000, populate_uuid: list[str] | None = None, schema_version: str | None = None, inflate: int = 0, verbose: bool = False, quiet: bool = False, targets: list[str] | None = None, set_name: str | None = None) -> int:
    for project, target in _runtime_selections(project_directory_path, environment, set_name, targets):
        if project.set_name:
            print(f'Set "{project.set_name}"')
        datafile = project.build_datafile_json(environment=environment, inflate=inflate or None, target=target)
        _run_assess_datafile(datafile, environment=environment, target=target, feature=feature, context=context, n=n, populate_uuid=populate_uuid, verbose=verbose, quiet=quiet)
    return 0


def _run_assess_datafile(datafile: dict[str, Any], *, environment: str | None, target: str | None, feature: str, context: dict[str, Any] | None, n: int, populate_uuid: list[str] | None, verbose: bool, quiet: bool) -> None:
    instance = create_featurevisor({"datafile": datafile, "logLevel": _log_level(verbose, quiet)})
    context = context or {}
    populate_uuid = populate_uuid or []
    feature_definition = instance.get_feature(feature) or {}
    has_variations = bool(feature_definition.get("variations"))
    flag_counts = {"enabled": 0, "disabled": 0}
    variation_counts: dict[str, int] = {}
    print("\nAssess Featurevisor distribution")
    print(f"  Feature: {feature}")
    if environment is not None:
        print(f"  Environment: {environment}")
    if target:
        print(f"  Target: {target}")
    print(f"  Iterations: {n}")
    print(f"Against context: {json.dumps(context)}")
    print(f"Running {n} times...")
    for _ in range(n):
        current_context = dict(context)
        for key in populate_uuid:
            current_context[key] = str(uuid.uuid4())
        enabled = instance.is_enabled(feature, current_context)
        flag_counts["enabled" if enabled else "disabled"] += 1
        if has_variations:
            variation = instance.get_variation(feature, current_context)
            if variation is not None:
                variation_counts[str(variation)] = variation_counts.get(str(variation), 0) + 1
    print("\n\nFlag evaluations:")
    for key, count in sorted(flag_counts.items(), key=lambda item: item[1], reverse=True):
        print(f"  - {key}: {count} {(count / n) * 100:.2f}%")
    if has_variations:
        print("\n\nVariation evaluations:")
        for key, count in sorted(variation_counts.items(), key=lambda item: item[1], reverse=True):
            print(f"  - {key}: {count} {(count / n) * 100:.2f}%")
