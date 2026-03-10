from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from .bucketer import get_bucket_key, get_bucketed_number


class EvaluationReason(StrEnum):
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


def evaluate_with_hooks(options: dict[str, Any]) -> dict[str, Any]:
    try:
        hooks_manager = options["hooksManager"]
        hooks = hooks_manager.get_all()
        current_options = options
        for hook in hooks:
            if hook.get("before"):
                current_options = hook["before"](current_options)
        evaluation = evaluate(current_options)
        if (
            current_options.get("defaultVariationValue") is not None
            and evaluation["type"] == "variation"
            and evaluation.get("variationValue") is None
            and evaluation.get("variation") is None
        ):
            evaluation["variationValue"] = current_options["defaultVariationValue"]
        if (
            current_options.get("defaultVariableValue") is not None
            and evaluation["type"] == "variable"
            and evaluation.get("variableValue") is None
        ):
            evaluation["variableValue"] = current_options["defaultVariableValue"]
        for hook in hooks:
            if hook.get("after"):
                evaluation = hook["after"](evaluation, current_options)
        return evaluation
    except Exception as exc:
        evaluation = {
            "type": options["type"],
            "featureKey": options["featureKey"],
            "variableKey": options.get("variableKey"),
            "reason": EvaluationReason.ERROR,
            "error": exc,
        }
        options["logger"].error("error during evaluation", evaluation)
        return evaluation


def _find_override_index(overrides: list[dict[str, Any]], context: dict[str, Any], datafile_reader) -> int:
    for index, override in enumerate(overrides):
        if override.get("conditions"):
            conditions = override["conditions"]
            if isinstance(conditions, str) and conditions != "*":
                conditions = json.loads(conditions)
            if datafile_reader.all_conditions_are_matched(conditions, context):
                return index
        if override.get("segments") and datafile_reader.all_segments_are_matched(
            datafile_reader.parse_segments_if_stringified(override["segments"]), context
        ):
            return index
    return -1


def evaluate(options: dict[str, Any]) -> dict[str, Any]:
    evaluation: dict[str, Any]
    type_ = options["type"]
    feature_key = options["featureKey"]
    variable_key = options.get("variableKey")
    context = options["context"]
    logger = options["logger"]
    datafile_reader = options["datafileReader"]
    sticky = options.get("sticky")
    hooks = options["hooksManager"].get_all()

    try:
        if type_ != "flag":
            flag = evaluate({**options, "type": "flag"})
            if flag.get("enabled") is False:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.DISABLED}
                feature = datafile_reader.get_feature(feature_key)
                if type_ == "variable" and feature and variable_key and feature.get("variablesSchema", {}).get(variable_key):
                    variable_schema = feature["variablesSchema"][variable_key]
                    if variable_schema.get("disabledValue") is not None:
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
                if type_ == "variation" and feature and feature.get("disabledVariationValue") is not None:
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.VARIATION_DISABLED,
                        "variationValue": feature["disabledVariationValue"],
                        "enabled": False,
                    }
                logger.debug("feature is disabled", evaluation)
                return evaluation

        if sticky and sticky.get(feature_key):
            item = sticky[feature_key]
            if type_ == "flag" and item.get("enabled") is not None:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.STICKY, "sticky": item, "enabled": item["enabled"]}
                logger.debug("using sticky enabled", evaluation)
                return evaluation
            if type_ == "variation" and item.get("variation") is not None:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.STICKY, "variationValue": item["variation"]}
                logger.debug("using sticky variation", evaluation)
                return evaluation
            if variable_key and item.get("variables", {}).get(variable_key) is not None:
                evaluation = {
                    "type": type_,
                    "featureKey": feature_key,
                    "reason": EvaluationReason.STICKY,
                    "variableKey": variable_key,
                    "variableValue": item["variables"][variable_key],
                }
                logger.debug("using sticky variable", evaluation)
                return evaluation

        feature = datafile_reader.get_feature(feature_key)
        if not feature:
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.FEATURE_NOT_FOUND}
            logger.warn("feature not found", evaluation)
            return evaluation
        if type_ == "flag" and feature.get("deprecated"):
            logger.warn("feature is deprecated", {"featureKey": feature_key})

        variable_schema = None
        if variable_key:
            variable_schema = feature.get("variablesSchema", {}).get(variable_key)
            if not variable_schema:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.VARIABLE_NOT_FOUND, "variableKey": variable_key}
                logger.warn("variable schema not found", evaluation)
                return evaluation
            if variable_schema.get("deprecated"):
                logger.warn("variable is deprecated", {"featureKey": feature_key, "variableKey": variable_key})

        if type_ == "variation" and not feature.get("variations"):
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.NO_VARIATIONS}
            logger.warn("no variations", evaluation)
            return evaluation

        force_result = datafile_reader.get_matched_force(feature, context)
        force = force_result.get("force")
        force_index = force_result.get("forceIndex")
        if force:
            if type_ == "flag" and force.get("enabled") is not None:
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.FORCED, "forceIndex": force_index, "force": force, "enabled": force["enabled"]}
                logger.debug("forced enabled found", evaluation)
                return evaluation
            if type_ == "variation" and force.get("variation") is not None and feature.get("variations"):
                variation = next((item for item in feature["variations"] if item["value"] == force["variation"]), None)
                if variation:
                    evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.FORCED, "forceIndex": force_index, "force": force, "variation": variation}
                    logger.debug("forced variation found", evaluation)
                    return evaluation
            if variable_key and force.get("variables", {}).get(variable_key) is not None:
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
                logger.debug("forced variable", evaluation)
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
                logger.debug("required features not enabled", evaluation)
                return evaluation

        bucket_key = get_bucket_key(featureKey=feature_key, bucketBy=feature["bucketBy"], context=context, logger=logger)
        for hook in hooks:
            if hook.get("bucketKey"):
                bucket_key = hook["bucketKey"]({"featureKey": feature_key, "context": context, "bucketBy": feature["bucketBy"], "bucketKey": bucket_key})
        bucket_value = get_bucketed_number(bucket_key)
        for hook in hooks:
            if hook.get("bucketValue"):
                bucket_value = hook["bucketValue"]({"featureKey": feature_key, "bucketKey": bucket_key, "context": context, "bucketValue": bucket_value})

        matched_traffic = datafile_reader.get_matched_traffic(feature["traffic"], context)
        matched_allocation = datafile_reader.get_matched_allocation(matched_traffic, bucket_value) if type_ != "flag" and matched_traffic else None

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
                logger.debug("matched rule with 0 percentage", evaluation)
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
                        logger.debug("matched", evaluation)
                        return evaluation
                    evaluation = {
                        "type": type_,
                        "featureKey": feature_key,
                        "reason": EvaluationReason.OUT_OF_RANGE,
                        "bucketKey": bucket_key,
                        "bucketValue": bucket_value,
                        "enabled": False,
                    }
                    logger.debug("not matched", evaluation)
                    return evaluation
                if matched_traffic.get("enabled") is not None:
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
                    logger.debug("override from rule", evaluation)
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
                    logger.debug("matched traffic", evaluation)
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
                        logger.debug("override from rule", evaluation)
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
                        logger.debug("allocated variation", evaluation)
                        return evaluation

        if type_ == "variable" and variable_key:
            if matched_traffic:
                overrides = matched_traffic.get("variableOverrides", {}).get(variable_key)
                if overrides:
                    override_index = _find_override_index(overrides, context, datafile_reader)
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
                        logger.debug("variable override from rule", evaluation)
                        return evaluation
                if matched_traffic.get("variables", {}).get(variable_key) is not None:
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
                    logger.debug("override from rule", evaluation)
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
                    override_index = _find_override_index(overrides, context, datafile_reader)
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
                        logger.debug("variable override from variation", evaluation)
                        return evaluation
                if variation and variation.get("variables", {}).get(variable_key) is not None:
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
                    logger.debug("allocated variable", evaluation)
                    return evaluation

        if type_ == "variation":
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.NO_MATCH, "bucketKey": bucket_key, "bucketValue": bucket_value}
            logger.debug("no matched variation", evaluation)
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
                logger.debug("using default value", evaluation)
                return evaluation
            evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.VARIABLE_NOT_FOUND, "variableKey": variable_key, "bucketKey": bucket_key, "bucketValue": bucket_value}
            logger.debug("variable not found", evaluation)
            return evaluation
        evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.NO_MATCH, "bucketKey": bucket_key, "bucketValue": bucket_value, "enabled": False}
        logger.debug("nothing matched", evaluation)
        return evaluation
    except Exception as exc:
        evaluation = {"type": type_, "featureKey": feature_key, "variableKey": variable_key, "reason": EvaluationReason.ERROR, "error": exc}
        logger.error("error", evaluation)
        return evaluation

