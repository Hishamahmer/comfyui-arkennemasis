import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
import uuid


SPEC = importlib.util.spec_from_file_location("ark_test_comfy_bridge", Path(__file__).resolve().parents[2] / "mcp_service" / "comfy_bridge.py")
bridge_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge_module)


class Content:
    def __init__(self, body):
        self.data = json.dumps(body).encode()

    async def iter_chunked(self, size):
        yield self.data


def request(body=None, browser=False, remote="127.0.0.1", token="test-private-token", origin="http://127.0.0.1:8188"):
    headers = {"Origin": origin, "X-Ark-Canvas": "1"} if browser else {"X-Ark-Bridge-Token": token}
    return SimpleNamespace(remote=remote, scheme="http", host="127.0.0.1:8188", headers=headers, content_type="application/json", content=Content(body or {}))


def result(response):
    return json.loads(response.text)


class CanvasBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = SimpleNamespace(sockets={"tab-one": object(), "tab-two": object()}, send=AsyncMock(),
                                      prompt_queue=SimpleNamespace(get_current_queue_volatile=Mock(return_value=([], []))))
        self.validator = AsyncMock(return_value=(True, None, ["2"], {}))
        self.bridge = bridge_module.CanvasBridge(self.server, self.validator, lambda: "test-private-token", timeout=1)

    async def register(self, client_id="tab-one"):
        response = await self.bridge.register(request({"client_id": client_id, "title": "My workflow"}, browser=True))
        self.assertEqual(response.status, 200)
        return result(response)

    async def start_canvas(self, session, command="read", **kwargs):
        task = asyncio.create_task(self.bridge.canvas(request({"session_id": session["session_id"], "command": command, **kwargs})))
        await asyncio.sleep(0)
        return task

    async def ack(self, session, **overrides):
        event = self.server.send.call_args.args[1]
        payload = {**session, "request_id": event["request_id"], "result": {"workflow": {"nodes": []}, "prompt": {}, "revision": "abc"}, **overrides}
        return await self.bridge.acknowledge(request(payload, browser=True))

    async def test_control_requires_loopback_and_private_token(self):
        for candidate in (request(token=""), request(remote="192.168.1.8"), request(remote="not-an-ip")):
            self.assertEqual((await self.bridge.list_sessions(candidate)).status, 403)
        self.assertEqual((await self.bridge.list_sessions(request())).status, 200)

    async def test_disabled_bridge_does_not_register_browser(self):
        self.bridge.token_provider = lambda: ""
        self.assertEqual((await self.bridge.register(request({"client_id": "tab-one"}, browser=True))).status, 503)
        self.assertFalse(self.bridge.sessions)

    async def test_registration_rejects_cross_origin_and_remote_browser(self):
        for candidate in (request(browser=True, origin="https://attacker.example"), request(browser=True, origin="null"), request(browser=True, remote="192.168.1.8")):
            self.assertEqual((await self.bridge.register(candidate)).status, 403)
        candidate = request(browser=True)
        del candidate.headers["X-Ark-Canvas"]
        self.assertEqual((await self.bridge.register(candidate)).status, 403)

    async def test_dns_rebinding_host_rejected(self):
        candidate = request(browser=True, origin="http://attacker.example:8188")
        candidate.host = "attacker.example:8188"
        self.assertEqual((await self.bridge.register(candidate)).status, 403)

    async def test_connection_status_requires_private_same_origin_browser(self):
        for candidate in (request(), request(browser=True, origin="https://attacker.example"),
                          request(browser=True, remote="192.168.1.8"), request(browser=True, origin="null")):
            self.assertEqual((await self.bridge.connection_status(candidate)).status, 403)
        candidate = request(browser=True)
        del candidate.headers["X-Ark-Canvas"]
        self.assertEqual((await self.bridge.connection_status(candidate)).status, 403)
        candidate = request(browser=True, origin="http://attacker.example:8188")
        candidate.host = "attacker.example:8188"
        self.assertEqual((await self.bridge.connection_status(candidate)).status, 403)
        self.bridge.token_provider = lambda: ""
        self.assertEqual((await self.bridge.connection_status(request(browser=True))).status, 503)

    async def test_connection_status_missing_or_invalid_file_is_offline(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge_module, "CONFIG_PATH", Path(directory) / "config.json"):
            status_file = Path(directory) / "session-status.json"
            for content in (None, "{unfinished", "[]", '{"state":"unknown"}'):
                if content is not None:
                    status_file.write_text(content, encoding="utf-8")
                response = result(await self.bridge.connection_status(request(browser=True)))
                self.assertEqual(response["state"], "stopped")
                self.assertIsNone(response["updated_at"])
        self.server.send.assert_not_awaited()

    async def test_connection_status_expires_heartbeat_and_never_returns_extra_fields(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge_module, "CONFIG_PATH", Path(directory) / "config.json"):
            status_file = Path(directory) / "session-status.json"
            now = time.time()
            record = {"state": "online", "message": "Ready for your AI connector.", "updated_at": now,
                      "connection_token": "secret-not-for-browser", "path": "private-file"}
            status_file.write_text(json.dumps(record), encoding="utf-8")
            response = result(await self.bridge.connection_status(request({"path": "other-file"}, browser=True)))
            self.assertEqual(response, {key: record[key] for key in ("state", "message", "updated_at")})
            self.assertNotIn("secret-not-for-browser", json.dumps(response))
            for state in ("waiting", "starting", "online"):
                for updated_at in (now - 16, None, "yesterday", True, float("nan")):
                    status_file.write_text(json.dumps({**record, "state": state, "updated_at": updated_at}), encoding="utf-8")
                    self.assertEqual(result(await self.bridge.connection_status(request(browser=True)))["state"], "stopped")
            status_file.write_text(json.dumps({**record, "state": "error", "updated_at": now - 60}), encoding="utf-8")
            self.assertEqual(result(await self.bridge.connection_status(request(browser=True)))["state"], "error")

    async def test_connection_status_exposes_only_safe_layer_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge_module, "CONFIG_PATH", Path(directory) / "config.json"):
            now = time.time()
            record = {"state": "reconnecting", "message": "Restoring the public connection.", "updated_at": now,
                      "last_success_at": now - 10, "layers": {"gateway": "online", "tunnel": "reconnecting", "backend": "online", "token": "secret", "other": "online"}}
            (Path(directory) / "session-status.json").write_text(json.dumps(record), encoding="utf-8")
            status = result(await self.bridge.connection_status(request(browser=True)))
            self.assertEqual(status["state"], "reconnecting")
            self.assertEqual(status["layers"], {"gateway": "online", "tunnel": "reconnecting", "backend": "online"})
            self.assertEqual(status["last_success_at"], now - 10)
            self.assertNotIn("secret", json.dumps(status))

    async def test_sessions_never_return_secrets(self):
        session = await self.register()
        listing = result(await self.bridge.list_sessions(request()))
        self.assertEqual(listing["sessions"][0]["session_id"], session["session_id"])
        self.assertNotIn(session["session_secret"], json.dumps(listing))
        self.assertNotIn("test-private-token", json.dumps(session))

    async def test_read_waits_for_targeted_authenticated_ack(self):
        session = await self.register()
        task = await self.start_canvas(session)
        self.assertFalse(task.done())
        self.assertEqual(self.server.send.call_args.args[2], "tab-one")
        denied = await self.ack(session, session_secret="wrong-secret")
        self.assertEqual(denied.status, 403)
        self.assertFalse(task.done())
        self.assertEqual((await self.ack(session)).status, 200)
        response = await task
        self.assertEqual(response.status, 200)
        self.assertEqual(result(response)["revision"], "abc")

    async def test_request_ids_deduplicate_and_reject_changed_operation(self):
        session = await self.register()
        first = await self.start_canvas(session, request_id="stable-id")
        second = await self.start_canvas(session, request_id="stable-id")
        self.assertEqual(self.server.send.await_count, 1)
        await self.ack(session)
        self.assertEqual(result(await first), result(await second))
        changed = await self.bridge.canvas(request({"session_id": session["session_id"], "command": "undo", "expected_revision": "abc", "request_id": "stable-id"}))
        self.assertEqual(changed.status, 409)
        self.assertEqual(result(changed)["error"]["code"], "request_id_reused")

    async def test_request_dedup_is_scoped_to_session(self):
        first_session, second_session = await self.register(), await self.register("tab-two")
        first = await self.start_canvas(first_session, request_id="same-id")
        await self.ack(first_session)
        second = await self.start_canvas(second_session, request_id="same-id")
        await self.ack(second_session)
        self.assertEqual((await first).status, 200)
        self.assertEqual((await second).status, 200)
        self.assertEqual(self.server.send.await_count, 2)

    async def test_apply_requires_revision_without_sending_to_browser(self):
        session = await self.register()
        response = await self.bridge.canvas(request({"session_id": session["session_id"], "command": "apply", "workflow": {"nodes": []}}))
        self.assertEqual(result(response)["error"]["code"], "revision_required")
        self.server.send.assert_not_awaited()

    async def test_timeout_never_reports_success_or_resends_same_edit(self):
        session = await self.register()
        body = {"session_id": session["session_id"], "command": "apply", "workflow": {"nodes": []}, "expected_revision": "abc", "request_id": "timeout-id"}
        first = await self.bridge.canvas(request(body))
        second = await self.bridge.canvas(request(body))
        self.assertEqual(first.status, 504)
        self.assertEqual(result(first), result(second))
        self.assertIn("may have applied", result(first)["error"]["message"])
        self.assertEqual(self.server.send.await_count, 1)

    async def test_disconnect_cancels_pending_operation(self):
        session = await self.register()
        task = await self.start_canvas(session)
        await self.bridge.disconnect(request(session, browser=True))
        self.assertEqual(result(await task)["error"]["code"], "session_closed")
        self.assertFalse(self.bridge.sessions)

    async def test_throttled_background_tab_survives_while_websocket_is_open(self):
        session = await self.register()
        self.bridge.sessions[session["session_id"]]["last_seen"] = 0
        listing = result(await self.bridge.list_sessions(request()))["sessions"]
        self.assertEqual(listing[0]["session_id"], session["session_id"])
        self.assertEqual(listing[0]["state"], "connected")
        task = await self.start_canvas(session)
        await self.ack(session)
        self.assertEqual((await task).status, 200)

    async def test_disconnected_session_has_reconnect_grace_then_expires(self):
        session = await self.register()
        self.server.sockets.clear()
        with patch.object(bridge_module.time, "time", return_value=1000):
            listing = result(await self.bridge.list_sessions(request()))["sessions"]
            self.assertEqual(listing[0]["state"], "reconnecting")
        with patch.object(bridge_module.time, "time", return_value=1000 + bridge_module.SESSION_TTL + 1):
            self.assertEqual(result(await self.bridge.list_sessions(request()))["sessions"], [])

    async def test_authenticated_resume_preserves_session_and_pending_request(self):
        session = await self.register()
        task = await self.start_canvas(session, request_id="reconnect-read")
        del self.server.sockets["tab-one"]
        self.bridge.prune()
        self.server.sockets["resumed-tab"] = object()
        denied = await self.bridge.heartbeat(request({**session, "client_id": "resumed-tab", "session_secret": "wrong"}, browser=True))
        self.assertEqual(denied.status, 403)
        response = await self.bridge.heartbeat(request({**session, "client_id": "resumed-tab"}, browser=True))
        self.assertEqual(response.status, 200)
        self.assertEqual(result(response)["commands"][0]["request_id"], "reconnect-read")
        self.assertEqual(self.bridge.sessions[session["session_id"]]["client_id"], "resumed-tab")
        await self.ack(session)
        self.assertEqual((await task).status, 200)

    async def test_resume_cannot_take_over_another_shared_browser(self):
        first = await self.register()
        second = await self.register("tab-two")
        response = await self.bridge.heartbeat(request({**first, "client_id": "tab-two"}, browser=True))
        self.assertEqual(result(response)["error"]["code"], "session_conflict")
        self.assertIn(second["session_id"], self.bridge.sessions)

    async def test_disconnected_canvas_reports_reconnecting_without_sending_edit(self):
        session = await self.register()
        del self.server.sockets["tab-one"]
        response = await self.bridge.canvas(request({"session_id": session["session_id"], "command": "read"}))
        self.assertEqual(result(response)["error"]["code"], "canvas_reconnecting")
        self.server.send.assert_not_awaited()
        self.assertIn(session["session_id"], self.bridge.sessions)

    async def test_explicit_stop_cannot_be_resumed_with_the_old_secret(self):
        session = await self.register()
        await self.bridge.disconnect(request(session, browser=True))
        response = await self.bridge.heartbeat(request({**session, "client_id": "tab-one"}, browser=True))
        self.assertEqual(result(response)["error"]["code"], "unknown_session")
        self.assertEqual(result(await self.bridge.list_sessions(request()))["sessions"], [])

    async def test_late_and_duplicate_acknowledgments_recover_without_reapplying(self):
        session = await self.register()
        self.bridge.timeout = 0.01
        body = {"session_id": session["session_id"], "command": "apply", "workflow": {"nodes": []}, "expected_revision": "abc", "request_id": "late-ack"}
        self.assertEqual((await self.bridge.canvas(request(body))).status, 504)
        for _ in range(2):
            self.assertEqual((await self.ack(session)).status, 200)
        recovered = await self.bridge.canvas(request(body))
        self.assertEqual(recovered.status, 200)
        self.assertEqual(result(recovered)["request_id"], "late-ack")
        self.assertEqual(self.server.send.await_count, 1)
        conflict = await self.ack(session, result={"workflow": {"nodes": []}, "prompt": {}, "revision": "other"})
        self.assertEqual(result(conflict)["error"]["code"], "acknowledgment_conflict")

    async def test_expired_commands_are_not_redelivered_and_pending_records_are_pruned(self):
        session = await self.register()
        self.bridge.timeout = 0.01
        await self.bridge.canvas(request({"session_id": session["session_id"], "command": "read", "request_id": "expired-read"}))
        heartbeat = await self.bridge.heartbeat(request(session, browser=True))
        self.assertEqual(result(heartbeat)["commands"], [])
        record = self.bridge.requests[(session["session_id"], "expired-read")]
        record["expires"] = 0
        self.bridge.prune()
        self.assertTrue(record["future"].done())
        self.assertFalse(self.bridge.requests)

    async def test_unknown_canvas_and_malformed_session_fail_without_edit(self):
        response = await self.bridge.canvas(request({"session_id": "unknown", "command": "apply"}))
        self.assertEqual(result(response)["error"]["code"], "no_canvas")
        response = await self.bridge.heartbeat(request({"session_id": []}, browser=True))
        self.assertEqual(response.status, 403)
        self.server.send.assert_not_awaited()

    async def test_validate_uses_core_validator_without_queueing(self):
        prompt = {"2": {"class_type": "SaveImage", "inputs": {}}}
        response = await self.bridge.validate(request({"prompt": prompt}))
        self.assertEqual(result(response), {"valid": True, "error": None, "outputs_to_execute": ["2"], "node_errors": {}})
        self.assertEqual(self.validator.call_args.args[1:], (prompt, None))
        self.server.send.assert_not_awaited()

    async def test_runtime_is_authenticated_and_does_not_expose_arbitrary_args_or_environment(self):
        self.assertEqual((await self.bridge.runtime(request(browser=True))).status, 403)
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge_module, "CONFIG_PATH", Path(directory) / "config.json"), \
                patch.object(bridge_module.sys, "argv", ["main.py", "--lowvram", "--preview-method", "auto", "--fast", "fp16_accumulation", "--secret", "private", "--input-directory", "private-path"]):
            payload = result(await self.bridge.runtime(request()))
            self.assertTrue(payload["python_executable"])
            self.assertEqual(payload["pid"], bridge_module.os.getpid())
            self.assertEqual(payload["launch_settings"], {"lowvram": True, "preview-method": "auto", "fast": ["fp16_accumulation"]})
            self.assertNotIn("private", json.dumps(payload))

    async def test_restart_rejects_browser_caller_arbitrary_args_and_busy_queue(self):
        restart_id = str(uuid.uuid4())
        self.assertEqual((await self.bridge.restart(request({"request_id": restart_id}, browser=True))).status, 403)
        for body in ({"request_id": "invalid"}, {"request_id": restart_id, "argv": ["other.py"]}, {"request_id": restart_id, "pid": 1}):
            self.assertEqual((await self.bridge.restart(request(body))).status, 400)
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge_module, "CONFIG_PATH", Path(directory) / "config.json"):
            for queue in ((["running"], []), ([], ["pending"])):
                self.server.prompt_queue.get_current_queue_volatile.return_value = queue
                response = await self.bridge.restart(request({"request_id": restart_id}))
                self.assertEqual(result(response)["error"]["code"], "queue_not_empty")
            self.assertIsNone(self.bridge.restart_task)
            self.assertFalse((Path(directory) / "backend-restarts.json").exists())

    async def test_restart_deduplicates_across_bridge_instances_and_keeps_trusted_python_args(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bridge_module, "CONFIG_PATH", Path(directory) / "config.json"), \
                patch.object(bridge_module.asyncio, "sleep", new=AsyncMock()), patch.object(bridge_module.os, "execv") as replace, \
                patch.object(bridge_module.sys, "_arkennemasis_base_comfy_args", ["--fast", "fp16_accumulation"], create=True):
            body = {"request_id": str(uuid.uuid4())}
            response = await self.bridge.restart(request(body))
            self.assertEqual(response.status, 202)
            self.assertEqual(result(response)["state"], "scheduled")
            self.assertFalse(replace.called, "The HTTP acknowledgment must be prepared before replacing the process")
            await self.bridge.restart_task
            self.assertEqual(replace.call_count, 1)
            executable, argv = replace.call_args.args
            self.assertEqual(executable, bridge_module.sys.executable)
            self.assertEqual(argv[1], "-s")
            self.assertIn("launch_backend.py", argv[2])
            self.assertEqual(argv[3:], ["--fast", "fp16_accumulation"])
            restarted_bridge = bridge_module.CanvasBridge(self.server, self.validator, lambda: "test-private-token")
            repeated = await restarted_bridge.restart(request(body))
            self.assertEqual(result(repeated)["request_id"], body["request_id"])
            self.assertIsNone(restarted_bridge.restart_task)
            self.assertEqual(replace.call_count, 1)

    async def test_restart_rechecks_queue_and_records_exec_failure(self):
        for mode in ("queue_changed", "exec_failed"):
            with tempfile.TemporaryDirectory() as directory, patch.object(bridge_module, "CONFIG_PATH", Path(directory) / "config.json"), \
                    patch.object(bridge_module.asyncio, "sleep", new=AsyncMock()), patch.object(bridge_module.os, "execv", side_effect=OSError("private-path")) as replace:
                self.server.prompt_queue.get_current_queue_volatile.return_value = ([], [])
                await self.bridge.restart(request({"request_id": str(uuid.uuid4())}))
                if mode == "queue_changed":
                    self.server.prompt_queue.get_current_queue_volatile.return_value = (["new-job"], [])
                await self.bridge.restart_task
                outcome = result(await self.bridge.runtime(request()))["last_restart"]
                self.assertEqual(outcome["state"], "cancelled" if mode == "queue_changed" else "failed")
                self.assertEqual(replace.call_count, 0 if mode == "queue_changed" else 1)
                self.assertNotIn("private-path", json.dumps(outcome))


if __name__ == "__main__":
    unittest.main()
