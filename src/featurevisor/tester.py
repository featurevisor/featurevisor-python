from __future__ import annotations

import json
import time
import uuid
from typing import Any

from .datafile_reader import _DatafileReader
from .instance import create_instance
from .logger import create_logger
from .project import FeaturevisorProject, pretty_duration, timed_build


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
                section = "expectedVariables"
            details = error.get("details") or {}
            if details.get("childIndex") is not None:
                section = f"children[{details['childIndex']}].{section}"
            if error["type"] == "variable":
                variable_key = details.get("variableKey")
                print(f"    => {section}.{variable_key}:")
                print(f"       => expected: {_stringify_value(error.get('expected'))}")
                print(f"       => received: {_stringify_value(error.get('actual'))}")
            else:
                if error["type"] == "evaluation":
                    if details.get("variableKey"):
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


def _get_base_datafile_key(assertion: dict[str, Any]) -> str | bool:
    environment = assertion.get("environment")
    return environment if environment is not None else False


def _get_target_datafile_key(environment: str | bool | None, target: str) -> str:
    return f"{environment}-target-{target}" if environment else f"false-target-{target}"


def _get_datafile_for_assertion(assertion: dict[str, Any], cache: dict[Any, dict[str, Any]]) -> dict[str, Any]:
    key = _get_base_datafile_key(assertion)
    target_key = _get_target_datafile_key(key, assertion["target"]) if assertion.get("target") else None
    if target_key and target_key in cache:
        return cache[target_key]
    return cache[key]


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
        if assertion.get("defaultVariationValue") is not None:
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
            if assertion.get("defaultVariableValues", {}).get(variable_key) is not None:
                override_options["defaultVariableValue"] = assertion["defaultVariableValues"][variable_key]
            actual = sdk.get_variable(feature_key, variable_key, context, override_options)
            variable_schema = variables_schema.get(variable_key)
            if not variable_schema:
                result["passed"] = False
                assertion_result["passed"] = False
                assertion_result["errors"].append({"type": "variable", "expected": expected, "actual": None, "message": f'schema for variable "{variable_key}" not found in feature'})
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
                    assertion_result["errors"].append({"type": "evaluation", "expected": expected, "actual": actual, "details": {**details_base, "evaluationType": "variable", "evaluationKey": variable_key}})


def test_segment(segment: dict[str, Any], assertion_options: dict[str, Any] | None = None) -> dict[str, Any]:
    options = assertion_options or {}
    logger = create_logger({"level": _log_level(options.get("verbose", False), options.get("quiet", False))})
    reader = _DatafileReader(datafile={"schemaVersion": "2", "revision": "tester", "segments": {}, "features": {}}, logger=logger)
    result = {"type": "segment", "key": segment["segment"], "notFound": False, "passed": True, "duration": 0, "assertions": []}
    start = time.perf_counter()
    for assertion in segment["assertions"]:
        assertion_start = time.perf_counter()
        actual = reader.all_conditions_are_matched(segment["conditions"], assertion.get("context", {}))
        passed = actual == assertion["expectedToMatch"]
        assertion_result = {"description": assertion.get("description", ""), "duration": 0, "passed": passed, "errors": []}
        if not passed:
            result["passed"] = False
            assertion_result["errors"].append({"type": "segment", "expected": assertion["expectedToMatch"], "actual": actual})
        assertion_result["duration"] = int((time.perf_counter() - assertion_start) * 1000)
        result["assertions"].append(assertion_result)
    result["duration"] = int((time.perf_counter() - start) * 1000)
    return result


def _resolve_targets(project: FeaturevisorProject, requested: list[str] | None) -> list[str]:
    available = project.list_targets()
    selected = list(dict.fromkeys(requested or []))
    unknown = next((target for target in selected if target not in available), None)
    if unknown:
        raise ValueError(f'Unknown target "{unknown}". Available targets: {", ".join(available) or "none"}.')
    return selected


def run_test_project(project_directory_path: str, *, key_pattern: str | None = None, assertion_pattern: str | None = None, verbose: bool = False, quiet: bool = False, show_datafile: bool = False, only_failures: bool = False, schema_version: str | None = None, inflate: int = 0, with_scopes: bool = False, with_tags: bool = False, targets: list[str] | None = None) -> bool:
    project = FeaturevisorProject(project_directory_path)
    config = project.get_config()
    tests = project.list_tests(key_pattern=key_pattern, assertion_pattern=assertion_pattern)
    features_by_key = {item["key"]: item for item in project.list_features()}
    segments_by_key = {item["key"]: item for item in project.list_segments()}
    selected_targets = _resolve_targets(project, targets)
    targets_to_build = selected_targets or project.list_targets()
    datafile_cache: dict[Any, dict[str, Any]] = {}
    start = time.perf_counter()
    passed_tests_count = 0
    failed_tests_count = 0
    passed_assertions_count = 0
    failed_assertions_count = 0

    environments = config.get("environments")
    if environments is False:
        environments = [None]
    for environment in environments or [None]:
        base_key = environment if environment is not None else False
        datafile_cache[base_key] = project.build_datafile_json(environment=environment, inflate=inflate or None)
        for target in targets_to_build:
            datafile_cache[_get_target_datafile_key(base_key, target)] = project.build_datafile_json(
                environment=environment,
                inflate=inflate or None,
                target=target,
            )

    passed = True
    for test in tests:
        if test.get("feature"):
            assertions = [
                assertion for assertion in test["assertions"]
                if not selected_targets or not assertion.get("target") or assertion.get("target") in selected_targets
            ]
            if not assertions:
                continue
            feature_key = test["feature"]
            result = {"type": "feature", "key": feature_key, "notFound": False, "passed": True, "duration": 0, "assertions": []}
            feature_start = time.perf_counter()
            if not any(item.get("feature") == feature_key for item in tests):
                result["notFound"] = True
                result["passed"] = False
            for assertion in assertions:
                assertion_start = time.perf_counter()
                datafile = _get_datafile_for_assertion(assertion, datafile_cache)
                if show_datafile:
                    print(json.dumps(datafile, indent=2))
                sdk = create_instance({
                    "datafile": datafile,
                    "sticky": assertion.get("sticky", {}),
                    "modules": [
                        {
                            "name": "tester",
                            "bucketValue": lambda opts, at=assertion.get("at"): int(at * 1000) if at is not None else opts["bucketValue"],
                        }
                    ],
                    "logLevel": _log_level(verbose, quiet),
                })
                context = dict(assertion.get("context", {}))
                if context:
                    sdk.set_context(context)
                assertion_result = {"description": assertion.get("description", ""), "duration": 0, "passed": True, "errors": []}
                _assert_feature(sdk, feature_key, {**assertion, "context": context}, datafile, result, assertion_result)
                for index, child in enumerate(assertion.get("children", [])):
                    child_instance = sdk.spawn(child.get("context", {}), {"sticky": child.get("sticky") or assertion.get("sticky")})
                    _assert_feature(child_instance, feature_key, child, datafile, result, assertion_result, child_index=index)
                assertion_result["duration"] = int((time.perf_counter() - assertion_start) * 1000)
                result["assertions"].append(assertion_result)
            result["duration"] = int((time.perf_counter() - feature_start) * 1000)
        else:
            segment_key = test["segment"]
            segment_source = next((value for value in segments_by_key.values() if value.get("key") == segment_key), None)
            if not segment_source:
                result = {"type": "segment", "key": segment_key, "notFound": True, "passed": False, "duration": 0, "assertions": []}
            else:
                result = test_segment({"segment": segment_key, "conditions": segment_source["conditions"], "assertions": test["assertions"]}, {"verbose": verbose, "quiet": quiet})
        if result["passed"]:
            passed_tests_count += 1
        else:
            failed_tests_count += 1
        for assertion in result["assertions"]:
            if assertion["passed"]:
                passed_assertions_count += 1
            else:
                failed_assertions_count += 1
        if not (only_failures and result["passed"]):
            _print_test_result(result, test["key"])
        passed = passed and result["passed"]
    if not only_failures or not passed:
        print("\n---")
    print("")
    print(f"Test specs: {passed_tests_count} passed, {failed_tests_count} failed")
    print(f"Assertions: {passed_assertions_count} passed, {failed_assertions_count} failed")
    print(f"Time:       {pretty_duration(time.perf_counter() - start)}")
    return passed


def run_benchmark(project_directory_path: str, *, environment: str, feature: str, context: dict[str, Any] | None = None, n: int = 1000, variation: bool = False, variable: str | None = None, schema_version: str | None = None, inflate: int = 0, verbose: bool = False, quiet: bool = False, targets: list[str] | None = None) -> int:
    project = FeaturevisorProject(project_directory_path)
    selected_targets = _resolve_targets(project, targets)
    entries = selected_targets or [None]
    for target in entries:
        datafile, build_duration = timed_build(project, environment=environment, inflate=inflate or None, target=target)
        _run_benchmark_datafile(datafile, build_duration, environment=environment, target=target, feature=feature, context=context, n=n, variation=variation, variable=variable, verbose=verbose, quiet=quiet)
    return 0


def _run_benchmark_datafile(datafile: dict[str, Any], build_duration: float, *, environment: str, target: str | None, feature: str, context: dict[str, Any] | None, n: int, variation: bool, variable: str | None, verbose: bool, quiet: bool) -> None:
    level = _log_level(verbose, quiet)
    instance = create_instance({"datafile": datafile, "logLevel": level})
    context = context or {}
    total_duration_ns = 0
    min_duration_ns: int | None = None
    max_duration_ns = 0
    value = None
    for _ in range(n):
        evaluation_start = time.perf_counter_ns()
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
    print("Benchmark Featurevisor feature")
    print(f"  Feature: {feature}")
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


def run_assess_distribution(project_directory_path: str, *, environment: str, feature: str, context: dict[str, Any] | None = None, n: int = 1000, populate_uuid: list[str] | None = None, schema_version: str | None = None, inflate: int = 0, verbose: bool = False, quiet: bool = False, targets: list[str] | None = None) -> int:
    project = FeaturevisorProject(project_directory_path)
    selected_targets = _resolve_targets(project, targets)
    for target in selected_targets or [None]:
        datafile = project.build_datafile_json(environment=environment, inflate=inflate or None, target=target)
        _run_assess_datafile(datafile, environment=environment, target=target, feature=feature, context=context, n=n, populate_uuid=populate_uuid, verbose=verbose, quiet=quiet)
    return 0


def _run_assess_datafile(datafile: dict[str, Any], *, environment: str, target: str | None, feature: str, context: dict[str, Any] | None, n: int, populate_uuid: list[str] | None, verbose: bool, quiet: bool) -> None:
    instance = create_instance({"datafile": datafile, "logLevel": _log_level(verbose, quiet)})
    context = context or {}
    populate_uuid = populate_uuid or []
    feature_definition = instance.get_feature(feature) or {}
    has_variations = bool(feature_definition.get("variations"))
    flag_counts = {"enabled": 0, "disabled": 0}
    variation_counts: dict[str, int] = {}
    print("\nAssess Featurevisor distribution")
    print(f"  Feature: {feature}")
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
