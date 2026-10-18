from __future__ import annotations

from .segment_tests import assert_segment_references, prepare_segment_datafile
from .evaluate import evaluate_with_modules

import json
import math
import re
from datetime import date
from typing import Any

from .compare_versions import compare_versions
from .evaluation_data_provider import _InstanceEvaluationDataProvider


MISSING = object()
UNSAFE_PATH_PARTS = {"__proto__", "prototype", "constructor"}
MAX_SAFE_INTEGER = 9_007_199_254_740_991
VALUELESS_OPERATORS = {"exists", "notExists", "isNull", "notNull", "empty", "notEmpty"}
KNOWN_OPERATORS = VALUELESS_OPERATORS | {
    "equals", "notEquals", "greaterThan", "greaterThanOrEquals", "lessThan",
    "lessThanOrEquals", "contains", "notContains", "startsWith", "endsWith",
    "matches", "notMatches", "semverEquals", "semverNotEquals",
    "semverGreaterThan", "semverGreaterThanOrEquals", "semverLessThan",
    "semverLessThanOrEquals", "before", "after", "in", "notIn", "includes",
    "notIncludes", "includesAny", "notIncludesAny",
}


class CheckError(ValueError):
    pass


class CheckPreflightError(CheckError):
    def __init__(self, message: str, expected: str, check_index: int | None = None, child_index: int | None = None):
        super().__init__(message)
        self.expected = expected
        self.details = {}
        if check_index is not None:
            self.details["checkIndex"] = check_index
        if child_index is not None:
            self.details["childIndex"] = child_index


def _json(value: Any) -> str:
    if value is MISSING:
        return "<missing>"
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _reference_label(reference: dict[str, Any], owner_variable: str | None) -> str:
    variable = reference.get("variable") or owner_variable or "<missing variable>"
    root = f'{reference["feature"]}.{variable}' if reference.get("feature") else variable
    path = reference.get("path")
    return root if path is None or path == [] else f"{root}:{_json(path)}"


def _path_parts(path: Any) -> list[str | int]:
    if path is None:
        return []
    if isinstance(path, str):
        raw: Any = path.split(".")
        if any(part == "" for part in raw):
            raise CheckError("path must not contain empty dot components")
        parts: list[str | int] = []
        for part in raw:
            if re.fullmatch(r"0|[1-9]\d*", part):
                index = int(part)
                if index > MAX_SAFE_INTEGER:
                    raise CheckError("path indices must be safe nonnegative integers")
                parts.append(index)
            else:
                parts.append(part)
    elif isinstance(path, list):
        parts = path
    else:
        raise CheckError("path must be a dot separated string or an array")
    for part in parts:
        if isinstance(part, bool) or not isinstance(part, (str, int)):
            raise CheckError("path entries must be string keys or nonnegative integer indices")
        if isinstance(part, int) and (part < 0 or part > MAX_SAFE_INTEGER):
            raise CheckError("path indices must be safe nonnegative integers")
        if isinstance(part, str) and part in UNSAFE_PATH_PARTS:
            raise CheckError(f'unsafe path segment "{part}" is not allowed')
        if part == "*":
            raise CheckError("wildcards are not allowed in paths")
    return parts


def _at_path(value: Any, parts: list[str | int]) -> Any:
    current = value
    for part in parts:
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return MISSING
            current = current[part]
        else:
            if isinstance(current, list):
                if part == "length":
                    current = len(current)
                elif re.fullmatch(r"0|[1-9]\d*", part) and int(part) < len(current):
                    current = current[int(part)]
                else:
                    return MISSING
            elif isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return MISSING
    return current


def _strict_equal(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isfinite(left) and math.isfinite(right) and left == right
    return type(left) is type(right) and isinstance(left, str) and left == right


def _primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool)) or (
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    )


def _membership_primitive(value: Any) -> bool:
    return value is None or isinstance(value, str) or (
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    )


def _date(value: Any) -> tuple[int, int]:
    if not isinstance(value, str):
        raise CheckError("date comparisons require valid portable timestamps")
    match = re.fullmatch(
        r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})(?:\.([0-9]{1,9}))?(Z|[+-][0-9]{2}:[0-9]{2})",
        value,
    )
    if match is None:
        raise CheckError("date comparisons require valid portable timestamps")
    year, month, day, hour, minute, second = (int(part) for part in match.groups()[:6])
    if not 1 <= year <= 9999 or not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise CheckError("date comparisons require valid portable timestamps")
    try:
        ordinal = date(year, month, day).toordinal()
    except ValueError as error:
        raise CheckError("date comparisons require valid portable timestamps") from error
    zone = match.group(8)
    offset = 0
    if zone != "Z":
        offset_hour, offset_minute = (int(part) for part in zone[1:].split(":"))
        if offset_hour > 14 or offset_minute > 59 or (offset_hour == 14 and offset_minute != 0):
            raise CheckError("date comparisons require valid portable timestamps")
        offset = offset_hour * 3600 + offset_minute * 60
        if zone[0] == "-":
            offset = -offset
    instant = (ordinal - 1) * 86400 + hour * 3600 + minute * 60 + second - offset
    maximum = date(9999, 12, 31).toordinal() * 86400 - 1
    if instant < 0 or instant > maximum:
        raise CheckError("date comparisons require valid portable timestamps")
    fraction = match.group(7) or ""
    return instant, int(fraction[:3].ljust(3, "0") or "0")


def _regex(pattern: Any, flags: Any) -> re.Pattern[str]:
    if not isinstance(pattern, str):
        raise CheckError("regular expression comparisons require string operands")
    flags = flags or ""
    if not isinstance(flags, str) or not re.fullmatch(r"[gims]*", flags) or len(set(flags)) != len(flags):
        raise CheckError("regexFlags must contain unique characters from g, i, m and s")
    if re.search(r"\(\?", pattern) or re.search(r"\\(?:[1-9]|k<|k'|g<|g')", pattern) or re.search(r"(?:[?*+]|\{\d+(?:,\d*)?\})\+", pattern):
        raise CheckError("regular expression uses syntax outside the portable subset")
    native_flags = (re.IGNORECASE if "i" in flags else 0) | (re.MULTILINE if "m" in flags else 0) | (re.DOTALL if "s" in flags else 0)
    try:
        return re.compile(pattern, native_flags)
    except re.error as error:
        raise CheckError(f"invalid regular expression: {error}") from error


def _compare(operator: str, left: Any, right: Any, regex_flags: Any = None) -> bool:
    if operator == "exists":
        return left is not MISSING
    if operator == "notExists":
        return left is MISSING
    if operator == "isNull":
        return left is None
    if operator == "notNull":
        return left is not MISSING and left is not None
    if operator in {"empty", "notEmpty"}:
        if left is MISSING or left is None:
            return False
        if not isinstance(left, (str, list, dict)):
            raise CheckError(f'operator "{operator}" requires a string, array or object operand')
        return len(left) == 0 if operator == "empty" else len(left) > 0
    if left is MISSING or right is MISSING:
        return False
    if operator in {"equals", "notEquals"}:
        if not _primitive(left) or not _primitive(right):
            raise CheckError(f'operator "{operator}" only supports scalar values')
        matched = _strict_equal(left, right)
        return matched if operator == "equals" else not matched
    if operator in {"greaterThan", "greaterThanOrEquals", "lessThan", "lessThanOrEquals"}:
        numeric = lambda value: isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        if not numeric(left) or not numeric(right):
            raise CheckError(f'operator "{operator}" requires numeric operands')
        return {"greaterThan": left > right, "greaterThanOrEquals": left >= right, "lessThan": left < right, "lessThanOrEquals": left <= right}[operator]
    if operator in {"contains", "notContains", "startsWith", "endsWith", "matches", "notMatches"}:
        if not isinstance(left, str) or not isinstance(right, str):
            raise CheckError(f'operator "{operator}" requires string operands')
        if operator == "contains": return right in left
        if operator == "notContains": return right not in left
        if operator == "startsWith": return left.startswith(right)
        if operator == "endsWith": return left.endswith(right)
        matched = _regex(right, regex_flags).search(left) is not None
        return matched if operator == "matches" else not matched
    if operator.startswith("semver"):
        if not isinstance(left, str) or not isinstance(right, str):
            raise CheckError(f'operator "{operator}" requires string operands')
        try:
            compared = compare_versions(left, right)
        except ValueError as error:
            raise CheckError(str(error)) from error
        return {
            "semverEquals": compared == 0, "semverNotEquals": compared != 0,
            "semverGreaterThan": compared == 1, "semverGreaterThanOrEquals": compared >= 0,
            "semverLessThan": compared == -1, "semverLessThanOrEquals": compared <= 0,
        }[operator]
    if operator in {"before", "after"}:
        return _date(left) < _date(right) if operator == "before" else _date(left) > _date(right)
    if operator in {"in", "notIn"}:
        if not _membership_primitive(left) or not isinstance(right, list) or not all(_membership_primitive(value) for value in right):
            raise CheckError(f'operator "{operator}" requires a scalar left operand and scalar array right operand')
        matched = any(_strict_equal(left, value) for value in right)
        return matched if operator == "in" else not matched
    if operator in {"includesAny", "notIncludesAny"}:
        if not isinstance(left, list) or not isinstance(right, list) or not all(_primitive(value) for value in right):
            raise CheckError(f'operator "{operator}" requires an array left operand and primitive array right operand')
        matched = any(_strict_equal(actual, candidate) for candidate in right for actual in left)
        return matched if operator == "includesAny" else not matched
    if operator in {"includes", "notIncludes"}:
        if not isinstance(left, list) or not _membership_primitive(right):
            raise CheckError(f'operator "{operator}" requires an array left operand and scalar right operand')
        matched = any(_strict_equal(value, right) for value in left)
        return matched if operator == "includes" else not matched
    raise CheckError(f'unknown check operator "{operator}"')


class _Resolver:
    def __init__(
        self,
        sdk: Any,
        datafile: dict[str, Any],
        diagnostics: list[dict[str, Any]],
        owner_variable: str | None,
        segments: dict[str, Any],
        context: dict[str, Any],
    ):
        self.sdk = sdk
        self.datafile = datafile
        self.diagnostics = diagnostics
        self.owner_variable = owner_variable
        self.segments = segments
        self.context = context


    def resolve(self, reference: dict[str, Any]) -> Any:
        variable = reference.get("variable") or self.owner_variable
        feature = reference.get("feature")
        if not isinstance(variable, str) or not variable:
            raise CheckError("variable reference requires a nonempty variable")
        if feature is not None and (not isinstance(feature, str) or not feature):
            raise CheckError("feature reference requires a nonempty feature")
        if feature:
            definition = (self.datafile.get("features") or {}).get(feature)
            if not isinstance(definition, dict):
                raise CheckError(f'feature "{feature}" is absent from the selected datafile')
            if variable not in (definition.get("variablesSchema") or {}):
                raise CheckError(f'variable "{variable}" is absent from feature "{feature}" in the selected datafile')
        elif variable not in (self.datafile.get("variables") or {}):
            raise CheckError(f'global variable "{variable}" is absent from the selected datafile')
        parts = _path_parts(reference.get("path"))
        self.diagnostics.clear()
        try:
            value = self.sdk.get_variable(feature, variable, {}) if feature else self.sdk.get_variable(variable, {})
        except Exception as error:
            raise CheckError(f"variable getter failed: {error}") from error
        errors = [item for item in self.diagnostics if item.get("level") == "error"]
        if errors:
            diagnostic = errors[0]
            raise CheckError(f'variable getter reported {diagnostic.get("code", "an error")}: {diagnostic.get("message", "evaluation failed")}')
        return _at_path(value, parts)

    def resolve_feature(self, feature: str, getter: str) -> Any:
        definition = (self.datafile.get("features") or {}).get(feature)
        if not isinstance(definition, dict):
            raise CheckError(f'feature "{feature}" is absent from the selected datafile')
        self.diagnostics.clear()
        try:
            value = self.sdk.is_enabled(feature, {}) if getter == "enabled" else self.sdk.get_variation(feature, {})
        except Exception as error:
            raise CheckError(f'feature "{feature}" {getter} getter failed: {error}') from error
        errors = [item for item in self.diagnostics if item.get("level") == "error"]
        if errors:
            diagnostic = errors[0]
            raise CheckError(
                f'feature "{feature}" {getter} getter reported {diagnostic.get("code", "an error")}: '
                f'{diagnostic.get("message", "evaluation failed")}'
            )
        return value

    def resolve_segment(self, segment: str) -> bool:
        definition = self.segments.get(segment)
        if not isinstance(definition, dict) or "conditions" not in definition:
            raise CheckError(f'segment "{segment}" definition is missing from the normalized test spec')
        self.diagnostics.clear()
        helper = self.datafile.get("_segment_test_keys", {}).get(segment)
        if helper is None:
            prepared = prepare_segment_datafile(self.datafile, self.segments)
            helper = prepared["_segment_test_keys"][segment]
            root = getattr(self.sdk, "parent", self.sdk)
            options = self.sdk._merge_options({}) if hasattr(self.sdk, "parent") else {}
            options["__featurevisor_context_is_effective"] = True
            dependencies = root._get_evaluation_dependencies(self.sdk.get_context(self.context), options)
            reader = _InstanceEvaluationDataProvider(datafile=prepared, diagnostics=root.evaluation_diagnostics)
            reader.conditions_are_matched = root._conditions_are_matched
            evaluation = evaluate_with_modules({**dependencies, "datafile": reader, "type": "flag", "featureKey": helper})
        else:
            evaluation = self.sdk.evaluate_flag(helper, {})
        errors = [item for item in self.diagnostics if item.get("level") == "error"]
        if evaluation.get("reason") == "error" or errors:
            error = evaluation.get("error") or errors[0].get("message", "evaluation failed")
            raise CheckError(f'segment "{segment}" evaluation failed: {error}')
        return evaluation.get("enabled") is True


def _is_feature_check(node: Any) -> bool:
    return isinstance(node, dict) and (
        "enabled" in node
        or "variation" in node
        or ("feature" in node and "variable" not in node and "operator" not in node)
    )


def _is_segment_check(node: Any) -> bool:
    return isinstance(node, dict) and ("segment" in node or "matches" in node)


def _validate_segment_check(node: dict[str, Any], path: str) -> None:
    allowed = {"segment", "matches", "key", "description"}
    unknown = next((key for key in node if key not in allowed), None)
    if unknown is not None:
        raise CheckError(f'{path}: segment check cannot contain property "{unknown}"')
    if not isinstance(node.get("segment"), str) or not node["segment"]:
        raise CheckError(f"{path}: segment check requires a nonempty segment")
    if "matches" not in node or not isinstance(node["matches"], bool):
        raise CheckError(f'{path}: segment "{node["segment"]}" matches must be a boolean')


def _validate_feature_check(node: dict[str, Any], path: str) -> None:
    allowed = {"feature", "enabled", "variation", "key", "description"}
    unknown = next((key for key in node if key not in allowed), None)
    if unknown is not None:
        raise CheckError(f'{path}: feature check cannot contain property "{unknown}"')
    if not isinstance(node.get("feature"), str) or not node["feature"]:
        raise CheckError(f"{path}: feature must be a nonempty string")
    if "enabled" not in node and "variation" not in node:
        raise CheckError(f"{path}: feature check requires enabled or variation")
    if "enabled" in node and not isinstance(node["enabled"], bool):
        raise CheckError(f"{path}: enabled must be a boolean")
    if "variation" in node and node["variation"] is not None and not isinstance(node["variation"], str):
        raise CheckError(f"{path}: variation must be a string or null")


def _validate_node(node: Any, owner_variable: str | None, path: str) -> None:
    if not isinstance(node, dict):
        raise CheckError(f"{path}: check must be an object")
    if "key" in node and (not isinstance(node["key"], str) or not node["key"]):
        raise CheckError(f"{path}.key: key must be a nonempty string")
    if "description" in node and not isinstance(node["description"], str):
        raise CheckError(f"{path}.description: description must be a string")
    if _is_segment_check(node):
        _validate_segment_check(node, path)
        return
    groups = [key for key in ("and", "or", "not") if key in node]
    if groups:
        allowed = {groups[0], "key", "description"}
        if len(groups) != 1 or any(key not in allowed for key in node):
            raise CheckError(f"{path}: group must contain exactly one of and, or or not plus optional key and description")
        children = node[groups[0]]
        if not isinstance(children, list) or not children:
            raise CheckError(f"{path}.{groups[0]}: group must be a nonempty array")
        _validate_sibling_keys(children, f"{path}.{groups[0]}")
        for index, child in enumerate(children):
            _validate_node(child, owner_variable, f"{path}.{groups[0]}[{index}]")
        return
    if _is_feature_check(node):
        _validate_feature_check(node, path)
        return
    operator = node.get("operator")
    allowed = {"variable", "feature", "path", "operator", "value", "compareTo", "regexFlags", "key", "description"}
    unknown = next((key for key in node if key not in allowed), None)
    if unknown is not None:
        raise CheckError(f'{path}: unknown check property "{unknown}"')
    if not isinstance(operator, str) or operator not in KNOWN_OPERATORS:
        raise CheckError(f'{path}: unknown check operator "{operator}"')
    if "feature" in node and (not isinstance(node["feature"], str) or not node["feature"]):
        raise CheckError(f"{path}: feature must be a nonempty string")
    if "variable" in node and (not isinstance(node["variable"], str) or not node["variable"]):
        raise CheckError(f"{path}: variable must be a nonempty string")
    if "feature" in node and "variable" not in node:
        raise CheckError(f"{path}: a feature reference requires an explicit variable")
    if not node.get("variable") and not owner_variable:
        raise CheckError(f"{path}: variable reference requires an explicit variable")
    if "path" in node and node["path"] is None:
        raise CheckError(f"{path}: path must not be null")
    _path_parts(node.get("path"))
    supplied = int("value" in node) + int("compareTo" in node)
    if operator in VALUELESS_OPERATORS:
        if supplied:
            raise CheckError(f'{path}: operator "{operator}" accepts neither value nor compareTo')
    elif supplied != 1:
        raise CheckError(f"{path}: exactly one of value or compareTo is required")
    if "value" in node:
        value = node["value"]
        if operator in {"equals", "notEquals"} and not _primitive(value):
            raise CheckError(f'{path}: operator "{operator}" requires a scalar value')
        if operator in {"greaterThan", "greaterThanOrEquals", "lessThan", "lessThanOrEquals"} and not _membership_primitive(value):
            raise CheckError(f'{path}: operator "{operator}" requires a numeric value')
        if operator in {"greaterThan", "greaterThanOrEquals", "lessThan", "lessThanOrEquals"} and not isinstance(value, (int, float)):
            raise CheckError(f'{path}: operator "{operator}" requires a numeric value')
        if operator in {"contains", "notContains", "startsWith", "endsWith", "matches", "notMatches", "semverEquals", "semverNotEquals", "semverGreaterThan", "semverGreaterThanOrEquals", "semverLessThan", "semverLessThanOrEquals", "before", "after"} and not isinstance(value, str):
            raise CheckError(f'{path}: operator "{operator}" requires a string value')
        if operator in {"in", "notIn"} and (not isinstance(value, list) or not all(_membership_primitive(item) for item in value)):
            raise CheckError(f'{path}: operator "{operator}" requires an array of string, number or null values')
        if operator in {"includes", "notIncludes"} and not _membership_primitive(value):
            raise CheckError(f'{path}: operator "{operator}" requires a scalar value')
        if operator in {"includesAny", "notIncludesAny"} and (not isinstance(value, list) or not all(_primitive(item) for item in value)):
            raise CheckError(f'{path}: operator "{operator}" requires an array of primitive values')
        if operator in {"matches", "notMatches"}:
            _regex(value, node.get("regexFlags"))
        if operator in {"before", "after"}:
            _date(value)
        if operator.startswith("semver"):
            try:
                compare_versions(value, value)
            except ValueError as error:
                raise CheckError(str(error)) from error
    if "compareTo" in node:
        other = node["compareTo"]
        if not isinstance(other, dict):
            raise CheckError(f"{path}.compareTo: reference must be an object")
        if "feature" in other and (not isinstance(other["feature"], str) or not other["feature"]):
            raise CheckError(f"{path}.compareTo: feature must be a nonempty string")
        if "variable" in other and (not isinstance(other["variable"], str) or not other["variable"]):
            raise CheckError(f"{path}.compareTo: variable must be a nonempty string")
        if "feature" in other and "variable" not in other:
            raise CheckError(f"{path}.compareTo: a feature reference requires an explicit variable")
        if not other.get("variable") and not owner_variable:
            raise CheckError(f"{path}.compareTo: variable reference requires an explicit variable")
        unknown = next((key for key in other if key not in {"variable", "feature", "path"}), None)
        if unknown is not None:
            raise CheckError(f'{path}.compareTo: unknown reference property "{unknown}"')
        if "path" in other and other["path"] is None:
            raise CheckError(f"{path}.compareTo: path must not be null")
        _path_parts(other.get("path"))
    if "regexFlags" in node and operator not in {"matches", "notMatches"}:
        raise CheckError(f'{path}: regexFlags are only allowed with matches or notMatches')
    if operator in {"matches", "notMatches"}:
        flags = node.get("regexFlags", "")
        if not isinstance(flags, str) or not re.fullmatch(r"[gims]*", flags) or len(set(flags)) != len(flags):
            raise CheckError(f"{path}: regexFlags must contain unique characters from g, i, m and s")


def _missing_value() -> dict[str, str]:
    return {"$type": "undefined"}


def _report_operand(value: Any) -> Any:
    return _missing_value() if value is MISSING else value


def _normalized_reference(reference: dict[str, Any], owner_variable: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    variable = reference.get("variable") or owner_variable
    if variable is not None:
        result["variable"] = variable
    if "feature" in reference:
        result["feature"] = reference["feature"]
    if "path" in reference:
        result["path"] = reference["path"]
    return result


def _format_path(path: list[str | int]) -> str:
    rendered = ""
    for index, part in enumerate(path):
        if index == 0:
            rendered = f"checks[{part}]"
        elif isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}"
    return rendered


def _evaluate_node(
    node: dict[str, Any],
    resolver: _Resolver,
    path: list[str | int],
    evaluated: list[dict[str, Any]],
) -> bool:
    for group in ("and", "or", "not"):
        if group in node:
            if group in {"and", "not"}:
                for index, child in enumerate(node[group]):
                    matched = _evaluate_node(child, resolver, [*path, group, index], evaluated)
                    if not matched:
                        return group == "not"
                return group != "not"
            for index, child in enumerate(node[group]):
                matched = _evaluate_node(child, resolver, [*path, group, index], evaluated)
                if matched:
                    return True
            return False
    if _is_segment_check(node):
        trace = {
            "path": path,
            "reference": {"segment": node["segment"]},
            "property": "matches",
            "operator": "equals",
            "actual": _missing_value(),
            "expected": node["matches"],
        }
        if "key" in node:
            trace["key"] = node["key"]
        if "description" in node:
            trace["description"] = node["description"]
        evaluated.append(trace)
        actual = resolver.resolve_segment(node["segment"])
        trace["actual"] = actual
        return actual is node["matches"]
    if _is_feature_check(node):
        feature = node["feature"]
        matched = True
        for property_name in ("enabled", "variation"):
            if property_name not in node:
                continue
            trace = {
                "path": path,
                "reference": {"feature": feature},
                "property": property_name,
                "operator": "equals",
                "actual": _missing_value(),
                "expected": node[property_name],
            }
            if "key" in node:
                trace["key"] = node["key"]
            if "description" in node:
                trace["description"] = node["description"]
            evaluated.append(trace)
            actual = resolver.resolve_feature(feature, property_name)
            trace["actual"] = _report_operand(actual)
            matched = _strict_equal(actual, node[property_name]) and matched
        return matched
    left_ref = {key: node[key] for key in ("variable", "feature", "path") if key in node}
    trace = {
        "path": path,
        "reference": _normalized_reference(left_ref, resolver.owner_variable),
        "operator": node["operator"],
        "actual": _missing_value(),
        "expected": _missing_value() if "compareTo" in node else _report_operand(node.get("value", MISSING)),
    }
    if "key" in node:
        trace["key"] = node["key"]
    if "description" in node:
        trace["description"] = node["description"]
    if "compareTo" in node:
        trace["compareTo"] = _normalized_reference(node["compareTo"], resolver.owner_variable)
    evaluated.append(trace)
    try:
        left = resolver.resolve(left_ref)
    except CheckError as error:
        raise CheckError(f"{_format_path(path)} left operand ({_reference_label(left_ref, resolver.owner_variable)}): {error}") from error
    trace["actual"] = _report_operand(left)
    if left is MISSING:
        trace["actualMissing"] = True
    if "compareTo" in node:
        right_ref = node["compareTo"]
        try:
            right = resolver.resolve(right_ref)
        except CheckError as error:
            raise CheckError(f"{_format_path(path)} compareTo operand ({_reference_label(right_ref, resolver.owner_variable)}): {error}") from error
    else:
        right = node.get("value", MISSING)
    trace["expected"] = _report_operand(right)
    if "compareTo" in node and right is MISSING:
        trace["expectedMissing"] = True
    return _compare(node["operator"], left, right, node.get("regexFlags"))


def _check_label(path: str, node: dict[str, Any]) -> str:
    metadata: list[str] = []
    if "key" in node:
        metadata.append(f'key={json.dumps(node["key"])}')
    if "description" in node:
        metadata.append(f'description={json.dumps(node["description"])}')
    return f'{path} [{", ".join(metadata)}]' if metadata else path


def _validate_sibling_keys(nodes: list[Any], path: str) -> None:
    seen: dict[str, int] = {}
    for index, node in enumerate(nodes):
        if not isinstance(node, dict) or "key" not in node:
            continue
        key = node["key"]
        if not isinstance(key, str) or not key:
            raise CheckError(f"{path}[{index}].key: key must be a nonempty string")
        if key in seen:
            raise CheckError(f'{path}[{index}].key: duplicate sibling key "{key}", first used at {path}[{seen[key]}]')
        seen[key] = index


def _validate_reference_membership(
    reference: dict[str, Any],
    datafile: dict[str, Any],
    owner_variable: str | None,
    path: str,
) -> None:
    variable = reference.get("variable") or owner_variable
    feature = reference.get("feature")
    if feature:
        definition = (datafile.get("features") or {}).get(feature)
        if not isinstance(definition, dict):
            raise CheckError(f'{path}: feature "{feature}" is absent from the selected datafile')
        if variable not in (definition.get("variablesSchema") or {}):
            raise CheckError(
                f'{path}: variable "{variable}" is absent from feature "{feature}" in the selected datafile'
            )
    elif variable not in (datafile.get("variables") or {}):
        raise CheckError(f'{path}: global variable "{variable}" is absent from the selected datafile')


def _preflight_node(
    node: dict[str, Any],
    datafile: dict[str, Any],
    owner_variable: str | None,
    segments: dict[str, Any],
    path: str,
) -> None:
    if _is_segment_check(node):
        definition = segments.get(node["segment"])
        if not isinstance(definition, dict) or "conditions" not in definition:
            raise CheckError(
                f'{path}: segment "{node["segment"]}" definition is missing from the normalized test spec'
            )
        try:
            assert_segment_references(node["segment"], datafile, segments)
        except ValueError as exc:
            raise CheckError(f"{path}: {exc}") from exc
        return
    for group in ("and", "or", "not"):
        if group in node:
            for index, child in enumerate(node[group]):
                _preflight_node(
                    child,
                    datafile,
                    owner_variable,
                    segments,
                    f"{path}.{group}[{index}]",
                )
            return
    if _is_feature_check(node):
        if not isinstance((datafile.get("features") or {}).get(node["feature"]), dict):
            raise CheckError(
                f'{path}: feature "{node["feature"]}" is absent from the selected datafile'
            )
        return
    _validate_reference_membership(node, datafile, owner_variable, path)
    if "compareTo" in node:
        _validate_reference_membership(
            node["compareTo"], datafile, owner_variable, f"{path}.compareTo"
        )


def preflight_checks(
    checks: Any,
    datafile: dict[str, Any],
    *,
    owner_variable: str | None,
    segments: dict[str, Any] | None = None,
    child_index: int | None = None,
) -> None:
    try:
        if not isinstance(checks, list) or not checks:
            raise CheckError("checks must be a nonempty array")
        _validate_sibling_keys(checks, "checks")
    except CheckError as error:
        raise CheckPreflightError(str(error), "valid checks", child_index=child_index) from error
    for index, check in enumerate(checks):
        try:
            _validate_node(check, owner_variable, f"checks[{index}]")
        except CheckError as error:
            raise CheckPreflightError(str(error), "valid checks", index, child_index) from error
    for index, check in enumerate(checks):
        try:
            _preflight_node(
                check,
                datafile,
                owner_variable,
                segments or {},
                f"checks[{index}]",
            )
        except CheckError as error:
            raise CheckPreflightError(str(error), "valid check references", index, child_index) from error


def evaluate_checks_result(
    sdk: Any,
    datafile: dict[str, Any],
    checks: Any,
    *,
    owner_variable: str | None,
    diagnostics: list[dict[str, Any]],
    child_index: int | None = None,
    segments: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    def comparison_error(
        message: str,
        expected: Any,
        actual: Any,
        *,
        check_index: int | None = None,
        check: Any = None,
        evaluated: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {}
        if check_index is not None:
            details["checkIndex"] = check_index
        if isinstance(check, dict) and "key" in check:
            details["checkKey"] = check["key"]
        if isinstance(check, dict) and "description" in check:
            details["checkDescription"] = check["description"]
        if evaluated is not None:
            details["evaluated"] = evaluated
        if child_index is not None:
            details["childIndex"] = child_index
        return {
            "type": "check",
            "message": message,
            "expected": expected,
            "actual": actual,
            "details": details,
        }

    def result(errors: list[dict[str, Any]], has_error: bool) -> dict[str, Any]:
        return {
            "passed": not errors,
            "error": has_error,
            "failures": [error["message"] for error in errors],
            "errors": errors,
        }

    try:
        preflight_checks(
            checks,
            datafile,
            owner_variable=owner_variable,
            segments=segments,
            child_index=child_index,
        )
    except CheckPreflightError as error:
        return result([comparison_error(
            str(error), error.expected, str(error),
            check_index=error.details.get("checkIndex"),
        )], True)
    except CheckError as error:
        return result([comparison_error(str(error), "valid checks", checks)], True)
    resolver = _Resolver(sdk, datafile, diagnostics, owner_variable, segments or {}, context or {})
    errors: list[dict[str, Any]] = []
    has_error = False
    for index, check in enumerate(checks):
        evaluated: list[dict[str, Any]] = []
        try:
            matched = _evaluate_node(check, resolver, [index], evaluated)
            if not matched:
                label = _check_label(f"checks[{index}]", check)
                if evaluated and all(leaf.get("reference", {}).get("segment") for leaf in evaluated):
                    evidence = ", ".join(
                        f'segment "{leaf["reference"]["segment"]}": expected matches={_json(leaf["expected"])}, '
                        f'actual matches={_json(leaf["actual"])}'
                        for leaf in evaluated
                    )
                    message = f"{label}: segment expectations failed ({evidence})"
                elif evaluated and all(leaf.get("reference", {}).get("feature") and leaf.get("property") for leaf in evaluated):
                    evidence = ", ".join(
                        f'expected {leaf["property"]}={_json(leaf["expected"])}, '
                        f'actual {leaf["property"]}={_json(leaf["actual"])}'
                        for leaf in evaluated
                    )
                    message = f'{label}: feature "{evaluated[0]["reference"]["feature"]}" expectations failed ({evidence})'
                elif evaluated and all(leaf.get("property") for leaf in evaluated):
                    evidence = ", ".join(
                        f'{"segment" if "segment" in leaf["reference"] else "feature"} '
                        f'"{leaf["reference"].get("segment") or leaf["reference"].get("feature")}": '
                        f'expected {leaf["property"]}={_json(leaf["expected"])}, '
                        f'actual {leaf["property"]}={_json(leaf["actual"])}'
                        for leaf in evaluated
                    )
                    message = f"{label}: expectations failed ({evidence})"
                else:
                    evidence = "; ".join(
                        f'{_reference_label(leaf["reference"], None)} {leaf["operator"]} failed '
                        f'(left={_json(leaf["actual"])}, right={_json(leaf["expected"])})'
                        for leaf in evaluated
                    )
                    message = f"{label}: {evidence}"
                errors.append(
                    comparison_error(
                        message,
                        True,
                        False,
                        check_index=index,
                        check=check,
                        evaluated=evaluated,
                    )
                )
        except CheckError as error:
            has_error = True
            errors.append(
                comparison_error(
                    str(error),
                    "successful comparison",
                    str(error),
                    check_index=index,
                    check=check,
                    evaluated=evaluated,
                )
            )
    return result(errors, has_error)


def evaluate_checks(
    sdk: Any,
    datafile: dict[str, Any],
    checks: Any,
    *,
    owner_variable: str | None,
    diagnostics: list[dict[str, Any]],
    child_index: int | None = None,
    segments: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> list[str]:
    return evaluate_checks_result(
        sdk,
        datafile,
        checks,
        owner_variable=owner_variable,
        diagnostics=diagnostics,
        child_index=child_index,
        segments=segments,
        context=context,
    )["failures"]
