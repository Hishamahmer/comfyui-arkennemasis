import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from mcp_service.installation import Installation, inspect_python
from mcp_service.settings import load_settings


class InstallationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # Resolved: a Windows CI runner's temp folder is an 8.3 short path (RUNNER~1), while the
        # code under test compares long, resolved paths.
        self.root = Path(self.temp.name).resolve()
        self.comfy = self.root / "ComfyUI"
        self.service = self.comfy / "custom_nodes" / "comfyui-arkennemasis" / "mcp_service"
        self.service.mkdir(parents=True)
        self.python = self.root / "python_embeded" / "python.exe"
        self.python.parent.mkdir()
        self.python.write_bytes(b"test")
        self.config = self.service / ".local" / "config.json"
        self.installation = Installation(self.config, self.comfy, str(self.python), self.service)
        self.probe = {"executable": str(self.python), "version": [3, 13, 1], "runtime_name": "py313"}
        self.mock_probe = patch("mcp_service.installation.inspect_python", return_value=self.probe).start()
        self.addCleanup(patch.stopall)

    def save(self, **extra):
        return self.installation.save({"public_url": "https://owner.example", **extra})

    def test_status_does_not_initialize_or_expose_secrets(self):
        with patch("mcp_service.installation.tailscale_info", return_value={"installed": False}):
            status = self.installation.status()
        self.assertFalse(self.config.exists())
        self.assertFalse(status["configured"])
        self.assertEqual(status["allowed_node_packs"], [])
        self.save()
        with patch("mcp_service.installation.tailscale_info", return_value={"installed": False}):
            status = self.installation.status()
        settings = load_settings(self.config)
        self.assertNotIn(settings.connection_token, json.dumps(status))
        self.assertNotIn(settings.bridge_token, json.dumps(status))
        self.assertNotIn("comfy:develop", status["enabled_scopes"])

    def test_save_preserves_credentials_and_existing_paths(self):
        self.save()
        before = load_settings(self.config)
        result = self.save(enabled_scopes=["comfy:read", "comfy:develop"], allowed_node_packs=["NewPack"])
        after = load_settings(self.config)
        self.assertTrue(result["restart_required"])
        for key in ("local_token", "bridge_token", "connection_token", "workflow_root", "state_dir", "comfy_root"):
            self.assertEqual(getattr(before, key), getattr(after, key))
        self.assertEqual(after.allowed_node_packs, ["NewPack"])

    def test_first_save_detects_nondefault_comfy_port(self):
        self.installation.save({}, comfy_url="http://127.0.0.1:8288")
        self.assertEqual(load_settings(self.config).comfy_url, "http://127.0.0.1:8288")

    def test_alternate_gateway_python_never_changes_maintenance_interpreter(self):
        alternative = {**self.probe, "executable": str(self.root / "other" / "python.exe")}
        self.mock_probe.return_value = alternative
        self.save(python=alternative["executable"])
        self.assertEqual(load_settings(self.config).comfy_python, str(self.python))
        launcher = json.loads((self.config.parent / "launcher.json").read_text())
        self.assertEqual(launcher["python"], alternative["executable"])

    def test_scope_and_path_validation_does_not_write_bad_config(self):
        for change in ({"enabled_scopes": ["comfy:develop"]}, {"allowed_node_packs": ["../outside"]},
                       {"allowed_node_packs": ["a/b"]}, {"enabled_scopes": ["unknown"]},
                       {"public_url": "http://localhost:8188"}, {"bridge_token": "new"}):
            with self.assertRaises(ValueError):
                self.save(**change)
        self.assertFalse(self.config.exists())

    def test_connection_url_is_only_explicitly_revealed(self):
        self.save()
        result = self.installation.connection_url()
        self.assertEqual(result["url"], load_settings(self.config).endpoint)
        self.assertEqual(result["authentication"], "No Auth")

    def test_dependency_install_is_isolated_and_writes_launcher(self):
        self.save()
        with patch("mcp_service.installation.run", return_value=subprocess.CompletedProcess([], 0, "", "")) as runner, \
                patch("mcp_service.installation.find_tailscale", return_value=None):
            result = self.installation.install_dependencies()
        self.assertTrue(result["installed"])
        command = runner.call_args_list[0].args[0]
        self.assertIn("--target", command)
        self.assertIn("--isolated", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertNotIn("--upgrade", command)
        self.assertTrue((self.service / ".runtime" / "py313" / "ark-installation.json").is_file())
        self.assertTrue((self.config.parent / "launcher.json").is_file())

    def test_dependency_failure_does_not_leave_partial_runtime(self):
        self.save()
        launcher = (self.config.parent / "launcher.json").read_bytes()
        with patch("mcp_service.installation.run", return_value=subprocess.CompletedProcess([], 1, "", "private-token")):
            with self.assertRaisesRegex(ValueError, "installation failed"):
                self.installation.install_dependencies()
        self.assertEqual(list((self.service / ".runtime").iterdir()), [])
        self.assertEqual((self.config.parent / "launcher.json").read_bytes(), launcher)

    def hooked_launcher(self, flags):
        service = r"ComfyUI\custom_nodes\comfyui-arkennemasis\mcp_service"
        return (f'@echo off\r\ncd /d "%~dp0"\r\nif exist "{service}\\companion.py" .\\python_embeded\\python.exe -s "{service}\\companion.py" start\r\n'
                f'.\\python_embeded\\python.exe -s "{service}\\launch_backend.py" {flags}\r\npause\r\n').encode()

    @unittest.skipUnless(os.name == "nt", "Portable BAT integration is Windows-specific")
    def test_only_fast_fp16_gets_mcp_launcher_and_originals_are_unchanged(self):
        self.save()
        path = self.root / "run_nvidia_gpu_fast_fp16_accumulation.bat"
        original = b'.\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build --preview-method auto\r\npause\r\n'
        path.write_bytes(original)
        ordinary = {self.root / "run_cpu.bat": b"echo my own CPU launcher\r\n",
                    self.root / "run_nvidia_gpu.bat": b'.\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build\npause'}
        for other, content in ordinary.items():
            other.write_bytes(content)
        result = self.installation.install_launchers()
        mcp = self.root / "run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat"
        self.assertEqual(result["changed"], [mcp.name])
        self.assertEqual(path.read_bytes(), original)
        for other, content in ordinary.items():
            self.assertEqual(other.read_bytes(), content)
            self.assertFalse(other.with_name(other.stem + "_with_mcp.bat").exists())
        connected = mcp.read_bytes()
        self.assertIn(b"launch_backend.py", connected)
        self.assertIn(b"companion.py", connected)
        self.assertIn(b"--windows-standalone-build --preview-method auto", connected)
        self.assertIn(b'cd /d "%~dp0"', connected)
        self.assertFalse((self.config.parent / "launcher-backups" / path.name).exists())
        self.assertEqual(self.installation.install_launchers()["changed"], [])
        self.assertEqual(mcp.read_bytes(), connected)

    @unittest.skipUnless(os.name == "nt", "Portable BAT integration is Windows-specific")
    def test_migration_removes_previous_auto_start_from_all_standard_launchers(self):
        self.save()
        originals = {}
        for name, flags in (("run_cpu", "--cpu --windows-standalone-build"),
                            ("run_nvidia_gpu", "--windows-standalone-build"),
                            ("run_nvidia_gpu_fast_fp16_accumulation", "--windows-standalone-build --fast fp16_accumulation")):
            originals[name] = self.hooked_launcher(flags)
            (self.root / (name + ".bat")).write_bytes(originals[name])
        self.assertEqual(len(self.installation.install_launchers()["changed"]), 4)
        for name, flag in (("run_cpu", "--cpu"), ("run_nvidia_gpu", "--windows-standalone-build"),
                           ("run_nvidia_gpu_fast_fp16_accumulation", "--fast fp16_accumulation")):
            normal = (self.root / (name + ".bat")).read_text()
            companion = self.root / (name + "_with_mcp.bat")
            self.assertNotIn("mcp_service", normal)
            self.assertIn(flag, normal)
            self.assertEqual((self.config.parent / "launcher-backups" / (name + ".bat")).read_bytes(), originals[name])
            if name == "run_nvidia_gpu_fast_fp16_accumulation":
                connected = companion.read_text()
                self.assertIn(flag, connected)
                self.assertEqual(connected.count("companion.py"), 2)
            else:
                self.assertFalse(companion.exists())
        self.assertEqual(self.installation.install_launchers()["changed"], [])

    @unittest.skipUnless(os.name == "nt", "Portable BAT integration is Windows-specific")
    def test_existing_custom_sidecar_is_not_overwritten_and_migration_is_preflighted(self):
        self.save()
        hooked = self.hooked_launcher("--cpu --windows-standalone-build")
        original = b'.\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build\r\npause\r\n'
        cpu = self.root / "run_cpu.bat"
        gpu = self.root / "run_nvidia_gpu_fast_fp16_accumulation.bat"
        cpu.write_bytes(hooked)
        gpu.write_bytes(original)
        custom = self.root / "run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat"
        custom.write_bytes(b"echo my own launcher\r\n")
        with self.assertRaisesRegex(ValueError, "not overwritten"):
            self.installation.install_launchers()
        self.assertEqual(cpu.read_bytes(), hooked)
        self.assertEqual(gpu.read_bytes(), original)
        self.assertEqual(custom.read_bytes(), b"echo my own launcher\r\n")

    @unittest.skipUnless(os.name == "nt", "Portable BAT integration is Windows-specific")
    def test_custom_hook_and_multiple_backend_commands_are_not_migrated(self):
        self.save()
        path = self.root / "run_nvidia_gpu_fast_fp16_accumulation.bat"
        normal = '.\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build\n'
        for text in (normal + normal, "python custom/companion.py start\n" + normal,
                     normal.rstrip() + " & echo custom\n"):
            path.write_text(text)
            with self.assertRaises(ValueError):
                self.installation.install_launchers()
            self.assertEqual(path.read_text(), text)
            self.assertFalse((self.root / "run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat").exists())

    def plan(self, **overrides):
        self.save()
        root = self.config.parent / "maintenance"
        root.mkdir(exist_ok=True)
        plan = {"plan_id": "a" * 32, "status": "planned", "created_at": time.time(),
                "protected_changes": ["torch==2.8.0"], "before": {"secret": "hidden"},
                "introduced_dependency_conflicts": [], **overrides}
        (root / f"package-{plan['plan_id']}.json").write_text(json.dumps(plan), encoding="utf-8")
        return plan

    def test_owner_can_review_and_approve_exact_versions_without_private_manifest(self):
        plan = self.plan()
        pending = self.installation.pending_package_plans()
        self.assertEqual(pending[0]["protected_changes"], ["torch==2.8.0"])
        self.assertNotIn("hidden", json.dumps(pending))
        self.installation.approve_package(plan["plan_id"])
        approval = json.loads((self.config.parent / "maintenance" / f"approved-{plan['plan_id']}.json").read_text())
        self.assertEqual(approval, {"plan_id": plan["plan_id"], "protected_changes": ["torch==2.8.0"]})
        self.assertEqual(self.installation.pending_package_plans(), [])

    def test_expired_applied_and_conflicting_package_plans_cannot_be_approved(self):
        for overrides in ({"created_at": time.time() - 86410}, {"status": "applied"},
                          {"introduced_dependency_conflicts": ["new conflict"]}):
            plan = self.plan(**overrides)
            with self.assertRaises(ValueError):
                self.installation.approve_package(plan["plan_id"])
        with self.assertRaises(ValueError):
            self.installation.approve_package("../outside")

    @unittest.skipUnless(os.name == "nt", "Automatic BAT ownership is Windows-specific")
    def test_start_follows_verified_supervisor_to_original_bat_owner(self):
        self.save()
        runner = self.service / "launch_backend.py"
        supervisor = {"Name": "python.exe", "CommandLine": f'python.exe -s "{runner}" --cpu', "ParentProcessId": 300}
        bat = {"Name": "cmd.exe", "CommandLine": f'cmd.exe /c ""{self.root / "run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat"}""', "ParentProcessId": 100}
        replies = [subprocess.CompletedProcess([], 0, json.dumps(item), "") for item in (supervisor, bat)]
        with patch("mcp_service.installation.os.getppid", return_value=200), \
                patch.object(sys, "_arkennemasis_supervised", True, create=True), \
                patch.object(sys, "_arkennemasis_supervisor_pid", 200, create=True), \
                patch("mcp_service.installation.run", side_effect=replies) as inspect, \
                patch("mcp_service.installation.subprocess.Popen") as spawn:
            self.assertTrue(self.installation.start()["started"])
            self.assertEqual(inspect.call_count, 2)
            self.assertEqual(spawn.call_args.args[0][-2:], ["--owner-pid", "300"])

    @unittest.skipUnless(os.name == "nt", "Automatic BAT ownership is Windows-specific")
    def test_start_accepts_an_exact_unquoted_with_mcp_path_without_spaces(self):
        self.save()
        path = self.root / "run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat"
        if " " in str(path):
            self.skipTest("Temporary directory contains spaces and requires quoting.")
        for command, accepted in ((f"cmd.exe /d /c {path}", True), (f"cmd.exe /d /c {path}.other", False)):
            bat = {"Name": "cmd.exe", "CommandLine": command, "ParentProcessId": 100}
            with patch("mcp_service.installation.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(bat), "")), \
                    patch("mcp_service.installation.subprocess.Popen") as spawn:
                if accepted:
                    self.assertTrue(self.installation.start()["started"])
                    spawn.assert_called_once()
                else:
                    with self.assertRaises(ValueError):
                        self.installation.start()
                    spawn.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Automatic BAT ownership is Windows-specific")
    def test_start_does_not_accept_an_unrelated_python_ancestor(self):
        self.save()
        unrelated = {"Name": "python.exe", "CommandLine": "python.exe other.py", "ParentProcessId": 300}
        with patch("mcp_service.installation.os.getppid", return_value=200), \
                patch.object(sys, "_arkennemasis_supervised", True, create=True), \
                patch.object(sys, "_arkennemasis_supervisor_pid", 200, create=True), \
                patch("mcp_service.installation.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(unrelated), "")), \
                patch("mcp_service.installation.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "_with_mcp.bat"):
                self.installation.start()
            spawn.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Automatic BAT ownership is Windows-specific")
    def test_normal_launcher_cannot_start_the_companion_from_setup(self):
        self.save()
        bat = {"Name": "cmd.exe", "CommandLine": f'cmd.exe /c ""{self.root / "run_nvidia_gpu.bat"}""', "ParentProcessId": 100}
        with patch("mcp_service.installation.run", return_value=subprocess.CompletedProcess([], 0, json.dumps(bat), "")), \
                patch("mcp_service.installation.subprocess.Popen") as spawn:
            with self.assertRaisesRegex(ValueError, "_with_mcp.bat"):
                self.installation.start()
            spawn.assert_not_called()


class PythonProbeTests(unittest.TestCase):
    def test_invalid_executable_is_rejected_without_execution(self):
        with patch("mcp_service.installation.run") as runner:
            for path in ("python.exe", "/missing/python", __file__):
                with self.assertRaises(ValueError):
                    inspect_python(path)
            runner.assert_not_called()
