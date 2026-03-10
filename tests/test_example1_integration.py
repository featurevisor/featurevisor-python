from __future__ import annotations

import os
import subprocess
import sys
import unittest

ROOT = "/Users/fahad/Repos/featurevisor/examples/example-1"


class Example1IntegrationTests(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = "src"
        return subprocess.run(
            [sys.executable, "-m", "featurevisor", *args],
            cwd="/Users/fahad/Repos/featurevisor-python",
            text=True,
            capture_output=True,
            env=env,
        )

    def test_example1_allow_signup(self) -> None:
        result = self._run("test", f"--projectDirectoryPath={ROOT}", "--keyPattern=allowSignup", "--onlyFailures")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_example1_all(self) -> None:
        result = self._run("test", f"--projectDirectoryPath={ROOT}", "--onlyFailures")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Test specs:", result.stdout)
        self.assertIn("Assertions:", result.stdout)

    def test_benchmark_smoke(self) -> None:
        result = self._run(
            "benchmark",
            f"--projectDirectoryPath={ROOT}",
            "--environment=production",
            "--feature=allowSignup",
            '--context={"deviceId":"123","country":"de"}',
            "--n=10",
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Evaluated value", result.stdout)

    def test_example1_with_tags(self) -> None:
        result = self._run("test", f"--projectDirectoryPath={ROOT}", "--with-tags", "--onlyFailures")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)

    def test_assertion_output_is_printed(self) -> None:
        result = self._run("test", f"--projectDirectoryPath={ROOT}", "--keyPattern=allowSignup")
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("Testing: features/allowSignup.spec.yml", result.stdout)
        self.assertIn("✔ Assertion #1: DE at 40% should have control variation", result.stdout)


if __name__ == "__main__":
    unittest.main()
