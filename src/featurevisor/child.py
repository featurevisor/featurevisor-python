from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .emitter import Emitter
from .events import get_params_for_sticky_set_event

if TYPE_CHECKING:
    from .instance import Featurevisor


class FeaturevisorChildInstance:
    def __init__(self, *, parent: "Featurevisor", context: dict[str, Any], sticky: dict[str, Any] | None = None) -> None:
        self.parent = parent
        self.context = context
        self.sticky = sticky or {}
        self.emitter = Emitter()
        self._parent_unsubscribers: list[Any] = []

    def on(self, event_name, callback):
        if event_name in {"context_set", "sticky_set"}:
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

    def set_sticky(self, sticky: dict[str, Any], replace: bool = False) -> None:
        previous = self.sticky or {}
        self.sticky = dict(sticky) if replace else {**self.sticky, **sticky}
        self.emitter.trigger("sticky_set", get_params_for_sticky_set_event(previous, self.sticky, replace))

    def _merge_context(self, context: dict[str, Any] | None) -> dict[str, Any]:
        return {**self.context, **(context or {})}

    def _merge_options(self, options: dict[str, Any] | None) -> dict[str, Any]:
        # Sticky assignments belong to an instance. This private value carries
        # this child instance's state to its parent without exposing a
        # per-evaluation sticky override in the public options API.
        return {**(options or {}), "__featurevisor_child_sticky": self.sticky}

    def is_enabled(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None) -> bool:
        return self.parent.is_enabled(feature_key, self._merge_context(context), self._merge_options(options))

    def evaluate_flag(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_flag(feature_key, self._merge_context(context), self._merge_options(options))

    def get_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variation(feature_key, self._merge_context(context), self._merge_options(options))

    def evaluate_variation(self, feature_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_variation(feature_key, self._merge_context(context), self._merge_options(options))

    def get_variable(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.get_variable(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

    def evaluate_variable(self, feature_key: str, variable_key: str, context: dict[str, Any] | None = None, options: dict[str, Any] | None = None):
        return self.parent.evaluate_variable(feature_key, variable_key, self._merge_context(context), self._merge_options(options))

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

    setContext = set_context
    getContext = get_context
    setSticky = set_sticky
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
