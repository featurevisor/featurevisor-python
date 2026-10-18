import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch

from featurevisor.cli import build_parser, main


class CLIOptionTests(unittest.TestCase):
    def test_aliases_are_equivalent_in_both_value_forms(self):
        aliases = {
            "projectDirectoryPath": ("project-directory-path", "/tmp/project with spaces"),
            "keyPattern": ("key-pattern", "^ready.*$"),
            "assertionPattern": ("assertion-pattern", "ready with spaces"),
            "onlyFailures": ("only-failures", None),
            "showDatafile": ("show-datafile", None),
            "populateUuid": ("populate-uuid", "userId"),
            "entityType": ("entity-type", "feature"),
        }
        parser = build_parser()
        for command in ("test", "benchmark", "assess-distribution"):
            for camel, (kebab, value) in aliases.items():
                if camel == "entityType" and command != "test":
                    continue
                for separate in (False, True):
                    def args(name):
                        return [f"--{name}"] if value is None else ([f"--{name}", value] if separate else [f"--{name}={value}"])
                    with self.subTest(command=command, option=kebab, separate=separate):
                        self.assertEqual(vars(parser.parse_args([command, *args(camel)])),
                                         vars(parser.parse_args([command, *args(kebab)])))
            for name in ("project-directory-path", "projectDirectoryPath", "root-directory-path", "rootDirectoryPath"):
                for arguments in ([f"--{name}=/tmp/quoted ' path"], [f"--{name}", "/tmp/quoted ' path"]):
                    self.assertEqual(parser.parse_args([command, *arguments]).projectDirectoryPath, "/tmp/quoted ' path")

    def test_iterations_repeated_selectors_and_json_are_not_split(self):
        parser = build_parser()
        context = '{"userId":"a b", "literal":"$(touch injected)"}'
        for command in ("test", "benchmark", "assess-distribution"):
            for iterations in (["--n=2"], ["--n", "2"], ["-n=2"], ["-n", "2"]):
                args = parser.parse_args([command, *iterations, "--target=web space", "--target", "mobile",
                    "--target=web space", "--populate-uuid=userId", "--populateUuid", "accountId", "--context", context])
                self.assertEqual(args.n, 2)
                self.assertEqual(args.target, ["web space", "mobile", "web space"])
                self.assertEqual(args.populateUuid, ["userId", "accountId"])
                self.assertEqual(args.context, context)
        with patch("featurevisor.cli.run_assess_distribution", return_value=0) as runner:
            self.assertEqual(main(["assess-distribution", "--feature=ready", "-n=2", "--context", context,
                "--populate-uuid=userId", "--populateUuid", "accountId"]), 0)
            self.assertEqual(runner.call_args.kwargs["context"], json.loads(context))
            self.assertEqual(runner.call_args.kwargs["populate_uuid"], ["userId", "accountId"])

    def test_usage_errors_never_delegate_or_write_stdout(self):
        invalid = [["--unknown"], ["extra"], ["--", "extra"], ["--target", "web", "mobile"],
                   ["--populate-uuid", "userId", "accountId"], ["--target="], ["--populate-uuid="],
                   ["--root-directory-path="], ["--target"], ["--populate-uuid"], ["--context="],
                   ["--context=[]"], ["--context=null"], ["--context={"], ["--feature="], ["--variable="]]
        for name in ("--n", "-n", "--inflate"):
            invalid.extend([[f"{name}={value}"] for value in ("0", "-1", "1.5", "bad", "")])
            invalid.append([name])
        with patch("featurevisor.cli.run_test_project") as test, patch("featurevisor.cli.run_benchmark") as benchmark, patch("featurevisor.cli.run_assess_distribution") as assess:
            for command in ("test", "benchmark", "assess-distribution"):
                for arguments in invalid:
                    with self.subTest(command=command, arguments=arguments):
                        stdout, stderr = StringIO(), StringIO()
                        with redirect_stdout(stdout), redirect_stderr(stderr):
                            try:
                                status = main([command, "--feature=ready", *arguments])
                            except SystemExit as error:
                                status = error.code
                        self.assertNotEqual(status, 0)
                        self.assertEqual(stdout.getvalue(), "")
                        self.assertTrue(stderr.getvalue())
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main(["test", "--entity-type=unknown", "--json"])
            for runner in (test, benchmark, assess):
                runner.assert_not_called()

    def test_help_and_unknown_commands(self):
        for arguments in (["--help"], ["test", "--help"], ["benchmark", "--help"], ["assess-distribution", "--help"]):
            output = StringIO()
            with redirect_stdout(output), self.assertRaises(SystemExit) as error:
                main(arguments)
            self.assertEqual(error.exception.code, 0)
            self.assertIn("usage:", output.getvalue())
            if len(arguments) > 1:
                self.assertIn("--root-directory-path", output.getvalue())
                self.assertIn("--populate-uuid", output.getvalue())
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main(["evaluate"])
        self.assertNotEqual(error.exception.code, 0)

    def test_real_process_preserves_paths_selectors_and_json_boundary(self):
        with tempfile.TemporaryDirectory(prefix="featurevisor cli ") as directory:
            root = Path(directory)
            project = root / "project ' café $(touch injected)"
            project.mkdir()
            calls = root / "calls.jsonl"
            shim = root / "npx"
            shim.write_text(f'''#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[2:]
with Path({str(calls)!r}).open("a") as log:
    log.write(json.dumps([os.getcwd(), args]) + "\\n")
if args[0] == "config": value = {{"environments": False, "sets": False}}
elif args[0] == "list":
    if "--tests" in args: value = {{"kind": "featurevisor.test-execution", "schemaVersion": 1, "tests": [{{"feature": "ready", "assertions": [{{"expectedToBeEnabled": True}}]}}]}}
    elif "--targets" in args: value = ["web space", "mobile'quote"]
    else: value = []
else:
    sys.stderr.write("delegated warning\\n")
    value = {{"schemaVersion": "2", "revision": "test", "segments": {{}}, "features": {{"ready": {{"bucketBy": "userId", "traffic": [{{"key": "all", "segments": "*", "percentage": 100000}}]}}}}}}
print(json.dumps(value))
''', encoding="utf-8")
            shim.chmod(0o755)
            env = dict(os.environ, PATH=f"{root}{os.pathsep}{os.environ['PATH']}",
                       PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
            context = json.dumps({"userId": "a b ' café", "literal": "$(touch injected)"})
            pattern = "ready ' café;$()"
            for command in ("test", "benchmark", "assess-distribution"):
                calls.write_text("")
                options = ["--json", "--show-datafile", "--only-failures", "--key-pattern", pattern,
                           "--assertion-pattern=with spaces", "--entity-type=feature"] if command == "test" else ["--context", context]
                result = subprocess.run([sys.executable, "-m", "featurevisor", command,
                    "--root-directory-path", str(project), "--feature=ready", "-n=2",
                    "--target=web space", "--target", "mobile'quote", "--target=web space", *options],
                    env=env, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                recorded = [json.loads(line) for line in calls.read_text().splitlines()]
                self.assertTrue(all(Path(cwd).resolve() == project.resolve() for cwd, _ in recorded))
                targets = [arg for _, call in recorded if call[0] == "build" for arg in call if arg.startswith("--target=")]
                self.assertEqual(targets, ["--target=web space", "--target=mobile'quote"])
                if command == "test":
                    self.assertTrue(json.loads(result.stdout)["passed"])
                    delegated = next(call for _, call in recorded if "--tests" in call)
                    self.assertIn(f"--keyPattern={pattern}", delegated)
                    self.assertIn("--assertionPattern=with spaces", delegated)
                    self.assertIn("--entityType=feature", delegated)
                    self.assertIn('"schemaVersion": "2"', result.stderr)
                else:
                    self.assertNotIn("Environment:", result.stdout)
                    self.assertIn(context, result.stdout)
                self.assertFalse((project / "injected").exists())
