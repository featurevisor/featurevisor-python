# Featurevisor Python SDK <!-- omit in toc -->

Use the Featurevisor Python SDK to evaluate flags, variations and variables in Python applications.

The package name is `featurevisor`, and it targets Python 3.10+.

## Table of contents <!-- omit in toc -->

- [Installation](#installation)
- [Initialization](#initialization)
  - [Public API](#public-api)
  - [Evaluation types](#evaluation-types)
- [Context](#context)
  - [Setting initial context](#setting-initial-context)
  - [Setting after initialization](#setting-after-initialization)
  - [Replacing existing context](#replacing-existing-context)
  - [Manually passing context](#manually-passing-context)
- [Check if enabled](#check-if-enabled)
- [Getting variation](#getting-variation)
- [Getting variables](#getting-variables)
  - [Getting feature variables](#getting-feature-variables)
  - [Getting global variables](#getting-global-variables)
  - [Conditions and composition](#conditions-and-composition)
  - [Type specific methods](#type-specific-methods)
- [Getting evaluations](#getting-evaluations)
- [Sticky](#sticky)
  - [Initialize with sticky](#initialize-with-sticky)
  - [Set sticky afterwards](#set-sticky-afterwards)
- [Setting datafile](#setting-datafile)
  - [Merging by default](#merging-by-default)
  - [Replacing](#replacing)
  - [Loading datafiles on demand](#loading-datafiles-on-demand)
  - [Updating datafile](#updating-datafile)
  - [Interval-based update](#interval-based-update)
- [Diagnostics](#diagnostics)
  - [Levels](#levels)
  - [Handler](#handler)
- [Events](#events)
  - [`datafile_set`](#datafile_set)
  - [`context_set`](#context_set)
  - [`sticky_features_set` and `sticky_variables_set`](#sticky_features_set-and-sticky_variables_set)
  - [`error`](#error)
- [Evaluation details](#evaluation-details)
- [Modules](#modules)
  - [Defining a module](#defining-a-module)
  - [Registering modules](#registering-modules)
- [Child instance](#child-instance)
- [Close](#close)
- [OpenFeature](#openfeature)
  - [Installation](#installation-1)
  - [Provider setup](#provider-setup)
  - [Flag key mapping](#flag-key-mapping)
  - [Context mapping](#context-mapping)
  - [Resolution details](#resolution-details)
  - [Tracking](#tracking)
  - [Using an existing Featurevisor instance](#using-an-existing-featurevisor-instance)
- [CLI usage](#cli-usage)
  - [Test](#test)
  - [Benchmark](#benchmark)
  - [Assess distribution](#assess-distribution)
- [GitHub repositories](#github-repositories)
- [Development](#development)
- [Releasing](#releasing)
- [License](#license)

<!-- FEATUREVISOR_DOCS_BEGIN -->

## Installation

Upgrading an existing application? Follow the [Python migration guide](https://featurevisor.com/docs/migrations/v4/python/).

```bash
pip install "featurevisor>=4.0.0,<5"
```

## Initialization

Initialize the SDK with Featurevisor datafile content:

```python
from urllib.request import urlopen
import json

from featurevisor import create_featurevisor

datafile_url = "https://cdn.yoursite.com/featurevisor-all.json"

with urlopen(datafile_url) as response:
    datafile_content = json.load(response)

f = create_featurevisor({
    "datafile": datafile_content,
})
try:
    print(f.is_enabled("checkout", {"userId": "123"}))
finally:
    f.close()
```

This example closes the instance after evaluation. In a long running application, keep it alive for subsequent evaluations and close it during shutdown. HTTP failures and invalid JSON propagate to the caller before initialization.

### Public API

The main runtime API is `create_featurevisor()`:

```python
from featurevisor import Featurevisor, create_featurevisor

f: Featurevisor = create_featurevisor({
    "datafile": datafile_content,
})
```

Most applications only need `create_featurevisor` and the `Featurevisor` instance type. Public extension and observability APIs include `FeaturevisorModule`, diagnostics, events, and the datafile dictionaries accepted by the factory.

Concurrent evaluations are safe after an instance is configured. Do not mutate or close the same instance concurrently with evaluations. Serialize calls to `set_datafile`, `set_context`, `set_sticky_features`, `set_sticky_variables`, `add_module`, `remove_module`, and `close`. Module, event, and diagnostic callbacks must synchronize mutable state that they capture.

### Evaluation types

Featurevisor evaluates four kinds of values:

- [**Flag**](#check-if-enabled) (`bool`): whether the feature is enabled or not
- [**Variation**](#getting-variation) (`str`): the variation of the feature (if any)
- [**Feature variable**](#getting-feature-variables): a configuration value owned by a feature
- [**Global variable**](#getting-global-variables): a configuration value evaluated independently of a feature

## Context

Context is a plain dictionary of attribute values used during evaluation:

Conditions compare these attributes using the [operators](https://featurevisor.com/docs/operators/), including `empty` and `notEmpty`.

Date comparisons accept portable timestamp strings and aware `datetime` values with a usable UTC offset. Plain `date` and naive `datetime` values are not accepted. Offsets must stay within 14 hours and the UTC year within 0001 to 9999. Fractions are truncated to milliseconds after conversion to UTC.

```python
context = {
    "userId": "123",
    "country": "nl",
}
```

### Setting initial context

Set context when you create the instance:

```python
f = create_featurevisor({
    "context": {
        "deviceId": "123",
        "country": "nl",
    },
})
```

### Setting after initialization

Merge more context later:

```python
f.set_context({
    "userId": "234",
})
```

### Replacing existing context

Or replace the existing context:

```python
f.set_context(
    {
        "deviceId": "123",
        "userId": "234",
        "country": "nl",
        "browser": "chrome",
    },
    True,
)
```

### Manually passing context

Pass additional context to one evaluation:

```python
is_enabled = f.is_enabled("checkout", {"country": "nl"})
variation = f.get_variation("checkout", {"country": "nl"})
feature_variable = f.get_variable("checkout", "bgColor", {"country": "nl"})
global_variable = f.get_variable("supportEmail", {"country": "nl"})
```

## Check if enabled

```python
if f.is_enabled("checkout"):
    pass
```

## Getting variation

```python
variation = f.get_variation("checkout")

if variation == "treatment":
    pass
```

## Getting variables

### Getting feature variables

```python
bg_color = f.get_variable("checkout", "bgColor")
```

### Getting global variables

Global variables use the same methods without a feature key:

```python
support_email = f.get_variable("supportEmail", {"country": "nl"})
evaluation = f.evaluate_variable("supportEmail", {"country": "nl"})
```

Conditions, composition and overrides are applied for you. See [Variables](https://featurevisor.com/docs/variables/#global-variables) and [Conditions](https://featurevisor.com/docs/conditions/) for authoring guidance.

To see which overrides changed a value, inspect `variableOverridePaths` in the detailed evaluation:

```python
evaluation = f.evaluate_variable("runtimePolicy", {"country": "nl"})
value = evaluation.get("variableValue")
matched_paths = evaluation.get("variableOverridePaths")
```

Modules should return the same result for the same input during one evaluation. Pass values that change through context.

### Conditions and composition

Schema version `"4"` uses one condition language for segments, root gates, force entries, traffic and variable overrides. Leaves compare attributes, reference a segment by its literal key, check a feature's enabled state and optional variation, or compare an ordinary variable getter result. A variable condition can specify its feature owner and a compiled path such as `["items", 0, "enabled"]`.

Arrays imply AND. Groups use `and`, `or` and `not`. NOT negates the AND of its direct children. Empty expressions are false. Conditions preserve order and short circuiting. Missing paths remain distinct from explicit null.

Feature evaluation checks sticky state, force entries, the root gate, then traffic and variable overrides. A failed feature or global variable root gate reports `conditions_unmet` and records the normalized expression in `evaluation.conditions`. Variation and feature variable disabled reasons are unchanged.

A global variable can compose ordinary getter results:

```python
datafile_content = {
    "schemaVersion": "4",
    "revision": "example",
    "segments": {},
    "features": {},
    "variables": {
        "policy": {"type": "object", "defaultValue": {"limit": 50}},
        "screen": {"type": "object", "compose": [
            {"key": "policy", "variable": "policy"},
        ]},
        "policyAlias": {"type": "object", "compose": "policy"},
    },
}
f = create_featurevisor({"datafile": datafile_content})
try:
    screen = f.get_variable("screen")
    policy = f.get_variable("policyAlias")
finally:
    f.close()
```

The compiled list creates a fresh outer dictionary with literal property names. Entries may read global variables, feature variables, enabled flags or variations. A string `compose` is an alias of one literal global variable key and returns its value directly. False, zero, empty strings, arrays and null remain ordinary source values. JSON is decoded exactly once, including alias chains.

Compositions have no `defaultValue` or `useDefaultWhenDisabled`. They may have root conditions and `disabledValue`. An unmet gate skips every source and override, returning the disabled value or null. Whole sticky state bypasses source reads. Otherwise all sources are read before the existing override program runs. Detailed evaluations report `variable_composed`, or `variable_override_rule` when an override matches.

Sources use effective context, modules and child sticky state without inheriting an outer explicit default. SDK mutations copy changed paths and never change source, sibling or sticky values. Treat supplied datafiles and returned dictionaries and lists as read only. Copy them before application mutations. Cycles and more than 256 dependency handoffs fail the entire evaluation, with no partial composition returned. Python's interpreter recursion limit can stop a very deep chain sooner. With the default recursion limit of 1000, a chain of about 120 feature handoffs fits. This depends on the host and the caller's own stack. Lint warns about chains deeper than 32, which fit comfortably. The logical bound counts active dependency frames rather than every entity name or terminal attribute predicate. The SDK preserves the host application's recursion setting. Exhaustion returns an error evaluation, and later independent evaluations remain usable.

Schema version `"2"` selectors are normalized eagerly before merging, without changing caller objects or their informational version metadata. Schema version `"4"` entities normalize lazily on their first read. Partial updates invalidate only incoming entities. Replacement clears all normalized entity caches. Concurrent evaluations and background datafile refreshes are supported. Diagnostic handlers may evaluate the same entity again.

Malformed stringified selector JSON fails that entire selector, even through NOT or a segment alias, while other entities remain usable. An error diagnostic with code `conditions_parse_error` is reported once per selector per incoming entity load, eagerly for schema version `"2"` and on first read for schema version `"4"`. Its details contain exactly `conditions`, `entityType` and `entityKey`. Accepted data is stored before eager callbacks, and lazy results are cached before callbacks, so diagnostic handlers can safely reenter evaluation.

### Type specific methods

Typed convenience methods are also available:

```python
f.get_variable_boolean(feature_key, variable_key, context={})
f.get_variable_string(feature_key, variable_key, context={})
f.get_variable_integer(feature_key, variable_key, context={})
f.get_variable_number(feature_key, variable_key, context={})
f.get_variable_array(feature_key, variable_key, context={})
f.get_variable_object(feature_key, variable_key, context={})
f.get_variable_json(feature_key, variable_key, context={})
```

The same helpers evaluate global variables when the feature key is omitted:

```python
f.get_variable_string("supportEmail", context={})
f.get_variable_object("checkoutSettings", context={})
```

Type specific methods do not coerce values. `get_variable_integer()` returns `None` for the string `"1"`, and boolean getters return `None` for non-boolean values.

`get_variable_number()` reads a variable of type `number` as a float, whether it holds `3` or `4.5`. Datafiles built before v4 call that type `double`, and it reads the same way. The v3 name `get_variable_double()` was removed in v4. Each method also has a camelCase name, such as `getVariableNumber()`.

## Getting evaluations

```python
features = f.get_feature_evaluations()
variables = f.get_variable_evaluations()
```

## Sticky

### Initialize with sticky

Pin feature evaluations with sticky values:

Sticky values belong to an SDK or child instance. Evaluation options do not accept sticky overrides. Pass `stickyFeatures` or `stickyVariables` when a child needs its own state.

Treat supplied sticky maps and their nested values as read only after handoff. Use the instance or child sticky setters to change them. Mutating a supplied map in place is not supported.

```python
f = create_featurevisor({
    "stickyFeatures": {
        "checkout": {
            "enabled": True,
            "variation": "treatment",
            "variables": {
                "myVariableKey": "myVariableValue",
            },
        },
    },
    "stickyVariables": {
        "supportEmail": "sticky@example.com",
    },
})
```

### Set sticky afterwards

Or update them later:

```python
f.set_sticky_features({
    "checkout": {
        "enabled": False,
    }
})

f.set_sticky_variables({"supportEmail": "new@example.com"})
```

## Setting datafile

You may initialize the SDK without passing `datafile`, and set it later on. The SDK accepts either parsed JSON content or a JSON string:

```python
f.set_datafile(datafile_content)
f.set_datafile(json.dumps(datafile_content))
```

`set_datafile` returns `True` when the datafile is accepted, including unchanged content. It returns `False` when the datafile is rejected or the instance is closed. Rejected datafiles leave the last accepted datafile in place. Diagnostics and events keep their existing behaviour. The `setDatafile` alias returns the same boolean.

### Merging by default

By default, `set_datafile(datafile)` merges incoming content into the SDK's current datafile:

- top-level metadata such as `schemaVersion`, `revision`, and `featurevisorVersion` comes from the incoming datafile
- `segments` are merged, with incoming entries overriding existing ones
- `features` are merged, with incoming entries overriding existing ones
- global `variables` are merged, with incoming entries overriding existing ones

Call `set_datafile` with different datafiles to merge their features, segments, and global variables. Use this to [load datafiles on demand](#loading-datafiles-on-demand).

### Replacing

To fully replace the stored datafile, pass `replace=True`:

```python
f.set_datafile(datafile_content, replace=True)
```

### Loading datafiles on demand

Merge datafiles for different [targets](https://featurevisor.com/docs/targets/) into the same instance. Here, the two datafile values have already been loaded and validated by your application from the same published release:

```python
f.set_datafile(products_datafile)
f.set_datafile(checkout_datafile)
```

Use immutable release URLs, check HTTP responses and keep shared dependencies consistent. See [Loading datafiles on demand](https://featurevisor.com/docs/use-cases/on-demand-datafiles/) for the complete loading and refresh flow.

### Updating datafile

Each accepted datafile update emits a [`datafile_set`](#datafile-set) event. Subscribe to react to updates.

### Interval-based update

Replace the complete datafile on each refresh so definitions removed upstream are removed from the SDK too. Use merging only when loading partial datafiles intentionally. Schedule the next attempt even if fetching or decoding fails.

```python
import json
import logging
import threading
from urllib.request import urlopen

def update_datafile():
    try:
        with urlopen(datafile_url, timeout=30) as response:
            f.set_datafile(json.load(response), replace=True)
    except Exception:
        logging.exception("Failed to update datafile")
    finally:
        threading.Timer(5 * 60, update_datafile).start()

update_datafile()
```

## Diagnostics

By default, Featurevisor reports diagnostics to the console for `info` level and above with a `[Featurevisor]` prefix.

### Levels

Available diagnostic levels are `fatal`, `error`, `warn`, `info`, and `debug`.

Set the level during initialization or update it afterwards:

```python
f = create_featurevisor({"logLevel": "debug"})
f.set_log_level("info")
```

### Handler

Use `onDiagnostic` to send structured diagnostics to your observability system:

```python
f = create_featurevisor({
    "logLevel": "info",
    "onDiagnostic": lambda diagnostic: print(
        diagnostic["level"],
        diagnostic["code"],
        diagnostic["message"],
    ),
})
```

Every diagnostic has `level`, `code`, `message`, and an object-shaped `details` dictionary. Optional `module`, `moduleName`, and `originalError` fields describe provenance. Evaluation metadata belongs in `details`.

Diagnostic handlers are isolated from SDK behaviour. An exception in a handler does not stop other handlers or evaluations.

## Events

Subscribe to SDK events to respond to context, sticky state and datafile changes.

### `datafile_set`

```python
def handle_datafile_set(event):
    revision = event["revision"]
    previous_revision = event["previousRevision"]
    revision_changed = event["revisionChanged"]
    features = event["features"]
    replaced = event["replaced"]

unsubscribe = f.on("datafile_set", handle_datafile_set)
unsubscribe()
```

The `features` list will contain keys of features that have either been added, updated, or removed compared to the previous datafile content.

### `context_set`

```python
unsubscribe = f.on("context_set", lambda event: print(event["context"]))
unsubscribe()
```

### `sticky_features_set` and `sticky_variables_set`

```python
unsubscribe = f.on("sticky_features_set", lambda event: print(event["features"]))
unsubscribe()

unsubscribe = f.on("sticky_variables_set", lambda event: print(event["variables"]))
unsubscribe()
```

### `error`

```python
unsubscribe = f.on("error", lambda event: print(event["diagnostic"]["message"]))
unsubscribe()
```

The `error` event is emitted for diagnostics reported with `level` set to `error`.

## Evaluation details

Inspect how variations and variables are evaluated against a context:

```python
# flag
evaluation = f.evaluate_flag(feature_key, context={})

# variation
evaluation = f.evaluate_variation(feature_key, context={})

# variable
evaluation = f.evaluate_variable(feature_key, variable_key, context={})
```

The returned object will always contain the following properties:

- `type`: flag, variation or variable
- `reason`: why the value was evaluated

Feature evaluations also contain `featureKey`. Global variable evaluations contain `variableKey`.

And optionally these properties depending on whether you are evaluating a feature variation or a variable:

- `bucketValue`: the bucket value between 0 and 100,000
- `ruleKey`: the rule key
- `error`: the error object
- `enabled`: if feature itself is enabled or not
- `variation`: the variation object
- `variationValue`: the variation value
- `variableKey`: the variable key
- `variableValue`: the variable value
- `variableSchema`: the variable schema
- `variableOverrideIndex`: index of matched variable override when applicable
- `variableOverrideKey`: key of the matched variable override when available
- `variableOverridePath`: nested authoring path of the matched global variable override
- `variableOverridePaths`: all matched paths from an accumulating global variable override
- `conditions`: the normalized root expression when its gate prevents evaluation
- `variable`: the global variable definition when available

## Modules

The before evaluation and after evaluation phases each use the module membership at the start of that phase. Where an evaluation uses bucketing, the bucket key and bucket value phases each use their own starting membership. Adding or removing a module during a phase does not change the iteration already in progress. The next phase uses the membership then installed. A module added before the after phase can receive after callbacks for evaluations already in progress, even if it did not receive their before callbacks. A module removed before the after phase receives no after callback.

Modules can intercept evaluation and participate in SDK lifecycle:

- `setup`
- `bucketKey`
- `bucketValue`
- `beforeEvaluation`
- `afterEvaluation`
- `close`

### Defining a module

```python
my_module = {
    "name": "my-module",
    "setup": lambda api: api["onDiagnostic"](lambda diagnostic: print(diagnostic)),
    "beforeEvaluation": lambda options: {**options, "context": {**options["context"], "country": "nl"}},
    "bucketKey": lambda options: options["bucketKey"],
    "bucketValue": lambda options: options["bucketValue"],
    "afterEvaluation": lambda evaluation, options: evaluation,
    "close": lambda: None,
}
```

`beforeEvaluation` and `afterEvaluation` run for feature and global variable evaluations. Required feature and variable checks use the same module pipeline, while an accumulating override publishes only its final result to `afterEvaluation`.

For an internal prerequisite read, both callbacks receive `options["dependencyOf"]` identifying the immediate requester, for example `{"type": "variable", "variableKey": "checkoutPolicy"}`. Feature variable requesters also include `featureKey`. Ordinary application calls have no `dependencyOf` entry. Callbacks can inspect this metadata without adding it to application options.

The module API passed to `setup` exposes `getRevision`, `onDiagnostic`, and `reportDiagnostic`.

If `setup` raises an exception, the module is not registered. Featurevisor removes subscriptions created during setup, reports `module_setup_error`, and calls `close` when present.

Internal reads carry `dependencyOf` identifying the immediate requester and `dependencyPurpose` set to `condition` or `composition`. Direct application calls carry neither. Conditions use owned comparison snapshots. Composition follows ordinary getter ownership. Memoization separates these purposes and is scoped to one public call. Tracking modules should normally skip condition reads, but composition reads consume real values. Private request state is never exposed to callbacks.

### Registering modules

Modules can be registered at initialization or afterwards:

```python

f = create_featurevisor({
    "modules": [my_module],
})

remove_module = f.add_module(my_module)
remove_module()

f.remove_module("my-module")
```

## Child instance

A child snapshots the parent keys that exist when it is spawned. Child values win for those keys. Parent keys introduced later are still inherited. Calling `close()` removes both child-owned listeners and subscriptions delegated to the parent.

```python
child = f.spawn({"country": "de"})
child.is_enabled("checkout")
child.evaluate_flag("checkout")
child.evaluate_variation("checkout")
child.evaluate_variable("checkout", "bgColor")
child.evaluate_variable("supportEmail")
```

## Close

```python
f.close()
```

## OpenFeature

The provider targets OpenFeature specification `0.8.0` through OpenFeature Python SDK `0.10.x`, tested with `0.10.0`. OpenFeature remains optional and is not installed or imported by the base Featurevisor package.

### Installation

```bash
pip install "featurevisor[openfeature]>=4.0.0,<5"
```

If the extra is not installed, importing `featurevisor.openfeature` reports the installation command needed to enable it.

### Provider setup

```python
from featurevisor.openfeature import FeaturevisorOpenFeatureProvider
from openfeature import api
from openfeature.evaluation_context import EvaluationContext

provider = FeaturevisorOpenFeatureProvider({"datafile": datafile_content})
api.set_provider_and_wait(provider)

client = api.get_client()
enabled = client.get_boolean_value(
    "checkout",
    False,
    EvaluationContext(targeting_key="user-123", attributes={"country": "nl"}),
)
```

Call `api.shutdown()` during application shutdown. This closes a Featurevisor instance created by the provider and releases provider subscriptions.

### Flag key mapping

| OpenFeature key | Featurevisor evaluation |
| --- | --- |
| `checkout` | Boolean flag for `checkout` |
| `checkout:variation` | Variation value for `checkout` |
| `checkout:title` | Variable `title` for `checkout` |
| `variable:supportEmail` | Global variable `supportEmail` |

Boolean variables use the boolean resolver. Integer and double variables use their matching numeric resolvers. Arrays, objects, and JSON variables use the object resolver.

The first separator divides the feature key from the selector. Use `key_separator` and `variation_key` when project keys require a different grammar:

```python
provider = FeaturevisorOpenFeatureProvider(
    {"datafile": datafile_content},
    key_separator="/",
    variation_key="$variation",
    global_variable_prefix="$variable",
)
```

This makes `checkout/$variation` the variation key, `checkout/title` a feature variable key, and `$variable/supportEmail` a global variable key. The global prefix defaults to `variable` and cannot contain the separator.

### Context mapping

OpenFeature's targeting key maps to `userId` by default. Use `targeting_key_field` to map it to another Featurevisor context field:

```python
provider = FeaturevisorOpenFeatureProvider(
    {"datafile": datafile_content},
    targeting_key_field="accountId",
)
```

The provider distinguishes a missing targeting key from an explicitly empty one when called directly. The upstream OpenFeature Python client merges contexts with a truthy fallback, so it drops an empty targeting key before the provider receives it. To override an existing targeting field with an empty string through that client, set the configured field itself in `attributes`, such as `{"userId": ""}`. Clear any inherited nonempty targeting key at its source first, since it would overwrite that attribute again. A narrower empty key may not clear it.

OpenFeature context attributes are copied without mutating the incoming context. Nested arrays and mappings are preserved. Datetimes are normalized to UTC ISO strings, matching the JavaScript provider.

### Resolution details

The provider maps Featurevisor evaluation results to OpenFeature details:

| Featurevisor result | OpenFeature result |
| --- | --- |
| Forced, sticky, rule match or composed variable | `TARGETING_MATCH` |
| Traffic allocation | `SPLIT` |
| Unmet root conditions, disabled variation or disabled variable | `DISABLED` |
| No match or variable default | `DEFAULT` |
| Missing feature, variable, or variations | `ERROR` with `FLAG_NOT_FOUND` |
| Wrong resolver type | `ERROR` with `TYPE_MISMATCH` |
| Invalid datafile | `ERROR` with `PARSE_ERROR` |
| Evaluation failure | `ERROR` with `GENERAL` |

Errors return the default value supplied to OpenFeature. A malformed datafile uses the stable message `Could not parse datafile`. A later successful `set_datafile` call clears the parse error.

Resolution metadata can include `featureKey`, `variableKey`, `featurevisorReason`, `revision`, `schemaVersion`, `ruleKey`, `bucketKey`, `bucketValue`, `forceIndex`, `variableOverrideIndex`, and `variableOverrideKey`. The selected variation is exposed as the OpenFeature variant when available.

### Tracking

Tracking is a no-op unless `on_track` is configured:

```python
def handle_track(name, context, details):
    print(name, context, details)

provider = FeaturevisorOpenFeatureProvider(
    {"datafile": datafile_content},
    on_track=handle_track,
)
```

### Using an existing Featurevisor instance

```python
from featurevisor import create_featurevisor

featurevisor = create_featurevisor({"datafile": datafile_content})
provider = FeaturevisorOpenFeatureProvider(featurevisor=featurevisor)
```

The caller owns an instance passed this way. Provider shutdown does not close it. Call `featurevisor.close()` when every consumer is finished with it. When the provider creates the instance from options, the provider owns and closes it. If both are supplied, `featurevisor` takes precedence over the options dictionary.

An invalid initial datafile in an owned instance fails `set_provider_and_wait` with `ParseError`, independently of `logLevel`. A valid datafile update clears the error and emits readiness. Supplied diagnostic callbacks keep their normal filtering. Shutdown removes provider subscriptions and closes only an owned instance.

See the [OpenFeature provider guide](https://featurevisor.com/docs/sdks/openfeature/) for resolution reasons, errors, metadata, tracking, lifecycle, and providers for other languages.

## CLI usage

Install the package in your Python environment:

```bash
python -m pip install "featurevisor>=4.0.0,<5"
```

The native test runner requires `@featurevisor/cli` 4.0.0 or later and Node.js 24 or later. Install the Node.js CLI in your Featurevisor project:

```bash
npm install --save-dev @featurevisor/cli@^4.0.0
```

The native CLI provides `test`, `benchmark` and `assess-distribution`. Use the Node.js CLI for authoring commands such as `init`, `lint` and `build`. Native commands delegate project parsing, test listing, matrix expansion and datafile generation to `npx featurevisor`, then evaluate with this Python SDK. The SDK runtime has no Node.js dependency.

Options use kebab case, such as `--project-directory-path`. The camelCase forms from earlier releases still work, and `--root-directory-path` is an alias for `--project-directory-path`. Pass values as `--name=value` or `--name value`. Repeat `--target` to select several targets in order. Run `<command> --help` to list every option.

Duplicate targets are evaluated once, at their first position. `--n` and `-n` accept a positive integer.

### Test

```bash
python -m featurevisor test \
  --project-directory-path=/path/to/featurevisor-project \
  --only-failures
```

See [Testing](https://featurevisor.com/docs/testing/) for authoring specs and [Checks](https://featurevisor.com/docs/testing/#checks) for check syntax and operators.

Useful test options:

- `--key-pattern=<pattern>` and `--assertion-pattern=<pattern>` filter tests.
- `--entity-type=scenario` selects scenarios. `--set=<set>` selects one project set. Omitting `--set` runs all sets.
- `--case=<id>` reruns one expanded case. Copy the opaque ID or rerun command from a failure report.
- `--json --pretty` produces an indented JSON report.
- `--coverage` reports observed rule and override selections. `--uncovered` lists features and global variables without associated specs.
- `--only-failures`, `--quiet`, `--verbose` and `--show-datafile` control diagnostic output.

### Benchmark

The examples below use a project without environments. Add `--environment=<name>` if your project configures environments. In a sets project, omit `--set` to run every set or pass `--set=<name>`. Repeat `--target=<key>` to evaluate specific target datafiles. The Node.js CLI validates these options and builds the datafiles. Python evaluates them. Replace `checkout` with a feature defined in your project.

```bash
python -m featurevisor benchmark \
  --project-directory-path=/path/to/featurevisor-project \
  --feature=checkout \
  --context='{"country":"nl"}' \
  --n=1000
```

Use `--variation` for a variation or `--variable=<key>` for a feature variable. Omit `--feature` to benchmark a global variable. Do not combine `--variation` with `--variable`. See [Benchmarking](https://featurevisor.com/docs/cli/#benchmarking).

### Assess distribution

```bash
python -m featurevisor assess-distribution \
  --project-directory-path=/path/to/featurevisor-project \
  --feature=checkout \
  --populate-uuid=userId \
  --n=1000
```

Repeat `--populate-uuid=<key>` to generate UUID values for multiple context attributes. Supply one key per option. See [Assess distribution](https://featurevisor.com/docs/cli/#assess-distribution).

## GitHub repositories

- SDK: [featurevisor/featurevisor-python](https://github.com/featurevisor/featurevisor-python)
- Example application: [featurevisor/featurevisor-example-python](https://github.com/featurevisor/featurevisor-example-python)

<!-- FEATUREVISOR_DOCS_END -->

<a id="getting-evaluation-snapshots"></a>

The aggregate evaluation reference has moved to [Getting evaluations](#getting-evaluations).

<a id="sticky-features-and-variables"></a>

The sticky state reference has moved to [Sticky](#sticky).

## Development

This repository assumes:

- Python 3.10+
- Node.js with `npx`
- Access to a Featurevisor project for CLI and tester integration

Run the local test suite:

```bash
python -m pip install -e '.[dev]'
make check
```

`make check` runs the base SDK tests, OpenFeature provider tests, and static type checking. Run them separately with `make test`, `make test-openfeature`, and `make typecheck`.

Run `PYTHONPATH=src python3 -m unittest discover -s tests -p test_readme_initialization.py -v` to execute the actual initialization snippet with simulated HTTP responses. The check covers evaluation, cleanup and failures without network access, and also runs in the ordinary SDK suite and CI.

Run the example project integration directly:

```bash
PYTHONPATH=src python3 -m featurevisor test \
  --projectDirectoryPath=../featurevisor/examples/example-1 \
  --onlyFailures
```

Or use:

```bash
make test-example-1
```

## Releasing

The next breaking release is `4.0.0`, including the optional OpenFeature extra. Before tagging, run `make check`, build both distributions with `python -m build`, and validate them with `python -m twine check dist/*`. Verify a base wheel installation without OpenFeature and a separate installation with the extra. The publish workflow rejects a tag that does not match `pyproject.toml`.

- Update version in pyproject.toml
- Push commit to main branch
- Wait for CI to complete
- Tag the release with the version number `vX.X.X`
- This will trigger a new release to PyPI

## License

MIT © [Fahad Heylaal](https://fahad19.com)
