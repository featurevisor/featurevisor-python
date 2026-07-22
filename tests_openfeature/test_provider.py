from __future__ import annotations

import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import Reason

from featurevisor import create_featurevisor
from featurevisor.openfeature import FeaturevisorOpenFeatureProvider


def feature(**overrides):
    result = {
        "bucketBy": "userId",
        "traffic": [{"key": "everyone", "segments": "*", "percentage": 100000, "variation": "on"}],
    }
    result.update(overrides)
    return result


def datafile():
    return {
        "schemaVersion": "2",
        "revision": "revision-1",
        "featurevisorVersion": "3.0.1",
        "segments": {},
        "features": {
            "checkout": feature(
                variations=[
                    {
                        "value": "on",
                        "variables": {
                            "title": "Hello",
                            "count": 3,
                            "ratio": 1.5,
                            "visible": True,
                            "items": ["a", "b"],
                            "config": {"color": "blue"},
                            "json": '{"nested":true}',
                            "invalidJson": "not-json",
                        },
                    }
                ],
                variablesSchema={
                    "title": {"type": "string", "defaultValue": "Default"},
                    "count": {"type": "integer", "defaultValue": 0},
                    "ratio": {"type": "double", "defaultValue": 0},
                    "visible": {"type": "boolean", "defaultValue": False},
                    "items": {"type": "array", "defaultValue": []},
                    "config": {"type": "object", "defaultValue": {}},
                    "json": {"type": "json", "defaultValue": "{}"},
                    "invalidJson": {"type": "json", "defaultValue": "{}"},
                },
            ),
            "disabled": feature(
                disabledVariationValue="off",
                variations=[{"value": "on"}],
                force=[
                    {
                        "conditions": {"attribute": "blocked", "operator": "equals", "value": True},
                        "enabled": False,
                    }
                ],
            ),
            "emptyVariation": feature(variations=[]),
        },
    }


class OpenFeatureProviderTest(unittest.TestCase):
    def tearDown(self) -> None:
        api.shutdown()

    def provider(self, **kwargs):
        return FeaturevisorOpenFeatureProvider({"datafile": datafile(), "logLevel": "fatal"}, **kwargs)

    def test_resolves_flags_variations_and_every_openfeature_type(self):
        provider = self.provider()
        context = EvaluationContext(targeting_key="user-1")

        flag = provider.resolve_boolean_details("checkout", False, context)
        self.assertTrue(flag.value)
        self.assertEqual(flag.reason, Reason.TARGETING_MATCH)

        variation = provider.resolve_string_details("checkout:variation", "fallback", context)
        self.assertEqual(variation.value, "on")
        self.assertEqual(variation.variant, "on")
        self.assertEqual(variation.reason, Reason.TARGETING_MATCH)

        self.assertEqual(provider.resolve_string_details("checkout:title", "fallback", context).value, "Hello")
        self.assertEqual(provider.resolve_integer_details("checkout:count", 0, context).value, 3)
        self.assertEqual(provider.resolve_float_details("checkout:ratio", 0.0, context).value, 1.5)
        self.assertTrue(provider.resolve_boolean_details("checkout:visible", False, context).value)
        self.assertEqual(provider.resolve_object_details("checkout:items", [], context).value, ["a", "b"])
        self.assertEqual(provider.resolve_object_details("checkout:config", {}, context).value, {"color": "blue"})
        self.assertEqual(provider.resolve_object_details("checkout:json", {}, context).value, {"nested": True})

    def test_maps_targeting_key_dates_arrays_and_nested_context_without_mutation(self):
        contexts = []
        created_at = datetime(2026, 1, 2, 4, 4, 5, tzinfo=timezone(timedelta(hours=1)))
        nested_date = datetime(2026, 1, 1)
        attributes = {
            "createdAt": created_at,
            "nested": {"dates": [nested_date]},
        }
        provider = FeaturevisorOpenFeatureProvider(
            {
                "datafile": datafile(),
                "logLevel": "fatal",
                "modules": [
                    {
                        "name": "capture",
                        "before": lambda options: contexts.append(options["context"]) or options,
                    }
                ],
            },
            targeting_key_field="accountId",
        )

        provider.resolve_boolean_details(
            "checkout",
            False,
            EvaluationContext(targeting_key="subject", attributes=attributes),
        )

        self.assertEqual(
            contexts[0],
            {
                "accountId": "subject",
                "createdAt": "2026-01-02T03:04:05.000Z",
                "nested": {"dates": ["2026-01-01T00:00:00.000Z"]},
            },
        )
        self.assertIs(attributes["createdAt"], created_at)
        self.assertIs(attributes["nested"]["dates"][0], nested_date)

    def test_supports_custom_key_separator_and_variation_selector(self):
        provider = self.provider(key_separator="/", variation_key="$variation")
        self.assertEqual(provider.resolve_string_details("checkout/$variation", "fallback").value, "on")
        self.assertEqual(provider.resolve_string_details("checkout/title", "fallback").value, "Hello")

    def test_returns_defaults_and_standard_errors_for_missing_entities_and_malformed_datafiles(self):
        provider = self.provider()

        missing_feature = provider.resolve_boolean_details("missing", True)
        self.assertTrue(missing_feature.value)
        self.assertEqual(missing_feature.reason, Reason.ERROR)
        self.assertEqual(missing_feature.error_code, ErrorCode.FLAG_NOT_FOUND)

        missing_variable = provider.resolve_string_details("checkout:missing", "fallback")
        self.assertEqual(missing_variable.value, "fallback")
        self.assertEqual(missing_variable.error_code, ErrorCode.FLAG_NOT_FOUND)

        no_variations = provider.resolve_string_details("emptyVariation:variation", "fallback")
        self.assertEqual(no_variations.value, "fallback")
        self.assertEqual(no_variations.error_code, ErrorCode.FLAG_NOT_FOUND)

        malformed = FeaturevisorOpenFeatureProvider({"datafile": "{", "logLevel": "fatal"})
        result = malformed.resolve_boolean_details("checkout", False)
        self.assertFalse(result.value)
        self.assertEqual(result.reason, Reason.ERROR)
        self.assertEqual(result.error_code, ErrorCode.PARSE_ERROR)
        self.assertEqual(result.error_message, "Could not parse datafile")

    def test_recovers_after_a_malformed_datafile_is_replaced(self):
        provider = FeaturevisorOpenFeatureProvider({"datafile": "{", "logLevel": "fatal"})
        self.assertEqual(provider.resolve_boolean_details("checkout", False).error_code, ErrorCode.PARSE_ERROR)

        provider.featurevisor.set_datafile(datafile(), replace=True)
        result = provider.resolve_boolean_details("checkout", False, EvaluationContext(targeting_key="user"))
        self.assertTrue(result.value)
        self.assertIsNone(result.error_code)

    def test_rejects_mismatched_values_non_finite_numbers_and_invalid_json(self):
        provider = self.provider()

        cases = [
            provider.resolve_string_details("checkout", "no"),
            provider.resolve_boolean_details("checkout:title", False),
            provider.resolve_object_details("checkout:invalidJson", {}),
            provider.resolve_integer_details("checkout:ratio", 0),
        ]
        for result in cases:
            self.assertEqual(result.reason, Reason.ERROR)
            self.assertEqual(result.error_code, ErrorCode.TYPE_MISMATCH)

        self.assertEqual(provider.resolve_float_details("checkout:count", math.nan).value, 3)

        for value, resolver in [
            (math.nan, provider.resolve_float_details),
            (math.inf, provider.resolve_float_details),
            (-math.inf, provider.resolve_float_details),
            (True, provider.resolve_integer_details),
            (True, provider.resolve_float_details),
        ]:
            with self.subTest(value=value, resolver=resolver.__name__):
                evaluation = {
                    "type": "variable",
                    "featureKey": "checkout",
                    "variableKey": "ratio",
                    "reason": "allocated",
                    "variableValue": value,
                    "variableSchema": {"type": "double"},
                }
                with patch.object(provider.featurevisor, "evaluate_variable", return_value=evaluation):
                    result = resolver("checkout:ratio", 0)
                self.assertEqual(result.value, 0)
                self.assertEqual(result.reason, Reason.ERROR)
                self.assertEqual(result.error_code, ErrorCode.TYPE_MISMATCH)

    def test_maps_disabled_evaluations(self):
        provider = self.provider()
        context = EvaluationContext(attributes={"blocked": True})

        flag = provider.resolve_boolean_details("disabled", True, context)
        self.assertFalse(flag.value)
        self.assertEqual(flag.reason, Reason.TARGETING_MATCH)

        variation = provider.resolve_string_details("disabled:variation", "fallback", context)
        self.assertEqual(variation.value, "off")
        self.assertEqual(variation.reason, Reason.DISABLED)

    def test_maps_all_featurevisor_reasons(self):
        mappings = {
            "required": Reason.TARGETING_MATCH,
            "forced": Reason.TARGETING_MATCH,
            "sticky": Reason.TARGETING_MATCH,
            "rule": Reason.TARGETING_MATCH,
            "variable_override_variation": Reason.TARGETING_MATCH,
            "variable_override_rule": Reason.TARGETING_MATCH,
            "allocated": Reason.SPLIT,
            "disabled": Reason.DISABLED,
            "variation_disabled": Reason.DISABLED,
            "variable_disabled": Reason.DISABLED,
            "out_of_range": Reason.DEFAULT,
            "no_match": Reason.DEFAULT,
            "variable_default": Reason.DEFAULT,
        }

        for featurevisor_reason, expected_reason in mappings.items():
            with self.subTest(featurevisor_reason=featurevisor_reason):
                featurevisor = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
                provider = FeaturevisorOpenFeatureProvider(featurevisor=featurevisor)
                evaluation = {
                    "type": "flag",
                    "featureKey": "checkout",
                    "reason": featurevisor_reason,
                    "enabled": True,
                }
                with patch.object(featurevisor, "evaluate_flag", return_value=evaluation):
                    result = provider.resolve_boolean_details("checkout", False)
                self.assertEqual(result.reason, expected_reason)
                self.assertIsNone(result.error_code)
                provider.shutdown()
                featurevisor.close()

    def test_maps_general_evaluation_errors(self):
        featurevisor = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        provider = FeaturevisorOpenFeatureProvider(featurevisor=featurevisor)
        evaluation = {
            "type": "flag",
            "featureKey": "checkout",
            "reason": "error",
            "error": RuntimeError("Evaluation failed"),
        }

        with patch.object(featurevisor, "evaluate_flag", return_value=evaluation):
            result = provider.resolve_boolean_details("checkout", False)

        self.assertFalse(result.value)
        self.assertEqual(result.reason, Reason.ERROR)
        self.assertEqual(result.error_code, ErrorCode.GENERAL)
        self.assertEqual(result.error_message, "Evaluation failed")

    def test_returns_stable_featurevisor_metadata(self):
        result = self.provider().resolve_string_details(
            "checkout:title",
            "fallback",
            EvaluationContext(targeting_key="u"),
        )
        self.assertEqual(result.flag_metadata["featureKey"], "checkout")
        self.assertEqual(result.flag_metadata["variableKey"], "title")
        self.assertEqual(result.flag_metadata["featurevisorReason"], "allocated")
        self.assertEqual(result.flag_metadata["revision"], "revision-1")
        self.assertEqual(result.flag_metadata["schemaVersion"], "2")

    def test_includes_all_metadata_and_selected_variant(self):
        featurevisor = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        provider = FeaturevisorOpenFeatureProvider(featurevisor=featurevisor)
        evaluation = {
            "type": "variation",
            "featureKey": "checkout",
            "variableKey": "title",
            "reason": "allocated",
            "ruleKey": "rule-1",
            "bucketKey": "checkout.user-1",
            "bucketValue": 0,
            "forceIndex": 0,
            "variableOverrideIndex": 0,
            "variationValue": "on",
        }

        with patch.object(featurevisor, "evaluate_variation", return_value=evaluation):
            result = provider.resolve_string_details("checkout:variation", "fallback")

        self.assertEqual(result.variant, "on")
        self.assertEqual(
            result.flag_metadata,
            {
                "featureKey": "checkout",
                "variableKey": "title",
                "featurevisorReason": "allocated",
                "revision": "revision-1",
                "schemaVersion": "2",
                "ruleKey": "rule-1",
                "bucketKey": "checkout.user-1",
                "bucketValue": 0,
                "forceIndex": 0,
                "variableOverrideIndex": 0,
            },
        )

    def test_forwards_tracking_and_closes_an_owned_instance_idempotently(self):
        events = []
        closed = []
        provider = FeaturevisorOpenFeatureProvider(
            {
                "datafile": datafile(),
                "logLevel": "fatal",
                "modules": [{"name": "close", "close": lambda: closed.append(True)}],
            },
            on_track=lambda name, context, details: events.append((name, context, details)),
        )
        context = EvaluationContext(targeting_key="u")
        provider.track("checkout", context, None)
        self.assertEqual(events, [("checkout", context, None)])

        provider.shutdown()
        provider.shutdown()
        self.assertEqual(closed, [True])

    def test_reuses_but_does_not_close_a_caller_owned_instance(self):
        closed = []
        featurevisor = create_featurevisor(
            {
                "datafile": datafile(),
                "logLevel": "fatal",
                "modules": [{"name": "owner", "close": lambda: closed.append(True)}],
            }
        )
        provider = FeaturevisorOpenFeatureProvider({"datafile": "{"}, featurevisor=featurevisor)

        self.assertIs(provider.featurevisor, featurevisor)
        self.assertTrue(provider.resolve_boolean_details("checkout", False, EvaluationContext(targeting_key="user")).value)
        provider.shutdown()
        self.assertEqual(closed, [])

        featurevisor.set_datafile({**datafile(), "features": {}}, replace=True)
        self.assertEqual(featurevisor.evaluate_flag("checkout")["reason"], "feature_not_found")
        featurevisor.close()
        self.assertEqual(closed, [True])

    def test_works_through_openfeature_api(self):
        provider = self.provider()
        api.set_provider_and_wait(provider)
        client = api.get_client()
        self.assertTrue(client.get_boolean_value("checkout", False, EvaluationContext(targeting_key="user")))


if __name__ == "__main__":
    unittest.main()
