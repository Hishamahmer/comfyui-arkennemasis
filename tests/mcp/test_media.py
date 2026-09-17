import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest.mock import patch
import zlib

from mcp_service.media import MediaConflict, MediaError, MediaLibrary, _PublicHTTPS, _download_address, _media_input


def chunk(kind, value):
    return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value))


def png(metadata=None):
    value = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 32, 8, 2, 0, 0, 0))
    if metadata is not None:
        value += chunk(b"tEXt", b"workflow\0" + json.dumps(metadata).encode())
    return value + chunk(b"IEND", b"")


class Response(io.BytesIO):
    def __init__(self, content=b"", status=200, **headers):
        super().__init__(content)
        self.status = status
        self.headers = headers

    def getheader(self, name, default=None):
        return self.headers.get(name, default)


class MediaTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.root = self.base / "ComfyUI"
        self.state = self.base / "state"
        for category in ("input", "output", "temp", "models/checkpoints"):
            (self.root / category).mkdir(parents=True)
        self.library = MediaLibrary(self.root, self.state)

    def create(self, category="output", name="image.png", content=None):
        target = self.root / category / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png({"nodes": []}) if content is None else content)
        return self.library.inspect(category, name)

    def test_inventory_limits_and_cursor(self):
        for i in range(5):
            self.create(name=f"{i}.png")
        (self.root / "output" / "credentials.json").write_text('{"password":"hidden"}')
        first = self.library.inventory("output", limit=2)
        second = self.library.inventory("output", limit=2, cursor=first["next_cursor"])
        last = self.library.inventory("output", limit=2, cursor=second["next_cursor"])
        self.assertEqual([f["path"] for r in (first, second, last) for f in r["files"]], [f"{i}.png" for i in range(5)])
        self.assertIsNone(last["next_cursor"])
        self.assertGreater(first["free_bytes"], 0)

    def test_inventory_recursive_and_missing_category(self):
        self.create(name="nested/a.png")
        self.assertEqual(self.library.inventory("output")["files"][0]["path"], "nested/a.png")
        self.assertEqual(self.library.inventory("models/vae")["files"], [])

    def test_png_dimensions_workflow_and_requested_hash(self):
        result = self.create()
        self.assertEqual((result["width"], result["height"]), (64, 32))
        self.assertEqual(result["workflow"], {"nodes": []})
        self.assertNotIn("sha256", result)
        result = self.library.inspect("output", "image.png", include_hash=True)
        self.assertEqual(result["sha256"], hashlib.sha256((self.root / "output/image.png").read_bytes()).hexdigest())

    def test_safetensors_header_without_loading_tensor(self):
        header = json.dumps({"__metadata__": {"architecture": "test"}, "weight": {"dtype": "F16", "shape": [2], "data_offsets": [0, 4]}}).encode()
        result = self.create("models/checkpoints", "fixture.safetensors", struct.pack("<Q", len(header)) + header + b"0000")
        self.assertEqual(result["tensor_count"], 1)
        self.assertEqual(result["dtypes"], ["F16"])
        self.assertEqual(result["metadata"], {"architecture": "test"})

    def test_oversized_safetensors_header_is_not_read(self):
        result = self.create("models/checkpoints", "fixture.safetensors", struct.pack("<Q", 2 ** 40))
        self.assertTrue(result["metadata_truncated"])

    def test_compressed_png_metadata_bomb_is_bounded(self):
        raw = png()[:-12] + chunk(b"zTXt", b"workflow\0\0" + zlib.compress(b"a" * (2 * 1024 * 1024))) + chunk(b"IEND", b"")
        result = self.create(content=raw)
        self.assertTrue(result["metadata_truncated"])
        self.assertNotIn("workflow", result)

    def test_path_boundaries(self):
        for path in ("../outside.png", "/absolute.png", "nested\\a.png", "a.png:stream", "a./x.png", ".env", "venv/a.json", "api_key.json", "NUL.png"):
            with self.subTest(path=path), self.assertRaises(MediaError):
                self.library.inspect("output", path)
        for category in ("custom_nodes", "models/../output", "models", "models/env"):
            with self.subTest(category=category), self.assertRaises(MediaError):
                self.library.inventory(category)
        with self.assertRaises(MediaError):
            self.library.inspect("output", "execute.py")

    def test_hardlinks_blocked(self):
        self.create()
        try:
            os.link(self.root / "output/image.png", self.root / "input/hard.png")
        except OSError:
            self.skipTest("Hard links unavailable on this filesystem")
        with self.assertRaises(MediaError):
            self.library.inspect("output", "image.png")
        self.assertEqual(self.library.inventory("input")["files"], [])

    def test_symlink_blocked(self):
        self.create()
        try:
            (self.root / "input/link.png").symlink_to(self.root / "output/image.png")
        except OSError:
            self.skipTest("Symlink creation requires privileges on this host")
        with self.assertRaises(MediaError):
            self.library.inspect("input", "link.png")

    def test_external_model_root_is_explicit(self):
        external = self.base / "selected-models"
        external.mkdir()
        (external / "weights.bin").write_bytes(b"test")
        library = MediaLibrary(self.root, self.state, {"checkpoints": str(external)})
        self.assertEqual(library.inventory("models/checkpoints")["files"][0]["path"], "weights.bin")

    def test_categories_only_lists_supported_model_folders(self):
        (self.root / "models/loras").mkdir()
        (self.root / "models/.secret").mkdir()
        (self.root / "models/credentials").mkdir()
        self.assertEqual(self.library.categories(), ["input", "output", "temp", "models/checkpoints", "models/loras"])

    def test_text_bounded(self):
        self.create(name="notes.txt", content=b"abcdef")
        result = self.library.read("output", "notes.txt", max_bytes=3)
        self.assertEqual(result["content"], "abc")
        self.assertTrue(result["truncated"])

    def test_copy_move_and_no_overwrite(self):
        original = self.create()
        copied = self.library.transfer("output", "image.png", "input", "copy.png", original["revision"])
        self.assertTrue((self.root / "output/image.png").exists())
        moved = self.library.transfer("input", "copy.png", "temp", "nested/new.png", copied["revision"], "move")
        self.assertFalse((self.root / "input/copy.png").exists())
        self.assertEqual(moved["path"], "nested/new.png")
        with self.assertRaises(MediaConflict):
            self.library.transfer("output", "image.png", "temp", "nested/new.png", original["revision"])

    def test_changed_revision_rejects_delete_and_move(self):
        original = self.create()
        (self.root / "output/image.png").write_bytes(png({"nodes": [1]}))
        with self.assertRaises(MediaConflict):
            self.library.delete("output", "image.png", original["revision"])
        with self.assertRaises(MediaConflict):
            self.library.transfer("output", "image.png", "input", "new.png", original["revision"], "move")

    def test_delete_is_recoverable_without_copy_on_same_volume(self):
        original = self.create()
        with patch.object(self.library, "_copy_new", side_effect=AssertionError("must not copy")):
            deleted = self.library.delete("output", "image.png", original["revision"])
            self.assertFalse((self.root / "output/image.png").exists())
            self.assertFalse(deleted["disk_space_reclaimed"])
            self.library.restore(deleted["trash_id"])
        self.assertTrue((self.root / "output/image.png").exists())
        with self.assertRaises(MediaError):
            self.library.restore(deleted["trash_id"])

    def test_restore_does_not_replace_new_file(self):
        original = self.create()
        deleted = self.library.delete("output", "image.png", original["revision"])
        self.create(content=png({"new": True}))
        with self.assertRaises(MediaConflict):
            self.library.restore(deleted["trash_id"])
        self.assertEqual(self.library.inspect("output", "image.png")["workflow"], {"new": True})

    def test_corrupt_trash_revision_rejected(self):
        original = self.create()
        deleted = self.library.delete("output", "image.png", original["revision"])
        (self.library.trash / deleted["trash_id"] / "payload").write_bytes(b"changed")
        with self.assertRaises(MediaConflict):
            self.library.restore(deleted["trash_id"])

    def test_corrupt_trash_record_retains_payload(self):
        original = self.create()
        deleted = self.library.delete("output", "image.png", original["revision"])
        folder = self.library.trash / deleted["trash_id"]
        (folder / "record.json").write_text("[]")
        with self.assertRaises(MediaError):
            self.library.restore(deleted["trash_id"])
        self.assertTrue((folder / "payload").exists())

    def test_interrupted_trash_record_update_preserves_recovery(self):
        original = self.create()
        replace = os.replace
        calls = []

        def fail_second_record(source, destination):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("simulated interrupted metadata replacement")
            return replace(source, destination)

        with patch("mcp_service.media.os.replace", side_effect=fail_second_record):
            with self.assertRaises(OSError):
                self.library.delete("output", "image.png", original["revision"])
        self.assertFalse((self.root / "output/image.png").exists())
        entries = self.library.list_trash("output")["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["path"], "image.png")
        self.library.restore(entries[0]["trash_id"])
        self.assertTrue((self.root / "output/image.png").exists())
        self.assertEqual(self.library.list_trash("output")["entries"], [])

    def test_initial_trash_record_failure_keeps_original(self):
        original = self.create()
        with patch("mcp_service.media.os.replace", side_effect=OSError("metadata unavailable")):
            with self.assertRaises(OSError):
                self.library.delete("output", "image.png", original["revision"])
        self.assertTrue((self.root / "output/image.png").exists())
        self.assertEqual(self.library.list_trash("output")["entries"], [])

    def test_trash_listing_filters_category_and_is_bounded(self):
        for index in range(2):
            original = self.create(name=f"{index}.png")
            self.library.delete("output", f"{index}.png", original["revision"])
        other = self.create("input", "other.png")
        self.library.delete("input", "other.png", other["revision"])
        result = self.library.list_trash("output", limit=1)
        self.assertEqual(len(result["entries"]), 1)
        self.assertTrue(result["truncated"])
        self.assertEqual(self.library.list_trash("input")["entries"][0]["path"], "other.png")

    def test_insufficient_disk_rejects_copy(self):
        original = self.create()
        with patch("mcp_service.media.shutil.disk_usage") as disk:
            disk.return_value.free = 5
            with self.assertRaises(MediaError):
                self.library.transfer("output", "image.png", "input", "copy.png", original["revision"])
        self.assertFalse((self.root / "input/copy.png").exists())

    def test_source_changes_during_copy_not_published(self):
        original = self.create()
        source = self.root / "output/image.png"
        original_revision = original["revision"]
        with patch("mcp_service.media._revision", side_effect=[original_revision, original_revision, "changed"]):
            with self.assertRaises(MediaConflict):
                self.library.transfer("output", "image.png", "input", "copy.png", original_revision)
        self.assertTrue(source.exists())
        self.assertEqual(list((self.root / "input").iterdir()), [])

    def test_ffprobe_missing_reports_limitation(self):
        with patch("mcp_service.media.shutil.which", return_value=None):
            result = self.create(name="clip.mp4", content=b"fixture")
        self.assertIn("ffprobe", result["inspection_unavailable"])

    def test_video_inspection_uses_local_protocols_and_selected_fields(self):
        self.create(name="clip.mp4", content=b"fixture")
        with patch("mcp_service.media._program", return_value="ffprobe.exe"), patch("mcp_service.media._run_media", return_value=b'{"format":{"duration":"2"}}') as run:
            result = self.library.inspect("output", "clip.mp4")
        self.assertEqual(result["media"]["format"]["duration"], "2")
        self.assertIn("file,pipe", run.call_args.args[0])
        args = run.call_args.args[0]
        self.assertEqual(args[args.index("-f") + 1], "mov")
        self.assertEqual(args[args.index("-enable_drefs") + 1], "0")
        self.assertEqual(args[args.index("-use_absolute_path") + 1], "0")

    def test_disguised_playlists_do_not_reach_media_utility(self):
        path = self.root / "output/clip.mp4"
        for data in [b"#EXTM3U\nfile:///private/clip.ts", b"ffconcat version 1.0\nfile /private/image.png",
                     b"\xef\xbb\xbf  #EXTM3U\n/private/image.png", b'<MPD><BaseURL>/private/clip.mp4</BaseURL></MPD>']:
            with self.subTest(data=data):
                path.write_bytes(data)
                with patch("mcp_service.media._program", return_value="ffmpeg"), patch("mcp_service.media._run_media") as run:
                    with self.assertRaisesRegex(MediaError, "Playlists"):
                        self.library.thumbnail("output", "clip.mp4")
                    result = self.library.inspect("output", "clip.mp4")
                    self.assertIn("Playlists", result["inspection_unavailable"])
                    run.assert_not_called()

    def test_media_formats_are_pinned_even_for_unrecognized_headers(self):
        for suffix, expected in [(".mp4", "mov"), (".mkv", "matroska"), (".png", "png_pipe"),
                                 (".jpg", "jpeg_pipe"), (".mp3", "mp3"), (".wav", "wav")]:
            path = self.root / "output" / ("fixture" + suffix)
            path.write_bytes(b"unrecognized header must not trigger arbitrary format autodetection")
            args = _media_input(path)
            self.assertEqual(args[args.index("-f") + 1], expected)

    def test_thumbnail_returns_bounded_image(self):
        self.create()
        with patch("mcp_service.media._program", return_value="ffmpeg.exe"), patch("mcp_service.media._run_media", return_value=b"\xff\xd8\xff\xd9") as run:
            result = self.library.thumbnail("output", "image.png")
        self.assertEqual(result["mime_type"], "image/jpeg")
        self.assertIn("-frames:v", run.call_args.args[0])
        with self.assertRaises(MediaError):
            self.library.thumbnail("output", "image.png", time_seconds=float("nan"))

    @unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is unavailable")
    def test_pinned_format_decodes_a_real_temporary_png(self):
        pixels = (b"\0" + b"\xff\0\0" * 64) * 32
        raw = png()[:-12] + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")
        self.create(content=raw)
        result = self.library.thumbnail("output", "image.png", max_width=64)
        self.assertEqual(result["mime_type"], "image/jpeg")
        self.assertTrue(result["data_base64"].startswith("/9j/"))

    @staticmethod
    def dns(address="93.184.216.34"):
        return [(2, 1, 6, "", (address, 443))]

    def test_url_validation_and_dns_ssrf(self):
        for url in ("http://huggingface.co/model", "https://user:pass@huggingface.co/model", "https://huggingface.co:8188/model", "https://huggingface.co.evil.test/model", "https://127.0.0.1/model", "https://huggingface.co/a#x"):
            with self.subTest(url=url), self.assertRaises(MediaError):
                _download_address(url, ["huggingface.co"])
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "100.100.100.100", "192.0.2.1"):
            with self.subTest(address=address), patch("mcp_service.media.socket.getaddrinfo", return_value=self.dns(address)), self.assertRaises(MediaError):
                _download_address("https://huggingface.co/model", ["huggingface.co"])

    def test_mixed_public_private_dns_answer_rejected(self):
        answers = self.dns() + self.dns("127.0.0.1")
        with patch("mcp_service.media.socket.getaddrinfo", return_value=answers), self.assertRaises(MediaError):
            _download_address("https://huggingface.co/model", ["huggingface.co"])

    def test_tls_connect_pins_address_and_verifies_original_hostname(self):
        with patch("mcp_service.media.ssl.create_default_context") as context, patch("mcp_service.media.socket.create_connection") as connect:
            connection = _PublicHTTPS("huggingface.co", "93.184.216.34")
            connection.connect()
        connect.assert_called_once_with(("93.184.216.34", 443), 30)
        context.return_value.wrap_socket.assert_called_once_with(connect.return_value, server_hostname="huggingface.co")

    def download(self, responses, content=b"model", max_bytes=100):
        with patch("mcp_service.media.socket.getaddrinfo", return_value=self.dns()), patch("mcp_service.media._PublicHTTPS") as connection:
            connection.return_value.getresponse.side_effect = responses
            result = self.library.download("https://huggingface.co/model", "models/checkpoints", "new.bin", hashlib.sha256(content).hexdigest(), max_bytes)
            self.assertEqual(connection.call_args.args, ("huggingface.co", "93.184.216.34"))
            return result

    def test_download_hash_verified_and_published(self):
        result = self.download([Response(b"model", **{"Content-Length": "5"})])
        self.assertTrue(result["downloaded"])
        self.assertEqual((self.root / "models/checkpoints/new.bin").read_bytes(), b"model")

    def test_bad_checksum_or_size_removes_partial(self):
        for response, limit in ((Response(b"wrong"), 100), (Response(b"model", **{"Content-Length": "5"}), 4), (Response(b"model"), 4)):
            with self.subTest(limit=limit), self.assertRaises(MediaError):
                self.download([response], max_bytes=limit)
            self.assertEqual(list((self.root / "models/checkpoints").iterdir()), [])

    def test_redirect_cannot_escape_allowlist_or_https(self):
        for location in ("http://huggingface.co/next", "https://evil.test/model", "https://127.0.0.1/model"):
            with self.subTest(location=location), self.assertRaises(MediaError):
                self.download([Response(status=302, Location=location)])

    def test_allowed_redirect_revalidated_and_downloaded(self):
        result = self.download([Response(status=302, Location="/second"), Response(b"model")])
        self.assertTrue(result["downloaded"])

    def test_download_never_overwrites_or_targets_media(self):
        self.create("models/checkpoints", "new.bin", b"keep")
        with self.assertRaises(MediaConflict):
            self.download([])
        with self.assertRaises(MediaError):
            self.library.download("https://huggingface.co/model", "output", "model.bin", "a" * 64, 10)


if __name__ == "__main__":
    unittest.main()
