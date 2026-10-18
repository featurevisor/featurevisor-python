"""CLI project selection regressions, optionally against an already built Node CLI.

Set FEATUREVISOR_NODE_CLI to its entry point to exercise real authored projects.
FEATUREVISOR_NATIVE_CLI can select another native runner for the same contract.
Neither mode installs dependencies or builds Node packages.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


class RuntimeProjectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="featurevisor runtime ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = self.root / "calls.jsonl"
        self.env = dict(os.environ, PATH=f"{self.root}{os.pathsep}{os.environ['PATH']}")
        self.env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
        self.runner = shlex.split(os.environ.get("FEATUREVISOR_NATIVE_CLI", "")) or [sys.executable, "-m", "featurevisor"]
        node_cli = os.environ.get("FEATUREVISOR_NODE_CLI")
        script = f'''#!{sys.executable}
import json, os, subprocess, sys
from pathlib import Path
args = sys.argv[2:]
with open({str(self.calls)!r}, "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
node_cli = {node_cli!r}
if node_cli:
    sys.exit(subprocess.call(["node", node_cli, *args]))
config = json.loads(Path("config.json").read_text())
selected = next((a.split("=", 1)[1] for a in args if a.startswith("--set=")), None)
if selected is not None and config.get("sets") is False:
    sys.stderr.write("This project does not configure sets. Omit --set.")
    sys.exit(3)
if selected and not Path("sets", selected).is_dir():
    sys.stderr.write("Unknown set: " + selected)
    sys.exit(3)
if args[0] == "config":
    value = config
elif args[0] == "list":
    if "--tests" in args:
        value = {{"kind": "featurevisor.test-execution", "schemaVersion": 1, "tests": [{{"key": "ready", "feature": "ready", "assertions": [{{"at": 50, "expectedToBeEnabled": True}}]}}]}}
    else:
        value = ["all", "web"] if "--targets" in args else []
elif Path("fail-build").exists():
    sys.stderr.write("café build failed")
    sys.exit(17)
else:
    value = {{"schemaVersion": "2", "revision": "test", "segments": {{}}, "features": {{"ready": {{"bucketBy": "userId", "traffic": [{{"key": "everyone", "segments": "*", "percentage": 100000}}]}}}}, "variables": {{}}}}
print(json.dumps(value))
'''
        shim = self.root / "npx"
        shim.write_text(script, encoding="utf-8")
        shim.chmod(0o755)

    def project(self, kind: str) -> Path:
        project = self.root / kind
        project.mkdir()
        config: dict = {}
        if "env" in kind:
            config["environments"] = ["production", "staging"]
        if "sets" in kind:
            config["sets"] = True
        (project / "config.json").write_text(json.dumps(config))
        (project / "featurevisor.config.js").write_text("module.exports = " + json.dumps(config))
        roots = [project / "sets" / name for name in ("blue", "green")] if config.get("sets") else [project]
        for root in roots:
            for directory in ("features", "segments", "attributes", "variables", "targets"):
                (root / directory).mkdir(parents=True, exist_ok=True)
            (root / "attributes/userId.yml").write_text("description: User ID\ntype: string\n")
            rules = "  - key: everyone\n    conditions: '*'\n    percentage: 100\n"
            feature = "description: Ready\nbucketBy: userId\nrules:\n"
            if config.get("environments"):
                feature += "  production:\n" + "\n".join("  " + line for line in rules.splitlines()) + "\n  staging:\n" + "\n".join("  " + line for line in rules.splitlines()) + "\n"
            else:
                feature += rules
            (root / "features/ready.yml").write_text(feature)
            for target in ("all", "web"):
                (root / f"targets/{target}.yml").write_text("description: Everything\n")
        return project

    def run_cli(self, command: str, project: Path, *args: str, success: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([*self.runner, command, f"--projectDirectoryPath={project}", "--feature=ready", "--n=2", *args], env=self.env, capture_output=True, encoding="utf-8", timeout=30)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        if success:
            self.assertIn("Iterations: 2", result.stdout)
            if command == "benchmark":
                self.assertRegex(result.stdout, r"Evaluated value\s*: true")
            else:
                self.assertRegex(result.stdout, r"(?:enabled|1): 2")
        return result

    def recorded(self) -> list[list[str]]:
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_flat_projects_and_repeated_targets(self) -> None:
        project = self.project("flat")
        for command in ("benchmark", "assess-distribution"):
            self.run_cli(command, project)
            output = self.run_cli(command, project, "--target=all", "--target=web").stdout
            self.assertIn("Target: all", output)
            self.assertIn("Target: web", output)
            self.run_cli(command, project, "--target=missing", success=False)
        self.assertFalse(any(arg.startswith("--environment=") for call in self.recorded() for arg in call))

    def test_configured_environments_require_valid_selection(self) -> None:
        project = self.project("env")
        for command in ("benchmark", "assess-distribution"):
            self.run_cli(command, project, success=False)
            self.run_cli(command, project, "--environment=missing", success=False)
            self.run_cli(command, project, "--environment=production", "--target=all")

    def test_flat_projects_reject_environment(self) -> None:
        project = self.project("flat")
        for command in ("benchmark", "assess-distribution"):
            self.run_cli(command, project, "--environment=production", success=False)
            self.run_cli(command, project, "--environment=", success=False)

    def test_sets_delegate_selected_set_and_targets(self) -> None:
        project = self.project("sets")
        for command in ("benchmark", "assess-distribution"):
            result = self.run_cli(command, project, "--set=blue", "--target=all", "--target=web")
            self.assertIn('Set "blue"', result.stdout)
            self.run_cli(command, project, "--set=missing", success=False)
        calls = self.recorded()
        self.assertEqual(sum("--set=blue" in call for call in calls if call[0] == "build"), 4)
        self.assertTrue(all(any(arg.startswith("--set=") for arg in call) for call in calls if call[0] in ("list", "build")))
        self.assertTrue(all(not any(arg.startswith("--set=") for arg in call) for call in calls if call[0] == "config"))

    def test_flat_projects_preserve_explicit_set_for_node_validation(self) -> None:
        project = self.project("flat")
        tests = project / "tests/features"
        tests.mkdir(parents=True)
        (tests / "ready.spec.yml").write_text("feature: ready\nassertions:\n  - at: 50\n    expectedToBeEnabled: true\n")
        for name, contents in (("config.json", '{"sets":false}'), ("featurevisor.config.js", 'module.exports = {sets: false}')):
            (project / name).write_text(contents)
        for command in ("test", "benchmark", "assess-distribution"):
            for options in ([], ["--set=blue"], ["--set="], ["--set=0"]):
                with self.subTest(command=command, options=options):
                    before = len(self.recorded()) if self.calls.exists() else 0
                    result = subprocess.run([*self.runner, command, f"--projectDirectoryPath={project}", "--feature=ready", "--n=2", *options], env=self.env, capture_output=True, encoding="utf-8", timeout=30)
                    self.assertEqual(result.returncode == 0, not options, result.stdout + result.stderr)
                    delegated = [call for call in self.recorded()[before:] if call[0] != "config"]
                    self.assertTrue(delegated)
                    for call in delegated:
                        self.assertEqual([arg for arg in call if arg.startswith("--set=")], options)
                    if options:
                        self.assertIn("Omit --set", result.stdout + result.stderr)

    def test_sets_without_selection_visit_every_set(self) -> None:
        project = self.project("sets")
        for command in ("benchmark", "assess-distribution"):
            output = self.run_cli(command, project).stdout
            self.assertIn('Set "blue"', output)
            self.assertIn('Set "green"', output)
        self.assertEqual(sum("--set=blue" in call for call in self.recorded() if call[0] == "build"), 2)
        self.assertEqual(sum("--set=green" in call for call in self.recorded() if call[0] == "build"), 2)

    def test_sets_with_configured_environments(self) -> None:
        project = self.project("sets-env")
        for command in ("benchmark", "assess-distribution"):
            self.run_cli(command, project, "--set=blue", success=False)
            self.run_cli(command, project, "--set=blue", "--environment=production", "--target=web")
        builds = [call for call in self.recorded() if call[0] == "build"]
        self.assertEqual(len(builds), 2)
        for call in builds:
            self.assertIn("--set=blue", call)
            self.assertIn("--environment=production", call)
            self.assertIn("--target=web", call)

    def test_invalid_options_fail(self) -> None:
        project = self.project("flat")
        for command in ("benchmark", "assess-distribution"):
            result = subprocess.run([*self.runner, command], env=self.env, capture_output=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)
            for args in (("--n=0",), ("--context=[]",), ("--context=invalid",), ("--variation", "--variable=colour")):
                # Override the default count rather than passing duplicate options.
                if args == ("--n=0",):
                    result = subprocess.run([*self.runner, command, f"--projectDirectoryPath={project}", "--feature=ready", *args], env=self.env, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                else:
                    self.run_cli(command, project, *args, success=False)

    def test_node_failure_returns_unsuccessful_exit(self) -> None:
        project = self.project("flat")
        (project / "fail-build").touch()
        if os.environ.get("FEATUREVISOR_NODE_CLI"):
            (project / "features/ready.yml").write_text("rules: [invalid\n")
        for command in ("benchmark", "assess-distribution"):
            self.run_cli(command, project, success=False)

    def test_invalid_inflate_fails_before_node_delegation(self) -> None:
        project = self.project("flat")
        for command in ("test", "benchmark", "assess-distribution"):
            for option in ("--inflate=0", "--inflate=-5", "--inflate=1.5", "--inflate=invalid", "--inflate=", "--inflate=1foo", "--inflate"):
                with self.subTest(command=command, option=option):
                    result = subprocess.run([*self.runner, command, f"--projectDirectoryPath={project}", "--feature=ready", "--n=2", option], env=self.env, capture_output=True, encoding="utf-8", timeout=30)
                    self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn("inflate", result.stdout + result.stderr)
                    self.assertFalse(self.calls.exists(), "Invalid inflation delegated to Node")

    def test_inflate_omission_and_positive_value_are_delegated(self) -> None:
        project = self.project("flat")
        tests = project / "tests/features"
        tests.mkdir(parents=True)
        (tests / "ready.spec.yml").write_text("feature: ready\nassertions:\n  - at: 50\n    expectedToBeEnabled: true\n")
        for command in ("test", "benchmark", "assess-distribution"):
            for options in ([], ["--inflate=1"]):
                with self.subTest(command=command, options=options):
                    before = len(self.recorded()) if self.calls.exists() else 0
                    result = subprocess.run([*self.runner, command, f"--projectDirectoryPath={project}", "--feature=ready", "--n=2", *options], env=self.env, capture_output=True, encoding="utf-8", timeout=30)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    builds = [call for call in self.recorded()[before:] if call[0] == "build"]
                    self.assertTrue(builds)
                    for call in builds:
                        self.assertEqual([arg for arg in call if arg.startswith("--inflate=")], options)


if __name__ == "__main__":
    unittest.main()
