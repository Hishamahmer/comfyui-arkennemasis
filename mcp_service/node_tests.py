"""Run one selected unittest file using ComfyUI's Python, never a caller command.

Enabled node tests execute with ComfyUI's operating-system permissions. This is
an explicit development operation, not an operating-system sandbox.
"""

import os
from pathlib import Path
import re
import subprocess
import threading
import time

from .development import DevelopmentError, _root
from .maintenance import _redact


MAX_OUTPUT = 64 * 1024
TIMEOUT_SECONDS = 110
ENVIRONMENT_KEYS = {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
                    "LANG", "LC_ALL", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
                    "CUDA_VISIBLE_DEVICES", "CUDA_DEVICE_ORDER"}
RUNNER = '''import importlib.util
from pathlib import Path
import sys
import unittest

test_path, pack_root, comfy_root = (Path(value) for value in sys.argv[1:])
sys.path[:0] = [str(pack_root), str(test_path.parent), str(comfy_root)]
module_name = ".".join(test_path.relative_to(pack_root).with_suffix("").parts)
spec = importlib.util.spec_from_file_location(module_name, test_path)
module = importlib.util.module_from_spec(spec)
sys.modules[module_name] = module
spec.loader.exec_module(module)
suite = unittest.defaultTestLoader.loadTestsFromModule(module)
if suite.countTestCases() == 0:
    print("No unittest test cases were found in the selected file.", file=sys.stderr)
    raise SystemExit(5)
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(0 if result.wasSuccessful() else 1)
'''


class NodeTests:
    def __init__(self, workspace, comfy_python, state_dir):
        self.workspace = workspace
        # Resolving this invocation path would bypass ordinary Unix venvs.
        self.python = Path(os.path.abspath(comfy_python))
        if not self.python.is_file():
            raise DevelopmentError("Configure the Python executable actually used by ComfyUI.")
        self.state = _root(Path(state_dir) / "node-tests")
        self.state.mkdir(parents=True, exist_ok=True)

    def run(self, pack, relative_test_file):
        folder = self.workspace.pack_path(pack)
        source = self.workspace.resolve(f"{pack}/{relative_test_file}")
        if not re.fullmatch(r"test_[A-Za-z0-9_]+\.py", source.name):
            raise DevelopmentError("Select one existing test_<name>.py unittest file in an enabled node pack.")
        runner = self.state / "runner.py"
        self.workspace._atomic_write(runner, RUNNER.encode("utf-8"))
        args = [str(self.python), "-I", "-u", "-B", "-X", "utf8", str(runner),
                str(source), str(folder), str(self.workspace.root.parent)]
        env = {key: value for key, value in os.environ.items() if key.upper() in ENVIRONMENT_KEYS}
        started = time.monotonic()
        process = subprocess.Popen(args, cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        captured = bytearray()
        overflow = threading.Event()
        read_failed = threading.Event()

        def read_output():
            try:
                while chunk := process.stdout.read(8192):
                    remaining = MAX_OUTPUT - len(captured)
                    captured.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
                        process.kill()
                        break
            except (OSError, ValueError):
                read_failed.set()
            finally:
                process.stdout.close()

        reader = threading.Thread(target=read_output, name="ark-node-test-output", daemon=True)
        reader.start()
        status = "failed"
        note = ""
        try:
            process.wait(timeout=TIMEOUT_SECONDS)
            status = "success" if process.returncode == 0 else "failed"
        except subprocess.TimeoutExpired:
            status = "timeout"
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                note = "The test runner did not confirm stopping; inspect local processes before retrying."
        finally:
            reader.join(timeout=3)
        if overflow.is_set():
            status = "output_limit"
        elif reader.is_alive() or read_failed.is_set():
            status = "timeout" if status == "timeout" else "failed"
            note = note or "Test output did not close cleanly; a test may have launched a child process."
        output = _redact(captured.decode("utf-8", errors="replace"))
        output_bytes = output.encode("utf-8")
        return {"pack": pack, "test_file": relative_test_file, "status": status, "success": status == "success",
                "returncode": process.returncode, "output": output_bytes[:MAX_OUTPUT].decode("utf-8", errors="ignore"),
                "output_truncated": overflow.is_set() or len(output_bytes) > MAX_OUTPUT,
                "timeout_seconds": TIMEOUT_SECONDS, "duration_seconds": round(time.monotonic() - started, 3),
                "note": note}
