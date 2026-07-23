from __future__ import annotations

import json
from enum import Enum
from typing import Any

from .bucketer import get_bucket_key, get_bucketed_number


class EvaluationReason(str, Enum):
    FEATURE_NOT_FOUND = "feature_not_found"
    DISABLED = "disabled"
    REQUIRED = "required"
    OUT_OF_RANGE = "out_of_range"
    NO_VARIATIONS = "no_variations"
    VARIATION_DISABLED = "variation_disabled"
    VARIABLE_NOT_FOUND = "variable_not_found"
    VARIABLE_DEFAULT = "variable_default"
    VARIABLE_DISABLED = "variable_disabled"
    VARIABLE_OVERRIDE_VARIATION = "variable_override_variation"
    VARIABLE_OVERRIDE_RULE = "variable_override_rule"
    NO_MATCH = "no_match"
    FORCED = "forced"
    STICKY = "sticky"
    RULE = "rule"
    ALLOCATED = "allocated"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


def evaluate_with_modules(options: dict[str, Any]) -> dict[str, Any]:
    try:
        modules_manager = options["modulesManager"]
        current_options = modules_manager.run_before_modules(options)
        evaluation = evaluate(current_options)
        if (
            "defaultVariationValue" in current_options
            and evaluation["type"] == "variation"
            and "variationValue" not in evaluation
            and "variation" not in evaluation
        ):
            evaluation["variationValue"] = current_options["defaultVariationValue"]
        if (
            "defaultVariableValue" in current_options
            and evaluation["type"] == "variable"
            and "variableValue" not in evaluation
        ):
            evaluation["variableValue"] = current_options["defaultVariableValue"]
        evaluation = modules_manager.run_after_modules(evaluation, current_options)
        return evaluation
    except Exception as exc:
        evaluation = {
            "type": options["type"],
            "featureKey": options["featureKey"],
            "variableKey": options.get("variableKey"),
            "reason": EvaluationReason.ERROR,
            "error": exc,
        }
        options["diagnostics"].error("Error during evaluation", evaluation)
        return evaluation


def _find_override_index(overrides: list[dict[str, Any]], context: dict[str, Any], datafile) -> int:
    for index, override in enumerate(overrides):
        if override.get("conditions"):
            conditions = override["conditions"]
            if isinstance(conditions, str) and conditions != "*":
                conditions = json.loads(conditions)
            if datafile.all_conditions_are_matched(conditions, context):
                return index
        if override.get("segments") and datafile.all_segments_are_matched(
            datafile.parse_segments_if_stringified(override["segments"]), context
        ):
            return index
    return -1


def evaluate(options: dict[str, Any]) -> dict[str, Any]:
    evaluation: dict[str, Any]
    type_ = options["type"]
    feature_key = options["featureKey"]
    variable_key = options.get("variableKey")
    context = options["context"]
    diagnostics = options["diagnostics"]
    datafile = options["datafile"]
    sticky = options.get("sticky")
    modules_manager = options["modulesManager"]

    try:
        if type_ != "flag":
            flag = evaluate({**options, "type": "flag"})
            if flag.get("enabled") is False:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.DISABLED}
                feature = datafile.get_feature(feature_key)
                if type_ == "variable" and feature and variable_key and feature.get("variablesSchema", {}).get(variable_key):
                    variable_schema = feature["variablesSchema"][variable_key]
                    if "disabledValue" in variable_schema:
                        evaluation = {
                            "type": type_,
                            "featureKey": feature_key,
                            "reason": EvaluationReason.VARIABLE_DISABLED,
                            "variableKey": variable_key,
                            "variableValue": variable_schema["disabledValue"],
                            "variableSchema": variable_schema,
                            "enabled": False,
                        }
                    elif variable_schema.get("useDefaultWhenDisabled"):
                        evaluation = {
                            "type": type_,
                            "featureKey": feature_key,
                            "reason": EvaluationReason.VARIABLE_DEFAULT,
                            "variableKey": variable_key,
                            "variableValue": variable_schema.get("defaultValue"),
                            "variableSchema": variable_schema,
                            "enabled": False,
                        }
                if type_ == "variation" and feature and "disabledVariationValue" in feature:
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.VARIATION_DISABLED,
                        "variationValue": feature["disabledVariationValue"],
                        "enabled": False,
                    }
                diagnostics.debug("feature is disabled", evaluation)
                return evaluation

        if sticky and sticky.get(feature_key):
            item = sticky[feature_key]
            if type_ == "flag" and "enabled" in item:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.STICKY, "sticky": item, "enabled": item["enabled"]}
                diagnostics.debug("using sticky enabled", evaluation)
                return evaluation
            if type_ == "variation" and "variation" in item:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.STICKY, "variationValue": item["variation"]}
                diagnostics.debug("using sticky variation", evaluation)
                return evaluation
            if variable_key and variable_key in item.get("variables", {}):
                evaluation = {
                    "type": type_,
                    "featureKey": feature_key,
                    "reason": EvaluationReason.STICKY,
                    "variableKey": variable_key,
                    "variableValue": item["variables"][variable_key],
                }
                diagnostics.debug("using sticky variable", evaluation)
                return evaluation

        feature = datafile.get_feature(feature_key)
        if not feature:
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.FEATURE_NOT_FOUND}
            diagnostics.warn("feature not found", evaluation)
            return evaluation
        if type_ == "flag" and feature.get("deprecated"):
            diagnostics.warn("feature is deprecated", {"featureKey": feature_key})

        variable_schema = None
        if variable_key:
            variable_schema = feature.get("variablesSchema", {}).get(variable_key)
            if not variable_schema:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.VARIABLE_NOT_FOUND, "variableKey": variable_key}
                diagnostics.warn("variable schema not found", evaluation)
                return evaluation
            if variable_schema.get("deprecated"):
                diagnostics.warn("variable is deprecated", {"featureKey": feature_key, "variableKey": variable_key})

        if type_ == "variation" and not feature.get("variations"):
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.NO_VARIATIONS}
            diagnostics.warn("no variations", evaluation)
            return evaluation

        force_result = datafile.get_matched_force(feature, context)
        force = force_result.get("force")
        force_index = force_result.get("forceIndex")
        if force:
            if type_ == "flag" and "enabled" in force:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.FORCED, "forceIndex": force_index, "force": force, "enabled": force["enabled"]}
                diagnostics.debug("forced enabled found", evaluation)
                return evaluation
            if type_ == "variation" and force.get("variation") is not None and feature.get("variations"):
                variation = next((item for item in feature["variations"] if item["value"] == force["variation"]), None)
                if variation:
                    evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.FORCED, "forceIndex": force_index, "force": force, "variation": variation}
                    diagnostics.debug("forced variation found", evaluation)
                    return evaluation
            if variable_key and variable_key in force.get("variables", {}):
                evaluation = {
                    "type": type_,
                    "featureKey": feature_key,
                    "reason": EvaluationReason.FORCED,
                    "forceIndex": force_index,
                    "force": force,
                    "variableKey": variable_key,
                    "variableSchema": variable_schema,
                    "variableValue": force["variables"][variable_key],
                }
                diagnostics.debug("forced variable", evaluation)
                return evaluation

        if type_ == "flag" and feature.get("required"):
            required_enabled = True
            for required in feature["required"]:
                required_key = required if isinstance(required, str) else required["key"]
                required_variation = None if isinstance(required, str) else required.get("variation")
                required_eval = evaluate({**options, "type": "flag", "featureKey": required_key})
                if not required_eval.get("enabled"):
                    required_enabled = False
                    break
                if required_variation is not None:
                    required_variation_eval = evaluate({**options, "type": "variation", "featureKey": required_key})
                    value = required_variation_eval.get("variationValue")
                    if value is None and required_variation_eval.get("variation"):
                        value = required_variation_eval["variation"]["value"]
                    if value != required_variation:
                        required_enabled = False
                        break
            if not required_enabled:
                evaluation = {
                    "type": type_,
                    "featureKey": feature_key,
                    "reason": EvaluationReason.REQUIRED,
                    "required": feature["required"],
                    "enabled": False,
                }
                diagnostics.debug("required features not enabled", evaluation)
                return evaluation

        bucket_key = get_bucket_key(featureKey=feature_key, bucketBy=feature["bucketBy"], context=context, diagnostics=diagnostics)
        bucket_key = modules_manager.run_bucket_key_modules(
            {"featureKey": feature_key, "context": context, "bucketBy": feature["bucketBy"], "bucketKey": bucket_key}
        )
        bucket_value = get_bucketed_number(bucket_key)
        bucket_value = modules_manager.run_bucket_value_modules(
            {"featureKey": feature_key, "bucketKey": bucket_key, "context": context, "bucketValue": bucket_value}
        )

        matched_traffic = datafile.get_matched_traffic(feature["traffic"], context)
        matched_allocation = datafile.get_matched_allocation(matched_traffic, bucket_value) if type_ != "flag" and matched_traffic else None

        if matched_traffic:
            if matched_traffic["percentage"] == 0:
                evaluation = {
                    "type": type_,
                    "featureKey": feature_key,
                    "reason": EvaluationReason.RULE,
                    "bucketKey": bucket_key,
                    "bucketValue": bucket_value,
                    "ruleKey": matched_traffic["key"],
                    "traffic": matched_traffic,
                    "enabled": False,
                }
                diagnostics.debug("matched rule with 0 percentage", evaluation)
                return evaluation
            if type_ == "flag":
                if feature.get("ranges"):
                    matched_range = next((range_ for range_ in feature["ranges"] if range_[0] <= bucket_value < range_[1]), None)
                    if matched_range:
                        evaluation = {
                            "type": type_,
                            "featureKey": feature_key,
                            "reason": EvaluationReason.ALLOCATED,
                            "bucketKey": bucket_key,
                            "bucketValue": bucket_value,
                            "ruleKey": matched_traffic["key"],
                            "traffic": matched_traffic,
                            "enabled": matched_traffic.get("enabled", True),
                        }
                        diagnostics.debug("matched", evaluation)
                        return evaluation
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.OUT_OF_RANGE,
                        "bucketKey": bucket_key,
                        "bucketValue": bucket_value,
                        "enabled": False,
                    }
                    diagnostics.debug("not matched", evaluation)
                    return evaluation
                if "enabled" in matched_traffic:
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.RULE,
                        "bucketKey": bucket_key,
                        "bucketValue": bucket_value,
                        "ruleKey": matched_traffic["key"],
                        "traffic": matched_traffic,
                        "enabled": matched_traffic["enabled"],
                    }
                    diagnostics.debug("override from rule", evaluation)
                    return evaluation
                if bucket_value <= matched_traffic["percentage"]:
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.RULE,
                        "bucketKey": bucket_key,
                        "bucketValue": bucket_value,
                        "ruleKey": matched_traffic["key"],
                        "traffic": matched_traffic,
                        "enabled": True,
                    }
                    diagnostics.debug("matched traffic", evaluation)
                    return evaluation
            if type_ == "variation" and feature.get("variations"):
                if matched_traffic.get("variation") is not None:
                    variation = next((item for item in feature["variations"] if item["value"] == matched_traffic["variation"]), None)
                    if variation:
                        evaluation = {
                            "type": type_,
                            "featureKey": feature_key,
                            "reason": EvaluationReason.RULE,
                            "bucketKey": bucket_key,
                            "bucketValue": bucket_value,
                            "ruleKey": matched_traffic["key"],
                            "traffic": matched_traffic,
                            "variation": variation,
                        }
                        diagnostics.debug("override from rule", evaluation)
                        return evaluation
                if matched_allocation and matched_allocation.get("variation") is not None:
                    variation = next((item for item in feature["variations"] if item["value"] == matched_allocation["variation"]), None)
                    if variation:
                        evaluation = {
                            "type": type_,
                            "featureKey": feature_key,
                            "reason": EvaluationReason.ALLOCATED,
                            "bucketKey": bucket_key,
                            "bucketValue": bucket_value,
                            "ruleKey": matched_traffic["key"],
                            "traffic": matched_traffic,
                            "variation": variation,
                        }
                        diagnostics.debug("allocated variation", evaluation)
                        return evaluation

        if type_ == "variable" and variable_key:
            if matched_traffic:
                overrides = matched_traffic.get("variableOverrides", {}).get(variable_key)
                if overrides:
                    override_index = _find_override_index(overrides, context, datafile)
                    if override_index != -1:
                        override = overrides[override_index]
                        evaluation = {
                            "type": type_,
                            "featureKey": feature_key,
                            "reason": EvaluationReason.VARIABLE_OVERRIDE_RULE,
                            "bucketKey": bucket_key,
                            "bucketValue": bucket_value,
                            "ruleKey": matched_traffic["key"],
                            "traffic": matched_traffic,
                            "variableKey": variable_key,
                            "variableSchema": variable_schema,
                            "variableValue": override["value"],
                            "variableOverrideIndex": override_index,
                        }
                        diagnostics.debug("variable override from rule", evaluation)
                        return evaluation
                if variable_key in matched_traffic.get("variables", {}):
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.RULE,
                        "bucketKey": bucket_key,
                        "bucketValue": bucket_value,
                        "ruleKey": matched_traffic["key"],
                        "traffic": matched_traffic,
                        "variableKey": variable_key,
                        "variableSchema": variable_schema,
                        "variableValue": matched_traffic["variables"][variable_key],
                    }
                    diagnostics.debug("override from rule", evaluation)
                    return evaluation

            variation_value = None
            if force and force.get("variation") is not None:
                variation_value = force["variation"]
            elif matched_traffic and matched_traffic.get("variation") is not None:
                variation_value = matched_traffic["variation"]
            elif matched_allocation and matched_allocation.get("variation") is not None:
                variation_value = matched_allocation["variation"]
            if variation_value is not None and feature.get("variations"):
                variation = next((item for item in feature["variations"] if item["value"] == variation_value), None)
                if variation and variation.get("variableOverrides", {}).get(variable_key):
                    overrides = variation["variableOverrides"][variable_key]
                    override_index = _find_override_index(overrides, context, datafile)
                    if override_index != -1:
                        override = overrides[override_index]
                        evaluation = {
                            "type": type_,
                            "featureKey": feature_key,
                            "reason": EvaluationReason.VARIABLE_OVERRIDE_VARIATION,
                            "bucketKey": bucket_key,
                            "bucketValue": bucket_value,
                            "ruleKey": matched_traffic["key"] if matched_traffic else None,
                            "traffic": matched_traffic,
                            "variableKey": variable_key,
                            "variableSchema": variable_schema,
                            "variableValue": override["value"],
                            "variableOverrideIndex": override_index,
                        }
                        diagnostics.debug("variable override from variation", evaluation)
                        return evaluation
                if variation and variable_key in variation.get("variables", {}):
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.ALLOCATED,
                        "bucketKey": bucket_key,
                        "bucketValue": bucket_value,
                        "ruleKey": matched_traffic["key"] if matched_traffic else None,
                        "traffic": matched_traffic,
                        "variableKey": variable_key,
                        "variableSchema": variable_schema,
                        "variableValue": variation["variables"][variable_key],
                    }
                    diagnostics.debug("allocated variable", evaluation)
                    return evaluation

        if type_ == "variation":
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.NO_MATCH, "bucketKey": bucket_key, "bucketValue": bucket_value}
            diagnostics.debug("no matched variation", evaluation)
            return evaluation
        if type_ == "variable":
            if variable_schema:
                evaluation = {
                    "type": type_,
                    "featureKey": feature_key,
                    "reason": EvaluationReason.VARIABLE_DEFAULT,
                    "bucketKey": bucket_key,
                    "bucketValue": bucket_value,
                    "variableKey": variable_key,
                    "variableSchema": variable_schema,
                    "variableValue": variable_schema.get("defaultValue"),
                }
                diagnostics.debug("using default value", evaluation)
                return evaluation
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.VARIABLE_NOT_FOUND, "variableKey": variable_key, "bucketKey": bucket_key, "bucketValue": bucket_value}
            diagnostics.debug("variable not found", evaluation)
            return evaluation
        evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.NO_MATCH, "bucketKey": bucket_key, "bucketValue": bucket_value, "enabled": False}
        diagnostics.debug("nothing matched", evaluation)
        return evaluation
    except Exception as exc:
        evaluation = {"type": type_, "featureKey": feature_key, "variableKey": variable_key, "reason": EvaluationReason.ERROR, "error": exc}
        diagnostics.error("Error during evaluation", evaluation)
        return evaluation
