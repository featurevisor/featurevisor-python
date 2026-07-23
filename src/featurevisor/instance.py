from __future__ import annotations

import json
import uuid
from typing import Any, cast

from .child import FeaturevisorChildInstance
from .evaluation_data_provider import _InstanceEvaluationDataProvider
from .emitter import Emitter
from .evaluate import evaluate_with_modules
from .events import get_params_for_datafile_set_event, get_params_for_sticky_set_event
from .helpers import get_value_by_type
from .diagnostics import DEFAULT_LOG_LEVEL, LOG_LEVELS, _EvaluationDiagnostics, should_report, write_diagnostic_to_console
from .modules import FeaturevisorModule, ModulesManager
from .types import DatafileContent, LogLevel

empty_datafile: DatafileContent = {"schemaVersion": "2", "revision": "unknown", "segments": {}, "features": {}}


class Featurevisor:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        options = options or {}
        self.context = options.get("context") or {}
        self.log_level = cast(LogLevel, options.get("logLevel") or DEFAULT_LOG_LEVEL)
        self.on_diagnostic = options.get("onDiagnostic") or options.get("on_diagnostic")
        self.emitter = Emitter()
        self.sticky = options.get("sticky")
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
        if self.closed:
            return
        previous = self.sticky or {}
        self.sticky = dict(sticky) if replace else {**(self.sticky or {}), **sticky}
        params = get_params_for_sticky_set_event(previous, self.sticky, replace)
        self.report_diagnostic({"level": "info", "code": "sticky_set", "message": "Sticky features set", "details": params})
        self.emitter.trigger("sticky_set", params)

    def get_revision(self) -> str:
        return self.datafile.get_revision()

    def get_schema_version(self) -> str:
        return self.datafile.get_schema_version()

    def get_segment(self, segment_key: str):
        return self.datafile.get_segment(segment_key)

    def get_feature_keys(self) -> list[str]:
        return self.datafile.get_feature_keys()

    def get_variable_keys(self, feature_key: str) -> list[str]:
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
        return FeaturevisorChildInstance(parent=self, context=self.get_context(context or {}), sticky=options.get("sticky"))

    def _get_evaluation_dependencies(self, context: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        dependencies = {
            "context": self.get_context(context),
            "diagnostics": self.evaluation_diagnostics,
            "reportDiagnostic": self.report_diagnostic,
            "modulesManager": self.modules_manager,
            "datafile": self.datafile,
            "sticky": (
                options["__featurevisor_child_sticky"]
                if "__featurevisor_child_sticky" in options
                else self.sticky
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

    def evaluate_variable(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return evaluate_with_modules({**self._get_evaluation_dependencies(context or {}, options), "type": "variable", "featureKey": feature_key, "variableKey": variable_key})

    def get_variable(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        try:
            evaluation = self.evaluate_variable(feature_key, variable_key, context or {}, options)
            if "variableValue" in evaluation:
                value = evaluation.get("variableValue")
                if evaluation.get("variableSchema", {}).get("type") == "json" and isinstance(value, str):
                    return json.loads(value)
                return value
            return None
        except Exception as exc:
            self.report_diagnostic({"level": "error", "code": "evaluation_error", "message": "getVariable failed", "originalError": exc, "details": {"featureKey": feature_key, "variableKey": variable_key}})
            return None

    def get_variable_boolean(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return get_value_by_type(self.get_variable(feature_key, variable_key, context or {}, options), "boolean")

    def get_variable_string(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return get_value_by_type(self.get_variable(feature_key, variable_key, context or {}, options), "string")

    def get_variable_integer(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return get_value_by_type(self.get_variable(feature_key, variable_key, context or {}, options), "integer")

    def get_variable_double(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return get_value_by_type(self.get_variable(feature_key, variable_key, context or {}, options), "double")

    def get_variable_array(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return get_value_by_type(self.get_variable(feature_key, variable_key, context or {}, options), "array")

    def get_variable_object(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return get_value_by_type(self.get_variable(feature_key, variable_key, context or {}, options), "object")

    def get_variable_json(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return get_value_by_type(self.get_variable(feature_key, variable_key, context or {}, options), "json")

    def get_all_evaluations(self, context: dict[str, Any] | None = None, feature_keys: list[str] | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
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
        }

    setLogLevel = set_log_level
    setDatafile = set_datafile
    setSticky = set_sticky
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


def create_featurevisor(options: dict[str, Any] | None = None) -> Featurevisor:
    return Featurevisor(options or {})
