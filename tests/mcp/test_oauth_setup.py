import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp_service.oauth_setup import OAuthClients, OAuthSetup
from mcp_service.settings import enable_connection_link, initialize, load_settings


PROVIDER = {
    "public_url": "https://comfy.example.test", "issuer_url": "https://issuer.example.test/",
    "jwks_url": "https://issuer.example.test/keys", "audience": "https://comfy.example.test/mcp",
    "allowed_subjects": ["owner-123"], "allowed_algorithms": ["RS256"], "scope_claim": "scope",
}


class OAuthSetupTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "config.json"
        initialize(self.path)
        self.original = enable_connection_link(self.path, public_url="https://comfy.example.test")
        self.setup = OAuthSetup(self.path)

    def save(self):
        return self.setup.save(PROVIDER, self.setup.status()["config_revision"])

    def test_saving_only_stages_provider_and_never_changes_connection(self):
        before = self.path.read_bytes()
        state = self.save()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(state["auth_mode"], "connection_link")
        self.assertEqual(state["draft"], PROVIDER)
        self.assertTrue(state["draft_revision"])
        self.assertEqual(state["draft"]["issuer_url"], "https://issuer.example.test/")

    def test_enable_requires_reviewed_revisions_and_retains_owner_credentials(self):
        draft = self.save()
        result = self.setup.enable(draft["config_revision"], draft["draft_revision"])
        enabled = load_settings(self.path)
        self.assertEqual(enabled.auth_mode, "oauth")
        self.assertEqual(enabled.endpoint, PROVIDER["audience"])
        self.assertTrue(result["restart_required"])
        for name in ("connection_token", "local_token", "bridge_token"):
            self.assertEqual(getattr(enabled, name), getattr(self.original, name))
            self.assertNotIn(getattr(enabled, name), json.dumps(result))
        disabled = self.setup.disable(result["config_revision"])
        self.assertEqual(disabled["auth_mode"], "connection_link")
        self.assertEqual(load_settings(self.path).connection_token, self.original.connection_token)

    def test_invalid_provider_never_overwrites_a_valid_draft_or_configuration(self):
        state = self.save()
        old_draft = self.setup.draft_path.read_bytes()
        old_config = self.path.read_bytes()
        changes = [
            {"public_url": "https://example.test/path"}, {"public_url": "https://example.test:99999"},
            {"public_url": "https://example.test\n"}, {"public_url": "https://@example.test"},
            {"public_url": "https://example.test\\x"}, {"issuer_url": "http://issuer.example.test/"},
            {"jwks_url": "https://user:secret@issuer.example.test/keys"}, {"jwks_url": "https://issuer.example.test/keys?secret=x"},
            {"audience": "https://another.example.test/mcp"}, {"allowed_subjects": []},
            {"allowed_subjects": "owner"}, {"allowed_subjects": [" "]}, {"allowed_subjects": ["owner\n"]},
            {"allowed_subjects": ["a" * 513]}, {"allowed_algorithms": ["HS256"]},
            {"allowed_algorithms": []}, {"allowed_algorithms": "RS256"},
            {"allowed_algorithms": [1]}, {"scope_claim": "scope claim"}, {"scope_claim": 1},
        ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.setup.save({**PROVIDER, **change}, state["config_revision"])
            self.assertEqual(self.setup.draft_path.read_bytes(), old_draft)
            self.assertEqual(self.path.read_bytes(), old_config)

    def test_changed_config_or_draft_must_be_reviewed_again(self):
        draft = self.save()
        self.path.write_text(self.path.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "settings changed"):
            self.setup.enable(draft["config_revision"], draft["draft_revision"])
        with self.assertRaisesRegex(ValueError, "settings changed"):
            self.setup.save(PROVIDER, draft["config_revision"])
        draft = self.save()
        self.setup.draft_path.write_text(self.setup.draft_path.read_text() + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "provider settings changed"):
            self.setup.enable(draft["config_revision"], draft["draft_revision"])
        self.assertEqual(load_settings(self.path).auth_mode, "connection_link")

    def test_atomic_replacement_failure_preserves_existing_config(self):
        draft = self.save()
        before = self.path.read_bytes()
        with patch("mcp_service.settings.os.replace", side_effect=PermissionError("busy")):
            with self.assertRaises(OSError):
                self.setup.enable(draft["config_revision"], draft["draft_revision"])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(list(self.path.parent.glob(".config-*.tmp")))

    def test_missing_and_corrupt_settings_report_useful_status(self):
        missing = OAuthSetup(self.path.parent / "missing.json").status()
        self.assertFalse(missing["configured"])
        self.assertTrue(missing["requires_provider_setup"])
        self.assertIn("MCP setup first", missing["message"])
        self.path.write_text('{"broken":', encoding="utf-8")
        state = self.setup.status()
        self.assertFalse(state["configured"])
        self.assertTrue(state["errors"])
        self.assertNotIn("broken", json.dumps(state))

    def test_missing_invalid_or_foreign_draft_cannot_enable(self):
        state = self.setup.status()
        with self.assertRaisesRegex(ValueError, "Save valid"):
            self.setup.enable(state["config_revision"], None)
        self.setup.draft_path.write_text('{"extra":true}', encoding="utf-8")
        self.assertIn("draft is invalid", self.setup.status()["errors"][0])
        with self.assertRaises(ValueError):
            self.setup.save({**PROVIDER, "auth_mode": "connection_link"}, state["config_revision"])

    def test_revocation_is_persisted_and_does_not_change_connection_mode(self):
        self.setup.revoke("https://chatgpt.com/oauth/client.json")
        recreated = OAuthSetup(self.path)
        self.assertEqual(recreated.status()["revoked_clients"], ["https://chatgpt.com/oauth/client.json"])
        self.assertEqual(recreated.status()["auth_mode"], "connection_link")
        recreated.revoke("https://chatgpt.com/oauth/client.json", False)
        self.assertEqual(self.setup.status()["revoked_clients"], [])


class OAuthClientStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "oauth-clients.json"
        self.clients = OAuthClients(self.path)

    def test_ids_are_exact_deduplicated_and_survive_recreation(self):
        self.assertEqual(self.clients.denied(), [])
        self.clients.set_revoked("ExactClient", True)
        self.clients.set_revoked("ExactClient", True)
        self.clients.set_revoked("other", True)
        self.assertEqual(OAuthClients(self.path).denied(), ["ExactClient", "other"])
        self.clients.set_revoked("exactclient", False)
        self.assertEqual(self.clients.denied(), ["ExactClient", "other"])

    def test_invalid_ids_and_broken_state_never_clear_revocations(self):
        self.clients.set_revoked("blocked", True)
        before = self.path.read_bytes()
        for identifier in ("", "has space", "newline\n", "x" * 1025, "\x7f", None, 42):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.clients.set_revoked(identifier, True)
            self.assertEqual(self.path.read_bytes(), before)
        self.path.write_text('{"version":1,"version":2,"denied_client_ids":[]}', encoding="utf-8")
        with self.assertRaises(ValueError):
            self.clients.denied()
        with self.assertRaises(ValueError):
            self.clients.set_revoked("another", True)


if __name__ == "__main__":
    unittest.main()
