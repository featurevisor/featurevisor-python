from __future__ import annotations

from typing import Any

from ._json import strict_json_loads


class InvalidConditions(dict):
    """A selector failure which cannot become true through negation."""


class ConditionsParseFailure(Exception):
    pass


def legacy_expression(value: Any, features: bool = False) -> Any:
    if isinstance(value, InvalidConditions):
        return value
    if isinstance(value, str):
        return {"feature": value} if features else value
    if isinstance(value, list):
        return [legacy_expression(item, features) for item in value] if value else "*"
    if isinstance(value, dict):
        for operator in ("and", "or", "not"):
            if operator in value:
                items = value[operator]
                if operator == "and" and not items:
                    return "*"
                return {operator: [legacy_expression(item, features) for item in items]}
    return value


def normalize_entity(kind: str, key: str, value: dict[str, Any], legacy: bool = False):
    errors: list[dict[str, Any]] = []

    def parse(expression):
        if not isinstance(expression, str) or expression.lstrip()[:1] not in ('{', '[', '"'):
            return expression
        try:
            return strict_json_loads(expression)
        except RecursionError:
            # Host exhaustion fails the current call and must not cache the entity as invalid.
            raise
        except Exception as exc:
            errors.append({"level": "error", "code": "conditions_parse_error",
                           "message": "Error parsing conditions", "originalError": exc,
                           "details": {"conditions": expression, "entityType": kind, "entityKey": key}})
            return InvalidConditions({"or": []})

    def select(entry, old=False):
        result = {name: item for name, item in entry.items()
                  if name not in {"segments", "requiredFeatures", "requiredVariables", "required"}}
        parts = []
        if "conditions" in entry:
            expression = parse(entry["conditions"])
            parts.append(legacy_expression(expression) if old else expression)
        if "segments" in entry:
            parts.append(legacy_expression(parse(entry["segments"])))
        if "requiredFeatures" in entry:
            features = entry["requiredFeatures"]
            features = features if isinstance(features, list) else [features]
            if features:
                parts.append([legacy_expression(item, True) for item in features])
        if any(isinstance(part, InvalidConditions) for part in parts):
            result["conditions"] = InvalidConditions({"or": []})
        elif len(parts) == 1:
            result["conditions"] = parts[0]
        elif parts:
            result["conditions"] = [leaf for part in parts for leaf in
                                    (part if isinstance(part, list) and part else
                                     [{"and": []}] if part == [] else [part])]
        return result

    def with_overrides(entry):
        result = dict(entry)
        if "variableOverrides" in entry:
            result["variableOverrides"] = {
                name: [select(override, legacy) for override in overrides]
                for name, overrides in entry["variableOverrides"].items()}
        return result

    if kind == "segment":
        expression = parse(value.get("conditions"))
        result = {**value, "conditions": legacy_expression(expression) if legacy else expression}
    else:
        result = select(value)
        if kind == "feature":
            result["traffic"] = [with_overrides(select(rule)) for rule in value.get("traffic", [])]
            if "force" in value:
                result["force"] = [select(force, legacy) for force in value["force"]]
            if "variations" in value:
                result["variations"] = [with_overrides(variation) for variation in value["variations"]]
        else:
            for name in ("overrides", "overrideProgram"):
                if name in value:
                    result[name] = [select(step, legacy and name == "overrides") for step in value[name]]
    return result, errors


def normalize_datafile(datafile):
    result = dict(datafile)
    errors = []
    for name, kind in (("features", "feature"), ("segments", "segment"), ("variables", "variable")):
        if name not in datafile:
            continue
        result[name] = {}
        for key, value in (datafile[name] or {}).items():
            normalized, diagnostics = normalize_entity(kind, key, value, True)
            result[name][key] = normalized
            errors.extend(diagnostics)
    return result, errors
