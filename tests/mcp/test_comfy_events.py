import base64
from io import BytesIO
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from PIL import Image

from mcp_service.comfy_events import attach, ExecutionEvents, MAX_PREVIEW_BYTES


class ExecutionEventTests(unittest.TestCase):
    def setUp(self):
        self.events = ExecutionEvents()
        self.events.capture("execution_start", {"prompt_id": "job-1"}, "browser-1")

    def test_only_safe_fields_are_retained_and_snapshots_are_copies(self):
        self.events.capture("execution_error", {"prompt_id": "job-1", "node_id": "12:4",
                            "current_inputs": {"password": "secret"}, "traceback": ["private/path"],
                            "exception_message": "secret", "output": "private/path"}, "browser-1")
        snapshot = self.events.snapshot()
        self.assertEqual(snapshot["events"][-1]["data"], {"prompt_id": "job-1", "node_id": "12:4"})
        self.assertEqual(snapshot["current"]["state"], "error")
        snapshot["events"][-1]["data"]["node_id"] = "changed"
        self.assertEqual(self.events.snapshot()["current"]["node_id"], "12:4")
        self.events.capture("executing", {"prompt_id": "job-1", "node": None}, "browser-1")
        self.assertEqual(self.events.snapshot()["current"]["state"], "error")

    def test_event_history_and_node_state_are_bounded(self):
        for index in range(80):
            self.events.capture("progress", {"prompt_id": "job-1", "node": "4", "value": index, "max": 100}, "browser-1")
        self.events.capture("progress_state", {"prompt_id": "job-1", "nodes": {
            str(index): {"value": index, "max": 100, "state": "running", "password": "secret"} for index in range(100)
        }}, "browser-1")
        snapshot = self.events.snapshot()
        self.assertEqual(len(snapshot["events"]), 64)
        self.assertEqual(len(snapshot["events"][-1]["data"]["nodes"]), 64)
        self.assertTrue(snapshot["events"][-1]["data"]["nodes_truncated"])
        self.assertNotIn("secret", json.dumps(snapshot))

    def test_unrelated_events_and_invalid_progress_values_are_not_exposed(self):
        self.events.capture("custom_secret_event", {"token": "secret"})
        self.events.capture("progress", {"prompt_id": "job-1", "value": float("nan"), "max": float("inf"), "node": "../../private"})
        data = self.events.snapshot()["events"][-1]["data"]
        self.assertEqual(data, {"prompt_id": "job-1"})
        self.assertEqual(len(self.events.snapshot()["events"]), 2)

    def test_status_exposes_only_queue_count(self):
        self.events.capture("status", {"status": {"exec_info": {"queue_remaining": 3}, "secret": "private"}, "sid": "private"})
        self.assertEqual(self.events.snapshot()["events"][-1]["data"], {"queue_remaining": 3})

    def test_tuple_preview_is_small_reencoded_and_job_scoped(self):
        source = Image.new("RGBA", (1200, 600), "red")
        source.info["private"] = "secret"
        self.events.capture(2, ("PNG", source, None), "browser-1")
        preview = self.events.preview("job-1")
        self.assertEqual((preview["width"], preview["height"]), (512, 256))
        raw = base64.b64decode(preview["data"])
        self.assertLessEqual(len(raw), MAX_PREVIEW_BYTES)
        self.assertEqual(Image.open(BytesIO(raw)).format, "JPEG")
        self.assertNotIn(b"secret", raw)
        self.assertIsNone(self.events.preview("job-other"))
        self.assertEqual(source.size, (1200, 600))
        self.events.capture("execution_start", {"prompt_id": "job-2"}, "browser-1")
        self.assertIsNone(self.events.preview("job-1"))

    def test_previews_from_other_clients_and_metadata_jobs_are_ignored(self):
        image = ("PNG", Image.new("RGB", (64, 64)), None)
        self.events.capture(2, image, "other-browser")
        self.events.capture(4, (image, {"prompt_id": "job-other"}), "browser-1")
        self.assertIsNone(self.events.preview("job-1"))
        self.events.capture(4, (image, {"prompt_id": "job-1", "node_id": "3", "secret": "private"}), "browser-1")
        self.assertEqual(self.events.preview("job-1")["node_id"], "3")
        self.assertNotIn("secret", self.events.preview("job-1"))

    def test_encoded_binary_formats_are_supported_without_metadata_leakage(self):
        output = BytesIO()
        Image.new("RGB", (32, 32)).save(output, "PNG")
        self.events.capture(1, struct.pack(">I", 2) + output.getvalue(), "browser-1")
        self.assertIsNotNone(self.events.preview("job-1"))
        self.events.last_preview_at = -float("inf")
        metadata = json.dumps({"prompt_id": "job-1", "node_id": "7", "private": "secret"}).encode()
        self.events.capture(4, struct.pack(">I", len(metadata)) + metadata + output.getvalue(), "browser-1")
        self.assertEqual(self.events.preview("job-1")["node_id"], "7")

    def test_preview_capture_is_throttled_and_terminal_jobs_ignore_new_previews(self):
        image = ("PNG", Image.new("RGB", (64, 64)), None)
        with patch("mcp_service.comfy_events.time.monotonic", return_value=10):
            self.events.capture(2, image, "browser-1")
            first = self.events.preview("job-1")
            self.events.capture(4, (image, {"prompt_id": "job-1", "node_id": "new"}), "browser-1")
            self.assertEqual(self.events.preview("job-1"), first)
        self.events.capture("execution_success", {"prompt_id": "job-1"}, "browser-1")
        self.events.last_preview_at = -float("inf")
        self.events.capture(4, (image, {"prompt_id": "job-1", "node_id": "new"}), "browser-1")
        self.assertEqual(self.events.preview("job-1"), first)


class SendContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_attachment_preserves_send_args_result_and_does_not_wrap_twice(self):
        original = AsyncMock(return_value=object())
        server = SimpleNamespace(send=original)
        observer = attach(server)
        wrapped = server.send
        data = {"prompt_id": "job"}
        result = await server.send("execution_start", data, sid="browser")
        original.assert_awaited_once_with("execution_start", data, "browser")
        self.assertIs(result, original.return_value)
        self.assertIs(attach(server), observer)
        self.assertIs(server.send, wrapped)

    async def test_original_errors_propagate_and_capture_failure_does_not_break_send(self):
        server = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("original failure")))
        observer = attach(server)
        with self.assertRaisesRegex(RuntimeError, "original failure"):
            await server.send("execution_start", {"prompt_id": "job"})
        self.assertEqual(observer.snapshot()["events"], [])
        server = SimpleNamespace(send=AsyncMock(return_value="sent"))
        observer = attach(server)
        with patch.object(observer, "capture", side_effect=ValueError("bad optional preview")):
            self.assertEqual(await server.send(4, b"malformed"), "sent")


if __name__ == "__main__":
    unittest.main()
