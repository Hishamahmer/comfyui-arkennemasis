from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mcp_service.development import DevelopmentConflict, DevelopmentError, MAX_FILE_BYTES, NodeWorkspace


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "custom_nodes"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "state"
        self.allowed = ["example", "new_pack", "comfyui-arkennemasis"]
        self.workspace = NodeWorkspace(self.root, self.state, self.allowed)

    def tearDown(self):
        self.temp.cleanup()

    def create(self, path="example/node.py", content="value = 1\n"):
        return self.workspace.write(path, content)

    def test_create_and_bounded_read_with_byte_revision(self):
        result = self.create(content="one\r\ntwo\r\nthree\r\n")
        result = self.workspace.read(result["path"], 2, 1)
        self.assertEqual(result["content"], "two\r\n")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["total_lines"], 3)
        self.assertEqual(result["revision"], hashlib.sha256(b"one\r\ntwo\r\nthree\r\n").hexdigest())

    def test_edit_conflict_and_lossless_restore(self):
        created = self.create(content="\ufeffvalue = 1\r\n")
        original = (self.root / created["path"]).read_bytes()
        for expected in (None, "wrong"):
            with self.assertRaises(DevelopmentConflict):
                self.workspace.write(created["path"], "value = 2\n", expected)
        changed = self.workspace.write(created["path"], "value = 2\n", created["revision"])
        self.assertEqual(changed["backup_id"], created["revision"])
        self.assertIn("+value = 2", changed["diff"])
        restored = self.workspace.restore(created["path"], changed["backup_id"], changed["revision"])
        self.assertEqual(restored["revision"], created["revision"])
        self.assertEqual((self.root / created["path"]).read_bytes(), original)

    def test_external_edit_is_not_overwritten(self):
        created = self.create()
        (self.root / created["path"]).write_bytes(b"human = 3\n")
        with self.assertRaises(DevelopmentConflict):
            self.workspace.write(created["path"], "ai = 4\n", created["revision"])
        self.assertEqual(self.workspace.read(created["path"])["content"], "human = 3\n")

    def test_independent_writers_cannot_apply_same_revision_twice(self):
        created = self.create()
        other = NodeWorkspace(self.root, self.state, self.allowed)

        def edit(workspace, content):
            try:
                return workspace.write(created["path"], content, created["revision"])
            except DevelopmentConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(edit, self.workspace, "value = 2\n"), executor.submit(edit, other, "value = 3\n")]
            results = [future.result() for future in futures]
        self.assertEqual(sum(result == "conflict" for result in results), 1)

    def test_exact_patch_rejects_ambiguous_match(self):
        created = self.create(content="a = 1\nb = 1\n")
        with self.assertRaises(DevelopmentConflict):
            self.workspace.patch(created["path"], "1", "2", created["revision"])
        changed = self.workspace.patch(created["path"], "a = 1", "a = 2", created["revision"])
        self.assertIn("+a = 2", changed["diff"])
        self.assertEqual(self.workspace.read(created["path"])["content"], "a = 2\nb = 1\n")

    def test_delete_is_recoverable_and_restore_does_not_overwrite_new_file(self):
        created = self.create()
        deleted = self.workspace.delete(created["path"], created["revision"])
        self.assertFalse((self.root / created["path"]).exists())
        restored = self.workspace.restore(created["path"], deleted["backup_id"])
        self.assertEqual(restored["revision"], created["revision"])
        with self.assertRaises(DevelopmentConflict):
            self.workspace.restore(created["path"], deleted["backup_id"])

    def test_rename_preserves_existing_target_and_backup(self):
        created = self.create()
        self.create("example/target.py", "target = 1\n")
        with self.assertRaises(DevelopmentConflict):
            self.workspace.rename(created["path"], "example/target.py", created["revision"])
        renamed = self.workspace.rename(created["path"], "example/nested/renamed.py", created["revision"])
        self.assertFalse((self.root / created["path"]).exists())
        self.assertEqual(renamed["revision"], created["revision"])
        self.assertEqual(len(self.workspace.history(created["path"])["backups"]), 1)
        self.workspace.restore(created["path"], renamed["backup_id"])

    def test_stale_revision_delete_and_rename_leave_files_untouched(self):
        created = self.create()
        with self.assertRaises(DevelopmentConflict):
            self.workspace.delete(created["path"], "stale")
        with self.assertRaises(DevelopmentConflict):
            self.workspace.rename(created["path"], "example/renamed.py", "stale")
        self.assertEqual(self.workspace.read(created["path"])["revision"], created["revision"])
        self.assertFalse((self.root / "example/renamed.py").exists())

    def test_rejects_unsafe_paths_sensitive_files_and_unselected_packs(self):
        paths = ["../node.py", "/example/node.py", "C:/example/node.py", "example/../node.py", "example\\node.py", "example//node.py",
                 "other/node.py", "example/CON.py", "example/COM1.txt", "example/a.py:stream", "example/a./node.py", "example/a /node.py",
                 "example/.env", "example/.git/config", "example/.local/config.json", "example/.runtime/file.py", "example/venv/file.py",
                 "example/credentials.json", "example/secrets.py", "example/api_key.txt", "example/auth_token.json", "example/AUTH_T~1.JSON",
                 "example/node.dll", "example/node.safetensors", "example/node.py\0", "example/node_modules/test.js"]
        for path in paths:
            with self.subTest(path=path), self.assertRaises(DevelopmentError):
                self.workspace.write(path, "test")
        self.assertEqual(self.workspace.list()["files"], [])

    def test_own_enforcement_paths_are_protected_but_other_nodes_work(self):
        for path in ["comfyui-arkennemasis/mcp_service/server.py", "comfyui-arkennemasis/MCP_SERVICE/server.py", "comfyui-arkennemasis/web/mcp/bridge.js"]:
            with self.subTest(path=path), self.assertRaises(DevelopmentError):
                self.workspace.write(path, "test")
        self.workspace.write("comfyui-arkennemasis/common/example.py", "value = 1\n")
        self.assertEqual(len(self.workspace.list("comfyui-arkennemasis")["files"]), 1)

    def test_private_repository_paths_are_excluded_from_all_source_access(self):
        for relative in ["realestate/node.py", "hairstyle/node.py", "thumbnail/node.py", "ecom/node.py", "vendor/node.py",
                         "variation/prompts_local.py", "web/realestate.js", "web/hairstyle.js", "web/thumbnail.js", "web/ecom.js",
                         "example workflows/example.json"]:
            path = f"comfyui-arkennemasis/{relative}"
            disk = self.root / path
            disk.parent.mkdir(parents=True, exist_ok=True)
            disk.write_text("private", encoding="utf-8")
            with self.subTest(path=path), self.assertRaises(DevelopmentError):
                self.workspace.read(path)
        self.assertEqual(self.workspace.list("comfyui-arkennemasis")["files"], [])
        self.assertEqual(self.workspace.search("private")["matches"], [])

    def test_public_resolvers_require_selected_safe_paths(self):
        self.create()
        self.assertEqual(self.workspace.pack_path("example"), self.root / "example")
        self.assertEqual(self.workspace.resolve("example/node.py"), self.root / "example/node.py")
        self.assertEqual(self.workspace.resolve("example/missing.py", must_exist=False), self.root / "example/missing.py")
        with self.assertRaises(DevelopmentError):
            self.workspace.resolve("example/missing.py")
        with self.assertRaises(DevelopmentError):
            self.workspace.pack_path("example/nested")

    def test_listing_and_search_exclude_protected_existing_files(self):
        self.create()
        pack = self.root / "example"
        (pack / "secrets.json").write_text('{"secret":"needle"}', encoding="utf-8")
        (pack / ".local").mkdir()
        (pack / ".local" / "node.py").write_text("needle = 1", encoding="utf-8")
        (pack / "large.bin").write_bytes(b"needle")
        self.assertEqual([item["path"] for item in self.workspace.list("example")["files"]], ["example/node.py"])
        self.assertEqual(self.workspace.search("needle")["matches"], [])
        self.assertEqual(self.workspace.search("value")["matches"][0]["line"], 1)

    def test_list_search_and_scan_limits_are_reported(self):
        for i in range(5):
            self.create(f"example/node{i}.py", "needle = 1\n")
        self.assertTrue(self.workspace.list(limit=2)["truncated"])
        self.assertTrue(self.workspace.search("needle", limit=2)["truncated"])
        with patch("mcp_service.development.MAX_SCAN_ENTRIES", 2):
            self.assertTrue(self.workspace.list()["truncated"])
            self.assertTrue(self.workspace.search("absent")["truncated"])

    def test_invalid_inputs_and_binary_or_large_content(self):
        for content in (None, "bad\0file", "x" * (MAX_FILE_BYTES + 1)):
            with self.subTest(content_type=type(content)), self.assertRaises(DevelopmentError):
                self.workspace.write("example/node.py", content)
        self.create()
        for args in [(0, 3), (1, 1001), (True, 3)]:
            with self.assertRaises(DevelopmentError):
                self.workspace.read("example/node.py", *args)
        (self.root / "example/binary.py").write_bytes(b"\xff")
        with self.assertRaises(DevelopmentError):
            self.workspace.read("example/binary.py")

    def test_validation_is_static_and_distinguishes_unsupported_languages(self):
        source = "raise RuntimeError('must never execute')\nclass Example: pass\nNODE_CLASS_MAPPINGS = {'Example': Example}\n"
        created = self.create(content=source)
        result = self.workspace.validate(created["path"])
        self.assertTrue(result["valid"])
        self.assertTrue(result["declares_node_mapping"])
        self.assertFalse(result["executed"])
        self.assertFalse(result["registration_verified"])
        self.workspace.write(created["path"], "class Broken(\n", created["revision"])
        self.assertFalse(self.workspace.validate(created["path"])["valid"])
        self.create("example/node.js", "this is invalid JavaScript")
        self.assertIsNone(self.workspace.validate("example/node.js")["valid"])
        self.create("example/config.json", '{"value": 1}')
        self.assertTrue(self.workspace.validate("example/config.json")["valid"])
        self.create("example/settings.toml", "[section\n")
        self.assertFalse(self.workspace.validate("example/settings.toml")["valid"])

    def test_scaffold_is_new_pack_only_and_registered_statically(self):
        result = self.workspace.scaffold("new_pack", "ExampleNode", "Example node")
        self.assertEqual(len(result["files"]), 3)
        self.assertTrue(self.workspace.validate("new_pack/__init__.py")["declares_node_mapping"])
        with self.assertRaises(DevelopmentConflict):
            self.workspace.scaffold("new_pack", "DifferentNode")
        with self.assertRaises(DevelopmentError):
            self.workspace.scaffold("unselected", "Node")
        with self.assertRaises(DevelopmentError):
            self.workspace.scaffold("example", "pass")

    def test_scaffold_failure_removes_only_its_created_files(self):
        original = self.workspace._atomic_write
        count = 0

        def fail_second(path, raw, create=False):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("disk error")
            return original(path, raw, create)

        with patch.object(self.workspace, "_atomic_write", side_effect=fail_second), self.assertRaises(OSError):
            self.workspace.scaffold("new_pack", "ExampleNode")
        self.assertFalse((self.root / "new_pack").exists())

    def test_backup_tampering_is_detected(self):
        created = self.create()
        changed = self.workspace.write(created["path"], "value = 2\n", created["revision"])
        backup = next(self.workspace.backups.glob("*/*.bin"))
        backup.write_bytes(b"tampered = 1\n")
        with self.assertRaises(DevelopmentError):
            self.workspace.restore(created["path"], changed["backup_id"], changed["revision"])
        self.assertEqual(self.workspace.read(created["path"])["revision"], changed["revision"])

    def test_linked_files_and_directories_are_rejected(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        (outside / "node.py").write_text("private = 1\n", encoding="utf-8")
        (self.root / "example").mkdir()
        try:
            (self.root / "example/link").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host.")
        with self.assertRaises(DevelopmentError):
            self.workspace.read("example/link/node.py")
        self.assertEqual(self.workspace.list()["files"], [])

    def test_hardlink_alias_is_rejected(self):
        self.create()
        original = self.root / "example/node.py"
        alias = self.root / "example/alias.py"
        os.link(original, alias)
        with self.assertRaises(DevelopmentError):
            self.workspace.read("example/alias.py")
        with self.assertRaises(DevelopmentError):
            self.workspace.write("example/alias.py", "change", "bad")


if __name__ == "__main__":
    unittest.main()
