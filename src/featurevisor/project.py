from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import time
from typing import Any


def node_cli_command(project_directory_path: pathlib.Path) -> list[str]:
    """Run the project's own Node CLI.

    Node starts the local package entry directly, which works the same on every
    platform. Without a local package, npx resolves the CLI as before. Windows
    needs the full path of its npx.cmd wrapper, because subprocess only looks for
    executables with an .exe suffix.
    """
    for directory in [project_directory_path.resolve(), *project_directory_path.resolve().parents]:
        entry = directory / "node_modules" / "@featurevisor" / "cli" / "bin.js"
        if entry.is_file():
            return ["node", str(entry)]
    npx = shutil.which("npx") if os.name == "nt" else None
    return [npx or "npx", "featurevisor"]


class FeaturevisorProjectError(ValueError):
    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class FeaturevisorProject:
    def __init__(self, project_directory_path: str | None = None, set_name: str | None = None) -> None:
        self.project_directory_path = pathlib.Path(project_directory_path or os.getcwd())
        self.set_name = set_name

    def run(self, *args: str, check: bool = True, include_set: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*node_cli_command(self.project_directory_path), *args, *([f"--set={self.set_name}"] if include_set and self.set_name is not None else [])],
            cwd=self.project_directory_path,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=check,
        )

    def run_json(self, *args: str, include_set: bool = True) -> Any:
        result = self.run(*args, include_set=include_set)
        return json.loads(result.stdout)

    def get_config(self) -> dict[str, Any]:
        return self.run_json("config", "--json", include_set=False)

    def list_tests(self, key_pattern: str | None = None, assertion_pattern: str | None = None, entity_type: str | None = None, case_id: str | None = None) -> list[dict[str, Any]]:
        args = ["list", "--tests", "--execution-protocol", "--json"]
        if key_pattern:
            args.append(f"--keyPattern={key_pattern}")
        if assertion_pattern:
            args.append(f"--assertionPattern={assertion_pattern}")
        if entity_type:
            args.append(f"--entityType={entity_type}")
        if case_id:
            args.append(f"--case={case_id}")
        result = self.run(*args, check=False)
        if result.returncode != 0:
            try:
                payload = json.loads(result.stderr)
                error = payload.get("error") or {}
                raise FeaturevisorProjectError(
                    error.get("message") or result.stderr.strip() or "Failed to list tests",
                    code=error.get("code"),
                )
            except json.JSONDecodeError as error:
                raise FeaturevisorProjectError(result.stderr.strip() or "Failed to list tests") from error
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            raise FeaturevisorProjectError("Invalid test execution protocol: expected an envelope object")
        if payload.get("kind") != "featurevisor.test-execution":
            raise FeaturevisorProjectError('Invalid test execution protocol: kind must be "featurevisor.test-execution"')
        version = payload.get("schemaVersion")
        if isinstance(version, bool) or not isinstance(version, (int, float)) or version != 1:
            raise FeaturevisorProjectError("Unsupported test execution protocol schemaVersion; expected numeric 1")
        tests = payload.get("tests")
        if not isinstance(tests, list) or not all(isinstance(test, dict) for test in tests):
            raise FeaturevisorProjectError("Invalid test execution protocol: tests must be an array of objects")
        return tests

    def list_segments(self) -> list[dict[str, Any]]:
        return self.run_json("list", "--segments", "--json")

    def list_features(self) -> list[dict[str, Any]]:
        return self.run_json("list", "--features", "--json")

    def list_uncovered(self, selector: str) -> list[str]:
        items = self.run_json("list", f"--{selector}", "--without-tests", "--json")
        return sorted(str(item.get("key") if isinstance(item, dict) else item) for item in items)

    def list_targets(self) -> list[str]:
        targets = self.run_json("list", "--targets", "--json")
        if isinstance(targets, list):
            result: list[str] = []
            for target in targets:
                value = (target.get("name") or target.get("key")) if isinstance(target, dict) else target
                if isinstance(value, str) and value:
                    result.append(value)
            return result
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


def timed_build(project: FeaturevisorProject, *, environment: str | None, inflate: int | None = None, target: str | None = None) -> tuple[dict[str, Any], float]:
    start = time.perf_counter()
    datafile = project.build_datafile_json(environment=environment, inflate=inflate, target=target)
    return datafile, time.perf_counter() - start
