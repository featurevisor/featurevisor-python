from __future__ import annotations

import json
from typing import Any

from .child import FeaturevisorChildInstance
from .datafile_reader import DatafileReader
from .emitter import Emitter
from .evaluate import evaluate_with_hooks
from .events import get_params_for_datafile_set_event, get_params_for_sticky_set_event
from .helpers import get_value_by_type
from .hooks import HooksManager
from .logger import Logger, create_logger

empty_datafile = {"schemaVersion": "2", "revision": "unknown", "segments": {}, "features": {}}


class FeaturevisorInstance:
    def __init__(self, options: dict[str, Any] | None = None) -> None:
        options = options or {}
        self.context = options.get("context") or {}
        self.logger: Logger = options.get("logger") or create_logger({"level": options.get("logLevel") or Logger.default_level})
        self.hooks_manager = HooksManager(hooks=options.get("hooks") or [], logger=self.logger)
        self.emitter = Emitter()
        self.sticky = options.get("sticky")
        self.datafile_reader = DatafileReader(datafile=empty_datafile, logger=self.logger)
        if options.get("datafile") is not None:
            datafile = options["datafile"]
            if isinstance(datafile, str):
                datafile = json.loads(datafile)
            self.datafile_reader = DatafileReader(datafile=datafile, logger=self.logger)
        self.logger.info("Featurevisor SDK initialized")

    def set_log_level(self, level: str) -> None:
        self.logger.set_level(level)

    def set_datafile(self, datafile) -> None:
        try:
            parsed = json.loads(datafile) if isinstance(datafile, str) else datafile
            new_reader = DatafileReader(datafile=parsed, logger=self.logger)
            details = get_params_for_datafile_set_event(self.datafile_reader, new_reader)
            self.datafile_reader = new_reader
            self.logger.info("datafile set", details)
            self.emitter.trigger("datafile_set", details)
        except Exception as exc:
            self.logger.error("could not parse datafile", {"error": exc})

    def set_sticky(self, sticky: dict[str, Any], replace: bool = False) -> None:
        previous = self.sticky or {}
        self.sticky = dict(sticky) if replace else {**(self.sticky or {}), **sticky}
        params = get_params_for_sticky_set_event(previous, self.sticky, replace)
        self.logger.info("sticky features set", params)
        self.emitter.trigger("sticky_set", params)

    def get_revision(self) -> str:
        return self.datafile_reader.get_revision()

    def get_feature(self, feature_key: str):
        return self.datafile_reader.get_feature(feature_key)

    def add_hook(self, hook: dict[str, Any]):
        return self.hooks_manager.add(hook)

    def on(self, event_name, callback):
        return self.emitter.on(event_name, callback)

    def close(self) -> None:
        self.emitter.clear_all()

    def set_context(self, context: dict[str, Any], replace: bool = False) -> None:
        self.context = dict(context) if replace else {**self.context, **context}
        self.emitter.trigger("context_set", {"context": self.context, "replaced": replace})
        self.logger.debug("context replaced" if replace else "context updated", {"context": self.context, "replaced": replace})

    def get_context(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return {**self.context, **context} if context else self.context

    def spawn(self, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> FeaturevisorChildInstance:
        options = options or {}
        return FeaturevisorChildInstance(parent=self, context=self.get_context(context or {}), sticky=options.get("sticky"))

    def _get_evaluation_dependencies(self, context: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        return {
            "context": self.get_context(context),
            "logger": self.logger,
            "hooksManager": self.hooks_manager,
            "datafileReader": self.datafile_reader,
            "sticky": {**(self.sticky or {}), **options.get("sticky", {})} if options.get("sticky") else self.sticky,
            "defaultVariationValue": options.get("defaultVariationValue"),
            "defaultVariableValue": options.get("defaultVariableValue"),
        }

    def evaluate_flag(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return evaluate_with_hooks({**self._get_evaluation_dependencies(context or {}, options), "type": "flag", "featureKey": feature_key})

    def is_enabled(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> bool:
        try:
            return self.evaluate_flag(feature_key, context or {}, options).get("enabled") is True
        except Exception as exc:
            self.logger.error("isEnabled", {"featureKey": feature_key, "error": exc})
            return False

    def evaluate_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return evaluate_with_hooks({**self._get_evaluation_dependencies(context or {}, options), "type": "variation", "featureKey": feature_key})

    def get_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        try:
            evaluation = self.evaluate_variation(feature_key, context or {}, options)
            if evaluation.get("variationValue") is not None:
                return evaluation.get("variationValue")
            if evaluation.get("variation"):
                return evaluation["variation"]["value"]
            return None
        except Exception as exc:
            self.logger.error("getVariation", {"featureKey": feature_key, "error": exc})
            return None

    def evaluate_variable(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return evaluate_with_hooks({**self._get_evaluation_dependencies(context or {}, options), "type": "variable", "featureKey": feature_key, "variableKey": variable_key})

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
            self.logger.error("getVariable", {"featureKey": feature_key, "variableKey": variable_key, "error": exc})
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
        keys = feature_keys or self.datafile_reader.get_feature_keys()
        for feature_key in keys:
            evaluated = {"enabled": self.is_enabled(feature_key, context or {}, options)}
            if self.datafile_reader.has_variations(feature_key):
                variation = self.get_variation(feature_key, context or {}, options)
                if variation is not None:
                    evaluated["variation"] = variation
            variable_keys = self.datafile_reader.get_variable_keys(feature_key)
            if variable_keys:
                evaluated["variables"] = {
                    variable_key: self.get_variable(feature_key, variable_key, context or {}, options)
                    for variable_key in variable_keys
                }
            result[feature_key] = evaluated
        return result

    setLogLevel = set_log_level
    setDatafile = set_datafile
    setSticky = set_sticky
    getRevision = get_revision
    getFeature = get_feature
    addHook = add_hook
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


def create_instance(options: dict[str, Any] | None = None) -> FeaturevisorInstance:
    return FeaturevisorInstance(options or {})

