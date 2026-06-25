# featurevisor-python

This repository ports the latest Featurevisor [JavaScript SDK](https://featurevisor.com/docs/sdks/javascript/) to Python.

The package name is `featurevisor`, and it targets Python 3.10+.

This SDK is compatible with Featurevisor v3 projects and v2 datafiles.

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

## Evaluation Types

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

You can provide context at initialization:

```python
f = create_instance({
    "context": {
        "deviceId": "123",
        "country": "nl",
    },
})
```

You can merge more context later:

```python
f.set_context({
    "userId": "234",
})
```

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

You can also pass additional per-evaluation context:

```python
is_enabled = f.is_enabled("my_feature", {"country": "nl"})
variation = f.get_variation("my_feature", {"country": "nl"})
variable_value = f.get_variable("my_feature", "my_variable", {"country": "nl"})
```

## Check If Enabled

```python
if f.is_enabled("my_feature"):
    pass
```

## Getting Variation

```python
variation = f.get_variation("my_feature")

if variation == "treatment":
    pass
```

## Getting Variables

```python
bg_color = f.get_variable("my_feature", "bgColor")
```

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

## Getting All Evaluations

```python
all_evaluations = f.get_all_evaluations()
```

## Sticky

You can pin feature evaluations with sticky values:

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

Or update them later:

```python
f.set_sticky({
    "myFeatureKey": {
        "enabled": False,
    }
})
```

## Setting Datafile

The SDK accepts either parsed JSON content or a JSON string:

```python
f.set_datafile(datafile_content)
f.set_datafile(json.dumps(datafile_content))
```

By default, `set_datafile(datafile)` merges incoming content into the SDK's current datafile:

- top-level metadata such as `schemaVersion`, `revision`, and `featurevisorVersion` comes from the incoming datafile
- `segments` are merged, with incoming entries overriding existing ones
- `features` are merged, with incoming entries overriding existing ones

To fully replace the stored datafile, pass `True` as the second argument:

```python
f.set_datafile(datafile_content, True)
```

## Logging

Supported log levels:

- `fatal`
- `error`
- `warn`
- `info`
- `debug`

```python
from featurevisor import create_instance

f = create_instance({
    "datafile": datafile_content,
    "logLevel": "debug",
})
```

You can also provide a custom logger handler via `create_logger`.

## Events

The SDK emits:

- `datafile_set`
- `context_set`
- `sticky_set`
- `error`

```python
unsubscribe = f.on("datafile_set", lambda details: print(details))
unsubscribe()
```

`datafile_set` includes a `replaced` flag. The `error` event is emitted for diagnostics reported with `level` set to `error` or `fatal`.

## Diagnostics

Diagnostics provide structured SDK and module events for observability:

```python
f = create_instance({
    "onDiagnostic": lambda diagnostic: print(diagnostic),
})
```

If `onDiagnostic` is not provided, diagnostics are sent to the SDK logger.

## Modules

Modules can intercept evaluation and participate in SDK lifecycle:

- `setup`
- `before`
- `bucketKey`
- `bucketValue`
- `after`
- `close`

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

f = create_instance({
    "modules": [my_module],
})

remove_module = f.add_module(my_module)
remove_module()

f.remove_module("my-module")
```

The module API passed to `setup` exposes `getRevision`, `onDiagnostic`, and `reportDiagnostic`.

## Child Instance

```python
child = f.spawn({"country": "de"})
child.is_enabled("my_feature")
```

## Close

```python
f.close()
```

## CLI Usage

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

### Assess Distribution

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
