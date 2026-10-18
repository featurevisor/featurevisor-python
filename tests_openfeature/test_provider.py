from __future__ import annotations

import math
import json
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from threading import Event
from unittest.mock import patch

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode, ParseError
from openfeature.event import ProviderEvent
from openfeature.flag_evaluation import Reason
from openfeature.track import TrackingEventDetails

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
        "variables": {
            "supportEmail": {
                "type": "string", "defaultValue": "support@example.com",
                "overrides": [{
                    "key": "netherlands", "keyPath": ["europe", "netherlands"],
                    "conditions": {"attribute": "country", "operator": "equals", "value": "nl"},
                    "value": "nl@example.com",
                }],
            },
            "limits": {"type": "object", "defaultValue": {"requests": 10}},
        },
    }


class OpenFeatureProviderTest(unittest.TestCase):
    def test_owned_provider_still_suppresses_default_console_diagnostics(self):
        with patch("featurevisor.instance.write_diagnostic_to_console") as console:
            provider = FeaturevisorOpenFeatureProvider({"datafile": "{"})
            self.assertEqual(provider.resolve_boolean_details("checkout", False).error_code, ErrorCode.PARSE_ERROR)
            provider.featurevisor.set_datafile(datafile(), True)
            provider.shutdown()
            console.assert_not_called()

    def test_initial_health_is_independent_of_logging_and_preserves_options(self):
        invalid = {"schemaVersion": "2", "revision": "x", "features": [], "segments": {}}
        for level in ("info", "error", "fatal"):
            for value in (invalid, json.dumps(invalid), "{", "null", {}, False,
                          {**datafile(), "variables": []}):
                for callback_key in ("onDiagnostic", "on_diagnostic"):
                    with self.subTest(level=level, value=value, callback_key=callback_key):
                        diagnostics, revisions, closed = [], [], []
                        callback = diagnostics.append
                        modules = [{"setup": lambda module: revisions.append(module["getRevision"]()),
                                    "close": lambda: closed.append(True)}]
                        options = {"datafile": value, "logLevel": level, callback_key: callback,
                                   "modules": modules, "context": {"country": "nl"}}
                        provider = FeaturevisorOpenFeatureProvider(options)
                        self.assertEqual(revisions, ["unknown"])
                        self.assertIs(options[callback_key], callback)
                        self.assertIs(options["modules"], modules)
                        self.assertEqual(provider.resolve_boolean_details("checkout", True).error_code, ErrorCode.PARSE_ERROR)
                        with self.assertRaises(ParseError):
                            provider.initialize(EvaluationContext())
                        self.assertEqual(sum(d["code"] == "invalid_datafile" for d in diagnostics), 0 if level == "fatal" else 1)
                        events = []
                        provider.attach(lambda _, event, details: events.append((event, details.error_code)))
                        provider.featurevisor.set_datafile(datafile(), True)
                        provider.initialize(EvaluationContext())
                        self.assertEqual(provider.resolve_string_details("variable:supportEmail", "").value, "nl@example.com")
                        provider.featurevisor.set_datafile(invalid, True)
                        self.assertEqual(events, [(ProviderEvent.PROVIDER_READY, None),
                                                  (ProviderEvent.PROVIDER_ERROR, ErrorCode.PARSE_ERROR)])
                        provider.shutdown()
                        provider.shutdown()
                        self.assertEqual(closed, [True])

    def test_invalid_owned_registration_fails_and_recovers_through_api(self):
        provider = FeaturevisorOpenFeatureProvider({"datafile": {"schemaVersion": "2", "revision": "x", "features": [], "segments": {}}, "logLevel": "fatal"})
        with self.assertRaises(ParseError):
            api.set_provider_and_wait(provider)
        provider.featurevisor.set_datafile(datafile(), True)
        self.assertTrue(api.get_client().get_boolean_value("checkout", False))

    def test_client_preserves_explicit_empty_targeting_key(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] / "conformance/openfeature-provider.json").read_text())
        provider = FeaturevisorOpenFeatureProvider({"datafile": fixture["datafile"], "logLevel": "fatal"})
        api.set_provider_and_wait(provider)
        client = api.get_client()
        fields = {"userId": "forced"}
        self.assertTrue(client.get_boolean_value("targetOnly", False, EvaluationContext(attributes=fields)))
        self.assertTrue(client.get_boolean_value("targetOnly", False, EvaluationContext(targeting_key="", attributes=fields)))
        self.assertFalse(client.get_boolean_value("targetOnly", False, EvaluationContext(attributes={"userId": ""})))
        self.assertFalse(client.get_boolean_value("targetOnly", False, EvaluationContext(targeting_key="other", attributes=fields)))
        self.assertEqual(fields, {"userId": "forced"})
        custom = FeaturevisorOpenFeatureProvider({"datafile": fixture["datafile"], "logLevel": "fatal"}, targeting_key_field="accountId")
        api.set_provider_and_wait(custom)
        account = {"accountId": "old"}
        self.assertFalse(api.get_client().get_boolean_value("accountTargeted", False, EvaluationContext(targeting_key="", attributes=account)))
        self.assertTrue(api.get_client().get_boolean_value("accountTargeted", False, EvaluationContext(attributes={"accountId": ""})))
        self.assertEqual(account, {"accountId": "old"})

    def test_shared_provider_conformance(self):
        fixture = json.loads((Path(__file__).resolve().parents[1] / "conformance/openfeature-provider.json").read_text())
        self.assertEqual(fixture["kind"], "featurevisor.openfeature-provider")
        self.assertEqual(fixture["schemaVersion"], 1)
        self.assertEqual(len(fixture["cases"]), 16)
        provider = FeaturevisorOpenFeatureProvider({"datafile": fixture["datafile"], "logLevel": "fatal"})
        resolvers = {
            "boolean": provider.resolve_boolean_details,
            "string": provider.resolve_string_details,
            "object": provider.resolve_object_details,
        }
        api.set_provider_and_wait(provider)
        client = api.get_client()
        client_resolvers = {"boolean": client.get_boolean_details,
                            "string": client.get_string_details,
                            "object": client.get_object_details}
        for case in fixture["cases"]:
            with self.subTest(case=case["name"]):
                source = json.loads(json.dumps(case["context"]))
                context = EvaluationContext(targeting_key=source.pop("targetingKey", None), attributes=source)
                result = resolvers[case["type"]](case["flagKey"], case["default"], context)
                self.assertEqual(result.value, case["expected"]["value"])
                if "reason" in case["expected"]:
                    self.assertEqual(result.reason.value if hasattr(result.reason, "value") else result.reason, case["expected"]["reason"])
                self.assertEqual(result.error_code.value if result.error_code else None, case["expected"].get("errorCode"))
                if "variant" in case["expected"]:
                    self.assertEqual(result.variant, case["expected"]["variant"])
                client_result = client_resolvers[case["type"]](case["flagKey"], case["default"], context)
                self.assertEqual(client_result.value, case["expected"]["value"])
                self.assertEqual(client_result.error_code, result.error_code)
                self.assertEqual(client_result.reason, result.reason)
                self.assertEqual(client_result.variant, result.variant)
                self.assertEqual(context.attributes, source)
        for case in fixture["targetingCases"]:
            with self.subTest(case=case["name"]):
                source = json.loads(json.dumps(case["context"]))
                fields = source.copy()
                key = fields.pop("targetingKey", None)
                context = EvaluationContext(targeting_key=key, attributes=fields)
                result = provider.resolve_boolean_details("targetOnly", False, context)
                self.assertEqual(result.value, case["expectedValue"])
                self.assertEqual(context.attributes, fields)
        provider.shutdown()
        custom = FeaturevisorOpenFeatureProvider({"datafile": fixture["datafile"], "logLevel": "fatal"}, targeting_key_field="accountId")
        case = fixture["customTargetingCase"]
        fields = case["context"].copy()
        key = fields.pop("targetingKey")
        result = custom.resolve_boolean_details(case["flagKey"], False, EvaluationContext(targeting_key=key, attributes=fields))
        self.assertEqual(result.value, case["expectedValue"])
        custom.shutdown()

    def test_borrowed_instance_parse_error_recovery_and_cleanup(self):
        f = create_featurevisor({"datafile": datafile(), "logLevel": "fatal"})
        provider = FeaturevisorOpenFeatureProvider(featurevisor=f)
        self.assertIs(f.set_datafile("{"), False)
        self.assertEqual(provider.resolve_boolean_details("checkout", False).error_code, ErrorCode.PARSE_ERROR)
        self.assertIs(f.set_datafile(datafile(), replace=True), True)
        self.assertIsNone(provider.resolve_boolean_details("checkout", False).error_code)
        provider.shutdown()
        self.assertIs(f.set_datafile("{"), False)
        self.assertIsNone(provider.resolve_boolean_details("checkout", False).error_code)
        f.close()
        self.assertIs(f.set_datafile(datafile()), False)

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
                        "beforeEvaluation": lambda options: contexts.append(options["context"]) or options,
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

    def test_resolves_global_variables_and_custom_prefix(self):
        provider = self.provider()
        result = provider.resolve_string_details(
            "variable:supportEmail", "fallback", EvaluationContext(attributes={"country": "nl"})
        )
        self.assertEqual(result.value, "nl@example.com")
        self.assertEqual(result.reason, Reason.TARGETING_MATCH)
        self.assertEqual(result.flag_metadata["variableKey"], "supportEmail")
        self.assertEqual(result.flag_metadata["variableOverrideKey"], "netherlands")
        self.assertEqual(provider.resolve_object_details("variable:limits", {}).value, {"requests": 10})

        custom = self.provider(key_separator="/", global_variable_prefix="$variable")
        self.assertEqual(custom.resolve_string_details("$variable/supportEmail", "fallback").value, "support@example.com")

    def test_rejects_global_prefix_containing_separator(self):
        with self.assertRaisesRegex(ValueError, "global_variable_prefix cannot contain key_separator"):
            self.provider(global_variable_prefix="global:variable")

    def test_missing_global_variable_uses_standard_not_found_error(self):
        result = self.provider().resolve_string_details("variable:missing", "fallback")
        self.assertEqual(result.value, "fallback")
        self.assertEqual(result.error_code, ErrorCode.FLAG_NOT_FOUND)

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

    def test_real_unmet_feature_requirement_maps_to_disabled(self):
        content = datafile()
        content["features"]["checkout"]["requiredFeatures"] = ["missing"]
        f = create_featurevisor({"datafile": content, "logLevel": "fatal"})
        self.addCleanup(f.close)
        provider = FeaturevisorOpenFeatureProvider(featurevisor=f)
        self.addCleanup(provider.shutdown)
        result = provider.resolve_boolean_details("checkout", True)
        self.assertIs(result.value, False)
        self.assertEqual(result.reason, Reason.DISABLED)
        self.assertEqual(result.flag_metadata["featurevisorReason"], "conditions_unmet")
        self.assertIsNone(result.error_code)

    def test_maps_all_featurevisor_reasons(self):
        mappings = {
            "conditions_unmet": Reason.DISABLED,
            "forced": Reason.TARGETING_MATCH,
            "sticky": Reason.TARGETING_MATCH,
            "rule": Reason.TARGETING_MATCH,
            "variable_override_variation": Reason.TARGETING_MATCH,
            "variable_override_rule": Reason.TARGETING_MATCH,
            "allocated": Reason.SPLIT,
            "disabled": Reason.DISABLED,
            "variation_disabled": Reason.DISABLED,
            "variable_disabled": Reason.DISABLED,
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

    def test_client_resolution_tracking_updates_and_shutdown(self):
        for borrowed in (False, True):
            with self.subTest(borrowed=borrowed):
                tracked, closed, events = [], [], []
                error_received, ready_received = Event(), Event()
                options = {"datafile": datafile(), "logLevel": "fatal",
                           "modules": [{"close": lambda: closed.append(True)}]}
                f = create_featurevisor(options) if borrowed else None
                provider = FeaturevisorOpenFeatureProvider(
                    options, featurevisor=f,
                    on_track=lambda *args: tracked.append(args),
                )
                try:
                    api.set_provider_and_wait(provider)
                    client = api.get_client()
                    client.add_handler(ProviderEvent.PROVIDER_ERROR, lambda _: (events.append("error"), error_received.set()))
                    client.add_handler(ProviderEvent.PROVIDER_READY, lambda _: (events.append("ready"), ready_received.set()))
                    self.assertTrue(ready_received.wait(2))
                    context = EvaluationContext(targeting_key="user", attributes={"country": "nl"})
                    self.assertEqual(client.get_string_value("variable:supportEmail", "", context), "nl@example.com")
                    self.assertEqual(client.get_integer_value("checkout:count", 0, context), 3)
                    self.assertEqual(client.get_float_value("checkout:ratio", 0.0, context), 1.5)
                    self.assertEqual(client.get_object_value("checkout:json", {}, context), {"nested": True})
                    missing = client.get_boolean_details("missing", True)
                    self.assertTrue(missing.value)
                    self.assertEqual(missing.error_code, ErrorCode.FLAG_NOT_FOUND)
                    mismatch = client.get_integer_details("checkout:ratio", 9)
                    self.assertEqual(mismatch.value, 9)
                    self.assertEqual(mismatch.error_code, ErrorCode.TYPE_MISMATCH)
                    details = TrackingEventDetails(3, {"currency": "EUR"})
                    client.track("purchase", context, details)
                    self.assertEqual(tracked[0][0], "purchase")
                    self.assertEqual(tracked[0][1].targeting_key, "user")
                    self.assertIs(tracked[0][2], details)
                    updated = datafile()
                    updated["features"]["checkout"]["variations"][0]["variables"]["title"] = "Updated"
                    self.assertTrue(provider.featurevisor.set_datafile(updated, True))
                    self.assertEqual(client.get_string_value("checkout:title", ""), "Updated")
                    self.assertFalse(provider.featurevisor.set_datafile("{"))
                    self.assertTrue(error_received.wait(2))
                    self.assertEqual(client.get_boolean_details("checkout", False).reason, Reason.ERROR)
                    ready_received.clear()
                    self.assertTrue(provider.featurevisor.set_datafile(updated, True))
                    self.assertTrue(ready_received.wait(2))
                    self.assertEqual(client.get_string_value("checkout:title", ""), "Updated")
                    self.assertEqual(events[-2:], ["error", "ready"])
                    api.shutdown()
                    provider.shutdown()
                    self.assertEqual(closed, [] if borrowed else [True])
                    if borrowed:
                        self.assertFalse(f.set_datafile("{"))
                        self.assertIsNone(provider.datafile_error)
                finally:
                    api.shutdown()
                    provider.shutdown()
                    if f is not None:
                        f.close()
                self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
