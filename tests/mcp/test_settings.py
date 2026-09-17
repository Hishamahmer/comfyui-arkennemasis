import json
import tempfile
import unittest
from pathlib import Path

from mcp_service.settings import Settings, enable_connection_link, initialize, load_settings


class SettingsTests(unittest.TestCase):
    def test_development_and_maintenance_require_owner_opt_in(self):
        settings = Settings(local_token="a" * 48, bridge_token="b" * 48).validate()
        self.assertNotIn("comfy:develop", settings.enabled_scopes)
        self.assertNotIn("comfy:maintain", settings.enabled_scopes)
        self.assertEqual(settings.allowed_node_packs, [])
        settings.allowed_node_packs = ["../outside"]
        with self.assertRaises(ValueError):
            settings.validate()

    def test_init_is_private_unique_and_never_replaces_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            first = initialize(path)
            self.assertNotEqual(first.local_token, first.bridge_token)
            self.assertEqual(load_settings(path).local_token, first.local_token)
            with self.assertRaises(FileExistsError):
                initialize(path)
            self.assertEqual(json.loads(path.read_text())["local_token"], first.local_token)

    def test_rejects_remote_backend_and_public_local_tokens(self):
        for changes in ({"host": "0.0.0.0"}, {"comfy_url": "http://example.com:8188"},
                        {"comfy_url": "http://localhost.evil.example:8188"},
                        {"public_url": "https://mcp.example.com"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Settings(local_token="a" * 48, bridge_token="b" * 48, **changes).validate()

    def test_private_connection_link_preserves_credentials_and_redacts_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            original = initialize(path)
            linked = enable_connection_link(path)
            self.assertEqual(linked.auth_mode, "connection_link")
            self.assertEqual(linked.local_token, original.local_token)
            self.assertEqual(linked.bridge_token, original.bridge_token)
            self.assertGreaterEqual(len(linked.connection_token), 48)
            self.assertEqual(enable_connection_link(path).connection_token, linked.connection_token)
            self.assertNotIn(linked.connection_token, linked.display_endpoint)
            self.assertNotIn(linked.connection_token, repr(linked))
            self.assertEqual(load_settings(path).connection_token, linked.connection_token)

    def test_oauth_requires_concrete_owner_and_https_configuration(self):
        settings = Settings(bridge_token="b" * 48, auth_mode="oauth",
                            public_url="https://mcp.example.com", issuer_url="https://identity.example.com/",
                            jwks_url="https://identity.example.com/jwks", audience="https://mcp.example.com/mcp")
        with self.assertRaises(ValueError):
            settings.validate()
        settings.allowed_subjects = ["owner"]
        self.assertIs(settings.validate(), settings)
        self.assertEqual(settings.endpoint, "https://mcp.example.com/mcp")

    def test_fixed_connection_origin_preserves_all_credentials_and_rejects_invalid_origin(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            initialize(path)
            old = enable_connection_link(path)
            fixed = enable_connection_link(path, public_url="https://machine.tailnet.ts.net/")
            self.assertEqual(fixed.public_url, "https://machine.tailnet.ts.net")
            for field in ("local_token", "bridge_token", "connection_token"):
                self.assertEqual(getattr(fixed, field), getattr(old, field))
            saved = path.read_bytes()
            for origin in ("http://example.com", "https://example.com/path", "https://user:pass@example.com"):
                with self.assertRaises(ValueError):
                    enable_connection_link(path, public_url=origin)
                self.assertEqual(path.read_bytes(), saved)


if __name__ == "__main__":
    unittest.main()
