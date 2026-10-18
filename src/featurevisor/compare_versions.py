from __future__ import annotations

import re

SEMVER_REGEX = re.compile(
    r"^[v^~<>=]*?([0-9]+)(?:\.([x*]|[0-9]+)(?:\.([x*]|[0-9]+)(?:\.([x*]|[0-9]+))?(?:-([0-9a-z\-]+(?:\.[0-9a-z\-]+)*))?(?:\+[0-9a-z\-]+(?:\.[0-9a-z\-]+)*)?)?)?$",
    re.IGNORECASE | re.ASCII,
)


def validate_and_parse(version: str) -> list[str]:
    if not isinstance(version, str) or not version:
        raise ValueError("invalid argument expected string")
    match = SEMVER_REGEX.fullmatch(version)
    if not match:
        raise ValueError(f"invalid argument not valid semver ('{version}' received)")
    return [group or "" for group in match.groups()]


def is_wildcard(value: str) -> bool:
    return value in {"*", "x", "X"}


def compare_strings(a: str, b: str, prerelease: bool = False) -> int:
    if not prerelease and (is_wildcard(a) or is_wildcard(b)):
        return 0
    an = bool(re.fullmatch(r"[0-9]+", a))
    bn = bool(re.fullmatch(r"[0-9]+", b))
    if an and bn:
        a, b = a.lstrip("0") or "0", b.lstrip("0") or "0"
        if len(a) != len(b):
            return 1 if len(a) > len(b) else -1
    elif prerelease and an != bn:
        return -1 if an else 1
    return (a > b) - (a < b)


def compare_segments(a: list[str], b: list[str], prerelease: bool = False) -> int:
    max_len = max(len(a), len(b))
    for index in range(max_len):
        if prerelease and (index == len(a) or index == len(b)):
            return 1 if len(a) > len(b) else -1
        left = (a[index] if index < len(a) else "0") or "0"
        right = (b[index] if index < len(b) else "0") or "0"
        result = compare_strings(left, right, prerelease)
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
        return compare_segments(p1.split("."), p2.split("."), True)
    if p1 or p2:
        return -1 if p1 else 1
    return 0
