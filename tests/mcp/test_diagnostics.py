import json
from pathlib import Path
import tempfile
import unittest

from mcp_service.diagnostics import connection_state, read_json, read_log, public_probe
from mcp_service.settings import Settings


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings(state_dir=self.temp.name, comfy_root=self.temp.name, connection_token="SAMPLE_PRIVATE_TOKEN")

    def tearDown(self):
        self.temp.cleanup()

    def test_corrupt_status_does_not_prevent_diagnosis(self):
        path = self.root / "session-status.json"
        for value in ([], 1, {"updated_at": "wrong"}, {"updated_at": float("nan")}):
            path.write_text(json.dumps(value), encoding="utf-8")
            self.assertIsInstance(read_json(path), dict)
            self.assertEqual(connection_state(self.settings)["session_state"], "unknown")

    def test_logs_are_bounded_and_credentials_redacted(self):
        text = '\n'.join(["older"] * 20 + ['SAMPLE_PRIVATE_TOKEN {"token": "SAMPLE_JSON_SECRET"} https://SAMPLE_URL_SECRET@github.com/repo'])
        (self.root / "web.log").write_text(text, encoding="utf-8")
        result = read_log(self.settings, "gateway", 1)
        self.assertEqual(len(result["lines"]), 1)
        self.assertNotIn("SAMPLE_", result["lines"][0])
        for source in ("../config.json", "config"):
            with self.assertRaises(ValueError):
                read_log(self.settings, source)

    def test_probe_rejects_unconfigured_origin_and_bad_limits(self):
        for samples in (0, 7, True):
            with self.assertRaises(ValueError):
                public_probe(self.settings, samples)
        with self.assertRaises(ValueError):
            public_probe(self.settings)
