"""ComfyUI media descriptors; never arbitrary local filesystem paths."""

import mimetypes
from pathlib import PurePosixPath
import re
from urllib.parse import urlencode


IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif", ".apng"}
MEDIA_EXTENSIONS = IMAGE_EXTENSIONS | {".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus"}
MAX_IMAGE_BYTES = 20 * 1024 * 1024


def media_descriptor(filename, subfolder="", kind="output", *, image_only=False):
    if kind not in {"input", "output", "temp"}:
        raise ValueError("Media type must be input, output, or temp.")
    if not isinstance(filename, str) or not filename or len(filename) > 240 or "/" in filename or "\\" in filename:
        raise ValueError("Media filename must be a filename without directory components.")
    if re.search(r'[<>:"|?*\x00-\x1f]', filename) or filename[-1] in " .":
        raise ValueError("Unsafe media filename.")
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in (IMAGE_EXTENSIONS if image_only else MEDIA_EXTENSIONS):
        raise ValueError("Unsupported media file extension.")
    if not isinstance(subfolder, str) or len(subfolder) > 1024 or "\\" in subfolder:
        raise ValueError("Use a relative media subfolder with forward slashes.")
    if subfolder and any(not part or part in {".", ".."} or part[-1] in " ." or re.search(r'[<>:"|?*\x00-\x1f]', part) for part in subfolder.split("/")):
        raise ValueError("Unsafe media subfolder.")
    descriptor = {"filename": filename, "subfolder": subfolder, "type": kind}
    descriptor["mime_type"] = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    descriptor["view_path"] = "/view?" + urlencode({"filename": filename, "subfolder": subfolder, "type": kind})
    return descriptor


def output_assets(history):
    assets = []
    seen = set()
    for node_id, output in history.get("outputs", {}).items():
        if not isinstance(output, dict):
            continue
        for field in ("images", "gifs", "videos", "audio"):
            entries = output.get(field, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or "filename" not in entry:
                    continue
                try:
                    descriptor = media_descriptor(entry["filename"], entry.get("subfolder", ""), entry.get("type", "output"))
                except ValueError:
                    continue
                identity = (descriptor["filename"], descriptor["subfolder"], descriptor["type"])
                if identity not in seen:
                    assets.append({"node_id": str(node_id), **descriptor})
                    seen.add(identity)
    return assets
