from __future__ import annotations

import json
import unittest

from featurevisor import create_featurevisor
from featurevisor.test_checks import evaluate_checks_result


CASES = json.loads(r'''
[
  ["strings",["read","write"],["admin","write"],true],
  ["no match",["read"],["write"],false],
  ["mixed primitives",["one",2,true,null],["missing",2,false],true],
  ["null",[null],[null],true],
  ["true",[true],[true],true],
  ["false",[false],[false],true],
  ["numeric equality",[1],[1],true],
  ["number not boolean",[1],[true],false],
  ["boolean not number",[true],[1],false],
  ["zero not false",[0],[false],false],
  ["false not zero",[false],[0],false],
  ["number not string",[1],["1"],false],
  ["string not number",["1"],[1],false],
  ["null not false",[null],[false],false],
  ["duplicates",["write","write"],["write","write"],true],
  ["candidate order",["read","write"],["write","read"],true],
  ["empty candidate",["read"],[],false],
  ["empty actual",[],["read"],false],
  ["both empty",[],[],false],
  ["structured actual ignored",[{},[],{"x":1},"read"],["read"],true],
  ["only structured actual",[{},[],{"x":1}],[null,1],false],
  ["null actual",null,[],null],
  ["string actual","read",["read"],null],
  ["number actual",1,[1],null],
  ["boolean actual",true,[true],null],
  ["object actual",{},[],null],
  ["numeric object actual",{"0":"read"},["read"],null],
  ["null candidate",["read"],null,null],
  ["scalar candidate",["read"],"read",null],
  ["object candidate",["read"],{},null],
  ["nested candidate after match",["read"],["read",[]],null],
  ["object candidate after match",["read"],["read",{}],null],
  ["invalid before match",["read"],[{},"read"],null],
  ["invalid candidate empty actual",[],[{}],null]
]
''')


class IncludesAnyTests(unittest.TestCase):
    def test_runtime_requirements_and_checks(self):
        for name, actual, candidate, matched in CASES:
            for operator in ("includesAny", "notIncludesAny"):
                expected = matched is not None and (matched if operator == "includesAny" else not matched)
                with self.subTest(case=name, operator=operator):
                    requirement = {"variable": "source", "path": ["items"], "operator": operator, "value": candidate}
                    traffic = [{"key": "all", "segments": "*", "percentage": 100000, "allocation": []}]
                    source = {"type": "object", "defaultValue": {"items": actual}}
                    datafile = {
                        "schemaVersion": "2", "revision": "any", "segments": {},
                        "features": {
                            "sourceFeature": {"bucketBy": "userId", "traffic": traffic, "variablesSchema": {"source": source}},
                            "condition": {"bucketBy": "userId", "traffic": [], "force": [{
                                "conditions": {"attribute": "profile.items", "operator": operator, "value": candidate}, "enabled": True,
                            }]},
                        },
                        "variables": {"source": source, "other": {"type": "object", "defaultValue": {"items": candidate}}},
                    }
                    for feature_source in (False, True):
                        selected = {**requirement, **({"feature": "sourceFeature"} if feature_source else {})}
                        for group in ("direct", "not", "or"):
                            requirements = [selected] if group == "direct" else [{group: [selected]}]
                            key = f"gate{feature_source}{group}"
                            datafile["variables"][key] = {"type": "boolean", "defaultValue": True, "disabledValue": False, "conditions": requirements}
                            datafile["features"][key] = {"bucketBy": "userId", "traffic": traffic, "conditions": requirements}
                    sdk = create_featurevisor({"datafile": datafile, "logLevel": "fatal"})
                    try:
                        self.assertEqual(sdk.is_enabled("condition", {"profile": {"items": actual}}), expected)
                        self.assertFalse(sdk.is_enabled("condition", {"profile": {}}))
                        condition = datafile["features"]["condition"]["force"][0]["conditions"]
                        for group in (None, "not", "or"):
                            expression = condition if group is None else {group: [condition]}
                            datafile["features"]["condition"]["force"][0]["conditions"] = json.dumps(expression)
                            sdk.set_datafile(datafile, replace=True)
                            self.assertEqual(sdk.is_enabled("condition", {"profile": {"items": actual}}), not expected if group == "not" else expected)
                        for feature_source in (False, True):
                            selected = {**requirement, **({"feature": "sourceFeature"} if feature_source else {})}
                            for group in ("direct", "not", "or"):
                                key = f"gate{feature_source}{group}"
                                self.assertEqual(sdk.get_variable(key), not expected if group == "not" else expected)
                                self.assertEqual(sdk.is_enabled(key), not expected if group == "not" else expected)
                            for compare_to in (False, True):
                                check = dict(selected)
                                if compare_to:
                                    del check["value"]
                                    check["compareTo"] = {"variable": "other", "path": ["items"]}
                                for group in ("direct", "not", "or"):
                                    node = check if group == "direct" else {group: [check]}
                                    report = evaluate_checks_result(sdk, datafile, [node], owner_variable=None, diagnostics=[])
                                    self.assertEqual(report["error"], matched is None)
                                    self.assertEqual(report["passed"], matched is not None and (not expected if group == "not" else expected))
                                    if matched is None:
                                        self.assertEqual(report["errors"][0]["type"], "check")
                                        self.assertIn(report["errors"][0]["expected"], ("valid checks", "successful comparison"))
                                        if compare_to:
                                            self.assertTrue(report["errors"][0]["details"]["evaluated"])
                        check = {"variable": "source", "path": ["absent"], "operator": operator, "value": []}
                        report = evaluate_checks_result(sdk, datafile, [check], owner_variable=None, diagnostics=[])
                        self.assertFalse(report["error"])
                        self.assertFalse(report["passed"])
                        malformed = {"variable": "source", "path": ["items"], "operator": operator, "compareTo": {"variable": "other", "path": ["items"]}}
                        report = evaluate_checks_result(sdk, datafile, [{"or": [{"variable": "source", "operator": "exists"}, malformed]}], owner_variable=None, diagnostics=[])
                        self.assertTrue(report["passed"])
                        self.assertFalse(report["error"])
                    finally:
                        sdk.close()
