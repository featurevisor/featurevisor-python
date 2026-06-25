from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
from typing import Any


class FeaturevisorProject:
    def __init__(self, project_directory_path: str | None = None) -> None:
        self.project_directory_path = pathlib.Path(project_directory_path or os.getcwd())

    def run(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["npx", "featurevisor", *args],
            cwd=self.project_directory_path,
            text=True,
            capture_output=True,
            check=check,
        )

    def run_json(self, *args: str) -> Any:
        result = self.run(*args)
        return json.loads(result.stdout)

    def get_config(self) -> dict[str, Any]:
        return self.run_json("config", "--json")

    def list_tests(self, key_pattern: str | None = None, assertion_pattern: str | None = None) -> list[dict[str, Any]]:
        args = ["list", "--tests", "--applyMatrix", "--json"]
        if key_pattern:
            args.append(f"--keyPattern={key_pattern}")
        if assertion_pattern:
            args.append(f"--assertionPattern={assertion_pattern}")
        return self.run_json(*args)

    def list_segments(self) -> list[dict[str, Any]]:
        return self.run_json("list", "--segments", "--json")

    def list_features(self) -> list[dict[str, Any]]:
        return self.run_json("list", "--features", "--json")

    def list_targets(self) -> list[str]:
        targets = self.run_json("list", "--targets", "--json")
        if isinstance(targets, list):
            return [
                (target.get("name") or target.get("key")) if isinstance(target, dict) else target
                for target in targets
                if ((target.get("name") or target.get("key")) if isinstance(target, dict) else target)
            ]
        if isinstance(targets, dict):
            return list(targets.keys())
        return []

    def build_datafile_json(self, environment: str | None = None, inflate: int | None = None, target: str | None = None) -> dict[str, Any]:
        args = ["build", "--json"]
        if environment:
            args.append(f"--environment={environment}")
        if inflate:
            args.append(f"--inflate={inflate}")
        if target:
            args.append(f"--target={target}")
        return self.run_json(*args)


def pretty_duration(seconds: float) -> str:
    if seconds == 0:
        return "0ms"
    milliseconds = int(seconds * 1000)
    if milliseconds == 0:
        return f"{int(seconds * 1_000_000)}μs"
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if whole_seconds:
        parts.append(f"{whole_seconds}s")
    if millis:
        parts.append(f"{millis}ms")
    return " ".join(parts)


def timed_build(project: FeaturevisorProject, *, environment: str, inflate: int | None = None) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    datafile = project.build_datafile_json(environment=environment, inflate=inflate)
    return datafile, time.perf_counter() - start
