"""Owner-initiated setup; importing this module never installs or starts anything."""

import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time

from .settings import COMFY_DIR, DEFAULT_CONFIG, SCOPES, SERVICE_DIR, Settings, load_settings, save_settings

LAUNCHERS = ("run_cpu.bat", "run_nvidia_gpu.bat", "run_nvidia_gpu_fast_fp16_accumulation.bat")
PYTHON_NAME = re.compile(r"python(?:3(?:\.\d+)?)?(?:\.exe)?$", re.IGNORECASE)
PACK_NAME = re.compile(r"[A-Za-z0-9_-]{1,120}$")


def run(arguments, **kwargs):
    return subprocess.run(arguments, shell=False, stdin=subprocess.DEVNULL, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **kwargs)


def inspect_python(value):
    executable = Path(value).expanduser()
    if not executable.is_absolute() or not executable.is_file() or not PYTHON_NAME.fullmatch(executable.name):
        raise ValueError("Choose an existing Python executable using its full path.")
    probe = run([str(executable), "-I", "-c",
                 "import json,sys; print(json.dumps({'version':list(sys.version_info[:3]),'implementation':sys.implementation.name}))"], timeout=15)
    if probe.returncode:
        raise ValueError("The selected Python could not start. Choose a working Python installation.")
    try:
        details = json.loads(probe.stdout)
        version = details["version"]
        if details["implementation"] != "cpython" or len(version) != 3 or any(type(part) is not int for part in version):
            raise ValueError
        if tuple(version[:2]) < (3, 11):
            raise ValueError
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("MCP setup requires CPython 3.11 or newer.") from exc
    return {"executable": str(executable), "version": version, "runtime_name": f"py{version[0]}{version[1]}"}


def find_tailscale():
    executable = shutil.which("tailscale")
    if executable:
        return executable
    if os.name == "nt" and os.environ.get("ProgramFiles"):
        candidate = Path(os.environ["ProgramFiles"]) / "Tailscale" / "tailscale.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def tailscale_info():
    executable = find_tailscale()
    if not executable:
        return {"installed": False, "running": False, "public_url": "", "message": "Install and sign in to Tailscale to use the automatic fixed connection."}
    try:
        result = run([executable, "status", "--json"], timeout=8)
        status = json.loads(result.stdout) if result.returncode == 0 else {}
        hostname = status.get("Self", {}).get("DNSName", "").rstrip(".")
        valid = bool(re.fullmatch(r"[a-z0-9.-]+\.ts\.net", hostname))
        ready = status.get("BackendState") == "Running"
        return {"installed": True, "running": ready, "public_url": f"https://{hostname}" if valid else "",
                "message": "Tailscale is connected. Funnel permission may still need one-time approval in Tailscale." if ready else "Open Tailscale and sign in, then check again."}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError, AttributeError):
        return {"installed": True, "running": False, "public_url": "", "message": "Tailscale status is unavailable. Open Tailscale and check its connection."}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".setup-", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Installation:
    def __init__(self, config_path=DEFAULT_CONFIG, comfy_root=COMFY_DIR, python=sys.executable, service_dir=SERVICE_DIR):
        self.config_path = Path(config_path)
        self.comfy_root = Path(comfy_root).resolve()
        self.python = str(python)
        self.service_dir = Path(service_dir).resolve()

    def settings(self):
        if self.config_path.is_file():
            return load_settings(self.config_path, transport="stdio")
        return Settings(comfy_root=str(self.comfy_root), comfy_python=self.python,
                        state_dir=str(self.config_path.parent),
                        workflow_root=str(self.comfy_root / "user" / "default" / "workflows"))

    def node_packs(self):
        root = self.comfy_root / "custom_nodes"
        if not root.is_dir():
            return []
        return sorted(entry.name for entry in root.iterdir()
                      if entry.is_dir() and not entry.is_symlink() and PACK_NAME.fullmatch(entry.name)
                      and not getattr(entry, "is_junction", lambda: False)())

    def launcher(self):
        path = Path(self.settings().state_dir) / "launcher.json"
        if not path.is_file():
            return {}
        if path.stat().st_size > 64 * 1024:
            raise ValueError("Launcher settings are too large.")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Launcher settings must be a JSON object.")
        return value

    def gateway_python(self):
        launcher = self.launcher()
        value = launcher.get("python", launcher.get("pythonw", self.python))
        if not isinstance(value, str):
            raise ValueError("Choose a valid MCP Python executable in setup.")
        path = Path(value)
        if path.name.lower() == "pythonw.exe":
            path = path.with_name("python.exe")
        return inspect_python(str(path))

    def save_gateway_python(self, python):
        settings = self.settings()
        launcher = self.launcher()
        pythonw = Path(python["executable"]).with_name("pythonw.exe")
        launcher.update(python=python["executable"], pythonw=str(pythonw) if os.name == "nt" and pythonw.is_file() else python["executable"])
        tailscale = find_tailscale()
        if tailscale:
            launcher["tailscale"] = tailscale
        atomic_json(Path(settings.state_dir) / "launcher.json", launcher)

    def status(self):
        settings = self.settings()
        python = self.gateway_python()
        runtime = self.service_dir / ".runtime" / python["runtime_name"]
        # The marker is written only after an import check in the selected interpreter.
        marker = runtime / "ark-installation.json"
        ready = False
        if marker.is_file():
            try:
                installed = json.loads(marker.read_text(encoding="utf-8"))
                ready = installed.get("version", [])[:2] == python["version"][:2]
            except (OSError, ValueError, TypeError):
                pass
        portable = os.name == "nt" and (self.comfy_root.parent / "python_embeded" / "python.exe").is_file()
        return {"configured": self.config_path.is_file(), "comfy_root": str(self.comfy_root), "python": python,
                "comfy_python": self.python,
                "dependencies": {"ready": ready, "runtime": str(runtime)},
                "public_url": settings.public_url, "auth_mode": settings.auth_mode,
                "enabled_scopes": settings.enabled_scopes, "allowed_node_packs": settings.allowed_node_packs,
                "node_packs": self.node_packs(), "tailscale": tailscale_info(),
                "automatic_lifecycle": portable,
                "lifecycle_message": "The standard portable BAT launchers can start and stop the connection with ComfyUI." if portable else
                "Automatic lifecycle currently supports Windows portable BAT launchers. This installation can use the manual gateway CLI and its own tunnel.",
                "launcher_ready": (Path(settings.state_dir) / "launcher.json").is_file(),
                "pending_package_plans": self.pending_package_plans()}

    def package_plan(self, plan_id):
        if not isinstance(plan_id, str) or not re.fullmatch(r"[a-f0-9]{32}", plan_id):
            raise ValueError("Select a valid package plan.")
        root = Path(self.settings().state_dir) / "maintenance"
        path = root / f"package-{plan_id}.json"
        if root.is_symlink() or getattr(root, "is_junction", lambda: False)() or path.is_symlink():
            raise ValueError("Package plans must be regular files in the local maintenance folder.")
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("Package plan is missing or too large.")
        plan = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(plan, dict) or plan.get("plan_id") != plan_id or plan.get("status") != "planned":
            raise ValueError("This package plan is no longer pending.")
        created_at = plan.get("created_at")
        if type(created_at) not in {int, float} or not math.isfinite(created_at) or not 0 <= time.time() - created_at <= 86400:
            raise ValueError("Package plan expired. Ask the AI to create a fresh plan.")
        protected = plan.get("protected_changes")
        if not isinstance(protected, list) or not protected or len(protected) > 100 or any(
                not isinstance(spec, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+==[A-Za-z0-9_.+!-]{1,100}", spec) for spec in protected):
            raise ValueError("This plan does not contain valid protected package changes.")
        if plan.get("introduced_dependency_conflicts"):
            raise ValueError("This package plan introduces dependency conflicts. Ask the AI to revise it.")
        return {"plan_id": plan_id, "protected_changes": protected, "created_at": created_at}

    def pending_package_plans(self):
        root = Path(self.settings().state_dir) / "maintenance"
        if not root.is_dir() or root.is_symlink() or getattr(root, "is_junction", lambda: False)():
            return []
        pending = []
        # Review only a bounded set of recent files; never return private manifests or pip logs.
        candidates = sorted(root.glob("package-*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:50]
        for path in candidates:
            plan_id = path.stem.removeprefix("package-")
            if (root / f"approved-{plan_id}.json").exists():
                continue
            try:
                pending.append(self.package_plan(plan_id))
            except (OSError, ValueError, TypeError):
                continue
        return pending

    def approve_package(self, plan_id):
        plan = self.package_plan(plan_id)
        root = Path(self.settings().state_dir) / "maintenance"
        atomic_json(root / f"approved-{plan_id}.json", {"plan_id": plan_id, "protected_changes": plan["protected_changes"]})
        return {"approved": True, "message": "These exact package versions are approved for this plan. The AI can now apply it."}

    def save(self, values, *, comfy_url=None):
        unknown = set(values) - {"public_url", "enabled_scopes", "allowed_node_packs", "python"}
        if unknown:
            raise ValueError("Unsupported setup field.")
        settings = self.settings()
        if comfy_url and not self.config_path.exists():
            settings.comfy_url = comfy_url
        python = inspect_python(values["python"]) if "python" in values else self.gateway_python()
        scopes = values.get("enabled_scopes", settings.enabled_scopes)
        packs = values.get("allowed_node_packs", settings.allowed_node_packs)
        if not isinstance(scopes, list) or any(not isinstance(scope, str) or scope not in SCOPES for scope in scopes):
            raise ValueError("Select supported access permissions.")
        if not isinstance(packs, list) or any(not isinstance(pack, str) or not PACK_NAME.fullmatch(pack) for pack in packs):
            raise ValueError("Select explicit custom-node folder names.")
        if "comfy:develop" in scopes and not packs:
            raise ValueError("Choose at least one node pack before enabling development access.")
        public_url = values.get("public_url", settings.public_url)
        if not isinstance(public_url, str):
            raise ValueError("The public URL must be an HTTPS origin.")
        settings.public_url = public_url.strip().rstrip("/")
        # Existing OAuth is never silently downgraded when owner settings are edited.
        if settings.auth_mode != "oauth":
            settings.auth_mode = "connection_link" if settings.public_url else "local_token"
        settings.local_token = settings.local_token or secrets.token_urlsafe(36)
        settings.bridge_token = settings.bridge_token or secrets.token_urlsafe(36)
        settings.connection_token = settings.connection_token or secrets.token_urlsafe(48)
        settings.enabled_scopes = list(dict.fromkeys(scopes))
        settings.allowed_node_packs = list(dict.fromkeys(packs))
        settings.comfy_python = self.python
        settings.validate()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        save_settings(settings, self.config_path)
        self.save_gateway_python(python)
        return {"saved": True, "restart_required": True,
                "message": "Settings saved. Restart the MCP connection to apply changes; the saved private URL is preserved."}

    def connection_url(self):
        settings = load_settings(self.config_path)
        if not settings.public_url:
            raise ValueError("Save a fixed public URL before connecting a web AI client.")
        return {"url": settings.endpoint, "authentication": "OAuth" if settings.auth_mode == "oauth" else "No Auth"}

    def install_dependencies(self):
        settings = load_settings(self.config_path, transport="stdio")
        python = self.gateway_python()
        runtime_root = self.service_dir / ".runtime"
        runtime_root.mkdir(parents=True, exist_ok=True)
        runtime = runtime_root / python["runtime_name"]
        if runtime.exists():
            raise ValueError("This Python runtime already exists. Use a fresh runtime or the documented maintenance procedure to repair it.")
        staging = Path(tempfile.mkdtemp(prefix=f".install-{python['runtime_name']}-", dir=runtime_root)).resolve()
        try:
            installed = run([python["executable"], "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                             "--index-url", "https://pypi.org/simple", "--only-binary=:all:", "--target", str(staging),
                             "-r", str(self.service_dir / "requirements.txt")], timeout=600)
            if installed.returncode:
                # pip output can contain credentials from machine configuration; keep it local and bounded.
                raise ValueError("Optional dependency installation failed. Check that Python has pip and can reach PyPI; ComfyUI packages were not changed.")
            checked = run([python["executable"], "-I", "-c",
                           "import site,sys; site.addsitedir(sys.argv[1]); import mcp,httpx,jwt,uvicorn; from mcp.server import MCPServer",
                           str(staging)], timeout=30)
            if checked.returncode:
                raise ValueError("Installed dependencies failed their import check. ComfyUI packages were not changed.")
            atomic_json(staging / "ark-installation.json", python)
            os.replace(staging, runtime)
            self.save_gateway_python(python)
            return {"installed": True, "message": "Optional MCP dependencies are ready. ComfyUI packages were not changed."}
        finally:
            if staging.exists() and staging.parent == runtime_root.resolve() and staging.name.startswith(".install-"):
                shutil.rmtree(staging)

    def install_launchers(self):
        settings = load_settings(self.config_path, transport="stdio")
        portable = self.comfy_root.parent
        embedded = portable / "python_embeded" / "python.exe"
        if os.name != "nt" or not embedded.is_file() or self.comfy_root.name != "ComfyUI":
            raise ValueError("Automatic BAT integration requires the Windows portable ComfyUI layout. Use the manual gateway instructions for this installation.")
        relative = self.service_dir.relative_to(portable)
        if any(character in str(relative) for character in '%!&|<>^\r\n"'):
            raise ValueError("This installation path cannot be safely represented in a BAT launcher.")
        hook = f'if exist "{relative}\\companion.py" .\\python_embeded\\python.exe -s "{relative}\\companion.py" start'
        changed = []
        for name in LAUNCHERS:
            path = portable / name
            if not path.is_file() or path.is_symlink() or path.resolve().parent != portable.resolve():
                continue
            original = path.read_bytes()
            text = original.decode("utf-8-sig")
            has_hook = "mcp_service\\companion.py" in text or "mcp_service/companion.py" in text
            wrapper = f'"{relative}\\launch_backend.py"'
            expression = re.compile(r'^(\s*(?:\.\\)?python_embeded\\python\.exe\s+-s\s+)ComfyUI\\main\.py([^\r\n]*)', re.MULTILINE | re.IGNORECASE)
            matches = list(expression.finditer(text))
            if any(any(character in match.group(2) for character in '&|<>^%!') for match in matches):
                raise ValueError(f"{name} contains an unsupported launch command; it was not changed.")
            if not matches and wrapper not in text:
                raise ValueError(f"{name} is not a recognized portable launcher; it was not changed.")
            updated = expression.sub(lambda match: match.group(1) + wrapper + match.group(2), text)
            if not has_hook:
                updated = '@echo off\r\ncd /d "%~dp0"\r\n' + hook + "\r\n" + updated
            if updated == text:
                continue
            backup = Path(settings.state_dir) / "launcher-backups" / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                with backup.open("xb") as handle:
                    handle.write(original)
            descriptor, temporary = tempfile.mkstemp(dir=portable, prefix=".ark-launcher-", suffix=".tmp")
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(updated.encode("utf-8"))
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            changed.append(name)
        return {"changed": changed, "message": "Portable launchers are ready. Use your usual BAT to start the connection with ComfyUI."}

    def start(self):
        if os.name != "nt":
            raise ValueError("Use the manual gateway instructions on this platform.")
        settings = load_settings(self.config_path)
        if not settings.public_url or settings.auth_mode != "connection_link":
            raise ValueError("Save a fixed private connection URL first.")
        if not (Path(settings.state_dir) / "launcher.json").is_file():
            raise ValueError("Install the optional MCP dependencies first.")
        parent = os.getppid()
        # Only a verified standard BAT parent may own the companion; an arbitrary caller PID cannot be supplied.
        query = f"Get-CimInstance Win32_Process -Filter 'ProcessId = {parent}' | Select-Object Name,CommandLine | ConvertTo-Json -Compress"
        result = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", query], timeout=10)
        try:
            owner = json.loads(result.stdout)
            command = owner["CommandLine"].casefold()
            expected = [str(self.comfy_root.parent / name).casefold() for name in LAUNCHERS]
            if owner["Name"].casefold() != "cmd.exe" or not any(f'"{path}"' in command for path in expected):
                raise ValueError
        except (TypeError, KeyError, ValueError) as exc:
            raise ValueError("Start ComfyUI using its usual portable BAT to enable automatic connection ownership.") from exc
        with (Path(settings.state_dir) / "companion.log").open("a", encoding="utf-8") as log:
            subprocess.Popen([self.python, str(self.service_dir / "companion.py"), "start", "--owner-pid", str(parent)],
                                       shell=False, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=subprocess.CREATE_NO_WINDOW, cwd=self.service_dir.parent)
        return {"started": True, "message": "Connection startup requested. Check the connection badge for readiness."}
