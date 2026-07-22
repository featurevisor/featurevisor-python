from __future__ import annotations

import sys
from typing import Any, Callable, Literal, TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired
else:
    from typing_extensions import NotRequired

AttributeValue = Any
VariableValue = Any
VariationValue = Any
Context = dict[str, AttributeValue]
FeatureKey = str
VariableKey = str
SegmentKey = str
RuleKey = str
BucketKey = str
BucketValue = int

LogLevel = Literal["fatal", "error", "warn", "info", "debug"]
EventName = Literal["datafile_set", "context_set", "sticky_set", "error"]


class EvaluatedFeature(TypedDict, total=False):
    enabled: bool
    variation: VariationValue
    variables: dict[VariableKey, VariableValue]


StickyFeatures = dict[FeatureKey, EvaluatedFeature]
EvaluatedFeatures = dict[FeatureKey, EvaluatedFeature]


class VariableOverride(TypedDict, total=False):
    conditions: Any
    segments: Any
    value: VariableValue


class Variation(TypedDict, total=False):
    value: VariationValue
    weight: int
    variables: dict[VariableKey, VariableValue]
    variableOverrides: dict[VariableKey, list[VariableOverride]]


class RequiredFeatureRef(TypedDict, total=False):
    key: FeatureKey
    variation: VariationValue


class VariableSchema(TypedDict, total=False):
    type: str
    defaultValue: VariableValue
    disabledValue: VariableValue
    useDefaultWhenDisabled: bool
    deprecated: bool


class Allocation(TypedDict):
    variation: VariationValue
    range: list[int]


class Traffic(TypedDict, total=False):
    key: RuleKey
    segments: Any
    percentage: int
    enabled: bool
    variation: VariationValue
    variables: dict[VariableKey, VariableValue]
    variationWeights: dict[str, int]
    variableOverrides: dict[VariableKey, list[VariableOverride]]
    allocation: list[Allocation]


class Force(TypedDict, total=False):
    conditions: Any
    segments: Any
    enabled: bool
    variation: VariationValue
    variables: dict[VariableKey, VariableValue]


class Feature(TypedDict, total=False):
    key: FeatureKey
    hash: str
    deprecated: bool
    required: list[str | RequiredFeatureRef]
    variablesSchema: dict[VariableKey, VariableSchema]
    disabledVariationValue: VariationValue
    variations: list[Variation]
    bucketBy: str | list[str] | dict[str, list[str]]
    traffic: list[Traffic]
    force: list[Force]
    ranges: list[list[int]]


class Segment(TypedDict, total=False):
    archived: bool
    description: str
    conditions: Any


class DatafileContent(TypedDict):
    schemaVersion: str
    revision: str
    featurevisorVersion: NotRequired[str]
    segments: dict[SegmentKey, Segment]
    features: dict[FeatureKey, Feature]


class Evaluation(TypedDict, total=False):
    type: Literal["flag", "variation", "variable"]
    featureKey: FeatureKey
    reason: str
    bucketKey: BucketKey
    bucketValue: BucketValue
    ruleKey: RuleKey
    error: Exception
    enabled: bool
    traffic: Traffic
    forceIndex: int
    force: Force
    required: list[str | RequiredFeatureRef]
    sticky: EvaluatedFeature
    variation: Variation
    variationValue: VariationValue
    variableKey: VariableKey
    variableValue: VariableValue
    variableSchema: VariableSchema
    variableOverrideIndex: int


class FeaturevisorDiagnostic(TypedDict, total=False):
    level: LogLevel
    code: str
    message: str
    module: str
    moduleName: str
    originalError: Exception
    details: dict[str, Any]


class FeaturevisorModule(TypedDict, total=False):
    name: str
    before: Callable[[dict[str, Any]], dict[str, Any]]
    bucketKey: Callable[[dict[str, Any]], BucketKey]
    bucketValue: Callable[[dict[str, Any]], BucketValue]
    after: Callable[[Evaluation, dict[str, Any]], Evaluation]
    setup: Callable[[dict[str, Any]], None]
    close: Callable[[], None]


class TestResultAssertionError(TypedDict, total=False):
    type: str
    expected: Any
    actual: Any
    message: str
    details: dict[str, Any]


class TestResultAssertion(TypedDict, total=False):
    description: str
    duration: int
    passed: bool
    errors: list[TestResultAssertionError]


class TestResult(TypedDict, total=False):
    type: str
    key: str
    notFound: bool
    passed: bool
    duration: int
    assertions: list[TestResultAssertion]


class CLIOptions(TypedDict, total=False):
    projectDirectoryPath: str
    environment: str
    feature: str
    variable: str
    context: str
    keyPattern: str
    assertionPattern: str
    onlyFailures: bool
    quiet: bool
    verbose: bool
    showDatafile: bool
    variation: bool
    n: int
    inflate: int
    with_scopes: bool
    with_tags: bool
    schema_version: str
    populateUuid: list[str]


class ProjectConfig(TypedDict, total=False):
    environments: list[str] | bool
    datafilesDirectoryPath: str
    datafileNamePattern: str
    testsDirectoryPath: str
    featuresDirectoryPath: str
    segmentsDirectoryPath: str
