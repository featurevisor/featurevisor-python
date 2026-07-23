from __future__ import annotations

import math
from decimal import Decimal

from .conditions import MISSING, get_value_from_context
from .murmurhash import murmurhash_v3
from .types import Context

HASH_SEED = 1
MAX_HASH_VALUE = 2**32
MAX_BUCKETED_NUMBER = 100000
DEFAULT_BUCKET_KEY_SEPARATOR = "."


def _to_javascript_string(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        if math.isnan(value):
            return "NaN"
        if math.isinf(value):
            return "Infinity" if value > 0 else "-Infinity"
        if value == 0:
            return "0"

        text = repr(value)
        absolute = abs(value)
        if "e" in text.lower() and 1e-6 <= absolute < 1e21:
            return format(Decimal(text), "f")
        if "e" in text.lower():
            coefficient, exponent = text.lower().split("e")
            if coefficient.endswith(".0"):
                coefficient = coefficient[:-2]
            parsed_exponent = int(exponent)
            sign = "+" if parsed_exponent >= 0 else ""
            return f"{coefficient}e{sign}{parsed_exponent}"
        return text[:-2] if text.endswith(".0") else text
    if isinstance(value, list):
        return ",".join(_to_javascript_string(item) for item in value)
    if isinstance(value, dict):
        return "[object Object]"
    return str(value)


def get_bucketed_number(bucket_key: str) -> int:
    hash_value = murmurhash_v3(bucket_key, HASH_SEED)
    ratio = hash_value / MAX_HASH_VALUE
    return int(ratio * MAX_BUCKETED_NUMBER)


def get_bucket_key(*, featureKey: str, bucketBy, context: Context, diagnostics) -> str:
    if isinstance(bucketBy, str):
        bucket_type = "plain"
        attribute_keys = [bucketBy]
    elif isinstance(bucketBy, list):
        bucket_type = "and"
        attribute_keys = bucketBy
    elif isinstance(bucketBy, dict) and isinstance(bucketBy.get("or"), list):
        bucket_type = "or"
        attribute_keys = bucketBy["or"]
    else:
        diagnostics.error("invalid bucketBy", {"featureKey": featureKey, "bucketBy": bucketBy})
        raise ValueError("invalid bucketBy")

    bucket_key: list[object] = []
    for attribute_key in attribute_keys:
        attribute_value = get_value_from_context(context, attribute_key)
        if attribute_value is MISSING:
            continue
        if bucket_type in {"plain", "and"}:
            bucket_key.append(attribute_value)
        elif not bucket_key:
            bucket_key.append(attribute_value)
    bucket_key.append(featureKey)
    return DEFAULT_BUCKET_KEY_SEPARATOR.join(_to_javascript_string(part) for part in bucket_key)
