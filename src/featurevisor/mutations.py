from __future__ import annotations

import math
from typing import Any, cast


_DANGEROUS_KEYS = {"__proto__", "prototype", "constructor"}
_MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, bool)) or (
        isinstance(value, (int, float)) and not isinstance(value, bool)
    )


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left == right
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, str) and isinstance(right, str):
        return left == right
    return left is right


def _array_index(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value < 0 or value > _MAX_SAFE_INTEGER or value != math.trunc(value):
        return None
    return int(value)


def _selector(part: Any) -> dict[str, Any] | None:
    if not isinstance(part, dict) or not isinstance(part.get("select"), dict):
        return None
    select = part["select"]
    if not isinstance(select.get("property"), str) or not isinstance(select.get("values"), list):
        return None
    if not all(_is_primitive(value) for value in select["values"]):
        return None
    return select


def _validate_operation(operation: Any) -> None:
    if not isinstance(operation, dict) or "op" not in operation or "path" not in operation:
        raise ValueError("Invalid variable mutation operation")
    op = operation["op"]
    if op not in {"set", "append", "prepend", "appendUnique", "removeValue", "before", "after", "remove"}:
        raise ValueError("Invalid variable mutation operation")
    if op != "remove" and ("value" not in operation or operation["value"] is _UNDEFINED):
        raise ValueError("Variable mutation operation requires a value")
    if op in {"appendUnique", "removeValue"}:
        value = operation.get("value")
        if not _is_primitive(value) or (
            isinstance(value, float) and not math.isfinite(value)
        ):
            raise ValueError("Variable mutation operation requires a primitive value")
    path = operation["path"]
    if not isinstance(path, list):
        raise ValueError("Invalid variable mutation path")
    for part in path:
        if isinstance(part, str):
            key = part
        elif _array_index(part) is not None:
            key = None
        else:
            select = _selector(part)
            if select is None:
                raise ValueError("Invalid variable mutation path")
            key = select["property"]
        if key in _DANGEROUS_KEYS:
            raise ValueError("Unsafe variable mutation key")


class _Undefined:
    pass


_UNDEFINED = _Undefined()


def _selected_index(value: list[Any], select: dict[str, Any]) -> int:
    for index, candidate in enumerate(value):
        if not isinstance(candidate, dict) or select["property"] not in candidate:
            continue
        property_value = candidate[select["property"]]
        if _is_primitive(property_value) and any(
            _same(property_value, expected) for expected in select["values"]
        ):
            return index
    return -1


def _path_key(container: Any, part: Any) -> str | int:
    if isinstance(part, str):
        if not isinstance(container, dict):
            raise ValueError("Variable mutation expected an object")
        return part
    if not isinstance(container, list):
        raise ValueError("Variable mutation expected an array")
    index = _array_index(part)
    if index is not None:
        return index
    select = _selector(part)
    if select is None:
        raise ValueError("Invalid variable mutation path")
    return _selected_index(container, select)


def _exists(container: Any, key: str | int) -> bool:
    return key in container if isinstance(key, str) else 0 <= key < len(container)


def _child(container: Any, key: str | int) -> Any:
    return container[key]


def _replace(container: Any, key: str | int, value: Any) -> Any:
    if isinstance(container, list):
        index = cast(int, key)
        result_list = list(container)
        if index == len(result_list):
            result_list.append(value)
        else:
            result_list[index] = value
        return result_list
    result_object = dict(container)
    result_object[cast(str, key)] = value
    return result_object


def _update_array(array: list[Any], op: str, item: Any) -> list[Any]:
    if op == "removeValue":
        if not any(_same(value, item) for value in array):
            return array
        return [value for value in array if not _same(value, item)]
    if op == "appendUnique" and any(_same(value, item) for value in array):
        return array
    return [item, *array] if op == "prepend" else [*array, item]


def _update_final(container: Any, part: Any, operation: dict[str, Any]) -> Any:
    if container is None:
        return container
    op = operation["op"]
    if op in {"before", "after"} and isinstance(part, str):
        raise ValueError("Variable mutation before/after requires an array element path")
    key = _path_key(container, part)
    found = _exists(container, key)

    if op == "remove":
        if not found:
            return container
        result = list(container) if isinstance(container, list) else dict(container)
        if isinstance(result, list):
            result.pop(cast(int, key))
        else:
            del result[key]
        return result

    item = operation["value"]
    if op == "set":
        if isinstance(key, int) and (key < 0 or key > len(container)):
            return container
        if found and _same(_child(container, key), item):
            return container
        return _replace(container, key, item)
    if op in {"before", "after"}:
        if not found:
            return container
        result = list(container)
        result.insert(cast(int, key) + (1 if op == "after" else 0), item)
        return result

    current = _child(container, key) if found else None
    if current is None and isinstance(key, int):
        return container
    if current is None and op == "removeValue":
        return container
    if current is not None and not isinstance(current, list):
        raise ValueError("Variable mutation expected an array")
    array = [] if current is None else current
    result = _update_array(array, op, item)
    return container if result is current else _replace(container, key, result)


def _update_at_path(value: Any, operation: dict[str, Any], offset: int) -> Any:
    path = operation["path"]
    if offset == len(path):
        if operation["op"] == "set":
            replacement = operation["value"]
            return value if _same(value, replacement) else replacement
        if operation["op"] == "remove":
            raise ValueError("Cannot remove the root variable value")
        if operation["op"] in {"before", "after"}:
            raise ValueError("Variable mutation before/after requires an array element path")
        if value is None:
            return value
        if not isinstance(value, list):
            raise ValueError("Variable mutation expected an array")
        return _update_array(value, operation["op"], operation["value"])

    if offset == len(path) - 1:
        return _update_final(value, path[offset], operation)
    if value is None:
        return value
    key = _path_key(value, path[offset])
    if not _exists(value, key):
        return value
    current = _child(value, key)
    if current is None:
        return value
    result = _update_at_path(current, operation, offset + 1)
    return value if result is current else _replace(value, key, result)


def apply_variable_operations(value: Any, operations: list[dict[str, Any]]) -> Any:
    """Apply compiled variable operations without mutating the input value."""
    if not isinstance(operations, list):
        raise ValueError("Invalid variable mutation operations")
    current = value
    for operation in operations:
        _validate_operation(operation)
        current = _update_at_path(current, operation, 0)
    return current
