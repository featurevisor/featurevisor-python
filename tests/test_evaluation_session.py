from __future__ import annotations

import copy
import gc
import unittest
import weakref

from featurevisor import create_featurevisor
from featurevisor._evaluation_session import EvaluationSession, _fingerprint


def graph_datafile() -> dict:
    requirement = lambda key: {"variable": key, "operator": "equals", "value": True}
    return {
        "schemaVersion": "2", "revision": "session", "segments": {}, "features": {},
        "variables": {
            "leaf": {"type": "boolean", "defaultValue": True},
            "left": {"type": "boolean", "defaultValue": True, "conditions": [requirement("leaf")]},
            "right": {"type": "boolean", "defaultValue": True, "conditions": [requirement("leaf")]},
            "root": {"type": "boolean", "defaultValue": True, "disabledValue": False,
                     "conditions": [requirement("left"), requirement("right")]},
        },
    }


class EvaluationSessionTests(unittest.TestCase):
    def test_session_owns_replacement_sticky_maps_until_root_boundary(self):
        class Sticky(dict):
            pass

        class Modules:
            def get_all(self):
                return []

        session = EvaluationSession()
        common = {"type": "flag", "featureKey": "gate", "context": {},
                  "dependencyOf": {"type": "flag", "featureKey": "root"},
                  "datafile": object(), "modulesManager": Modules()}
        first = Sticky({"unrelated": {"enabled": True}})
        first_ref = weakref.ref(first)
        first_key = session.key({**common, "stickyFeatures": first})
        del first
        gc.collect()
        self.assertIsNotNone(first_ref())

        replacement = Sticky({"unrelated": {"enabled": True}})
        replacement_ref = weakref.ref(replacement)
        replacement_key = session.key({**common, "stickyFeatures": replacement})
        self.assertNotEqual(first_key, replacement_key)
        del replacement
        gc.collect()
        self.assertIsNotNone(replacement_ref())

        with session.frame({"type": "flag", "featureKey": "root"}):
            pass
        gc.collect()
        self.assertIsNone(first_ref())
        self.assertIsNone(replacement_ref())

    def test_sticky_handoff_never_enumerates_unrelated_entries(self):
        class ObservedSticky(dict):
            visits = 0

            def items(self):
                self.visits += 1
                return super().items()

            def keys(self):
                self.visits += 1
                return super().keys()

            def values(self):
                self.visits += 1
                return super().values()

            def __iter__(self):
                self.visits += 1
                return super().__iter__()

        for kind in ("features", "variables"):
            for child_mode in (False, True):
                for size in (0, 100, 1000, 5000):
                    with self.subTest(kind=kind, child=child_mode, size=size):
                        datafile = graph_datafile()
                        datafile["features"]["gate"] = {
                            "bucketBy": "userId", "traffic": [
                                {"key": "all", "segments": "*", "percentage": 100000, "enabled": True},
                            ],
                        }
                        requirement = {"variable": "leaf", "operator": "equals", "value": True}
                        datafile["variables"]["root"] = {
                            "type": "boolean", "defaultValue": True,
                            **({"requiredFeatures": ["gate", "gate"]} if kind == "features"
                               else {"conditions": [requirement, requirement]}),
                        }
                        # An unsupported unrelated value also proves that a full
                        # sticky fingerprint has not merely moved elsewhere.
                        sticky = ObservedSticky({f"unrelated{i}": (
                            {"enabled": True, "variables": {"unused": object()}}
                            if kind == "features" else object()) for i in range(size)})
                        reads = []
                        def before(options):
                            if options.get("featureKey") == "gate" or options.get("variableKey") == "leaf":
                                reads.append(1)
                            return options
                        config = {"datafile": datafile, "logLevel": "fatal",
                                  "modules": [{"beforeEvaluation": before}]}
                        sticky_name = "stickyFeatures" if kind == "features" else "stickyVariables"
                        if not child_mode:
                            config[sticky_name] = sticky
                        sdk = create_featurevisor(config)
                        target = sdk.spawn({}, {sticky_name: sticky}) if child_mode else sdk
                        self.assertIs(target.get_variable("root", {"userId": "a"}), True)
                        self.assertEqual(len(reads), 1)
                        self.assertIs(target.get_variable("root", {"userId": "a"}), True)
                        self.assertEqual(len(reads), 2)
                        self.assertEqual(sticky.visits, 0)
                        if child_mode:
                            target.close()
                        sdk.close()

    def test_module_replaced_sticky_map_reaches_dependencies_and_reuses_result(self):
        datafile = graph_datafile()
        enabled = {"bucketBy": "userId", "traffic": [
            {"key": "all", "segments": "*", "percentage": 100000, "enabled": True},
        ]}
        datafile["features"] = {
            "gate": enabled,
            "root": {**enabled, "requiredFeatures": [
                {"feature": "gate", "enabled": False},
                {"feature": "gate", "enabled": False},
            ]},
        }
        reads = []
        def before(options):
            if options.get("featureKey") == "root":
                return {**options, "stickyFeatures": {"gate": {"enabled": False}}}
            if options.get("featureKey") == "gate":
                reads.append(1)
            return options
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before}]})
        self.assertTrue(sdk.is_enabled("root", {"userId": "a"}))
        self.assertEqual(len(reads), 1)
        sdk.close()

    def test_memo_fingerprint_preserves_observable_numeric_and_container_types(self):
        self.assertNotEqual(_fingerprint({"n": True}), _fingerprint({"n": 1}))
        self.assertNotEqual(_fingerprint({"n": 1}), _fingerprint({"n": 1.0}))
        self.assertNotEqual(_fingerprint({"n": 0.0}), _fingerprint({"n": -0.0}))
        self.assertNotEqual(_fingerprint([1]), _fingerprint((1,)))
        self.assertEqual(_fingerprint({"a": 1, "b": 2}), _fingerprint({"b": 2, "a": 1}))
        self.assertNotEqual(_fingerprint(None), _fingerprint({}))
        self.assertNotEqual(_fingerprint({}), _fingerprint({"value": None}))

    def test_internal_dependencies_keep_post_module_context_without_restoring_root_keys(self):
        datafile = graph_datafile()
        datafile["features"]["gate"] = {
            "bucketBy": "userId", "traffic": [{
                "key": "all", "segments": "*", "percentage": 100000, "enabled": True,
            }],
        }
        datafile["variables"]["root"] = {
            "type": "boolean", "defaultValue": True,
            "requiredFeatures": ["gate"],
            "conditions": [{"variable": "leaf", "operator": "equals", "value": True}],
        }
        seen = []
        def before(options):
            if options.get("variableKey") == "root":
                return {**options, "context": {}}
            if options.get("dependencyOf"):
                seen.append((options.get("featureKey"), options.get("variableKey"), dict(options["context"])))
            return options
        sdk = create_featurevisor({"datafile": datafile, "context": {"country": "de"},
                                   "logLevel": "fatal", "modules": [{"beforeEvaluation": before}]})
        self.assertIs(sdk.get_variable("root"), True)
        self.assertEqual(seen, [(None, "leaf", {}), ("gate", None, {})])

    def test_module_observable_numeric_types_do_not_share_memo_entries(self):
        datafile = graph_datafile()
        datafile["variables"]["mutator"] = {"type": "boolean", "defaultValue": True}
        datafile["variables"]["root"]["conditions"] = [
            {"variable": "leaf", "operator": "equals", "value": True},
            {"variable": "mutator", "operator": "equals", "value": True},
            {"variable": "leaf", "operator": "equals", "value": False},
        ]
        seen = []
        def after(evaluation, options):
            if options.get("variableKey") == "mutator":
                options["context"]["nested"]["value"] = 1.0
            if options.get("variableKey") == "leaf":
                value = options["context"]["nested"]["value"]
                seen.append(type(value))
                evaluation["variableValue"] = isinstance(value, int)
            return evaluation
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"afterEvaluation": after}]})
        self.assertIs(sdk.get_variable("root", {"nested": {"value": 1}}), True)
        self.assertEqual(seen, [int, float])
        sdk.close()

    def test_feature_requirement_keeps_post_module_context(self):
        datafile = graph_datafile()
        datafile["features"]["root"] = {
            "bucketBy": "userId", "traffic": [{
                "key": "all", "segments": "*", "percentage": 100000, "enabled": True,
            }],
            "conditions": [{"variable": "leaf", "operator": "equals", "value": True}],
        }
        seen = []
        def before(options):
            if options.get("featureKey") == "root":
                return {**options, "context": {}}
            if options.get("variableKey") == "leaf":
                seen.append(dict(options["context"]))
            return options
        sdk = create_featurevisor({"datafile": datafile, "context": {"country": "de"},
                                   "logLevel": "fatal", "modules": [{"beforeEvaluation": before}]})
        self.assertTrue(sdk.is_enabled("root"))
        self.assertEqual(seen, [{}])

    def test_unsupported_module_value_skips_memo_without_changing_getter(self):
        datafile = graph_datafile()
        datafile["variables"]["root"] = {
            "type": "integer", "defaultValue": 1,
            "conditions": [
                {"variable": "leaf", "operator": "exists"},
                {"variable": "leaf", "operator": "exists"},
            ],
        }
        reads = []
        def after(evaluation, options):
            if options.get("variableKey") == "leaf":
                reads.append(1)
                return {**evaluation, "variableValue": {1, 2}}
            return evaluation
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"afterEvaluation": after}]})
        self.assertEqual(sdk.get_variable("root"), 1)
        self.assertEqual(len(reads), 2)

    def test_module_result_is_copied_before_memoisation(self):
        datafile = graph_datafile()
        datafile["variables"]["root"] = {
            "type": "integer", "defaultValue": 1,
            "conditions": [
                {"variable": "leaf", "path": ["value"], "operator": "equals", "value": 1},
                {"variable": "mutator", "operator": "equals", "value": 1},
                {"variable": "leaf", "path": ["value"], "operator": "equals", "value": 1},
            ],
        }
        datafile["variables"]["mutator"] = {"type": "integer", "defaultValue": 1}
        shared = {"value": 1}
        reads = []
        def after(evaluation, options):
            if options.get("variableKey") == "leaf":
                reads.append(1)
                return {**evaluation, "variableValue": shared}
            if options.get("variableKey") == "mutator":
                shared["value"] = 2
            return evaluation
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"afterEvaluation": after}]})
        self.assertEqual(sdk.get_variable("root"), 1)
        self.assertEqual(len(reads), 1)

    def test_state_changes_during_hooks_invalidate_datafile_modules_and_context(self):
        for state in ("datafile", "modules", "context"):
            with self.subTest(state=state):
                datafile = graph_datafile()
                datafile["variables"]["leaf"] = {"type": "integer", "defaultValue": 1}
                datafile["variables"]["mutator"] = {"type": "integer", "defaultValue": 1}
                datafile["variables"]["root"] = {
                    "type": "integer", "defaultValue": 1,
                    "conditions": [
                        {"variable": "leaf", "operator": "equals", "value": 1},
                        {"variable": "mutator", "operator": "equals", "value": 1},
                        {"variable": "leaf", "operator": "equals", "value": 2},
                    ],
                }
                reads = []
                sdk = None
                def after(evaluation, options):
                    if options.get("variableKey") == "leaf":
                        reads.append(1)
                        if state == "context":
                            return {**evaluation, "variableValue": 2 if sdk.get_context().get("changed") else 1}
                    if options.get("variableKey") == "mutator":
                        if state == "datafile":
                            replacement = copy.deepcopy(datafile)
                            replacement["variables"]["leaf"]["defaultValue"] = 2
                            sdk.set_datafile(replacement, True)
                        elif state == "modules":
                            sdk.add_module({"afterEvaluation": lambda result, _: (
                                {**result, "variableValue": 2} if result.get("variableKey") == "leaf" else result
                            )})
                        else:
                            sdk.set_context({"changed": True})
                    return evaluation
                sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                           "modules": [{"afterEvaluation": after}]})
                self.assertEqual(sdk.get_variable("root"), 1)
                self.assertEqual(len(reads), 2)

    def test_hook_state_change_does_not_memoise_started_dependency(self):
        datafile = graph_datafile()
        datafile["variables"]["leaf"] = {"type": "integer", "defaultValue": 1}
        datafile["variables"]["root"] = {
            "type": "integer", "defaultValue": 1,
            "conditions": [
                {"variable": "leaf", "operator": "equals", "value": 1},
                {"variable": "leaf", "operator": "equals", "value": 2},
            ],
        }
        sdk = None
        reads = []
        def after(evaluation, options):
            if options.get("variableKey") == "leaf":
                reads.append(1)
                if len(reads) == 1:
                    sdk.set_sticky_variables({"leaf": 2})
            return evaluation
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"afterEvaluation": after}]})
        self.assertEqual(sdk.get_variable("root"), 1)
        self.assertEqual(len(reads), 2)

    def test_missed_override_selector_skips_cyclic_prerequisite(self):
        datafile = graph_datafile()
        datafile["variables"]["root"] = {
            "type": "integer", "defaultValue": 1,
            "overrides": [
                {"key": "skip", "conditions": [{"attribute": "country", "operator": "equals", "value": "nl"},
                                               {"variable": "root", "operator": "isNull"}], "value": 2},
                {"key": "take", "segments": "*", "value": 3},
            ],
        }
        seen = []
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": lambda options: seen.append(options.get("variableKey")) or options}]})
        self.assertEqual(sdk.get_variable("root", {"country": "us"}), 3)
        self.assertEqual(seen, ["root"])

    def test_dependency_memo_is_immediate_parent_aware_for_module_hooks(self):
        datafile = graph_datafile()
        datafile["variables"]["leaf"] = {"type": "integer", "defaultValue": 0}
        datafile["variables"]["left"]["conditions"] = [{"variable": "leaf", "operator": "equals", "value": 1}]
        datafile["variables"]["right"]["conditions"] = [{"variable": "leaf", "operator": "equals", "value": 2}]
        visits = []
        def before(options):
            if options.get("variableKey") == "leaf":
                parent = options["dependencyOf"]["variableKey"]
                visits.append(parent)
                return {**options, "parentSelectedValue": 1 if parent == "left" else 2}
            return options
        def after(evaluation, options):
            if options.get("variableKey") == "leaf":
                return {**evaluation, "variableValue": options["parentSelectedValue"]}
            return evaluation
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before, "afterEvaluation": after}]})
        self.assertIs(sdk.get_variable("root"), True)
        self.assertEqual(visits, ["left", "right"])

    def test_dependency_identities_distinguish_flag_variation_feature_and_global_variables(self):
        datafile = graph_datafile()
        datafile["features"] = {"source": {
            "bucketBy": "userId", "traffic": [{
                "key": "all", "segments": "*", "percentage": 100000,
                "enabled": True, "variation": "on",
            }],
            "variations": [{"value": "on"}],
            "variablesSchema": {"source": {"type": "integer", "defaultValue": 2}},
        }}
        datafile["variables"]["source"] = {"type": "integer", "defaultValue": 1}
        datafile["variables"]["root"] = {
            "type": "integer", "defaultValue": 1,
            "requiredFeatures": [
                {"feature": "source", "variation": "on"},
                {"feature": "source", "variation": "on"},
            ],
            "conditions": [
                {"variable": "source", "operator": "equals", "value": 1},
                {"feature": "source", "variable": "source", "operator": "equals", "value": 2},
                {"variable": "source", "operator": "equals", "value": 1},
            ],
        }
        seen = []
        def before(options):
            seen.append((options["type"], options.get("featureKey"), options.get("variableKey"), options.get("dependencyOf")))
            return options
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before}]})
        self.assertEqual(sdk.get_variable("root"), 1)
        self.assertEqual(len(seen), 5)
        self.assertEqual(len({entry[:3] for entry in seen}), 5)
        self.assertIsNone(seen[0][3])
        self.assertTrue(all(entry[3] == {"type": "variable", "variableKey": "root"} for entry in seen[1:]))

    def test_deep_diamond_is_linear_per_public_call(self):
        datafile = graph_datafile()
        datafile["variables"] = {"leaf": {"type": "integer", "defaultValue": 1}}
        previous = "leaf"
        for index in range(18):
            key = f"level{index}"
            requirement = {"variable": previous, "operator": "equals", "value": 1}
            datafile["variables"][key] = {
                "type": "integer", "defaultValue": 1,
                "conditions": [requirement, requirement],
            }
            previous = key
        visits = []
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": lambda options: visits.append(options["variableKey"]) or options}]})
        self.assertEqual(sdk.get_variable(previous), 1)
        self.assertEqual(len(visits), 19)
        self.assertEqual(sdk.get_variable(previous), 1)
        self.assertEqual(len(visits), 38)

    def test_feature_diamond_is_linear_and_uses_immediate_parent(self):
        datafile = graph_datafile()
        datafile["variables"] = {}
        enabled = {"bucketBy": "userId", "traffic": [
            {"key": "all", "segments": "*", "percentage": 100000, "enabled": True},
        ]}
        datafile["features"] = {"leaf": dict(enabled)}
        previous = "leaf"
        for index in range(18):
            key = f"level{index}"
            datafile["features"][key] = {**enabled, "requiredFeatures": [previous, previous]}
            previous = key
        visits = []
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": lambda options: visits.append((options["featureKey"], options.get("dependencyOf"))) or options}]})
        self.assertTrue(sdk.is_enabled(previous))
        self.assertEqual(len(visits), 19)
        self.assertIsNone(visits[0][1])
        self.assertEqual(visits[-1][1], {"type": "flag", "featureKey": "level0"})

    def test_global_and_feature_cycles_are_errors(self):
        datafile = graph_datafile()
        datafile["variables"]["cycle"] = {
            "type": "boolean", "defaultValue": True,
            "conditions": [{"variable": "cycle", "operator": "isNull"}],
        }
        datafile["features"]["cycle"] = {"requiredFeatures": ["cycle"]}
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
        for evaluation in (sdk.evaluate_variable("cycle"), sdk.evaluate_flag("cycle")):
            self.assertEqual(evaluation["reason"], "error")
            self.assertIn("Circular", str(evaluation["error"]))

    def test_cycle_checks_post_module_identity(self):
        datafile = graph_datafile()
        datafile["variables"]["root"] = {
            "type": "boolean", "defaultValue": True,
            "conditions": [{"variable": "alias", "operator": "equals", "value": True}],
        }
        datafile["variables"]["other"] = {
            "type": "boolean", "defaultValue": True,
            "conditions": [{"variable": "other", "operator": "isNull"}],
        }
        def before(options):
            return {**options, "variableKey": "other"} if options.get("variableKey") == "alias" else options
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before}]})
        evaluation = sdk.evaluate_variable("root")
        self.assertEqual(evaluation["reason"], "error")
        self.assertIn("Circular", str(evaluation["error"]))

    def test_retained_module_options_release_session_after_success_and_error(self):
        for throws in (False, True):
            datafile = graph_datafile()
            retained = []
            def before(options):
                retained.append(options)
                replacement = {**options}
                retained.append(replacement)
                if throws and options.get("variableKey") == "leaf":
                    raise RuntimeError("ordinary module error")
                return replacement
            sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                       "modules": [{"beforeEvaluation": before}]})
            sdk.get_variable("root")
            for options in retained:
                self.assertNotIn("_evaluationSession", options)
                self.assertNotIn("__featurevisor_state_version", options)

    def test_ordinary_module_error_is_memoised_as_null_operand(self):
        datafile = graph_datafile()
        datafile["variables"]["root"] = {
            "type": "boolean", "defaultValue": True,
            "conditions": [
                {"variable": "broken", "operator": "isNull"},
                {"variable": "broken", "operator": "isNull"},
            ],
        }
        visits = []
        def before(options):
            visits.append(options["variableKey"])
            if options["variableKey"] == "broken":
                raise RuntimeError("module failed")
            return options
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before}]})
        self.assertTrue(sdk.get_variable("root"))
        self.assertEqual(visits, ["root", "broken"])

    def test_ordinary_feature_module_error_is_false_prerequisite_operand(self):
        datafile = graph_datafile()
        datafile["features"] = {"root": {
            "bucketBy": "userId", "traffic": [{
                "key": "all", "segments": "*", "percentage": 100000, "enabled": True,
            }],
            "requiredFeatures": [{"feature": "broken", "enabled": False}, {"not": ["broken"]}],
        }}
        visits = []
        def before(options):
            visits.append(options.get("featureKey"))
            if options.get("featureKey") == "broken":
                raise RuntimeError("module failed")
            return options
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before}]})
        self.assertTrue(sdk.is_enabled("root"))
        self.assertEqual(visits, ["root", "broken"])

    def test_state_change_during_hook_rechecks_dependency(self):
        datafile = graph_datafile()
        datafile["variables"]["root"] = {
            "type": "integer", "defaultValue": 1,
            "conditions": [
                {"variable": "leaf", "operator": "equals", "value": True},
                {"variable": "mutator", "operator": "equals", "value": 1},
                {"variable": "leaf", "operator": "equals", "value": 2},
            ],
        }
        datafile["variables"]["mutator"] = {"type": "integer", "defaultValue": 1}
        reads = []
        sdk = None
        def after(evaluation, options):
            if options.get("variableKey") == "leaf":
                reads.append(evaluation.get("variableValue"))
            if options.get("variableKey") == "mutator":
                sdk.set_sticky_variables({"leaf": 2})
            return evaluation
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"afterEvaluation": after}]})
        self.assertEqual(sdk.get_variable("root"), 1)
        self.assertEqual(len(reads), 2)

    def test_child_sticky_change_and_restoration_do_not_resurrect_memo(self):
        datafile = graph_datafile()
        datafile["variables"]["leaf"] = {"type": "integer", "defaultValue": 1}
        datafile["variables"]["restore"] = {"type": "integer", "defaultValue": 1}
        datafile["variables"]["root"] = {
            "type": "integer", "defaultValue": 1,
            "conditions": [
                {"variable": "leaf", "operator": "equals", "value": 2},
                {"variable": "restore", "operator": "equals", "value": 1},
                {"variable": "leaf", "operator": "equals", "value": 1},
            ],
        }
        child = None
        reads = []
        def before(options):
            if options.get("variableKey") == "leaf":
                reads.append(1)
                if len(reads) == 1:
                    child.set_sticky_variables({"leaf": 2})
            if options.get("variableKey") == "restore":
                child.set_sticky_variables({"leaf": 1}, True)
            return options
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before}]})
        child = sdk.spawn(options={"stickyVariables": {"leaf": 1}})
        self.assertEqual(child.get_variable("root"), 1)
        self.assertEqual(len(reads), 2)

    def test_diamond_callbacks_are_once_per_request_and_mark_immediate_parent(self):
        visits: list[tuple[str, dict | None]] = []

        def before(options):
            visits.append((options.get("variableKey", ""), options.get("dependencyOf")))
            return options

        sdk = create_featurevisor({"datafile": graph_datafile(), "logLevel": "fatal",
                                   "modules": [{"name": "observe", "beforeEvaluation": before}]})
        self.assertIs(sdk.get_variable("root"), True)
        self.assertEqual([key for key, _ in visits], ["root", "left", "leaf", "right", "leaf"])
        self.assertIsNone(visits[0][1])
        self.assertEqual(visits[2][1], {"type": "variable", "variableKey": "left"})
        self.assertIs(sdk.get_variable("root"), True)
        self.assertEqual([key for key, _ in visits].count("leaf"), 4)

    def test_reentrant_public_getter_owns_a_new_stack(self):
        sdk = None
        reentered = False

        def before(options):
            nonlocal reentered
            if options.get("variableKey") == "leaf" and not reentered:
                reentered = True
                self.assertIs(sdk.get_variable("leaf"), True)
            return options

        sdk = create_featurevisor({"datafile": graph_datafile(), "logLevel": "fatal",
                                   "modules": [{"name": "reenter", "beforeEvaluation": before}]})
        self.assertIs(sdk.get_variable("root"), True)
        self.assertTrue(reentered)

    def test_reentrant_root_evaluation_has_no_dependency_marker(self):
        sdk = None
        inside = False
        reentered = False
        markers = []
        leaf_reads = []
        def before(options):
            nonlocal inside, reentered
            if options.get("variableKey") == "root":
                markers.append(options.get("dependencyOf"))
            if options.get("variableKey") == "leaf":
                leaf_reads.append(1)
                if not inside and not reentered:
                    inside = True
                    reentered = True
                    try:
                        self.assertIs(sdk.get_variable("root"), True)
                    finally:
                        inside = False
            return options
        sdk = create_featurevisor({"datafile": graph_datafile(), "logLevel": "fatal",
                                   "modules": [{"beforeEvaluation": before}]})
        self.assertIs(sdk.get_variable("root"), True)
        self.assertEqual(markers, [None, None])
        self.assertEqual(len(leaf_reads), 4)

    def test_context_transforms_keep_dependency_reads_separate(self):
        countries: list[str] = []

        def before(options):
            key = options.get("variableKey")
            if key in {"left", "right"}:
                options = {**options, "context": {**options["context"], "country": key}}
            if key == "leaf":
                countries.append(options["context"].get("country"))
            return options

        sdk = create_featurevisor({"datafile": graph_datafile(), "logLevel": "fatal",
                                   "modules": [{"name": "transform", "beforeEvaluation": before}]})
        self.assertIs(sdk.get_variable("root"), True)
        self.assertEqual(countries, ["left", "right"])

    def test_requirement_path_failure_does_not_become_an_is_null_match(self):
        datafile = graph_datafile()
        datafile["variables"]["source"] = {
            "type": "boolean", "defaultValue": True,
            "conditions": [{"variable": "leaf", "path": "uncompiled", "operator": "isNull"}],
        }
        datafile["variables"]["subject"] = {
            "type": "boolean", "defaultValue": True,
            "conditions": [{"variable": "source", "operator": "isNull"}],
        }
        sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
        self.assertIsNone(sdk.get_variable("source"))
        self.assertEqual(sdk.evaluate_variable("subject")["reason"], "error")


if __name__ == "__main__":
    unittest.main()
