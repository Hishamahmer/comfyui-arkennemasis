import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp_service.development import DevelopmentConflict, DevelopmentError, NodeWorkspace
from mcp_service.maintenance import Maintenance, MaintenanceError, _github_url, _redact, _specs


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "custom_nodes"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "state"
        self.workspace = NodeWorkspace(self.root, self.state, ["example", "new_pack", "comfyui-arkennemasis"])
        self.maintenance = Maintenance(self.workspace, sys.executable, self.state)

    def tearDown(self):
        self.temp.cleanup()

    def repo(self, pack="example"):
        if not shutil.which("git"):
            self.skipTest("Git is unavailable")
        path = self.root / pack
        path.mkdir()
        self.git(path, "init", "-q")
        self.git(path, "config", "user.name", "Local test")
        self.git(path, "config", "user.email", "test@localhost")
        (path / "node.py").write_text("value = 1\n")
        (path / "other.py").write_text("value = 1\n")
        self.git(path, "add", ".")
        self.git(path, "commit", "-qm", "Initial")
        return path

    @staticmethod
    def git(path, *args):
        result = subprocess.run([shutil.which("git"), "-C", str(path), *args], capture_output=True, text=True,
                                env=Maintenance._environment(), check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.stdout.strip()

    def manifest(self, packages=None):
        return {"executable": str(sys.executable), "prefix": "test", "version": "test",
                "packages": packages or [{"name": "torch", "version": "2.0"}], "interpreter_size": 1, "interpreter_modified_ns": 1}

    @staticmethod
    def wheel(name="example", version="1.0"):
        return {"metadata": {"name": name, "version": version}, "download_info": {
            "url": f"https://files.pythonhosted.org/packages/{name}-{version}-py3-none-any.whl",
            "archive_info": {"hashes": {"sha256": "a" * 64}}}}

    def make_plan(self, wheels=None):
        wheels = [self.wheel()] if wheels is None else wheels

        def pip(args, **kwargs):
            if "--dry-run" in args:
                Path(args[args.index("--report") + 1]).write_text(json.dumps({"install": wheels}), encoding="utf-8")
            return 0, "OK"

        with patch.object(self.maintenance, "_manifest", return_value=self.manifest()), patch.object(self.maintenance, "_pip", side_effect=pip):
            return self.maintenance.package_plan([f"{w['metadata']['name']}=={w['metadata']['version']}" for w in wheels] or ["example==1.0"])

    def test_spec_validation_excludes_flags_urls_paths_ranges_and_duplicates(self):
        self.assertEqual(_specs(["Foo_Bar==1.2+local"]), ["foo-bar==1.2+local"])
        for value in [[], ["--target=x"], ["foo>=1"], ["foo[bar]==1"], ["x @ https://example.com/x.whl"], ["../x"], ["foo==1", "FOO==2"]]:
            with self.subTest(value=value), self.assertRaises(MaintenanceError):
                _specs(value)

    def test_git_url_excludes_credentials_other_hosts_protocols_and_query(self):
        self.assertEqual(_github_url("https://github.com/example/nodes.git"), "https://github.com/example/nodes.git")
        for url in ["https://github.com.evil.com/x/y", "git@github.com:x/y", "https://token@github.com/x/y", "https://github.com/x/y?token=secret", "file:///x", "https://github.com/x/../y"]:
            with self.subTest(url=url), self.assertRaises(MaintenanceError):
                _github_url(url)

    def test_logs_redact_credentials_tokens_and_connection_path(self):
        text = _redact("https://bob:password@example.com/a?key=xxx /connect/abcdef/mcp Bearer tok123 password=hunter2")
        for secret in ["bob:password", "xxx", "abcdef", "tok123", "hunter2"]:
            self.assertNotIn(secret, text)

    def test_redaction_covers_token_userinfo_and_quoted_json_values(self):
        for text, secret in [
            ("package @ https://ghp_EXAMPLE_ONLY@github.com/private/repo", "ghp_EXAMPLE_ONLY"),
            ('{"token": "EXAMPLE ONLY"}', "EXAMPLE ONLY"),
            ('{"access_token": "EXAMPLE\\\"ONLY"}', "EXAMPLE"),
            ("{'api_key': 'EXAMPLE ONLY'}", "EXAMPLE ONLY"),
            ('password="EXAMPLE ONLY"', "EXAMPLE ONLY"),
        ]:
            with self.subTest(text=text):
                self.assertNotIn(secret, _redact(text))
        self.assertEqual(json.loads(_redact('{"token": "EXAMPLE ONLY", "name": "keep"}')),
                         {"token": "[redacted]", "name": "keep"})

    def test_venv_interpreter_symlink_retains_invocation_path(self):
        executable = Path(self.temp.name) / "venv" / "bin" / "python"
        executable.parent.mkdir(parents=True)
        try:
            executable.symlink_to(sys.executable)
        except OSError:
            self.skipTest("Interpreter symlink creation requires privileges on this host")
        maintenance = Maintenance(self.workspace, executable, self.state)
        with patch.object(maintenance, "_run", return_value=(0, "OK")) as run:
            maintenance._pip(["check"])
        self.assertEqual(maintenance.python, executable)
        self.assertEqual(run.call_args.args[0][0], str(executable))
        self.assertNotEqual(str(maintenance.python), str(executable.resolve()))

    def test_whole_arkennemasis_update_and_clone_are_excluded(self):
        for action in [lambda: self.maintenance.git_clone("comfyui-arkennemasis", "https://github.com/x/y"),
                       lambda: self.maintenance.git_update("comfyui-arkennemasis", "a" * 40)]:
            with self.assertRaisesRegex(MaintenanceError, "active MCP"):
                action()

    def test_git_reads_and_branch_do_not_change_worktree(self):
        path = self.repo()
        status = self.maintenance.git_status("example")
        self.assertTrue(status["clean"])
        self.assertEqual(self.maintenance.git_log("example")["commits"][0]["subject"], "Initial")
        (path / "node.py").write_text("value = 2\n")
        self.assertIn("+value = 2", self.maintenance.git_diff("example", "node.py")["diff"])
        created = self.maintenance.git_branch("example", "development/test", status["head"])
        self.assertFalse(created["checked_out"])
        self.assertEqual(self.maintenance.git_status("example")["branch"], status["branch"])
        with self.assertRaises(DevelopmentConflict):
            self.maintenance.git_branch("example", "second", "0" * 40)

    def test_git_commit_preserves_unrelated_staged_changes_and_skips_hook(self):
        path = self.repo()
        head = self.maintenance.git_status("example")["head"]
        (path / "other.py").write_text("human = 2\n")
        self.git(path, "add", "other.py")
        (path / "node.py").write_text("ai = 3\n")
        hook = path / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 99\n")
        hook.chmod(0o755)
        result = self.maintenance.git_commit("example", ["node.py"], "Node change", head)
        self.assertNotEqual(result["head"], head)
        self.assertEqual(self.git(path, "show", "HEAD:node.py"), "ai = 3")
        self.assertEqual(self.git(path, "show", "HEAD:other.py"), "value = 1")
        self.assertEqual(self.git(path, "diff", "--cached", "--name-only"), "other.py")

    def test_git_commit_new_file_and_deleted_file(self):
        path = self.repo()
        head = self.maintenance.git_status("example")["head"]
        (path / "new.py").write_text("new = 1\n")
        (path / "node.py").unlink()
        self.maintenance.git_commit("example", ["new.py", "node.py"], "Replace", head)
        self.assertEqual(self.git(path, "ls-tree", "--name-only", "HEAD"), "new.py\nother.py")

    def test_git_filter_is_disabled_for_commit(self):
        path = self.repo()
        (path / ".gitattributes").write_text("*.py filter=danger\n")
        self.git(path, "config", "filter.danger.clean", "exit 99")
        self.git(path, "config", "filter.danger.required", "true")
        (path / "node.py").write_text("value = 7\n")
        head = self.maintenance.git_status("example")["head"]
        self.maintenance.git_commit("example", ["node.py"], "Safe filter override", head)
        self.assertEqual(self.git(path, "show", "HEAD:node.py"), "value = 7")

    def test_git_update_refuses_dirty_tree_before_network(self):
        path = self.repo()
        (path / "node.py").write_text("changed = True\n")
        head = self.maintenance.git_status("example")["head"]
        with self.assertRaisesRegex(DevelopmentConflict, "local or staged"):
            self.maintenance.git_update("example", head)

    def test_git_rejects_protected_paths_and_filters_status(self):
        path = self.repo("comfyui-arkennemasis")
        (path / "mcp_service").mkdir()
        (path / "mcp_service" / "auth.py").write_text("private = True\n")
        status = self.maintenance.git_status("comfyui-arkennemasis")
        self.assertEqual(status["excluded_changes"], 1)
        self.assertFalse(status["files"])
        with self.assertRaises(DevelopmentError):
            self.maintenance.git_diff("comfyui-arkennemasis", "mcp_service/auth.py")
        with self.assertRaises(DevelopmentError):
            self.maintenance.git_commit("comfyui-arkennemasis", ["mcp_service/auth.py"], "bad", status["head"])

    def test_git_rejects_url_rewrites_and_config_includes(self):
        path = self.repo()
        self.git(path, "config", "url.https://other.example/.insteadOf", "https://github.com/")
        with self.assertRaisesRegex(MaintenanceError, "URL rewrites"):
            self.maintenance.git_status("example")

    def test_plan_is_nonmutating_and_records_before_manifest(self):
        plan = self.make_plan()
        self.assertEqual(plan["status"], "planned")
        self.assertNotIn("before", plan)
        saved = json.loads(self.maintenance._plan_file(plan["plan_id"]).read_text())
        self.assertEqual(saved["before"], self.manifest())
        self.assertEqual(saved["changes"][0]["sha256"], "a" * 64)

    def test_apply_pins_reported_wheel_hashes_and_does_not_resolve_again(self):
        plan = self.make_plan()
        with patch.object(self.maintenance, "_manifest", return_value=self.manifest()), patch.object(self.maintenance, "_pip", return_value=(0, "OK")) as pip:
            result = self.maintenance.package_apply(plan["plan_id"])
        install = pip.call_args_list[0].args[0]
        self.assertIn("--no-deps", install)
        self.assertIn("--require-hashes", install)
        self.assertIn("--no-index", install)
        lock = Path(install[install.index("--requirement") + 1]).read_text()
        self.assertIn("--hash=sha256:" + "a" * 64, lock)
        self.assertEqual(result["status"], "applied")
        self.assertFalse(result["rollback_available"])
        with self.assertRaises(DevelopmentConflict):
            self.maintenance.package_apply(plan["plan_id"])

    def test_protected_package_requires_exact_approval(self):
        plan = self.make_plan([self.wheel("torch", "2.1")])
        self.assertEqual(plan["protected_changes"], ["torch==2.1"])
        with patch.object(self.maintenance, "_manifest", return_value=self.manifest()), patch.object(self.maintenance, "_pip", return_value=(0, "OK")) as pip:
            with self.assertRaisesRegex(MaintenanceError, "protected packages"):
                self.maintenance.package_apply(plan["plan_id"])
            pip.assert_not_called()
            result = self.maintenance.package_apply(plan["plan_id"], ["torch==2.1"])
            self.assertEqual(result["status"], "applied")

    def test_environment_drift_blocks_apply(self):
        plan = self.make_plan()
        with patch.object(self.maintenance, "_manifest", return_value=self.manifest([{"name": "torch", "version": "different"}])), patch.object(self.maintenance, "_pip") as pip:
            with self.assertRaisesRegex(DevelopmentConflict, "environment changed"):
                self.maintenance.package_apply(plan["plan_id"])
            pip.assert_not_called()

    def test_source_archives_and_external_wheels_are_rejected(self):
        for url in ["https://files.pythonhosted.org/x.tar.gz", "https://example.com/x.whl", "https://token@files.pythonhosted.org/x.whl"]:
            wheel = self.wheel()
            wheel["download_info"]["url"] = url
            with self.subTest(url=url), self.assertRaises(MaintenanceError):
                self.make_plan([wheel])

    def test_failed_install_is_persisted_and_not_automatically_replayed(self):
        plan = self.make_plan()
        with patch.object(self.maintenance, "_manifest", return_value=self.manifest()), patch.object(self.maintenance, "_pip", side_effect=[(1, "install failed password=secret"), (0, "OK")]):
            result = self.maintenance.package_apply(plan["plan_id"])
        self.assertEqual(result["status"], "failed")
        self.assertNotIn("password=secret", result["log"])
        with self.assertRaises(DevelopmentConflict):
            self.maintenance.package_apply(plan["plan_id"])

    def test_environment_strips_command_and_installer_overrides(self):
        with patch.dict(os.environ, {"GIT_SSH_COMMAND": "malicious", "PIP_TARGET": "outside", "PYTHONPATH": "outside"}):
            env = Maintenance._environment()
        self.assertNotIn("GIT_SSH_COMMAND", env)
        self.assertNotIn("PIP_TARGET", env)
        self.assertNotIn("PYTHONPATH", env)
        self.assertEqual(env["GIT_CONFIG_GLOBAL"], os.devnull)


if __name__ == "__main__":
    unittest.main()
