import json
import os
from pathlib import Path
import subprocess
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
        self.root = Path(self.temp.name)
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

    @unittest.skipUnless(os.name == "nt", "Portable BAT integration is Windows-specific")
    def test_launchers_upgrade_hook_and_main_idempotently_with_original_backup(self):
        self.save()
        path = self.root / "run_nvidia_gpu.bat"
        original = b'.\\python_embeded\\python.exe -s ComfyUI\\main.py --windows-standalone-build\r\npause\r\n'
        path.write_bytes(original)
        result = self.installation.install_launchers()
        self.assertEqual(result["changed"], [path.name])
        updated = path.read_bytes()
        self.assertIn(b"launch_backend.py", updated)
        self.assertIn(b"companion.py", updated)
        self.assertIn(b"--windows-standalone-build", updated)
        self.assertEqual((self.config.parent / "launcher-backups" / path.name).read_bytes(), original)
        self.assertEqual(self.installation.install_launchers()["changed"], [])
        self.assertEqual(path.read_bytes(), updated)
        # Existing deployments already have the companion hook but still need the backend wrapper.
        path.write_bytes(updated.replace(b'"ComfyUI\\custom_nodes\\comfyui-arkennemasis\\mcp_service\\launch_backend.py"', b"ComfyUI\\main.py"))
        self.assertEqual(self.installation.install_launchers()["changed"], [path.name])
        self.assertEqual((self.config.parent / "launcher-backups" / path.name).read_bytes(), original)

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


class PythonProbeTests(unittest.TestCase):
    def test_invalid_executable_is_rejected_without_execution(self):
        with patch("mcp_service.installation.run") as runner:
            for path in ("python.exe", "/missing/python", __file__):
                with self.assertRaises(ValueError):
                    inspect_python(path)
            runner.assert_not_called()
