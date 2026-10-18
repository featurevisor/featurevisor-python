"""Local review timings. Run with PYTHONPATH pointing to the source snapshot."""

import json
import platform
import statistics
import time

from featurevisor import create_featurevisor


def corpus(schema):
    return {"schemaVersion": schema, "revision": "bench", "segments": {},
            "features": {f"f{i}": {"bucketBy": "userId", "hash": str(i),
                "variations": [{"value": "on"}],
                "variablesSchema": {"value": {"type": "integer", "defaultValue": 7}},
                "traffic": [{"key": "all", "conditions": '[{"attribute":"country","operator":"equals","value":"nl"}]',
                             "percentage": 100000, "variation": "on"}]} for i in range(500)},
            "variables": {"value": {"type": "integer", "defaultValue": 7}}}


def measure(call, iterations, expected=None):
    for _ in range(100):
        call()
    rounds = []
    for _ in range(5):
        elapsed = 0
        for _ in range(iterations):
            start = time.perf_counter_ns()
            result = call()
            elapsed += time.perf_counter_ns() - start
            if expected is not None:
                assert result == expected, result
        rounds.append(elapsed / iterations / 1000)
    return {"median_us": statistics.median(rounds), "rounds_us": rounds}


if __name__ == "__main__":
    data = corpus("4")
    f = create_featurevisor({"datafile": data, "logLevel": "fatal"})
    context = {"country": "nl", "userId": "bench"}
    results = {"python": platform.python_version()}
    for name, call, expected in (
        ("flag", lambda: f.is_enabled("f0", context), True),
        ("variation", lambda: f.get_variation("f0", context), "on"),
        ("feature_variable", lambda: f.get_variable("f0", "value", context), 7),
        ("global_variable", lambda: f.get_variable("value", context), 7),
    ):
        results[name] = measure(call, 20000, expected)
    f.close()
    for schema in ("2", "4"):
        data = corpus(schema)

        def load():
            instance = create_featurevisor({"datafile": data, "logLevel": "fatal"})
            assert not instance.datafile.entity_cache
            instance.close()

        results[f"schema_{schema}_load"] = measure(load, 100)
    print(json.dumps(results, indent=2))
