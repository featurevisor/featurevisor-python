from __future__ import annotations

from typing import Any


def get_value_by_type(value: Any, field_type: str) -> Any:
    if value is None:
        return None
    if field_type == "string":
        return value if isinstance(value, str) else None
    if field_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    if field_type == "double":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if field_type == "boolean":
        return value is True
    if field_type == "array":
        return value if isinstance(value, list) else None
    if field_type == "object":
        return value if isinstance(value, dict) else None
    return value

