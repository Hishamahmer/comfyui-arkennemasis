import base64
import tempfile
import unittest
import uuid
from functools import wraps
from dataclasses import replace
from pathlib import Path

import httpx

from mcp_service.server import create_http_app
from mcp_service.settings import Settings


def running_server(test):
    @wraps(test)
    async def run(self):
        async with self.app.router.lifespan_context(self.app):
            await test(self)
    return run


class FakeComfy:
    def __init__(self):
        self.calls = []

    async def close(self):
        pass

    async def status(self):
        self.calls.append("status")
        return {"connected": True, "queue": {"queue_running": [], "queue_pending": []}}

    async def queue(self, prompt, workflow, request_id):
        self.calls.append("queue")
        return {"prompt_id": request_id}

    async def outputs(self, prompt_id):
        return {"assets": [{"filename": "test.png", "subfolder": "", "type": "output", "mime_type": "image/png"}]}

    async def image(self, filename, subfolder, kind):
        return b"sample-image-bytes", "image/png"


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.backend = FakeComfy()
        self.settings = Settings(local_token="a" * 48, bridge_token="b" * 48,
                                 workflow_root=str(Path(self.temp.name) / "workflows"), state_dir=self.temp.name)
        self.app = create_http_app(self.settings, client=self.backend)
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                      base_url="http://127.0.0.1:8190",
                                      headers={"Authorization": "Bearer " + self.settings.local_token,
                                               "Accept": "application/json, text/event-stream",
                                               "MCP-Protocol-Version": "2025-11-25"})

    async def asyncTearDown(self):
        await self.http.aclose()
        self.temp.cleanup()

    async def rpc(self, method, params=None):
        response = await self.http.post("/mcp", json={"jsonrpc": "2.0", "id": 1,
                                                     "method": method, "params": params or {}})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @running_server
    async def test_protocol_discovery_auth_and_host_boundary(self):
        response = await self.http.post("/mcp", headers={"Authorization": ""}, json={})
        self.assertEqual(response.status_code, 401)
        response = await self.http.post("/mcp", headers={"Host": "untrusted.example"}, json={})
        self.assertEqual(response.status_code, 421)
        self.assertEqual(self.backend.calls, [])
        initialized = await self.rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                     "clientInfo": {"name": "test", "version": "1"}})
        self.assertEqual(initialized["result"]["serverInfo"]["name"], "Arkennemasis MCP")
        listing = await self.rpc("tools/list")
        tools = {t["name"]: t for t in listing["result"]["tools"]}
        self.assertIn("queue_prompt", tools)
        self.assertIn("patch_canvas", tools)
        self.assertTrue(tools["server_status"]["annotations"]["readOnlyHint"])
        self.assertFalse(tools["queue_prompt"]["annotations"]["readOnlyHint"])
        status = await self.rpc("tools/call", {"name": "server_status", "arguments": {}})
        self.assertFalse(status["result"].get("isError", False))
        self.assertEqual(self.backend.calls, ["status"])

    @running_server
    async def test_write_conflicts_are_reported_as_tool_errors(self):
        workflow = {"1": {"class_type": "EmptyImage", "inputs": {"width": 64}}}
        first = await self.rpc("tools/call", {"name": "save_workflow", "arguments": {"name": "scratch.json", "workflow": workflow}})
        self.assertFalse(first["result"].get("isError", False))
        second = await self.rpc("tools/call", {"name": "save_workflow", "arguments": {"name": "scratch.json", "workflow": workflow}})
        self.assertTrue(second["result"]["isError"])

    @running_server
    async def test_new_capabilities_are_explicitly_disabled_until_owner_enables(self):
        for name, arguments in (("list_node_files", {}), ("get_launch_settings", {}),
                                ("plan_python_packages", {"packages": ["example==1.0"], "request_id": str(uuid.uuid4())})):
            result = await self.rpc("tools/call", {"name": name, "arguments": arguments})
            self.assertTrue(result["result"]["isError"])
            self.assertIn("disabled", result["result"]["content"][0]["text"])
        self.assertFalse((Path(self.temp.name) / "operations.sqlite3-journal").exists())

    @running_server
    async def test_queue_retries_do_not_generate_twice(self):
        arguments = {"request_id": str(uuid.uuid4()), "prompt": {"1": {"class_type": "EmptyImage", "inputs": {}}}}
        for _ in range(2):
            result = await self.rpc("tools/call", {"name": "queue_prompt", "arguments": arguments})
            self.assertFalse(result["result"].get("isError", False))
        self.assertEqual(self.backend.calls, ["queue"])

    @running_server
    async def test_images_are_mcp_content_not_local_paths(self):
        result = await self.rpc("tools/call", {"name": "get_output_image", "arguments": {"prompt_id": str(uuid.uuid4())}})
        item = result["result"]["content"][0]
        self.assertEqual(item["type"], "image")
        self.assertEqual(item["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(item["data"]), b"sample-image-bytes")

    @running_server
    async def test_private_link_needs_exact_secret_but_no_authorization_header(self):
        settings = replace(self.settings, auth_mode="connection_link", connection_token="c" * 64)
        app = create_http_app(settings, client=self.backend)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://127.0.0.1:8190",
                                         headers={"Accept": "application/json, text/event-stream",
                                                  "MCP-Protocol-Version": "2025-11-25"}) as client:
                payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "server_status", "arguments": {}}}
                for path in ("/mcp", "/connect/wrong/mcp", "/", "/connect/" + "d" * 64 + "/mcp"):
                    response = await client.post(path, json=payload)
                    self.assertEqual(response.status_code, 404)
                self.assertEqual(self.backend.calls, [])
                response = await client.post(settings.endpoint, json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertFalse(response.json()["result"].get("isError", False))
                self.assertEqual(self.backend.calls, ["status"])
                self.assertNotIn(settings.connection_token, str(response.headers))
                response = await client.post("/mcp", json=payload,
                                             headers={"Authorization": "Bearer " + self.settings.local_token})
                self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
