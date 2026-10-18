from __future__ import annotations

import uuid
from threading import Lock
from typing import Any, cast

from .child import FeaturevisorChildInstance
from ._evaluation_session import DependencySnapshot, EvaluationSession, RequirementInputError, dependency_of, refresh_evaluation_state
from .evaluation_data_provider import _InstanceEvaluationDataProvider
from .emitter import Emitter
from .evaluate import evaluate_with_modules
from .events import get_params_for_datafile_set_event, get_params_for_sticky_features_set_event, get_params_for_sticky_variables_set_event
from .helpers import get_value_by_type
from ._json import strict_json_loads
from .conditions import MISSING, value_is_matched, _strict_equal
from .normalize import normalize_datafile, InvalidConditions, ConditionsParseFailure
from .diagnostics import DEFAULT_LOG_LEVEL, LOG_LEVELS, _EvaluationDiagnostics, should_report, write_diagnostic_to_console
from .mutations import _array_index, apply_variable_operations
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
        self.sticky_features = options.get("stickyFeatures")
        self.sticky_variables = options.get("stickyVariables")
        self.closed = False
        self._state_version = 0
        self._datafile_lock = Lock()
        self.module_diagnostic_subscriptions: list[dict[str, Any]] = []
        self.evaluation_diagnostics = _EvaluationDiagnostics(
            self.report_diagnostic,
            self._diagnostic_is_observed,
        )
        self.datafile = _InstanceEvaluationDataProvider(datafile=empty_datafile, diagnostics=self.evaluation_diagnostics)
        self.datafile.conditions_are_matched = self._conditions_are_matched
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

    def set_datafile(self, datafile, replace: bool = False) -> bool:
        """Return whether the datafile was accepted, even if unchanged."""
        if self.closed:
            return False

        try:
            parsed = strict_json_loads(datafile) if isinstance(datafile, str) else datafile
            if not (
                isinstance(parsed, dict)
                and isinstance(parsed.get("schemaVersion"), str)
                and isinstance(parsed.get("revision"), str)
                and isinstance(parsed.get("segments"), dict)
                and isinstance(parsed.get("features"), dict)
                and (parsed.get("variables") is None or isinstance(parsed.get("variables"), dict))
            ):
                raise ValueError("Invalid datafile")
            errors = []
            if parsed["schemaVersion"] == "2":
                parsed, errors = normalize_datafile(parsed)
            # Serialize read, merge and publication, but never application callbacks.
            with self._datafile_lock:
                if self.closed:
                    return False
                previous_datafile = self.datafile
                next_datafile = parsed if replace else self._merge_datafiles(previous_datafile.get_datafile(), parsed)
                new_datafile = _InstanceEvaluationDataProvider(datafile=cast(DatafileContent, next_datafile), diagnostics=self.evaluation_diagnostics)
                new_datafile.conditions_are_matched = self._conditions_are_matched
                if not replace:
                    incoming = {"feature": parsed.get("features", {}), "segment": parsed.get("segments", {}),
                                "variable": parsed.get("variables") or {}}
                    new_datafile.retain_entities(previous_datafile, incoming)
                details = get_params_for_datafile_set_event(previous_datafile, new_datafile, replace, parsed)
                self.datafile = new_datafile
                self._state_version += 1
            for diagnostic in errors:
                self.report_diagnostic(diagnostic)
            self.report_diagnostic({"level": "info", "code": "datafile_set", "message": "Datafile set", "details": details})
            self.emitter.trigger("datafile_set", details)
            return True
        except Exception as exc:
            self.report_diagnostic({"level": "error", "code": "invalid_datafile", "message": "Could not parse datafile", "originalError": exc})
            return False

    def set_sticky_features(self, sticky: dict[str, Any], replace: bool = False) -> None:
        if self.closed:
            return
        previous = self.sticky_features or {}
        self.sticky_features = dict(sticky) if replace else {**(self.sticky_features or {}), **sticky}
        self._state_version += 1
        params = get_params_for_sticky_features_set_event(previous, self.sticky_features, replace)
        self.report_diagnostic({"level": "info", "code": "sticky_features_set", "message": "Sticky features set", "details": params})
        self.emitter.trigger("sticky_features_set", params)

    def set_sticky_variables(self, sticky: dict[str, Any], replace: bool = False) -> None:
        if self.closed:
            return
        previous = self.sticky_variables or {}
        self.sticky_variables = dict(sticky) if replace else {**(self.sticky_variables or {}), **sticky}
        self._state_version += 1
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
        added = self.modules_manager.add(module)
        if added is not None:
            self._state_version += 1
        return added

    def remove_module(self, name: str) -> None:
        if self.closed:
            return
        self.modules_manager.remove(name)
        self._state_version += 1

    def on(self, event_name, callback):
        if self.closed:
            return lambda: None
        return self.emitter.on(event_name, callback)

    def close(self) -> None:
        with self._datafile_lock:
            if self.closed:
                return
            self.closed = True
        self.modules_manager.close_all()
        self.module_diagnostic_subscriptions = []
        self.datafile.close()
        self.emitter.clear_all()

    def set_context(self, context: dict[str, Any], replace: bool = False) -> None:
        if self.closed:
            return
        self.context = dict(context) if replace else {**self.context, **context}
        self._state_version += 1
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
            sticky_features=options.get("stickyFeatures"),
            sticky_variables=options.get("stickyVariables"),
        )

    def _get_evaluation_dependencies(self, context: dict[str, Any], options: dict[str, Any] | None = None) -> dict[str, Any]:
        options = options or {}
        child_version = options.get("__featurevisor_child_state_version")
        state_version = (lambda: (self._state_version, child_version())) if callable(child_version) else (lambda: self._state_version)
        generation = state_version()
        child_source = options.get("__featurevisor_child_sticky_source") or (lambda: (self.sticky_features, self.sticky_variables))
        child_initial = options.get("__featurevisor_child_initial_sticky", (self.sticky_features, self.sticky_variables))
        sticky_features = options.get("__featurevisor_child_sticky_features", self.sticky_features)
        sticky_variables = options.get("__featurevisor_child_sticky_variables", self.sticky_variables)
        dependencies = {
            "context": context if options.get("__featurevisor_context_is_effective") else self.get_context(context),
            "diagnostics": self.evaluation_diagnostics,
            "reportDiagnostic": self.report_diagnostic,
            "modulesManager": self.modules_manager,
            "datafile": self.datafile,
            "stickyFeatures": sticky_features,
            "stickyVariables": sticky_variables,
            "_evaluationSession": options.get("__featurevisor_session") or EvaluationSession(),
            "__featurevisor_state_version": state_version,
            "__featurevisor_child_state_version": child_version,
            "__featurevisor_child_sticky_source": child_source,
            "__featurevisor_child_initial_sticky": child_initial,
            "__featurevisor_datafile_source": lambda: self.datafile,
            "__featurevisor_initial_datafile": self.datafile,
        }
        # Fresh root inputs already carry current owner maps. Inherited child
        # and dependency inputs still need their identity based refresh below.
        if ("__featurevisor_child_sticky_source" not in options
                or options.get("__featurevisor_refreshed_state_version") == generation):
            dependencies["__featurevisor_refreshed_state_version"] = generation
        refresh_evaluation_state(dependencies)
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
                variable_type = (evaluation.get("variableSchema") or {}).get("type") or (evaluation.get("variable") or {}).get("type")
                if variable_type == "json" and isinstance(value, str) and "compose" not in (evaluation.get("variable") or {}):
                    return strict_json_loads(value)
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

    def get_variable_number(self, *args):
        """Return a number variable, whole or fractional, as a float."""
        return get_value_by_type(self.get_variable(*args), "number")

    def get_variable_array(self, *args):
        return get_value_by_type(self.get_variable(*args), "array")

    def get_variable_object(self, *args):
        return get_value_by_type(self.get_variable(*args), "object")

    def get_variable_json(self, *args):
        return get_value_by_type(self.get_variable(*args), "json")

    def _conditions_are_matched(self, conditions, dependencies) -> bool:
        try:
            return self._match_conditions(conditions, dependencies)
        except ConditionsParseFailure:
            return False

    def _match_conditions(self, conditions, dependencies) -> bool:
        if isinstance(conditions, InvalidConditions):
            raise ConditionsParseFailure()
        if conditions == "*":
            return True
        if isinstance(conditions, str):
            refresh_evaluation_state(dependencies)
            session = dependencies["_evaluationSession"]
            provider = dependencies["datafile"]
            segment = provider.get_segment(conditions)
            if segment is None:
                return False
            prepared = provider.get_attribute_selector(segment.get("conditions"))
            if prepared is not None:
                return provider.all_conditions_are_matched(prepared, dependencies["context"])
            if conditions in session.segments:
                raise RequirementInputError("Circular dependency")
            if len(session.segments) + len(session.active) - 1 >= 256:
                raise RequirementInputError("Evaluation dependency depth exceeded")
            session.segments.add(conditions)
            try:
                return self._match_conditions(segment.get("conditions"), dependencies)
            finally:
                session.segments.remove(conditions)
        if isinstance(conditions, dict):
            if "variable" in conditions:
                return self._variable_condition_is_matched(conditions, dependencies)
            if "feature" in conditions:
                flag = self._evaluate_dependency(conditions, dependencies, "flag")
                if (flag.get("enabled") is True) != conditions.get("enabled", True):
                    return False
                if "variation" in conditions:
                    variation = self._evaluate_dependency(conditions, dependencies, "variation")
                    actual = variation.get("variationValue", (variation.get("variation") or {}).get("value"))
                    return _strict_equal(actual, conditions["variation"])
                return True
            if "attribute" in conditions:
                return dependencies["datafile"].all_conditions_are_matched(conditions, dependencies["context"])
            for operator in ("and", "or", "not"):
                if isinstance(conditions.get(operator), list):
                    items = conditions[operator]
                    if not items:
                        return False
                    if operator == "or":
                        return any(self._match_conditions(item, dependencies) for item in items)
                    result = all(self._match_conditions(item, dependencies) for item in items)
                    return not result if operator == "not" else result
        if isinstance(conditions, list):
            return bool(conditions) and all(self._match_conditions(item, dependencies) for item in conditions)
        return False

    def _evaluate_dependency(self, reference, dependencies, type_, purpose="condition"):
        session = dependencies["_evaluationSession"]
        nested = {key: value for key, value in dependencies.items()
                  if key not in {"type", "featureKey", "variableKey", "defaultVariableValue",
                                 "defaultVariationValue", "dependencyOf", "dependencyPurpose"}}
        if reference.get("feature") is None and type_ == "variable":
            result = self._evaluate_global_variable(reference["variable"], dependencies["context"], {
                "__featurevisor_session": session, "__dependency_of": session.parent(),
                "__dependency_purpose": purpose, "__featurevisor_context_is_effective": True,
                "__featurevisor_child_sticky_features": dependencies.get("stickyFeatures"),
                "__featurevisor_child_sticky_variables": dependencies.get("stickyVariables"),
                "__featurevisor_child_state_version": dependencies.get("__featurevisor_child_state_version"),
                "__featurevisor_child_sticky_source": dependencies.get("__featurevisor_child_sticky_source"),
                "__featurevisor_child_initial_sticky": dependencies.get("__featurevisor_child_initial_sticky"),
            })
        else:
            result = evaluate_with_modules({
                **nested, "type": type_, "featureKey": reference["feature"],
                **({"variableKey": reference["variable"]} if type_ == "variable" else {}),
                "dependencyOf": session.parent(), "dependencyPurpose": purpose,
            })
        if isinstance(result.get("error"), RecursionError):
            raise RequirementInputError("Host recursion limit exceeded during dependency evaluation") from result["error"]
        if isinstance(result.get("error"), RequirementInputError):
            raise result["error"]
        return result

    def _evaluate_required_variable(self, required: dict[str, Any], dependencies: dict[str, Any], purpose="condition"):
        evaluation = self._evaluate_dependency(required, dependencies, "variable", purpose)
        if isinstance(evaluation.get("error"), RequirementInputError):
            raise evaluation["error"]
        if isinstance(evaluation, DependencySnapshot) and hasattr(evaluation, "getter_value"):
            return evaluation.getter_value
        value = evaluation.get("variableValue")
        variable_type = (evaluation.get("variableSchema") or {}).get("type") or (evaluation.get("variable") or {}).get("type")
        if variable_type == "json" and isinstance(value, str) and "compose" not in (evaluation.get("variable") or {}):
            try:
                value = strict_json_loads(value)
            except RecursionError as exc:
                raise RequirementInputError("Host recursion limit exceeded during dependency evaluation") from exc
            except Exception as exc:
                details = {"variableKey": required["variable"]}
                if "feature" in required:
                    details["featureKey"] = required["feature"]
                self.report_diagnostic({"level": "error", "code": "evaluation_error", "message": "getVariable failed", "originalError": exc, "details": details})
                value = None
        if isinstance(evaluation, DependencySnapshot):
            evaluation.getter_value = value
        return value

    def _variable_condition_is_matched(self, required, dependencies: dict[str, Any]) -> bool:
        value = self._evaluate_required_variable(required, dependencies)
        if "path" in required:
            if not isinstance(required["path"], list):
                raise RequirementInputError("Variable requirement path must be compiled")
            for part in required["path"]:
                if isinstance(part, str) and part in {"__proto__", "prototype", "constructor"}:
                    raise RequirementInputError("Unsafe variable requirement path")
                index = _array_index(part)
                if isinstance(part, (int, float)) and not isinstance(part, bool) and index is None:
                    raise RequirementInputError("Invalid variable requirement path")
                if index is not None:
                    value = value[index] if isinstance(value, list) and index < len(value) else MISSING
                elif isinstance(part, str):
                    if isinstance(value, dict):
                        value = value[part] if part in value else MISSING
                    elif isinstance(value, list) and part == "length":
                        value = len(value)
                    elif (
                        isinstance(value, list)
                        and part.isdigit()
                        and str(int(part)) == part
                        and int(part) < len(value)
                    ):
                        value = value[int(part)]
                    else:
                        value = MISSING
                else:
                    raise RequirementInputError("Invalid variable requirement path")
        try:
            return value_is_matched(value, required, self.datafile.get_regex)
        except Exception as exc:
            raise RequirementInputError(str(exc)) from exc

    def _evaluate_global_variable(self, variable_key: str, context: dict[str, Any], options: dict[str, Any]):
        session: EvaluationSession = options.get("__featurevisor_session") or EvaluationSession()
        options = {**options, "__featurevisor_session": session}
        identity = {"type": "variable", "variableKey": variable_key}
        try:
            with session.frame(identity):
                return self._evaluate_global_variable_impl(variable_key, context, options, session)
        except (RequirementInputError, RecursionError) as exc:
            if isinstance(exc, RecursionError):
                exc = RequirementInputError("Host recursion limit exceeded during dependency evaluation")
            return {**identity, "reason": "error", "error": exc}

    def _evaluate_global_variable_impl(self, variable_key: str, context: dict[str, Any], options: dict[str, Any], session: EvaluationSession):
        options = {
            **options,
            "__featurevisor_child_sticky_source": options.get("__featurevisor_child_sticky_source") or
                (lambda: (self.sticky_features, self.sticky_variables)),
            "__featurevisor_child_initial_sticky": options.get("__featurevisor_child_initial_sticky",
                                                               (self.sticky_features, self.sticky_variables)),
        }
        evaluation_options: dict[str, Any] = {
            "type": "variable",
            "variableKey": variable_key,
            "context": context if options.get("__featurevisor_context_is_effective") else self.get_context(context),
        }
        if "defaultVariableValue" in options:
            evaluation_options["defaultVariableValue"] = options["defaultVariableValue"]
        if "__dependency_of" in options:
            evaluation_options["dependencyOf"] = options["__dependency_of"]
            evaluation_options["dependencyPurpose"] = options.get("__dependency_purpose", "condition")
        child_version = options.get("__featurevisor_child_state_version")
        state_version = (lambda: (self._state_version, child_version())) if callable(child_version) else (lambda: self._state_version)
        evaluation_options["__featurevisor_state_version"] = state_version
        generation = state_version()
        session.retain(evaluation_options)
        sticky_features = options.get("__featurevisor_child_sticky_features", self.sticky_features)
        sticky_variables = options.get("__featurevisor_child_sticky_variables", self.sticky_variables)
        child_source = options.get("__featurevisor_child_sticky_source")
        child_initial = options.get("__featurevisor_child_initial_sticky")
        if callable(child_source) and isinstance(child_initial, tuple):
            current_features, current_variables = child_source()
            baseline = list(child_initial)
            if sticky_features is child_initial[0]:
                sticky_features = current_features
                baseline[0] = current_features
            if sticky_variables is child_initial[1]:
                sticky_variables = current_variables
                baseline[1] = current_variables
            options["__featurevisor_child_initial_sticky"] = tuple(baseline)
        # These inherited maps were just refreshed. Reuse that generation only
        # if callbacks and diagnostics leave both owners unchanged.
        options["__featurevisor_refreshed_state_version"] = generation
        # Outer requests cannot reuse a dependency memo entry.
        cache_key = session.key({
            **evaluation_options,
            "datafile": self.datafile,
            "modulesManager": self.modules_manager,
            "stickyFeatures": sticky_features,
            "stickyVariables": sticky_variables,
        }) if "dependencyOf" in evaluation_options else None
        cached = session.get(cache_key)
        if cached is not None:
            return cached
        input_options = evaluation_options
        try:
            had_modules = bool(self.modules_manager.modules)
            if had_modules:
                evaluation_options = self.modules_manager.run_before_evaluation_modules(
                    {key: value for key, value in evaluation_options.items() if not key.startswith("__featurevisor_")},
                    session.retain,
                )
            session.update(evaluation_options)
            resolved_key = evaluation_options["variableKey"]
            resolved_context = evaluation_options["context"]
            nested_options = {**options, "__dependency_of": session.parent(),
                              "__featurevisor_context_is_effective": True}
            variable = self.datafile.get_global_variable(resolved_key)
            evaluation: dict[str, Any] = {
                "type": "variable", "variableKey": resolved_key, "reason": "variable_not_found"
            }
            dependencies = {
                **self._get_evaluation_dependencies(resolved_context, {
                    **nested_options, "__featurevisor_session": session,
                    "__featurevisor_child_sticky_features": evaluation_options.get("stickyFeatures", sticky_features),
                    "__featurevisor_child_sticky_variables": evaluation_options.get("stickyVariables", sticky_variables),
                }),
                "type": "variable", "variableKey": resolved_key,
            }
            sticky = dependencies.get("stickyVariables") or {}

            if resolved_key in sticky:
                evaluation.update(reason="sticky", variable=variable, variableValue=sticky[resolved_key])
            elif variable:
                composed = "compose" in variable
                if "conditions" in variable and not self._conditions_are_matched(variable["conditions"], dependencies):
                    evaluation.update(reason="conditions_unmet", variable=variable, conditions=variable["conditions"])
                    value_key = "defaultValue" if not composed and variable.get("useDefaultWhenDisabled") else "disabledValue"
                    if value_key in variable:
                        value = variable[value_key]
                        evaluation["variableValue"] = dict(value) if isinstance(variable.get("compose"), list) and isinstance(value, dict) else value
                    elif composed:
                        evaluation["variableValue"] = None
                else:
                    source_value = self._compose_variable(variable["compose"], dependencies) if composed else variable.get("defaultValue")
                    program = variable.get("overrideProgram")
                    overrides = program if program is not None else (variable.get("overrides") or [])
                    accumulated_value = source_value
                    applied_paths: list[list[str]] = []
                    matched_index: int | None = None
                    matched_override: dict[str, Any] | None = None
                    index = 0
                    while index < len(overrides):
                        override = overrides[index]
                        matched = "conditions" not in override or self._conditions_are_matched(override["conditions"], dependencies)
                        if matched:
                            if program is not None:
                                accumulated_value = apply_variable_operations(accumulated_value, override.get("operations"))
                                applied_paths.append(override.get("keyPath") or [override.get("key")])
                            matched_index = index
                            matched_override = override
                            if program is None:
                                break
                        next_index = override.get("onMatch" if matched else "onMiss") if program is not None else index + 1
                        if program is not None and (
                            not isinstance(next_index, int) or isinstance(next_index, bool)
                            or next_index <= index or next_index > len(overrides)
                        ):
                            raise ValueError("Invalid variable override program jump")
                        index = next_index
                    if matched_override is not None:
                        evaluation.update(
                            reason="variable_override_rule",
                            variable=variable,
                            variableValue=accumulated_value if program is not None else matched_override.get("value"),
                            variableOverrideIndex=matched_index,
                        )
                        if "key" in matched_override:
                            evaluation["variableOverrideKey"] = matched_override["key"]
                        if "keyPath" in matched_override:
                            evaluation["variableOverridePath"] = matched_override["keyPath"]
                        if program is not None:
                            evaluation["variableOverridePaths"] = applied_paths
                    else:
                        evaluation.update(reason="variable_composed" if composed else "variable_default", variable=variable)
                        if composed or "defaultValue" in variable:
                            evaluation["variableValue"] = source_value
                    if isinstance(variable.get("compose"), list) and isinstance(evaluation.get("variableValue"), dict):
                        evaluation["variableValue"] = dict(evaluation["variableValue"])
                if variable.get("deprecated"):
                    self.report_diagnostic({
                        "level": "warn", "code": "deprecated_variable",
                        "message": f'Variable "{resolved_key}" is deprecated',
                        "details": {"variableKey": resolved_key, "evaluation": evaluation},
                    })

            if "variableValue" not in evaluation and "defaultVariableValue" in evaluation_options:
                evaluation["variableValue"] = evaluation_options["defaultVariableValue"]
            if self.modules_manager.modules:
                public_options = (evaluation_options if had_modules else {
                    key: value for key, value in evaluation_options.items()
                    if not key.startswith("__featurevisor_")
                })
                evaluation = self.modules_manager.run_after_evaluation_modules(evaluation, public_options)
            if self._diagnostic_is_observed("debug"):
                self.report_diagnostic({
                    "level": "debug", "code": str(evaluation["reason"]),
                    "message": "Global variable evaluated", "details": dict(evaluation),
                })
            post_key = session.key({**input_options, "datafile": self.datafile,
                                    "modulesManager": self.modules_manager,
                                    "stickyFeatures": sticky_features, "stickyVariables": sticky_variables}) if cache_key is not None else None
            if cache_key is not None and cache_key == post_key:
                snapshot = session.put(cache_key, evaluation)
                if snapshot is not None:
                    return snapshot
            return evaluation
        except Exception as exc:
            if isinstance(exc, RecursionError):
                exc = RequirementInputError("Host recursion limit exceeded during dependency evaluation")
            evaluation = {"type": "variable", "variableKey": evaluation_options["variableKey"], "reason": "error", "error": exc}
            if evaluation_options.get("dependencyOf") is None or not isinstance(exc, RequirementInputError):
                self.report_diagnostic({"level": "error", "code": "evaluation_error", "message": "Global variable evaluation failed", "originalError": exc, "details": evaluation})
            post_key = session.key({**input_options, "datafile": self.datafile,
                                    "modulesManager": self.modules_manager,
                                    "stickyFeatures": sticky_features, "stickyVariables": sticky_variables}) if cache_key is not None else None
            if cache_key is not None and cache_key == post_key:
                session.put(cache_key, evaluation)
            return evaluation

    def _compose_variable(self, compose, dependencies):
        if isinstance(compose, str) and compose:
            return self._evaluate_required_variable({"variable": compose}, dependencies, "composition")
        if not isinstance(compose, list) or not compose:
            raise RequirementInputError("Invalid composition")
        value = {}
        for reference in compose:
            key = reference.get("key")
            if not isinstance(key, str) or not key or key in {"__proto__", "constructor", "prototype"}:
                raise RequirementInputError("Invalid composition property")
            if "variable" in reference:
                member = self._evaluate_required_variable(reference, dependencies, "composition")
            elif "feature" in reference:
                type_ = "variation" if reference.get("variation") is True else "flag"
                result = self._evaluate_dependency(reference, dependencies, type_, "composition")
                member = (result.get("enabled") is True if type_ == "flag" else
                          result.get("variationValue", (result.get("variation") or {}).get("value")))
            else:
                raise RequirementInputError("Invalid composition source")
            value[key] = member
        return value

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
        level = (diagnostic or {}).get("level") or "info"
        if not self._diagnostic_is_observed(level, source_module):
            return
        diagnostic = dict(diagnostic or {})
        diagnostic["level"] = level
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

    def _diagnostic_is_observed(
        self,
        diagnostic_level: str,
        source_module: FeaturevisorModule | None = None,
    ) -> bool:
        if diagnostic_level == "error":
            return True
        for subscription in self.module_diagnostic_subscriptions:
            if source_module and subscription["moduleId"] == source_module.id:
                continue
            if self._should_report_diagnostic(diagnostic_level, subscription["level"]):
                return True
        return self._should_report_diagnostic(diagnostic_level, self.log_level)

    def _should_report_diagnostic(self, diagnostic_level: str, subscriber_level: str) -> bool:
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
    getVariableNumber = get_variable_number
    getVariableArray = get_variable_array
    getVariableObject = get_variable_object
    getVariableJSON = get_variable_json
    getFeatureEvaluations = get_feature_evaluations
    getVariableEvaluations = get_variable_evaluations


def create_featurevisor(options: dict[str, Any] | None = None) -> Featurevisor:
    return Featurevisor(options or {})
