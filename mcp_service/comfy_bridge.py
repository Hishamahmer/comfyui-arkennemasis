"""Local control routes and the opt-in bridge to an open ComfyUI browser tab."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import platform
import secrets
import subprocess
import sys
import time
from urllib.parse import urlsplit
import uuid

from aiohttp import web


PREFIX = "/arkennemasis/mcp"
CONFIG_PATH = Path(__file__).parent / ".local" / "config.json"
SESSION_TTL = 300
MAX_BODY = 16 * 1024 * 1024
RUNTIME_FLAGS = {
    "--cpu", "--highvram", "--normalvram", "--lowvram", "--novram", "--gpu-only",
    "--force-fp32", "--force-fp16", "--fp16-unet", "--bf16-unet", "--fp32-unet",
    "--fp8_e4m3fn-unet", "--fp8_e5m2-unet", "--fp16-vae", "--fp32-vae", "--bf16-vae", "--cpu-vae",
    "--use-split-cross-attention", "--use-quad-cross-attention", "--use-pytorch-cross-attention",
    "--use-sage-attention", "--use-flash-attention", "--disable-xformers",
    "--cache-classic", "--cache-none", "--disable-smart-memory", "--windows-standalone-build",
}
RUNTIME_OPTIONS = {"--port", "--preview-method", "--preview-size", "--cache-lru", "--reserve-vram"}


def bridge_token():
    token = os.environ.get("ARK_MCP_BRIDGE_TOKEN")
    if token:
        return token
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return ""
    token = config.get("bridge_token", "") if isinstance(config, dict) else ""
    return token if isinstance(token, str) else ""


def failure(status, code, message):
    return web.json_response({"error": {"code": code, "message": message}}, status=status)


def launch_settings(argv):
    settings = {}
    for index, value in enumerate(argv):
        key, separator, inline = value.partition("=")
        if key in RUNTIME_FLAGS and not separator:
            settings[key[2:]] = True
        elif key in RUNTIME_OPTIONS:
            candidate = inline if separator else argv[index + 1] if index + 1 < len(argv) else ""
            if len(candidate) <= 32 and all(character.isalnum() or character in "._-" for character in candidate):
                settings[key[2:]] = candidate
        elif key == "--fast":
            allowed = {"fp16_accumulation", "fp8_matrix_mult", "cublas_ops", "autotune", "matmul"}
            selected = [inline] if separator else argv[index + 1:]
            settings["fast"] = [entry for entry in selected if entry in allowed]
    return settings


def is_loopback(request):
    try:
        address = ipaddress.ip_address(request.remote)
        if address.version == 6 and address.ipv4_mapped:
            address = address.ipv4_mapped
        return address.is_loopback
    except (TypeError, ValueError):
        return False


def browser_origin_allowed(request):
    if not is_loopback(request):
        return False
    try:
        target = urlsplit(f"{request.scheme}://{request.host}")
        origin = urlsplit(request.headers.get("Origin", ""))
        local_host = target.hostname == "localhost"
        if not local_host:
            local_host = ipaddress.ip_address(target.hostname).is_loopback
        return local_host and (origin.scheme, origin.netloc) == (target.scheme, target.netloc)
    except (TypeError, ValueError):
        return False


async def json_body(request):
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Use application/json.")
    data = bytearray()
    async for chunk in request.content.iter_chunked(64 * 1024):
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=len(data))
    try:
        result = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise web.HTTPBadRequest(text="Invalid JSON.") from exc
    if not isinstance(result, dict):
        raise web.HTTPBadRequest(text="Expected a JSON object.")
    return result


class CanvasBridge:
    def __init__(self, prompt_server, validate_prompt, token_provider=bridge_token, timeout=20):
        self.server = prompt_server
        self.validate_prompt = validate_prompt
        self.token_provider = token_provider
        self.timeout = timeout
        self.sessions = {}
        self.requests = {}
        self.restart_task = None

    def authorize_control(self, request):
        token = self.token_provider()
        if not token:
            return failure(503, "bridge_disabled", "Configure the local MCP bridge token first.")
        supplied = request.headers.get("X-Ark-Bridge-Token", "")
        if not is_loopback(request) or not hmac.compare_digest(supplied, token):
            return failure(403, "forbidden", "A local authenticated MCP service is required.")
        return None

    def authorize_browser(self, request):
        if not self.token_provider():
            return failure(503, "bridge_disabled", "Configure the local MCP bridge token first.")
        if not browser_origin_allowed(request) or request.headers.get("X-Ark-Canvas") != "1":
            return failure(403, "forbidden", "Use the local ComfyUI page to share its canvas.")
        return None

    def prune(self):
        now = time.time()
        for session_id, session in list(self.sessions.items()):
            # Background browsers throttle timers. An open ComfyUI socket is stronger
            # evidence of liveness than the page's last HTTP heartbeat.
            if session["client_id"] in self.server.sockets:
                session["disconnected_at"] = None
            elif session["disconnected_at"] is None:
                session["disconnected_at"] = now
            elif now - session["disconnected_at"] > SESSION_TTL:
                self.remove_session(session_id)
        for key, item in list(self.requests.items()):
            if item["expires"] < now:
                if not item["future"].done():
                    item["future"].set_result((504, self.timeout_result()))
                del self.requests[key]

    @staticmethod
    def timeout_result():
        return {"error": {"code": "canvas_timeout", "message": "Browser acknowledgment timed out. The edit may have applied; read the canvas before retrying. If the browser was suspended, bring its shared tab to the foreground."}}

    def remove_session(self, session_id):
        self.sessions.pop(session_id, None)
        for key, item in list(self.requests.items()):
            if key[0] == session_id:
                if not item["future"].done():
                    item["future"].set_result((409, {"error": {"code": "session_closed", "message": "Canvas sharing disconnected; read the canvas before retrying an edit."}}))
                del self.requests[key]

    def session_for_browser(self, body):
        session_id = body.get("session_id")
        session = self.sessions.get(session_id) if isinstance(session_id, str) else None
        secret = body.get("session_secret", "")
        if session and isinstance(secret, str) and hmac.compare_digest(session["secret"], secret):
            return session
        return None

    async def connection_status(self, request):
        denied = self.authorize_browser(request)
        if denied is not None:
            return denied
        await json_body(request)
        offline = {"state": "stopped", "message": "Start ComfyUI with its BAT file to enable the connection.", "updated_at": None}
        try:
            with (CONFIG_PATH.parent / "session-status.json").open(encoding="utf-8") as handle:
                record = json.loads(handle.read(4096))
        except (OSError, ValueError):
            return web.json_response(offline)
        if not isinstance(record, dict) or record.get("state") not in ("waiting", "starting", "online", "reconnecting", "restarting", "degraded", "stopped", "error"):
            return web.json_response(offline)
        updated_at = record.get("updated_at")
        recent = type(updated_at) in (int, float) and 0 <= time.time() - updated_at <= 15
        if record["state"] in ("waiting", "starting", "online", "reconnecting", "restarting", "degraded") and not recent:
            return web.json_response({**offline, "message": "The connection has stopped. Relaunch ComfyUI with its BAT file."})
        message = record.get("message")
        response = {"state": record["state"], "message": message[:300] if isinstance(message, str) else "",
                    "updated_at": updated_at if recent else None}
        layers = record.get("layers")
        if isinstance(layers, dict):
            response["layers"] = {key: value for key, value in layers.items() if key in ("gateway", "tunnel", "backend")
                                  and value in ("online", "offline", "starting", "reconnecting", "restarting", "unresponsive", "exited", "error", "unknown")}
        last_success = record.get("last_success_at")
        if type(last_success) in (int, float) and 0 <= last_success <= time.time():
            response["last_success_at"] = last_success
        layer_success = record.get("layer_success_at")
        if isinstance(layer_success, dict):
            response["layer_success_at"] = {key: value for key, value in layer_success.items()
                                            if key in ("gateway", "tunnel", "backend") and type(value) in (int, float)
                                            and 0 <= value <= time.time()}
        return web.json_response(response)

    def restart_records(self):
        try:
            with (CONFIG_PATH.parent / "backend-restarts.json").open(encoding="utf-8") as handle:
                records = json.loads(handle.read(64 * 1024))
        except FileNotFoundError:
            return []
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ValueError("Invalid restart state. Inspect the local MCP state before restarting.")
        return records

    def save_restart(self, record):
        records = [previous for previous in self.restart_records() if previous["request_id"] != record["request_id"]]
        records.append(record)
        path = CONFIG_PATH.parent / "backend-restarts.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(records[-32:]), encoding="utf-8")
        temporary.replace(path)

    async def runtime(self, request):
        denied = self.authorize_control(request)
        if denied is not None:
            return denied
        records = self.restart_records()
        return web.json_response({"python_executable": sys.executable, "python_version": platform.python_version(),
                                  "platform": sys.platform, "pid": os.getpid(), "launch_settings": launch_settings(sys.argv[1:]),
                                  "last_restart": records[-1] if records else None})

    async def progress(self, request):
        denied = self.authorize_control(request)
        if denied is not None:
            return denied
        observer = getattr(self.server, "_arkennemasis_execution_events", None)
        return web.json_response(observer.snapshot() if observer else {"available": False, "events": []})

    async def preview(self, request):
        denied = self.authorize_control(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        if set(body) != {"prompt_id"}:
            return failure(400, "invalid_preview", "Supply only the job prompt_id.")
        observer = getattr(self.server, "_arkennemasis_execution_events", None)
        try:
            result = observer.preview(body["prompt_id"]) if observer else None
        except ValueError:
            return failure(400, "invalid_preview", "Supply the job prompt_id.")
        return web.json_response(result or {"available": False})

    async def restart(self, request):
        denied = self.authorize_control(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        request_id = body.get("request_id")
        try:
            valid = isinstance(request_id, str) and str(uuid.UUID(request_id)) == request_id
        except ValueError:
            valid = False
        if not valid or set(body) != {"request_id"}:
            return failure(400, "invalid_restart", "Supply only a canonical UUID request_id; launch arguments and process IDs cannot be supplied.")
        for previous in self.restart_records():
            if previous["request_id"] == request_id:
                return web.json_response(previous)
        if self.restart_task is not None and not self.restart_task.done():
            return failure(409, "restart_in_progress", "A ComfyUI restart is already scheduled.")
        if getattr(sys, "frozen", False) or "__COMFY_CLI_SESSION__" in os.environ:
            return failure(409, "unsupported_launcher", "This ComfyUI process is managed by an external launcher. Restart it through that launcher.")
        running, queued = self.server.prompt_queue.get_current_queue_volatile()
        if running or queued:
            return failure(409, "queue_not_empty", "Finish or cancel running and pending jobs before restarting ComfyUI.")
        record = {"accepted": True, "request_id": request_id, "state": "scheduled", "pid": os.getpid(), "requested_at": time.time()}
        self.save_restart(record)
        self.restart_task = asyncio.create_task(self.perform_restart(record))
        return web.json_response(record, status=202)

    async def perform_restart(self, record):
        await asyncio.sleep(1)
        running, queued = self.server.prompt_queue.get_current_queue_volatile()
        if running or queued:
            self.save_restart({**record, "state": "cancelled", "error": "The queue changed before restart. No process was stopped."})
            return
        base_args = getattr(sys, "_arkennemasis_base_comfy_args", sys.argv[1:])
        runner = str(Path(__file__).with_name("launch_backend.py"))
        argv = [sys.executable, "-s", runner, *base_args]
        if sys.platform == "win32":
            argv = [subprocess.list2cmdline([argument]) for argument in argv]
        self.save_restart({**record, "state": "restarting"})
        try:
            os.execv(sys.executable, argv)
        except OSError as error:
            self.save_restart({**record, "state": "failed", "error": f"ComfyUI could not restart ({error.__class__.__name__})."})

    async def register(self, request):
        denied = self.authorize_browser(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        self.prune()
        client_id = body.get("client_id")
        if not isinstance(client_id, str) or client_id not in self.server.sockets:
            return failure(409, "browser_disconnected", "Wait for the ComfyUI browser connection.")
        for session_id, session in list(self.sessions.items()):
            if session["client_id"] == client_id:
                self.remove_session(session_id)
        session_id, secret = secrets.token_urlsafe(24), secrets.token_urlsafe(32)
        self.sessions[session_id] = {"client_id": client_id, "secret": secret, "title": str(body.get("title", "ComfyUI"))[:256], "last_seen": time.time(), "disconnected_at": None}
        return web.json_response({"session_id": session_id, "session_secret": secret, "heartbeat_seconds": 15})

    async def heartbeat(self, request):
        denied = self.authorize_browser(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        self.prune()
        session = self.session_for_browser(body)
        if session is None:
            return failure(403, "unknown_session", "Canvas sharing must reconnect.")
        client_id = body.get("client_id", session["client_id"])
        if not isinstance(client_id, str) or client_id not in self.server.sockets:
            return failure(409, "browser_disconnected", "Waiting for this ComfyUI tab's browser connection to recover.")
        if any(other is not session and other["client_id"] == client_id for other in self.sessions.values()):
            return failure(409, "session_conflict", "This browser connection belongs to another shared page. Stop sharing and reconnect this page.")
        session["client_id"] = client_id
        session["disconnected_at"] = None
        session["last_seen"] = time.time()
        session["title"] = str(body.get("title", session["title"]))[:256]
        pending = [item["event"] for key, item in self.requests.items()
                   if key[0] == body["session_id"] and not item["future"].done() and item["event"]["expires_at"] > time.time()]
        return web.json_response({"connected": True, "commands": pending})

    async def disconnect(self, request):
        denied = self.authorize_browser(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        if self.session_for_browser(body) is None:
            return failure(403, "unknown_session", "Canvas session is not connected.")
        self.remove_session(body["session_id"])
        return web.json_response({"connected": False})

    async def list_sessions(self, request):
        denied = self.authorize_control(request)
        if denied is not None:
            return denied
        self.prune()
        return web.json_response({"sessions": [{"session_id": key, "state": "connected" if value["client_id"] in self.server.sockets else "reconnecting",
                                                **{field: value[field] for field in ("client_id", "title", "last_seen")}} for key, value in self.sessions.items()]})

    async def canvas(self, request):
        denied = self.authorize_control(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        self.prune()
        session_id, command = body.get("session_id"), body.get("command")
        session = self.sessions.get(session_id) if isinstance(session_id, str) else None
        if session is None:
            return failure(409, "no_canvas", "Open ComfyUI and enable Arkennemasis MCP canvas sharing, then select its session.")
        if command not in ("read", "apply", "undo"):
            return failure(400, "invalid_command", "Use read, apply, or undo.")
        if command != "read" and not isinstance(body.get("expected_revision"), str):
            return failure(400, "revision_required", "Read the canvas and provide its expected_revision before editing.")
        if command == "apply" and (not isinstance(body.get("workflow"), dict) or not isinstance(body["workflow"].get("nodes"), list)):
            return failure(400, "invalid_workflow", "Apply requires a ComfyUI UI workflow containing a nodes list.")
        request_id = body.get("request_id", uuid.uuid4().hex)
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
            return failure(400, "invalid_request_id", "request_id must contain 1 to 128 characters.")
        key = (session_id, request_id)
        command_body = {field: body[field] for field in ("command", "expected_revision", "workflow") if field in body}
        fingerprint = hashlib.sha256(json.dumps(command_body, sort_keys=True).encode()).hexdigest()
        item = self.requests.get(key)
        if item and item["fingerprint"] != fingerprint:
            return failure(409, "request_id_reused", "Use a new request_id for a different operation.")
        if item is None:
            if session["client_id"] not in self.server.sockets:
                return failure(409, "canvas_reconnecting", "The selected shared page is reconnecting. Keep that ComfyUI tab open, then read the same session again.")
            if sum(key[0] == session_id for key in self.requests) >= 64:
                return failure(429, "too_many_requests", "Wait before issuing more canvas requests.")
            event = {**command_body, "request_id": request_id, "session_id": session_id, "expires_at": time.time() + self.timeout}
            item = {"fingerprint": fingerprint, "future": asyncio.get_running_loop().create_future(), "expires": time.time() + 300, "event": event}
            self.requests[key] = item
            await self.server.send("arkennemasis.mcp.canvas", event, session["client_id"])
        try:
            status, payload = await asyncio.wait_for(asyncio.shield(item["future"]), self.timeout)
        except asyncio.TimeoutError:
            # Keep the operation pending for a late acknowledgment. A timed-out
            # HTTP caller must not make an already applied edit run a second time.
            status, payload = 504, self.timeout_result()
        return web.json_response(payload, status=status)

    async def acknowledge(self, request):
        denied = self.authorize_browser(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        if self.session_for_browser(body) is None:
            return failure(403, "unknown_session", "Canvas session is not connected.")
        request_id = body.get("request_id")
        if not isinstance(request_id, str):
            return failure(400, "invalid_request_id", "An acknowledgment requires request_id.")
        item = self.requests.get((body["session_id"], request_id))
        if item is None or item["expires"] < time.time():
            return failure(409, "request_expired", "Canvas request has expired.")
        result = body.get("result")
        if not isinstance(result, dict):
            return failure(400, "invalid_result", "An acknowledgment requires a result object.")
        if "error" not in result and not all(field in result for field in ("workflow", "prompt", "revision")):
            return failure(400, "invalid_result", "Canvas result requires workflow, prompt, and revision.")
        status = 409 if "error" in result else 200
        payload = {**result, "session_id": body["session_id"], "request_id": request_id}
        if item["future"].done():
            if item["future"].result() != (status, payload):
                return failure(409, "acknowledgment_conflict", "This request was already acknowledged with a different result.")
        else:
            item["future"].set_result((status, payload))
        self.sessions[body["session_id"]]["last_seen"] = time.time()
        return web.json_response({"acknowledged": True})

    async def validate(self, request):
        denied = self.authorize_control(request)
        if denied is not None:
            return denied
        body = await json_body(request)
        prompt = body.get("prompt")
        if not isinstance(prompt, dict) or not all(isinstance(node, dict) for node in prompt.values()):
            return failure(400, "invalid_prompt", "Supply an API prompt mapping node IDs to node objects.")
        valid, error, outputs, node_errors = await self.validate_prompt(uuid.uuid4().hex, prompt, None)
        return web.json_response({"valid": valid, "error": error, "outputs_to_execute": list(outputs), "node_errors": node_errors})


def register_routes():
    # ComfyUI owns server initialization; defer these imports until its node loader runs.
    from execution import validate_prompt
    from server import PromptServer
    from .comfy_events import attach
    from .setup_routes import register_setup_routes

    server = PromptServer.instance
    bridge = CanvasBridge(server, validate_prompt)
    routes = server.routes
    attach(server)
    register_setup_routes(routes)
    routes.get(PREFIX + "/sessions")(bridge.list_sessions)
    routes.post(PREFIX + "/canvas")(bridge.canvas)
    routes.post(PREFIX + "/validate")(bridge.validate)
    routes.get(PREFIX + "/runtime")(bridge.runtime)
    routes.post(PREFIX + "/restart")(bridge.restart)
    routes.get(PREFIX + "/progress")(bridge.progress)
    routes.post(PREFIX + "/preview")(bridge.preview)
    routes.post(PREFIX + "/browser/register")(bridge.register)
    routes.post(PREFIX + "/browser/connection-status")(bridge.connection_status)
    routes.post(PREFIX + "/browser/heartbeat")(bridge.heartbeat)
    routes.post(PREFIX + "/browser/disconnect")(bridge.disconnect)
    routes.post(PREFIX + "/browser/ack")(bridge.acknowledge)
    return bridge
