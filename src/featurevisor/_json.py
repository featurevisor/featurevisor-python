from __future__ import annotations

import json
from typing import Any


def _reject_non_finite_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant: {value}")


def strict_json_loads(source: str) -> Any:
    """Decode standards compliant JSON without Python's NaN and Infinity extensions."""
    return json.loads(source, parse_constant=_reject_non_finite_constant)
