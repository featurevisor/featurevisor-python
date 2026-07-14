from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Callable

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagResolutionDetails, Reason
from openfeature.provider import AbstractProvider
from openfeature.provider.metadata import Metadata
from openfeature.track import TrackingEventDetails

from .instance import Featurevisor, create_featurevisor


class FeaturevisorOpenFeatureProvider(AbstractProvider):
    """OpenFeature provider backed by the Featurevisor v3 SDK."""

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        *,
        featurevisor: Featurevisor | None = None,
        targeting_key_field: str = "userId",
        key_separator: str = ":",
        variation_key: str = "variation",
        on_track: Callable[[str, EvaluationContext | None, TrackingEventDetails | None], None] | None = None,
    ) -> None:
        super().__init__()
        self.targeting_key_field = targeting_key_field or "userId"
        self.key_separator = key_separator or ":"
        self.variation_key = variation_key or "variation"
        self.on_track = on_track
        self.datafile_error: str | None = None
        self._owns_featurevisor = featurevisor is None
        if featurevisor is not None:
            self.featurevisor = featurevisor
        else:
            featurevisor_options = dict(options or {})
            datafile = featurevisor_options.get("datafile")
            if isinstance(datafile, str):
                try:
                    json.loads(datafile)
                except (TypeError, ValueError):
                    self.datafile_error = "Could not parse datafile"
            original_handler = featurevisor_options.get("onDiagnostic") or featurevisor_options.get("on_diagnostic")

            def on_diagnostic(diagnostic: dict[str, Any]) -> None:
                if diagnostic.get("code") == "invalid_datafile":
                    self.datafile_error = str(diagnostic.get("message"))
                if diagnostic.get("code") == "datafile_set":
                    self.datafile_error = None
                if original_handler:
                    original_handler(diagnostic)

            featurevisor_options["onDiagnostic"] = on_diagnostic
            self.featurevisor = create_featurevisor(featurevisor_options)
        self._datafile_unsubscribe = self.featurevisor.on("datafile_set", lambda _: setattr(self, "datafile_error", None))

    def get_metadata(self) -> Metadata:
        return Metadata(name="Featurevisor")

    def shutdown(self) -> None:
        self._datafile_unsubscribe()
        if self._owns_featurevisor:
            self.featurevisor.close()

    def track(
        self,
        tracking_event_name: str,
        evaluation_context: EvaluationContext | None = None,
        tracking_event_details: TrackingEventDetails | None = None,
    ) -> None:
        if self.on_track:
            self.on_track(tracking_event_name, evaluation_context, tracking_event_details)

    def resolve_boolean_details(self, flag_key: str, default_value: bool, evaluation_context: EvaluationContext | None = None) -> FlagResolutionDetails[bool]:
        return self._resolve(flag_key, default_value, evaluation_context, "boolean")

    def resolve_string_details(self, flag_key: str, default_value: str, evaluation_context: EvaluationContext | None = None) -> FlagResolutionDetails[str]:
        return self._resolve(flag_key, default_value, evaluation_context, "string")

    def resolve_integer_details(self, flag_key: str, default_value: int, evaluation_context: EvaluationContext | None = None) -> FlagResolutionDetails[int]:
        return self._resolve(flag_key, default_value, evaluation_context, "integer")

    def resolve_float_details(self, flag_key: str, default_value: float, evaluation_context: EvaluationContext | None = None) -> FlagResolutionDetails[float]:
        return self._resolve(flag_key, default_value, evaluation_context, "number")

    def resolve_object_details(self, flag_key: str, default_value: Sequence[Any] | Mapping[str, Any], evaluation_context: EvaluationContext | None = None) -> FlagResolutionDetails[Sequence[Any] | Mapping[str, Any]]:
        return self._resolve(flag_key, default_value, evaluation_context, "object")

    def _resolve(self, flag_key: str, default_value: Any, evaluation_context: EvaluationContext | None, expected_type: str) -> FlagResolutionDetails[Any]:
        if self.datafile_error:
            return self._error(default_value, ErrorCode.PARSE_ERROR, self.datafile_error)

        feature_key, separator, selector = flag_key.partition(self.key_separator)
        selector = selector if separator else None
        context = self._context(evaluation_context)

        if not selector:
            if expected_type != "boolean":
                return self._type_mismatch(flag_key, default_value, expected_type)
            evaluation = self.featurevisor.evaluate_flag(feature_key, context)
            value = evaluation.get("enabled")
        elif selector == self.variation_key:
            evaluation = self.featurevisor.evaluate_variation(feature_key, context)
            value = evaluation.get("variationValue")
            if value is None and evaluation.get("variation"):
                value = evaluation["variation"].get("value")
        else:
            evaluation = self.featurevisor.evaluate_variable(feature_key, selector, context)
            value = evaluation.get("variableValue")
            if evaluation.get("variableSchema", {}).get("type") == "json" and isinstance(value, str):
                try:
                    value = json.loads(value)
                except (TypeError, ValueError):
                    pass

        metadata = self._metadata(evaluation)
        error_code = self._error_code(evaluation.get("reason"))
        if error_code:
            return self._error(default_value, error_code, self._error_message(evaluation), metadata)
        if value is None:
            value = default_value
        elif not self._matches(value, expected_type):
            return self._type_mismatch(flag_key, default_value, expected_type, metadata)

        return FlagResolutionDetails(
            value=value,
            variant=self._variant(evaluation),
            reason=self._reason(evaluation.get("reason")),
            flag_metadata=metadata,
        )

    def _context(self, context: EvaluationContext | None) -> dict[str, Any]:
        result = self._normalize(dict(context.attributes)) if context else {}
        if context and context.targeting_key:
            result[self.targeting_key_field] = context.targeting_key
        return result

    def _metadata(self, evaluation: dict[str, Any]) -> dict[str, bool | int | float | str]:
        metadata: dict[str, bool | int | float | str] = {
            "featureKey": evaluation["featureKey"],
            "featurevisorReason": evaluation["reason"],
            "schemaVersion": self.featurevisor.get_schema_version(),
        }
        revision = self.featurevisor.get_revision()
        if revision:
            metadata["revision"] = revision
        for key in ("variableKey", "ruleKey", "bucketKey", "bucketValue", "forceIndex", "variableOverrideIndex"):
            if evaluation.get(key) is not None:
                metadata[key] = evaluation[key]
        return metadata

    @staticmethod
    def _reason(reason: str | None) -> Reason:
        if reason in {"feature_not_found", "variable_not_found", "no_variations", "error"}:
            return Reason.ERROR
        if reason in {"required", "forced", "sticky", "rule", "variable_override_variation", "variable_override_rule"}:
            return Reason.TARGETING_MATCH
        if reason == "allocated":
            return Reason.SPLIT
        if reason in {"disabled", "variation_disabled", "variable_disabled"}:
            return Reason.DISABLED
        return Reason.DEFAULT

    @staticmethod
    def _error_code(reason: str | None) -> ErrorCode | None:
        if reason in {"feature_not_found", "variable_not_found", "no_variations"}:
            return ErrorCode.FLAG_NOT_FOUND
        if reason == "error":
            return ErrorCode.GENERAL
        return None

    @staticmethod
    def _error_message(evaluation: dict[str, Any]) -> str:
        error = evaluation.get("error")
        if error:
            return str(error)
        if evaluation.get("reason") == "feature_not_found":
            return f'Feature "{evaluation["featureKey"]}" was not found'
        if evaluation.get("reason") == "variable_not_found":
            return f'Variable "{evaluation.get("variableKey")}" was not found for feature "{evaluation["featureKey"]}"'
        if evaluation.get("reason") == "no_variations":
            return f'Feature "{evaluation["featureKey"]}" has no variations'
        return "Featurevisor evaluation failed"

    @staticmethod
    def _variant(evaluation: dict[str, Any]) -> str | None:
        if evaluation.get("variationValue") is not None:
            return str(evaluation["variationValue"])
        if evaluation.get("variation"):
            return str(evaluation["variation"].get("value"))
        return None

    @staticmethod
    def _matches(value: Any, expected_type: str) -> bool:
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "string":
            return isinstance(value, str)
        if expected_type == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if expected_type == "number":
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        return isinstance(value, (dict, list, tuple))

    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Mapping):
            return {key: cls._normalize(item) for key, item in value.items()}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return [cls._normalize(item) for item in value]
        return value

    @staticmethod
    def _error(value: Any, code: ErrorCode, message: str, metadata: Mapping[str, Any] | None = None) -> FlagResolutionDetails[Any]:
        return FlagResolutionDetails(value=value, reason=Reason.ERROR, error_code=code, error_message=message, flag_metadata=metadata or {})

    @classmethod
    def _type_mismatch(cls, flag_key: str, value: Any, expected_type: str, metadata: Mapping[str, Any] | None = None) -> FlagResolutionDetails[Any]:
        return cls._error(value, ErrorCode.TYPE_MISMATCH, f'Flag "{flag_key}" did not resolve to a {expected_type} value', metadata)
