"""Serial sticky handoff benchmark: PYTHONPATH=src python3 tests/benchmark_sticky_handoff.py.

Use --check to validate fixtures without running timings.
"""

from __future__ import annotations

import argparse
import time

from featurevisor import create_featurevisor


CONTEXT = {"userId": "sticky-bench"}
ENABLED = {"bucketBy": "userId", "traffic": [
    {"key": "all", "segments": "*", "percentage": 100000, "enabled": True},
]}


def setup(kind: str, size: int, child_mode: bool):
    keys = [f"unrelated{index}" for index in range(size)]
    source = {
        "schemaVersion": "2", "revision": "sticky-source", "segments": {},
        "features": {key: ENABLED for key in keys} if kind == "features" else {},
        "variables": {key: {"type": "boolean", "defaultValue": True} for key in keys}
        if kind == "variables" else {},
    }
    server = create_featurevisor({"datafile": source, "logLevel": "fatal"})
    if kind == "features":
        sticky = server.get_feature_evaluations(CONTEXT, keys)
        sticky_name = "stickyFeatures"
    else:
        sticky = server.get_variable_evaluations(CONTEXT, keys)
        sticky_name = "stickyVariables"
    server.close()
    if len(sticky) != size:
        raise AssertionError(f"exported {len(sticky)} sticky entries, expected {size}")

    requirement = {"variable": "leaf", "operator": "equals", "value": True}
    gated = {
        "schemaVersion": "2", "revision": "sticky-gated", "segments": {},
        "features": {"gate": ENABLED},
        "variables": {
            "leaf": {"type": "boolean", "defaultValue": True},
            "root": {"type": "boolean", "defaultValue": True,
                     **({"requiredFeatures": ["gate", "gate"]} if kind == "features"
                        else {"conditions": [requirement, requirement]})},
        },
    }
    # The gated global is created only after export, so no sticky value can
    # bypass its prerequisite path.
    config = {"datafile": gated, "logLevel": "fatal"}
    if not child_mode:
        config[sticky_name] = sticky
    instance = create_featurevisor(config)
    target = instance.spawn({}, {sticky_name: sticky}) if child_mode else instance
    if target.get_variable("root", CONTEXT) is not True:
        raise AssertionError("gated global did not evaluate to true")
    return instance, target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=1000)
    parser.add_argument("--warmup", type=int, default=100)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.iterations < 1:
        parser.error("--iterations must be positive")
    if args.warmup < 0:
        parser.error("--warmup must be nonnegative")
    if not args.check:
        print("kind,size,instance,average_ns_per_evaluation")
    for kind in ("features", "variables"):
        for size in (0, 100, 1000, 5000):
            for child_mode in (False, True):
                instance, target = setup(kind, size, child_mode)
                try:
                    if not args.check:
                        for _ in range(args.warmup):
                            if target.get_variable("root", CONTEXT) is not True:
                                raise AssertionError("warmup result changed")
                        elapsed = 0
                        for _ in range(args.iterations):
                            start = time.perf_counter_ns()
                            result = target.get_variable("root", CONTEXT)
                            elapsed += time.perf_counter_ns() - start
                            if result is not True:
                                raise AssertionError("benchmark result changed")
                        print(f"{kind},{size},{'child' if child_mode else 'instance'},"
                              f"{elapsed / args.iterations:.1f}")
                finally:
                    if child_mode:
                        target.close()
                    instance.close()
    if args.check:
        print("All 16 sticky handoff fixtures passed")


if __name__ == "__main__":
    main()
