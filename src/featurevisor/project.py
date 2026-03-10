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

    def build_datafile_json(self, environment: str | None = None, schema_version: str | None = None, inflate: int | None = None) -> dict[str, Any]:
        args = ["build", "--json"]
        if environment:
            args.append(f"--environment={environment}")
        if schema_version:
            args.append(f"--schema-version={schema_version}")
        if inflate:
            args.append(f"--inflate={inflate}")
        return self.run_json(*args)

    def ensure_built(self, schema_version: str | None = None, inflate: int | None = None) -> None:
        args = ["build"]
        if schema_version:
            args.append(f"--schema-version={schema_version}")
        if inflate:
            args.append(f"--inflate={inflate}")
        self.run(*args)

    def read_generated_datafile(self, config: dict[str, Any], *, environment: str | None, kind: str, value: str) -> dict[str, Any]:
        datafiles_dir = pathlib.Path(config["datafilesDirectoryPath"])
        base_dir = datafiles_dir / environment if environment else datafiles_dir
        file_name = config.get("datafileNamePattern", "featurevisor-%s.json") % f"{kind}-{value}"
        return json.loads((base_dir / file_name).read_text())


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


def timed_build(project: FeaturevisorProject, *, environment: str, schema_version: str | None = None, inflate: int | None = None) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    datafile = project.build_datafile_json(environment=environment, schema_version=schema_version, inflate=inflate)
    return datafile, time.perf_counter() - start

