"""Revision-checked source editing for explicitly selected custom-node packs.

These path controls restrict file operations; edited Python runs with ComfyUI's
operating-system permissions when ComfyUI subsequently loads it.
"""

import ast
from collections import deque
from contextlib import contextmanager
import difflib
import hashlib
import json
import keyword
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import tomllib

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MAX_FILE_BYTES = 1024 * 1024
MAX_RESPONSE_CHARS = 64 * 1024
MAX_SCAN_ENTRIES = 5000
EXTENSIONS = {".py", ".js", ".mjs", ".cjs", ".ts", ".css", ".html", ".json", ".toml", ".md", ".txt", ".yaml", ".yml"}
BLOCKED_PARTS = {"env", "venv", "node_modules", "__pycache__", "credentials", "secrets", "site-packages"}
RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}
SENSITIVE_NAME = re.compile(r"(?:credential|secret|password|token|api[_-]?key)", re.I)
PRIVATE_ARK_FOLDERS = {"realestate", "hairstyle", "thumbnail", "ecom", "vendor", "example workflows"}
PRIVATE_ARK_FILES = {"variation/prompts_local.py", "web/realestate.js", "web/hairstyle.js", "web/thumbnail.js", "web/ecom.js"}


class DevelopmentError(ValueError):
    """Unsupported source operation, unsafe path or invalid input."""


class DevelopmentConflict(DevelopmentError):
    """The requested source revision no longer matches the file."""


def _reject_link(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        raise DevelopmentError("Source storage cannot contain symbolic links, junctions or reparse points.")
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise DevelopmentError("Source storage cannot contain files with multiple hard links.")


def _root(path):
    path = Path(os.path.abspath(path))
    for ancestor in (*reversed(path.parents), path):
        _reject_link(ancestor)
    return path


def _parts(name):
    if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name:
        raise DevelopmentError("Use a relative source path with forward slashes, up to 240 characters.")
    parts = name.split("/")
    if len(parts) > 16:
        raise DevelopmentError("Source paths may contain at most 16 components.")
    for part in parts:
        if (not part or part.startswith(".") or len(part) > 120 or part[-1] in " ."
                or re.search(r'[<>:"|?*~\x00-\x1f]', part)
                or part.split(".")[0].upper() in RESERVED_NAMES
                or part.casefold() in BLOCKED_PARTS or SENSITIVE_NAME.search(part)):
            raise DevelopmentError("This path is hidden, reserved or protected from source tools.")
    return parts


def _revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _text(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DevelopmentError("Source files must use UTF-8 encoding.") from exc
    if "\0" in text:
        raise DevelopmentError("Binary files cannot be edited with source tools.")
    return text


def _encode(content):
    if not isinstance(content, str) or "\0" in content:
        raise DevelopmentError("Source content must be text without null bytes.")
    try:
        raw = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise DevelopmentError("Source content must be valid UTF-8 text.") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise DevelopmentError("Source files are limited to 1 MiB per operation.")
    return raw


def _limit(value, maximum, label):
    if type(value) is not int or not 1 <= value <= maximum:
        raise DevelopmentError(f"{label} must be between 1 and {maximum}.")
    return value


def _diff(path, before, after):
    value = "".join(difflib.unified_diff(_text(before).splitlines(keepends=True), _text(after).splitlines(keepends=True),
                                      fromfile=f"a/{path}", tofile=f"b/{path}"))
    return {"diff": value[:MAX_RESPONSE_CHARS], "diff_truncated": len(value) > MAX_RESPONSE_CHARS}


class NodeWorkspace:
    def __init__(self, custom_nodes_root, state_dir, allowed_packs):
        self.root = _root(custom_nodes_root)
        if not self.root.is_dir():
            raise DevelopmentError("The configured custom_nodes directory does not exist.")
        if not isinstance(allowed_packs, (list, tuple, set, frozenset)):
            raise DevelopmentError("Configure an explicit list of allowed custom-node pack names.")
        self.allowed_packs = frozenset(allowed_packs)
        for pack in self.allowed_packs:
            if len(_parts(pack)) != 1:
                raise DevelopmentError("An allowed pack must be one directory name.")
        self.backups = _root(Path(state_dir) / "node-backups")
        self.backups.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, name, *, directory=False):
        parts = _parts(name)
        if parts[0] not in self.allowed_packs:
            raise DevelopmentError("This custom-node pack has not been enabled for development.")
        folded = [part.casefold() for part in parts]
        if folded[0] == "comfyui-arkennemasis" and (len(parts) > 1 and folded[1] == "mcp_service"
                                                   or len(parts) > 2 and folded[1:3] == ["web", "mcp"]):
            raise DevelopmentError("The MCP connection and access-control implementation is protected.")
        if folded[0] == "comfyui-arkennemasis" and (len(parts) > 1 and folded[1] in PRIVATE_ARK_FOLDERS
                                                   or "/".join(folded[1:]) in PRIVATE_ARK_FILES):
            raise DevelopmentError("This repository path is marked private and is excluded from MCP source access.")
        if not directory and (len(parts) < 2 or Path(parts[-1]).suffix.casefold() not in EXTENSIONS):
            raise DevelopmentError("Choose a supported text source file inside an allowed node pack.")
        path = self.root.joinpath(*parts)
        _root(self.root)
        for i in range(1, len(parts) + 1):
            _reject_link(self.root.joinpath(*parts[:i]))
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise DevelopmentError("Source path escapes custom_nodes.")
        if path == self.backups or self.backups in path.parents:
            raise DevelopmentError("Source backup storage is protected.")
        return path

    def resolve(self, path, must_exist=True):
        """Resolve an allowed source file for other bounded development operations."""
        resolved = self._path(path)
        if must_exist and not resolved.is_file():
            raise DevelopmentError("The selected source file does not exist.")
        return resolved

    def pack_path(self, pack):
        """Resolve a selected pack; callers must still filter individual file paths."""
        if len(_parts(pack)) != 1:
            raise DevelopmentError("Select one configured custom-node pack name.")
        return self._path(pack, directory=True)

    @staticmethod
    def _read_raw(path):
        _reject_link(path)
        with path.open("rb") as stream:
            raw = stream.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise DevelopmentError("Source files are limited to 1 MiB per operation.")
        _text(raw)
        return raw

    def read(self, path, start_line=1, max_lines=300):
        _limit(start_line, MAX_FILE_BYTES, "start_line")
        _limit(max_lines, 1000, "max_lines")
        with self._lock:
            raw = self._read_raw(self._path(path))
            lines = _text(raw).splitlines(keepends=True)
            content = "".join(lines[start_line - 1:start_line - 1 + max_lines])
            return {"path": path, "revision": _revision(raw), "size_bytes": len(raw), "start_line": start_line,
                    "total_lines": len(lines), "content": content[:MAX_RESPONSE_CHARS],
                    "truncated": len(content) > MAX_RESPONSE_CHARS or start_line - 1 + max_lines < len(lines)}

    def _files(self, pack=None, directory=""):
        if pack is None and directory:
            raise DevelopmentError("Specify a pack when listing a subdirectory.")
        packs = [pack] if pack is not None else sorted(self.allowed_packs)
        folders = deque(self._path(f"{name}/{directory}" if directory else name, directory=True) for name in packs)
        scanned = 0
        while folders and scanned < MAX_SCAN_ENTRIES:
            folder = folders.popleft()
            self._path(folder.relative_to(self.root).as_posix(), directory=True)
            if not folder.exists():
                continue
            with os.scandir(folder) as entries:
                for entry in entries:
                    scanned += 1
                    if scanned > MAX_SCAN_ENTRIES:
                        yield None, None
                        return
                    name = Path(entry.path).relative_to(self.root).as_posix()
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            folders.append(self._path(name, directory=True))
                        elif entry.is_file(follow_symlinks=False):
                            yield name, self._path(name)
                    except DevelopmentError:
                        continue
        if folders:
            yield None, None

    def list(self, pack=None, directory="", limit=200):
        _limit(limit, 500, "limit")
        items = []
        for name, path in self._files(pack, directory):
            if name is None or len(items) == limit:
                return {"files": items, "truncated": True, "scan_limit": MAX_SCAN_ENTRIES}
            items.append({"path": name, "size_bytes": path.stat().st_size})
        return {"files": items, "truncated": False, "scan_limit": MAX_SCAN_ENTRIES}

    def search(self, query, pack=None, limit=50):
        _limit(limit, 200, "limit")
        if not isinstance(query, str) or not query or len(query) > 500:
            raise DevelopmentError("Search for a literal text string between 1 and 500 characters.")
        matches, scanned = [], 0
        for name, path in self._files(pack):
            if name is None:
                return {"matches": matches, "truncated": True, "files_scanned": scanned, "scan_limit": MAX_SCAN_ENTRIES}
            scanned += 1
            try:
                text = _text(self._read_raw(path))
            except (DevelopmentError, OSError):
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if query in line:
                    if len(matches) == limit:
                        return {"matches": matches, "truncated": True, "files_scanned": scanned}
                    index = line.index(query)
                    matches.append({"path": name, "line": number, "text": line[max(0, index - 120):index + 380]})
        return {"matches": matches, "truncated": False, "files_scanned": scanned, "scan_limit": MAX_SCAN_ENTRIES}

    @contextmanager
    def _write_guard(self):
        with self._lock:
            _root(self.backups)
            lock_path = self.backups / ".write.lock"
            _reject_link(lock_path)
            with lock_path.open("a+b") as stream:
                if stream.tell() == 0:
                    stream.write(b"0")
                    stream.flush()
                stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    stream.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _atomic_write(path, raw, create=False):
        _root(path.parent)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".ark-source-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _root(path)
            if path.exists():
                os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
            if create:
                os.link(temporary, path)
            else:
                os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _backup_path(self, name, backup_id):
        self._path(name)
        if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9a-f]{64}", backup_id):
            raise DevelopmentError("Invalid source backup revision.")
        bucket = _revision(os.path.normcase(str(self._path(name))).encode("utf-8"))
        return _root(self.backups / bucket / f"{backup_id}.bin")

    def _backup(self, name, raw):
        revision = _revision(raw)
        path = self._backup_path(name, revision)
        if not path.exists():
            self._atomic_write(path, raw, create=True)
        elif self._read_raw(path) != raw:
            raise DevelopmentError("Existing source backup is damaged; repair backup storage before editing.")
        return revision

    def _current(self, name, expected_revision):
        path = self._path(name)
        if path.exists():
            raw = self._read_raw(path)
            if _revision(raw) != expected_revision:
                raise DevelopmentConflict("Read the file and supply its current revision before changing it.")
            return raw
        if expected_revision is not None:
            raise DevelopmentConflict("The source file no longer exists.")
        return None

    def _write(self, name, raw, expected_revision):
        current = self._current(name, expected_revision)
        result = {"path": name, "revision": _revision(raw), "size_bytes": len(raw),
                  "changed": current != raw, **_diff(name, current or b"", raw)}
        if current == raw:
            return result
        if current is not None:
            result["backup_id"] = self._backup(name, current)
        self._current(name, expected_revision)
        try:
            self._atomic_write(self._path(name), raw, create=current is None)
        except FileExistsError as exc:
            raise DevelopmentConflict("The destination appeared while saving; read it before retrying.") from exc
        return result

    def write(self, path, content, expected_revision=None):
        raw = _encode(content)
        with self._write_guard():
            return self._write(path, raw, expected_revision)

    def patch(self, path, old_text, new_text, expected_revision):
        if not isinstance(old_text, str) or not old_text or not isinstance(new_text, str):
            raise DevelopmentError("Supply nonempty old_text and replacement new_text.")
        with self._write_guard():
            current = self._current(path, expected_revision)
            if current is None:
                raise DevelopmentConflict("The source file no longer exists.")
            content = _text(current)
            if content.count(old_text) != 1:
                raise DevelopmentConflict("old_text must match exactly once; read a larger context before retrying.")
            return self._write(path, _encode(content.replace(old_text, new_text, 1)), expected_revision)

    def delete(self, path, expected_revision):
        with self._write_guard():
            raw = self._current(path, expected_revision)
            if raw is None:
                raise DevelopmentConflict("The source file no longer exists.")
            backup_id = self._backup(path, raw)
            self._current(path, expected_revision)
            self._path(path).unlink()
            return {"path": path, "deleted": True, "backup_id": backup_id, **_diff(path, raw, b"")}

    def rename(self, path, new_path, expected_revision):
        with self._write_guard():
            source, destination = self._path(path), self._path(new_path)
            if source == destination:
                raise DevelopmentError("Choose a different destination path.")
            raw = self._current(path, expected_revision)
            if raw is None:
                raise DevelopmentConflict("The source file no longer exists.")
            if destination.exists():
                raise DevelopmentConflict("Rename never overwrites an existing destination.")
            backup_id = self._backup(path, raw)
            self._atomic_write(destination, raw, create=True)
            try:
                self._current(path, expected_revision)
            except DevelopmentConflict:
                if self._read_raw(self._path(new_path)) == raw:
                    self._path(new_path).unlink()
                raise
            self._path(path).unlink()
            return {"path": new_path, "previous_path": path, "revision": _revision(raw), "backup_id": backup_id}

    def history(self, path, limit=20):
        _limit(limit, 100, "limit")
        folder = self._backup_path(path, "0" * 64).parent
        if not folder.exists():
            return {"path": path, "backups": []}
        items = []
        for backup in folder.iterdir():
            if not re.fullmatch(r"[0-9a-f]{64}\.bin", backup.name):
                continue
            _reject_link(backup)
            info = backup.stat()
            items.append({"backup_id": backup.stem, "size_bytes": info.st_size, "saved_at": info.st_mtime})
        items.sort(key=lambda item: item["saved_at"], reverse=True)
        return {"path": path, "backups": items[:limit], "truncated": len(items) > limit}

    def restore(self, path, backup_id, expected_revision=None):
        with self._write_guard():
            raw = self._read_raw(self._backup_path(path, backup_id))
            if _revision(raw) != backup_id:
                raise DevelopmentError("Source backup content does not match its revision.")
            return self._write(path, raw, expected_revision)

    def validate(self, path):
        raw = self._read_raw(self._path(path))
        content = _text(raw).removeprefix("\ufeff")
        extension = Path(path).suffix.casefold()
        result = {"path": path, "revision": _revision(raw), "executed": False,
                  "registration_verified": False, "runtime_verified": False}
        try:
            if extension == ".py":
                tree = ast.parse(content, filename=path)
                result.update(valid=True, check="python_syntax", classes=[node.name for node in tree.body if isinstance(node, ast.ClassDef)],
                              declares_node_mapping=any(isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "NODE_CLASS_MAPPINGS" for target in node.targets) for node in tree.body))
            elif extension == ".json":
                json.loads(content)
                result.update(valid=True, check="json_syntax")
            elif extension == ".toml":
                tomllib.loads(content)
                result.update(valid=True, check="toml_syntax")
            else:
                result.update(valid=None, check="utf8_only", message="Syntax validation is unavailable for this file type; no code was executed.")
        except (SyntaxError, ValueError, RecursionError) as exc:
            result.update(valid=False, error=str(exc)[:2000], line=getattr(exc, "lineno", None))
        return result

    def scaffold(self, pack, class_name, display_name=""):
        if not isinstance(class_name, str) or not class_name.isascii() or not class_name.isidentifier() or keyword.iskeyword(class_name) or len(class_name) > 80:
            raise DevelopmentError("Use a Python class identifier of at most 80 ASCII characters.")
        if not isinstance(display_name, str) or len(display_name) > 120 or any(ord(char) < 32 for char in display_name):
            raise DevelopmentError("Display name must be plain text up to 120 characters.")
        if len(_parts(pack)) != 1:
            raise DevelopmentError("Scaffolding creates one new, explicitly allowed node pack.")
        folder = self._path(pack, directory=True)
        label = display_name or class_name
        source = (f"class {class_name}:\n    @classmethod\n    def INPUT_TYPES(cls):\n"
                  "        return {\"required\": {\"text\": (\"STRING\", {\"multiline\": True})}}\n\n"
                  "    RETURN_TYPES = (\"STRING\",)\n    FUNCTION = \"process\"\n    CATEGORY = \"Arkennemasis/Custom\"\n\n"
                  "    def process(self, text):\n        return (text,)\n\n\n"
                  f"NODE_CLASS_MAPPINGS = {{{class_name!r}: {class_name}}}\n"
                  f"NODE_DISPLAY_NAME_MAPPINGS = {{{class_name!r}: {label!r}}}\n")
        with self._write_guard():
            if folder.exists():
                raise DevelopmentConflict("Scaffolding requires a new folder; existing node packs are preserved.")
            folder.mkdir()
            created = []
            try:
                for filename, content in {"__init__.py": source, "README.md": f"# {label}\n\nA ComfyUI custom node. Restart ComfyUI after editing Python source.\n",
                                          "requirements.txt": ""}.items():
                    path = f"{pack}/{filename}"
                    self._atomic_write(self._path(path), _encode(content), create=True)
                    created.append(path)
            except (OSError, DevelopmentError):
                for path in created:
                    self._path(path).unlink()
                folder.rmdir()
                raise
            return {"pack": pack, "files": created, "class_name": class_name, "restart_required": True, "runtime_verified": False}
