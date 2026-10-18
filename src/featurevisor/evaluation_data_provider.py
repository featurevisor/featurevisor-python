from __future__ import annotations

import copy
import re
from threading import Lock
from typing import Any

from .conditions import (
    _PreparedCondition,
    _PreparedConditionGroup,
    condition_is_matched,
    normalize_portable_regex,
    prepare_conditions,
    prepared_condition_is_matched,
)
from .diagnostics import _EvaluationDiagnostics
from .dependencies import DatafileDependencyIndex
from ._json import strict_json_loads
from .normalize import InvalidConditions, normalize_entity
from .types import Context, DatafileContent, Feature, Force, Segment, Traffic


class _EntitySlot:
    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: Any = None


class _InstanceEvaluationDataProvider:
    def __init__(self, *, datafile: DatafileContent, diagnostics: _EvaluationDiagnostics) -> None:
        helpers: Any = datafile.get("_segment_test_keys", {})
        self.synthetic_keys = set(helpers.values())
        self.diagnostics = diagnostics
        self.schema_version = datafile["schemaVersion"]
        self.revision = datafile["revision"]
        self.featurevisor_version = datafile.get("featurevisorVersion")
        self.segments = datafile.get("segments", {})
        self.features = datafile.get("features", {})
        self.variables = datafile.get("variables", {})
        self.regex_cache: dict[str, re.Pattern[str]] = {}
        self.expression_cache: dict[str, Any] = {}
        self.prepared_expression_cache: dict[str, Any] = {}
        self.cached_expression_ids: set[int] = set()
        self.prepared_conditions: dict[int, Any] = {}
        self.entity_cache: dict[tuple[str, str], Any] = {}
        self._entity_lock = Lock()
        self._entity_slots: dict[tuple[str, str], _EntitySlot] = {}
        self._attribute_selectors: dict[int, tuple[Any, Any]] = {}
        self.conditions_are_matched: Any = None
        self.dependency_index: DatafileDependencyIndex | None = None

    def _entity(self, kind: str, key: str, definitions):
        identity = (kind, key)
        # Entries are published once and never replaced within this provider.
        # A lookup retains its value even if close clears the cache afterwards.
        cached = self.entity_cache.get(identity)
        if cached is not None:
            return cached
        errors = []
        with self._entity_lock:
            if identity in self.entity_cache:
                return self.entity_cache[identity]
            value = definitions.get(key)
            if value is None:
                return None
            slot = self._entity_slots.get(identity)
            if slot is None:
                slot = self._entity_slots[identity] = _EntitySlot()
            if slot.value is None:
                slot.value, errors = normalize_entity(kind, key, value)
            result = self.entity_cache[identity] = slot.value
        # A diagnostic may reenter evaluation or publish another datafile.
        for diagnostic in errors:
            self.diagnostics._report(diagnostic)
        return result

    def retain_entities(self, previous: _InstanceEvaluationDataProvider, incoming: dict) -> None:
        # Share individual cold slots, not the previous provider or its datafile.
        # All snapshots using those slots also share their synchronization lock.
        with previous._entity_lock:
            self._entity_lock = previous._entity_lock
            for kind, definitions in (("feature", previous.features), ("segment", previous.segments),
                                      ("variable", previous.variables)):
                for key in definitions:
                    if key in incoming[kind]:
                        continue
                    identity = (kind, key)
                    slot = previous._entity_slots.get(identity)
                    if slot is None:
                        slot = previous._entity_slots[identity] = _EntitySlot()
                    self._entity_slots[identity] = slot
            self.entity_cache = {identity: value for identity, value in previous.entity_cache.items()
                                 if identity[1] not in incoming[identity[0]]}

    def get_revision(self) -> str:
        return self.revision

    def get_schema_version(self) -> str:
        return self.schema_version

    def get_datafile(self) -> dict[str, Any]:
        datafile = {
            "schemaVersion": self.schema_version,
            "revision": self.revision,
            "featurevisorVersion": self.featurevisor_version,
            "segments": self.segments,
            "features": self.features,
            "variables": self.variables,
        }
        return {key: value for key, value in datafile.items() if value is not None}

    def get_segment(self, segment_key: str) -> Segment | None:
        return self._entity("segment", segment_key, self.segments)

    def get_attribute_selector(self, conditions: Any) -> Any:
        identity = id(conditions)
        cached = self._attribute_selectors.get(identity)
        if cached is not None:
            return cached[1]
        with self._entity_lock:
            cached = self._attribute_selectors.get(identity)
            if cached is None:
                prepared = self._prepare_attribute_selector(conditions)
                # Retain the syntax itself so its identity cannot be recycled.
                self._attribute_selectors[identity] = (conditions, prepared)
                return prepared
            return cached[1]

    @staticmethod
    def _prepare_attribute_selector(conditions: Any) -> Any:
        if isinstance(conditions, InvalidConditions):
            return None
        operator = "and"
        if isinstance(conditions, dict):
            if "variable" in conditions or "feature" in conditions:
                return None
            if "attribute" in conditions:
                return prepare_conditions(conditions)
            operator = next((name for name in ("and", "or", "not") if name in conditions), "and")
            conditions = conditions.get(operator, [])
        if not isinstance(conditions, list):
            return None
        # Unified empty groups are false, including AND. The general attribute
        # matcher also supports legacy syntax, whose empty AND is true.
        if not conditions:
            return _PreparedConditionGroup("or", ())
        items = tuple(_InstanceEvaluationDataProvider._prepare_attribute_selector(item) for item in conditions)
        if any(item is None for item in items):
            return None
        return _PreparedConditionGroup(operator, items)

    def get_feature_keys(self) -> list[str]:
        return list(self.features.keys())

    def get_feature(self, feature_key: str) -> Feature | None:
        return self._entity("feature", feature_key, self.features)

    def get_variable_keys(self, feature_key: str | None = None) -> list[str]:
        if feature_key is None:
            return list(self.variables.keys())
        feature = self.get_feature(feature_key)
        return list((feature or {}).get("variablesSchema", {}).keys())

    def get_global_variable(self, variable_key: str):
        return self._entity("variable", variable_key, self.variables)

    def get_segment_keys(self) -> list[str]:
        return list(self.segments.keys())

    def has_variations(self, feature_key: str) -> bool:
        feature = self.get_feature(feature_key)
        return bool(feature and feature.get("variations"))

    def get_regex(self, regex_string: str, regex_flags: str = "") -> re.Pattern[str]:
        key = f"{regex_string}-{regex_flags}"
        if key not in self.regex_cache:
            invalid_flags = set(regex_flags) - set("gimsuy")
            if invalid_flags:
                raise ValueError(f"invalid regular expression flags: {''.join(sorted(invalid_flags))}")
            flags = 0
            if "i" in regex_flags:
                flags |= re.IGNORECASE
            if "m" in regex_flags:
                flags |= re.MULTILINE
            if "s" in regex_flags:
                flags |= re.DOTALL
            self.regex_cache[key] = re.compile(normalize_portable_regex(regex_string), flags)
        return self.regex_cache[key]

    def all_conditions_are_matched(self, conditions: Any, context: Context) -> bool:
        if isinstance(conditions, str):
            return conditions == "*"
        if isinstance(conditions, _PreparedCondition):
            try:
                return prepared_condition_is_matched(conditions, context, self.get_regex)
            except Exception as exc:
                self.diagnostics.warn(str(exc), {
                    "code": "condition_match_error",
                    "originalError": exc,
                    "details": {"condition": copy.deepcopy(conditions.source), "context": context},
                })
                return False
        if isinstance(conditions, _PreparedConditionGroup):
            if conditions.operator == "and":
                return all(self.all_conditions_are_matched(item, context) for item in conditions.items)
            if conditions.operator == "or":
                return any(self.all_conditions_are_matched(item, context) for item in conditions.items)
            return bool(conditions.items) and not all(
                self.all_conditions_are_matched(item, context) for item in conditions.items
            )
        if isinstance(conditions, dict) and "attribute" in conditions:
            try:
                identity = id(conditions)
                if identity not in self.prepared_conditions:
                    self.prepared_conditions[identity] = prepare_conditions(conditions)
                return prepared_condition_is_matched(self.prepared_conditions[identity], context, self.get_regex)
            except Exception as exc:
                diagnostic_condition = copy.deepcopy(conditions)
                self.diagnostics.warn(str(exc), {
                    "code": "condition_match_error",
                    "originalError": exc,
                    "details": {"condition": diagnostic_condition, "context": context},
                })
                return False
        if isinstance(conditions, dict) and isinstance(conditions.get("and"), list):
            return all(self.all_conditions_are_matched(item, context) for item in conditions["and"])
        if isinstance(conditions, dict) and isinstance(conditions.get("or"), list):
            return any(self.all_conditions_are_matched(item, context) for item in conditions["or"])
        if isinstance(conditions, dict) and isinstance(conditions.get("not"), list):
            if not conditions["not"]:
                return False
            return not self.all_conditions_are_matched({"and": conditions["not"]}, context)
        if isinstance(conditions, list):
            return all(self.all_conditions_are_matched(item, context) for item in conditions)
        return False

    def segment_is_matched(self, segment: Segment, context: Context) -> bool:
        return self.all_conditions_are_matched(segment.get("conditions"), context)

    def all_segments_are_matched(self, group_segments: Any, context: Context) -> bool:
        if group_segments == "*":
            return True
        if isinstance(group_segments, str):
            segment = self.segments.get(group_segments)
            return self.all_conditions_are_matched(
                self.parse_conditions_if_stringified(segment["conditions"]), context
            ) if segment else False
        if isinstance(group_segments, dict) and isinstance(group_segments.get("and"), list):
            return all(self.all_segments_are_matched(item, context) for item in group_segments["and"])
        if isinstance(group_segments, dict) and isinstance(group_segments.get("or"), list):
            return any(self.all_segments_are_matched(item, context) for item in group_segments["or"])
        if isinstance(group_segments, dict) and isinstance(group_segments.get("not"), list):
            if not group_segments["not"]:
                return False
            return not self.all_segments_are_matched({"and": group_segments["not"]}, context)
        if isinstance(group_segments, list):
            return all(self.all_segments_are_matched(item, context) for item in group_segments)
        return False

    def get_matched_traffic(self, traffic: list[Traffic], context: Context, options=None) -> Traffic | None:
        for rule in traffic:
            if "conditions" in rule and self.conditions_are_matched(rule["conditions"], options):
                return rule
        return None

    def get_matched_allocation(self, traffic: Traffic, bucket_value: int):
        for allocation in traffic.get("allocation", []) or []:
            start, end = allocation["range"]
            if start <= bucket_value <= end:
                return allocation
        return None

    def get_matched_force(self, feature_or_key: str | Feature, context: Context, options=None) -> dict[str, Any]:
        feature = self.get_feature(feature_or_key) if isinstance(feature_or_key, str) else feature_or_key
        if not feature or not feature.get("force"):
            return {"force": None, "forceIndex": None}
        for index, current_force in enumerate(feature["force"]):
            if "conditions" in current_force and self.conditions_are_matched(current_force["conditions"], options):
                return {"force": current_force, "forceIndex": index}
        return {"force": None, "forceIndex": None}

    def parse_conditions_if_stringified(self, conditions: Any) -> Any:
        return self._parse_conditions_if_stringified(conditions, cache=True)

    def parse_conditions_if_stringified_strict(self, conditions: Any) -> Any:
        return self._parse_conditions_if_stringified(conditions, cache=True, raise_errors=True)

    def _parse_conditions_if_stringified(
        self,
        conditions: Any,
        *,
        cache: bool,
        raise_errors: bool = False,
    ) -> Any:
        if not isinstance(conditions, str) or conditions == "*":
            return conditions
        if cache and conditions in self.expression_cache:
            if conditions not in self.prepared_expression_cache:
                self.prepared_expression_cache[conditions] = prepare_conditions(self.expression_cache[conditions])
            return self.prepared_expression_cache[conditions]
        try:
            parsed = strict_json_loads(conditions)
            if cache:
                self._cache_expression(conditions, parsed)
                prepared = prepare_conditions(parsed)
                self.prepared_expression_cache[conditions] = prepared
                return prepared
            return parsed
        except Exception as exc:
            if raise_errors:
                raise
            self.diagnostics.error("Error parsing conditions", {
                "code": "conditions_parse_error",
                "originalError": exc,
                "details": {"conditions": conditions},
            })
            return conditions

    def parse_segments_if_stringified(self, segments: Any) -> Any:
        if isinstance(segments, str) and segments[:1] in {"{", "["}:
            if segments not in self.expression_cache:
                self._cache_expression(segments, strict_json_loads(segments))
            return self.expression_cache[segments]
        return segments

    def _cache_expression(self, source: str, parsed: Any) -> None:
        self.expression_cache[source] = parsed

        def record_ids(value: Any) -> None:
            if isinstance(value, dict):
                self.cached_expression_ids.add(id(value))
                for item in value.values():
                    record_ids(item)
            elif isinstance(value, list):
                self.cached_expression_ids.add(id(value))
                for item in value:
                    record_ids(item)

        record_ids(parsed)

    def close(self) -> None:
        self.dependency_index = None
        with self._entity_lock:
            self.entity_cache.clear()
            self._entity_slots.clear()
            self._attribute_selectors.clear()
        self.prepared_conditions.clear()
        self.expression_cache.clear()
        self.prepared_expression_cache.clear()
        self.cached_expression_ids.clear()
        self.regex_cache.clear()

    getRevision = get_revision
    getSchemaVersion = get_schema_version
    getDatafile = get_datafile
    getSegment = get_segment
    getFeatureKeys = get_feature_keys
    getFeature = get_feature
    getVariableKeys = get_variable_keys
    getGlobalVariable = get_global_variable
    getSegmentKeys = get_segment_keys
    hasVariations = has_variations
    getRegex = get_regex
    allConditionsAreMatched = all_conditions_are_matched
    segmentIsMatched = segment_is_matched
    allSegmentsAreMatched = all_segments_are_matched
    getMatchedTraffic = get_matched_traffic
    getMatchedAllocation = get_matched_allocation
    getMatchedForce = get_matched_force
    parseConditionsIfStringified = parse_conditions_if_stringified
    parseSegmentsIfStringified = parse_segments_if_stringified
