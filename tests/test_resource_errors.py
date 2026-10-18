from __future__ import annotations

import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from featurevisor import create_featurevisor
from featurevisor._json import strict_json_loads
from featurevisor._evaluation_session import EvaluationSession, RequirementInputError
from featurevisor._evaluation_session import dependency_of


def feature(**values):
    return {"bucketBy": "userId", "conditions": "*",
            "traffic": [{"key": "all", "conditions": "*", "percentage": 100000,
                         "variation": "on"}],
            "variations": [{"value": "on"}],
            "variablesSchema": {"copy": {"type": "integer", "defaultValue": 7}}, **values}


def aliases():
    return {**{str(i): {"type": "integer", "compose": str(i + 1)} for i in range(256)},
            "256": {"type": "integer", "defaultValue": 7}}


DEEP_JSON = "[" * 10000 + "0" + "]" * 10000


def exhausted_on_deep_json(source):
    # Python 3.12 and later can decode this depth, so the decoder's exhaustion is simulated.
    if source == DEEP_JSON:
        raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
    return strict_json_loads(source)


class ResourceErrorTests(unittest.TestCase):
    def setUp(self):
        previous = sys.getrecursionlimit()
        sys.setrecursionlimit(1000)
        self.addCleanup(sys.setrecursionlimit, previous)
        setter = patch("sys.setrecursionlimit", side_effect=AssertionError("SDK changed the host budget"))
        setter.start()
        self.addCleanup(setter.stop)

    def instance(self, *, variables=None, features=None, segments=None, modules=None):
        f = create_featurevisor({"logLevel": "fatal", "modules": modules or [], "datafile": {
            "schemaVersion": "4", "revision": "resource", "segments": segments or {},
            "features": {"healthy": feature(), **(features or {})},
            "variables": {"healthy": {"type": "integer", "defaultValue": 7}, **(variables or {})},
        }})
        self.addCleanup(f.close)
        return f

    def assert_error(self, evaluation, *, feature_key=None, variable_key=None):
        self.assertEqual(evaluation["reason"], "error", evaluation)
        self.assertIsInstance(evaluation["error"], RequirementInputError)
        self.assertNotIn("variableValue", evaluation)
        self.assertNotIn("variationValue", evaluation)
        self.assertNotIn("variation", evaluation)
        self.assertIsNot(evaluation.get("enabled"), True)
        if feature_key is not None:
            self.assertEqual(evaluation["featureKey"], feature_key)
        if variable_key is not None:
            self.assertEqual(evaluation["variableKey"], variable_key)
        self.assertEqual(sys.getrecursionlimit(), 1000)

    def assert_clean(self, session):
        for name in ("active", "current", "segments", "memo", "sticky_refs", "aliases"):
            self.assertFalse(getattr(session, name), name)

    def test_deep_aliases_abort_maps_before_overrides_and_explicit_defaults(self):
        variables = aliases()
        variables["map"] = {"type": "object", "compose": [
            {"key": "good", "variable": "healthy"}, {"key": "bad", "variable": "0"}],
            "overrides": [{"conditions": "*", "value": {"rescued": True}}]}
        f = self.instance(variables=variables)
        for owner in (f, f.spawn({"userId": "child"})):
            for key in ("0", "map"):
                for _ in range(2):
                    with self.subTest(child=owner is not f, key=key):
                        session = EvaluationSession()
                        options = {"__featurevisor_session": session, "defaultVariableValue": "outer fallback"}
                        self.assert_error(owner.evaluate_variable(key, {}, options), variable_key=key)
                        self.assert_clean(session)
                        self.assertIsNone(owner.get_variable(key, {}, options))
                        self.assert_clean(session)
                        self.assertEqual(owner.get_variable("healthy", {}, options), 7)
                        self.assert_clean(session)

    def test_deep_alias_errors_cannot_be_negated_or_matched_as_null(self):
        for operator in ("isNull", "notNull", "equals"):
            leaf = {"variable": "0", "operator": operator, "value": 7}
            for conditions in (leaf, {"not": [leaf]}, {"not": [{"not": [leaf]}]},
                               {"or": [leaf, "*"]}):
                with self.subTest(operator=operator, conditions=conditions):
                    f = self.instance(variables={**aliases(), "root": {
                        "type": "integer", "conditions": conditions, "defaultValue": 99,
                        "disabledValue": 88, "useDefaultWhenDisabled": True}},
                        features={"root": feature(conditions=conditions, disabledVariationValue="off")})
                    self.assert_error(f.evaluate_variable("root"), variable_key="root")
                    self.assertIsNone(f.get_variable("root"))
                    for evaluate, getter, args in ((f.evaluate_flag, f.is_enabled, ("root",)),
                                                   (f.evaluate_variation, f.get_variation, ("root",)),
                                                   (f.evaluate_variable, f.get_variable, ("root", "copy"))):
                        self.assert_error(evaluate(*args), feature_key="root")
                        self.assertEqual(getter(*args), False if getter == f.is_enabled else None)

    def test_mixed_segments_features_and_variables_under_not_cleanup_and_recover(self):
        variables, features, segments = {}, {}, {}
        for i in range(80):
            variables[f"v{i}"] = {"type": "integer", "defaultValue": 7,
                                    "conditions": {"not": [f"s{i}"]}}
            segments[f"s{i}"] = {"conditions": {"feature": f"f{i}"}}
            features[f"f{i}"] = feature(conditions={"variable": f"v{i + 1}",
                                                     "operator": "equals", "value": 7})
        variables["v80"] = {"type": "integer", "defaultValue": 7}
        variables["root"] = {"type": "object", "compose": [
            {"key": "good", "variable": "healthy"}, {"key": "bad", "variable": "v0"}]}
        f = self.instance(variables=variables, features=features, segments=segments)
        session = EvaluationSession()
        options = {"__featurevisor_session": session}
        for key in ("v0", "root"):
            self.assert_error(f.evaluate_variable(key, {}, options), variable_key=key)
            self.assert_clean(session)
            self.assertIsNone(f.get_variable(key, {}, options))
            self.assert_clean(session)
        self.assert_error(f.evaluate_flag("f0", {}, options), feature_key="f0")
        self.assert_clean(session)
        self.assertEqual(f.get_variable("v80", {}, options), 7)
        self.assertTrue(f.is_enabled("healthy", {}, options))
        self.assert_clean(session)

    @patch("featurevisor.instance.strict_json_loads", exhausted_on_deep_json)
    def test_json_decoder_exhaustion_is_not_an_ordinary_null_dependency(self):
        # The source getter still has ordinary JSON parsing semantics. Once used
        # as a dependency, host exhaustion must abort its entire requester.
        f = self.instance(variables={
            "source": {"type": "json", "defaultValue": DEEP_JSON},
            "alias": {"type": "json", "compose": "source"},
            "map": {"type": "object", "compose": [{"key": "good", "variable": "healthy"},
                                                        {"key": "bad", "variable": "source"}]},
            "root": {"type": "integer", "conditions": {"not": [
                {"variable": "source", "operator": "notNull"}]}, "defaultValue": 99},
        }, features={"root": feature(conditions={"not": [
            {"variable": "source", "operator": "notNull"}]})})
        self.assertIsNone(f.get_variable("source"))
        for key in ("alias", "map", "root"):
            self.assert_error(f.evaluate_variable(key), variable_key=key)
            self.assertIsNone(f.get_variable(key))
        self.assert_error(f.evaluate_flag("root"), feature_key="root")
        self.assertFalse(f.is_enabled("root"))
        self.assertEqual(f.get_variable("healthy"), 7)

    def test_condition_parse_exhaustion_is_not_cached_as_invalid_conditions(self):
        # A feature first read near the host limit must still match once the stack is shallow.
        errors = []
        f = create_featurevisor({"logLevel": "error", "datafile": {
            "schemaVersion": "4", "revision": "parse", "segments": {}, "features": {"leaf": {
                "bucketBy": "userId", "traffic": [{"key": "all", "percentage": 100000,
                                                   "conditions": '[{"attribute": "userId", "operator": "exists"}]'}],
            }},
        }, "onDiagnostic": lambda diagnostic: errors.append(diagnostic["code"]) if diagnostic["level"] == "error" else None})
        self.addCleanup(f.close)
        with patch("featurevisor.normalize.strict_json_loads", side_effect=RecursionError), \
                patch("featurevisor.evaluation_data_provider.strict_json_loads", side_effect=RecursionError):
            self.assertFalse(f.is_enabled("leaf", {"userId": "u"}))
        self.assertTrue(f.is_enabled("leaf", {"userId": "u"}))
        self.assertEqual(errors, ["evaluation_error"])

    def test_feature_flag_exhaustion_cannot_continue_into_variation_or_variable(self):
        f = self.instance(features={"source": feature()})
        original = f.datafile.conditions_are_matched

        def exhausted(conditions, options):
            if options.get("featureKey") == "source" and options["type"] == "flag":
                raise RecursionError("flag stack exhausted")
            return original(conditions, options)

        with patch.object(f.datafile, "conditions_are_matched", exhausted):
            for evaluate, getter, args in ((f.evaluate_flag, f.is_enabled, ("source",)),
                                           (f.evaluate_variation, f.get_variation, ("source",)),
                                           (f.evaluate_variable, f.get_variable, ("source", "copy"))):
                with self.subTest(args=args, evaluate=evaluate.__name__):
                    self.assert_error(evaluate(*args), feature_key="source")
                    self.assertEqual(getter(*args), False if getter == f.is_enabled else None)
        self.assertEqual(f.get_variation("source"), "on")
        self.assertEqual(f.get_variable("source", "copy"), 7)

    def test_callback_exhaustion_aborts_each_composition_source_kind_and_not(self):
        for hook in ("beforeEvaluation", "afterEvaluation", "bucketKey", "bucketValue"):
            for source in ({"variable": "source"}, {"feature": "source"},
                           {"feature": "source", "variation": True},
                           {"feature": "source", "variable": "copy"}):
                if hook.startswith("bucket") and "feature" not in source:
                    continue
                with self.subTest(hook=hook, source=source):
                    def fail(*args):
                        options = args[-1]
                        if options.get("featureKey") == "source" or options.get("variableKey") == "source":
                            raise RecursionError("callback stack exhausted")
                        return args[0] if hook == "afterEvaluation" else options.get(hook, options)

                    condition = ({"feature": "source"} if "variable" not in source else
                                 {**source, "operator": "notNull"})
                    f = self.instance(modules=[{hook: fail}], features={"source": feature()}, variables={
                        "source": {"type": "integer", "defaultValue": 7},
                        "map": {"type": "object", "compose": [{"key": "good", "variable": "healthy"},
                                                                    {"key": "bad", **source}]},
                        "not": {"type": "integer", "defaultValue": 99, "conditions": {"not": [condition]}},
                    })
                    for key in ("map", "not"):
                        session = EvaluationSession()
                        self.assert_error(f.evaluate_variable(key, {}, {"__featurevisor_session": session}),
                                          variable_key=key)
                        self.assert_clean(session)
                        self.assertIsNone(f.get_variable(key))
                    self.assertEqual(f.get_variable("healthy"), 7)

    def test_failed_resource_outcomes_are_never_memoized_as_completed_null(self):
        session = EvaluationSession()
        # Composition memo values intentionally omit ordinary getter errors,
        # but resource errors are not completed getter values.
        for purpose in ("condition", "composition"):
            key = (("variable", None, "source"), (), None, 0, purpose, ())
            session.put(key, {"type": "variable", "variableKey": "source", "reason": "error",
                              "error": RecursionError("stack exhausted")})
            self.assertIsNone(session.get(key))

    def test_exhaustion_during_frame_entry_leaves_no_partial_stack(self):
        f = self.instance(variables={"root": {"type": "integer", "compose": "source"},
                                     "source": {"type": "integer", "defaultValue": 7}})
        session = EvaluationSession()
        options = {"__featurevisor_session": session}

        def exhausted(options):
            if options.get("variableKey") == "source":
                raise RecursionError("frame entry stack exhausted")
            return dependency_of(options)

        with patch("featurevisor._evaluation_session.dependency_of", exhausted):
            self.assert_error(f.evaluate_variable("root", {}, options), variable_key="root")
        self.assert_clean(session)
        self.assertEqual(f.get_variable("root", {}, options), 7)
        self.assert_clean(session)

    def test_condition_snapshot_exhaustion_aborts_instead_of_borrowing_or_returning_null(self):
        leaf = {"variable": "source", "operator": "isNull"}
        f = self.instance(variables={"source": {"type": "object", "defaultValue": {"a": 7}},
                                     "root": {"type": "integer", "defaultValue": 99,
                                              "conditions": {"not": [leaf]}}})
        session = EvaluationSession()
        with patch("featurevisor._evaluation_session.copy.deepcopy", side_effect=RecursionError("snapshot exhausted")):
            self.assert_error(f.evaluate_variable("root", {}, {"__featurevisor_session": session}),
                              variable_key="root")
        self.assert_clean(session)
        self.assertEqual(f.get_variable("root"), 99)

    def test_context_fingerprint_exhaustion_aborts_both_root_kinds_and_cleans_up(self):
        context = {}
        for _ in range(1100):
            context = {"nested": context}
        leaf = {"variable": "healthy", "operator": "isNull"}
        f = self.instance(variables={"root": {"type": "integer", "defaultValue": 99,
                                              "conditions": {"not": [leaf]}}},
                          features={"root": feature(conditions={"not": [leaf]})})
        session = EvaluationSession()
        options = {"__featurevisor_session": session}
        self.assert_error(f.evaluate_variable("root", context, options), variable_key="root")
        self.assert_clean(session)
        self.assert_error(f.evaluate_flag("root", context, options), feature_key="root")
        self.assert_clean(session)
        self.assertEqual(f.get_variable("healthy", {}, options), 7)
        self.assertTrue(f.is_enabled("healthy", {}, options))
        self.assert_clean(session)

    def test_completed_null_false_and_ordinary_getter_errors_remain_values(self):
        def before(options):
            if options.get("variableKey") == "ordinaryError":
                raise ValueError("ordinary getter failure")
            return options

        f = self.instance(modules=[{"beforeEvaluation": before}], variables={
            "null": {"type": "object", "defaultValue": None},
            "false": {"type": "boolean", "defaultValue": False},
            "malformedJson": {"type": "json", "defaultValue": "{"},
            "ordinaryError": {"type": "integer", "defaultValue": 7},
            "map": {"type": "object", "compose": [
                {"key": key, "variable": key} for key in ("null", "false", "malformedJson", "ordinaryError")]},
        })
        self.assertEqual(f.get_variable("map"), {"null": None, "false": False,
                                                "malformedJson": None, "ordinaryError": None})
        self.assertEqual(f.evaluate_variable("map")["reason"], "variable_composed")
        self.assertEqual(sys.getrecursionlimit(), 1000)

    def test_threads_keep_the_host_budget_and_recover_after_deep_getters(self):
        f = self.instance(variables=aliases())
        child = f.spawn({"userId": "child"})

        def read(index):
            owner = f if index % 2 else child
            self.assert_error(owner.evaluate_variable("0"), variable_key="0")
            self.assertIsNone(owner.get_variable("0"))
            self.assertEqual(owner.get_variable("healthy"), 7)
            self.assertEqual(owner.get_variable("256"), 7)
            self.assertTrue(owner.is_enabled("healthy"))
            return sys.getrecursionlimit()

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(read, range(20))), [1000] * 20)
        self.assertEqual(f.get_variable("healthy"), 7)

    def test_internal_flags_preserve_the_original_requester_in_before_traces(self):
        visits = []

        def before(options):
            visits.append({key: options[key] for key in
                           ("type", "featureKey", "variableKey", "dependencyOf", "dependencyPurpose")
                           if key in options})
            return options

        f = self.instance(modules=[{"beforeEvaluation": before}], features={
            "gate": feature(), "subject": feature(conditions={"feature": "gate"})})
        for owner in (f, f.spawn({"userId": "child"})):
            for type_, read, expected in (
                ("variable", lambda: owner.get_variable("subject", "copy"), 7),
                ("variation", lambda: owner.get_variation("subject"), "on"),
            ):
                with self.subTest(type=type_, child=owner is not f):
                    visits.clear()
                    self.assertEqual(read(), expected)
                    requester = {"type": type_, "featureKey": "subject"}
                    if type_ == "variable":
                        requester["variableKey"] = "copy"
                    self.assertEqual(visits, [requester, {
                        "type": "flag", "featureKey": "gate", "dependencyOf": requester,
                        "dependencyPurpose": "condition"}])

    def test_malformed_json_getter_fallback_is_memoized_once_per_root_and_purpose(self):
        for source in ({"variable": "source"}, {"feature": "source", "variable": "copy"}):
            for negate in (False, True):
                with self.subTest(source=source, negate=negate):
                    leaf = {**source, "operator": "isNull"}
                    conditions = [leaf, leaf]
                    if negate:
                        conditions = {"not": [conditions]}
                    visits, diagnostics = [], []

                    def before(options):
                        if options.get("dependencyPurpose"):
                            visits.append((options.get("featureKey"), options.get("variableKey"),
                                           options["dependencyPurpose"], options["dependencyOf"]))
                        return options

                    f = self.instance(modules=[{"beforeEvaluation": before}], variables={
                        "source": {"type": "json", "defaultValue": "{oops"},
                        "root": {"type": "integer", "defaultValue": 7, "conditions": conditions},
                    }, features={"source": feature(variablesSchema={
                        "copy": {"type": "json", "defaultValue": "{oops"}})})
                    f.on_diagnostic = diagnostics.append
                    f.set_log_level("error")
                    for run in range(2):
                        visits.clear()
                        evaluation = f.evaluate_variable("root")
                        self.assertEqual(evaluation["reason"], "conditions_unmet" if negate else "variable_default")
                        self.assertEqual(evaluation.get("variableValue"), None if negate else 7)
                        self.assertEqual(visits, [(source.get("feature"), source["variable"], "condition",
                                                   {"type": "variable", "variableKey": "root"})])
                        failures = [item for item in diagnostics if item.get("message") == "getVariable failed"]
                        self.assertEqual(len(failures), run + 1)
                        self.assertEqual(failures[-1]["details"], {"variableKey": source["variable"],
                            **({"featureKey": source["feature"]} if "feature" in source else {})})

    def test_getter_memos_separate_condition_and_composition_without_double_decoding(self):
        visits, diagnostics = [], []

        def before(options):
            if options.get("dependencyPurpose"):
                visits.append((options.get("variableKey"), options["dependencyPurpose"], options["dependencyOf"]))
            return options

        f = self.instance(modules=[{"beforeEvaluation": before}], variables={
            "bad": {"type": "json", "defaultValue": "{oops"},
            "text": {"type": "json", "defaultValue": '"null"'},
            "alias": {"type": "json", "compose": "text"},
            "root": {"type": "object", "conditions": [
                {"variable": "bad", "operator": "isNull"}, {"variable": "bad", "operator": "isNull"}],
                "compose": [{"key": "a", "variable": "bad"}, {"key": "b", "variable": "bad"},
                            {"key": "text1", "variable": "alias"}, {"key": "text2", "variable": "alias"}]},
        })
        f.on_diagnostic = diagnostics.append
        f.set_log_level("error")
        self.assertEqual(f.get_variable("root"), {"a": None, "b": None, "text1": "null", "text2": "null"})
        root = {"type": "variable", "variableKey": "root"}
        self.assertEqual(visits, [("bad", "condition", root), ("bad", "composition", root),
                                  ("alias", "composition", root),
                                  ("text", "composition", {"type": "variable", "variableKey": "alias"})])
        self.assertEqual(len([item for item in diagnostics if item.get("message") == "getVariable failed"]), 2)

    def test_not_never_inverts_variable_comparison_errors(self):
        for leaf in ({"variable": "source", "operator": "matches", "value": "["},
                     {"variable": "source", "operator": "isNull", "path": ["__proto__"]}):
            with self.subTest(leaf=leaf):
                f = self.instance(variables={"source": {"type": "string", "defaultValue": "text"},
                                             "root": {"type": "integer", "defaultValue": 7,
                                                      "conditions": {"not": [leaf]}}},
                                  features={"root": feature(conditions={"not": [leaf]})})
                self.assert_error(f.evaluate_variable("root"), variable_key="root")
                self.assertIsNone(f.get_variable("root"))
                self.assert_error(f.evaluate_flag("root"), feature_key="root")
                self.assertFalse(f.is_enabled("root"))


if __name__ == "__main__":
    unittest.main()
