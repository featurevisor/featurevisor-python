from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Any, Callable

from .compare_versions import compare_versions
from .types import AttributeValue, Context

MISSING = object()
PORTABLE_TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?(?:Z|[+-][0-9]{2}:[0-9]{2})$")
MIN_PORTABLE_DATE = dt.datetime(1, 1, 1, tzinfo=dt.timezone.utc)
MAX_PORTABLE_DATE = dt.datetime(9999, 12, 31, 23, 59, 59, 999000, tzinfo=dt.timezone.utc)


def normalize_portable_regex(pattern: str) -> str:
    """Use JS ASCII digit and word shorthands without altering Unicode whitespace."""
    inside = {
        "d": "0-9", "D": r"\x00-\x2F\x3A-\U0010FFFF",
        "w": "A-Za-z0-9_", "W": r"\x00-\x2F\x3A-\x40\x5B-\x5E\x60\x7B-\U0010FFFF",
    }
    outside = {"d": "[0-9]", "D": "[^0-9]", "w": "(?-i:[A-Za-z0-9_])", "W": "(?-i:[^A-Za-z0-9_])"}
    result: list[str] = []
    in_class = False
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "\\" and index + 1 < len(pattern):
            escaped = pattern[index + 1]
            result.append((inside if in_class else outside).get(escaped, pattern[index:index + 2]))
            index += 2
            continue
        if char == "[" and not in_class:
            in_class = True
        elif char == "]" and in_class:
            in_class = False
        result.append(char)
        index += 1
    return "".join(result)


@dataclass(frozen=True, slots=True)
class _PreparedCondition:
    source: dict[str, Any]
    attribute: str | None
    path: tuple[str, ...] | None
    operator: Any
    value: Any
    regex_flags: Any


@dataclass(frozen=True, slots=True)
class _PreparedConditionGroup:
    operator: str
    items: tuple[Any, ...]


def prepare_conditions(conditions: Any) -> Any:
    """Prepare private parsed syntax without caching a context decision."""
    if isinstance(conditions, list):
        return _PreparedConditionGroup("and", tuple(prepare_conditions(item) for item in conditions))
    if not isinstance(conditions, dict):
        return conditions
    if "attribute" in conditions:
        attribute = conditions.get("attribute")
        return _PreparedCondition(
            source=conditions,
            attribute=attribute if isinstance(attribute, str) else None,
            path=tuple(attribute.split(".")) if isinstance(attribute, str) and "." in attribute else None,
            operator=conditions.get("operator"),
            value=conditions.get("value"),
            regex_flags=conditions.get("regexFlags", ""),
        )
    for operator in ("and", "or", "not"):
        items = conditions.get(operator)
        if isinstance(items, list):
            return _PreparedConditionGroup(operator, tuple(prepare_conditions(item) for item in items))
    return conditions


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
        try:
            native_offset = value.utcoffset()
        except (OverflowError, ValueError, TypeError):
            return None
        if native_offset is None or abs(native_offset) > dt.timedelta(hours=14):
            return None
        parsed = value
    elif isinstance(value, str) and PORTABLE_TIMESTAMP.fullmatch(value):
        offset = value[-6:] if value[-1] != "Z" else "+00:00"
        if offset != "+00:00" and (int(offset[1:3]) > 14 or int(offset[4:]) > 59 or (offset[1:3] == "14" and offset[4:] != "00")):
            return None
        try:
            parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    try:
        utc = parsed.astimezone(dt.timezone.utc)
        utc = utc.replace(microsecond=(utc.microsecond // 1000) * 1000)
    except (OverflowError, ValueError):
        return None
    return utc if MIN_PORTABLE_DATE <= utc <= MAX_PORTABLE_DATE else None


def condition_is_matched(condition: dict[str, Any], context: Context, get_regex: Callable[[str, str], re.Pattern[str]]) -> bool:
    attribute = condition.get("attribute")
    if not isinstance(attribute, str):
        return False
    return value_is_matched(get_value_from_context(context, attribute), condition, get_regex)


def prepared_condition_is_matched(
    condition: _PreparedCondition,
    context: Context,
    get_regex: Callable[[str, str], re.Pattern[str]],
) -> bool:
    if condition.attribute is None:
        return False
    context_value: Any
    if condition.path is None:
        context_value = context.get(condition.attribute, MISSING)
    else:
        context_value = context
        for part in condition.path:
            if not isinstance(context_value, dict):
                context_value = MISSING
                break
            context_value = context_value.get(part, MISSING)
            if context_value is MISSING:
                break
    return _value_is_matched(
        context_value,
        condition.operator,
        condition.value,
        condition.regex_flags,
        get_regex,
    )


def value_is_matched(
    context_value: Any,
    condition: dict[str, Any],
    get_regex: Callable[[str, str], re.Pattern[str]],
) -> bool:
    return _value_is_matched(
        context_value,
        condition.get("operator"),
        condition.get("value"),
        condition.get("regexFlags", ""),
        get_regex,
    )


def _value_is_matched(
    context_value: Any,
    operator: Any,
    value: Any,
    regex_flags: Any,
    get_regex: Callable[[str, str], re.Pattern[str]],
) -> bool:

    if operator == "isNull":
        return context_value is None
    if operator == "notNull":
        return context_value is not MISSING and context_value is not None
    if operator in {"empty", "notEmpty"}:
        if context_value is MISSING or context_value is None:
            return False
        if not isinstance(context_value, (str, list, dict)):
            return False
        return len(context_value) == 0 if operator == "empty" else len(context_value) > 0

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
    if operator in {"includesAny", "notIncludesAny"}:
        if not isinstance(context_value, list) or not isinstance(value, list):
            return False
        if not all(candidate is None or isinstance(candidate, (str, int, float, bool)) for candidate in value):
            return False
        matched = any(_strict_equal(candidate, actual) for candidate in value for actual in context_value)
        return matched if operator == "includesAny" else not matched
    if isinstance(context_value, list) and (isinstance(value, (str, int, float, bool)) or value is None):
        if operator == "includes":
            return any(_strict_equal(value, candidate) for candidate in context_value)
        if operator == "notIncludes":
            return not any(_strict_equal(value, candidate) for candidate in context_value)
    return False
