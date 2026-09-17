import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

from mcp_service.canvas_edits import CanvasEdits


class CanvasEditTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "edits.sqlite3"
        self.edits = CanvasEdits(self.path)
        self.client = AsyncMock()
        self.request = str(uuid.uuid4())
        self.ops = [{"op": "replace", "path": "/nodes/0/title", "value": "updated"}]
        self.snapshot = {"revision": "before", "workflow": {"nodes": [{"id": 1, "title": "original"}], "links": []}}

    async def asyncTearDown(self):
        self.edits.close()
        self.temp.cleanup()

    async def test_lost_response_retry_reuses_prepared_graph_without_rereading(self):
        self.client.bridge.side_effect = [self.snapshot, TimeoutError(), {"revision": "after", "applied": True}]
        with self.assertRaises(TimeoutError):
            await self.edits.apply(self.client, "tab", self.request, "patch", "before", operations=self.ops)
        result = await self.edits.apply(self.client, "tab", self.request, "patch", "before", operations=self.ops)
        self.assertTrue(result["applied"])
        calls = self.client.bridge.call_args_list
        self.assertEqual(calls[1], calls[2])
        self.assertEqual(calls[2].args[1]["workflow"]["nodes"][0]["title"], "updated")

    async def test_confirmed_result_survives_gateway_restart(self):
        self.client.bridge.return_value = {"revision": "after"}
        await self.edits.apply(self.client, "tab", self.request, "undo", "before")
        self.edits.close()
        self.edits = CanvasEdits(self.path)
        self.client.bridge.reset_mock()
        result = await self.edits.apply(self.client, "tab", self.request, "undo", "before")
        self.assertTrue(result["reused_request"])
        self.client.bridge.assert_not_called()

    async def test_reused_id_cannot_change_target_or_edit(self):
        self.client.bridge.return_value = {"revision": "after"}
        await self.edits.apply(self.client, "tab", self.request, "undo", "before")
        with self.assertRaises(ValueError):
            await self.edits.apply(self.client, "another-tab", self.request, "undo", "before")
        self.assertEqual(self.client.bridge.call_count, 1)

    async def test_stale_revision_never_sends_mutation(self):
        self.client.bridge.return_value = self.snapshot
        with self.assertRaises(ValueError):
            await self.edits.apply(self.client, "tab", self.request, "patch", "outdated", operations=self.ops)
        self.assertEqual(self.client.bridge.call_count, 1)

    async def test_canvas_run_snapshot_survives_disconnect_and_rejects_retarget(self):
        self.client.bridge.side_effect = [{**self.snapshot, "prompt": {"1": {"class_type": "EmptyImage", "inputs": {}}}},
                                         {"sessions": [{"session_id": "tab", "client_id": "browser", "state": "connected"}]}]
        prepared = await self.edits.prepare_run(self.client, "tab", "before", self.request)
        self.assertEqual(prepared["client_id"], "browser")
        self.edits.close()
        self.edits = CanvasEdits(self.path)
        self.client.bridge.reset_mock()
        reused = await self.edits.prepare_run(self.client, "tab", "before", self.request)
        self.assertEqual(reused, prepared)
        self.client.bridge.assert_not_called()
        with self.assertRaises(ValueError):
            await self.edits.prepare_run(self.client, "other", "before", self.request)

    async def test_canvas_run_requires_current_revision_and_shared_tab(self):
        self.client.bridge.return_value = self.snapshot
        with self.assertRaises(ValueError):
            await self.edits.prepare_run(self.client, "tab", "stale", self.request)
        self.client.bridge.side_effect = [self.snapshot, {"sessions": []}]
        with self.assertRaises(ValueError):
            await self.edits.prepare_run(self.client, "tab", "before", self.request)


if __name__ == "__main__":
    unittest.main()
