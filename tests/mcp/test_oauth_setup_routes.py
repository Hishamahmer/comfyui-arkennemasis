import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aiohttp import web

from mcp_service.oauth_setup_routes import OAuthSetupRoutes, register_oauth_setup_routes


class Content:
    def __init__(self, data):
        self.data = data

    async def iter_chunked(self, size):
        yield self.data


def request(body=None, remote="127.0.0.1", origin="http://127.0.0.1:8188"):
    return SimpleNamespace(remote=remote, scheme="http", host="127.0.0.1:8188",
                           headers={"Origin": origin, "X-Ark-Canvas": "1"}, content_type="application/json",
                           content=Content(json.dumps(body or {}).encode()))


class OAuthSetupRoutesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.oauth = Mock()
        self.routes = OAuthSetupRoutes(self.oauth)

    async def test_all_mutations_require_loopback_same_origin_owner(self):
        for name in ("status", "save", "enable", "disable", "revoke", "unrevoke"):
            for candidate in (request(remote="192.168.1.2"), request(origin="https://chatgpt.com"), request(origin="null")):
                with self.subTest(name=name), self.assertRaises(web.HTTPForbidden):
                    await getattr(self.routes, name)(candidate)
        self.assertEqual(self.oauth.mock_calls, [])

    async def test_status_does_not_accept_control_arguments(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.routes.status(request({"auth_mode": "oauth"}))

    async def test_enable_uses_only_saved_revisions(self):
        self.oauth.enable.return_value = {"restart_required": True}
        result = await self.routes.enable(request({"config_revision": "config", "draft_revision": "draft"}))
        self.assertEqual(result.status, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        self.oauth.enable.assert_called_once_with("config", "draft")
        self.oauth.enable.reset_mock()
        result = await self.routes.enable(request({"config_revision": "config", "draft_revision": "draft", "issuer_url": "https://wrong.example"}))
        self.assertEqual(result.status, 400)
        self.oauth.enable.assert_not_called()

    async def test_revoke_and_unrevoke_pass_exact_client_identity(self):
        self.oauth.revoke.return_value = {"revoked": True}
        result = await self.routes.revoke(request({"client_id": "test-client"}))
        self.assertEqual(result.status, 200)
        self.oauth.revoke.assert_called_with("test-client", True)
        await self.routes.unrevoke(request({"client_id": "test-client"}))
        self.oauth.revoke.assert_called_with("test-client", False)

    async def test_route_registration_has_no_public_gets(self):
        routes = web.RouteTableDef()
        register_oauth_setup_routes(routes)
        self.assertEqual(len(routes), 6)
        self.assertEqual({route.method for route in routes}, {"POST"})
        self.assertTrue(all(route.path.startswith("/arkennemasis/mcp/oauth/") for route in routes))


if __name__ == "__main__":
    unittest.main()
