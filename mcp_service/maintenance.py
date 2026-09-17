"""Named Git operations and reviewable wheel installation plans for ComfyUI.

These controls are not an operating-system sandbox. Installed node/package code
runs with ComfyUI's permissions. No command string or caller-provided flags run.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

from .development import DevelopmentConflict, DevelopmentError, _reject_link, _root


MAX_OUTPUT = 64 * 1024
MAX_REPORT = 8 * 1024 * 1024
PROTECTED_PACKAGES = {"torch", "torchvision", "torchaudio", "numpy", "pip", "setuptools", "wheel",
                      "triton", "triton-windows", "xformers", "sageattention", "comfy-kitchen",
                      "comfyui-frontend-package", "comfyui-workflow-templates"}
SPEC = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]{0,99})==([A-Za-z0-9][A-Za-z0-9.!+_-]{0,99})\Z")
HEAD = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


class MaintenanceError(DevelopmentError):
    pass


def _name(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def _redact(value):
    value = re.sub(r"(?i)(https?://)[^\s/\"'<>]+@", r"\1[redacted]@", value)
    value = re.sub(r"(?i)(/connect/)[^/\s]+", r"\1[redacted]", value)
    value = re.sub(r"(?i)(bearer\s+)[a-z0-9._~-]+", r"\1[redacted]", value)
    value = re.sub(r'''(?i)((?:token|password|secret|api[_-]?key)["']?\s*[=:]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;}\]]+)''',
                   lambda match: match[1] + (match[2][0] + "[redacted]" + match[2][0]
                                             if match[2][0] in "\"'" else "[redacted]"), value)
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", value)


def _public(value):
    if isinstance(value, str):
        return _redact(value)
    if isinstance(value, list):
        return [_public(item) for item in value]
    if isinstance(value, dict):
        return {key: _public(item) for key, item in value.items()}
    return value


def _github_url(value):
    if not isinstance(value, str) or not re.fullmatch(r"https://github\.com/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}(?:\.git)?", value):
        raise MaintenanceError("Use a public HTTPS github.com owner/repository URL without credentials, query strings or fragments.")
    return value


def _specs(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 30:
        raise MaintenanceError("Supply 1 to 30 exact package==version specifications.")
    result = {}
    for value in values:
        match = SPEC.fullmatch(value) if isinstance(value, str) else None
        if not match:
            raise MaintenanceError("Only exact package==version specifications are supported; URLs, extras, paths and flags are excluded.")
        name, version = _name(match[1]), match[2]
        if name in result:
            raise MaintenanceError("Each package may appear only once in an installation plan.")
        result[name] = version
    return [f"{name}=={version}" for name, version in sorted(result.items())]


class Maintenance:
    def __init__(self, workspace, comfy_python, state_dir):
        self.workspace = workspace
        # The owner-selected interpreter may be a venv symlink. Keep its
        # invocation path: executing the resolved system binary loses the venv.
        self.python = Path(os.path.abspath(comfy_python))
        if not self.python.is_file():
            raise MaintenanceError("Configure the Python executable actually used by ComfyUI.")
        self.state = _root(Path(state_dir) / "maintenance")
        self.state.mkdir(parents=True, exist_ok=True)
        self.hooks = _root(self.state / "disabled-hooks")
        self.hooks.mkdir(exist_ok=True)
        if any(self.hooks.iterdir()):
            raise MaintenanceError("The disabled-hooks directory must be empty.")
        self.empty_attributes = self.state / "empty-attributes"
        self.empty_attributes.touch(exist_ok=True)
        self.git = shutil.which("git")

    @staticmethod
    def _environment():
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("GIT_", "PIP_", "PYTHON"))}
        env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                   GIT_ASKPASS="", GIT_OPTIONAL_LOCKS="0", GIT_LFS_SKIP_SMUDGE="1", PIP_CONFIG_FILE=os.devnull)
        return env

    def _run(self, args, cwd, *, timeout=60, max_bytes=MAX_OUTPUT, allow_failure=False, env=None):
        process = subprocess.Popen(args, cwd=cwd, env=env or self._environment(), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        captured = bytearray()
        overflow = threading.Event()

        def read_output():
            while chunk := process.stdout.read(16384):
                remaining = max_bytes - len(captured)
                captured.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    overflow.set()
                    process.kill()
                    break

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise MaintenanceError(f"Maintenance operation exceeded its {timeout}-second limit; inspect the installation before retrying.") from exc
        finally:
            reader.join(timeout=5)
            if not reader.is_alive():
                process.stdout.close()
        if reader.is_alive():
            raise MaintenanceError("Maintenance process ended but a child still owns its output; inspect it before retrying.")
        if overflow.is_set():
            raise MaintenanceError("Maintenance output exceeded its bounded response limit; narrow the request.")
        output = captured.decode("utf-8", errors="replace")
        if process.returncode and not allow_failure:
            raise MaintenanceError(_redact(output)[-8000:] or "Maintenance command failed.")
        return process.returncode, output

    def _git_base(self):
        if not self.git:
            raise MaintenanceError("Git is not installed or is unavailable to ComfyUI.")
        return [self.git, "--no-pager", "--literal-pathspecs", "-c", f"core.hooksPath={self.hooks}",
                "-c", "core.fsmonitor=false", "-c", f"core.attributesFile={self.empty_attributes}",
                "-c", "credential.helper=", "-c", "credential.interactive=false", "-c", "core.askPass=",
                "-c", "diff.external=", "-c", "commit.gpgSign=false", "-c", "tag.gpgSign=false",
                "-c", "protocol.allow=never", "-c", "protocol.https.allow=always", "-c", "http.followRedirects=false",
                "-c", "submodule.recurse=false", "-c", "fetch.recurseSubmodules=false", "-c", "core.protectNTFS=true"]

    def _repo(self, pack):
        folder = self.workspace.pack_path(pack)
        metadata = _root(folder / ".git")
        if not metadata.is_dir():
            raise MaintenanceError("Select a standalone Git node-pack repository; linked worktrees and submodules are excluded.")
        for path in (metadata / "config", metadata / "index", metadata / "HEAD", metadata / "objects", metadata / "refs"):
            _root(path)
        for path in (metadata / "commondir", metadata / "objects" / "info" / "alternates", metadata / "config.worktree"):
            if path.exists():
                raise MaintenanceError("External Git storage and worktree configuration are not supported.")
        for ref_root in (metadata / "refs", metadata / "logs", metadata / "objects" / "info"):
            scanned = 0
            for directory, folders, files in os.walk(ref_root, followlinks=False):
                for name in folders + files:
                    _reject_link(Path(directory) / name)
                    scanned += 1
                    if scanned > 20000:
                        raise MaintenanceError("Git reference storage exceeds the maintenance inspection limit.")
        _, config = self._run(self._git_base() + ["config", "--local", "--no-includes", "--null", "--list"], folder)
        overrides = []
        for entry in config.split("\0"):
            key = entry.split("\n", 1)[0]
            folded = key.lower()
            if (folded.startswith(("include.", "includeif.", "url.")) or folded == "core.worktree"
                    or folded == "core.bare" and entry.split("\n", 1)[-1] != "false"):
                raise MaintenanceError("Repository includes, URL rewrites and external worktrees must be removed before MCP Git operations.")
            if folded.startswith("filter."):
                overrides += ["-c", key + ("=false" if folded.endswith(".required") else "=")]
        return folder, overrides

    def _git(self, pack, args, *, timeout=60, max_bytes=MAX_OUTPUT, allow_failure=False):
        folder, overrides = self._repo(pack)
        return self._run(self._git_base() + overrides + args, folder, timeout=timeout,
                         max_bytes=max_bytes, allow_failure=allow_failure)

    def _head(self, pack, expected=None):
        _, output = self._git(pack, ["rev-parse", "--verify", "HEAD"])
        head = output.strip()
        if not HEAD.fullmatch(head):
            raise MaintenanceError("This repository has no valid commit yet.")
        if expected is not None and (not isinstance(expected, str) or not HEAD.fullmatch(expected) or head != expected):
            raise DevelopmentConflict("Repository HEAD changed; inspect its current status and diff before retrying.")
        return head

    def _source(self, pack, path, *, must_exist=False):
        self.workspace.resolve(f"{pack}/{path}", must_exist=must_exist)
        return path

    @staticmethod
    def _whole_pack(pack):
        if pack.casefold() == "comfyui-arkennemasis":
            raise MaintenanceError("Whole-repository clone/update of Arkennemasis is excluded because it contains the active MCP and private paths.")

    def _clean(self, pack):
        _, output = self._git(pack, ["status", "--porcelain=v1", "-z", "--untracked-files=all"])
        if output:
            raise DevelopmentConflict("Repository has local or staged changes; preserve or commit them before updating.")

    def _checkout_storage(self, pack):
        root = self.workspace.pack_path(pack)
        scanned = 0
        for directory, folders, files in os.walk(root, followlinks=False):
            if Path(directory) == root:
                folders[:] = [name for name in folders if name != ".git"]
            for name in folders + files:
                _reject_link(Path(directory) / name)
                scanned += 1
                if scanned > 50000:
                    raise MaintenanceError("Node-pack checkout exceeds the maintenance inspection limit.")

    def git_status(self, pack):
        _, raw = self._git(pack, ["status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"])
        files, hidden = [], 0
        for item in raw.split("\0"):
            if not item:
                continue
            try:
                path = self._source(pack, item[3:])
            except DevelopmentError:
                hidden += 1
                continue
            files.append({"path": path, "index_status": item[0], "worktree_status": item[1]})
        _, branch = self._git(pack, ["symbolic-ref", "--quiet", "--short", "HEAD"], allow_failure=True)
        return {"pack": pack, "head": self._head(pack), "branch": branch.strip() or None,
                "files": files, "excluded_changes": hidden, "clean": not bool(raw)}

    def git_log(self, pack, limit=20):
        if type(limit) is not int or not 1 <= limit <= 50:
            raise MaintenanceError("Log limit must be between 1 and 50.")
        _, output = self._git(pack, ["log", f"-{limit}", "--format=%H%x00%ct%x00%s%x00"])
        fields = output.split("\0")
        commits = [{"head": fields[i].strip(), "timestamp": int(fields[i + 1]), "subject": _redact(fields[i + 2])[:1000]}
                   for i in range(0, len(fields) - 2, 3)]
        return {"pack": pack, "commits": commits}

    def git_diff(self, pack, path):
        path = self._source(pack, path)
        _, output = self._git(pack, ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", "HEAD", "--", path])
        return {"pack": pack, "path": path, "head": self._head(pack), "diff": _redact(output),
                "note": "Diff compares tracked file contents with HEAD; read untracked files through source tools."}

    def _tree_safe(self, pack, ref):
        _, output = self._git(pack, ["ls-tree", "-r", "-z", ref], max_bytes=MAX_REPORT)
        for entry in output.split("\0"):
            if not entry:
                continue
            metadata, path = entry.split("\t", 1)
            if metadata.split()[0] in {"120000", "160000"}:
                raise MaintenanceError("Repository contains symlinks or submodules; automatic checkout is excluded.")
            parts = path.replace("\\", "/").split("/")
            if any(part in {"", ".", ".."} or ":" in part or part.casefold() == ".git" for part in parts):
                raise MaintenanceError("Repository contains a path unsuitable for automatic checkout.")

    def git_clone(self, pack, url):
        self._whole_pack(pack)
        url = _github_url(url)
        folder = self.workspace.pack_path(pack)
        with self.workspace._write_guard():
            if folder.exists():
                raise DevelopmentConflict("Clone requires a new, explicitly allowed node-pack directory.")
            self._run(self._git_base() + ["clone", "--no-checkout", "--no-recurse-submodules", "--", url, str(folder)],
                      self.workspace.root, timeout=600)
            self._tree_safe(pack, "HEAD")
            self._checkout_storage(pack)
            self._git(pack, ["reset", "--hard", "HEAD"])
            return {"pack": pack, "head": self._head(pack), "installed": True,
                    "dependencies_installed": False, "restart_required": True}

    def git_update(self, pack, expected_head):
        self._whole_pack(pack)
        with self.workspace._write_guard():
            before = self._head(pack, expected_head)
            self._checkout_storage(pack)
            self._clean(pack)
            _, origin = self._git(pack, ["config", "--local", "--get", "remote.origin.url"])
            url = _github_url(origin.strip())
            _, branch = self._git(pack, ["symbolic-ref", "--quiet", "--short", "HEAD"])
            branch = branch.strip()
            self._git(pack, ["check-ref-format", "--branch", branch])
            self._git(pack, ["fetch", "--no-tags", "--no-recurse-submodules", "--", url, f"refs/heads/{branch}"], timeout=600)
            self._tree_safe(pack, "FETCH_HEAD")
            self._head(pack, expected_head)
            self._clean(pack)
            self._checkout_storage(pack)
            self._git(pack, ["merge", "--ff-only", "--no-edit", "FETCH_HEAD"])
            return {"pack": pack, "previous_head": before, "head": self._head(pack),
                    "dependencies_installed": False, "restart_required": True}

    def git_branch(self, pack, name, expected_head):
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,100}", name):
            raise MaintenanceError("Use a plain Git branch name up to 101 characters.")
        with self.workspace._write_guard():
            head = self._head(pack, expected_head)
            self._git(pack, ["check-ref-format", "--branch", name])
            self._git(pack, ["branch", "--", name, head])
            return {"pack": pack, "branch": name, "head": head, "checked_out": False}

    def git_commit(self, pack, paths, message, expected_head):
        if not isinstance(paths, list) or not 1 <= len(paths) <= 100 or len(set(paths)) != len(paths):
            raise MaintenanceError("Select 1 to 100 distinct source file paths to commit.")
        paths = [self._source(pack, path) for path in paths]
        if not isinstance(message, str) or not message.strip() or len(message) > 2000 or "\0" in message:
            raise MaintenanceError("Provide a commit message of 1 to 2000 characters.")
        with self.workspace._write_guard():
            before = self._head(pack, expected_head)
            self._git(pack, ["add", "--", *paths])
            self._head(pack, expected_head)
            self._git(pack, ["-c", "user.name=Arkennemasis MCP", "-c", "user.email=arkennemasis@localhost",
                             "commit", "--only", "--no-verify", "--no-gpg-sign", "-m", message, "--", *paths])
            return {"pack": pack, "previous_head": before, "head": self._head(pack), "paths": paths,
                    "note": "Committed selected working-tree files; unrelated staged changes were preserved. Nothing was pushed."}

    def _pip(self, args, *, timeout=120, max_bytes=MAX_REPORT, allow_failure=False):
        return self._run([str(self.python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-input", *args],
                         self.state, timeout=timeout, max_bytes=max_bytes, allow_failure=allow_failure)

    def _manifest(self):
        script = ("import importlib.metadata as m,json,sys,hashlib; "
                  "print(json.dumps({'executable':sys.executable,'prefix':sys.prefix,'version':sys.version,"
                  "'packages':sorted([{'name':d.metadata['Name'],'version':d.version,'requires_dist':d.requires or [],"
                  "'origin_fingerprint':hashlib.sha256((d.read_text('direct_url.json') or '').encode()).hexdigest()} "
                  "for d in m.distributions() if d.metadata['Name']],key=lambda d:d['name'].lower())}))")
        _, output = self._run([str(self.python), "-I", "-c", script], self.state, max_bytes=MAX_REPORT)
        manifest = json.loads(output)
        manifest["packages"] = sorted([{**item, "name": _name(item["name"])}
                                       for item in manifest["packages"]], key=lambda item: (item["name"], item["version"]))
        manifest["interpreter_size"] = self.python.stat().st_size
        manifest["interpreter_modified_ns"] = self.python.stat().st_mtime_ns
        manifest["interpreter_target"] = str(self.python.resolve(strict=True))
        return manifest

    @staticmethod
    def _fingerprint(manifest):
        return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()

    def package_inspect(self):
        manifest = self._manifest()
        code, check = self._pip(["check"], allow_failure=True)
        return _public({**manifest, "fingerprint": self._fingerprint(manifest),
                "dependency_check_ok": code == 0, "dependency_check": _redact(check)[:MAX_OUTPUT],
                "protected_packages": sorted(PROTECTED_PACKAGES)})

    def _plan_file(self, plan_id):
        if not isinstance(plan_id, str) or not re.fullmatch(r"[0-9a-f]{32}", plan_id):
            raise MaintenanceError("Invalid package plan ID.")
        return _root(self.state / f"package-{plan_id}.json")

    def _save_plan(self, plan):
        self.workspace._atomic_write(self._plan_file(plan["plan_id"]), json.dumps(plan, indent=2).encode())

    @staticmethod
    def _wheel(item):
        info = item["download_info"]
        url = urlsplit(info["url"])
        digest = info.get("archive_info", {}).get("hashes", {}).get("sha256", "")
        if (url.scheme != "https" or url.hostname != "files.pythonhosted.org" or url.username or url.password
                or url.port is not None or url.query or not url.path.endswith(".whl") or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise MaintenanceError("Automatic package plans support SHA-256 verified PyPI wheels only; source builds and external package URLs are excluded.")
        name, version = _name(item["metadata"]["name"]), item["metadata"]["version"]
        _specs([f"{name}=={version}"])
        return {"name": name, "version": version, "url": urlunsplit((url.scheme, url.netloc, url.path, "", "")),
                "sha256": digest, "requires_dist": item["metadata"].get("requires_dist", [])}

    def _dependency_conflicts(self, packages, plan_id):
        data_path = _root(self.state / f"dependencies-{plan_id}.json")
        self.workspace._atomic_write(data_path, json.dumps(packages).encode())
        script = """import json, sys
from pip._vendor.packaging.requirements import Requirement, InvalidRequirement
from pip._vendor.packaging.utils import canonicalize_name
packages = json.load(open(sys.argv[1], encoding='utf-8'))
versions = {canonicalize_name(p['name']): p['version'] for p in packages}
problems = []
for package in packages:
    for value in package.get('requires_dist', []):
        try:
            requirement = Requirement(value)
            if requirement.marker and not requirement.marker.evaluate({'extra': ''}):
                continue
            installed = versions.get(canonicalize_name(requirement.name))
            if installed is None or installed not in requirement.specifier:
                problems.append({'package': package['name'], 'requirement': str(requirement), 'installed': installed})
        except (InvalidRequirement, ValueError) as exc:
            problems.append({'package': package['name'], 'invalid_metadata': str(exc)[:300]})
print(json.dumps(problems))
"""
        try:
            _, output = self._run([str(self.python), "-I", "-c", script, str(data_path)], self.state, max_bytes=MAX_REPORT)
            return json.loads(output)
        finally:
            data_path.unlink(missing_ok=True)

    def package_plan(self, packages):
        specs = _specs(packages)
        with self.workspace._write_guard():
            before = self._manifest()
            plan_id = uuid.uuid4().hex
            report_path = self.state / f"report-{plan_id}.json"
            _, log = self._pip(["install", "--dry-run", "--report", str(report_path), "--only-binary=:all:",
                               "--index-url", "https://pypi.org/simple", *specs], timeout=600)
            _reject_link(report_path)
            if report_path.stat().st_size > MAX_REPORT:
                raise MaintenanceError("Package resolver report exceeded its size limit.")
            report = json.loads(report_path.read_text(encoding="utf-8"))
            try:
                changes = [self._wheel(item) for item in report.get("install", [])]
            finally:
                report_path.unlink(missing_ok=True)
            if len(changes) > 200:
                raise MaintenanceError("Package plan exceeds 200 wheel changes; split the request.")
            installed = {item["name"]: item["version"] for item in before["packages"]}
            protected = []
            for change in changes:
                change["previous_version"] = installed.get(change["name"])
                if change["name"] in PROTECTED_PACKAGES or change["name"].startswith("nvidia-"):
                    protected.append(f"{change['name']}=={change['version']}")
            if len({item["name"] for item in changes}) != len(changes):
                raise MaintenanceError("Package resolver returned duplicate package names.")
            projected = {item["name"]: item for item in before["packages"]}
            projected.update({item["name"]: item for item in changes})
            baseline_conflicts = self._dependency_conflicts(before["packages"], plan_id)
            projected_conflicts = self._dependency_conflicts(list(projected.values()), plan_id)
            introduced_conflicts = [item for item in projected_conflicts if item not in baseline_conflicts]
            if self._fingerprint(before) != self._fingerprint(self._manifest()):
                raise DevelopmentConflict("Python environment changed during planning; create a fresh package plan.")
            check_code, check = self._pip(["check"], allow_failure=True)
            plan = {"plan_id": plan_id, "status": "planned", "created_at": time.time(), "specs": specs,
                    "before": before, "environment_fingerprint": self._fingerprint(before), "changes": changes,
                    "protected_changes": sorted(protected), "baseline_dependency_check_ok": check_code == 0,
                    "introduced_dependency_conflicts": introduced_conflicts,
                    "projected_dependency_conflicts": projected_conflicts,
                    "baseline_dependency_check": _redact(check)[:MAX_OUTPUT], "log": _redact(log)[-MAX_OUTPUT:]}
            self._save_plan(plan)
            return self._public_plan(plan)

    @staticmethod
    def _public_plan(plan):
        return _public({key: value for key, value in plan.items() if key != "before"})

    def package_apply(self, plan_id, approved_protected_changes=None):
        with self.workspace._write_guard():
            path = self._plan_file(plan_id)
            if not path.is_file() or path.stat().st_size > MAX_REPORT:
                raise MaintenanceError("Package plan is missing or too large.")
            plan = json.loads(path.read_text(encoding="utf-8"))
            if plan["status"] != "planned":
                raise DevelopmentConflict("This plan was already attempted; inspect the result and create a fresh plan if needed.")
            if time.time() - plan["created_at"] > 24 * 60 * 60:
                raise DevelopmentConflict("Package plans expire after 24 hours; create a fresh plan.")
            if self._fingerprint(self._manifest()) != plan["environment_fingerprint"]:
                raise DevelopmentConflict("Python environment changed since planning; create a fresh plan before installing.")
            approved = [] if approved_protected_changes in (None, []) else _specs(approved_protected_changes)
            if sorted(approved) != plan["protected_changes"]:
                raise MaintenanceError("This plan changes protected packages. Explicitly approve exactly these versions after review: "
                                       + ", ".join(plan["protected_changes"]))
            if plan.get("introduced_dependency_conflicts"):
                raise MaintenanceError("This plan introduces dependency conflicts. Revise the package versions and create a new plan before installing.")
            requirements = []
            for change in plan["changes"]:
                self._wheel({"metadata": change, "download_info": {"url": change["url"], "archive_info": {"hashes": {"sha256": change["sha256"]}}}})
                requirements.append(f"{change['name']} @ {change['url']} --hash=sha256:{change['sha256']}\n")
            lock_path = _root(self.state / f"package-{plan_id}.txt")
            self.workspace._atomic_write(lock_path, "".join(requirements).encode())
            plan.update(status="applying", started_at=time.time())
            self._save_plan(plan)
            try:
                if requirements:
                    code, log = self._pip(["install", "--no-deps", "--require-hashes", "--only-binary=:all:", "--no-index",
                                           "--requirement", str(lock_path)], timeout=1800, max_bytes=MAX_REPORT, allow_failure=True)
                else:
                    code, log = 0, "The environment already satisfies the requested specifications."
                plan.update(status="applied" if code == 0 else "failed", install_returncode=code,
                            log=_redact(log)[-MAX_OUTPUT:], finished_at=time.time())
                plan["after"] = self._manifest()
                check_code, check = self._pip(["check"], allow_failure=True)
                plan.update(dependency_check_ok=check_code == 0, dependency_check=_redact(check)[:MAX_OUTPUT],
                            restart_required=bool(requirements), rollback_available=False)
            except (MaintenanceError, OSError, ValueError) as exc:
                plan.update(status="failed", error=_redact(str(exc))[:8000], finished_at=time.time(), rollback_available=False)
                self._save_plan(plan)
                raise
            self._save_plan(plan)
            return self._public_plan(plan)
