"""Local owner setup endpoints, deliberately separate from public MCP tools."""

import asyncio
import ipaddress
import json
import logging
import subprocess
import sys
from urllib.parse import urlsplit

from aiohttp import web

from .installation import Installation
from .settings import COMFY_DIR, DEFAULT_CONFIG

PREFIX = "/arkennemasis/mcp/setup"
MAX_BODY = 64 * 1024
LOGGER = logging.getLogger(__name__)


def authorized_owner(request):
    try:
        peer = ipaddress.ip_address(request.remote)
        if peer.version == 6 and peer.ipv4_mapped:
            peer = peer.ipv4_mapped
        target = urlsplit(f"{request.scheme}://{request.host}")
        origin = urlsplit(request.headers.get("Origin", ""))
        local_host = target.hostname == "localhost" or ipaddress.ip_address(target.hostname).is_loopback
        return (peer.is_loopback and local_host and not origin.username and not origin.password
                and not origin.path and not origin.query and not origin.fragment
                and (origin.scheme, origin.netloc) == (target.scheme, target.netloc)
                and request.headers.get("X-Ark-Canvas") == "1")
    except (TypeError, ValueError):
        return False


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON property.")
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError("JSON must not contain non-finite numbers.")


async def owner_body(request):
    if not authorized_owner(request):
        raise web.HTTPForbidden(text="Open setup from the local ComfyUI page.")
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="Use application/json.")
    data = bytearray()
    async for chunk in request.content.iter_chunked(8192):
        data.extend(chunk)
        if len(data) > MAX_BODY:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_BODY, actual_size=len(data))
    try:
        body = json.loads(data, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise web.HTTPBadRequest(text="Invalid JSON object.") from exc
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="Expected a JSON object.")
    return body


class SetupRoutes:
    def __init__(self, installation):
        self.installation = installation
        self.lock = asyncio.Lock()
        self.install_task = None
        self.install_status = {"state": "idle", "message": ""}

    async def response(self, request, action, *, empty=True, mutation=False):
        body = await owner_body(request)
        if empty and body:
            raise web.HTTPBadRequest(text="This setup action takes no parameters.")
        try:
            if mutation:
                if self.lock.locked():
                    return web.json_response({"error": {"message": "Another setup action is running. Wait for it to finish."}}, status=409)
                async with self.lock:
                    value = await asyncio.to_thread(action, body)
            else:
                value = await asyncio.to_thread(action, body)
            return web.json_response(value, headers={"Cache-Control": "no-store"})
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            message = str(exc) if isinstance(exc, ValueError) else "Setup could not complete. Check file permissions and the local ComfyUI log."
            LOGGER.warning("Arkennemasis owner setup failed: %s", type(exc).__name__)
            return web.json_response({"error": {"message": message}}, status=400, headers={"Cache-Control": "no-store"})

    async def status(self, request):
        return await self.response(request, lambda _: {**self.installation.status(), "installation": self.install_status})

    async def save(self, request):
        return await self.response(request, lambda body: self.installation.save(body, comfy_url=f"{request.scheme}://{request.host}"), empty=False, mutation=True)

    async def connection_url(self, request):
        return await self.response(request, lambda _: self.installation.connection_url())

    async def install_launchers(self, request):
        return await self.response(request, lambda _: self.installation.install_launchers(), mutation=True)

    async def start(self, request):
        return await self.response(request, lambda _: self.installation.start(), mutation=True)

    async def approve_package(self, request):
        def approve(body):
            if set(body) != {"plan_id"}:
                raise ValueError("Select one pending package plan to approve.")
            return self.installation.approve_package(body["plan_id"])
        return await self.response(request, approve, empty=False, mutation=True)

    async def install_dependencies(self, request):
        body = await owner_body(request)
        if body:
            raise web.HTTPBadRequest(text="Dependency setup takes no parameters. Save the selected Python first.")
        if self.lock.locked() or self.install_task is not None and not self.install_task.done():
            return web.json_response({"error": {"message": "Another setup action is already running."}}, status=409)
        self.install_status = {"state": "running", "message": "Installing optional MCP dependencies. This can take several minutes."}
        self.install_task = asyncio.create_task(self.install())
        return web.json_response(self.install_status, status=202, headers={"Cache-Control": "no-store"})

    async def install(self):
        async with self.lock:
            try:
                result = await asyncio.to_thread(self.installation.install_dependencies)
                self.install_status = {"state": "complete", "message": result["message"]}
            except (ValueError, OSError, subprocess.SubprocessError) as exc:
                message = str(exc) if isinstance(exc, ValueError) else "Installation failed. Check Python, file permissions and internet access."
                self.install_status = {"state": "error", "message": message}


def register_setup_routes(routes, *, config_path=DEFAULT_CONFIG, comfy_root=COMFY_DIR, python=sys.executable):
    setup = SetupRoutes(Installation(config_path, comfy_root, python))
    for path, handler in (("status", setup.status), ("save", setup.save),
                          ("connection-url", setup.connection_url), ("install-dependencies", setup.install_dependencies),
                          ("install-launchers", setup.install_launchers), ("start", setup.start),
                          ("approve-package", setup.approve_package)):
        routes.post(f"{PREFIX}/{path}")(handler)
    return setup
