"""Bounded media/model operations inside configured roots, never a general file API."""

import base64
from collections import deque
from contextlib import contextmanager
import errno
import hashlib
import http.client
import ipaddress
import json
import mimetypes
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat
import struct
import subprocess
import tempfile
import threading
from urllib.parse import urljoin, urlsplit
import uuid
import zlib

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MAX_METADATA = 1024 * 1024
MAX_SCAN = 10000
DISK_RESERVE = 256 * 1024 * 1024
MEDIA_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif",
    ".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus",
    ".json", ".txt", ".csv", ".safetensors", ".latent", ".npy", ".npz",
}
MODEL_EXTENSIONS = {".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx", ".json", ".yaml", ".yml", ".txt", ".model"}
TEXT_EXTENSIONS = {".json", ".txt", ".csv", ".yaml", ".yml"}
AV_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".opus"}
INPUT_FORMATS = {
    ".mp4": "mov", ".mov": "mov", ".m4a": "mov", ".avif": "mov",
    ".webm": "matroska", ".mkv": "matroska", ".mp3": "mp3", ".wav": "wav",
    ".flac": "flac", ".ogg": "ogg", ".opus": "ogg", ".png": "png_pipe",
    ".jpg": "jpeg_pipe", ".jpeg": "jpeg_pipe", ".webp": "webp_pipe", ".gif": "gif",
    ".bmp": "bmp_pipe", ".tif": "tiff_pipe", ".tiff": "tiff_pipe",
}
RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
PROTECTED = {"env", "venv", "node_modules", "__pycache__", "credentials", "secrets", "site-packages", "mcp_service"}
SENSITIVE = re.compile(r"(?:credential|secret|password|api[_-]?key|access[_-]?token|auth[_-]?token)", re.I)
DEFAULT_DOWNLOAD_HOSTS = ("huggingface.co", "hf.co", "civitai.com")


class MediaError(ValueError):
    """Unsupported or unsafe media operation."""


class MediaConflict(MediaError):
    """The source changed or the destination is occupied."""


def _parts(value, empty=False):
    if empty and value == "":
        return []
    if not isinstance(value, str) or not value or len(value) > 1024 or "\\" in value:
        raise MediaError("Use a relative media path with forward slashes.")
    parts = value.split("/")
    if len(parts) > 20:
        raise MediaError("Media paths may contain at most 20 components.")
    for part in parts:
        if (not part or part.startswith(".") or len(part) > 240 or part[-1] in " ."
                or re.search(r'[<>:"|?*~\x00-\x1f]', part) or part.split(".")[0].upper() in RESERVED
                or part.casefold() in PROTECTED or SENSITIVE.search(part)):
            raise MediaError("This path is hidden, reserved or protected from media tools.")
    return parts


def _check(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise MediaError("Media roots cannot contain symbolic links, junctions or reparse points.")
    if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
        raise MediaError("Files with multiple hard links cannot be accessed.")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise MediaError("Only regular files and directories are supported.")


def _root(value):
    result = Path(os.path.abspath(value))
    for path in (*reversed(result.parents), result):
        _check(path)
    return result


def _limit(value, maximum, label):
    if type(value) is not int or not 1 <= value <= maximum:
        raise MediaError(f"{label} must be between 1 and {maximum}.")
    return value


def _revision(path):
    _check(path)
    info = path.stat()
    return hashlib.sha256(f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}".encode()).hexdigest()


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _disk(path, needed):
    while not path.exists():
        path = path.parent
    free = shutil.disk_usage(path).free
    if free < needed + DISK_RESERVE:
        raise MediaError("Not enough disk space; operations keep at least 256 MiB free.")
    return free


def _move_new(source, destination):
    # rename is no-clobber on Windows. POSIX link/unlink retains the same inode
    # without copying large models or overwriting a concurrently created file.
    if os.name == "nt":
        os.rename(source, destination)
    else:
        os.link(source, destination)
        source.unlink()


def _write_record(path, record):
    """Keep a valid recovery record if writing its replacement is interrupted."""
    _root(path)
    descriptor, temporary = tempfile.mkstemp(prefix=".ark-record-", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        _root(path)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _media_input(source):
    """Pin self-contained demuxers; a filename must not enable local playlists."""
    format_name = INPUT_FORMATS.get(source.suffix.lower())
    if format_name is None:
        raise MediaError("This file has no supported media inspection format.")
    _check(source)
    with source.open("rb") as stream:
        header = stream.read(1024).lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if header.startswith((b"#extm3u", b"ffconcat version", b"[playlist]", b"<?xml", b"<mpd")):
        raise MediaError("Playlists and files that reference other media are excluded from inspection.")
    args = ["-protocol_whitelist", "file,pipe", "-f", format_name]
    if format_name == "mov":
        args += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    return args


def _png_metadata(path):
    result, total = {}, 0
    with path.open("rb") as stream:
        if stream.read(8) != b"\x89PNG\r\n\x1a\n":
            raise MediaError("Invalid PNG signature.")
        for _ in range(10000):
            header = stream.read(8)
            if len(header) != 8:
                break
            length, kind = struct.unpack(">I4s", header)
            if kind in {b"IHDR", b"tEXt", b"zTXt", b"iTXt"}:
                if length > MAX_METADATA or total + length > MAX_METADATA:
                    result["metadata_truncated"] = True
                    break
                raw = stream.read(length)
                total += length
                if len(raw) != length:
                    raise MediaError("Truncated PNG metadata.")
                if kind == b"IHDR" and len(raw) == 13:
                    result["width"], result["height"] = struct.unpack(">II", raw[:8])
                elif kind in {b"tEXt", b"zTXt", b"iTXt"} and b"\0" in raw:
                    key, value = raw.split(b"\0", 1)
                    skip = key not in {b"workflow", b"prompt"}
                    compressed = False
                    if kind == b"zTXt":
                        compressed, value = True, value[1:] if value[:1] == b"\0" else b""
                    elif kind == b"iTXt":
                        if len(value) < 2 or value[1] != 0:
                            skip = True
                        else:
                            compressed = value[0] == 1
                            fields = value[2:].split(b"\0", 2)
                            value = fields[2] if len(fields) == 3 else b""
                    if not skip:
                        try:
                            if compressed:
                                decoder = zlib.decompressobj()
                                value = decoder.decompress(value, MAX_METADATA + 1)
                                if len(value) > MAX_METADATA or not decoder.eof:
                                    result["metadata_truncated"] = True
                                    value = b""
                            if value:
                                parsed = json.loads(value.decode("utf-8"))
                                result[key.decode()] = parsed
                        except (UnicodeError, ValueError, zlib.error):
                            result["metadata_warning"] = "Embedded workflow metadata could not be decoded."
            else:
                stream.seek(length, 1)
            if len(stream.read(4)) != 4 or kind == b"IEND":
                break
    return result


def _safetensors_metadata(path):
    with path.open("rb") as stream:
        prefix = stream.read(8)
        if len(prefix) != 8:
            raise MediaError("Invalid safetensors header.")
        length = struct.unpack("<Q", prefix)[0]
        if length > MAX_METADATA:
            return {"metadata_truncated": True}
        raw = stream.read(length)
    try:
        header = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise MediaError("Invalid safetensors metadata.") from exc
    if not isinstance(header, dict):
        raise MediaError("Invalid safetensors metadata.")
    tensors = [v for k, v in header.items() if k != "__metadata__" and isinstance(v, dict)]
    return {"metadata": header.get("__metadata__", {}), "tensor_count": len(tensors),
            "dtypes": sorted({str(t.get("dtype", "unknown")) for t in tensors})}


def _program(name):
    found = shutil.which(name)
    if not found or not Path(found).is_absolute():
        raise MediaError(f"{name} is not installed on PATH; install it to enable this media operation.")
    return found


def _run_media(args, timeout=20, maximum=MAX_METADATA):
    # A temporary file keeps subprocess output bounded in RAM. Read a strict
    # limit and restrict ffmpeg to one small output frame.
    with tempfile.TemporaryFile() as output:
        try:
            result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                                    timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise MediaError("Media inspection timed out.") from exc
        if result.returncode:
            raise MediaError("The installed media utility could not inspect this file.")
        output.seek(0)
        raw = output.read(maximum + 1)
        if len(raw) > maximum:
            raise MediaError("Media utility output exceeds the response limit.")
        return raw


class _PublicHTTPS(http.client.HTTPSConnection):
    def __init__(self, hostname, address):
        super().__init__(hostname, port=443, timeout=30, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


def _download_address(url, allowed_hosts):
    if not isinstance(url, str) or len(url) > 16384:
        raise MediaError("Provide an HTTPS download URL up to 16384 characters.")
    try:
        parsed = urlsplit(url)
        hostname, port = parsed.hostname, parsed.port
    except (ValueError, TypeError) as exc:
        raise MediaError("Provide a valid HTTPS download URL.") from exc
    if (parsed.scheme != "https" or not hostname or parsed.username is not None or parsed.password is not None
            or port not in (None, 443) or parsed.fragment or any(c in url for c in "\r\n\t\\")):
        raise MediaError("Downloads require HTTPS on port 443, without URL credentials or fragments.")
    hostname = hostname.lower()
    if not any(hostname == host or hostname.endswith("." + host) for host in allowed_hosts):
        raise MediaError("The download or redirect hostname is not enabled in download_hosts.")
    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise MediaError("The model download host could not be resolved.") from exc
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise MediaError("Downloads cannot contact private, loopback, link-local or reserved IP addresses.")
    return parsed, sorted(addresses)


class MediaLibrary:
    def __init__(self, comfy_root, state_dir, model_roots=None):
        self.root = _root(comfy_root)
        self.state = _root(Path(state_dir) / "media")
        self.trash = self.state / "trash"
        self.trash.mkdir(parents=True, exist_ok=True)
        self.model_roots = {}
        for name, value in (model_roots or {}).items():
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name)
                    or name.casefold() in PROTECTED or SENSITIVE.search(name)):
                raise MediaError("Model categories must be plain directory names.")
            self.model_roots[name] = _root(value)
        self._lock = threading.RLock()

    def _category(self, category):
        if category in {"input", "output", "temp"}:
            return _root(self.root / category), False
        if isinstance(category, str) and category.startswith("models/"):
            name = category[7:]
            if (re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name) and name.casefold() not in PROTECTED
                    and not SENSITIVE.search(name)):
                return _root(self.model_roots.get(name, self.root / "models" / name)), True
        raise MediaError("Category must be input, output, temp or models/<category>.")

    def _path(self, category, name, directory=False):
        root, model = self._category(category)
        parts = _parts(name, empty=directory)
        if not directory and Path(parts[-1]).suffix.lower() not in (MODEL_EXTENSIONS if model else MEDIA_EXTENSIONS):
            raise MediaError("This file extension is not supported in the selected category.")
        path = root.joinpath(*parts)
        for i in range(len(parts) + 1):
            _check(root.joinpath(*parts[:i]))
        if not path.resolve().is_relative_to(root.resolve()):
            raise MediaError("Media path escapes the selected root.")
        if path == self.state or self.state in path.parents:
            raise MediaError("Private media state is protected.")
        return path

    @contextmanager
    def _guard(self):
        with self._lock:
            _root(self.state)
            lock_path = self.state / "operations.lock"
            _check(lock_path)
            with lock_path.open("a+b") as stream:
                stream.seek(0, 2)
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream, fcntl.LOCK_UN)

    def _current(self, category, name, expected_revision):
        path = self._path(category, name)
        if not path.is_file():
            raise MediaConflict("The selected file no longer exists.")
        if not expected_revision or _revision(path) != expected_revision:
            raise MediaConflict("The file changed; inspect it again before retrying.")
        return path

    def _entry(self, category, name, path):
        info = path.stat()
        return {"category": category, "path": name, "size_bytes": info.st_size,
                "modified_at": info.st_mtime, "mime_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream",
                "revision": _revision(path)}

    def categories(self):
        """List logical model categories without disclosing installation paths."""
        names = set(self.model_roots)
        models = _root(self.root / "models")
        if models.is_dir():
            with os.scandir(models) as entries:
                for index, entry in enumerate(entries):
                    if index >= 1000:
                        break
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", entry.name):
                        try:
                            folder, _ = self._category("models/" + entry.name)
                            if folder.is_dir():
                                names.add(entry.name)
                        except MediaError:
                            continue
        return ["input", "output", "temp", *("models/" + name for name in sorted(names))]

    def inventory(self, category, path="", limit=100, cursor=""):
        _limit(limit, 500, "limit")
        if not isinstance(cursor, str) or (cursor and not cursor.isdigit()):
            raise MediaError("Inventory cursor must be a numeric offset from the previous result.")
        offset = int(cursor or "0")
        if offset > MAX_SCAN:
            raise MediaError("Inventory cursor exceeds the scan limit; choose a narrower subfolder.")
        root = self._path(category, path, directory=True)
        if not root.is_dir():
            existing = root
            while not existing.exists():
                existing = existing.parent
            return {"files": [], "next_cursor": None, "scan_truncated": False, "free_bytes": shutil.disk_usage(existing).free}
        base, _ = self._category(category)
        pending, entries, scanned, matched = deque([root]), [], 0, 0
        while pending and scanned < MAX_SCAN and len(entries) <= limit:
            directory = pending.popleft()
            # Bound each directory too; sorting an entire enormous directory can
            # otherwise consume memory before the global scan limit is applied.
            children = []
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if scanned + len(children) >= MAX_SCAN:
                        break
                    children.append(Path(entry.path))
            for child in sorted(children, key=lambda p: p.name.casefold()):
                scanned += 1
                name = child.relative_to(base).as_posix()
                try:
                    safe = self._path(category, name, directory=child.is_dir())
                    if safe.is_dir():
                        pending.append(safe)
                        continue
                    if not safe.is_file():
                        continue
                    matched += 1
                    if matched > offset:
                        entries.append(self._entry(category, name, safe))
                    if len(entries) > limit:
                        break
                except (MediaError, OSError):
                    continue
        return {"files": entries[:limit], "next_cursor": str(offset + limit) if len(entries) > limit else None,
                "scan_truncated": scanned >= MAX_SCAN, "free_bytes": shutil.disk_usage(root).free}

    def inspect(self, category, path, include_hash=False):
        source = self._path(category, path)
        if not source.is_file():
            raise MediaError("The selected media file does not exist.")
        result = self._entry(category, path, source)
        suffix = source.suffix.lower()
        if suffix == ".png":
            result.update(_png_metadata(source))
        elif suffix == ".safetensors":
            result.update(_safetensors_metadata(source))
        elif suffix in AV_EXTENSIONS:
            try:
                raw = _run_media([_program("ffprobe"), "-v", "error", "-max_alloc", "67108864", "-probesize", "16777216",
                                  "-analyzeduration", "5000000", *_media_input(source),
                                  "-show_entries", "format=duration,format_name:stream=codec_name,codec_type,width,height,sample_rate,channels,r_frame_rate",
                                  "-of", "json", str(source)])
                result["media"] = json.loads(raw)
            except MediaError as exc:
                result["inspection_unavailable"] = str(exc)
        elif suffix not in TEXT_EXTENSIONS and not category.startswith("models/"):
            try:
                from PIL import Image
                with Image.open(source) as image:
                    result.update(width=image.width, height=image.height, format=image.format)
            except ImportError:
                result["inspection_unavailable"] = "Install Pillow in the MCP runtime for this image format."
            except (OSError, ValueError) as exc:
                raise MediaError("The image metadata could not be decoded.") from exc
        if include_hash:
            result["sha256"] = _sha256(source)
        self._current(category, path, result["revision"])
        return result

    def read(self, category, path, max_bytes=65536):
        _limit(max_bytes, MAX_METADATA, "max_bytes")
        source = self._path(category, path)
        if source.suffix.lower() not in TEXT_EXTENSIONS:
            raise MediaError("Text inspection supports JSON, TXT, CSV and YAML files only.")
        revision = _revision(source)
        with source.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        try:
            content = raw[:max_bytes].decode("utf-8", errors="replace")
        except UnicodeError as exc:
            raise MediaError("Text could not be decoded.") from exc
        self._current(category, path, revision)
        return {"category": category, "path": path, "revision": revision, "content": content,
                "truncated": len(raw) > max_bytes}

    def thumbnail(self, category, path, time_seconds=0, max_width=480):
        _limit(max_width, 1024, "max_width")
        if type(time_seconds) not in (int, float) or not 0 <= time_seconds <= 86400:
            raise MediaError("Thumbnail time must be between 0 and 86400 seconds.")
        source = self._path(category, path)
        if category.startswith("models/") or source.suffix.lower() in TEXT_EXTENSIONS | {".latent", ".npy", ".npz", ".safetensors"}:
            raise MediaError("Choose an image or video file for a thumbnail.")
        revision = _revision(source)
        raw = _run_media([_program("ffmpeg"), "-nostdin", "-v", "error", "-max_alloc", "67108864", "-probesize", "16777216",
                          "-analyzeduration", "5000000", *_media_input(source),
                          "-ss", str(time_seconds), "-i", str(source), "-frames:v", "1", "-vf",
                          f"scale={max_width}:{max_width}:force_original_aspect_ratio=decrease", "-threads", "1",
                          "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"], maximum=2 * MAX_METADATA)
        if not raw.startswith(b"\xff\xd8"):
            raise MediaError("This media file did not produce an image preview.")
        self._current(category, path, revision)
        return {"category": category, "path": path, "revision": revision, "mime_type": "image/jpeg",
                "data_base64": base64.b64encode(raw).decode("ascii")}

    def _copy_new(self, source, destination):
        source_revision = _revision(source)
        expected_size = source.stat().st_size
        _disk(destination.parent, expected_size)
        fd, temporary = tempfile.mkstemp(prefix=".ark-copy-", dir=destination.parent)
        temporary = Path(temporary)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as input_stream:
                total = 0
                while raw := input_stream.read(min(1024 * 1024, expected_size - total + 1)):
                    total += len(raw)
                    if total > expected_size:
                        raise MediaConflict("The source grew during copy; no destination was published.")
                    _disk(destination.parent, len(raw))
                    output.write(raw)
                output.flush()
                os.fsync(output.fileno())
            if _revision(source) != source_revision or total != expected_size:
                raise MediaConflict("The source changed during copy; no destination was published.")
            _root(destination.parent)
            _move_new(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)

    def transfer(self, category, path, destination_category, destination_path, expected_revision, operation="copy"):
        if operation not in {"copy", "move"}:
            raise MediaError("Transfer operation must be copy or move.")
        with self._guard():
            source = self._current(category, path, expected_revision)
            destination = self._path(destination_category, destination_path)
            if source == destination or destination.exists():
                raise MediaConflict("Destination already exists; transfers never overwrite files.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._path(destination_category, destination_path)
            if operation == "move":
                try:
                    _move_new(source, destination)
                except OSError as exc:
                    if exc.errno != errno.EXDEV:
                        raise
                    self._copy_new(source, destination)
                    self._current(category, path, expected_revision)
                    source.unlink()
            else:
                self._copy_new(source, destination)
                self._current(category, path, expected_revision)
            return {**self._entry(destination_category, destination_path, destination), "operation": operation}

    def delete(self, category, path, expected_revision):
        with self._guard():
            source = self._current(category, path, expected_revision)
            trash_id = uuid.uuid4().hex
            folder = self.trash / trash_id
            _root(self.trash)
            folder.mkdir()
            record = {"category": category, "path": path, "size_bytes": source.stat().st_size}
            _write_record(folder / "record.json", record)
            payload = folder / "payload"
            try:
                _move_new(source, payload)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                self._copy_new(source, payload)
                self._current(category, path, expected_revision)
                source.unlink()
            record["revision"] = _revision(payload)
            _write_record(folder / "record.json", record)
            return {"category": category, "path": path, "deleted": True, "trash_id": trash_id,
                    "recoverable": True, "disk_space_reclaimed": False}

    def list_trash(self, category, limit=100):
        """Recover a trash ID after a delete response was lost or interrupted."""
        self._category(category)
        _limit(limit, 500, "limit")
        items, scanned = [], 0
        with self._guard(), os.scandir(self.trash) as entries:
            for entry in entries:
                scanned += 1
                if scanned > MAX_SCAN:
                    return {"entries": items, "truncated": True}
                if not re.fullmatch(r"[a-f0-9]{32}", entry.name):
                    continue
                try:
                    folder = _root(Path(entry.path))
                    record_path, payload = folder / "record.json", folder / "payload"
                    _check(record_path)
                    _check(payload)
                    if not payload.is_file() or not record_path.is_file() or record_path.stat().st_size > 4096:
                        continue
                    record = json.loads(record_path.read_text(encoding="utf-8"))
                    if not isinstance(record, dict) or record.get("category") != category:
                        continue
                    destination = self._path(category, record.get("path"))
                    if len(items) == limit:
                        return {"entries": items, "truncated": True}
                    items.append({"trash_id": entry.name, "category": category, "path": record["path"],
                                  "size_bytes": payload.stat().st_size, "destination_occupied": destination.exists()})
                except (MediaError, OSError, ValueError, UnicodeError):
                    continue
        return {"entries": items, "truncated": False}

    def restore(self, trash_id):
        if not isinstance(trash_id, str) or not re.fullmatch(r"[a-f0-9]{32}", trash_id):
            raise MediaError("Choose a valid trash ID returned by delete.")
        with self._guard():
            folder = _root(self.trash / trash_id)
            record_path, payload = folder / "record.json", folder / "payload"
            _check(record_path)
            _check(payload)
            if not record_path.is_file() or not payload.is_file() or record_path.stat().st_size > 4096:
                raise MediaError("This trash entry is unavailable or already restored.")
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                if not isinstance(record, dict) or not isinstance(record.get("category"), str) or not isinstance(record.get("path"), str):
                    raise ValueError("invalid record")
            except (ValueError, UnicodeError) as exc:
                raise MediaError("The trash record is damaged; its payload has been retained.") from exc
            if record.get("revision") and _revision(payload) != record["revision"]:
                raise MediaConflict("The trash payload changed; restoration was stopped.")
            destination = self._path(record["category"], record["path"])
            if destination.exists():
                raise MediaConflict("Restore never overwrites a file created at the original location.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            self._path(record["category"], record["path"])
            try:
                _move_new(payload, destination)
            except OSError as exc:
                if exc.errno != errno.EXDEV:
                    raise
                self._copy_new(payload, destination)
                payload.unlink()
            return {**self._entry(record["category"], record["path"], destination), "restored": True}

    def download(self, url, category, path, expected_sha256, max_bytes, allowed_hosts=None):
        """Explicit artifact download; no discovery, telemetry or background fetches."""
        _limit(max_bytes, 1024 ** 4, "max_bytes")
        if not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256):
            raise MediaError("Provide the expected SHA-256 checksum before downloading a model.")
        if not isinstance(category, str) or not category.startswith("models/"):
            raise MediaError("Downloads can only target a configured model category.")
        hosts = DEFAULT_DOWNLOAD_HOSTS if allowed_hosts is None else allowed_hosts
        if not isinstance(hosts, (list, tuple)) or not hosts or any(
                not isinstance(h, str) or not re.fullmatch(r"[a-z0-9]+(?:[.-][a-z0-9]+)*", h) for h in hosts):
            raise MediaError("Configure an explicit list of allowed download hostnames.")
        destination = self._path(category, path)
        if destination.exists():
            raise MediaConflict("Model downloads never overwrite existing files.")
        _disk(destination.parent, max_bytes)
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._path(category, path)
        connection, response, temporary = None, None, None
        try:
            for redirect in range(6):
                parsed, addresses = _download_address(url, hosts)
                connection = _PublicHTTPS(parsed.hostname, addresses[0])
                request_path = parsed.path or "/"
                if parsed.query:
                    request_path += "?" + parsed.query
                connection.request("GET", request_path, headers={"Accept-Encoding": "identity", "User-Agent": "Arkennemasis-MCP-model-download"})
                response = connection.getresponse()
                if response.status in {301, 302, 303, 307, 308}:
                    location = response.getheader("Location")
                    if not location or redirect == 5:
                        raise MediaError("Model download returned an invalid redirect chain.")
                    url = urljoin(url, location)
                    response.close()
                    connection.close()
                    continue
                if response.status != 200:
                    raise MediaError(f"Model host returned HTTP {response.status}.")
                break
            if response.getheader("Content-Encoding", "identity").lower() not in {"", "identity"}:
                raise MediaError("Compressed HTTP transfer encoding is not supported for model downloads.")
            declared = response.getheader("Content-Length")
            if declared and (not declared.isdigit() or int(declared) > max_bytes):
                raise MediaError("Model download exceeds max_bytes or has an invalid content length.")
            descriptor, temporary = tempfile.mkstemp(prefix=".ark-download-", dir=destination.parent)
            temporary = Path(temporary)
            digest, total = hashlib.sha256(), 0
            with os.fdopen(descriptor, "wb") as stream:
                while chunk := response.read(min(1024 * 1024, max_bytes - total + 1)):
                    total += len(chunk)
                    if total > max_bytes:
                        raise MediaError("Model download exceeded max_bytes; partial data was removed.")
                    _disk(destination.parent, len(chunk))
                    stream.write(chunk)
                    digest.update(chunk)
                stream.flush()
                os.fsync(stream.fileno())
            if digest.hexdigest() != expected_sha256.lower():
                raise MediaError("Model checksum did not match; partial data was removed.")
            if declared and total != int(declared):
                raise MediaError("Model transfer ended before the declared size.")
            with self._guard():
                destination = self._path(category, path)
                if destination.exists():
                    raise MediaConflict("The model destination appeared during download; nothing was overwritten.")
                _move_new(temporary, destination)
            return {**self._entry(category, path, destination), "sha256": digest.hexdigest(), "downloaded": True}
        except (OSError, http.client.HTTPException) as exc:
            raise MediaError("The HTTPS model transfer failed; no existing model was replaced.") from exc
        finally:
            if response:
                response.close()
            if connection:
                connection.close()
            if temporary:
                temporary.unlink(missing_ok=True)
