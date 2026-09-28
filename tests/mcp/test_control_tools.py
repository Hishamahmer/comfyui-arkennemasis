import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from mcp_service.tools_control import register_control_tools


class ControlToolTests(unittest.TestCase):
    def test_old_restart_result_survives_later_restart_and_offline_companion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_id, new_id = str(uuid.uuid4()), str(uuid.uuid4())
            old = {"id": old_id, "state": "ready"}
            (root / "lifecycle-results").mkdir()
            (root / "lifecycle-results" / f"{old_id}.json").write_text(json.dumps(old))
            latest = {"id": new_id, "state": "ready"}
            (root / "lifecycle-result.json").write_text(json.dumps(latest))
            (root / "lifecycle-request.json").write_text(json.dumps({"id": new_id}))
            functions = {}
            def tool(*args, **kwargs):
                def register(fn):
                    functions[fn.__name__] = fn
                    return fn
                return register
            register_control_tools(tool, SimpleNamespace(state_dir=directory), None, None, None, None)
            self.assertEqual(functions["get_restart_status"](old_id), old)
            self.assertEqual(functions["restart_comfyui"](old_id), old)
            self.assertEqual(json.loads((root / "lifecycle-request.json").read_text())["id"], new_id)
            with self.assertRaises(ValueError):
                functions["get_restart_status"]("../config")
