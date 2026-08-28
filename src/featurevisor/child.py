from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .emitter import Emitter
from .events import get_params_for_sticky_features_set_event, get_params_for_sticky_variables_set_event

if TYPE_CHECKING:
    from .instance import Featurevisor


class FeaturevisorChildInstance:
    def __init__(self, *, parent: "Featurevisor", context: dict[str, Any], sticky_features: dict[str, Any] | None = None, sticky_variables: dict[str, Any] | None = None) -> None:
        self.parent = parent
        self.context = context
        self.sticky_features = sticky_features or {}
        self.sticky_variables = sticky_variables or {}
        self.emitter = Emitter()
        self._parent_unsubscribers: list[Any] = []

    def on(self, event_name, callback):
        if event_name in {"context_set", "sticky_features_set", "sticky_variables_set"}:
            return self.emitter.on(event_name, callback)
        parent_unsubscribe = self.parent.on(event_name, callback)
        active = True

        def unsubscribe():
            nonlocal active
            if not active:
                return
            active = False
            parent_unsubscribe()
            if unsubscribe in self._parent_unsubscribers:
                self._parent_unsubscribers.remove(unsubscribe)

        self._parent_unsubscribers.append(unsubscribe)
        return unsubscribe

    def close(self) -> None:
        for unsubscribe in list(self._parent_unsubscribers):
            unsubscribe()
        self._parent_unsubscribers.clear()
        self.emitter.clear_all()

    def set_context(self, context: dict[str, Any], replace: bool = False) -> None:
        self.context = dict(context) if replace else {**self.context, **context}
        self.emitter.trigger("context_set", {"context": self.context, "replaced": replace})

    def get_context(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.parent.get_context({**self.context, **(context or {})})

    def set_sticky_features(self, sticky: dict[str, Any], replace: bool = False) -> None:
        previous = self.sticky_features
        self.sticky_features = dict(sticky) if replace else {**self.sticky_features, **sticky}
        params = get_params_for_sticky_features_set_event(previous, self.sticky_features, replace)
        self.emitter.trigger("sticky_features_set", params)

    def set_sticky_variables(self, sticky: dict[str, Any], replace: bool = False) -> None:
        previous = self.sticky_variables
        self.sticky_variables = dict(sticky) if replace else {**self.sticky_variables, **sticky}
        self.emitter.trigger("sticky_variables_set", get_params_for_sticky_variables_set_event(previous, self.sticky_variables, replace))

    def _merge_context(self, context: dict[str, Any] | None) -> dict[str, Any]:
        return {**self.context, **(context or {})}

    def _merge_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        # Sticky assignments belong to an instance. This private value carries
        # this child instance's state to its parent without exposing a
        # per-evaluation sticky override in the public options API.
        return {
            **(options or {}),
            "__featurevisor_child_sticky_features": self.sticky_features,
            "__featurevisor_child_sticky_variables": self.sticky_variables,
        }

    def is_enabled(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> bool:
        return self.parent.is_enabled(feature_key, self._merge_context(context), self._merge_options(options))

    def evaluate_flag(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_flag(feature_key, self._merge_context(context), self._merge_options(options))

    def get_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variation(feature_key, self._merge_context(context), self._merge_options(options))

    def evaluate_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_variation(feature_key, self._merge_context(context), self._merge_options(options))

    def _variable_args(self, feature_or_variable_key, variable_key_or_context=None, context_or_options=None, options=None):
        if isinstance(variable_key_or_context, str):
            return (
                feature_or_variable_key, variable_key_or_context,
                self._merge_context(context_or_options), self._merge_options(options),
            )
        return (
            feature_or_variable_key,
            self._merge_context(variable_key_or_context),
            self._merge_options(context_or_options),
        )

    def get_variable(self, *args):
        return self.parent.get_variable(*self._variable_args(*args))

    def evaluate_variable(self, *args):
        return self.parent.evaluate_variable(*self._variable_args(*args))

    def get_variable_boolean(self, *args):
        return self.parent.get_variable_boolean(*self._variable_args(*args))

    def get_variable_string(self, *args):
        return self.parent.get_variable_string(*self._variable_args(*args))

    def get_variable_integer(self, *args):
        return self.parent.get_variable_integer(*self._variable_args(*args))

    def get_variable_double(self, *args):
        return self.parent.get_variable_double(*self._variable_args(*args))

    def get_variable_array(self, *args):
        return self.parent.get_variable_array(*self._variable_args(*args))

    def get_variable_object(self, *args):
        return self.parent.get_variable_object(*self._variable_args(*args))

    def get_variable_json(self, *args):
        return self.parent.get_variable_json(*self._variable_args(*args))

    def get_feature_evaluations(self, context=None, feature_keys=None, options=None):
        return self.parent.get_feature_evaluations(self._merge_context(context), feature_keys, self._merge_options(options))

    def get_variable_evaluations(self, context=None, variable_keys=None, options=None):
        return self.parent.get_variable_evaluations(self._merge_context(context), variable_keys, self._merge_options(options))

    setContext = set_context
    getContext = get_context
    setStickyFeatures = set_sticky_features
    setStickyVariables = set_sticky_variables
    isEnabled = is_enabled
    evaluateFlag = evaluate_flag
    getVariation = get_variation
    evaluateVariation = evaluate_variation
    getVariable = get_variable
    evaluateVariable = evaluate_variable
    getVariableBoolean = get_variable_boolean
    getVariableString = get_variable_string
    getVariableInteger = get_variable_integer
    getVariableDouble = get_variable_double
    getVariableArray = get_variable_array
    getVariableObject = get_variable_object
    getVariableJSON = get_variable_json
    getFeatureEvaluations = get_feature_evaluations
    getVariableEvaluations = get_variable_evaluations
