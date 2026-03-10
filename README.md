# Featurevisor Python SDK

This repository ports the latest Featurevisor JavaScript SDK to Python and includes a companion CLI for SDK validation tasks like `test`, `benchmark`, and `assess-distribution`.

The package name is `featurevisor`, and it targets Python 3.10+.

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

The SDK evaluates three kinds of values against a feature:

- Flag: whether the feature is enabled.
- Variation: the selected variation value.
- Variables: any variable values defined on the feature.

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

```python
unsubscribe = f.on("datafile_set", lambda details: print(details))
unsubscribe()
```

## Hooks

Hooks support:

- `before`
- `bucketKey`
- `bucketValue`
- `after`

They can be passed during initialization or added later with `add_hook`.

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
featurevisor test
featurevisor benchmark
featurevisor assess-distribution
```

These commands are intended for use from inside a Featurevisor project and rely on `npx featurevisor` being available locally.

### Test

Run Featurevisor test specs using the Python SDK:

```bash
featurevisor test \
  --projectDirectoryPath=/path/to/featurevisor-project
```

Useful options:

```bash
featurevisor test --keyPattern=foo
featurevisor test --assertionPattern=variation
featurevisor test --onlyFailures
featurevisor test --showDatafile
featurevisor test --verbose
featurevisor test --with-tags
featurevisor test --with-scopes
```

### Benchmark

Benchmark repeated Python SDK evaluations against a built datafile:

```bash
featurevisor benchmark \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --context='{"userId":"123"}' \
  --n=1000
```

For variation benchmarks:

```bash
featurevisor benchmark \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --variation \
  --context='{"userId":"123"}'
```

For variable benchmarks:

```bash
featurevisor benchmark \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --variable=my_variable_key \
  --context='{"userId":"123"}'
```

### Assess Distribution

Inspect enabled/disabled and variation distribution over repeated evaluations:

```bash
featurevisor assess-distribution \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --context='{"country":"nl"}' \
  --n=1000
```

You can also populate UUID-based context keys per iteration:

```bash
featurevisor assess-distribution \
  --projectDirectoryPath=/path/to/featurevisor-project \
  --environment=production \
  --feature=my_feature \
  --populateUuid=userId \
  --populateUuid=deviceId
```

<!-- FEATUREVISOR_DOCS_BEGIN -->

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
  --projectDirectoryPath=/path/to/featurevisor-project
```

## License

MIT © [Fahad Heylaal](https://fahad19.com)
