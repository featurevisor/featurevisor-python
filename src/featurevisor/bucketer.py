from __future__ import annotations

from .conditions import MISSING, get_value_from_context
from .murmurhash import murmurhash_v3
from .types import Context

HASH_SEED = 1
MAX_HASH_VALUE = 2**32
MAX_BUCKETED_NUMBER = 100000
DEFAULT_BUCKET_KEY_SEPARATOR = "."


def get_bucketed_number(bucket_key: str) -> int:
    hash_value = murmurhash_v3(bucket_key, HASH_SEED)
    ratio = hash_value / MAX_HASH_VALUE
    return int(ratio * MAX_BUCKETED_NUMBER)


def get_bucket_key(*, featureKey: str, bucketBy, context: Context, logger) -> str:
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
        logger.error("invalid bucketBy", {"featureKey": featureKey, "bucketBy": bucketBy})
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
    return DEFAULT_BUCKET_KEY_SEPARATOR.join(str(part) for part in bucket_key)
