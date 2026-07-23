from __future__ import annotations

from typing import Any, Callable

LOG_LEVELS = ["fatal", "error", "warn", "info", "debug"]
DEFAULT_LOG_LEVEL = "info"


def should_report(configured_level: str, diagnostic_level: str) -> bool:
    try:
        return LOG_LEVELS.index(configured_level) >= LOG_LEVELS.index(diagnostic_level)
    except ValueError:
        return False


def write_diagnostic_to_console(diagnostic: dict[str, Any]) -> None:
    print("[Featurevisor]", diagnostic.get("message") or diagnostic.get("code") or "diagnostic", diagnostic)


class _EvaluationDiagnostics:
    """Evaluator adapter that emits structured diagnostics directly."""

    def __init__(self, report: Callable[[dict[str, Any]], None]) -> None:
        self._report = report

    def _emit(self, level: str, message: str, details: dict[str, Any] | None = None) -> None:
        details = dict(details or {})
        explicit_code = details.pop("code", None)
        original_error = details.pop("originalError", details.pop("error", None))
        code_by_message = {
            "feature is deprecated": "deprecated_feature",
            "variable is deprecated": "deprecated_variable",
            "feature not found": "feature_not_found",
            "variable schema not found": "variable_not_found",
            "no variations": "no_variations",
            "invalid bucketBy": "invalid_bucket_by",
            "Error during evaluation": "evaluation_error",
            "Error parsing conditions": "conditions_parse_error",
        }
        code = str(explicit_code or code_by_message.get(message, details.get("reason") or message))
        nested_details = details.pop("details", None)
        if isinstance(nested_details, dict):
            details.update(nested_details)
        evaluation = dict(details) if "featureKey" in details and "reason" in details else None
        if evaluation is not None:
            details = {
                "featureKey": evaluation.get("featureKey"),
                "variableKey": evaluation.get("variableKey"),
                "reason": evaluation.get("reason"),
                "evaluation": evaluation,
            }
        diagnostic: dict[str, Any] = {"level": level, "code": code, "message": message, "details": details}
        if original_error is not None:
            diagnostic["originalError"] = original_error
        self._report(diagnostic)

    def debug(self, message: str, details: dict[str, Any] | None = None) -> None:
        self._emit("debug", message, details)

    def warn(self, message: str, details: dict[str, Any] | None = None) -> None:
        self._emit("warn", message, details)

    def error(self, message: str, details: dict[str, Any] | None = None) -> None:
        self._emit("error", message, details)


def _create_evaluation_diagnostics() -> _EvaluationDiagnostics:
    return _EvaluationDiagnostics(lambda diagnostic: None)
