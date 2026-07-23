# Featurevisor Python SDK <!-- omit in toc -->

This repository ports the latest Featurevisor [JavaScript SDK](https://featurevisor.com/docs/sdks/javascript/) to Python.

The package name is `featurevisor`, and it targets Python 3.10+.

This SDK is compatible with Featurevisor v3 projects and v2 datafiles.

## Table of contents <!-- omit in toc -->

- [Installation](#installation)
- [Public API](#public-api)
- [Initialization](#initialization)
- [Evaluation types](#evaluation-types)
- [Context](#context)
  - [Setting initial context](#setting-initial-context)
  - [Setting after initialization](#setting-after-initialization)
  - [Replacing existing context](#replacing-existing-context)
  - [Manually passing context](#manually-passing-context)
- [Check if enabled](#check-if-enabled)
- [Getting variation](#getting-variation)
- [Getting variables](#getting-variables)
  - [Type specific methods](#type-specific-methods)
- [Getting all evaluations](#getting-all-evaluations)
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
  - [`sticky_set`](#sticky_set)
  - [`error`](#error)
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
- [Development](#development)
- [Releasing](#releasing)
- [License](#license)

<!-- FEATUREVISOR_DOCS_BEGIN -->

## Installation

```bash
pip install featurevisor
```

## Public API

The main runtime API is `create_featurevisor()`:

```python
from featurevisor import Featurevisor, create_featurevisor

f: Featurevisor = create_featurevisor({
    "datafile": datafile_content,
})
```

Most applications only need `create_featurevisor` and the `Featurevisor` instance type. Public extension and observability APIs include `FeaturevisorModule`, diagnostics, events, and the datafile dictionaries accepted by the factory.

Concurrent evaluations are safe after an instance is configured. Do not mutate or close the same instance concurrently with evaluations. Serialize calls to `set_datafile`, `set_context`, `set_sticky`, `add_module`, `remove_module`, and `close`. Module, event, and diagnostic callbacks must synchronize mutable state that they capture.

## Initialization

Initialize the SDK with Featurevisor datafile content:

```python
from urllib.request import urlopen
import json

from featurevisor import create_featurevisor

datafile_url = "https://cdn.yoursite.com/datafile.json"

with urlopen(datafile_url) as response:
    datafile_content = json.load(response)

f = create_featurevisor({
    "datafile": datafile_content,
})
```

## Evaluation types

We can evaluate 3 types of values against a particular [feature](https://featurevisor.com/docs/features/):

- [**Flag**](#check-if-enabled) (`bool`): whether the feature is enabled or not
- [**Variation**](#getting-variation) (`string`): the variation of the feature (if any)
- [**Variables**](#getting-variables): variable values of the feature (if any)

## Context

Context is a plain dictionary of attribute values used during evaluation:

```python
context = {
    "userId": "123",
    "country": "nl",
}
```

### Setting initial context

You can provide context at initialization:

```python
f = create_featurevisor({
    "context": {
        "deviceId": "123",
        "country": "nl",
    },
})
```

### Setting after initialization

You can merge more context later:

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

You can also pass additional per-evaluation context:

```python
is_enabled = f.is_enabled("my_feature", {"country": "nl"})
variation = f.get_variation("my_feature", {"country": "nl"})
variable_value = f.get_variable("my_feature", "my_variable", {"country": "nl"})
```

## Check if enabled

```python
if f.is_enabled("my_feature"):
    pass
```

## Getting variation

```python
variation = f.get_variation("my_feature")

if variation == "treatment":
    pass
```

## Getting variables

```python
bg_color = f.get_variable("my_feature", "bgColor")
```

### Type specific methods

Typed convenience methods are also available:

```python
f.get_variable_boolean(feature_key, variable_key, context={})
f.get_variable_string(feature_key, variable_key, context={})
f.get_variable_integer(feature_key, variable_key, context={})
f.get_variable_double(feature_key, variable_key, context={})
f.get_variable_array(feature_key, variable_key, context={})
f.get_variable_object(feature_key, variable_key, context={})
f.get_variable_json(feature_key, variable_key, context={})
```

Type specific methods do not coerce values. `get_variable_integer()` returns `None` for the string `"1"`, and boolean getters return `None` for non-boolean values.

## Getting all evaluations

```python
all_evaluations = f.get_all_evaluations()
```

## Sticky

### Initialize with sticky

You can pin feature evaluations with sticky values:

Sticky values belong to an SDK or child instance. Evaluation options do not accept sticky overrides; use `spawn(context, {"sticky": ...})` when a child needs its own sticky state.

```python
f = create_featurevisor({
    "sticky": {
        "myFeatureKey": {
            "enabled": True,
            "variation": "treatment",
            "variables": {
                "myVariableKey": "myVariableValue",
            },
        }
    }
})
```

### Set sticky afterwards

Or update them later:

```python
f.set_sticky({
    "myFeatureKey": {
        "enabled": False,
    }
})
```

## Setting datafile

You may initialize the SDK without passing `datafile`, and set it later on. The SDK accepts either parsed JSON content or a JSON string:

```python
f.set_datafile(datafile_content)
f.set_datafile(json.dumps(datafile_content))
```

### Merging by default

By default, `set_datafile(datafile)` merges incoming content into the SDK's current datafile:

- top-level metadata such as `schemaVersion`, `revision`, and `featurevisorVersion` comes from the incoming datafile
- `segments` are merged, with incoming entries overriding existing ones
- `features` are merged, with incoming entries overriding existing ones

This means you can call `set_datafile` more than once with different datafiles, and the SDK instance accumulates their features and segments together. This is what makes [loading datafiles on demand](#loading-datafiles-on-demand) possible.

### Replacing

To fully replace the stored datafile, pass `True` as the second argument:

```python
f.set_datafile(datafile_content, True)
```

### Loading datafiles on demand

Because merging is the default, a single SDK instance can start with a small datafile and load more datafiles later as your application needs them, instead of downloading every feature upfront.

This pairs well with [targets](https://featurevisor.com/docs/targets/), where each target produces a smaller datafile for a specific part of your application. You can load the datafile for the current part, and load others only when the user reaches them:

```python
from urllib.request import urlopen
import json

from featurevisor import create_featurevisor

f = create_featurevisor({})

def load_datafile(target):
    url = f"https://cdn.yoursite.com/production/featurevisor-{target}.json"
    with urlopen(url) as response:
        datafile = json.load(response)

    # merges into whatever was loaded before
    f.set_datafile(datafile)

load_datafile("products")

# later, when the user reaches checkout
load_datafile("checkout")
```

### Updating datafile

You can set the datafile as many times as you want in your application, which will emit a [`datafile_set`](#datafile_set) event that you can listen and react to accordingly.

### Interval-based update

Here's a minimal interval-style example:

```python
import json
import threading
from urllib.request import urlopen

def update_datafile():
    with urlopen(datafile_url) as response:
        f.set_datafile(json.load(response))

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

Diagnostic handlers are isolated from SDK behavior. An exception in a handler does not stop other handlers or evaluations.


## Events

Featurevisor SDK implements a simple event emitter that allows you to listen to events that happen in the runtime.

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

### `sticky_set`

```python
unsubscribe = f.on("sticky_set", lambda event: print(event["features"]))
unsubscribe()
```

### `error`

```python
unsubscribe = f.on("error", lambda event: print(event["diagnostic"]["message"]))
unsubscribe()
```

The `error` event is emitted for diagnostics reported with `level` set to `error`.

## Evaluation details

Besides logging with debug level enabled, you can also get more details about how feature variations and variables are evaluated at runtime against a given context:

```python
# flag
evaluation = f.evaluate_flag(feature_key, context={})

# variation
evaluation = f.evaluate_variation(feature_key, context={})

# variable
evaluation = f.evaluate_variable(feature_key, variable_key, context={})
```

The returned object will always contain the following properties:

- `featureKey`: the feature key
- `reason`: the reason how the value was evaluated

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

## Modules

Modules can intercept evaluation and participate in SDK lifecycle:

- `setup`
- `before`
- `bucketKey`
- `bucketValue`
- `after`
- `close`

### Defining a module

```python
my_module = {
    "name": "my-module",
    "setup": lambda api: api["onDiagnostic"](lambda diagnostic: print(diagnostic)),
    "before": lambda options: {**options, "context": {**options["context"], "country": "nl"}},
    "bucketKey": lambda options: options["bucketKey"],
    "bucketValue": lambda options: options["bucketValue"],
    "after": lambda evaluation, options: evaluation,
    "close": lambda: None,
}
```

The module API passed to `setup` exposes `getRevision`, `onDiagnostic`, and `reportDiagnostic`.

If `setup` raises an exception, the module is not registered. Featurevisor removes subscriptions created during setup, reports `module_setup_error`, and calls `close` when present.

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
child.is_enabled("my_feature")
child.evaluate_flag("my_feature")
child.evaluate_variation("my_feature")
child.evaluate_variable("my_feature", "my_variable")
```

## Close

```python
f.close()
```

## CLI usage

The Python package also exposes a CLI:

```bash
python -m featurevisor test
python -m featurevisor benchmark
python -m featurevisor assess-distribution
```

These commands are intended for use from inside a Featurevisor project and rely on `npx featurevisor` being available locally.

All three commands accept repeatable `--target=<target>` options. `test` builds only the selected Target datafiles and runs untargeted assertions plus assertions for those targets. `benchmark` and `assess-distribution` run independently against every selected Target datafile. Without `--target`, existing project-wide behavior is preserved. Project definitions, test specs, Target discovery, and datafile generation continue to come from the Node.js CLI.

### Test

Run Featurevisor test specs using the Python SDK:

```bash
python -m featurevisor test \
  --projectDirectoryPath=/path/to/featurevisor-project
```

Useful options:

```bash
python -m featurevisor test --keyPattern=foo
python -m featurevisor test --assertionPattern=variation
python -m featurevisor test --onlyFailures
python -m featurevisor test --showDatafile
python -m featurevisor test --verbose
```

The Python test runner builds base datafiles and Target datafiles with `npx featurevisor build --json`. Assertions containing `target` are evaluated against the matching Target datafile.

### Benchmark

Benchmark repeated Python SDK evaluations against a built datafile:

```bash
python -m featurevisor benchmark \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --context='{"userId":"123"}' \
  --n=1000
```

For variation benchmarks:

```bash
python -m featurevisor benchmark \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --variation \
  --context='{"userId":"123"}'
```

For variable benchmarks:

```bash
python -m featurevisor benchmark \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --variable=my_variable_key \
  --context='{"userId":"123"}'
```

### Assess distribution

Inspect enabled/disabled and variation distribution over repeated evaluations:

```bash
python -m featurevisor assess-distribution \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --context='{"country":"nl"}' \
  --n=1000
```

You can also populate UUID-based context keys per iteration:

```bash
python -m featurevisor assess-distribution \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --populateUuid=userId \
  --populateUuid=deviceId
```

## OpenFeature

The provider targets OpenFeature specification `0.8.0` through OpenFeature Python SDK `0.10.x`. OpenFeature remains optional and is not installed or imported by the base Featurevisor package.

### Installation

```bash
pip install "featurevisor[openfeature]"
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

Boolean variables use the boolean resolver. Integer and double variables use their matching numeric resolvers. Arrays, objects, and JSON variables use the object resolver.

The first separator divides the feature key from the selector. Use `key_separator` and `variation_key` when project keys require a different grammar:

```python
provider = FeaturevisorOpenFeatureProvider(
    {"datafile": datafile_content},
    key_separator="/",
    variation_key="$variation",
)
```

This makes `checkout/$variation` the variation key and `checkout/title` a variable key.

### Context mapping

OpenFeature's targeting key maps to `userId` by default. Use `targeting_key_field` to map it to another Featurevisor context field:

```python
provider = FeaturevisorOpenFeatureProvider(
    {"datafile": datafile_content},
    targeting_key_field="accountId",
)
```

OpenFeature context attributes are copied without mutating the incoming context. Nested arrays and mappings are preserved. Datetimes are normalized to UTC ISO strings, matching the JavaScript provider.

### Resolution details

The provider maps Featurevisor evaluation results to OpenFeature details:

| Featurevisor result | OpenFeature result |
| --- | --- |
| Required, forced, sticky, or rule match | `TARGETING_MATCH` |
| Traffic allocation | `SPLIT` |
| Disabled variation or variable | `DISABLED` |
| No match or variable default | `DEFAULT` |
| Missing feature, variable, or variations | `ERROR` with `FLAG_NOT_FOUND` |
| Wrong resolver type | `ERROR` with `TYPE_MISMATCH` |
| Invalid datafile | `ERROR` with `PARSE_ERROR` |
| Evaluation failure | `ERROR` with `GENERAL` |

Errors return the default value supplied to OpenFeature. A malformed datafile uses the stable message `Could not parse datafile`. A later successful `set_datafile` call clears the parse error.

Resolution metadata can include `featureKey`, `variableKey`, `featurevisorReason`, `revision`, `schemaVersion`, `ruleKey`, `bucketKey`, `bucketValue`, `forceIndex`, and `variableOverrideIndex`. The selected variation is exposed as the OpenFeature variant when available.

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

See the [OpenFeature provider guide](https://featurevisor.com/docs/sdks/openfeature/) for resolution reasons, errors, metadata, tracking, lifecycle, and providers for other languages.

<!-- FEATUREVISOR_DOCS_END -->

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

`make check` runs the base SDK tests, OpenFeature provider tests, and static type checking. You can also run them separately with `make test`, `make test-openfeature`, and `make typecheck`.

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

- Update version in pyproject.toml
- Push commit to main branch
- Wait for CI to complete
- Tag the release with the version number `vX.X.X`
- This will trigger a new release to PyPI

## License

MIT © [Fahad Heylaal](https://fahad19.com)
