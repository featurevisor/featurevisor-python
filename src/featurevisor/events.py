from __future__ import annotations

import json

from .evaluation_data_provider import _InstanceEvaluationDataProvider


def get_params_for_sticky_features_set_event(previous_sticky_features: dict | None = None, new_sticky_features: dict | None = None, replace: bool = False) -> dict:
    previous_sticky_features = previous_sticky_features or {}
    new_sticky_features = new_sticky_features or {}
    all_keys = list(previous_sticky_features.keys()) + list(new_sticky_features.keys())
    features = []
    for key in all_keys:
        if key not in features:
            features.append(key)
    return {"features": features, "replaced": replace}


def get_params_for_sticky_variables_set_event(previous: dict | None = None, new: dict | None = None, replace: bool = False) -> dict:
    keys = list((previous or {}).keys()) + list((new or {}).keys())
    return {"variables": list(dict.fromkeys(keys)), "replaced": replace}


def get_params_for_datafile_set_event(previous_datafile: _InstanceEvaluationDataProvider, new_datafile: _InstanceEvaluationDataProvider, replace: bool = False) -> dict:
    previous_revision = previous_datafile.get_revision()
    previous_feature_keys = previous_datafile.get_feature_keys()
    new_revision = new_datafile.get_revision()
    new_feature_keys = new_datafile.get_feature_keys()
    removed_features = [key for key in previous_feature_keys if key not in new_feature_keys]
    changed_features = [
        key
        for key in previous_feature_keys
        if key in new_feature_keys
        and (
            not (previous_datafile.get_feature(key) or {}).get("hash")
            or not (new_datafile.get_feature(key) or {}).get("hash")
            or (previous_datafile.get_feature(key) or {}).get("hash") != (new_datafile.get_feature(key) or {}).get("hash")
        )
    ]
    added_features = [key for key in new_feature_keys if key not in previous_feature_keys]
    features = []
    for key in removed_features + changed_features + added_features:
        if key not in features:
            features.append(key)
    previous_variable_keys = previous_datafile.get_variable_keys()
    new_variable_keys = new_datafile.get_variable_keys()
    variables = [
        key for key in dict.fromkeys(previous_variable_keys + new_variable_keys)
        if _entity_changed(previous_datafile.get_global_variable(key), new_datafile.get_global_variable(key))
    ]
    changed_segments = [
        key for key in dict.fromkeys(previous_datafile.get_segment_keys() + new_datafile.get_segment_keys())
        if previous_datafile.get_segment(key) != new_datafile.get_segment(key)
    ]

    feature_keys = list(dict.fromkeys(previous_feature_keys + new_feature_keys))
    while True:
        before = len(features)
        for key in feature_keys:
            if key in features:
                continue
            candidates = [value for value in (previous_datafile.get_feature(key), new_datafile.get_feature(key)) if value]
            for feature in candidates:
                segments, required = _feature_dependencies(feature)
                if set(segments) & set(changed_segments) or set(required) & set(features):
                    features.append(key)
                    break
        if len(features) == before:
            break

    for key in dict.fromkeys(previous_variable_keys + new_variable_keys):
        if key in variables:
            continue
        candidates = [value for value in (previous_datafile.get_global_variable(key), new_datafile.get_global_variable(key)) if value]
        for variable in candidates:
            segments, required = _global_variable_dependencies(variable)
            if set(segments) & set(changed_segments) or set(required) & set(features):
                variables.append(key)
                break
    return {
        "revision": new_revision,
        "previousRevision": previous_revision,
        "revisionChanged": previous_revision != new_revision,
        "features": features,
        "variables": variables,
        "replaced": replace,
    }


def _entity_changed(previous, current) -> bool:
    if previous is None or current is None:
        return True
    return not previous.get("hash") or not current.get("hash") or previous.get("hash") != current.get("hash")


def _required_feature_keys(values) -> list[str]:
    values = values if isinstance(values, list) else ([values] if values is not None else [])
    return [value if isinstance(value, str) else value.get("feature") or value.get("key") for value in values]


def _segment_keys(value) -> list[str]:
    if value is None or value == "*":
        return []
    if isinstance(value, str):
        if value[:1] in {"{", "["}:
            try:
                return _segment_keys(json.loads(value))
            except ValueError:
                return []
        return [value]
    if isinstance(value, list):
        return list(dict.fromkeys(key for item in value for key in _segment_keys(item)))
    if isinstance(value, dict):
        return list(dict.fromkeys(key for operator in ("and", "or", "not") for key in _segment_keys(value.get(operator))))
    return []


def _override_dependencies(groups) -> tuple[list[str], list[str]]:
    overrides = [override for values in (groups or {}).values() for override in values]
    return (
        list(dict.fromkeys(key for override in overrides for key in _segment_keys(override.get("segments")))),
        list(dict.fromkeys(key for override in overrides for key in _required_feature_keys(override.get("requiredFeatures")))),
    )


def _feature_dependencies(feature) -> tuple[list[str], list[str]]:
    requirements = feature.get("requiredFeatures") if "requiredFeatures" in feature else feature.get("required")
    segments: list[str] = []
    required = _required_feature_keys(requirements)
    for traffic in feature.get("traffic", []):
        segments.extend(_segment_keys(traffic.get("segments")))
        nested_segments, nested_required = _override_dependencies(traffic.get("variableOverrides"))
        segments.extend(nested_segments)
        required.extend(nested_required)
    for force in feature.get("force", []):
        segments.extend(_segment_keys(force.get("segments")))
    for variation in feature.get("variations", []):
        nested_segments, nested_required = _override_dependencies(variation.get("variableOverrides"))
        segments.extend(nested_segments)
        required.extend(nested_required)
    return list(dict.fromkeys(segments)), list(dict.fromkeys(required))


def _global_variable_dependencies(variable) -> tuple[list[str], list[str]]:
    segments: list[str] = []
    required = _required_feature_keys(variable.get("requiredFeatures"))
    for override in variable.get("overrides", []):
        segments.extend(_segment_keys(override.get("segments")))
        required.extend(_required_feature_keys(override.get("requiredFeatures")))
    return list(dict.fromkeys(segments)), list(dict.fromkeys(required))
