"""Bounded local ComfyUI HTTP operations used by the MCP tool layer."""

import asyncio
import ipaddress
import json
from urllib.parse import quote, urlsplit
import uuid

import httpx

from .assets import MAX_IMAGE_BYTES, media_descriptor, output_assets
from .workflows import workflow_format, workflow_revision


class ComfyError(RuntimeError):
    def __init__(self, message, *, status_code=None, details=None):
        super().__init__(message)
        self.status_code = status_code
        self.details = details


def _job_id(value):
    try:
        valid = isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("The job/request id must be a canonical lowercase UUID.")
    return value


class ComfyClient:
    def __init__(self, base_url, bridge_token="", *, client=None):
        parsed = urlsplit(base_url)
        host = parsed.hostname or ""
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host.lower() == "localhost"
        if (parsed.scheme not in {"http", "https"} or not loopback or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}):
            raise ValueError("ComfyUI must use a local loopback HTTP(S) URL without credentials or a path.")
        self.base_url = base_url.rstrip("/")
        self.bridge_token = bridge_token
        self._owns_client = client is None
        self._http = client or httpx.AsyncClient(timeout=httpx.Timeout(60, connect=5), trust_env=False, follow_redirects=False)
        self._queue_lock = asyncio.Lock()
        self._submissions = {}

    async def close(self):
        if self._owns_client:
            await self._http.aclose()

    async def _json(self, method, path, **kwargs):
        response = await self._http.request(method, self.base_url + path, follow_redirects=False, **kwargs)
        if response.is_error or response.is_redirect:
            try:
                details = response.json()
            except ValueError:
                details = response.text[:1000]
            raise ComfyError(f"ComfyUI request failed ({response.status_code}): {str(details)[:2000]}", status_code=response.status_code, details=details)
        return response.json() if response.content else {}

    async def bridge(self, path, payload=None):
        if path not in {"sessions", "canvas", "validate", "runtime", "progress", "preview"}:
            raise ValueError("Unknown MCP bridge operation.")
        if not self.bridge_token:
            raise ComfyError("The local ComfyUI bridge is not configured.")
        return await self._json("GET" if payload is None else "POST", "/arkennemasis/mcp/" + path,
                                headers={"X-Ark-Bridge-Token": self.bridge_token}, **({"json": payload} if payload is not None else {}))

    async def status(self):
        stats, queue = await asyncio.gather(self._json("GET", "/system_stats", timeout=5), self._json("GET", "/queue", timeout=5))
        system = stats.get("system", {})
        return {"connected": True, "system": {key: system[key] for key in ("os", "comfyui_version", "python_version", "pytorch_version") if key in system},
                "devices": stats.get("devices", []), "queue_running": len(queue.get("queue_running", [])), "queue_pending": len(queue.get("queue_pending", []))}

    async def node_types(self, query="", limit=100):
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("Node listing limit must be between 1 and 500.")
        if not isinstance(query, str):
            raise ValueError("Node query must be text.")
        objects = await self._json("GET", "/object_info")
        matches = []
        for class_type, schema in sorted(objects.items()):
            if query.casefold() not in " ".join(str(schema.get(key, "")) for key in ("display_name", "category", "name" )).casefold() and query.casefold() not in class_type.casefold():
                continue
            matches.append({"class_type": class_type, "display_name": schema.get("display_name", class_type),
                            "category": schema.get("category", ""), "output_node": bool(schema.get("output_node"))})
        return {"total": len(matches), "items": matches[:limit]}

    async def node_schema(self, class_type):
        if not isinstance(class_type, str) or not class_type or class_type in {".", ".."} or len(class_type) > 240:
            raise ValueError("Provide a valid node class name.")
        objects = await self._json("GET", "/object_info/" + quote(class_type, safe=""))
        if class_type not in objects:
            raise ComfyError("Node class is not installed.", status_code=404)
        return objects[class_type]

    async def validate(self, prompt):
        if workflow_format(prompt) != "api":
            raise ValueError("Validation requires an API prompt graph. Use the canvas bridge to convert a UI workflow.")
        workflow_revision(prompt)
        return await self.bridge("validate", {"prompt": prompt})

    async def queue(self, prompt, workflow=None, request_id="", client_id=None):
        request_id = _job_id(request_id)
        if workflow_format(prompt) != "api":
            raise ValueError("Queue requires an API prompt graph.")
        digest = workflow_revision(prompt)
        if client_id is not None:
            if not isinstance(client_id, str) or not 1 <= len(client_id) <= 128:
                raise ValueError("Invalid shared browser client identity.")
            digest += client_id
        if workflow is not None:
            if workflow_format(workflow) != "ui":
                raise ValueError("The optional workflow must be a UI workflow, separate from the API prompt graph.")
            digest += workflow_revision(workflow)
        async with self._queue_lock:
            previous = self._submissions.get(request_id)
            if previous:
                if previous[0] != digest:
                    raise ValueError("This request id was already used with a different graph.")
                if previous[1] is None:
                    raise ComfyError("The earlier submission has an uncertain result. Inspect this job id before making another request.")
                return previous[1]
            extra_data = {"arkennemasis_mcp": {"request_id": request_id}}
            if workflow is not None:
                extra_data["extra_pnginfo"] = {"workflow": workflow}
            self._submissions[request_id] = (digest, None)
            result = await self._json("POST", "/prompt", json={"prompt_id": request_id, "prompt": prompt, "client_id": client_id or "arkennemasis-mcp", "extra_data": extra_data})
            self._submissions[request_id] = (digest, result)
            return result

    async def job(self, prompt_id):
        prompt_id = _job_id(prompt_id)
        history = await self._json("GET", "/history/" + prompt_id)
        if prompt_id in history:
            record = history[prompt_id]
            state = record.get("status", {})
            status = "completed" if state.get("completed") and state.get("status_str") != "error" else "failed"
            return {"prompt_id": prompt_id, "status": status, "messages": state.get("messages", []), "assets": output_assets(record)}
        queue = await self._json("GET", "/queue")
        for field, state in (("queue_running", "running"), ("queue_pending", "pending")):
            for position, item in enumerate(queue.get(field, [])):
                if item[1] == prompt_id:
                    return {"prompt_id": prompt_id, "status": state, "queue_position": position}
        return {"prompt_id": prompt_id, "status": "unknown", "detail": "Job is absent from the current queue and retained history."}

    async def cancel(self, prompt_id):
        prompt_id = _job_id(prompt_id)
        queue = await self._json("GET", "/queue")
        if any(item[1] == prompt_id for item in queue.get("queue_running", [])):
            return {"prompt_id": prompt_id, "cancelled": False, "status": "running", "detail": "Only pending jobs can be cancelled through this service."}
        item = next((item for item in queue.get("queue_pending", []) if item[1] == prompt_id), None)
        if item is None:
            return {"prompt_id": prompt_id, "cancelled": False, "status": "not_pending"}
        metadata = item[3] if len(item) > 3 and isinstance(item[3], dict) else {}
        marker = metadata.get("arkennemasis_mcp", {})
        if not isinstance(marker, dict) or marker.get("request_id") != prompt_id:
            raise ComfyError("This pending job was not submitted by Arkennemasis MCP.")
        await self._json("POST", "/queue", json={"delete": [prompt_id]})
        latest = await self.job(prompt_id)
        return {"prompt_id": prompt_id, "cancelled": latest["status"] == "unknown", "status": "cancelled" if latest["status"] == "unknown" else latest["status"]}

    async def outputs(self, prompt_id):
        result = await self.job(prompt_id)
        return {"prompt_id": result["prompt_id"], "status": result["status"], "assets": result.get("assets", [])}

    async def queue_state(self):
        queue = await self._json("GET", "/queue", timeout=5)
        return {key: [{"prompt_id": item[1], "position": index} for index, item in enumerate(queue.get(key, []))]
                for key in ("queue_running", "queue_pending")}

    async def interrupt(self, prompt_id):
        prompt_id = _job_id(prompt_id)
        queue = await self.queue_state()
        if not any(item["prompt_id"] == prompt_id for item in queue["queue_running"]):
            return {"prompt_id": prompt_id, "interruption_requested": False, "reason": "Job is not running."}
        await self._json("POST", "/interrupt", json={"prompt_id": prompt_id})
        return {"prompt_id": prompt_id, "interruption_requested": True, "note": "Poll get_job to confirm execution stopped."}

    async def clear_pending(self, prompt_ids):
        if not isinstance(prompt_ids, list) or not 1 <= len(prompt_ids) <= 500:
            raise ValueError("Provide 1 to 500 pending job ids from get_queue.")
        ids = list(dict.fromkeys(_job_id(value) for value in prompt_ids))
        queue = await self.queue_state()
        pending = {item["prompt_id"] for item in queue["queue_pending"]}
        if not set(ids).issubset(pending):
            raise ValueError("The pending queue changed. Read it again before removing jobs.")
        await self._json("POST", "/queue", json={"delete": ids})
        latest = await self.queue_state()
        retained = {item["prompt_id"] for item in latest["queue_pending"]}
        return {"removed": [value for value in ids if value not in retained], "queue": latest,
                "note": "Jobs added after the read were preserved; a job may have started before removal."}

    async def free_memory(self, unload_models=True, free_memory=True):
        if not unload_models and not free_memory:
            raise ValueError("Select unload_models or free_memory.")
        queue = await self.queue_state()
        if queue["queue_running"] or queue["queue_pending"]:
            raise ValueError("Wait until the queue is idle before clearing model memory.")
        await self._json("POST", "/free", json={"unload_models": unload_models, "free_memory": free_memory})
        return {"requested": True, "unload_models": unload_models, "free_memory": free_memory,
                "note": "ComfyUI processes these memory cleanup flags; this is an acknowledgment, not a measured memory reduction."}

    async def image(self, filename, subfolder="", kind="output"):
        descriptor = media_descriptor(filename, subfolder, kind, image_only=True)
        async with self._http.stream("GET", self.base_url + descriptor["view_path"], follow_redirects=False) as response:
            if response.status_code != 200:
                raise ComfyError(f"ComfyUI image request failed ({response.status_code}).", status_code=response.status_code)
            mime_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if not mime_type.startswith("image/"):
                raise ComfyError("The requested file was not returned as an image.")
            chunks = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    raise ComfyError("Image exceeds the 20 MiB inline image limit.")
                chunks.append(chunk)
            return b"".join(chunks), mime_type

    async def upload_image(self, filename, data, overwrite=False):
        descriptor = media_descriptor(filename, kind="input", image_only=True)
        if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Provide nonempty image bytes, up to 20 MiB.")
        result = await self._json("POST", "/upload/image", data={"type": "input", "overwrite": "true" if overwrite else "false"},
                                  files={"image": (filename, data, descriptor["mime_type"])})
        return media_descriptor(result["name"], result.get("subfolder", ""), result.get("type", "input"), image_only=True)
