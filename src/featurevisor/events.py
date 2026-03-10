from __future__ import annotations

from .datafile_reader import DatafileReader


def get_params_for_sticky_set_event(previous_sticky_features: dict | None = None, new_sticky_features: dict | None = None, replace: bool = False) -> dict:
    previous_sticky_features = previous_sticky_features or {}
    new_sticky_features = new_sticky_features or {}
    all_keys = list(previous_sticky_features.keys()) + list(new_sticky_features.keys())
    features = []
    for key in all_keys:
        if key not in features:
            features.append(key)
    return {"features": features, "replaced": replace}


def get_params_for_datafile_set_event(previous_datafile_reader: DatafileReader, new_datafile_reader: DatafileReader) -> dict:
    previous_revision = previous_datafile_reader.get_revision()
    previous_feature_keys = previous_datafile_reader.get_feature_keys()
    new_revision = new_datafile_reader.get_revision()
    new_feature_keys = new_datafile_reader.get_feature_keys()
    removed_features = [key for key in previous_feature_keys if key not in new_feature_keys]
    changed_features = [
        key
        for key in previous_feature_keys
        if key in new_feature_keys
        and (previous_datafile_reader.get_feature(key) or {}).get("hash") != (new_datafile_reader.get_feature(key) or {}).get("hash")
    ]
    added_features = [key for key in new_feature_keys if key not in previous_feature_keys]
    features = []
    for key in removed_features + changed_features + added_features:
        if key not in features:
            features.append(key)
    return {
        "revision": new_revision,
        "previousRevision": previous_revision,
        "revisionChanged": previous_revision != new_revision,
        "features": features,
    }

