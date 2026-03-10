from __future__ import annotations

import re

SEMVER_REGEX = re.compile(
    r"(?i)^[v^~<>=]*?(\d+)(?:\.([x*]|\d+)(?:\.([x*]|\d+)(?:\.([x*]|\d+))?(?:-([\da-z\-]+(?:\.[\da-z\-]+)*))?(?:\+[\da-z\-]+(?:\.[\da-z\-]+)*)?)?)?$"
)


def validate_and_parse(version: str) -> list[str]:
    if not version:
        raise ValueError("invalid argument expected string")
    match = SEMVER_REGEX.match(version)
    if not match:
        raise ValueError(f"invalid argument not valid semver ('{version}' received)")
    return [group or "" for group in match.groups()]


def is_wildcard(value: str) -> bool:
    return value in {"*", "x", "X"}


def try_parse(value: str):
    try:
        return int(value)
    except ValueError:
        return value


def compare_strings(a: str, b: str) -> int:
    if is_wildcard(a) or is_wildcard(b):
        return 0
    parsed_a = try_parse(a)
    parsed_b = try_parse(b)
    if type(parsed_a) is not type(parsed_b):
        parsed_a = str(parsed_a)
        parsed_b = str(parsed_b)
    return (parsed_a > parsed_b) - (parsed_a < parsed_b)


def compare_segments(a: list[str], b: list[str]) -> int:
    max_len = max(len(a), len(b))
    for index in range(max_len):
        left = a[index] if index < len(a) else "0"
        right = b[index] if index < len(b) else "0"
        result = compare_strings(left, right)
        if result:
            return result
    return 0


def compare_versions(v1: str, v2: str) -> int:
    n1 = validate_and_parse(v1)
    n2 = validate_and_parse(v2)
    p1 = n1.pop() if n1 else ""
    p2 = n2.pop() if n2 else ""
    result = compare_segments(n1, n2)
    if result:
        return result
    if p1 and p2:
        return compare_segments(p1.split("."), p2.split("."))
    if p1 or p2:
        return -1 if p1 else 1
    return 0

