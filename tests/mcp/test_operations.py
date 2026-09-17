import tempfile
import threading
import unittest
import uuid

from mcp_service.operations import Operations


class OperationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.operations = Operations(self.temp.name)
        self.request = str(uuid.uuid4())

    def tearDown(self):
        self.operations.close()
        self.temp.cleanup()

    def test_retry_returns_result_without_rerunning(self):
        calls = []
        run = lambda: calls.append(1) or {"ok": True}
        self.operations.submit("test", "comfy:maintain", self.request, {}, run)
        self.operations.futures[self.request].result(timeout=3)
        result = self.operations.submit("test", "comfy:maintain", self.request, {}, run)
        self.assertEqual(result["state"], "completed")
        self.assertEqual(calls, [1])
        for scope, arguments in (("comfy:read", {}), ("comfy:maintain", {"different": True})):
            with self.assertRaises(ValueError):
                self.operations.submit("test", scope, self.request, arguments, run)

    def test_serialization_error_becomes_failed(self):
        self.operations.submit("test", "comfy:read", self.request, {}, lambda: {"bad": float("nan")})
        self.operations.futures[self.request].result(timeout=3)
        self.assertEqual(self.operations.get(self.request)["state"], "failed")

    def test_interrupted_work_is_not_replayed(self):
        ready = threading.Event()
        try:
            self.operations.submit("test", "comfy:maintain", self.request, {}, lambda: ready.wait(3))
            observer = Operations(self.temp.name)
            try:
                result = observer.submit("test", "comfy:maintain", self.request, {}, lambda: self.fail("replayed"))
                self.assertEqual(result["state"], "unconfirmed")
                self.assertEqual(result["required_scope"], "comfy:maintain")
            finally:
                observer.close()
        finally:
            ready.set()


if __name__ == "__main__":
    unittest.main()
