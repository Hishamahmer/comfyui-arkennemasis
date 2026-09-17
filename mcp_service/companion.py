"""Windows BAT companion: share MCP only while its ComfyUI session is in use."""

import argparse
from collections import deque
import ctypes
import json
import msvcrt
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp_service.settings import load_settings
from mcp_service.tunnel import port_in_use, stop_child, write_connection_file


class OwnerProcess:
    """Keep a process handle so a reused PID cannot extend the connection."""

    def __init__(self, pid):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        self.kernel.OpenProcess.restype = ctypes.c_void_p
        self.kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        self.kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        self.handle = self.kernel.OpenProcess(0x00100000, False, pid)
        if not self.handle:
            raise OSError("The ComfyUI launcher has already closed or is unavailable.")

    def alive(self):
        return self.kernel.WaitForSingleObject(self.handle, 0) == 258

    def close(self):
        self.kernel.CloseHandle(self.handle)


def write_status(state_dir, state, message, owner_pid, **details):
    path = Path(state_dir) / "session-status.json"
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps({"state": state, "message": message, "updated_at": time.time(),
                                     "owner_pid": owner_pid, "watcher_pid": os.getpid(), **details}), encoding="utf-8")
    os.replace(temporary, path)


class ConnectionStatus:
    def __init__(self, state_dir, owner_pid):
        self.state_dir = Path(state_dir)
        self.owner_pid = owner_pid
        self.last_success_at = None
        self.layer_success_at = {}
        self.previous = None

    def __call__(self, state, message, layers=None):
        layers = layers or {}
        now = time.time()
        for name, value in layers.items():
            if value == "online":
                self.layer_success_at[name] = now
        if all(layers.get(name) == "online" for name in ("gateway", "tunnel", "backend")):
            self.last_success_at = now
        write_status(self.state_dir, state, message, self.owner_pid,
                     layers=layers, last_success_at=self.last_success_at, layer_success_at=self.layer_success_at)
        transition = (state, message, layers)
        if transition != self.previous:
            path = self.state_dir / "connection-events.jsonl"
            if path.exists() and path.stat().st_size > 256 * 1024:
                os.replace(path, path.with_suffix(".previous.jsonl"))
            with path.open("a", encoding="utf-8") as log:
                log.write(json.dumps({"time": now, "state": state, "message": message, "layers": layers}) + "\n")
            self.previous = transition


def listener_pid(host, port):
    """Read Windows' IPv4 listener owner; an HTTP timeout is not a process exit."""
    api = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    size = ctypes.c_ulong()
    result = api(None, ctypes.byref(size), False, socket.AF_INET, 3, 0)
    if result not in {0, 122}:
        raise OSError("Cannot inspect the ComfyUI listener process.")
    table = ctypes.create_string_buffer(size.value)
    if api(table, ctypes.byref(size), False, socket.AF_INET, 3, 0):
        raise OSError("Cannot inspect the ComfyUI listener process.")
    count = struct.unpack_from("<I", table.raw)[0]
    for index in range(count):
        state, address, local_port, _, _, pid = struct.unpack_from("<6I", table.raw, 4 + index * 24)
        bound = socket.inet_ntoa(struct.pack("<I", address))
        if state == 2 and socket.ntohs(local_port & 0xffff) == port and bound in {host, "0.0.0.0"}:
            return pid
    return None


class BackendProcess:
    def __init__(self, url):
        parsed = urlsplit(url)
        self.host = "127.0.0.1" if parsed.hostname == "localhost" else parsed.hostname
        self.port = parsed.port or 80
        self.process = None
        self.pid = None
        self.ever_seen = False

    def state(self, ready):
        if self.process is not None and not self.process.alive():
            self.close()
        if ready and self.process is None:
            try:
                pid = listener_pid(self.host, self.port)
                if pid:
                    self.process = OwnerProcess(pid)
                    self.pid = pid
                    self.ever_seen = True
            except OSError:
                pass
        if ready:
            return "online"
        if self.process is not None:
            return "unresponsive"
        return "exited" if self.ever_seen else "unknown"

    def close(self):
        if self.process is not None:
            self.process.close()
        self.process = None
        self.pid = None


class RecoveryBudget:
    def __init__(self, limit=5, window=300):
        self.limit = limit
        self.window = window
        self.attempts = deque()
        self.next_attempt = 0
        self.exhausted = False

    def due(self):
        return not self.exhausted and time.monotonic() >= self.next_attempt

    def attempt(self):
        now = time.monotonic()
        while self.attempts and now - self.attempts[0] >= self.window:
            self.attempts.popleft()
        if len(self.attempts) >= self.limit:
            self.exhausted = True
            return False
        self.attempts.append(now)
        self.next_attempt = now + min(30, 2 ** len(self.attempts))
        return True


def local_ready(opener, url, *, gateway=False):
    try:
        with opener.open(url, timeout=0.8) as response:
            data = json.load(response)
        if not isinstance(data, dict):
            return False
        return data.get("service") == "Arkennemasis MCP" if gateway else "system" in data and "devices" in data
    except (OSError, ValueError):
        return False


def tailscale_status(executable, *arguments):
    result = subprocess.run([str(executable), *arguments, "--json"], capture_output=True, text=True,
                            timeout=6, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError("Open Tailscale and check that it is connected, then relaunch ComfyUI.")
    try:
        status = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError("Tailscale returned an invalid status response.") from error
    if not isinstance(status, dict):
        raise RuntimeError("Tailscale returned an invalid status response.")
    return status


def mappings(status):
    yield status
    yield from (item for item in status.get("Foreground", {}).values() if isinstance(item, dict))


def port_443_used(status):
    return any("443" in item.get("TCP", {}) or any(name.endswith(":443") for name in item.get("Web", {}))
               for item in mappings(status))


def funnel_ready(status, hostname, target):
    key = hostname + ":443"
    return any(item.get("AllowFunnel", {}).get(key) is True
               and item.get("Web", {}).get(key, {}).get("Handlers", {}).get("/", {}).get("Proxy") == target
               for item in mappings(status))


def public_connection_ready(executable, hostname, target):
    status = tailscale_status(executable, "status")
    return (status.get("BackendState") == "Running"
            and status.get("Self", {}).get("DNSName", "").rstrip(".") == hostname
            and funnel_ready(tailscale_status(executable, "funnel", "status"), hostname, target))


def wait_until_ready(owner, ready, heartbeat, *, child=None, timeout=180):
    deadline = time.monotonic() + timeout
    while owner.alive():
        if child is not None and child.poll() is not None:
            raise RuntimeError("The MCP connection process stopped during startup. Check .local/companion.log.")
        if ready():
            return True
        if time.monotonic() >= deadline:
            raise RuntimeError("Connection startup timed out. Check ComfyUI and Tailscale, then relaunch the BAT.")
        heartbeat()
        time.sleep(0.5)
    return False


class RestartRequest:
    def __init__(self, settings, opener):
        self.state_dir = Path(settings.state_dir)
        self.settings = settings
        self.opener = opener
        self.current = None
        self.deadline = None
        self.old_pid = None
        self.saw_offline = False

    def result(self, request, state, message):
        result = {"id": request["id"], "action": "restart", "state": state,
                  "message": message, "updated_at": time.time()}
        directory = self.state_dir / "lifecycle-results"
        directory.mkdir(exist_ok=True)
        for path in (directory / (request["id"] + ".json"), self.state_dir / "lifecycle-result.json"):
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(result), encoding="utf-8")
            os.replace(temporary, path)

    def tick(self, backend_state, backend_pid):
        if self.current is not None:
            try:
                with (self.state_dir / "backend-restarts.json").open(encoding="utf-8") as handle:
                    records = json.loads(handle.read(64 * 1024))
                if isinstance(records, list):
                    for record in records:
                        if (isinstance(record, dict) and record.get("request_id") == self.current["id"]
                                and record.get("state") in {"cancelled", "failed"}):
                            message = ("The queue changed before restart; no process was stopped." if record["state"] == "cancelled"
                                       else "ComfyUI could not restart. MCP remains connected for diagnosis.")
                            self.result(self.current, "failed", message)
                            self.current = None
                            return
            except (OSError, ValueError):
                pass
            self.saw_offline |= backend_state != "online"
            if (backend_state == "online" and backend_pid is not None
                    and ((self.old_pid is not None and backend_pid != self.old_pid)
                         or (self.old_pid is None and self.saw_offline))):
                self.result(self.current, "ready", "ComfyUI restarted and is ready.")
                self.current = None
            elif time.monotonic() >= self.deadline:
                self.result(self.current, "failed", "ComfyUI did not finish restarting within 180 seconds. MCP remains connected for diagnosis.")
                self.current = None
            return
        path = self.state_dir / "lifecycle-request.json"
        try:
            if path.stat().st_size > 4096:
                return
            request = json.loads(path.read_text(encoding="utf-8"))
            valid = (isinstance(request, dict) and set(request) == {"id", "action", "created_at"}
                     and isinstance(request["id"], str) and str(uuid.UUID(request["id"])) == request["id"]
                     and request["action"] == "restart" and type(request["created_at"]) in {int, float})
        except (OSError, ValueError, AttributeError):
            return
        if not valid:
            return
        record = self.state_dir / "lifecycle-results" / (request["id"] + ".json")
        if record.exists():
            try:
                prior = json.loads(record.read_text(encoding="utf-8"))
                if prior.get("state") in {"accepted", "restarting"}:
                    self.result(request, "failed", "The connection watcher restarted before restart completion was confirmed. This request will not be sent again.")
            except (OSError, ValueError, AttributeError):
                pass
            return
        if not 0 <= time.time() - request["created_at"] <= 300:
            self.result(request, "failed", "Restart request expired. Submit a new request when ready.")
            return
        # Persist before sending: an uncertain reply must never cause a second restart.
        self.result(request, "accepted", "ComfyUI restart request accepted.")
        self.old_pid = backend_pid
        self.saw_offline = backend_state != "online"
        self.current = request
        self.deadline = time.monotonic() + 180
        call = urllib.request.Request(self.settings.comfy_url + "/arkennemasis/mcp/restart",
                                      data=json.dumps({"request_id": request["id"]}).encode(), method="POST",
                                      headers={"Content-Type": "application/json", "X-Ark-Bridge-Token": self.settings.bridge_token})
        try:
            with self.opener.open(call, timeout=3) as response:
                reply = json.load(response)
            if not isinstance(reply, dict) or reply.get("accepted") is not True:
                self.result(request, "failed", "ComfyUI did not accept the restart request.")
                self.current = None
                return
            if self.old_pid is None and type(reply.get("pid")) is int:
                self.old_pid = reply["pid"]
        except urllib.error.HTTPError as exc:
            message = "ComfyUI rejected the restart request. Check the local bridge."
            try:
                reply = json.loads(exc.read(4096))
                code = reply.get("error", {}).get("code") if isinstance(reply, dict) else None
                message = {
                    "queue_not_empty": "Finish or cancel queued and running jobs before restarting ComfyUI.",
                    "unsupported_launcher": "This ComfyUI installation must be restarted through its external launcher.",
                    "restart_in_progress": "A ComfyUI restart is already in progress.",
                }.get(code, message)
            except (OSError, ValueError, AttributeError):
                pass
            self.result(request, "failed", message)
            self.current = None
            return
        except (OSError, ValueError):
            # The server may have restarted before its response arrived. Observe; do not resend.
            pass
        self.result(request, "restarting", "Waiting for ComfyUI to restart; the MCP connection remains available.")


class OwnedConnection:
    def __init__(self, settings, launcher, opener):
        self.settings = settings
        self.launcher = launcher
        self.opener = opener
        self.hostname = urlsplit(settings.public_url).hostname
        self.target = f"http://{settings.host}:{settings.port}"
        self.gateway = None
        self.funnel = None
        self.closing_funnel = False

    def alive(self, layer):
        child = self.gateway if layer == "gateway" else self.funnel
        return child is not None and child.poll() is None

    def ready(self, layer):
        if not self.alive(layer):
            return False
        if layer == "gateway":
            return local_ready(self.opener, self.target + "/health", gateway=True)
        return public_connection_ready(self.launcher["tailscale"], self.hostname, self.target)

    def start(self, layer, owner, update):
        if not owner.alive():
            return False
        if layer == "gateway":
            stop_child(self.gateway)
            self.gateway = None
            if port_in_use(self.settings.host, self.settings.port):
                raise ValueError("Another MCP gateway owns the local port. Its process was left unchanged.")
            launch = Path(__file__).with_name("launch.py")
            self.gateway = subprocess.Popen([self.launcher["pythonw"], str(launch), "serve"],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                           creationflags=subprocess.CREATE_NO_WINDOW, cwd=launch.parent.parent)
            child = self.gateway
        else:
            self.closing_funnel |= self.funnel is not None
            stop_child(self.funnel)
            self.funnel = None
            executable = self.launcher["tailscale"]
            status = tailscale_status(executable, "status")
            hostname = status.get("Self", {}).get("DNSName", "").rstrip(".")
            if status.get("BackendState") != "Running":
                raise RuntimeError("Waiting for Tailscale to reconnect.")
            if hostname != self.hostname:
                raise ValueError("Tailscale's hostname changed. Review the saved connection configuration.")
            mapping = tailscale_status(executable, "funnel", "status")
            if port_443_used(mapping):
                if self.closing_funnel and funnel_ready(mapping, self.hostname, self.target):
                    raise RuntimeError("Waiting for the previous owned public route to close.")
                raise ValueError("Tailscale HTTPS port 443 is already shared. Its existing connection was left unchanged.")
            self.closing_funnel = False
            # No --bg: this process owns the route and closing it removes only this route.
            self.funnel = subprocess.Popen([executable, "funnel", "--https=443", self.target],
                                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                          creationflags=subprocess.CREATE_NO_WINDOW)
            child = self.funnel
        def ready():
            try:
                return self.ready(layer)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                return False
        return wait_until_ready(owner, ready, update, child=child, timeout=30)

    def close(self):
        try:
            stop_child(self.funnel)
        finally:
            stop_child(self.gateway)


def keep_connected(owner, comfy_ready, connection, backend, update, restart, *, recovery_grace=45):
    budgets = {name: RecoveryBudget() for name in ("gateway", "tunnel")}
    missed_since = {name: None for name in budgets}
    checked = {name: 0 for name in budgets}
    ready = {name: False for name in budgets}
    while owner.alive():
        backend_state = backend.state(comfy_ready())
        if not owner.alive():
            break
        restart.tick(backend_state, backend.pid)
        for layer in budgets:
            if not owner.alive():
                return
            now = time.monotonic()
            alive = connection.alive(layer)
            if not alive or now >= checked[layer]:
                try:
                    ready[layer] = connection.ready(layer)
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                    ready[layer] = False
                checked[layer] = time.monotonic() + 10
            if ready[layer]:
                missed_since[layer] = None
                if budgets[layer].exhausted:
                    budgets[layer] = RecoveryBudget()
                continue
            missed_since[layer] = now if missed_since[layer] is None else missed_since[layer]
            if ((not alive or now - missed_since[layer] >= recovery_grace) and budgets[layer].due()
                    and (layer == "gateway" or ready["gateway"])):
                if not budgets[layer].attempt():
                    continue
                def heartbeat():
                    layers = {name: "online" if ready[name] else "offline" for name in budgets}
                    layers.update({layer: "starting", "backend": backend_state})
                    update("starting", "Recovering " + ("local MCP gateway" if layer == "gateway" else "public connection"), layers)
                heartbeat()
                try:
                    ready[layer] = connection.start(layer, owner, heartbeat)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    ready[layer] = False
                checked[layer] = 0
        layers = {name: "online" if ready[name] else "offline" for name in budgets}
        layers["backend"] = backend_state
        paused = [name for name, budget in budgets.items() if budget.exhausted and not ready[name]]
        if paused:
            layers.update({name: "error" for name in paused})
            update("error", "Automatic " + ", ".join(paused) + " recovery paused after repeated failures. Check connection diagnostics and relaunch the BAT.", layers)
        elif ready["gateway"] and ready["tunnel"]:
            if restart.current is not None:
                layers["backend"] = "restarting"
                update("waiting", "MCP connected; ComfyUI is restarting", layers)
            elif backend_state == "online":
                update("online", "AI connection online", layers)
            else:
                message = "ComfyUI stopped" if backend_state == "exited" else "ComfyUI is starting or not responding"
                update("waiting", "MCP connected; " + message + ". Connection stays available for diagnosis.", layers)
        else:
            update("waiting", "Reconnecting " + ("local MCP gateway" if not ready["gateway"] else "public connection"), layers)
        time.sleep(2)


def run_session(settings, owner, owner_pid, launcher):
    state_dir = Path(settings.state_dir)
    update = ConnectionStatus(state_dir, owner_pid)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    comfy_ready = lambda: local_ready(opener, settings.comfy_url + "/system_stats")
    connection = OwnedConnection(settings, launcher, opener)
    backend = BackendProcess(settings.comfy_url)
    restart = RestartRequest(settings, opener)
    failed = False
    try:
        if settings.auth_mode != "connection_link" or not settings.public_url:
            raise ValueError("Configure a fixed private MCP URL before using the BAT companion.")
        write_connection_file(state_dir / "connection.txt", settings.public_url, settings.endpoint)
        keep_connected(owner, comfy_ready, connection, backend, update, restart)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        failed = True
        update("error", str(exc))
        print(str(exc), flush=True)
    finally:
        connection.close()
        backend.close()
        if not failed:
            update("stopped", "AI connection off")


def watch(owner_pid):
    settings = load_settings()
    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "companion.lock").open("a+b") as lock:
        lock.seek(0)
        if not lock.read(1):
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return 0
        owner = None
        try:
            owner = OwnerProcess(owner_pid)
            launcher = json.loads((state_dir / "launcher.json").read_text(encoding="utf-8"))
            run_session(settings, owner, owner_pid, launcher)
        except (OSError, ValueError, KeyError) as exc:
            write_status(state_dir, "error", str(exc), owner_pid)
        finally:
            if owner is not None:
                owner.close()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "watch"])
    parser.add_argument("--owner-pid", type=int)
    args = parser.parse_args()
    if args.command == "watch":
        if not args.owner_pid:
            parser.error("watch requires --owner-pid")
        return watch(args.owner_pid)
    try:
        settings = load_settings()
        state_dir = Path(settings.state_dir)
        launcher = json.loads((state_dir / "launcher.json").read_text(encoding="utf-8"))
        with (state_dir / "companion.log").open("a", encoding="utf-8") as log:
            subprocess.Popen([launcher["pythonw"], str(Path(__file__).resolve()), "watch",
                              "--owner-pid", str(args.owner_pid or os.getppid())],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                             creationflags=subprocess.CREATE_NO_WINDOW, cwd=Path(__file__).resolve().parents[1])
        print("[Arkennemasis MCP] Connection will start with ComfyUI. Check the connection badge at bottom left.")
    except (OSError, ValueError, KeyError) as exc:
        print(f"[Arkennemasis MCP] Could not start the connection: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
