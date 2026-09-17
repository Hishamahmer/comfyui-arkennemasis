"""Revision-checked storage for ComfyUI workflow documents."""

from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MAX_WORKFLOW_BYTES = 16 * 1024 * 1024
MAX_PATCH_OPERATIONS = 500
_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL"} | {f"{p}{i}" for p in ("COM", "LPT") for i in range(1, 10)}


class WorkflowError(ValueError):
    """An unsafe name, malformed graph, or conflicting revision."""


class RevisionConflict(WorkflowError):
    pass


def workflow_format(workflow):
    if not isinstance(workflow, dict):
        raise WorkflowError("A workflow must be a JSON object.")
    if isinstance(workflow.get("nodes"), list) and isinstance(workflow.get("links"), list):
        return "ui"
    if all(isinstance(node, dict) and isinstance(node.get("class_type"), str)
           and isinstance(node.get("inputs"), dict) for node in workflow.values()):
        return "api"
    raise WorkflowError("Expected a UI workflow with nodes and links arrays, or an API graph with class_type and inputs on each node.")


def _encode(workflow):
    workflow_format(workflow)
    try:
        raw = json.dumps(workflow, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError) as exc:
        raise WorkflowError("The workflow must contain finite JSON values.") from exc
    if len(raw) > MAX_WORKFLOW_BYTES:
        raise WorkflowError("Workflow exceeds the 16 MiB document limit.")
    return raw


def workflow_revision(workflow):
    return hashlib.sha256(_encode(workflow)).hexdigest()


def _reject_link(path):
    if path.is_symlink():
        raise WorkflowError("Workflow storage cannot contain symbolic links.")
    if path.exists():
        attrs = getattr(path.lstat(), "st_file_attributes", 0)
        if attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise WorkflowError("Workflow storage cannot contain junctions or reparse points.")


def _safe_root(root):
    root = Path(os.path.abspath(root))
    for part in (*reversed(root.parents), root):
        _reject_link(part)
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _pointer(path):
    if not isinstance(path, str) or not path.startswith("/"):
        raise WorkflowError("Patch paths must be JSON pointers below the document root.")
    result = []
    for part in path[1:].split("/"):
        if re.search(r"~(?![01])", part):
            raise WorkflowError("Invalid JSON pointer escape.")
        result.append(part.replace("~1", "/").replace("~0", "~"))
    if len(result) > 128:
        raise WorkflowError("Patch path exceeds the nesting limit.")
    return result


def _array_index(part, length, append=False):
    if part == "-" and append:
        return length
    if not re.fullmatch(r"0|[1-9][0-9]*", part):
        raise WorkflowError("Array paths require a non-negative index, or '-' when adding.")
    index = int(part)
    if index >= length + int(append):
        raise WorkflowError("Patch array index is out of range.")
    return index


def _json_equal(left, right):
    # JSON booleans and numbers are distinct even though Python equates True and 1.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(v, right[k]) for k, v in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def apply_patch(workflow, operations):
    """Apply add/remove/replace/test atomically; callers persist the returned copy."""
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_PATCH_OPERATIONS:
        raise WorkflowError("Provide between 1 and 500 patch operations.")
    document = deepcopy(workflow)
    for operation in operations:
        if not isinstance(operation, dict) or operation.get("op") not in {"add", "remove", "replace", "test"}:
            raise WorkflowError("Supported patch operations are add, remove, replace, and test.")
        op = operation["op"]
        if op != "remove" and "value" not in operation:
            raise WorkflowError(f"The {op} operation requires value.")
        parts = _pointer(operation.get("path"))
        parent = document
        for part in parts[:-1]:
            if isinstance(parent, list):
                parent = parent[_array_index(part, len(parent))]
            elif isinstance(parent, dict) and part in parent:
                parent = parent[part]
            else:
                raise WorkflowError("Patch parent does not exist.")
        key = parts[-1]
        if isinstance(parent, list):
            key = _array_index(key, len(parent), append=op == "add")
            exists = key < len(parent)
        elif isinstance(parent, dict):
            exists = key in parent
        else:
            raise WorkflowError("Patch parent must be an object or array.")
        if op != "add" and not exists:
            raise WorkflowError("Patch target does not exist.")
        if op == "test":
            if not _json_equal(parent[key], operation["value"]):
                raise RevisionConflict("Patch test failed; the workflow was not changed.")
        elif op == "remove":
            del parent[key]
        elif op == "add" and isinstance(parent, list):
            parent.insert(key, deepcopy(operation["value"]))
        else:
            parent[key] = deepcopy(operation["value"])
    if workflow_format(document) != workflow_format(workflow):
        raise WorkflowError("A patch cannot change between UI and API workflow formats.")
    _encode(document)
    return document


class WorkflowStore:
    def __init__(self, root: Path, backup_root: Path):
        self.root = _safe_root(root)
        self.backup_root = _safe_root(backup_root)
        if self.root == self.backup_root or self.root in self.backup_root.parents or self.backup_root in self.root.parents:
            raise WorkflowError("Workflow and backup roots must be separate directories.")
        self._lock = threading.RLock()

    def _path(self, name):
        if not isinstance(name, str) or not name or len(name) > 240 or "\\" in name:
            raise WorkflowError("Use a relative workflow name with forward slashes, up to 240 characters.")
        parts = name.split("/")
        if len(parts) > 8 or not name.lower().endswith(".json"):
            raise WorkflowError("Workflow names must end in .json and use at most eight path components.")
        for part in parts:
            if (not part or part in {".", ".."} or len(part) > 120 or part[-1] in " ."
                    or re.search(r'[<>:"|?*\x00-\x1f]', part)
                    or part.split(".")[0].upper() in _RESERVED_NAMES):
                raise WorkflowError("Unsafe workflow path component.")
        path = self.root.joinpath(*parts)
        _reject_link(self.root)
        for ancestor in [self.root.joinpath(*parts[:i]) for i in range(1, len(parts) + 1)]:
            _reject_link(ancestor)
        if not path.resolve().is_relative_to(self.root):
            raise WorkflowError("Workflow path escapes its storage root.")
        return path

    def _read_path(self, path):
        with path.open("rb") as stream:
            raw = stream.read(MAX_WORKFLOW_BYTES + 1)
        if len(raw) > MAX_WORKFLOW_BYTES:
            raise WorkflowError("Workflow exceeds the 16 MiB document limit.")
        try:
            workflow = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            raise WorkflowError("Workflow is not valid JSON.") from exc
        _encode(workflow)
        return workflow

    def _result(self, name, workflow):
        return {"name": name, "format": workflow_format(workflow), "revision": workflow_revision(workflow), "workflow": workflow}

    def list(self):
        items = []
        with self._lock:
            _reject_link(self.root)
            for folder, dirs, files in os.walk(self.root, followlinks=False):
                dirs[:] = [name for name in dirs if not self._is_link(Path(folder) / name)]
                for filename in files:
                    if not filename.lower().endswith(".json"):
                        continue
                    name = (Path(folder) / filename).relative_to(self.root).as_posix()
                    try:
                        item = self.read(name)
                        item.pop("workflow")
                    except (WorkflowError, OSError) as exc:
                        item = {"name": name, "error": str(exc)}
                    items.append(item)
        return sorted(items, key=lambda item: item["name"].casefold())

    @staticmethod
    def _is_link(path):
        try:
            _reject_link(path)
        except WorkflowError:
            return True
        return False

    def read(self, name):
        with self._lock:
            return self._result(name, self._read_path(self._path(name)))

    def _backup_path(self, path, backup_id):
        if not isinstance(backup_id, str) or not re.fullmatch(r"[0-9a-f]{64}", backup_id):
            raise WorkflowError("Invalid backup id.")
        bucket = hashlib.sha256(os.path.normcase(str(path)).encode("utf-8")).hexdigest()
        backup = self.backup_root / bucket / f"{backup_id}.json"
        for part in (self.backup_root, backup.parent, backup):
            _reject_link(part)
        return backup

    @staticmethod
    def _atomic_write(path, raw, *, create=False):
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".ark-mcp-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            _reject_link(path)
            if create:
                # Publish the complete document only if the destination is still absent.
                os.link(temporary, path)
            else:
                os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def _write_guard(self):
        with self._lock:
            lock_path = self.backup_root / ".write.lock"
            _reject_link(self.backup_root)
            _reject_link(lock_path)
            with lock_path.open("a+b") as lock_file:
                if lock_file.tell() == 0:
                    lock_file.write(b"0")
                    lock_file.flush()
                lock_file.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    lock_file.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def write(self, name, workflow, expected_revision=None):
        with self._write_guard():
            return self._write(name, workflow, expected_revision)

    def _write(self, name, workflow, expected_revision=None):
        raw = _encode(workflow)
        with self._lock:
            path = self._path(name)
            backup_id = None
            if path.exists():
                current = self._read_path(path)
                backup_id = workflow_revision(current)
                if expected_revision != backup_id:
                    raise RevisionConflict("Read the workflow and supply its current revision before overwriting it.")
                if raw == _encode(current):
                    return self._result(name, current)
                backup_path = self._backup_path(path, backup_id)
                if not backup_path.exists():
                    self._atomic_write(backup_path, _encode(current), create=True)
                if workflow_revision(self._read_path(self._path(name))) != expected_revision:
                    raise RevisionConflict("Workflow changed while saving; read it again before retrying.")
            elif expected_revision is not None:
                raise RevisionConflict("Workflow no longer exists; create it without an expected revision.")
            self._atomic_write(self._path(name), raw, create=backup_id is None)
            result = self._result(name, workflow)
            if backup_id is not None:
                result["backup_id"] = backup_id
            return result

    def patch(self, name, operations, expected_revision):
        with self._write_guard():
            current = self.read(name)
            if expected_revision != current["revision"]:
                raise RevisionConflict("Workflow revision changed; read it again before editing.")
            return self._write(name, apply_patch(current["workflow"], operations), expected_revision)

    def restore(self, name, backup_id, expected_revision):
        with self._write_guard():
            path = self._path(name)
            workflow = self._read_path(self._backup_path(path, backup_id))
            if workflow_revision(workflow) != backup_id:
                raise WorkflowError("Backup content does not match its revision.")
            return self._write(name, workflow, expected_revision)
