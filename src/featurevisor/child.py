from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .emitter import Emitter
from .events import get_params_for_sticky_set_event

if TYPE_CHECKING:
    from .instance import FeaturevisorInstance


class FeaturevisorChildInstance:
    def __init__(self, *, parent: "FeaturevisorInstance", context: dict[str, Any], sticky: dict[str, Any] | None = None) -> None:
        self.parent = parent
        self.context = context
        self.sticky = sticky or {}
        self.emitter = Emitter()

    def on(self, event_name, callback):
        if event_name in {"context_set", "sticky_set"}:
            return self.emitter.on(event_name, callback)
        return self.parent.on(event_name, callback)

    def close(self) -> None:
        self.emitter.clear_all()

    def set_context(self, context: dict[str, Any], replace: bool = False) -> None:
        self.context = dict(context) if replace else {**self.context, **context}
        self.emitter.trigger("context_set", {"context": self.context, "replaced": replace})

    def get_context(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.parent.get_context({**self.context, **(context or {})})

    def set_sticky(self, sticky: dict[str, Any], replace: bool = False) -> None:
        previous = self.sticky or {}
        self.sticky = dict(sticky) if replace else {**self.sticky, **sticky}
        self.emitter.trigger("sticky_set", get_params_for_sticky_set_event(previous, self.sticky, replace))

    def _merge_context(self, context: dict[str, Any] | None) -> dict[str, Any]:
        return {**self.context, **(context or {})}

    def _merge_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        return {"sticky": self.sticky, **(options or {})}

    def is_enabled(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> bool:
        return self.parent.is_enabled(feature_key, self._merge_context(context), self._merge_options(options))

    def get_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variation(feature_key, self._merge_context(context), self._merge_options(options))

    def get_variable(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def get_variable_boolean(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable_boolean(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def get_variable_string(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable_string(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def get_variable_integer(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable_integer(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def get_variable_double(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable_double(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def get_variable_array(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable_array(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def get_variable_object(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable_object(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def get_variable_json(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable_json(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def evaluate_flag(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_flag(feature_key, self._merge_context(context), self._merge_options(options))

    def evaluate_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_variation(feature_key, self._merge_context(context), self._merge_options(options))

    def evaluate_variable(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_variable(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    setContext = set_context
    getContext = get_context
    setSticky = set_sticky
    isEnabled = is_enabled
    getVariation = get_variation
    getVariable = get_variable
    getVariableBoolean = get_variable_boolean
    getVariableString = get_variable_string
    getVariableInteger = get_variable_integer
    getVariableDouble = get_variable_double
    getVariableArray = get_variable_array
    getVariableObject = get_variable_object
    getVariableJSON = get_variable_json
    evaluateFlag = evaluate_flag
    evaluateVariation = evaluate_variation
    evaluateVariable = evaluate_variable

