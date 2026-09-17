"""Small, explicit MCP tools; domain behavior belongs to the owning modules."""

import base64
import uuid
from functools import partial, wraps
from inspect import iscoroutinefunction

import anyio.to_thread
from mcp.server.mcpserver import Image
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .auth import require_scope
from .workflows import workflow_format
from .tools_development import register_development_tools
from .tools_control import register_control_tools
from .tools_maintenance import register_maintenance_tools
from .tools_media import register_media_tools


def register_tools(server, settings, client, store, ledger, canvas_edits, operations, *, stdio=False):
    def check(scope):
        if scope not in settings.enabled_scopes:
            raise PermissionError(f"Capability {scope} is disabled. The installation owner can enable it in local MCP settings.")
        if not stdio:
            require_scope(scope)

    def tool(scope, *, write=False, repeatable=True, open_world=False):
        oauth = settings.auth_mode == "oauth" and not stdio
        scheme = {"type": "oauth2", "scopes": [scope]} if oauth else {"type": "noauth"}

        def register(fn):
            @wraps(fn)
            async def authorized(*args, **kwargs):
                try:
                    check(scope)
                except PermissionError as exc:
                    metadata = {}
                    if oauth:
                        discovery = settings.public_url.rstrip("/") + "/.well-known/oauth-protected-resource/mcp"
                        metadata["mcp/www_authenticate"] = [
                            f'Bearer error="insufficient_scope", scope="{scope}", resource_metadata="{discovery}"']
                    return CallToolResult(content=[TextContent(type="text", text=str(exc))],
                                          is_error=True, structured_content={}, meta=metadata)
                if iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return await anyio.to_thread.run_sync(partial(fn, *args, **kwargs))

            return server.tool(annotations=ToolAnnotations(read_only_hint=not write,
                                                            destructive_hint=write,
                                                            idempotent_hint=repeatable,
                                                            open_world_hint=open_world),
                               meta={"securitySchemes": [scheme]})(authorized)
        return register

    @tool("comfy:read")
    async def server_status() -> dict:
        """Inspect local ComfyUI connectivity, system details and current queue. Does not start a generation."""
        check("comfy:read")
        return await client.status()

    @tool("comfy:read")
    async def list_node_types(query: str = "", limit: int = 50) -> dict:
        """Find installed node classes by name/category. Fetch get_node_schema before configuring a node."""
        check("comfy:read")
        return await client.node_types(query=query, limit=limit)

    @tool("comfy:read")
    async def get_node_schema(class_type: str) -> dict:
        """Read an installed node's exact input types, widget options and outputs."""
        check("comfy:read")
        return await client.node_schema(class_type)

    @tool("comfy:read")
    def list_workflows() -> dict:
        """List saved JSON workflows in the configured workflow directory."""
        check("comfy:read")
        return {"workflows": store.list()}

    @tool("comfy:read")
    def read_workflow(name: str) -> dict:
        """Read a saved workflow, its UI/API format and revision. This is not the live browser canvas."""
        check("comfy:read")
        return store.read(name)

    @tool("comfy:write", write=True)
    def save_workflow(name: str, workflow: dict, expected_revision: str | None = None) -> dict:
        """Create a workflow JSON file. To overwrite, supply its current revision; a backup is saved first."""
        check("comfy:write")
        return store.write(name, workflow, expected_revision=expected_revision)

    @tool("comfy:write", write=True)
    def patch_workflow(name: str, operations: list[dict], expected_revision: str) -> dict:
        """Atomically patch a saved workflow using JSON Patch add/remove/replace/test and its current revision."""
        check("comfy:write")
        return store.patch(name, operations, expected_revision)

    @tool("comfy:write", write=True)
    def restore_workflow(name: str, backup_id: str, expected_revision: str) -> dict:
        """Restore a returned backup_id, checking the current revision and backing up the state being replaced."""
        check("comfy:write")
        return store.restore(name, backup_id, expected_revision)

    @tool("comfy:read")
    async def validate_prompt(prompt: dict) -> dict:
        """Validate an executable API prompt using ComfyUI without queuing it. UI JSON needs read_canvas conversion first."""
        check("comfy:read")
        if workflow_format(prompt) != "api":
            raise ValueError("Expected executable API prompt, not UI workflow JSON.")
        return await client.validate(prompt)

    @tool("comfy:run", write=True, open_world=True)
    async def queue_prompt(prompt: dict, request_id: str, workflow: dict | None = None) -> dict:
        """Queue an API prompt once. Supply a fresh UUID per intended run; reuse the same UUID on retries. Returns a job ID, not a finished image."""
        check("comfy:run")
        if workflow_format(prompt) != "api":
            raise ValueError("Expected executable API prompt. Read the live canvas to obtain one from UI JSON.")
        return await ledger.submit(client, request_id, prompt, workflow)

    @tool("comfy:read")
    async def get_job(prompt_id: str) -> dict:
        """Read a specific generation's queue/history state and any persisted submission uncertainty."""
        check("comfy:read")
        result = await client.job(prompt_id)
        return {**result, "submission": ledger.get(prompt_id)}

    @tool("comfy:run", write=True)
    async def cancel_pending_job(prompt_id: str) -> dict:
        """Remove this exact pending job. Does not globally interrupt a running generation or clear the queue."""
        check("comfy:run")
        return await client.cancel(prompt_id)

    @tool("comfy:read")
    async def list_outputs(prompt_id: str) -> dict:
        """List generated media from a job. Use get_output_image for inline images; local view paths aren't public URLs."""
        check("comfy:read")
        return await client.outputs(prompt_id)

    @tool("comfy:media")
    async def get_output_image(prompt_id: str, asset_index: int = 0) -> Image:
        """Return an actual generated image to the AI, selected from list_outputs. Supports images within the media size limit."""
        check("comfy:media")
        assets = (await client.outputs(prompt_id))["assets"]
        if not 0 <= asset_index < len(assets):
            raise ValueError("asset_index is outside this job's output list.")
        asset = assets[asset_index]
        if not asset["mime_type"].startswith("image/"):
            raise ValueError("This output is not an image.")
        data, mime_type = await client.image(asset["filename"], asset["subfolder"], asset["type"])
        if len(data) > settings.max_media_bytes:
            raise ValueError("Image exceeds the configured media size limit.")
        return Image(data=data, format=mime_type.split("/", 1)[-1])

    @tool("comfy:media", write=True, repeatable=False)
    async def upload_reference_image(filename: str, image_base64: str) -> dict:
        """Import base64 image bytes into ComfyUI input, preserving existing files. Returns the filename to set on a LoadImage node."""
        check("comfy:media")
        if len(image_base64) > ((settings.max_media_bytes + 2) // 3) * 4:
            raise ValueError("Image exceeds the configured media size limit.")
        data = base64.b64decode(image_base64, validate=True)
        if not data or len(data) > settings.max_media_bytes:
            raise ValueError("Image is empty or too large.")
        return await client.upload_image(filename, data, overwrite=False)

    @tool("comfy:read")
    async def list_canvases() -> dict:
        """List browser sessions that have explicitly enabled Arkennemasis MCP canvas sharing."""
        check("comfy:read")
        return await client.bridge("sessions")

    @tool("comfy:read")
    async def read_canvas(session_id: str) -> dict:
        """Read the selected live canvas, including UI workflow, executable prompt and revision. Requires a connected opted-in browser."""
        check("comfy:read")
        return await client.bridge("canvas", {"session_id": session_id, "command": "read",
                                               "request_id": str(uuid.uuid4())})

    @tool("comfy:write", write=True)
    async def apply_canvas(session_id: str, workflow: dict, expected_revision: str, request_id: str) -> dict:
        """Apply UI workflow JSON to one live canvas if its revision still matches. Acknowledges actual browser state and keeps an undo snapshot. Reuse request_id only on retries."""
        check("comfy:write")
        if workflow_format(workflow) != "ui":
            raise ValueError("A live canvas requires UI workflow JSON with nodes and links.")
        return await canvas_edits.apply(client, session_id, request_id, "apply", expected_revision, workflow=workflow)

    @tool("comfy:write", write=True)
    async def patch_canvas(session_id: str, operations: list[dict], expected_revision: str, request_id: str) -> dict:
        """Patch the selected live UI workflow using JSON Patch add/remove/replace/test. Checks the revision again in the browser before applying."""
        check("comfy:write")
        return await canvas_edits.apply(client, session_id, request_id, "patch", expected_revision, operations=operations)

    @tool("comfy:write", write=True)
    async def undo_canvas(session_id: str, expected_revision: str, request_id: str) -> dict:
        """Restore the previous MCP canvas snapshot only if no intervening canvas edits occurred."""
        check("comfy:write")
        return await canvas_edits.apply(client, session_id, request_id, "undo", expected_revision)

    register_development_tools(tool, settings)
    register_control_tools(tool, settings, client, ledger, canvas_edits, operations)
    register_maintenance_tools(tool, settings, operations)
    register_media_tools(tool, settings, operations)

    @tool("comfy:read")
    def get_operation(request_id: str) -> dict:
        """Poll a maintenance/download operation. A started operation is not a completed change. Do not resubmit uncertain operations under a new id without inspecting the target."""
        result = operations.get(request_id)
        check(result["required_scope"])
        return result
