from __future__ import annotations

import datetime as dt
import re
from typing import Any, Callable

from .compare_versions import compare_versions
from .types import AttributeValue, Context

MISSING = object()


def _strict_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    return False


def get_value_from_context(obj: Context, path: str) -> AttributeValue:
    if "." not in path:
        return obj.get(path, MISSING)
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict):
            return MISSING
        current = current.get(part, MISSING)
        if current is MISSING:
            return MISSING
    return current


def _to_datetime(value: Any) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value if value.tzinfo is not None else None
    if not isinstance(value, str):
        return None
    if isinstance(value, str) and value.endswith("Z"):
        value = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def condition_is_matched(condition: dict[str, Any], context: Context, get_regex: Callable[[str, str], re.Pattern[str]]) -> bool:
    attribute = condition.get("attribute")
    operator = condition.get("operator")
    value = condition.get("value")
    regex_flags = condition.get("regexFlags", "")
    if not isinstance(attribute, str):
        return False
    context_value = get_value_from_context(context, attribute)

    if operator == "equals":
        return _strict_equal(context_value, value)
    if operator == "notEquals":
        return not _strict_equal(context_value, value)
    if operator in {"before", "after"}:
        date_in_context = _to_datetime(context_value)
        date_in_condition = _to_datetime(value)
        if date_in_context is None or date_in_condition is None:
            return False
        return date_in_context < date_in_condition if operator == "before" else date_in_context > date_in_condition
    if isinstance(value, list) and (
        isinstance(context_value, str)
        or (isinstance(context_value, (int, float)) and not isinstance(context_value, bool))
        or context_value is None
    ):
        if operator == "in":
            return any(_strict_equal(context_value, candidate) for candidate in value)
        if operator == "notIn":
            return not any(_strict_equal(context_value, candidate) for candidate in value)
    if isinstance(context_value, str) and isinstance(value, str):
        if operator == "contains":
            return value in context_value
        if operator == "notContains":
            return value not in context_value
        if operator == "startsWith":
            return context_value.startswith(value)
        if operator == "endsWith":
            return context_value.endswith(value)
        if operator == "semverEquals":
            return compare_versions(context_value, value) == 0
        if operator == "semverNotEquals":
            return compare_versions(context_value, value) != 0
        if operator == "semverGreaterThan":
            return compare_versions(context_value, value) == 1
        if operator == "semverGreaterThanOrEquals":
            return compare_versions(context_value, value) >= 0
        if operator == "semverLessThan":
            return compare_versions(context_value, value) == -1
        if operator == "semverLessThanOrEquals":
            return compare_versions(context_value, value) <= 0
        if operator == "matches":
            return bool(get_regex(value, regex_flags).search(context_value))
        if operator == "notMatches":
            return not get_regex(value, regex_flags).search(context_value)
    if not isinstance(context_value, bool) and not isinstance(value, bool) and isinstance(context_value, (int, float)) and isinstance(value, (int, float)):
        if operator == "greaterThan":
            return context_value > value
        if operator == "greaterThanOrEquals":
            return context_value >= value
        if operator == "lessThan":
            return context_value < value
        if operator == "lessThanOrEquals":
            return context_value <= value
    if operator == "exists":
        return context_value is not MISSING
    if operator == "notExists":
        return context_value is MISSING
    if isinstance(context_value, list) and (isinstance(value, (str, int, float, bool)) or value is None):
        if operator == "includes":
            return any(_strict_equal(value, candidate) for candidate in context_value)
        if operator == "notIncludes":
            return not any(_strict_equal(value, candidate) for candidate in context_value)
    return False
