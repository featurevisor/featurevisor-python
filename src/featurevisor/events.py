from __future__ import annotations

from .dependencies import add_datafile_dependency_changes, create_datafile_dependency_index
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


def get_params_for_datafile_set_event(previous_datafile: _InstanceEvaluationDataProvider, new_datafile: _InstanceEvaluationDataProvider, replace: bool = False, incoming: dict | None = None) -> dict:
    previous_revision = previous_datafile.get_revision()
    previous_feature_keys = previous_datafile.get_feature_keys()
    new_revision = new_datafile.get_revision()
    new_feature_keys = new_datafile.get_feature_keys()
    # An empty instance already announces every new feature and variable.
    # The first actual update pays the initial graph extraction.
    if not previous_feature_keys and not previous_datafile.variables and not previous_datafile.segments:
        return {
            "revision": new_revision,
            "previousRevision": previous_revision,
            "revisionChanged": previous_revision != new_revision,
            "features": new_feature_keys,
            "variables": new_datafile.get_variable_keys(),
            "replaced": replace,
        }
    removed_features = [key for key in previous_feature_keys if key not in new_datafile.features]
    changed_features = [
        key
        for key in previous_feature_keys
        if key in new_datafile.features
        and (
            not (previous_datafile.features.get(key) or {}).get("hash")
            or not (new_datafile.features.get(key) or {}).get("hash")
            or (previous_datafile.features.get(key) or {}).get("hash") != (new_datafile.features.get(key) or {}).get("hash")
        )
    ]
    added_features = [key for key in new_feature_keys if key not in previous_datafile.features]
    features = list(dict.fromkeys(removed_features + changed_features + added_features))
    previous_variable_keys = previous_datafile.get_variable_keys()
    new_variable_keys = new_datafile.get_variable_keys()
    variables = [
        key for key in dict.fromkeys(previous_variable_keys + new_variable_keys)
        if _entity_changed(previous_datafile.variables.get(key), new_datafile.variables.get(key))
    ]
    segment_keys = dict.fromkeys(previous_datafile.get_segment_keys() + new_datafile.get_segment_keys())
    changed_segments = [
        key for key in segment_keys
        if previous_datafile.segments.get(key) is not new_datafile.segments.get(key)
        and previous_datafile.segments.get(key) != new_datafile.segments.get(key)
    ]

    index = previous_datafile.dependency_index
    if index is None:
        index = create_datafile_dependency_index(previous_datafile.get_datafile())
    next_datafile = new_datafile.get_datafile()
    if replace or incoming is None:
        incoming = {name: dict.fromkeys(list(getattr(previous_datafile, name)) + list(next_datafile[name]))
                    for name in ("segments", "features", "variables")}
    # Preserve graph generation order rather than inventing union paths.
    add_datafile_dependency_changes(features, variables, changed_segments, index)
    index.update(next_datafile, incoming, previous_datafile.get_datafile())
    if replace:
        index.reorder(next_datafile)
    add_datafile_dependency_changes(features, variables, changed_segments, index)
    new_datafile.dependency_index = index
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
