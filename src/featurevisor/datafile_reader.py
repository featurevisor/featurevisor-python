from __future__ import annotations

import json
import re
from typing import Any

from .conditions import condition_is_matched
from .logger import Logger
from .types import Context, DatafileContent, Feature, Force, Segment, Traffic


class DatafileReader:
    def __init__(self, *, datafile: DatafileContent, logger: Logger) -> None:
        self.logger = logger
        self.schema_version = datafile["schemaVersion"]
        self.revision = datafile["revision"]
        self.segments = datafile.get("segments", {})
        self.features = datafile.get("features", {})
        self.regex_cache: dict[str, re.Pattern[str]] = {}

    def get_revision(self) -> str:
        return self.revision

    def get_schema_version(self) -> str:
        return self.schema_version

    def get_segment(self, segment_key: str) -> Segment | None:
        segment = self.segments.get(segment_key)
        if not segment:
            return None
        segment["conditions"] = self.parse_conditions_if_stringified(segment["conditions"])
        return segment

    def get_feature_keys(self) -> list[str]:
        return list(self.features.keys())

    def get_feature(self, feature_key: str) -> Feature | None:
        return self.features.get(feature_key)

    def get_variable_keys(self, feature_key: str) -> list[str]:
        feature = self.get_feature(feature_key)
        return list((feature or {}).get("variablesSchema", {}).keys())

    def has_variations(self, feature_key: str) -> bool:
        feature = self.get_feature(feature_key)
        return bool(feature and feature.get("variations"))

    def get_regex(self, regex_string: str, regex_flags: str = "") -> re.Pattern[str]:
        key = f"{regex_string}-{regex_flags}"
        if key not in self.regex_cache:
            flags = 0
            if "i" in regex_flags:
                flags |= re.IGNORECASE
            self.regex_cache[key] = re.compile(regex_string, flags)
        return self.regex_cache[key]

    def all_conditions_are_matched(self, conditions: Any, context: Context) -> bool:
        if isinstance(conditions, str):
            return conditions == "*"
        get_regex = lambda regex_string, regex_flags="": self.get_regex(regex_string, regex_flags)
        if isinstance(conditions, dict) and "attribute" in conditions:
            try:
                return condition_is_matched(conditions, context, get_regex)
            except Exception as exc:
                self.logger.warn(str(exc), {"error": exc, "details": {"condition": conditions, "context": context}})
                return False
        if isinstance(conditions, dict) and isinstance(conditions.get("and"), list):
            return all(self.all_conditions_are_matched(item, context) for item in conditions["and"])
        if isinstance(conditions, dict) and isinstance(conditions.get("or"), list):
            return any(self.all_conditions_are_matched(item, context) for item in conditions["or"])
        if isinstance(conditions, dict) and isinstance(conditions.get("not"), list):
            return all(not self.all_conditions_are_matched({"and": conditions["not"]}, context) for _ in conditions["not"])
        if isinstance(conditions, list):
            return all(self.all_conditions_are_matched(item, context) for item in conditions)
        return False

    def segment_is_matched(self, segment: Segment, context: Context) -> bool:
        return self.all_conditions_are_matched(segment.get("conditions"), context)

    def all_segments_are_matched(self, group_segments: Any, context: Context) -> bool:
        if group_segments == "*":
            return True
        if isinstance(group_segments, str):
            segment = self.get_segment(group_segments)
            return self.segment_is_matched(segment, context) if segment else False
        if isinstance(group_segments, dict) and isinstance(group_segments.get("and"), list):
            return all(self.all_segments_are_matched(item, context) for item in group_segments["and"])
        if isinstance(group_segments, dict) and isinstance(group_segments.get("or"), list):
            return any(self.all_segments_are_matched(item, context) for item in group_segments["or"])
        if isinstance(group_segments, dict) and isinstance(group_segments.get("not"), list):
            return all(not self.all_segments_are_matched(item, context) for item in group_segments["not"])
        if isinstance(group_segments, list):
            return all(self.all_segments_are_matched(item, context) for item in group_segments)
        return False

    def get_matched_traffic(self, traffic: list[Traffic], context: Context) -> Traffic | None:
        for rule in traffic:
            if self.all_segments_are_matched(self.parse_segments_if_stringified(rule["segments"]), context):
                return rule
        return None

    def get_matched_allocation(self, traffic: Traffic, bucket_value: int):
        for allocation in traffic.get("allocation", []) or []:
            start, end = allocation["range"]
            if start <= bucket_value <= end:
                return allocation
        return None

    def get_matched_force(self, feature_or_key: str | Feature, context: Context) -> dict[str, Any]:
        feature = self.get_feature(feature_or_key) if isinstance(feature_or_key, str) else feature_or_key
        if not feature or not feature.get("force"):
            return {"force": None, "forceIndex": None}
        for index, current_force in enumerate(feature["force"]):
            if current_force.get("conditions") and self.all_conditions_are_matched(
                self.parse_conditions_if_stringified(current_force["conditions"]), context
            ):
                return {"force": current_force, "forceIndex": index}
            if current_force.get("segments") and self.all_segments_are_matched(
                self.parse_segments_if_stringified(current_force["segments"]), context
            ):
                return {"force": current_force, "forceIndex": index}
        return {"force": None, "forceIndex": None}

    def parse_conditions_if_stringified(self, conditions: Any) -> Any:
        if not isinstance(conditions, str) or conditions == "*":
            return conditions
        try:
            return json.loads(conditions)
        except Exception as exc:
            self.logger.error("Error parsing conditions", {"error": exc, "details": {"conditions": conditions}})
            return conditions

    def parse_segments_if_stringified(self, segments: Any) -> Any:
        if isinstance(segments, str) and segments[:1] in {"{", "["}:
            return json.loads(segments)
        return segments

    getRevision = get_revision
    getSchemaVersion = get_schema_version
    getSegment = get_segment
    getFeatureKeys = get_feature_keys
    getFeature = get_feature
    getVariableKeys = get_variable_keys
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

