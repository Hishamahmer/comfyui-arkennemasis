import asyncio
import tempfile
import unittest
import uuid
from pathlib import Path

from mcp_service.execution import JobLedger


class FakeClient:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail
        self.visible = False

    async def queue(self, prompt, workflow, request_id):
        self.calls += 1
        await asyncio.sleep(0)
        if self.fail:
            raise TimeoutError("lost response")
        return {"prompt_id": request_id}

    async def job(self, request_id):
        return {"prompt_id": request_id, "status": "running" if self.visible else "unknown"}


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_retries_and_restart_submit_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.db"
            ledger = JobLedger(path)
            client = FakeClient()
            request_id = str(uuid.uuid4())
            prompt = {"1": {"class_type": "EmptyImage", "inputs": {}}}
            results = await asyncio.gather(*(ledger.submit(client, request_id, prompt) for _ in range(4)))
            self.assertEqual(client.calls, 1)
            self.assertTrue(all(r["prompt_id"] == request_id for r in results))
            ledger.close()
            ledger = JobLedger(path)
            self.assertTrue((await ledger.submit(client, request_id, prompt))["reused_request"])
            self.assertEqual(client.calls, 1)
            with self.assertRaises(ValueError):
                await ledger.submit(client, request_id, {"different": True})
            ledger.close()

    async def test_ambiguous_timeout_never_resubmits_and_can_recover(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = JobLedger(Path(directory) / "jobs.db")
            client = FakeClient(fail=True)
            request_id = str(uuid.uuid4())
            with self.assertRaises(TimeoutError):
                await ledger.submit(client, request_id, {})
            result = await ledger.submit(client, request_id, {})
            self.assertFalse(result["resubmitted"])
            client.visible = True
            self.assertTrue((await ledger.submit(client, request_id, {}))["recovered"])
            self.assertEqual(client.calls, 1)
            ledger.close()


if __name__ == "__main__":
    unittest.main()
