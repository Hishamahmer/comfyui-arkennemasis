from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest

from mcp_service.workflows import RevisionConflict, WorkflowError, WorkflowStore, apply_patch, workflow_format, workflow_revision


UI = {"nodes": [{"id": 1, "type": "Example", "widgets_values": ["before"]}], "links": [], "version": 0.4}
API = {"1": {"class_type": "Example", "inputs": {"text": "before"}}}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "workflows"
        self.backups = Path(self.temp.name) / "backups"
        self.store = WorkflowStore(self.root, self.backups)

    def tearDown(self):
        self.temp.cleanup()

    def test_create_read_list_preserve_formats(self):
        self.store.write("nested/ui.json", UI)
        self.store.write("api.json", API)
        self.assertEqual(self.store.read("nested/ui.json")["workflow"], UI)
        self.assertEqual([(x["name"], x["format"]) for x in self.store.list()], [("api.json", "api"), ("nested/ui.json", "ui")])
        self.assertEqual(workflow_format({"nodes": [], "links": []}), "ui")

    def test_overwrite_requires_current_revision_and_creates_restorable_backup(self):
        created = self.store.write("test.json", UI)
        changed = deepcopy(UI)
        changed["nodes"][0]["widgets_values"][0] = "after"
        for revision in (None, "stale"):
            with self.assertRaises(RevisionConflict):
                self.store.write("test.json", changed, revision)
        saved = self.store.write("test.json", changed, created["revision"])
        self.assertEqual(saved["backup_id"], created["revision"])
        restored = self.store.restore("test.json", saved["backup_id"], saved["revision"])
        self.assertEqual(restored["workflow"], UI)
        self.assertEqual(restored["backup_id"], saved["revision"])

    def test_revisions_ignore_json_whitespace_and_key_order(self):
        created = self.store.write("test.json", UI)
        (self.root / "test.json").write_text(json.dumps(UI, indent=4), encoding="utf-8")
        self.assertEqual(self.store.read("test.json")["revision"], created["revision"])

    def test_patch_failure_leaves_file_unchanged(self):
        created = self.store.write("test.json", API)
        ops = [{"op": "replace", "path": "/1/inputs/text", "value": "changed"}, {"op": "test", "path": "/1/inputs/text", "value": "before"}]
        with self.assertRaises(RevisionConflict):
            self.store.patch("test.json", ops, created["revision"])
        self.assertEqual(self.store.read("test.json"), created)

    def test_patch_array_add_remove_and_pointer_escapes(self):
        graph = {"1": {"class_type": "Example", "inputs": {"a/b~c": [1, 2]}}}
        edited = apply_patch(graph, [{"op": "add", "path": "/1/inputs/a~1b~0c/-", "value": 3}, {"op": "remove", "path": "/1/inputs/a~1b~0c/0"}])
        self.assertEqual(edited["1"]["inputs"]["a/b~c"], [2, 3])
        self.assertEqual(graph["1"]["inputs"]["a/b~c"], [1, 2])

    def test_patch_invalid_index_and_boolean_test(self):
        for path in ("/nodes/01", "/nodes/-1", "/nodes/2", "/nodes/~2"):
            with self.subTest(path=path), self.assertRaises(WorkflowError):
                apply_patch(UI, [{"op": "remove", "path": path}])
        graph = {"1": {"class_type": "Example", "inputs": {"flag": True}}}
        with self.assertRaises(RevisionConflict):
            apply_patch(graph, [{"op": "test", "path": "/1/inputs/flag", "value": 1}])

    def test_rejects_path_traversal_and_windows_special_names(self):
        for name in ("../escape.json", "/absolute.json", "C:/file.json", "a\\b.json", "a//b.json", "CON.json", "file.json:stream", "dir./x.json", "a/../x.json", "thing.txt"):
            with self.subTest(name=name), self.assertRaises(WorkflowError):
                self.store.write(name, UI)

    def test_rejects_symlink_escape(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host.")
        with self.assertRaises(WorkflowError):
            self.store.write("linked/escape.json", UI)
        self.assertFalse((outside / "escape.json").exists())

    def test_independent_store_writers_cannot_both_overwrite_same_revision(self):
        created = self.store.write("test.json", API)
        second = WorkflowStore(self.root, self.backups)
        barrier = threading.Barrier(2)

        def save(store, value):
            barrier.wait(timeout=5)
            try:
                store.patch("test.json", [{"op": "replace", "path": "/1/inputs/text", "value": value}], created["revision"])
                return "saved"
            except RevisionConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            one = pool.submit(save, self.store, "one")
            two = pool.submit(save, second, "two")
            self.assertCountEqual([one.result(timeout=15), two.result(timeout=15)], ["saved", "conflict"])

    def test_nan_and_corrupt_backup_rejected(self):
        graph = deepcopy(API)
        graph["1"]["inputs"]["number"] = float("nan")
        with self.assertRaises(WorkflowError):
            self.store.write("nan.json", graph)
        created = self.store.write("test.json", API)
        changed = self.store.patch("test.json", [{"op": "replace", "path": "/1/inputs/text", "value": "after"}], created["revision"])
        backup = next(self.backups.rglob("*.json"))
        backup.write_text(json.dumps(UI), encoding="utf-8")
        with self.assertRaises(WorkflowError):
            self.store.restore("test.json", changed["backup_id"], changed["revision"])


if __name__ == "__main__":
    unittest.main()
