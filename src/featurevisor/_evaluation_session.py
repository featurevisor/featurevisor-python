from __future__ import annotations

import copy
import datetime as dt
import math
from contextlib import contextmanager
from typing import Any, Iterator


class RequirementInputError(ValueError):
    """A malformed or circular prerequisite, distinct from an ordinary getter error."""


class DependencySnapshot(dict[str, Any]):
    """Internal evaluation with a lazily normalized ordinary getter value."""

    getter_value: Any


_UNSUPPORTED = object()


def _fingerprint(value: Any, active: set[int] | None = None) -> Any:
    if value is None:
        return ("null",)
    if isinstance(value, bool):
        return ("boolean", value)
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return _UNSUPPORTED
        if isinstance(value, float) and value == 0 and math.copysign(1.0, value) < 0:
            return ("number", "negative-zero")
        # Modules can distinguish Python integers from floats even when the
        # comparison operators deliberately treat their numeric values equally.
        return ("float" if isinstance(value, float) else "integer", value)
    if isinstance(value, str):
        return ("string", value)
    if isinstance(value, (dt.datetime, dt.date)):
        return (type(value).__name__, value.isoformat())
    if not isinstance(value, (dict, list, tuple)):
        return _UNSUPPORTED
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        return _UNSUPPORTED
    active.add(identity)
    try:
        if isinstance(value, dict):
            items = []
            for key, item in value.items():
                key_part = _fingerprint(key, active)
                value_part = _fingerprint(item, active)
                if key_part is _UNSUPPORTED or value_part is _UNSUPPORTED:
                    return _UNSUPPORTED
                items.append((key_part, value_part))
            return ("object", tuple(sorted(items, key=lambda pair: repr(pair[0]))))
        parts = tuple(_fingerprint(item, active) for item in value)
        if any(part is _UNSUPPORTED for part in parts):
            return _UNSUPPORTED
        return ("tuple" if isinstance(value, tuple) else "array", parts)
    finally:
        active.remove(identity)


def dependency_of(options: dict[str, Any]) -> dict[str, Any]:
    parent: dict[str, Any] = {"type": options["type"]}
    if options.get("featureKey") is not None:
        parent["featureKey"] = options["featureKey"]
    if options.get("variableKey") is not None:
        parent["variableKey"] = options["variableKey"]
    return parent


def refresh_evaluation_state(options: dict[str, Any]) -> None:
    # Skip only owner state lookups. Module replacements remain in options,
    # and dependency memo keys still fingerprint the effective context.
    version = options.get("__featurevisor_state_version")
    if callable(version):
        generation = version()
        if options.get("__featurevisor_refreshed_state_version", _UNSUPPORTED) == generation:
            return
        options["__featurevisor_refreshed_state_version"] = generation
    source = options.get("__featurevisor_child_sticky_source")
    initial = options.get("__featurevisor_child_initial_sticky")
    if callable(source) and isinstance(initial, tuple):
        current = source()
        baseline = list(initial)
        for index, name in enumerate(("stickyFeatures", "stickyVariables")):
            if options.get(name) is initial[index]:
                options[name] = current[index]
                baseline[index] = current[index]
        options["__featurevisor_child_initial_sticky"] = tuple(baseline)
    datafile = options.get("__featurevisor_datafile_source")
    if callable(datafile) and options.get("datafile") is options.get("__featurevisor_initial_datafile"):
        options["datafile"] = datafile()
        options["__featurevisor_initial_datafile"] = options["datafile"]


class EvaluationSession:
    """Request-local prerequisite stack and completed dependency values."""

    def __init__(self) -> None:
        self.memo: dict[tuple[Any, ...], dict[str, Any]] = {}
        self.sticky_refs: dict[int, Any] = {}
        self.active: list[tuple[Any, ...]] = []
        self.current: list[dict[str, Any]] = []
        self.aliases: list[dict[str, Any]] = []
        self.segments: set[str] = set()

    @staticmethod
    def identity(options: dict[str, Any]) -> tuple[Any, ...]:
        return (options.get("type"), options.get("featureKey"), options.get("variableKey"))

    @contextmanager
    def frame(self, options: dict[str, Any]) -> Iterator[None]:
        identity = self.identity(options)
        if len(self.active) + len(self.segments) > 256:
            raise RequirementInputError("Evaluation dependency depth exceeded")
        if identity in self.active or any(identity == self.identity(current) for current in self.current):
            raise RequirementInputError("Circular evaluation requirement")
        parent = dependency_of(options)
        self.active.append(identity)
        self.current.append(parent)
        try:
            yield
        finally:
            self.active.pop()
            self.current.pop()
            if not self.active:
                self.memo.clear()
                self.sticky_refs.clear()
                for alias in self.aliases:
                    for name in ("_evaluationSession", "__featurevisor_state_version",
                                 "__featurevisor_child_state_version", "__featurevisor_child_sticky_source",
                                 "__featurevisor_child_initial_sticky", "__featurevisor_datafile_source",
                                 "__featurevisor_initial_datafile", "__featurevisor_refreshed_state_version"):
                        alias.pop(name, None)
                self.aliases.clear()

    def retain(self, options: dict[str, Any]) -> None:
        self.aliases.append(options)

    def update(self, options: dict[str, Any]) -> None:
        if self.current:
            self.current[-1] = dependency_of(options)

    def parent(self) -> dict[str, Any]:
        return dict(self.current[-1])

    def key(self, options: dict[str, Any]) -> tuple[Any, ...] | None:
        if "dependencyOf" not in options:
            return None
        # Sticky maps and their nested values are read only inputs. Identity
        # distinguishes module replacements, while owner generations detect
        # setter changes even when a value is restored during the same request.
        # Keep every keyed map alive until the root request ends so an id cannot
        # be recycled while an earlier memo key still exists.
        features = options.get("stickyFeatures")
        variables = options.get("stickyVariables")
        if features is not None:
            self.sticky_refs[id(features)] = features
        if variables is not None:
            self.sticky_refs[id(variables)] = variables
        parts = (_fingerprint(options.get("context")), id(features), id(variables),
                 _fingerprint(options.get("dependencyOf")))
        if any(part is _UNSUPPORTED for part in parts):
            return None
        version = options.get("__featurevisor_state_version")
        generation = version() if callable(version) else None
        return (self.identity(options), parts, generation, id(options.get("datafile")),
                options.get("dependencyPurpose"),
                tuple(id(module) for module in options["modulesManager"].get_all()))

    def get(self, key: tuple[Any, ...] | None) -> dict[str, Any] | None:
        if key is None or key not in self.memo:
            return None
        # Internal reads only. Conditions own snapshots, while composition keeps
        # borrowed values. Neither entry is passed to application callbacks.
        return self.memo[key]

    def put(self, key: tuple[Any, ...] | None, evaluation: dict[str, Any]) -> DependencySnapshot | None:
        if key is None or isinstance(evaluation.get("error"), (RequirementInputError, RecursionError)):
            return None
        fields = {"type", "featureKey", "variableKey", "reason", "enabled", "variationValue", "variableValue"}
        minimal = {name: value for name, value in evaluation.items() if name in fields}
        if "variationValue" not in minimal and isinstance(evaluation.get("variation"), dict):
            minimal["variationValue"] = evaluation["variation"].get("value")
        for name in ("variableSchema", "variable"):
            value = evaluation.get(name)
            if isinstance(value, dict) and "type" in value:
                minimal[name] = {"type": value["type"]}
                if "compose" in value:
                    minimal[name]["compose"] = True
        if key[4] == "composition":
            snapshot = DependencySnapshot(minimal)
            self.memo[key] = snapshot
            return snapshot
        if _fingerprint(minimal) is _UNSUPPORTED:
            return None
        try:
            snapshot = DependencySnapshot(copy.deepcopy(minimal))
            self.memo[key] = snapshot
            return snapshot
        except RecursionError as exc:
            raise RequirementInputError("Host recursion limit exceeded during dependency evaluation") from exc
        except Exception:
            return None
