import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from aiohttp import web

from mcp_service.setup_routes import SetupRoutes, authorized_owner, owner_body


class Content:
    def __init__(self, body):
        self.body = body

    async def iter_chunked(self, size):
        for offset in range(0, len(self.body), size):
            yield self.body[offset:offset + size]


def request(body=b"{}", remote="127.0.0.1", origin="http://127.0.0.1:8188", host="127.0.0.1:8188"):
    return SimpleNamespace(remote=remote, scheme="http", host=host, content_type="application/json",
                           headers={"Origin": origin, "X-Ark-Canvas": "1"}, content=Content(body))


class SetupRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.installation = Mock()
        self.setup = SetupRoutes(self.installation)

    async def test_first_setup_requires_same_origin_loopback_and_header_not_token(self):
        self.installation.status.return_value = {"configured": False}
        result = await self.setup.status(request())
        self.assertEqual(result.status, 200)
        self.assertEqual(result.headers["Cache-Control"], "no-store")
        for candidate in (request(remote="192.168.1.2"), request(origin="https://evil.example"),
                          request(origin="null"), request(origin="http://evil.example:8188", host="evil.example:8188"),
                          request(origin="http://127.0.0.1:8188/path")):
            with self.assertRaises(web.HTTPForbidden):
                await self.setup.status(candidate)
        candidate = request()
        del candidate.headers["X-Ark-Canvas"]
        self.assertFalse(authorized_owner(candidate))
        self.assertTrue(authorized_owner(request(remote="::ffff:127.0.0.1")))

    async def test_strict_json_and_body_limit(self):
        for body in (b"[]", b'{"a":1,"a":2}', b'{"n":NaN}', b'not json'):
            with self.assertRaises(web.HTTPBadRequest):
                await owner_body(request(body))
        with self.assertRaises(web.HTTPRequestEntityTooLarge):
            await owner_body(request(b" " * 65537))
        candidate = request()
        candidate.content_type = "text/plain"
        with self.assertRaises(web.HTTPUnsupportedMediaType):
            await owner_body(candidate)

    async def test_read_only_actions_reject_arbitrary_parameters(self):
        with self.assertRaises(web.HTTPBadRequest):
            await self.setup.connection_url(request(b'{"path":"outside"}'))
        self.installation.connection_url.assert_not_called()

    async def test_save_passes_observed_local_origin(self):
        self.installation.save.return_value = {"saved": True}
        response = await self.setup.save(request(b'{"public_url":"https://owner.example"}'))
        self.assertEqual(response.status, 200)
        self.installation.save.assert_called_once_with({"public_url": "https://owner.example"}, comfy_url="http://127.0.0.1:8188")

    async def test_install_is_explicit_background_job(self):
        self.installation.install_dependencies.return_value = {"message": "Dependencies ready"}
        response = await self.setup.install_dependencies(request())
        self.assertEqual(response.status, 202)
        await self.setup.install_task
        self.assertEqual(self.setup.install_status["state"], "complete")
        self.installation.install_dependencies.assert_called_once_with()

    async def test_install_failure_is_visible_without_machine_error_details(self):
        self.installation.install_dependencies.side_effect = OSError("private-machine-token")
        await self.setup.install_dependencies(request())
        await self.setup.install_task
        self.assertEqual(self.setup.install_status["state"], "error")
        self.assertNotIn("private-machine-token", self.setup.install_status["message"])

    async def test_overlapping_mutations_are_rejected(self):
        async with self.setup.lock:
            result = await self.setup.save(request())
            self.assertEqual(result.status, 409)
            result = await self.setup.install_dependencies(request())
            self.assertEqual(result.status, 409)
        self.installation.save.assert_not_called()

    async def test_critical_package_approval_is_owner_only_and_has_fixed_shape(self):
        self.installation.approve_package.return_value = {"approved": True}
        body = json.dumps({"plan_id": "a" * 32}).encode()
        with self.assertRaises(web.HTTPForbidden):
            await self.setup.approve_package(request(body, origin="https://remote-ai.example"))
        self.installation.approve_package.assert_not_called()
        result = await self.setup.approve_package(request(body))
        self.assertEqual(result.status, 200)
        self.installation.approve_package.assert_called_once_with("a" * 32)
        result = await self.setup.approve_package(request(b'{"plan_id":"x","protected_changes":[]}'))
        self.assertEqual(result.status, 400)
