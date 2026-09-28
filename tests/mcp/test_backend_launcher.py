import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from mcp_service import launch_backend


class BackendLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ark-launcher-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = self.root / "ComfyUI" / "custom_nodes" / "test-pack" / "mcp_service"
        self.state = self.service / ".local"
        self.state.mkdir(parents=True)
        self.records = self.state / "backend-restarts.json"

    def record(self, **changes):
        record = {"request_id": str(uuid.uuid4()), "accepted": True, "state": "restarting",
                  "pid": 123, "requested_at": time.time(), **changes}
        self.records.write_text(json.dumps([record]))
        return record

    def test_reserved_exit_requires_fresh_matching_unconsumed_record(self):
        record = self.record()
        self.assertEqual(launch_backend.pending_restart(self.records, 123, set()), record["request_id"])
        self.assertIsNone(launch_backend.pending_restart(self.records, 124, set()))
        self.assertIsNone(launch_backend.pending_restart(self.records, 123, {record["request_id"]}))
        for changes in ({"requested_at": time.time() - 181}, {"state": "scheduled"}, {"accepted": False},
                        {"request_id": "../outside"}, {"pid": True}, {"requested_at": float("nan")}):
            self.record(**changes)
            self.assertIsNone(launch_backend.pending_restart(self.records, 123, set()))

    def test_supervisor_remains_alive_until_replacement_child_finishes(self):
        shutil.copyfile(launch_backend.__file__, self.service / "launch_backend.py")
        worker = '''import json,os,sys,time,uuid
from pathlib import Path
state=Path(__file__).parent/".local"
events=state/"events.json"
rows=json.loads(events.read_text()) if events.exists() else []
rows.append({"pid":os.getpid(),"parent":os.getppid(),"args":sys.argv[1:]})
events.write_text(json.dumps(rows))
if len(rows)==1:
    (state/"backend-restarts.json").write_text(json.dumps([{"request_id":str(uuid.uuid4()),"state":"restarting","accepted":True,"pid":os.getpid(),"requested_at":time.time()}]))
    raise SystemExit(75)
time.sleep(0.5)
(state/"finished").write_text("done")
'''
        (self.service / "backend_worker.py").write_text(worker)
        arguments = ["--output-directory", "a path with spaces", "--cpu"]
        process = subprocess.Popen([sys.executable, str(self.service / "launch_backend.py"), *arguments],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        output, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error.decode())
        rows = json.loads((self.state / "events.json").read_text())
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["parent"] for row in rows}, {process.pid})
        self.assertNotEqual(rows[0]["pid"], rows[1]["pid"])
        self.assertTrue((self.state / "finished").exists())
        self.assertEqual([row["args"] for row in rows], [arguments, arguments])
        self.assertIn(b"AI connection stays open", output)

    def test_crash_and_unrequested_reserved_exit_are_not_restarted(self):
        for code in (1, 75):
            child = unittest.mock.Mock(pid=123)
            child.wait.return_value = code
            with patch.object(launch_backend.subprocess, "Popen", return_value=child) as spawn, \
                    patch.object(launch_backend, "__file__", str(self.service / "launch_backend.py")), \
                    patch.object(launch_backend.sys, "argv", ["launch_backend.py", "--cpu"]):
                self.assertEqual(launch_backend.main(), code)
                spawn.assert_called_once()
                self.assertEqual(spawn.call_args.args[0][2], str(self.service / "backend_worker.py"))
                self.assertFalse(spawn.call_args.kwargs["shell"])

    def test_worker_uses_fixed_installation_main_and_preserves_original_args(self):
        source = Path(launch_backend.__file__).parent
        for name in ("launch_backend.py", "backend_worker.py", "launch_config.py"):
            shutil.copyfile(source / name, self.service / name)
        (self.service / "__init__.py").write_text("")
        comfy = self.root / "ComfyUI"
        (comfy / "comfy").mkdir()
        (comfy / "comfy" / "cli_args.py").write_text("parser.add_argument('--cpu')\nparser.add_argument('--normalvram')\n")
        observed = self.state / "worker-observed.json"
        (comfy / "main.py").write_text('''import json,os,sys
from pathlib import Path
target=Path(__file__).parent/"custom_nodes"/"test-pack"/"mcp_service"/".local"/"worker-observed.json"
target.write_text(json.dumps({"script":sys.argv[0],"args":sys.argv[1:],"original":sys._arkennemasis_base_comfy_args,"supervised":sys._arkennemasis_supervised,"parent":sys._arkennemasis_supervisor_pid,"environment_removed":"ARK_MCP_SUPERVISOR_PID" not in os.environ}))
''')
        (self.state / "launch-overrides.json").write_text('{"vram_mode":"cpu"}')
        requested = ["--normalvram", "--output-directory", "a path with spaces"]
        process = subprocess.Popen([sys.executable, str(self.service / "launch_backend.py"), *requested],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        _, error = process.communicate(timeout=15)
        self.assertEqual(process.returncode, 0, error.decode())
        result = json.loads(observed.read_text())
        self.assertEqual(result["script"], str(comfy / "main.py"))
        self.assertEqual(result["original"], requested)
        self.assertEqual(result["args"], ["--output-directory", "a path with spaces", "--cpu"])
        self.assertEqual(result["parent"], process.pid)
        self.assertTrue(result["supervised"])
        self.assertTrue(result["environment_removed"])


if __name__ == "__main__":
    unittest.main()
