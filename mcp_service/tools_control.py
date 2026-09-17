"""Connection diagnosis, explicit queue actions and registered-session restart."""

import json
import base64
from pathlib import Path
import threading
import time
from typing import Literal
import uuid

import httpx
from mcp.server.mcpserver import Image

from .client import ComfyError
from .diagnostics import connection_state, public_probe, read_json, read_log
from .launch_config import LaunchConfig


def register_control_tools(tool, settings, client, ledger, canvas_edits, operations):
    restart_lock = threading.Lock()

    @tool("comfy:read")
    async def get_connection_status() -> dict:
        """Separate MCP gateway, ComfyUI, browser sharing and tunnel diagnostics. Tool absence in an AI chat is different from an unreachable endpoint or unshared canvas."""
        result = connection_state(settings)
        try:
            result["comfyui"] = await client.status()
            result["canvases"] = await client.bridge("sessions")
        except (httpx.HTTPError, ComfyError) as exc:
            result["backend_error"] = type(exc).__name__
        return result

    @tool("comfy:read", open_world=True)
    def check_public_connection(request_id: str, samples: int = 3) -> dict:
        """Start an explicit public TLS/HTTP check of this installation's fixed origin. Poll get_operation; it diagnoses the public route separately from local readiness."""
        return operations.submit("public_connection_check", "comfy:read", request_id, {"samples": samples},
                                 lambda: public_probe(settings, samples))

    @tool("comfy:develop")
    def read_comfyui_log(source: Literal["comfyui", "connection", "gateway", "launcher"] = "comfyui", lines: int = 100) -> dict:
        """Read recent startup/import/execution or connection errors with known credentials redacted. Available even when the ComfyUI backend has stopped."""
        return read_log(settings, source, lines)

    @tool("comfy:develop")
    async def inspect_comfyui_runtime() -> dict:
        """Read the backend's actual Python executable/version, process identity and supported launch flags, without dumping environment variables."""
        return await client.bridge("runtime")

    @tool("comfy:maintain", write=True)
    def restart_comfyui(request_id: str) -> dict:
        """Request one restart of the ComfyUI session registered with the companion. The queue must be idle. Reuse the same UUID on retries; poll get_restart_status. No arbitrary process or command control."""
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError("Use a canonical UUID request_id.")
        root = Path(settings.state_dir)
        with restart_lock:
            session = read_json(root / "session-status.json")
            if time.time() - session.get("updated_at", 0) > 30:
                raise ValueError("The companion is not active. Start ComfyUI with its MCP launcher before requesting restart.")
            result = read_json(root / "lifecycle-result.json")
            previous = read_json(root / "lifecycle-request.json")
            if result.get("id") == request_id:
                return result
            if previous.get("id") == request_id:
                return {"id": request_id, "state": "requested"}
            if previous and (result.get("id") != previous.get("id") or result.get("state") in {"accepted", "restarting"}):
                if time.time() - previous.get("created_at", 0) < 200:
                    raise ValueError("A restart is already pending. Inspect get_restart_status first.")
            request = {"id": request_id, "action": "restart", "created_at": time.time()}
            temporary = root / "lifecycle-request.tmp"
            temporary.write_text(json.dumps(request), encoding="utf-8")
            temporary.replace(root / "lifecycle-request.json")
        return {"id": request_id, "state": "requested", "note": "Restart is not yet confirmed; poll get_restart_status."}

    @tool("comfy:maintain")
    def get_restart_status(request_id: str) -> dict:
        """Read the registered companion's restart result: requested, accepted, restarting, ready or failed."""
        result = read_json(Path(settings.state_dir) / "lifecycle-result.json")
        if result.get("id") == request_id:
            return result
        pending = read_json(Path(settings.state_dir) / "lifecycle-request.json")
        return {"id": request_id, "state": "requested" if pending.get("id") == request_id else "unknown"}

    @tool("comfy:read")
    async def get_queue() -> dict:
        """List current running and pending job IDs before an interruption or queue change."""
        return await client.queue_state()

    @tool("comfy:run", write=True)
    async def interrupt_job(prompt_id: str) -> dict:
        """Request interruption of the specified running job. Does not silently interrupt a different job. Poll get_job to confirm stopping."""
        return await client.interrupt(prompt_id)

    @tool("comfy:run", write=True)
    async def remove_pending_jobs(prompt_ids: list[str]) -> dict:
        """Remove only the listed pending jobs after reading get_queue. Jobs added concurrently are preserved."""
        return await client.clear_pending(prompt_ids)

    @tool("comfy:maintain", write=True)
    async def release_model_memory(unload_models: bool = True, free_memory: bool = True) -> dict:
        """Ask ComfyUI to unload models and/or release cached memory while its queue is idle."""
        return await client.free_memory(unload_models, free_memory)

    @tool("comfy:run", write=True, open_world=True)
    async def run_live_canvas(session_id: str, expected_revision: str, request_id: str) -> dict:
        """Run the exact shared canvas revision using its browser client identity for execution highlights. Reuse a UUID only for retries of this same run. Returns a job id; never call this for edit-only requests."""
        payload = await canvas_edits.prepare_run(client, session_id, expected_revision, request_id)
        if ledger.get(request_id) is not None:
            return await ledger.submit(client, request_id, payload["prompt"], payload["workflow"], payload["client_id"])
        checked = await client.validate(payload["prompt"])
        if not checked.get("valid", False):
            return {"queued": False, "validation": checked}
        return await ledger.submit(client, request_id, payload["prompt"], payload["workflow"], payload["client_id"])

    @tool("comfy:read")
    async def get_execution_progress() -> dict:
        """Read recent execution events/progress captured by the local bridge. Poll explicitly; this does not make the AI continuously watch a generation."""
        return await client.bridge("progress")

    @tool("comfy:media")
    async def get_execution_preview(prompt_id: str):
        """Retrieve the latest small preview for this exact job, when ComfyUI emits previews. This is a snapshot, not an automatic stream."""
        result = await client.bridge("preview", {"prompt_id": prompt_id})
        if not result.get("data"):
            return {"available": False}
        return Image(data=base64.b64decode(result["data"]), format="jpeg")

    @tool("comfy:maintain")
    def get_launch_settings() -> dict:
        """Read validated VRAM, precision, cache and preview overrides and their revision. Lists only flags supported by the installed ComfyUI."""
        return LaunchConfig(settings.state_dir, settings.comfy_root).get()

    @tool("comfy:maintain", write=True)
    def change_launch_settings(changes: dict, expected_revision: str) -> dict:
        """Save supported launch overrides with a backup. Null restores the original launcher value. Changes activate on the next explicit restart or managed BAT launch; this does not restart ComfyUI."""
        return LaunchConfig(settings.state_dir, settings.comfy_root).patch(changes, expected_revision)

    @tool("comfy:maintain", write=True)
    def restore_launch_settings(backup_revision: str, expected_revision: str) -> dict:
        """Restore a previously saved launch-settings revision without restarting ComfyUI."""
        return LaunchConfig(settings.state_dir, settings.comfy_root).restore(backup_revision, expected_revision)
