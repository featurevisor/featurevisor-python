from __future__ import annotations

from typing import Any

from ._json import strict_json_loads
from .normalize import normalize_entity


def prepare_segment_datafile(datafile, segments):
    definitions = {**segments, **datafile.get("segments", {})}
    features = dict(datafile.get("features", {}))
    keys = {}
    for key in segments:
        helper = "__featurevisor_segment_test__" + key
        while helper in features:
            helper += "_"
        keys[key] = helper
        features[helper] = {"bucketBy": "__segment_test__", "conditions": key,
                            "traffic": [{"key": "segment", "conditions": "*", "percentage": 100000}]}
    return {**datafile, "features": features, "segments": definitions, "_segment_test_keys": keys}


def assert_segment_references(key, datafile, segments):
    """Inspect the full dependency closure without calling an SDK getter."""
    pending: list[tuple[str, Any, Any]] = [("segment", key, None)]
    seen = set()

    def expression(value):
        if isinstance(value, str):
            if value.lstrip()[:1] in ('{', '[', '"'):
                try:
                    expression(strict_json_loads(value))
                except ValueError:
                    pass
            elif value != "*":
                pending.append(("segment", value, None))
        elif isinstance(value, list):
            for child in value:
                expression(child)
        elif isinstance(value, dict):
            if "variable" in value:
                pending.append(("feature" if value.get("feature") is not None else "variable",
                                value.get("feature", value["variable"]), value["variable"] if value.get("feature") is not None else None))
            elif "feature" in value:
                pending.append(("feature", value["feature"], None))
            else:
                for group in ("and", "or", "not"):
                    if group in value:
                        expression(value[group])

    def selectors(entry):
        if isinstance(entry, dict):
            expression(entry.get("conditions"))

    cursor = 0
    while cursor < len(pending):
        identity = pending[cursor]
        cursor += 1
        if identity in seen:
            continue
        seen.add(identity)
        kind, name, variable = identity
        if kind == "segment":
            definition = segments.get(name, datafile.get("segments", {}).get(name))
        else:
            definition = (datafile.get("features" if kind == "feature" else "variables") or {}).get(name)
        if not isinstance(definition, dict):
            raise ValueError(f'{kind} "{name}" is absent from the selected datafile and test definitions')
        if variable is not None and variable not in definition.get("variablesSchema", {}):
            raise ValueError(f'variable "{variable}" is absent from feature "{name}" in the selected datafile')
        normalized, _ = normalize_entity(kind, name, definition)
        selectors(normalized)
        if kind == "feature":
            for entry in [*normalized.get("force", []), *normalized.get("traffic", [])]:
                selectors(entry)
            if variable is not None:
                for entry in [*normalized.get("traffic", []), *normalized.get("variations", [])]:
                    for override in (entry.get("variableOverrides") or {}).get(variable, []):
                        selectors(override)
        elif kind == "variable":
            for entry in [*normalized.get("overrides", []), *normalized.get("overrideProgram", [])]:
                selectors(entry)
        compose = definition.get("compose")
        if isinstance(compose, str):
            pending.append(("variable", compose, None))
        elif isinstance(compose, list):
            for reference in compose:
                if "variable" in reference:
                    expression(reference)
                elif "feature" in reference:
                    pending.append(("feature", reference["feature"], None))
