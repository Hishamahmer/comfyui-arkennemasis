import json
from pathlib import Path
import tempfile
import unittest

from mcp_service.launch_config import ARITY, LaunchConfig, LaunchConfigConflict, LaunchConfigError, apply_overrides


class LaunchConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.comfy = self.root / "ComfyUI"
        (self.comfy / "comfy").mkdir(parents=True)
        self.parser = self.comfy / "comfy" / "cli_args.py"
        self.parser.write_text("\n".join(f"parser.add_argument({flag!r})" for flag in ARITY), encoding="utf-8")
        self.state = self.root / "private"
        self.config = LaunchConfig(self.state, self.comfy)

    def save(self, value):
        return self.config.patch(value, self.config.get()["revision"])

    def apply(self, arguments):
        return apply_overrides(arguments, self.state, self.comfy)

    def test_get_does_not_create_settings_and_empty_config_preserves_argv(self):
        args = ["-s", "main.py", "--listen", "127.0.0.1", "--port", "8188", "--fast", "fp16_accumulation"]
        self.assertEqual(self.config.get()["overrides"], {})
        self.assertEqual(self.apply(args), args)
        self.assertFalse(self.state.exists())

    def test_revision_backup_restore_and_conflicts(self):
        initial = self.config.get()["revision"]
        changed = self.config.patch({"vram_mode": "low", "precision": "fp16"}, initial)
        self.assertNotEqual(changed["revision"], initial)
        self.assertIn(initial, changed["backups"])
        with self.assertRaises(LaunchConfigConflict):
            self.config.patch({"vram_mode": "high"}, initial)
        restored = self.config.restore(initial, changed["revision"])
        self.assertEqual(restored["overrides"], {})
        self.assertIn(changed["revision"], restored["backups"])
        self.assertEqual(restored["activation"], "next_explicit_restart_or_launch")

    def test_unsupported_dangerous_keys_and_invalid_values_never_save(self):
        for value in ({"args": ["--listen", "0.0.0.0"]}, {"command": "python"}, {"precision": "fp16; bad"},
                      {"preview_size": True}, {"cache_lru_size": 16}, {"cache_mode": "lru", "cache_lru_size": 0},
                      {"vram_mode": "../../bad"}, {"preview_size": 100000}):
            with self.subTest(value=value), self.assertRaises(LaunchConfigError):
                self.save(value)
        self.assertFalse(self.config.path.exists())

    def test_replace_only_selected_groups_preserving_python_and_other_arguments(self):
        self.save({"vram_mode": "low", "precision": "fp32", "preview_method": "taesd", "preview_size": 256,
                   "cache_mode": "lru", "cache_lru_size": 32})
        args = ["-s", "ComfyUI/main.py", "--windows-standalone-build", "--highvram", "--force-fp16",
                "--preview-method=auto", "--cache-ram", "2.5", "16", "--preview-size", "1024",
                "--listen", "127.0.0.1", "--port=8188", "--fast", "fp16_accumulation", "--fp32-vae"]
        effective = self.apply(args)
        self.assertEqual(effective, ["-s", "ComfyUI/main.py", "--windows-standalone-build", "--listen", "127.0.0.1",
                                    "--port=8188", "--fast", "fp16_accumulation", "--fp32-vae", "--lowvram",
                                    "--force-fp32", "--cache-lru", "32", "--preview-method", "taesd", "--preview-size", "256"])
        self.assertEqual(args[3], "--highvram")

    def test_auto_removes_conflicting_group_flags_and_null_restores_inheritance(self):
        self.save({"vram_mode": "auto", "cache_mode": "auto", "precision": "auto"})
        original = ["main.py", "--cpu", "--cache-lru=32", "--force-fp16", "--preview-method", "auto"]
        self.assertEqual(self.apply(original), ["main.py", "--preview-method", "auto"])
        self.save({"vram_mode": None})
        self.assertIn("--cpu", self.apply(original))

    def test_legacy_normal_vram_is_replaced_when_selecting_another_mode(self):
        self.save({"vram_mode": "low"})
        self.assertEqual(self.apply(["main.py", "--normalvram"]), ["main.py", "--lowvram"])
        self.parser.write_text("parser.add_argument('--lowvram')", encoding="utf-8")
        current = LaunchConfig(self.state, self.comfy)
        self.assertNotIn("normal", current.get()["supported"]["vram_mode"])
        self.assertEqual(apply_overrides(["main.py", "--normalvram"], self.state, self.comfy), ["main.py", "--lowvram"])

    def test_missing_installed_flag_is_rejected_without_executing_parser(self):
        self.parser.write_text("raise RuntimeError('must never execute')\nparser.add_argument('--preview-method')", encoding="utf-8")
        config = LaunchConfig(self.state, self.comfy)
        self.assertEqual(config.get()["supported"]["vram_mode"], ["auto"])
        with self.assertRaises(LaunchConfigError):
            config.patch({"precision": "fp16"}, config.get()["revision"])

    def test_tampered_and_oversized_settings_are_rejected(self):
        self.state.mkdir()
        self.config.path.write_text('{"listen":"0.0.0.0"}', encoding="utf-8")
        with self.assertRaises(LaunchConfigError):
            self.apply(["main.py"])
        self.config.path.write_text(" " * 8193, encoding="utf-8")
        with self.assertRaises(LaunchConfigError):
            self.config.get()

    def test_restore_verifies_backup_hash_and_rejects_paths(self):
        first = self.config.get()["revision"]
        changed = self.save({"preview_method": "auto"})
        (self.config.backups / f"{first}.json").write_text('{"preview_method":"none"}', encoding="utf-8")
        with self.assertRaises(LaunchConfigError):
            self.config.restore(first, changed["revision"])
        with self.assertRaises(LaunchConfigError):
            self.config.restore("../config", changed["revision"])

    def test_noop_keeps_revision_and_existing_files_unchanged(self):
        changed = self.save({"cache_mode": "lru"})
        self.assertEqual(self.apply(["main.py", "--cache-classic"]), ["main.py", "--cache-lru", "128"])
        before = self.config.path.read_bytes()
        again = self.save({"cache_mode": "lru"})
        self.assertEqual(changed["revision"], again["revision"])
        self.assertEqual(before, self.config.path.read_bytes())


if __name__ == "__main__":
    unittest.main()
