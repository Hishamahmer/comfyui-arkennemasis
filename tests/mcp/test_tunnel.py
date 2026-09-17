import contextlib
import io
import json
from pathlib import Path
import queue
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from mcp_service import tunnel


class TunnelTests(unittest.TestCase):
    def test_stop_request_ends_monitor_without_terminating_unowned_processes(self):
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "web.stop"
            gateway, child = Mock(), Mock()
            gateway.poll.return_value = child.poll.return_value = None
            with patch.object(tunnel.time, "sleep", side_effect=lambda _: stop_file.touch()):
                tunnel.monitor_children(gateway, child, stop_file)
            gateway.terminate.assert_not_called()
            child.terminate.assert_not_called()

    def test_fixed_url_monitor_waits_for_stop_without_a_tunnel_child(self):
        with tempfile.TemporaryDirectory() as directory:
            stop_file = Path(directory) / "web.stop"
            gateway = Mock()
            gateway.poll.return_value = None
            with patch.object(tunnel.time, "sleep", side_effect=lambda _: stop_file.touch()) as sleep:
                tunnel.monitor_children(gateway, None, stop_file)
            gateway.poll.assert_called_once()
            sleep.assert_called_once()
            gateway.terminate.assert_not_called()

    def test_reader_extracts_only_quick_hostname_and_connection_status(self):
        events = queue.Queue()
        tunnel.read_tunnel_output(io.StringIO(
            "Ignore https://attacker.example\n"
            "Ignore https://good.trycloudflare.com.evil.example\n"
            "| https://quiet-blue-forest.trycloudflare.com |\n"
            "INF Registered tunnel connection connIndex=0\n"), events)
        self.assertEqual(events.get_nowait(), ("url", "https://quiet-blue-forest.trycloudflare.com"))
        self.assertEqual(events.get_nowait(), ("connected", None))
        self.assertTrue(events.empty())

    def test_public_url_wait_requires_connection_and_live_children(self):
        gateway, child = Mock(), Mock()
        gateway.poll.return_value = child.poll.return_value = None
        events = queue.Queue()
        events.put(("url", "https://quiet-blue-forest.trycloudflare.com"))
        events.put(("connected", None))
        self.assertEqual(tunnel.wait_for_public_url(gateway, child, events), "https://quiet-blue-forest.trycloudflare.com")
        child.poll.return_value = 1
        with self.assertRaisesRegex(RuntimeError, "HTTPS tunnel stopped"):
            tunnel.wait_for_public_url(gateway, child, queue.Queue())

    def test_occupied_port_refuses_before_settings_change_or_process_start(self):
        with patch.object(tunnel.configuration, "load_settings", return_value=SimpleNamespace(host="127.0.0.1", port=8190)), \
             patch.object(tunnel, "port_in_use", return_value=True), \
             patch.object(tunnel.configuration, "enable_connection_link", create=True) as enable, \
             patch.object(tunnel, "start_child") as start:
            with self.assertRaisesRegex(ValueError, "already in use"):
                tunnel.run_web(Path("config.json"))
            enable.assert_not_called()
            start.assert_not_called()

    def test_fixed_url_stays_identical_on_restart_without_changing_external_tunnel_or_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            configured = tunnel.configuration.initialize(config_path)
            configured.auth_mode = "connection_link"
            configured.public_url = "https://workstation.tailnet.ts.net"
            configured.connection_token = "s" * 64
            config_path.write_text(json.dumps(configured.__dict__), encoding="utf-8")
            original_config = config_path.read_bytes()
            connection_file = Path(directory) / "connection.txt"
            stop_file = Path(directory) / "web.stop"

            for _ in range(2):
                gateway = Mock()
                output = io.StringIO()

                def request_stop(active_gateway, active_tunnel, marker):
                    self.assertIs(active_gateway, gateway)
                    self.assertIsNone(active_tunnel)
                    self.assertEqual(marker, stop_file)
                    self.assertFalse(marker.exists())
                    marker.touch()

                with patch.object(tunnel, "port_in_use", return_value=False), \
                     patch.object(tunnel, "find_cloudflared") as find, \
                     patch.object(tunnel.configuration, "enable_connection_link") as enable, \
                     patch.object(tunnel, "start_child", return_value=gateway) as start, \
                     patch.object(tunnel, "wait_for_gateway") as wait, \
                     patch.object(tunnel, "wait_for_public_url") as public, \
                     patch.object(tunnel, "monitor_children", side_effect=request_stop), \
                     patch.object(tunnel, "stop_child") as stop, contextlib.redirect_stdout(output):
                    self.assertEqual(tunnel.run_web(config_path), 0)
                find.assert_not_called()
                enable.assert_not_called()
                public.assert_not_called()
                start.assert_called_once()
                self.assertEqual(start.call_args.args[0][-3:], ["serve", "--config", str(config_path.resolve())])
                wait.assert_called_once_with(gateway, configured.host, configured.port)
                self.assertEqual([call.args[0] for call in stop.call_args_list], [None, gateway])
                self.assertEqual(config_path.read_bytes(), original_config)
                self.assertEqual(connection_file.read_text(), configured.endpoint + "\n")
                self.assertNotIn(configured.connection_token, output.getvalue())
                self.assertNotIn(configured.public_url, output.getvalue())

    def test_fixed_url_occupied_port_refuses_before_start(self):
        configured = tunnel.configuration.Settings(auth_mode="connection_link", public_url="https://workstation.tailnet.ts.net")
        with patch.object(tunnel.configuration, "load_settings", return_value=configured), \
             patch.object(tunnel, "port_in_use", return_value=True), \
             patch.object(tunnel, "find_cloudflared") as find, \
             patch.object(tunnel.configuration, "enable_connection_link") as enable, \
             patch.object(tunnel, "start_child") as start:
            with self.assertRaisesRegex(ValueError, "already in use"):
                tunnel.run_web(Path("config.json"))
        find.assert_not_called()
        enable.assert_not_called()
        start.assert_not_called()

    def test_web_writes_private_url_without_printing_it_and_cleans_up_children(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            secret = "private-test-secret-that-must-not-be-printed"
            configured = SimpleNamespace(host="127.0.0.1", port=8190, state_dir=directory, auth_mode="connection_link", public_url="",
                                         endpoint=f"http://127.0.0.1:8190/connect/{secret}/mcp")
            gateway, child = Mock(), Mock()
            child.stderr = io.StringIO("")
            output = io.StringIO()
            with patch.object(tunnel.configuration, "load_settings", return_value=configured), \
                 patch.object(tunnel.configuration, "enable_connection_link", return_value=configured, create=True), \
                 patch.object(tunnel, "port_in_use", return_value=False), \
                 patch.object(tunnel, "find_cloudflared", return_value=Path("cloudflared.exe")), \
                 patch.object(tunnel, "start_child", side_effect=[gateway, child]) as start, \
                 patch.object(tunnel, "wait_for_gateway"), \
                 patch.object(tunnel, "wait_for_public_url", return_value="https://quiet-blue-forest.trycloudflare.com"), \
                 patch.object(tunnel, "monitor_children", side_effect=KeyboardInterrupt), \
                 patch.object(tunnel, "stop_child") as stop, contextlib.redirect_stdout(output):
                self.assertEqual(tunnel.run_web(config_path), 0)
            expected = f"https://quiet-blue-forest.trycloudflare.com/connect/{secret}/mcp\n"
            self.assertEqual((Path(directory) / "connection.txt").read_text(), expected)
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn("trycloudflare.com", output.getvalue())
            self.assertIn(str(Path(directory) / "connection.txt"), output.getvalue())
            gateway_args = start.call_args_list[0].args[0]
            self.assertEqual(gateway_args[-3:], ["serve", "--config", str(config_path.resolve())])
            cloud_args = start.call_args_list[1].args[0]
            self.assertEqual(cloud_args[cloud_args.index("--url") + 1], "http://127.0.0.1:8190")
            self.assertEqual(cloud_args[cloud_args.index("--http-host-header") + 1], "127.0.0.1:8190")
            self.assertIn("--no-autoupdate", cloud_args)
            self.assertEqual([call.args[0] for call in stop.call_args_list], [child, gateway])

    def test_gateway_cleanup_runs_if_tunnel_creation_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            configured = SimpleNamespace(host="127.0.0.1", port=8190, state_dir=directory, auth_mode="connection_link", public_url="")
            gateway = Mock()
            with patch.object(tunnel.configuration, "load_settings", return_value=configured), \
                 patch.object(tunnel.configuration, "enable_connection_link", return_value=configured, create=True), \
                 patch.object(tunnel, "port_in_use", return_value=False), \
                 patch.object(tunnel, "find_cloudflared", return_value=Path("cloudflared.exe")), \
                 patch.object(tunnel, "start_child", side_effect=[gateway, OSError("cannot start")]), \
                 patch.object(tunnel, "wait_for_gateway"), \
                 patch.object(tunnel, "stop_child") as stop, contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(OSError):
                    tunnel.run_web(Path(directory) / "config.json")
            self.assertIn(gateway, [call.args[0] for call in stop.call_args_list])

    def test_stuck_child_is_killed_after_bounded_grace(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired(["child"], 5), 0]
        tunnel.stop_child(child)
        child.terminate.assert_called_once()
        child.kill.assert_called_once()
        self.assertEqual([call.kwargs for call in child.wait.call_args_list], [{"timeout": 5}, {"timeout": 5}])

    def test_process_start_never_uses_a_shell_or_inherited_output(self):
        with patch.object(tunnel.subprocess, "Popen") as popen:
            tunnel.start_child(["program.exe", "argument with spaces"])
        self.assertEqual(popen.call_args.args[0], ["program.exe", "argument with spaces"])
        self.assertIs(popen.call_args.kwargs["shell"], False)
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertEqual(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
