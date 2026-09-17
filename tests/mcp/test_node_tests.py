import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from mcp_service.development import DevelopmentError, NodeWorkspace
from mcp_service.node_tests import MAX_OUTPUT, NodeTests, RUNNER, TIMEOUT_SECONDS


class NodeTestRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "ComfyUI" / "custom_nodes"
        self.pack = self.root / "example"
        self.pack.mkdir(parents=True)
        self.source = self.pack / "test_example.py"
        self.source.write_text("raise AssertionError('fixture must never execute in these tests')\n")
        self.state = Path(self.temp.name) / "state"
        self.workspace = NodeWorkspace(self.root, self.state, ["example", "comfyui-arkennemasis"])
        self.runner = NodeTests(self.workspace, sys.executable, self.state)

    def process(self, output=b"", returncode=0):
        process = Mock()
        process.stdout = io.BytesIO(output)
        process.returncode = returncode
        return process

    def test_selected_file_uses_fixed_helper_cwd_and_python(self):
        process = self.process(b"Ran 1 test\nOK\n")
        with patch("mcp_service.node_tests.subprocess.Popen", return_value=process) as popen:
            result = self.runner.run("example", "test_example.py")
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["success"])
        args = popen.call_args.args[0]
        self.assertEqual(args[0], sys.executable)
        self.assertEqual(args[-3:], [str(self.source), str(self.pack), str(self.root.parent)])
        self.assertEqual(Path(args[-4]).read_text(), RUNNER)
        self.assertEqual(popen.call_args.kwargs["cwd"], self.pack)
        self.assertNotIn("shell", popen.call_args.kwargs)
        self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
        process.wait.assert_called_once_with(timeout=TIMEOUT_SECONDS)
        self.assertLessEqual(TIMEOUT_SECONDS + 6, 120)

    def test_rejects_non_tests_missing_files_other_packs_and_outside_paths(self):
        (self.pack / "ordinary.py").write_text("pass")
        (self.pack / "test_[a].py").write_text("pass")
        for pack, path in [("example", "ordinary.py"), ("example", "missing_test.py"),
                           ("example", "test_[a].py"), ("disabled", "test_example.py"),
                           ("example", "../test_example.py"), ("example", str(self.source)),
                           ("example", "test_example.py:stream"),
                           ("comfyui-arkennemasis", "mcp_service/test_auth.py")]:
            with self.subTest(pack=pack, path=path), patch("mcp_service.node_tests.subprocess.Popen") as popen:
                with self.assertRaises((DevelopmentError, FileNotFoundError)):
                    self.runner.run(pack, path)
                popen.assert_not_called()

    def test_nonzero_test_exit_reports_failure_and_redacts_output(self):
        process = self.process(b'FAILED token="EXAMPLE ONLY" https://ghp_EXAMPLE@github.com/owner/repo', 1)
        with patch("mcp_service.node_tests.subprocess.Popen", return_value=process):
            result = self.runner.run("example", "test_example.py")
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["success"])
        self.assertNotIn("EXAMPLE", result["output"])
        self.assertIn("[redacted]", result["output"])

    def test_timeout_kills_runner_and_returns_bounded_result(self):
        process = self.process(b"started\n", None)
        process.wait.side_effect = [subprocess.TimeoutExpired("fixed runner", TIMEOUT_SECONDS), None]
        with patch("mcp_service.node_tests.subprocess.Popen", return_value=process):
            result = self.runner.run("example", "test_example.py")
        self.assertEqual(result["status"], "timeout")
        self.assertFalse(result["success"])
        process.kill.assert_called_once()
        self.assertEqual(process.wait.call_args.kwargs["timeout"], 3)

    def test_excessive_output_stops_runner_and_is_bounded(self):
        process = self.process(b"x" * (MAX_OUTPUT + 8192), -1)
        with patch("mcp_service.node_tests.subprocess.Popen", return_value=process):
            result = self.runner.run("example", "test_example.py")
        self.assertEqual(result["status"], "output_limit")
        self.assertTrue(result["output_truncated"])
        self.assertEqual(len(result["output"].encode()), MAX_OUTPUT)
        process.kill.assert_called_once()

    def test_utf8_replacement_and_redaction_expansion_stay_bounded(self):
        process = self.process(b"\xff" * MAX_OUTPUT)
        with patch("mcp_service.node_tests.subprocess.Popen", return_value=process):
            result = self.runner.run("example", "test_example.py")
        self.assertLessEqual(len(result["output"].encode()), MAX_OUTPUT)
        self.assertTrue(result["output_truncated"])

    def test_environment_excludes_credentials_and_python_overrides(self):
        environment = {"PATH": "configured", "SYSTEMROOT": "system", "OPENAI_API_KEY": "private",
                       "PYTHONPATH": "outside", "PIP_INDEX_URL": "private", "GIT_ASKPASS": "arbitrary",
                       "CUDA_VISIBLE_DEVICES": "0"}
        with patch.dict(os.environ, environment, clear=True), patch("mcp_service.node_tests.subprocess.Popen", return_value=self.process()) as popen:
            self.runner.run("example", "test_example.py")
        self.assertEqual(popen.call_args.kwargs["env"], {key: environment[key] for key in ("PATH", "SYSTEMROOT", "CUDA_VISIBLE_DEVICES")})

    def test_venv_invocation_path_is_retained(self):
        executable = Path(self.temp.name) / "venv/bin/python"
        executable.parent.mkdir(parents=True)
        try:
            executable.symlink_to(sys.executable)
        except OSError:
            self.skipTest("Interpreter symlink creation requires privileges on this host")
        runner = NodeTests(self.workspace, executable, self.state)
        with patch("mcp_service.node_tests.subprocess.Popen", return_value=self.process()) as popen:
            runner.run("example", "test_example.py")
        self.assertEqual(popen.call_args.args[0][0], str(executable))

    def test_helper_loads_only_the_selected_file_without_discovery_globs(self):
        self.assertIn("spec_from_file_location(module_name, test_path)", RUNNER)
        self.assertIn("loadTestsFromModule(module)", RUNNER)
        self.assertNotIn("discover(", RUNNER)
        self.assertIn("suite.countTestCases() == 0", RUNNER)


if __name__ == "__main__":
    unittest.main()
