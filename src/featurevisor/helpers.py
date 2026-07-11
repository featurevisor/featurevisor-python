from __future__ import annotations

from typing import Any


def get_value_by_type(value: Any, field_type: str) -> Any:
    if value is None:
        return None
    if field_type == "string":
        return value if isinstance(value, str) else None
    if field_type == "integer":
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float) and value.is_integer():
            return int(value)
        return None
    if field_type == "double":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        result = float(value)
        return result if result not in {float("inf"), float("-inf")} and result == result else None
    if field_type == "boolean":
        return value if isinstance(value, bool) else None
    if field_type == "array":
        return value if isinstance(value, list) else None
    if field_type == "object":
        return value if isinstance(value, dict) else None
    return value
