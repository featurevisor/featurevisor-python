import os
import pathlib
import tempfile
import unittest
from unittest import mock

from featurevisor.project import node_cli_command


class NodeCliCommandTest(unittest.TestCase):
    def test_runs_the_nearest_local_cli_entry_with_node(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory).resolve()
            entry = root / "node_modules" / "@featurevisor" / "cli" / "bin.js"
            entry.parent.mkdir(parents=True)
            entry.write_text("")
            project = root / "packages" / "flags"
            project.mkdir(parents=True)
            self.assertEqual(node_cli_command(project), ["node", str(entry)])

    def test_falls_back_to_npx_and_uses_the_windows_wrapper_path(self):
        with tempfile.TemporaryDirectory() as directory:
            project = pathlib.Path(directory)
            self.assertEqual(node_cli_command(project), ["npx", "featurevisor"])
            with mock.patch.object(os, "name", "nt"), mock.patch(
                "featurevisor.project.shutil.which", return_value=r"C:\\node\\npx.cmd"
            ):
                self.assertEqual(node_cli_command(project), [r"C:\\node\\npx.cmd", "featurevisor"])


if __name__ == "__main__":
    unittest.main()
