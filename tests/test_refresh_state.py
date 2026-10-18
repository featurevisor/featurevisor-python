from __future__ import annotations

import unittest
from unittest.mock import Mock

from featurevisor import create_featurevisor


def feature(**values):
    return {"bucketBy": "userId", "traffic": [{"key": "all", "conditions": "*", "percentage": 100000}], **values}


def datafile(**values):
    return {"schemaVersion": "4", "revision": "initial", "segments": {}, "features": {}, "variables": {}, **values}


class RefreshStateTests(unittest.TestCase):
    def test_global_getter_reuses_initial_sticky_refresh_until_a_before_hook_changes_state(self):
        for child_mode in (False, True):
            for setter in (False, True):
                for replacement in (False, True):
                    with self.subTest(child=child_mode, setter=setter, replacement=replacement):
                        def before(options):
                            if setter:
                                target.set_sticky_variables({"leaf": 2})
                            return {**options, "stickyVariables": {"leaf": 3}} if replacement else options
                        f = create_featurevisor({"logLevel": "fatal", "datafile": datafile(
                            variables={"leaf": {"type": "integer", "defaultValue": 1}}),
                            "modules": [{"beforeEvaluation": before}]})
                        self.addCleanup(f.close)
                        target = f.spawn() if child_mode else f
                        if child_mode:
                            self.addCleanup(target.close)
                        source = Mock(side_effect=lambda: (target.sticky_features, target.sticky_variables))
                        options = {"__featurevisor_child_sticky_source": source,
                                   "__featurevisor_child_initial_sticky": (target.sticky_features, target.sticky_variables)}
                        if child_mode:
                            options.update(__featurevisor_child_state_version=lambda: target._state_version,
                                           __featurevisor_child_sticky_features=target.sticky_features,
                                           __featurevisor_child_sticky_variables=target.sticky_variables)
                        self.assertEqual(f.get_variable("leaf", {}, options), 3 if replacement else 2 if setter else 1)
                        self.assertEqual(source.call_count, 2 if setter else 1)

    def test_before_hook_setters_refresh_the_current_feature_read(self):
        for child in (False, True):
            for setter in ("datafile", "sticky"):
                with self.subTest(child=child, setter=setter):
                    def before(options):
                        if setter == "datafile":
                            self.assertTrue(f.set_datafile(datafile(revision="updated", features={
                                "source": feature(conditions={"or": []})})))
                        else:
                            target.set_sticky_features({"source": {"enabled": False}})
                        return options
                    f = create_featurevisor({"datafile": datafile(features={"source": feature()}),
                                             "logLevel": "fatal", "modules": [{"name": "setter", "beforeEvaluation": before}]})
                    self.addCleanup(f.close)
                    target = f.spawn() if child else f
                    if child:
                        self.addCleanup(target.close)
                    result = target.evaluate_flag("source")
                    self.assertIs(result["enabled"], False)
                    self.assertEqual(result["reason"], "conditions_unmet" if setter == "datafile" else "sticky")
                    f.remove_module("setter")
                    if child and setter == "sticky":
                        self.assertTrue(f.is_enabled("source"))

    def test_child_sticky_setters_invalidate_completed_composition_reads(self):
        for feature_source in (False, True):
            for hook in ("before", "after"):
                with self.subTest(feature_source=feature_source, hook=hook):
                    reads = []
                    def before(options):
                        if options.get("variableKey") == ("value" if feature_source else "source"):
                            reads.append(1)
                        if hook == "before" and options.get("variableKey") == "mutator":
                            update()
                        return options

                    def after(result, options):
                        if hook == "after" and options.get("variableKey") == "mutator":
                            update()
                        return result

                    def update():
                        if feature_source:
                            child.set_sticky_features({"source": {"enabled": True, "variables": {"value": 2}}})
                        else:
                            child.set_sticky_variables({"source": 2})

                    reference = {"feature": "source", "variable": "value"} if feature_source else {"variable": "source"}
                    f = create_featurevisor({"logLevel": "fatal", "modules": [{"beforeEvaluation": before, "afterEvaluation": after}],
                        "datafile": datafile(features={"source": feature(variablesSchema={"value": {"type": "integer", "defaultValue": 9}})},
                            variables={"source": {"type": "integer", "defaultValue": 9},
                                       "mutator": {"type": "integer", "defaultValue": 0},
                                       "root": {"type": "object", "compose": [
                                           {"key": "first", **reference}, {"key": "mutator", "variable": "mutator"},
                                           {"key": "last", **reference}]}})})
                    self.addCleanup(f.close)
                    child = f.spawn(options={"stickyFeatures": {"source": {"enabled": True, "variables": {"value": 1}}},
                                             "stickyVariables": {"source": 1}})
                    self.addCleanup(child.close)
                    self.assertEqual(child.get_variable("root"), {"first": 1, "mutator": 0, "last": 2})
                    self.assertEqual(reads, [1, 1])
                    self.assertIsNone(f.sticky_features)
                    self.assertIsNone(f.sticky_variables)

    def test_refresh_marker_is_absent_during_every_public_module_hook(self):
        seen = []

        def record(hook, options):
            seen.append(hook)
            self.assertNotIn("__featurevisor_refreshed_state_version", options)
            self.assertNotIn("_evaluationSession", options)
            self.assertFalse(any(key.startswith("__featurevisor_") for key in options))

        def before(options):
            record("before", options)
            return options

        def after(result, options):
            record("after", options)
            return result

        def bucket_key(options):
            record("bucketKey", options)
            return options["bucketKey"]

        def bucket_value(options):
            record("bucketValue", options)
            return options["bucketValue"]

        module = {"beforeEvaluation": before, "afterEvaluation": after, "bucketKey": bucket_key, "bucketValue": bucket_value}
        f = create_featurevisor({"logLevel": "fatal", "modules": [module], "datafile": datafile(
            features={"source": feature(variations=[{"value": "on"}],
                variablesSchema={"value": {"type": "integer", "defaultValue": 1}})},
            variables={"source": {"type": "integer", "defaultValue": 1},
                       "alias": {"type": "integer", "compose": "source"},
                       "root": {"type": "object", "compose": [
                           {"key": "global", "variable": "alias"},
                           {"key": "feature", "feature": "source", "variable": "value"}]}})})
        self.addCleanup(f.close)
        child = f.spawn()
        self.addCleanup(child.close)
        for target in (f, child):
            self.assertTrue(target.is_enabled("source"))
            target.get_variation("source")
            self.assertEqual(target.get_variable("source", "value"), 1)
            self.assertEqual(target.get_variable("source"), 1)
            self.assertEqual(target.get_variable("root"), {"global": 1, "feature": 1})
        self.assertEqual(set(seen), {"before", "after", "bucketKey", "bucketValue"})

        # A diagnostic can install the first module after before hooks were skipped.
        for global_variable in (False, True):
            late = create_featurevisor({"logLevel": "warn", "datafile": datafile(
                features={"late": feature(deprecated=True)},
                variables={"late": {"type": "integer", "defaultValue": 1, "deprecated": True}})})
            self.addCleanup(late.close)
            seen.clear()
            def diagnostic(item):
                if "deprecated" in item["code"] and not late.modules_manager.modules:
                    late.add_module(module)
            late.on_diagnostic = diagnostic
            if global_variable:
                self.assertEqual(late.get_variable("late"), 1)
            else:
                self.assertTrue(late.is_enabled("late"))
            self.assertIn("after", seen)
