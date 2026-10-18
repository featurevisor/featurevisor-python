import contextlib
import io
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from featurevisor import Featurevisor, create_featurevisor


class ReadmeInitializationTests(unittest.TestCase):
    def setUp(self):
        readme = Path(__file__).resolve().parents[1] / "README.md"
        match = re.search(r"## Initialization\n.*?```python\n(.*?)\n```", readme.read_text(), re.S)
        self.assertIsNotNone(match, "README initialization fence not found")
        self.code = compile(match[1], str(readme), "exec")

    def fixture(self, enabled):
        return json.dumps({
            "schemaVersion": "2", "revision": "readme", "segments": {}, "variables": {},
            "features": {"checkout": {"key": "checkout", "bucketBy": "userId", "traffic": [
                {"key": "all", "segments": "*", "percentage": 100000, "enabled": enabled},
            ]}},
        }).encode()

    def test_evaluation_and_cleanup(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled):
                response = io.BytesIO(self.fixture(enabled))
                namespace = {}
                output = io.StringIO()
                with patch("urllib.request.urlopen", return_value=response) as fetch:
                    with contextlib.redirect_stdout(output):
                        exec(self.code, namespace)
                fetch.assert_called_once_with("https://cdn.yoursite.com/featurevisor-all.json")
                self.assertIn(str(enabled), output.getvalue().splitlines())
                self.assertTrue(response.closed)
                self.assertTrue(namespace["f"].closed)

    def test_cleanup_when_application_evaluation_raises(self):
        instance = create_featurevisor({})
        self.addCleanup(instance.close)
        with patch("urllib.request.urlopen", return_value=io.BytesIO(self.fixture(True))):
            with patch("featurevisor.create_featurevisor", return_value=instance):
                with patch.object(Featurevisor, "is_enabled", side_effect=RuntimeError("application failure")):
                    with self.assertRaisesRegex(RuntimeError, "application failure"):
                        exec(self.code, {})
        self.assertTrue(instance.closed)

    def test_invalid_json_does_not_initialize(self):
        response = io.BytesIO(b"{")
        with patch("urllib.request.urlopen", return_value=response):
            with patch("featurevisor.create_featurevisor") as create:
                with self.assertRaises(json.JSONDecodeError):
                    exec(self.code, {})
                create.assert_not_called()
        self.assertTrue(response.closed)

    def test_http_and_transport_errors_do_not_initialize(self):
        for error in (HTTPError("fixture", 503, "Unavailable", {}, None), URLError("offline")):
            with self.subTest(error=error):
                with patch("urllib.request.urlopen", side_effect=error):
                    with patch("featurevisor.create_featurevisor") as create:
                        with self.assertRaises(type(error)):
                            exec(self.code, {})
                        create.assert_not_called()
