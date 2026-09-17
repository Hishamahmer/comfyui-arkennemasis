import asyncio
import json
import unittest
import uuid

import httpx

from mcp_service.assets import media_descriptor, output_assets
from mcp_service.client import ComfyClient, ComfyError


PROMPT = {"1": {"class_type": "Example", "inputs": {"text": "hello"}}}


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.handler = lambda request: httpx.Response(200, json={})

        def dispatch(request):
            self.requests.append(request)
            return self.handler(request)

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(dispatch))
        self.client = ComfyClient("http://127.0.0.1:8188", "bridge-secret", client=self.http)

    async def asyncTearDown(self):
        await self.http.aclose()

    async def test_validate_uses_protected_bridge_without_queuing(self):
        self.handler = lambda request: httpx.Response(200, json={"valid": True})
        self.assertTrue((await self.client.validate(PROMPT))["valid"])
        self.assertEqual(self.requests[0].url.path, "/arkennemasis/mcp/validate")
        self.assertEqual(self.requests[0].headers["X-Ark-Bridge-Token"], "bridge-secret")
        self.assertEqual(json.loads(self.requests[0].content), {"prompt": PROMPT})

    async def test_concurrent_queue_retries_do_not_duplicate_and_use_supplied_id(self):
        request_id = str(uuid.uuid4())
        self.handler = lambda request: httpx.Response(200, json={"prompt_id": request_id})
        results = await asyncio.gather(*(self.client.queue(PROMPT, request_id=request_id) for _ in range(3)))
        self.assertTrue(all(r["prompt_id"] == request_id for r in results))
        self.assertEqual(len(self.requests), 1)
        submitted = json.loads(self.requests[0].content)
        self.assertEqual(submitted["prompt_id"], request_id)
        self.assertEqual(submitted["extra_data"]["arkennemasis_mcp"]["request_id"], request_id)
        with self.assertRaises(ValueError):
            await self.client.queue({"1": {"class_type": "Other", "inputs": {}}}, request_id=request_id)

    async def test_unknown_submission_result_is_not_automatically_replayed(self):
        request_id = str(uuid.uuid4())

        def timeout(request):
            raise httpx.ReadTimeout("lost response", request=request)

        self.handler = timeout
        with self.assertRaises(httpx.ReadTimeout):
            await self.client.queue(PROMPT, request_id=request_id)
        with self.assertRaises(ComfyError):
            await self.client.queue(PROMPT, request_id=request_id)
        self.assertEqual(len(self.requests), 1)

    async def test_cancel_never_interrupts_running_job(self):
        prompt_id = str(uuid.uuid4())
        self.handler = lambda request: httpx.Response(200, json={"queue_running": [[0, prompt_id]], "queue_pending": []})
        result = await self.client.cancel(prompt_id)
        self.assertFalse(result["cancelled"])
        self.assertEqual([r.method for r in self.requests], ["GET"])

    async def test_cancel_refuses_another_clients_pending_job(self):
        prompt_id = str(uuid.uuid4())
        self.handler = lambda request: httpx.Response(200, json={"queue_running": [], "queue_pending": [[0, prompt_id, {}, {}]]})
        with self.assertRaises(ComfyError):
            await self.client.cancel(prompt_id)
        self.assertEqual(len(self.requests), 1)

    async def test_cancel_deletes_only_exact_owned_pending_job(self):
        prompt_id = str(uuid.uuid4())
        pending = [[0, prompt_id, {}, {"arkennemasis_mcp": {"request_id": prompt_id}}]]

        def respond(request):
            if request.method == "POST":
                self.assertEqual(request.url.path, "/queue")
                self.assertEqual(json.loads(request.content), {"delete": [prompt_id]})
                pending.clear()
                return httpx.Response(200)
            return httpx.Response(200, json={"queue_pending": pending, "queue_running": []} if request.url.path == "/queue" else {})

        self.handler = respond
        self.assertTrue((await self.client.cancel(prompt_id))["cancelled"])
        self.assertNotIn("/interrupt", [r.url.path for r in self.requests])

    async def test_image_stream_and_upload_use_media_only_routes(self):
        self.handler = lambda request: httpx.Response(200, content=b"image-bytes", headers={"content-type": "image/png"})
        self.assertEqual(await self.client.image("a b.png", "folder"), (b"image-bytes", "image/png"))
        self.assertEqual(self.requests[0].url.params["filename"], "a b.png")
        self.handler = lambda request: httpx.Response(200, json={"name": "reference.png", "subfolder": "", "type": "input"})
        uploaded = await self.client.upload_image("reference.png", b"png-bytes")
        self.assertEqual(uploaded["type"], "input")
        self.assertEqual(self.requests[-1].url.path, "/upload/image")

    async def test_image_rejects_redirects_and_non_images(self):
        for status, headers in ((302, {"location": "https://example.com/"}), (200, {"content-type": "text/plain"})):
            self.handler = lambda request: httpx.Response(status, headers=headers, content=b"not-image")
            with self.assertRaises(ComfyError):
                await self.client.image("result.png")

    async def test_status_hides_process_arguments_and_counts_queue(self):
        self.handler = lambda request: httpx.Response(200, json={"system": {"comfyui_version": "test", "argv": ["secret"]}, "devices": []} if request.url.path == "/system_stats" else {"queue_running": [[0, "id"]], "queue_pending": []})
        status = await self.client.status()
        self.assertNotIn("argv", status["system"])
        self.assertEqual(status["queue_running"], 1)

    async def test_non_loopback_and_unsafe_assets_rejected_before_request(self):
        for url in ("https://example.com", "http://127.0.0.1:8188/path", "http://user:secret@127.0.0.1"):
            with self.assertRaises(ValueError):
                ComfyClient(url)
        for filename, folder in (("../secret.png", ""), ("secret.txt", ""), ("image.png", "../outside"), ("x.png:stream", ""), ("x.png", "/absolute")):
            with self.assertRaises(ValueError):
                await self.client.image(filename, folder)
        self.assertEqual(self.requests, [])

    async def test_history_assets_are_deduplicated_and_confined(self):
        assets = output_assets({"outputs": {"1": {"images": [{"filename": "output.png"}, {"filename": "../escape.png"}]}, "2": {"images": [{"filename": "output.png"}]}}})
        self.assertEqual(len(assets), 1)
        self.assertEqual(assets[0]["node_id"], "1")


if __name__ == "__main__":
    unittest.main()
