from __future__ import annotations

import copy
import json
import sys
import unittest

from featurevisor import create_featurevisor


def feature(**values):
    return {"bucketBy": "userId", "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}], **values}


def datafile(**values):
    return {"schemaVersion": "4", "revision": "review", "segments": {}, "features": {}, "variables": {}, **values}


class ReviewGapTests(unittest.TestCase):
    def test_segment_reads_use_datafile_accepted_by_previous_dependency_callback(self):
        for global_root in (False, True):
            with self.subTest(global_root=global_root):
                gate = [{"variable": "mutator", "operator": "notNull"}, "audience"]
                d = datafile(segments={"audience": {"conditions": {"or": []}}},
                    features={"root": feature(conditions=gate), "probe": feature(conditions="audience")},
                    variables={"root": {"type": "string", "conditions": gate, "defaultValue": "yes"},
                               "mutator": {"type": "integer", "defaultValue": 1}})
                def after(evaluation, options):
                    if options.get("variableKey") == "mutator":
                        f.set_datafile(datafile(revision="updated", segments={"audience": {"conditions": "*"}}))
                    return evaluation
                f = create_featurevisor({"datafile": d, "logLevel": "fatal", "modules": [{"afterEvaluation": after}]})
                self.addCleanup(f.close)
                self.assertFalse(f.is_enabled("probe"))
                self.assertEqual(f.get_variable("root") if global_root else f.is_enabled("root"), "yes" if global_root else True)
                self.assertTrue(f.is_enabled("probe"))

    def test_module_add_remove_and_unsubscribe_invalidate_composition_reads(self):
        for change in ("add", "remove", "unsubscribe"):
            with self.subTest(change=change):
                module = {"name": "value", "afterEvaluation": lambda result, options: (
                    {**result, "variableValue": 2} if options.get("variableKey") == "leaf" else result)}
                def before(options):
                    if options.get("variableKey") == "mutator":
                        if change == "add":
                            f.add_module(module)
                        elif change == "remove":
                            f.remove_module("value")
                        else:
                            remove()
                    return options
                f = create_featurevisor({"logLevel": "fatal", "modules": [{"beforeEvaluation": before}],
                    "datafile": datafile(variables={"leaf": {"type": "integer", "defaultValue": 1},
                        "mutator": {"type": "integer", "defaultValue": 0}, "root": {"type": "object", "compose": [
                            {"key": "before", "variable": "leaf"}, {"key": "mutator", "variable": "mutator"},
                            {"key": "after", "variable": "leaf"}]}})})
                self.addCleanup(f.close)
                if change != "add":
                    remove = f.add_module(module)
                self.assertEqual(f.get_variable("root"), {"before": 1 if change == "add" else 2,
                                                          "mutator": 0, "after": 2 if change == "add" else 1})

    def test_terminal_segment_fast_predicates_preserve_the_active_depth_boundary(self):
        country = {"attribute": "country", "operator": "equals", "value": "nl"}
        for terminal in ("*", country, {"and": [country, {"not": [{**country, "value": "de"}]}]}):
            for count in (256, 257, 258):
                with self.subTest(terminal=terminal, count=count):
                    segments = {str(i): {"conditions": str(i + 1) if i + 1 < count else terminal} for i in range(count)}
                    f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(
                        features={"root": feature(conditions="0"), "independent": feature()}, segments=segments)})
                    self.addCleanup(f.close)
                    allowed = count <= (256 if terminal == "*" else 257)
                    for _ in range(2):
                        evaluation = f.evaluate_flag("root", {"country": "nl"})
                        self.assertEqual(evaluation["reason"], "rule" if allowed else "error")
                        self.assertEqual(f.is_enabled("root", {"country": "nl"}), allowed)
                        if allowed and terminal != "*":
                            self.assertEqual(f.evaluate_flag("root", {"country": "de"})["reason"], "conditions_unmet")
                        self.assertTrue(f.is_enabled("independent"))

    def test_mixed_getter_and_segment_depth_preserves_terminal_fast_predicates(self):
        host_limit = sys.getrecursionlimit()
        country = {"attribute": "country", "operator": "equals", "value": "nl"}
        for getters in (1, 8):
            for terminal in ("*", country):
                for active_depth in (256, 257):
                    with self.subTest(getters=getters, terminal=terminal, active_depth=active_depth):
                        count = active_depth - getters + (0 if terminal == "*" else 1)
                        segments = {str(i): {"conditions": str(i + 1) if i + 1 < count else terminal} for i in range(count)}
                        variables = {f"v{i}": {"type": "integer", "compose": f"v{i + 1}"} for i in range(getters)}
                        variables[f"v{getters}"] = {"type": "integer", "conditions": "0", "defaultValue": 7}
                        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(segments=segments, variables=variables)})
                        self.addCleanup(f.close)
                        for _ in range(2):
                            self.assertEqual(f.evaluate_variable("v0", {"country": "nl"})["reason"],
                                             "variable_composed" if active_depth == 256 else "error")
                            self.assertEqual(f.get_variable("v0", {"country": "nl"}), 7 if active_depth == 256 else None)
                            self.assertEqual(f.get_variable(f"v{getters}", {"country": "nl"}), 7)
        self.assertEqual(sys.getrecursionlimit(), host_limit)

    def test_not_operands_and_explicit_null_gates_at_each_root_and_segment(self):
        expressions = [(None, False), ({"not": None}, False), ({"not": "*"}, False),
                       ({"not": {}}, False), ({"not": {"0": None}}, False),
                       ({"not": {"attribute": "missing", "operator": "exists"}}, False),
                       ({"not": 1}, False), ({"not": False}, False), ({"not": []}, False),
                       ({"not": [None]}, True)]
        for expression, expected in expressions:
            for encoding in ("native", "selector", "datafile"):
                with self.subTest(expression=expression, encoding=encoding):
                    selector = json.dumps(expression) if encoding == "selector" and expression is not None else expression
                    d = datafile(segments={"selected": {"conditions": selector}},
                        features={"direct": feature(conditions=selector), "segment": feature(conditions="selected"), "absent": feature()},
                        variables={"direct": {"type": "string", "conditions": selector, "defaultValue": "yes", "disabledValue": "no"}})
                    diagnostics = []
                    f = create_featurevisor({"datafile": json.dumps(d) if encoding == "datafile" else d,
                                             "logLevel": "error", "onDiagnostic": diagnostics.append})
                    self.addCleanup(f.close)
                    for _ in range(2):
                        self.assertEqual(f.is_enabled("direct"), expected)
                        self.assertEqual(f.is_enabled("segment"), expected)
                        if expression is None or isinstance(expression.get("not"), list):
                            self.assertEqual(f.get_variable("direct"), "yes" if expected else "no")
                        else:
                            self.assertNotEqual(f.get_variable("direct"), "yes")
                        self.assertTrue(f.is_enabled("absent"))
                    self.assertFalse([item for item in diagnostics if item["code"] == "conditions_parse_error"])
                    if expression is None:
                        evaluation = f.evaluate_flag("direct")
                        self.assertEqual(evaluation["reason"], "conditions_unmet")
                        self.assertIn("conditions", evaluation)
                        self.assertIsNone(evaluation["conditions"])

    def test_composition_gate_ignores_explicit_caller_and_module_defaults(self):
        for alias in (False, True):
            for disabled in ({}, {"disabledValue": None}, {"disabledValue": False}, {"disabledValue": 0}, {"disabledValue": "null"}):
                with self.subTest(alias=alias, disabled=disabled):
                    seen = []
                    def before(options):
                        seen.append(options["variableKey"])
                        return {**options, "defaultVariableValue": "module"}
                    f = create_featurevisor({"logLevel": "fatal", "modules": [{"beforeEvaluation": before}],
                        "datafile": datafile(variables={"root": {"type": "json" if alias else "object", "conditions": None,
                            "compose": "source" if alias else [{"key": "source", "variable": "source"}],
                            "overrides": [{"conditions": "*", "value": "override"}], **disabled},
                            "source": {"type": "integer", "defaultValue": 1}})})
                    self.addCleanup(f.close)
                    for _ in range(2):
                        expected = disabled.get("disabledValue")
                        self.assertIs(type(f.get_variable("root", {}, {"defaultVariableValue": "caller"})), type(expected))
                        self.assertEqual(f.get_variable("root", {}, {"defaultVariableValue": "caller"}), expected)
                        evaluation = f.evaluate_variable("root", {}, {"defaultVariableValue": "caller"})
                        self.assertEqual(evaluation["reason"], "conditions_unmet")
                        self.assertEqual(evaluation["variableValue"], expected)
                    self.assertEqual(seen, ["root"] * 6)

    def test_module_sticky_replacements_survive_later_owner_setters(self):
        def before(options):
            if options.get("variableKey") == "root":
                return {**options, "stickyVariables": {"source": 2},
                        "stickyFeatures": {"source": {"enabled": True, "variables": {"value": 3}}}}
            return options
        def after(evaluation, options):
            if options.get("variableKey") == "mutator":
                f.set_sticky_variables({"source": 9})
                f.set_sticky_features({"source": {"enabled": True, "variables": {"value": 9}}})
            return evaluation
        f = create_featurevisor({"logLevel": "fatal", "modules": [{"beforeEvaluation": before, "afterEvaluation": after}],
            "datafile": datafile(features={"source": feature(variablesSchema={"value": {"type": "integer", "defaultValue": 1}})},
                variables={"source": {"type": "integer", "defaultValue": 1}, "mutator": {"type": "integer", "defaultValue": 0},
                    "root": {"type": "object", "compose": [{"key": "before", "variable": "source"},
                        {"key": "mutator", "variable": "mutator"}, {"key": "after", "variable": "source"},
                        {"key": "feature", "feature": "source", "variable": "value"}]}})})
        self.addCleanup(f.close)
        for _ in range(2):
            self.assertEqual(f.get_variable("root"), {"before": 2, "mutator": 0, "after": 2, "feature": 3})
        self.assertEqual(f.get_variable("source"), 9)
        self.assertEqual(f.get_variable("source", "value"), 9)

    def test_callback_setters_refresh_current_read_and_whole_composition_sticky(self):
        for global_root in (False, True):
            with self.subTest(global_root=global_root):
                seen = []
                def before(options):
                    seen.append(options.get("variableKey") or options.get("featureKey"))
                    if options.get("variableKey") == "root" or options.get("featureKey") == "root":
                        if global_root:
                            f.set_sticky_variables({"root": "whole"})
                        else:
                            f.set_sticky_features({"root": {"enabled": True, "variables": {"value": "whole"}}})
                    return options
                f = create_featurevisor({"logLevel": "fatal", "modules": [{"beforeEvaluation": before}], "datafile": datafile(
                    features={"root": feature(variablesSchema={"value": {"type": "string", "defaultValue": "old"}})},
                    variables={"root": {"type": "object", "compose": [{"key": "unused", "variable": "unused"}]}})})
                self.addCleanup(f.close)
                self.assertEqual(f.get_variable("root") if global_root else f.get_variable("root", "value"), "whole")
                self.assertEqual(seen, ["root"] if global_root else ["value"])

    def test_context_changes_and_independent_callback_reentry_keep_requester_and_memo(self):
        seen = []
        inner = []
        def before(options):
            if options.get("featureKey") == "source":
                seen.append((options.get("dependencyOf"), options.get("dependencyPurpose")))
                if not options["context"].get("inner"):
                    inner.append(f.get_variable("source", "value", {"inner": True, "nested": {"value": 9}}))
            return options
        def after(evaluation, options):
            if options.get("featureKey") == "source":
                evaluation["variableValue"] = options["context"]["nested"]["value"]
            if options.get("variableKey") == "mutator":
                options["context"]["nested"]["value"] = 2
            return evaluation
        leaf = {"feature": "source", "variable": "value"}
        f = create_featurevisor({"logLevel": "fatal", "modules": [{"beforeEvaluation": before, "afterEvaluation": after}],
            "datafile": datafile(features={"source": feature(variablesSchema={"value": {"type": "integer", "defaultValue": 0}})},
                variables={"mutator": {"type": "integer", "defaultValue": 0}, "root": {"type": "object", "compose": [
                    {"key": "first", **leaf}, {"key": "duplicate", **leaf},
                    {"key": "mutator", "variable": "mutator"}, {"key": "changed", **leaf}]}})})
        self.addCleanup(f.close)
        for _ in range(2):
            seen.clear()
            inner.clear()
            self.assertEqual(f.get_variable("root", {"nested": {"value": 1}}), {"first": 1, "duplicate": 1, "mutator": 0, "changed": 2})
            self.assertEqual(inner, [9, 9])
            self.assertEqual(seen, [({"type": "variable", "variableKey": "root"}, "composition"), (None, None)] * 2)

    def test_sources_keep_exact_ordinary_fallbacks_without_outer_defaults(self):
        sources = {"null": None, "false": False, "zero": 0, "empty": "", "array": [], "object": {}}
        variables = {key: {"type": "object", "defaultValue": value} for key, value in sources.items()}
        variables.update({"json": {"type": "json", "defaultValue": '"null"'},
                          "broken": {"type": "json", "defaultValue": '{'},
                          "throws": {"type": "integer", "defaultValue": 1},
                          "gated": {"type": "json", "compose": "throws", "conditions": {"or": []}, "disabledValue": "null"}})
        expected = {**sources, "json": "null", "broken": None, "throws": None, "missing": None, "gated": "null",
                    "flag": False, "variation": None, "featureVariable": None}
        members = [{"key": key, "variable": key} for key in [*sources, "json", "broken", "throws", "missing", "gated"]]
        members += [{"key": "flag", "feature": "absent"}, {"key": "variation", "feature": "absent", "variation": True},
                    {"key": "featureVariable", "feature": "absent", "variable": "value"}]
        variables["root"] = {"type": "object", "compose": members}
        def before(options):
            if options.get("variableKey") == "throws":
                raise ValueError("ordinary module failure")
            return options
        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(variables=variables), "modules": [{"beforeEvaluation": before}]})
        self.addCleanup(f.close)
        for _ in range(2):
            result = f.get_variable("root", {}, {"defaultVariableValue": "outer", "defaultVariationValue": "outer"})
            self.assertEqual(result, expected)
            self.assertIs(result["false"], False)
            self.assertIs(type(result["zero"]), int)

    def test_global_module_sticky_replacements_reach_composition_and_conditions(self):
        for composed in (False, True):
            with self.subTest(composed=composed):
                root = {"type": "object", "conditions": [
                    {"variable": "source", "operator": "equals", "value": 2},
                    {"feature": "source", "variable": "value", "operator": "equals", "value": 3},
                ], "defaultValue": "matched"}
                if composed:
                    root["compose"] = [{"key": "global", "variable": "source"},
                                       {"key": "feature", "feature": "source", "variable": "value"}]
                def before(options):
                    if options.get("variableKey") == "root":
                        return {**options, "stickyVariables": {"source": 2},
                                "stickyFeatures": {"source": {"enabled": True, "variables": {"value": 3}}}}
                    return options
                f = create_featurevisor({"logLevel": "fatal", "modules": [{"beforeEvaluation": before}],
                    "datafile": datafile(features={"source": feature(variablesSchema={"value": {"type": "integer", "defaultValue": 1}})},
                        variables={"source": {"type": "integer", "defaultValue": 1}, "root": root})})
                self.addCleanup(f.close)
                for _ in range(2):
                    self.assertEqual(f.get_variable("root"), {"global": 2, "feature": 3} if composed else "matched")
                self.assertEqual(f.get_variable("source"), 1)
                self.assertEqual(f.get_variable("source", "value"), 1)

    def test_feature_sources_refresh_after_module_setters(self):
        for state in ("datafile", "sticky"):
            for child_mode in (False, True):
                for composed in (False, True):
                    with self.subTest(state=state, child=child_mode, composed=composed):
                        leaf = {"feature": "source", "variable": "value"}
                        root = {"type": "object", "defaultValue": "matched", "conditions": [
                            {**leaf, "operator": "equals", "value": 1},
                            {"variable": "mutator", "operator": "equals", "value": 0},
                            {**leaf, "operator": "equals", "value": 2}]}
                        if composed:
                            root = {"type": "object", "compose": [
                                {"key": "before", **leaf}, {"key": "mutator", "variable": "mutator"},
                                {"key": "after", **leaf}]}
                        d = datafile(features={"source": feature(variablesSchema={"value": {"type": "integer", "defaultValue": 1}})},
                                     variables={"mutator": {"type": "integer", "defaultValue": 0}, "root": root})
                        reads = []
                        def after(evaluation, options):
                            if options.get("variableKey") == "value":
                                reads.append(evaluation.get("variableValue"))
                            if options.get("variableKey") == "mutator":
                                if state == "sticky":
                                    target.set_sticky_features({"source": {"enabled": True, "variables": {"value": 2}}})
                                else:
                                    update = copy.deepcopy(d)
                                    update["features"]["source"]["variablesSchema"]["value"]["defaultValue"] = 2
                                    f.set_datafile(update, True)
                            return evaluation
                        f = create_featurevisor({"logLevel": "fatal", "datafile": d, "modules": [{"afterEvaluation": after}]})
                        self.addCleanup(f.close)
                        target = f.spawn() if child_mode else f
                        if child_mode:
                            self.addCleanup(target.close)
                        self.assertEqual(target.get_variable("root"), {"before": 1, "mutator": 0, "after": 2} if composed else "matched")
                        self.assertEqual(reads, [1, 2])
