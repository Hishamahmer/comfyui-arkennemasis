import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from mcp.server.auth.provider import AccessToken

from mcp_service.server import create_http_app
from mcp_service.settings import SCOPES, Settings


class TestVerifier:
    async def verify_token(self, token):
        if token != "read-only-owner":
            return None
        return AccessToken(token=token, client_id="test-client", subject="owner",
                           scopes=["comfy:read"], expires_at=int(time.time()) + 120,
                           resource="https://comfy.example.test/mcp")

    async def aclose(self):
        pass


class Backend:
    def __init__(self):
        self.calls = []

    async def status(self):
        self.calls.append("status")
        return {"connected": True}

    async def close(self):
        pass


class OAuthIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_challenge_and_per_tool_scope_enforcement(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(auth_mode="oauth", bridge_token="b" * 48,
                                public_url="https://comfy.example.test", audience="https://comfy.example.test/mcp",
                                issuer_url="https://issuer.example.test/", jwks_url="https://issuer.example.test/keys",
                                allowed_subjects=["owner"], workflow_root=str(Path(directory) / "workflows"),
                                state_dir=directory)
            backend = Backend()
            with patch("mcp_service.server.OAuthTokenVerifier", return_value=TestVerifier()):
                app = create_http_app(settings, client=backend)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url=settings.public_url,
                                             headers={"Accept": "application/json, text/event-stream",
                                                      "MCP-Protocol-Version": "2025-11-25"}) as client:
                    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
                        response = await client.get(path)
                        self.assertEqual(response.status_code, 200)
                        metadata = response.json()
                        self.assertEqual(metadata["resource"], settings.endpoint)
                        self.assertEqual(set(metadata["scopes_supported"]), set(SCOPES))
                        self.assertEqual(metadata["authorization_servers"], [settings.issuer_url])
                    response = await client.post("/mcp", json={})
                    self.assertEqual(response.status_code, 401)
                    self.assertIn("/.well-known/oauth-protected-resource/mcp", response.headers["www-authenticate"])
                    client.headers["Authorization"] = "Bearer read-only-owner"

                    async def call(name, arguments):
                        result = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                               "params": {"name": name, "arguments": arguments}})
                        self.assertEqual(result.status_code, 200, result.text)
                        return result.json()["result"]

                    result = await call("server_status", {})
                    self.assertFalse(result.get("isError", False))
                    result = await call("save_workflow", {"name": "denied.json", "workflow": {}})
                    self.assertTrue(result["isError"])
                    challenge = result["_meta"]["mcp/www_authenticate"][0]
                    self.assertIn('scope="comfy:write"', challenge)
                    self.assertFalse((Path(settings.workflow_root) / "denied.json").exists())
                    self.assertEqual(backend.calls, ["status"])


if __name__ == "__main__":
    unittest.main()
