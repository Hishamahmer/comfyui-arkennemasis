"""Scoped inventory, inspection and recoverable management of ComfyUI assets."""

import base64
from typing import Literal

from mcp.server.mcpserver import Image

from .media import MediaLibrary


def register_media_tools(tool, settings, operations):
    def library():
        return MediaLibrary(settings.comfy_root, settings.state_dir, settings.model_roots)

    @tool("comfy:media")
    def list_asset_files(category: str = "", path: str = "", limit: int = 100, cursor: str = "") -> dict:
        """List media/model categories or files with sizes and revisions. Categories are input, output, temp and models/<type>. Use returned cursors for further pages."""
        assets = library()
        return assets.inventory(category, path, limit, cursor) if category else assets.categories()

    @tool("comfy:media")
    def inspect_asset_file(category: str, path: str) -> dict:
        """Read bounded image/video/audio/model metadata, including embedded PNG workflow data where present. Does not load model tensors or execute file contents."""
        return library().inspect(category, path)

    @tool("comfy:media")
    def list_deleted_assets(category: str, limit: int = 100) -> dict:
        """Find recoverable deleted assets and their trash IDs, including when a delete response was lost."""
        return library().list_trash(category, limit)

    @tool("comfy:media")
    def hash_asset_file(category: str, path: str, request_id: str) -> dict:
        """Compute a SHA-256 hash in the background, useful for large models. Poll get_operation."""
        return operations.submit("hash_asset", "comfy:media", request_id, {"category": category, "path": path},
                                 lambda: library().inspect(category, path, include_hash=True))

    @tool("comfy:media")
    def read_asset_text(category: str, path: str, max_bytes: int = 65536) -> dict:
        """Read a bounded text/JSON sidecar from a supported media category."""
        return library().read(category, path, max_bytes)

    @tool("comfy:media")
    def preview_asset_file(category: str, path: str, time_seconds: float = 0, max_width: int = 480) -> Image:
        """Inspect a small image or video-frame preview. Optional local FFmpeg is required; client display support varies."""
        result = library().thumbnail(category, path, time_seconds, max_width)
        return Image(data=base64.b64decode(result["data_base64"]), format="jpeg")

    @tool("comfy:media", write=True)
    def organize_asset_file(action: Literal["copy", "move", "delete", "restore"], request_id: str,
                            category: str = "", path: str = "", expected_revision: str = "",
                            destination_category: str = "", destination_path: str = "", trash_id: str = "") -> dict:
        """Copy/move or recoverably delete a previously inspected asset, or restore its trash ID. Never overwrites destination files. Poll get_operation and reuse its UUID on retry."""
        def run():
            assets = library()
            if action == "delete":
                return assets.delete(category, path, expected_revision)
            if action == "restore":
                return assets.restore(trash_id)
            return assets.transfer(category, path, destination_category, destination_path, expected_revision, action)
        return operations.submit("organize_asset", "comfy:media", request_id,
                                 {"action": action, "category": category, "path": path, "revision": expected_revision,
                                  "destination_category": destination_category, "destination_path": destination_path, "trash_id": trash_id}, run)

    @tool("comfy:maintain", write=True, open_world=True)
    def download_model_file(url: str, category: str, path: str, expected_sha256: str, max_bytes: int, request_id: str) -> dict:
        """Download an explicitly selected model into models/<type>, enforcing SHA-256, size/disk limits and owner-approved HTTPS hosts on every redirect. No overwrite. Poll get_operation; model binaries stay local."""
        return operations.submit("download_model", "comfy:maintain", request_id,
                                 {"url": url, "category": category, "path": path, "sha256": expected_sha256, "max_bytes": max_bytes},
                                 lambda: library().download(url, category, path, expected_sha256, max_bytes, settings.download_hosts))
