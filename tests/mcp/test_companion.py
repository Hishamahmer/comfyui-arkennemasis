import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import uuid

from mcp_service import companion
from mcp_service.settings import Settings


class Clock:
    def __init__(self, deadline=600):
        self.now = 0
        self.deadline = deadline

    def sleep(self, seconds):
        self.now += seconds
        if self.now > self.deadline:
            raise AssertionError("Lifecycle test exceeded its fake-time deadline")

    @contextlib.contextmanager
    def running(self):
        with patch.object(companion.time, "monotonic", side_effect=lambda: self.now), \
             patch.object(companion.time, "time", side_effect=lambda: 1000 + self.now), \
             patch.object(companion.time, "sleep", side_effect=self.sleep):
            yield self

    def owner(self, duration):
        return SimpleNamespace(alive=lambda: self.now < duration)


def public_route():
    key = "machine.tailnet.ts.net:443"
    return {"Foreground": {"owned-session": {"TCP": {"443": {"HTTPS": True}}, "AllowFunnel": {key: True},
            "Web": {key: {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8190"}}}}}}}


def monitor(clock, duration, *, connection=None, backend=None, restart=None):
    connection = connection or Mock()
    if backend is None:
        backend = Mock(pid=123)
        backend.state.return_value = "online"
    restart = restart or SimpleNamespace(current=None, tick=Mock())
    update = Mock()
    with clock.running():
        companion.keep_connected(clock.owner(duration), lambda: True, connection, backend, update, restart)
    return update


class CompanionTests(unittest.TestCase):
    def test_foreground_mapping_must_match_public_flag_hostname_and_target(self):
        state = public_route()
        self.assertTrue(companion.port_443_used(state))
        self.assertTrue(companion.funnel_ready(state, "machine.tailnet.ts.net", "http://127.0.0.1:8190"))
        self.assertFalse(companion.funnel_ready(state, "machine.tailnet.ts.net", "http://127.0.0.1:8188"))
        state["Foreground"]["owned-session"]["AllowFunnel"]["machine.tailnet.ts.net:443"] = False
        self.assertFalse(companion.funnel_ready(state, "machine.tailnet.ts.net", "http://127.0.0.1:8190"))

    def test_closed_owner_never_starts_readiness_work(self):
        ready = Mock()
        self.assertFalse(companion.wait_until_ready(SimpleNamespace(alive=lambda: False), ready, Mock()))
        ready.assert_not_called()

    def test_owner_exit_during_startup_ends_wait(self):
        clock = Clock()
        with clock.running():
            self.assertFalse(companion.wait_until_ready(clock.owner(1), lambda: False, Mock()))
        self.assertEqual(clock.now, 1)

    def test_startup_has_a_finite_deadline(self):
        clock = Clock()
        with clock.running(), self.assertRaisesRegex(RuntimeError, "timed out"):
            companion.wait_until_ready(clock.owner(100), lambda: False, Mock(), timeout=3)
        self.assertEqual(clock.now, 3)

    def test_child_failure_during_startup_is_not_reported_online(self):
        child, ready = Mock(), Mock()
        child.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "stopped during startup"):
            companion.wait_until_ready(SimpleNamespace(alive=lambda: True), ready, Mock(), child=child)
        ready.assert_not_called()

    def test_valid_json_without_health_object_is_not_ready(self):
        opener = Mock()
        for value in ([], None, 1, "system devices", {"unexpected": True}):
            opener.open.return_value = io.BytesIO(json.dumps(value).encode())
            self.assertFalse(companion.local_ready(opener, "http://localhost/health", gateway=True))

    def test_backend_busy_for_minutes_preserves_connection_and_recovers(self):
        clock = Clock()
        connection, backend = Mock(), Mock(pid=123)
        connection.alive.return_value = connection.ready.return_value = True
        backend.state.side_effect = lambda _: "unresponsive" if 2 <= clock.now < 122 else "online"
        update = monitor(clock, 130, connection=connection, backend=backend)
        connection.start.assert_not_called()
        self.assertTrue(any(call.args[2]["backend"] == "unresponsive" and call.args[2]["tunnel"] == "online"
                            for call in update.call_args_list))
        self.assertEqual(update.call_args.args[0], "online")

    def test_backend_exit_keeps_diagnostic_connection_until_owner_closes(self):
        connection, backend = Mock(), Mock(pid=None)
        connection.alive.return_value = connection.ready.return_value = True
        backend.state.return_value = "exited"
        update = monitor(Clock(), 80, connection=connection, backend=backend)
        connection.start.assert_not_called()
        self.assertEqual(update.call_args.args[2], {"gateway": "online", "tunnel": "online", "backend": "exited"})
        self.assertIn("diagnosis", update.call_args.args[1])

    def test_transient_tailscale_probe_failure_does_not_restart_children(self):
        clock, connection = Clock(), Mock()
        connection.alive.return_value = True

        def readiness(layer):
            if layer == "tunnel" and 10 <= clock.now < 20:
                raise OSError("Transient local Tailscale status failure")
            return True

        connection.ready.side_effect = readiness
        update = monitor(clock, 40, connection=connection)
        connection.start.assert_not_called()
        self.assertTrue(any(call.args[2]["tunnel"] == "offline" for call in update.call_args_list))
        self.assertEqual(update.call_args.args[0], "online")

    def test_gateway_crash_recovers_gateway_without_restarting_tunnel(self):
        clock, connection = Clock(), Mock()
        recovered = []
        connection.alive.side_effect = lambda layer: layer != "gateway" or clock.now < 10 or bool(recovered)
        connection.ready.side_effect = connection.alive.side_effect
        connection.start.side_effect = lambda layer, *_: recovered.append(layer) or True
        update = monitor(clock, 40, connection=connection)
        self.assertEqual(recovered, ["gateway"])
        self.assertEqual(update.call_args.args[0], "online")

    def test_live_tunnel_failure_gets_grace_then_recovers_once(self):
        clock, connection = Clock(), Mock()
        recovered = []
        connection.alive.return_value = True
        connection.ready.side_effect = lambda layer: layer != "tunnel" or clock.now < 10 or bool(recovered)
        connection.start.side_effect = lambda layer, *_: recovered.append((layer, clock.now)) or True
        update = monitor(clock, 90, connection=connection)
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0][0], "tunnel")
        self.assertGreaterEqual(recovered[0][1], 55)
        self.assertEqual(update.call_args.args[0], "online")

    def test_recovery_exhaustion_is_bounded_and_keeps_other_layer_alive(self):
        connection = Mock()
        connection.alive.side_effect = lambda layer: layer == "tunnel"
        connection.ready.side_effect = connection.alive.side_effect
        connection.start.side_effect = RuntimeError("Cannot start gateway")
        update = monitor(Clock(), 140, connection=connection)
        self.assertEqual(connection.start.call_count, 5)
        self.assertTrue(all(call.args[0] == "gateway" for call in connection.start.call_args_list))
        self.assertEqual(update.call_args.args[0], "error")
        self.assertEqual(update.call_args.args[2]["tunnel"], "online")
        connection.close.assert_not_called()

    def test_paused_recovery_can_observe_external_connection_recovery(self):
        clock, connection = Clock(), Mock()
        connection.alive.side_effect = lambda layer: layer == "gateway" or clock.now >= 100
        connection.ready.side_effect = connection.alive.side_effect
        connection.start.side_effect = RuntimeError("Tailscale unavailable")
        update = monitor(clock, 140, connection=connection)
        self.assertEqual(connection.start.call_count, 5)
        self.assertTrue(any(call.args[0] == "error" for call in update.call_args_list))
        self.assertEqual(update.call_args.args[0], "online")

    def test_owner_close_during_gateway_recovery_does_not_start_tunnel(self):
        clock, connection = Clock(), Mock()
        connection.alive.return_value = connection.ready.return_value = False

        def start(layer, *_):
            clock.now = 10
            return False

        connection.start.side_effect = start
        monitor(clock, 10, connection=connection)
        self.assertEqual([call.args[0] for call in connection.start.call_args_list], ["gateway"])

    def test_closed_owner_does_not_probe_any_layer(self):
        connection = Mock()
        monitor(Clock(), 0, connection=connection)
        connection.alive.assert_not_called()
        connection.ready.assert_not_called()

    def test_process_handle_distinguishes_unresponsive_exited_and_replacement(self):
        first, second = Mock(), Mock()
        first.alive.return_value = second.alive.return_value = True
        backend = companion.BackendProcess("http://127.0.0.1:8188")
        with patch.object(companion, "listener_pid", side_effect=[111, 222]), \
             patch.object(companion, "OwnerProcess", side_effect=[first, second]):
            self.assertEqual(backend.state(True), "online")
            self.assertEqual(backend.pid, 111)
            self.assertEqual(backend.state(False), "unresponsive")
            first.alive.return_value = False
            self.assertEqual(backend.state(False), "exited")
            first.close.assert_called_once()
            self.assertEqual(backend.state(True), "online")
            self.assertEqual(backend.pid, 222)
            backend.close()
            second.close.assert_called_once()

    def test_failed_process_inspection_does_not_claim_exit(self):
        backend = companion.BackendProcess("http://127.0.0.1:8188")
        with patch.object(companion, "listener_pid", side_effect=OSError("Access denied")):
            self.assertEqual(backend.state(True), "online")
        self.assertEqual(backend.state(False), "unknown")

    def test_status_records_transitions_and_last_success(self):
        clock = Clock()
        with tempfile.TemporaryDirectory() as directory, clock.running():
            status = companion.ConnectionStatus(directory, 123)
            healthy = {"gateway": "online", "tunnel": "online", "backend": "online"}
            status("online", "AI connection online", healthy)
            clock.sleep(2)
            status("online", "AI connection online", healthy)
            clock.sleep(2)
            status("waiting", "ComfyUI is not responding", {**healthy, "backend": "unresponsive"})
            record = json.loads((Path(directory) / "session-status.json").read_text())
            self.assertEqual(record["last_success_at"], 1002)
            self.assertEqual(record["layer_success_at"]["backend"], 1002)
            self.assertEqual(record["layer_success_at"]["gateway"], 1004)
            self.assertEqual(len((Path(directory) / "connection-events.jsonl").read_text().splitlines()), 2)

    def test_public_readiness_requires_running_backend_hostname_and_funnel(self):
        hostname, target = "machine.tailnet.ts.net", "http://127.0.0.1:8190"
        tailnet = {"BackendState": "Running", "Self": {"DNSName": hostname + "."}}
        for backend in ({**tailnet, "BackendState": "Stopped"}, {**tailnet, "Self": {"DNSName": "renamed.ts.net"}}):
            with patch.object(companion, "tailscale_status", return_value=backend) as status:
                self.assertFalse(companion.public_connection_ready("tailscale.exe", hostname, target))
                status.assert_called_once_with("tailscale.exe", "status")
        for mapping, expected in (({}, False), (public_route(), True)):
            with patch.object(companion, "tailscale_status", side_effect=[tailnet, mapping]):
                self.assertEqual(companion.public_connection_ready("tailscale.exe", hostname, target), expected)

    def test_gateway_port_conflict_leaves_unowned_processes_alone(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(state_dir=directory, auth_mode="connection_link", public_url="https://machine.tailnet.ts.net")
            with patch.object(companion, "port_in_use", return_value=True), \
                 patch.object(companion, "local_ready", return_value=False), \
                 patch.object(companion.subprocess, "Popen") as start, \
                 patch.object(companion, "stop_child") as stop, contextlib.redirect_stdout(io.StringIO()):
                companion.run_session(settings, SimpleNamespace(alive=lambda: True), 123, {})
            start.assert_not_called()
            self.assertTrue(all(call.args[0] is None for call in stop.call_args_list))
            self.assertEqual(json.loads((Path(directory) / "session-status.json").read_text())["state"], "error")

    def test_tunnel_port_conflict_never_resets_unowned_route(self):
        connection = companion.OwnedConnection(Settings(public_url="https://machine.tailnet.ts.net"), {"tailscale": "tailscale.exe"}, Mock())
        with patch.object(companion, "tailscale_status", side_effect=[
                {"BackendState": "Running", "Self": {"DNSName": "machine.tailnet.ts.net."}}, public_route()]), \
             patch.object(companion.subprocess, "Popen") as start, patch.object(companion, "stop_child") as stop:
            with self.assertRaisesRegex(ValueError, "already shared"):
                connection.start("tunnel", SimpleNamespace(alive=lambda: True), Mock())
        start.assert_not_called()
        stop.assert_called_once_with(None)

    def test_owned_funnel_recovery_waits_for_previous_route_removal(self):
        connection = companion.OwnedConnection(Settings(public_url="https://machine.tailnet.ts.net"), {"tailscale": "tailscale.exe"}, Mock())
        previous = connection.funnel = Mock()
        running = {"BackendState": "Running", "Self": {"DNSName": "machine.tailnet.ts.net."}}
        with patch.object(companion, "tailscale_status", side_effect=[running, public_route(), running, public_route(), running, {}]), \
             patch.object(companion, "stop_child") as stop, patch.object(companion, "wait_until_ready", return_value=True), \
             patch.object(companion.subprocess, "Popen") as start:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, "previous owned"):
                    connection.start("tunnel", SimpleNamespace(alive=lambda: True), Mock())
            self.assertTrue(connection.start("tunnel", SimpleNamespace(alive=lambda: True), Mock()))
        self.assertEqual([call.args[0] for call in stop.call_args_list], [previous, None, None])
        start.assert_called_once()
        self.assertNotIn("--bg", start.call_args.args[0])
        self.assertEqual(start.call_args.args[0][-1], "http://127.0.0.1:8190")

    def test_session_closes_children_and_preserves_url_on_owner_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(state_dir=directory, auth_mode="connection_link", public_url="https://machine.tailnet.ts.net", connection_token="s" * 64)
            connection, backend = Mock(), Mock()
            with patch.object(companion, "OwnedConnection", return_value=connection), \
                 patch.object(companion, "BackendProcess", return_value=backend):
                companion.run_session(settings, SimpleNamespace(alive=lambda: False), 123, {})
            connection.close.assert_called_once()
            backend.close.assert_called_once()
            self.assertEqual((Path(directory) / "connection.txt").read_text(), settings.endpoint + "\n")
            self.assertEqual(json.loads((Path(directory) / "session-status.json").read_text())["state"], "stopped")


class RestartTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.settings = Settings(state_dir=str(self.root), bridge_token="secret-bridge-token")
        self.opener = Mock()
        self.opener.open.return_value = io.BytesIO(b'{"accepted": true, "pid": 111}')
        self.clock = Clock()
        self.restart = companion.RestartRequest(self.settings, self.opener)
        self.request = {"id": str(uuid.uuid4()), "action": "restart", "created_at": 1000}
        (self.root / "lifecycle-request.json").write_text(json.dumps(self.request))

    def result(self):
        return json.loads((self.root / "lifecycle-results" / (self.request["id"] + ".json")).read_text())

    def test_restart_persists_before_send_and_duplicate_id_is_not_resent(self):
        def respond(*_args, **_kwargs):
            self.assertEqual(self.result()["state"], "accepted")
            return io.BytesIO(b'{"accepted":true,"pid":111}')

        self.opener.open.side_effect = respond
        with self.clock.running():
            self.restart.tick("online", 111)
            self.assertEqual(self.result()["state"], "restarting")
            self.restart.tick("exited", None)
            self.restart.tick("online", 222)
            self.assertEqual(self.result()["state"], "ready")
            self.restart.tick("online", 222)
        self.opener.open.assert_called_once()
        call = self.opener.open.call_args.args[0]
        self.assertEqual(call.full_url, "http://127.0.0.1:8188/arkennemasis/mcp/restart")
        self.assertEqual(json.loads(call.data), {"request_id": self.request["id"]})
        self.assertNotIn(self.settings.bridge_token, (self.root / "lifecycle-result.json").read_text())

    def test_uncertain_response_is_observed_without_resend(self):
        self.opener.open.side_effect = OSError("Connection closed before response")
        with self.clock.running():
            self.restart.tick("online", 111)
            self.restart.tick("exited", None)
            self.restart.tick("online", 222)
        self.assertEqual(self.result()["state"], "ready")
        self.opener.open.assert_called_once()

    def test_same_process_recovering_from_busy_is_not_restart_success(self):
        with self.clock.running():
            self.restart.tick("online", 111)
            self.restart.tick("unresponsive", 111)
            self.restart.tick("online", 111)
            self.assertEqual(self.result()["state"], "restarting")
            self.clock.sleep(181)
            self.restart.tick("online", 111)
        self.assertEqual(self.result()["state"], "failed")
        self.assertIn("remains connected", self.result()["message"])

    def test_restart_or_import_failure_keeps_diagnostic_connection(self):
        connection, backend = Mock(), Mock(pid=None)
        connection.alive.return_value = connection.ready.return_value = True
        backend.state.return_value = "exited"
        update = monitor(self.clock, 190, connection=connection, backend=backend, restart=self.restart)
        self.assertEqual(self.result()["state"], "failed")
        connection.start.assert_not_called()
        connection.close.assert_not_called()
        self.assertEqual(update.call_args.args[2]["gateway"], "online")
        self.assertEqual(update.call_args.args[2]["tunnel"], "online")

    def test_watcher_restart_marks_inflight_uncertain_without_repeating(self):
        with self.clock.running():
            self.restart.result(self.request, "accepted", "Accepted before interrupted watcher")
            companion.RestartRequest(self.settings, self.opener).tick("online", 111)
        self.opener.open.assert_not_called()
        self.assertEqual(self.result()["state"], "failed")
        self.assertIn("will not be sent again", self.result()["message"])

    def test_queue_changed_cancellation_is_reported_promptly(self):
        with self.clock.running():
            self.restart.tick("online", 111)
            (self.root / "backend-restarts.json").write_text(json.dumps([
                {"request_id": self.request["id"], "state": "cancelled", "error": "untrusted details"}]))
            self.restart.tick("online", 111)
        self.assertEqual(self.result()["state"], "failed")
        self.assertIn("queue changed", self.result()["message"])
        self.assertNotIn("untrusted", self.result()["message"])

    def test_rejection_has_specific_sanitized_reason(self):
        body = io.BytesIO(b'{"error":{"code":"unsupported_launcher","message":"secret output"}}')
        self.opener.open.side_effect = urllib.error.HTTPError("http://localhost", 409, "Conflict", {}, body)
        with self.clock.running():
            self.restart.tick("online", 111)
        self.assertIsNone(self.restart.current)
        self.assertIn("external launcher", self.result()["message"])
        self.assertNotIn("secret", self.result()["message"])

    def test_expired_request_is_not_executed(self):
        with self.clock.running():
            self.clock.sleep(301)
            self.restart.tick("online", 111)
        self.opener.open.assert_not_called()
        self.assertEqual(self.result()["state"], "failed")

    def test_malformed_request_cannot_select_action_or_result_path(self):
        for request in ({**self.request, "id": "../outside"}, {**self.request, "action": "exec"},
                        {**self.request, "created_at": True}, {**self.request, "arguments": []}, []):
            (self.root / "lifecycle-request.json").write_text(json.dumps(request))
            with self.clock.running():
                self.restart.tick("online", 111)
        (self.root / "lifecycle-request.json").write_text(" " * 5000)
        self.restart.tick("online", 111)
        self.opener.open.assert_not_called()
        self.assertFalse((self.root / "lifecycle-results").exists())


if __name__ == "__main__":
    unittest.main()
