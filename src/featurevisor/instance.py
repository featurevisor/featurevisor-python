from __future__ import annotations

import json
import uuid
from typing import Any, cast

from .child import FeaturevisorChildInstance
from .evaluation_data_provider import _InstanceEvaluationDataProvider
from .emitter import Emitter
from .evaluate import evaluate_with_modules
from .events import get_params_for_datafile_set_event, get_params_for_sticky_set_event, get_params_for_sticky_variables_set_event
from .helpers import get_value_by_type
from .diagnostics import DEFAULT_LOG_LEVEL, LOG_LEVELS, _EvaluationDiagnostics, should_report, write_diagnostic_to_console
from .modules import FeaturevisorModule, ModulesManager
from .types import DatafileContent, LogLevel

empty_datafile: DatafileContent = {"schemaVersion": "2", "revision": "unknown", "segments": {}, "features": {}, "variables": {}}


class Featurevisor:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        options = options or {}
        self.context = options.get("context") or {}
        self.log_level = cast(LogLevel, options.get("logLevel") or DEFAULT_LOG_LEVEL)
        self.on_diagnostic = options.get("onDiagnostic") or options.get("on_diagnostic")
        self.emitter = Emitter()
        self.sticky_features = options.get("stickyFeatures") or options.get("sticky")
        self.sticky_variables = options.get("stickyVariables")
        self.closed = False
        self.module_diagnostic_subscriptions: list[dict[str, Any]] = []
        self.evaluation_diagnostics = _EvaluationDiagnostics(self.report_diagnostic)
        self.datafile = _InstanceEvaluationDataProvider(datafile=empty_datafile, diagnostics=self.evaluation_diagnostics)
        self.modules_manager = ModulesManager(
            modules=options.get("modules") or [],
            report_diagnostic=self.report_diagnostic,
            module_api_factory=self.create_module_api,
            clear_module_diagnostic_subscriptions=self.clear_module_diagnostic_subscriptions,
        )

        if options.get("datafile") is not None:
            self.set_datafile(options["datafile"], True)

        self.report_diagnostic(
            {
                "level": "info",
                "code": "sdk_initialized",
                "message": "SDK initialized",
            }
        )

    def set_log_level(self, level: LogLevel) -> None:
        if level not in LOG_LEVELS:
            raise ValueError("Invalid log level")
        self.log_level = level

    def set_datafile(self, datafile, replace: bool = False) -> None:
        if self.closed:
            return

        try:
            parsed = json.loads(datafile) if isinstance(datafile, str) else datafile
            if not (
                isinstance(parsed, dict)
                and isinstance(parsed.get("schemaVersion"), str)
                and isinstance(parsed.get("revision"), str)
                and isinstance(parsed.get("segments"), dict)
                and isinstance(parsed.get("features"), dict)
                and (parsed.get("variables") is None or isinstance(parsed.get("variables"), dict))
            ):
                raise ValueError("Invalid datafile")
            next_datafile = parsed if replace else self._merge_datafiles(self.datafile.get_datafile(), parsed)
            new_datafile = _InstanceEvaluationDataProvider(datafile=cast(DatafileContent, next_datafile), diagnostics=self.evaluation_diagnostics)
            details = get_params_for_datafile_set_event(self.datafile, new_datafile, replace)
            self.datafile = new_datafile
            self.report_diagnostic({"level": "info", "code": "datafile_set", "message": "Datafile set", "details": details})
            self.emitter.trigger("datafile_set", details)
        except Exception as exc:
            self.report_diagnostic({"level": "error", "code": "invalid_datafile", "message": "Could not parse datafile", "originalError": exc})

    def set_sticky(self, sticky: dict[str, Any], replace: bool = False) -> None:
        self.set_sticky_features(sticky, replace)

    def set_sticky_features(self, sticky: dict[str, Any], replace: bool = False) -> None:
        if self.closed:
            return
        previous = self.sticky_features or {}
        self.sticky_features = dict(sticky) if replace else {**(self.sticky_features or {}), **sticky}
        params = get_params_for_sticky_set_event(previous, self.sticky_features, replace)
        self.report_diagnostic({"level": "info", "code": "sticky_set", "message": "Sticky features set", "details": params})
        self.emitter.trigger("sticky_set", params)
        self.emitter.trigger("sticky_features_set", params)

    def set_sticky_variables(self, sticky: dict[str, Any], replace: bool = False) -> None:
        if self.closed:
            return
        previous = self.sticky_variables or {}
        self.sticky_variables = dict(sticky) if replace else {**(self.sticky_variables or {}), **sticky}
        params = get_params_for_sticky_variables_set_event(previous, self.sticky_variables, replace)
        self.report_diagnostic({"level": "info", "code": "sticky_variables_set", "message": "Sticky variables set", "details": params})
        self.emitter.trigger("sticky_variables_set", params)

    def get_revision(self) -> str:
        return self.datafile.get_revision()

    def get_schema_version(self) -> str:
        return self.datafile.get_schema_version()

    def get_segment(self, segment_key: str):
        return self.datafile.get_segment(segment_key)

    def get_feature_keys(self) -> list[str]:
        return self.datafile.get_feature_keys()

    def get_variable_keys(self, feature_key: str | None = None) -> list[str]:
        return self.datafile.get_variable_keys(feature_key)

    def has_variations(self, feature_key: str) -> bool:
        return self.datafile.has_variations(feature_key)

    def get_feature(self, feature_key: str):
        return self.datafile.get_feature(feature_key)

    def add_module(self, module: dict[str, Any] | FeaturevisorModule):
        if self.closed:
            return None
        return self.modules_manager.add(module)

    def remove_module(self, name: str) -> None:
        if self.closed:
            return
        self.modules_manager.remove(name)

    def on(self, event_name, callback):
        if self.closed:
            return lambda: None
        return self.emitter.on(event_name, callback)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.modules_manager.close_all()
        self.module_diagnostic_subscriptions = []
        self.emitter.clear_all()

    def set_context(self, context: dict[str, Any], replace: bool = False) -> None:
        if self.closed:
            return
        self.context = dict(context) if replace else {**self.context, **context}
        self.emitter.trigger("context_set", {"context": self.context, "replaced": replace})
        self.report_diagnostic({
            "level": "debug",
            "code": "context_set",
            "message": "Context replaced" if replace else "Context updated",
            "details": {"context": self.context, "replaced": replace},
        })

    def get_context(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return {**self.context, **context} if context else self.context

    def spawn(self, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> FeaturevisorChildInstance:
        options = options or {}
        return FeaturevisorChildInstance(
            parent=self,
            context=self.get_context(context or {}),
            sticky_features=options.get("stickyFeatures") or options.get("sticky"),
            sticky_variables=options.get("stickyVariables"),
        )

    def _get_evaluation_dependencies(self, context: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        dependencies = {
            "context": self.get_context(context),
            "diagnostics": self.evaluation_diagnostics,
            "reportDiagnostic": self.report_diagnostic,
            "modulesManager": self.modules_manager,
            "datafile": self.datafile,
            "stickyFeatures": (
                options["__featurevisor_child_sticky_features"]
                if "__featurevisor_child_sticky_features" in options
                else self.sticky_features
            ),
        }
        if "defaultVariationValue" in options:
            dependencies["defaultVariationValue"] = options["defaultVariationValue"]
        if "defaultVariableValue" in options:
            dependencies["defaultVariableValue"] = options["defaultVariableValue"]
        return dependencies

    def evaluate_flag(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return evaluate_with_modules({**self._get_evaluation_dependencies(context or {}, options), "type": "flag", "featureKey": feature_key})

    def is_enabled(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> bool:
        try:
            return self.evaluate_flag(feature_key, context or {}, options).get("enabled") is True
        except Exception as exc:
            self.report_diagnostic({"level": "error", "code": "evaluation_error", "message": "isEnabled failed", "originalError": exc, "details": {"featureKey": feature_key}})
            return False

    def evaluate_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return evaluate_with_modules({**self._get_evaluation_dependencies(context or {}, options), "type": "variation", "featureKey": feature_key})

    def get_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        try:
            evaluation = self.evaluate_variation(feature_key, context or {}, options)
            if "variationValue" in evaluation:
                return evaluation["variationValue"]
            if evaluation.get("variation"):
                return evaluation["variation"]["value"]
            return None
        except Exception as exc:
            self.report_diagnostic({"level": "error", "code": "evaluation_error", "message": "getVariation failed", "originalError": exc, "details": {"featureKey": feature_key}})
            return None

    def evaluate_variable(self, feature_or_variable_key: str, variable_key_or_context=None, context_or_options=None, options=None):
        if isinstance(variable_key_or_context, str):
            return evaluate_with_modules({
                **self._get_evaluation_dependencies(context_or_options or {}, options),
                "type": "variable", "featureKey": feature_or_variable_key, "variableKey": variable_key_or_context,
            })
        return self._evaluate_global_variable(
            feature_or_variable_key,
            variable_key_or_context or {},
            context_or_options or {},
        )

    def get_variable(self, feature_or_variable_key: str, variable_key_or_context=None, context_or_options=None, options=None):
        try:
            evaluation = self.evaluate_variable(feature_or_variable_key, variable_key_or_context, context_or_options, options)
            if "variableValue" in evaluation:
                value = evaluation.get("variableValue")
                variable_type = evaluation.get("variableSchema", {}).get("type") or evaluation.get("variable", {}).get("type")
                if variable_type == "json" and isinstance(value, str):
                    return json.loads(value)
                return value
            return None
        except Exception as exc:
            self.report_diagnostic({"level": "error", "code": "evaluation_error", "message": "getVariable failed", "originalError": exc, "details": {"variableKey": feature_or_variable_key}})
            return None

    def get_variable_boolean(self, *args):
        return get_value_by_type(self.get_variable(*args), "boolean")

    def get_variable_string(self, *args):
        return get_value_by_type(self.get_variable(*args), "string")

    def get_variable_integer(self, *args):
        return get_value_by_type(self.get_variable(*args), "integer")

    def get_variable_double(self, *args):
        return get_value_by_type(self.get_variable(*args), "double")

    def get_variable_array(self, *args):
        return get_value_by_type(self.get_variable(*args), "array")

    def get_variable_object(self, *args):
        return get_value_by_type(self.get_variable(*args), "object")

    def get_variable_json(self, *args):
        return get_value_by_type(self.get_variable(*args), "json")

    def _required_features_are_matched(self, requirements, context, options) -> bool:
        items = requirements if isinstance(requirements, list) else ([requirements] if requirements is not None else [])
        clean_options = {
            key: value
            for key, value in (options or {}).items()
            if key not in {"defaultVariationValue", "defaultVariableValue"}
        }
        for required in items:
            if isinstance(required, str):
                feature_key, expected_enabled, expected_variation = required, True, None
            else:
                feature_key = required["feature"]
                expected_enabled = required.get("enabled", True)
                expected_variation = required.get("variation")
            if self.is_enabled(feature_key, context, clean_options) != expected_enabled:
                return False
            if expected_variation is not None and self.get_variation(feature_key, context, clean_options) != expected_variation:
                return False
        return True

    def _evaluate_global_variable(self, variable_key: str, context: dict[str, Any], options: dict[str, Any]):
        evaluation_options: dict[str, Any] = {
            "type": "variable",
            "variableKey": variable_key,
            "context": self.get_context(context),
        }
        if "defaultVariableValue" in options:
            evaluation_options["defaultVariableValue"] = options["defaultVariableValue"]
        try:
            evaluation_options = self.modules_manager.run_before_evaluation_modules(evaluation_options)
            resolved_key = evaluation_options["variableKey"]
            resolved_context = evaluation_options["context"]
            variable = self.datafile.get_global_variable(resolved_key)
            evaluation: dict[str, Any] = {
                "type": "variable", "variableKey": resolved_key, "reason": "variable_not_found"
            }
            sticky = options.get("__featurevisor_child_sticky_variables", self.sticky_variables) or {}

            if resolved_key in sticky:
                evaluation.update(reason="sticky", variable=variable, variableValue=sticky[resolved_key])
            elif variable:
                if not self._required_features_are_matched(variable.get("requiredFeatures"), resolved_context, options):
                    evaluation.update(reason="required_features_unmet", variable=variable)
                    value_key = "defaultValue" if variable.get("useDefaultWhenDisabled") else "disabledValue"
                    if value_key in variable:
                        evaluation["variableValue"] = variable[value_key]
                else:
                    for index, override in enumerate(variable.get("overrides", [])):
                        if not self._required_features_are_matched(override.get("requiredFeatures"), resolved_context, options):
                            continue
                        conditions_match = not override.get("conditions") or self.datafile.all_conditions_are_matched(
                            self.datafile.parse_conditions_if_stringified(override["conditions"]), resolved_context
                        )
                        segments_match = not override.get("segments") or self.datafile.all_segments_are_matched(
                            self.datafile.parse_segments_if_stringified(override["segments"]), resolved_context
                        )
                        if conditions_match and segments_match:
                            evaluation.update(
                                reason="variable_override_rule",
                                variable=variable,
                                variableValue=override.get("value"),
                                variableOverrideIndex=index,
                            )
                            if override.get("key") is not None:
                                evaluation["variableOverrideKey"] = override["key"]
                            if override.get("keyPath") is not None:
                                evaluation["variableOverridePath"] = override["keyPath"]
                            break
                    if evaluation["reason"] == "variable_not_found":
                        evaluation.update(reason="variable_default", variable=variable)
                        if "defaultValue" in variable:
                            evaluation["variableValue"] = variable["defaultValue"]
                if variable.get("deprecated"):
                    self.report_diagnostic({
                        "level": "warn", "code": "variable_deprecated",
                        "message": f'Variable "{resolved_key}" is deprecated',
                        "details": {"variableKey": resolved_key, "evaluation": evaluation},
                    })

            if "variableValue" not in evaluation and "defaultVariableValue" in options:
                evaluation["variableValue"] = options["defaultVariableValue"]
            evaluation = self.modules_manager.run_after_evaluation_modules(evaluation, evaluation_options)
            self.report_diagnostic({
                "level": "debug", "code": str(evaluation["reason"]),
                "message": "Global variable evaluated", "details": dict(evaluation),
            })
            return evaluation
        except Exception as exc:
            evaluation = {"type": "variable", "variableKey": variable_key, "reason": "error", "error": exc}
            self.report_diagnostic({"level": "error", "code": "evaluation_error", "message": "Global variable evaluation failed", "originalError": exc, "details": evaluation})
            return evaluation

    def get_feature_evaluations(self, context: dict[str, Any] | None = None, feature_keys: list[str] | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
        result: dict[str, Any] = {}
        keys = feature_keys or self.datafile.get_feature_keys()
        for feature_key in keys:
            evaluated: dict[str, Any] = {"enabled": self.is_enabled(feature_key, context or {}, options)}
            if self.datafile.has_variations(feature_key):
                variation = self.get_variation(feature_key, context or {}, options)
                if variation is not None:
                    evaluated["variation"] = variation
            variable_keys = self.datafile.get_variable_keys(feature_key)
            if variable_keys:
                evaluated["variables"] = {
                    variable_key: self.get_variable(feature_key, variable_key, context or {}, options)
                    for variable_key in variable_keys
                }
            result[feature_key] = evaluated
        return result

    def get_variable_evaluations(self, context: dict[str, Any] | None = None, variable_keys: list[str] | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
        keys = variable_keys or self.datafile.get_variable_keys()
        return {key: self.get_variable(key, context or {}, options or {}) for key in keys}

    def get_all_evaluations(self, context: dict[str, Any] | None = None, feature_keys: list[str] | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.get_feature_evaluations(context, feature_keys, options)

    def create_module_api(self, module: FeaturevisorModule) -> dict[str, Any]:
        def on_diagnostic(handler, options: dict[str, Any] | None = None):
            options = options or {}
            subscription = {
                "id": str(uuid.uuid4()),
                "moduleId": module.id,
                "handler": handler,
                "level": options.get("level") or options.get("logLevel") or "info",
            }
            self.module_diagnostic_subscriptions.append(subscription)

            def unsubscribe() -> None:
                self.module_diagnostic_subscriptions = [
                    item for item in self.module_diagnostic_subscriptions if item["id"] != subscription["id"]
                ]

            return unsubscribe

        return {
            "getRevision": self.get_revision,
            "get_revision": self.get_revision,
            "onDiagnostic": on_diagnostic,
            "on_diagnostic": on_diagnostic,
            "reportDiagnostic": lambda diagnostic: self.report_diagnostic(diagnostic, module),
            "report_diagnostic": lambda diagnostic: self.report_diagnostic(diagnostic, module),
        }

    def clear_module_diagnostic_subscriptions(self, module: FeaturevisorModule) -> None:
        self.module_diagnostic_subscriptions = [
            item for item in self.module_diagnostic_subscriptions if item["moduleId"] != module.id
        ]

    def report_diagnostic(self, diagnostic: dict[str, Any], source_module: FeaturevisorModule | None = None) -> None:
        diagnostic = dict(diagnostic or {})
        diagnostic["level"] = diagnostic.get("level") or "info"
        if source_module and source_module.name:
            diagnostic["module"] = source_module.name
        details = dict(diagnostic.get("details") or {})
        reserved = {"level", "code", "message", "module", "moduleName", "originalError", "details"}
        for key, value in list(diagnostic.items()):
            if key not in reserved:
                details[key] = value
                del diagnostic[key]
        diagnostic["details"] = details

        for subscription in list(self.module_diagnostic_subscriptions):
            if source_module and subscription["moduleId"] == source_module.id:
                continue
            if self._should_report_diagnostic(diagnostic["level"], subscription["level"]):
                try:
                    subscription["handler"](diagnostic)
                except Exception as exc:
                    print("[Featurevisor] Diagnostic handler failed:", exc)

        if self.on_diagnostic:
            if self._should_report_diagnostic(diagnostic["level"], self.log_level):
                try:
                    self.on_diagnostic(diagnostic)
                except Exception as exc:
                    print("[Featurevisor] Diagnostic handler failed:", exc)
        elif self._should_report_diagnostic(diagnostic["level"], self.log_level):
            write_diagnostic_to_console(diagnostic)

        if diagnostic["level"] == "error":
            self.emitter.trigger("error", {"diagnostic": diagnostic})

    def _should_report_diagnostic(self, diagnostic_level: LogLevel, subscriber_level: LogLevel) -> bool:
        return should_report(subscriber_level, diagnostic_level)

    def _merge_datafiles(self, previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": incoming.get("schemaVersion"),
            "revision": incoming.get("revision"),
            "featurevisorVersion": incoming.get("featurevisorVersion"),
            "segments": {**(previous.get("segments") or {}), **(incoming.get("segments") or {})},
            "features": {**(previous.get("features") or {}), **(incoming.get("features") or {})},
            "variables": {**(previous.get("variables") or {}), **(incoming.get("variables") or {})},
        }

    setLogLevel = set_log_level
    setDatafile = set_datafile
    setSticky = set_sticky
    setStickyFeatures = set_sticky_features
    setStickyVariables = set_sticky_variables
    getRevision = get_revision
    getSchemaVersion = get_schema_version
    getSegment = get_segment
    getFeatureKeys = get_feature_keys
    getVariableKeys = get_variable_keys
    hasVariations = has_variations
    getFeature = get_feature
    addModule = add_module
    removeModule = remove_module
    setContext = set_context
    getContext = get_context
    evaluateFlag = evaluate_flag
    isEnabled = is_enabled
    evaluateVariation = evaluate_variation
    getVariation = get_variation
    evaluateVariable = evaluate_variable
    getVariable = get_variable
    getVariableBoolean = get_variable_boolean
    getVariableString = get_variable_string
    getVariableInteger = get_variable_integer
    getVariableDouble = get_variable_double
    getVariableArray = get_variable_array
    getVariableObject = get_variable_object
    getVariableJSON = get_variable_json
    getAllEvaluations = get_all_evaluations
    getFeatureEvaluations = get_feature_evaluations
    getVariableEvaluations = get_variable_evaluations


def create_featurevisor(options: dict[str, Any] | None = None) -> Featurevisor:
    return Featurevisor(options or {})
