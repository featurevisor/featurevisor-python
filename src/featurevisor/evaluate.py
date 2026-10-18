from __future__ import annotations

from enum import Enum
from typing import Any

from .bucketer import get_bucket_key, get_bucketed_number
from ._evaluation_session import EvaluationSession, RequirementInputError, dependency_of, refresh_evaluation_state


class EvaluationReason(str, Enum):
    FEATURE_NOT_FOUND = "feature_not_found"
    DISABLED = "disabled"
    CONDITIONS_UNMET = "conditions_unmet"
    NO_VARIATIONS = "no_variations"
    VARIATION_DISABLED = "variation_disabled"
    VARIABLE_NOT_FOUND = "variable_not_found"
    VARIABLE_DEFAULT = "variable_default"
    VARIABLE_DISABLED = "variable_disabled"
    VARIABLE_OVERRIDE_VARIATION = "variable_override_variation"
    VARIABLE_OVERRIDE_RULE = "variable_override_rule"
    VARIABLE_COMPOSED = "variable_composed"
    NO_MATCH = "no_match"
    FORCED = "forced"
    STICKY = "sticky"
    RULE = "rule"
    ALLOCATED = "allocated"
    ERROR = "error"

    def __str__(self) -> str:
        return self.value


def evaluate_with_modules(options: dict[str, Any]) -> dict[str, Any]:
    session: EvaluationSession = options.get("_evaluationSession") or EvaluationSession()
    options = {**options, "_evaluationSession": session}
    refresh_evaluation_state(options)
    cache_key = None
    try:
        with session.frame(options):
            session.retain(options)
            cache_key = session.key(options)
            cached = session.get(cache_key)
            if cached is not None:
                return cached
            modules_manager = options["modulesManager"]
            module_options = None
            current_options = options
            if modules_manager.modules:
                private = {key: value for key, value in options.items()
                           if key == "_evaluationSession" or key.startswith("__featurevisor_")}
                module_options = {key: value for key, value in options.items() if key not in private}
                module_options = modules_manager.run_before_evaluation_modules(module_options, session.retain)
                current_options = {**module_options, **private}
                session.retain(current_options)
            session.update(current_options)
            refresh_evaluation_state(current_options)
            evaluation = evaluate(current_options)
            if isinstance(evaluation.get("error"), RequirementInputError):
                raise evaluation["error"]
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
            # Evaluation callbacks may install a module after the initial
            # empty-list check. Its after hook must still see public options.
            if modules_manager.modules:
                if module_options is None:
                    module_options = {key: value for key, value in options.items()
                                      if key != "_evaluationSession" and not key.startswith("__featurevisor_")}
                evaluation = modules_manager.run_after_evaluation_modules(evaluation, module_options)
            if cache_key == session.key(options):
                snapshot = session.put(cache_key, evaluation)
                if snapshot is not None:
                    return snapshot
            return evaluation
    except Exception as exc:
        if isinstance(exc, RecursionError):
            exc = RequirementInputError("Host recursion limit exceeded during dependency evaluation")
        evaluation = {
            "type": options["type"],
            "featureKey": options["featureKey"],
            "variableKey": options.get("variableKey"),
            "reason": EvaluationReason.ERROR,
            "error": exc,
        }
        # A dependency failure fails the complete public call. Nested getters raise it
        # again, so only the call that started the session reports it.
        if options.get("dependencyOf") is None or not isinstance(exc, RequirementInputError):
            options["diagnostics"].error("Error during evaluation", evaluation)
        if cache_key == session.key(options):
            session.put(cache_key, evaluation)
        return evaluation


def _find_override_index(overrides: list[dict[str, Any]], context: dict[str, Any], datafile, options: dict[str, Any]) -> int:
    for index, override in enumerate(overrides):
        if "conditions" not in override or not datafile.conditions_are_matched(override["conditions"], options):
            continue
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
    sticky = options.get("stickyFeatures")
    modules_manager = options["modulesManager"]

    try:
        if type_ != "flag":
            flag = evaluate({**options, "type": "flag"})
            if isinstance(flag.get("error"), RequirementInputError):
                raise flag["error"]
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
                evaluation = {"type": type_, "featureKey": feature_key, "reason": EvaluationReason.STICKY, "stickyFeature": item, "enabled": item["enabled"]}
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

        force_result = datafile.get_matched_force(feature, context, options)
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

        if type_ == "flag" and "conditions" in feature and not datafile.conditions_are_matched(feature["conditions"], options):
            evaluation = {
                "type": type_, "featureKey": feature_key,
                "reason": EvaluationReason.CONDITIONS_UNMET,
                "conditions": feature["conditions"], "enabled": False,
            }
            diagnostics.debug("root conditions did not match", evaluation)
            return evaluation

        bucket_key = get_bucket_key(featureKey=feature_key, bucketBy=feature["bucketBy"], context=context, diagnostics=diagnostics)
        bucket_key = modules_manager.run_bucket_key_modules(
            {"featureKey": feature_key, "context": context, "bucketBy": feature["bucketBy"], "bucketKey": bucket_key}
        )
        bucket_value = get_bucketed_number(bucket_key)
        bucket_value = modules_manager.run_bucket_value_modules(
            {"featureKey": feature_key, "bucketKey": bucket_key, "context": context, "bucketValue": bucket_value}
        )

        matched_traffic = datafile.get_matched_traffic(feature["traffic"], context, options)
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
                    override_index = _find_override_index(overrides, context, datafile, options)
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
                            "variableOverrideKey": override.get("key"),
                            "variableOverridePath": override.get("keyPath"),
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
                    override_index = _find_override_index(overrides, context, datafile, options)
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
                            "variableOverrideKey": override.get("key"),
                            "variableOverridePath": override.get("keyPath"),
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
        if isinstance(exc, RecursionError):
            exc = RequirementInputError("Host recursion limit exceeded during dependency evaluation")
        evaluation = {"type": type_, "featureKey": feature_key, "variableKey": variable_key, "reason": EvaluationReason.ERROR, "error": exc}
        # evaluate_with_modules raises dependency failures again and reports them once.
        if not isinstance(exc, RequirementInputError):
            diagnostics.error("Error during evaluation", evaluation)
        return evaluation
