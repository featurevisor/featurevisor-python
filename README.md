# Featurevisor Python SDK <!-- omit in toc -->

This repository ports the latest Featurevisor [JavaScript SDK](https://featurevisor.com/docs/sdks/javascript/) to Python.

The package name is `featurevisor`, and it targets Python 3.10+.

This SDK is compatible with Featurevisor v3 projects and v2 datafiles.

## Table of contents <!-- omit in toc -->

- [Installation](#installation)
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
- [Logging](#logging)
  - [Levels](#levels)
  - [Customizing levels](#customizing-levels)
  - [Handler](#handler)
- [Events](#events)
  - [`datafile_set`](#datafile_set)
  - [`context_set`](#context_set)
  - [`sticky_set`](#sticky_set)
  - [`error`](#error)
- [Diagnostics](#diagnostics)
- [Modules](#modules)
  - [Defining a module](#defining-a-module)
  - [Registering modules](#registering-modules)
- [Child instance](#child-instance)
- [Close](#close)
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

## Initialization

Initialize the SDK with Featurevisor datafile content:

```python
from urllib.request import urlopen
import json

from featurevisor import create_instance

datafile_url = "https://cdn.yoursite.com/datafile.json"

with urlopen(datafile_url) as response:
    datafile_content = json.load(response)

f = create_instance({
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
f = create_instance({
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
f = create_instance({
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

from featurevisor import create_instance

f = create_instance({})

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

## Logging

By default, Featurevisor SDKs report diagnostics/logs for `info` level and above.

### Levels

Supported log levels:

- `fatal`
- `error`
- `warn`
- `info`
- `debug`

### Customizing levels

```python
from featurevisor import create_instance

f = create_instance({
    "datafile": datafile_content,
    "logLevel": "debug",
})
```

You can also set the log level from the SDK instance afterwards:

```python
f.set_log_level("debug")
```

### Handler

You can also provide a custom logger handler via `create_logger`.

## Diagnostics

Diagnostics provide structured SDK and module events for observability:

```python
f = create_instance({
    "onDiagnostic": lambda diagnostic: print(diagnostic),
})
```

If `onDiagnostic` is not provided, diagnostics are sent to the SDK logger.

Every diagnostic has `level`, `code`, `message`, and an object-shaped `details` dictionary. Optional `module`, `moduleName`, and `originalError` fields describe provenance; evaluation metadata belongs in `details`.

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
unsubscribe = f.on("error", lambda diagnostic: print(diagnostic["message"]))
unsubscribe()
```

The `error` event is emitted for diagnostics reported with `level` set to `error` or `fatal`.

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

f = create_instance({
    "modules": [my_module],
})

remove_module = f.add_module(my_module)
remove_module()

f.remove_module("my-module")
```

## Child instance

```python
child = f.spawn({"country": "de"})
child.is_enabled("my_feature")
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

<!-- FEATUREVISOR_DOCS_END -->

## Development

This repository assumes:

- Python 3.10+
- Node.js with `npx`
- Access to a Featurevisor project for CLI and tester integration

Run the local test suite:

```bash
make test
```

Run the example project integration directly:

```bash
PYTHONPATH=src python3 -m featurevisor test \
  --projectDirectoryPath=/Users/fahad/Projects/featurevisor/featurevisor/examples/example-1 \
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
