"""Revision-checked launch overrides applied only by the explicit launch/restart path."""

import ast
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

if os.name == "nt":
    import msvcrt
else:
    import fcntl


MAX_CONFIG_BYTES = 8192
DEFAULT_COMFY_ROOT = Path(__file__).resolve().parents[3]
CHOICES = {
    "vram_mode": {"auto": None, "normal": "--normalvram", "high": "--highvram", "low": "--lowvram", "none": "--novram", "gpu_only": "--gpu-only", "cpu": "--cpu"},
    "precision": {"auto": None, "fp32": "--force-fp32", "fp16": "--force-fp16"},
    "cache_mode": {"auto": None, "ram": "--cache-ram", "classic": "--cache-classic", "lru": "--cache-lru", "none": "--cache-none"},
    "preview_method": {name: "--preview-method" for name in ("none", "auto", "latent2rgb", "taesd")},
}
NUMBERS = {"cache_lru_size": (1, 4096, "--cache-lru"), "preview_size": (64, 2048, "--preview-size")}
ARITY = {flag: 0 for values in CHOICES.values() for flag in values.values() if flag}
ARITY.update({"--cache-lru": 1, "--preview-method": 1, "--preview-size": 1, "--cache-ram": "*"})


class LaunchConfigError(ValueError):
    pass


class LaunchConfigConflict(LaunchConfigError):
    pass


def _plain_path(path):
    path = Path(os.path.abspath(path))
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise LaunchConfigError("Launch settings cannot use symlinks or junctions.")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise LaunchConfigError("Launch settings cannot use hard-linked files.")
    return path


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _revision(raw):
    return hashlib.sha256(raw).hexdigest()


def _supported(comfy_root):
    path = Path(comfy_root) / "comfy" / "cli_args.py"
    if path.stat().st_size > 1024 * 1024:
        raise LaunchConfigError("The installed ComfyUI launch parser is too large to inspect.")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {arg.value for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument"
            for arg in node.args if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value.startswith("--")}


def _validate(overrides, flags):
    if not isinstance(overrides, dict) or set(overrides) - (CHOICES.keys() | NUMBERS.keys()):
        raise LaunchConfigError("Only VRAM mode, precision, cache and preview settings are supported.")
    for key, value in overrides.items():
        if key in CHOICES:
            if not isinstance(value, str) or value not in CHOICES[key]:
                raise LaunchConfigError(f"Unsupported value for {key}.")
            flag = CHOICES[key][value]
        else:
            low, high, flag = NUMBERS[key]
            if type(value) is not int or not low <= value <= high:
                raise LaunchConfigError(f"{key} must be an integer between {low} and {high}.")
        if flag and flag not in flags:
            raise LaunchConfigError(f"The installed ComfyUI version does not support {key}={value}.")
    if "cache_lru_size" in overrides and overrides.get("cache_mode") != "lru":
        raise LaunchConfigError("Set cache_mode=lru when configuring cache_lru_size.")


def _atomic(path, raw):
    _plain_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".launch-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class LaunchConfig:
    def __init__(self, state_dir, comfy_root=None):
        self.state_dir = _plain_path(state_dir)
        self.path = self.state_dir / "launch-overrides.json"
        self.backups = self.state_dir / "launch-backups"
        self.flags = _supported(comfy_root or DEFAULT_COMFY_ROOT)

    @contextmanager
    def _locked(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        path = _plain_path(self.state_dir / "launch-overrides.lock")
        with path.open("a+b") as handle:
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _read(self, path=None):
        path = _plain_path(path or self.path)
        try:
            with path.open("rb") as handle:
                raw = handle.read(MAX_CONFIG_BYTES + 1)
        except FileNotFoundError:
            if path != self.path:
                raise LaunchConfigError("Unknown launch-settings backup.")
            raw = b"{}"
        if len(raw) > MAX_CONFIG_BYTES:
            raise LaunchConfigError("Launch settings exceed the supported size.")
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise LaunchConfigError("Launch settings are not valid JSON.") from exc
        _validate(value, self.flags)
        return raw, value

    def get(self):
        raw, value = self._read()
        supported = {key: [name for name, flag in values.items() if flag is None or flag in self.flags] for key, values in CHOICES.items()}
        supported.update({key: {"minimum": low, "maximum": high} for key, (low, high, flag) in NUMBERS.items() if flag in self.flags})
        backups = []
        if _plain_path(self.backups).is_dir():
            backups = sorted(path.stem for path in self.backups.glob("*.json") if re.fullmatch(r"[0-9a-f]{64}", path.stem))
        return {"revision": _revision(raw), "overrides": value, "supported": supported,
                "backups": backups, "activation": "next_explicit_restart_or_launch", "restart_required": True}

    def _write(self, value, expected_revision):
        before, previous = self._read()
        if expected_revision != _revision(before):
            raise LaunchConfigConflict("Launch settings changed. Read the current revision before editing.")
        _validate(value, self.flags)
        if value != previous:
            _plain_path(self.backups).mkdir(parents=True, exist_ok=True)
            backup = self.backups / f"{_revision(before)}.json"
            if not backup.exists():
                _atomic(backup, before)
            _atomic(self.path, _encode(value))
        return self.get()

    def patch(self, changes, expected_revision):
        if not isinstance(changes, dict) or set(changes) - (CHOICES.keys() | NUMBERS.keys()):
            raise LaunchConfigError("Unsupported launch setting.")
        with self._locked():
            _, value = self._read()
            for key, setting in changes.items():
                if setting is None:
                    value.pop(key, None)
                else:
                    value[key] = setting
            return self._write(value, expected_revision)

    def restore(self, backup_revision, expected_revision):
        if not isinstance(backup_revision, str) or not re.fullmatch(r"[0-9a-f]{64}", backup_revision):
            raise LaunchConfigError("Use a backup revision returned by get_launch_settings.")
        with self._locked():
            raw, value = self._read(self.backups / f"{backup_revision}.json")
            if _revision(raw) != backup_revision:
                raise LaunchConfigError("The launch-settings backup checksum does not match.")
            return self._write(value, expected_revision)


def apply_overrides(argv, state_dir, comfy_root=None):
    """Compile trusted original arguments; callers launch directly without a shell."""
    config = LaunchConfig(state_dir, comfy_root)
    _, overrides = config._read()
    if not overrides:
        return list(argv)
    remove = set()
    for key in overrides:
        if key in CHOICES:
            remove.update(flag for flag in CHOICES[key].values() if flag)
        else:
            remove.add(NUMBERS[key][2])
    result = []
    index = 0
    while index < len(argv):
        argument = argv[index]
        flag = argument.split("=", 1)[0]
        index += 1
        if flag not in remove:
            result.append(argument)
            continue
        if "=" in argument:
            continue
        count = ARITY[flag]
        if count == "*":
            while index < len(argv) and re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", argv[index]):
                index += 1
        elif count and index < len(argv) and not argv[index].startswith("--"):
            index += count
    for key, choices in CHOICES.items():
        if key not in overrides:
            continue
        flag = choices[overrides[key]]
        if flag:
            result.append(flag)
            if key == "preview_method":
                result.append(overrides[key])
            elif flag == "--cache-lru":
                result.append(str(overrides.get("cache_lru_size", 128)))
    if "preview_size" in overrides:
        result.extend(["--preview-size", str(overrides["preview_size"])])
    return result
