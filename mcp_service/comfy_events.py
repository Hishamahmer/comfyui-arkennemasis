"""Bounded, local observation of ComfyUI's existing execution messages."""

import base64
from collections import deque
import copy
from io import BytesIO
from itertools import islice
import json
import math
import re
import struct
import time

from PIL import Image, ImageOps


MAX_EVENTS = 64
MAX_PREVIEW_BYTES = 512 * 1024
MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_SOURCE_PIXELS = 16 * 1024 * 1024
EVENTS = {"status", "execution_start", "execution_cached", "executing", "executed",
          "progress", "progress_state", "execution_success", "execution_error", "execution_interrupted"}
ID_FIELDS = ("prompt_id", "node", "node_id", "display_node", "display_node_id", "parent_node_id", "real_node_id")


def _identifier(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None


def _fields(data):
    result = {key: data[key] for key in ID_FIELDS if _identifier(data.get(key))}
    if "node" in data and data["node"] is None:
        result["node"] = None
    for key in ("value", "max"):
        value = data.get(key)
        if type(value) in (int, float) and 0 <= value <= 10**12 and math.isfinite(value):
            result[key] = value
    if isinstance(data.get("state"), str) and data["state"] in {"pending", "running", "finished", "error"}:
        result["state"] = data["state"]
    return result


class ExecutionEvents:
    def __init__(self):
        self.events = deque(maxlen=MAX_EVENTS)
        self.current = {"state": "unknown"}
        self.sequence = 0
        self.latest_preview = None
        self.current_sid = None
        self.last_preview_at = -float("inf")

    def capture(self, event, data, sid=None):
        if type(event) is int and event in {1, 2, 4}:
            self._capture_preview(event, data, sid)
            return
        if not isinstance(event, str) or event not in EVENTS or not isinstance(data, dict):
            return
        safe = _fields(data)
        if event == "status":
            status = data.get("status")
            info = status.get("exec_info") if isinstance(status, dict) else None
            remaining = info.get("queue_remaining") if isinstance(info, dict) else None
            safe = {"queue_remaining": remaining} if type(remaining) is int and 0 <= remaining <= 10**9 else {}
        elif event == "progress_state":
            nodes = data.get("nodes")
            if isinstance(nodes, dict):
                safe["nodes"] = {key: _fields(value) for key, value in islice(nodes.items(), 64)
                                 if _identifier(key) and isinstance(value, dict)}
                safe["nodes_truncated"] = len(nodes) > 64
        elif event == "execution_cached":
            nodes = data.get("nodes")
            if isinstance(nodes, list):
                safe["nodes"] = [node for node in nodes[:64] if _identifier(node)]
                safe["nodes_truncated"] = len(nodes) > 64
        if not safe:
            return
        prompt_id = safe.get("prompt_id")
        if prompt_id and (event == "execution_start" or self.current.get("prompt_id") != prompt_id):
            self.current = {"prompt_id": prompt_id, "state": "running"}
            self.latest_preview = None
            self.current_sid = sid
        if prompt_id and prompt_id == self.current.get("prompt_id"):
            self.current.update({key: value for key, value in safe.items() if key in ID_FIELDS or key in {"value", "max"}})
            terminal = {"execution_success": "finished", "execution_error": "error", "execution_interrupted": "interrupted"}
            if event in terminal:
                self.current["state"] = terminal[event]
            elif event == "executing" and safe.get("node", "") is None and self.current["state"] == "running":
                self.current["state"] = "finished"
        self.sequence += 1
        self.events.append({"sequence": self.sequence, "event": event, "timestamp": time.time(), "data": safe})

    def _capture_preview(self, event, data, sid):
        prompt_id = self.current.get("prompt_id")
        if not prompt_id or sid != self.current_sid or self.current.get("state") != "running":
            return
        if time.monotonic() - self.last_preview_at < 0.5:
            return
        metadata = {}
        if event == 4:
            if isinstance(data, (tuple, list)) and len(data) == 2:
                data, metadata = data
                if not isinstance(metadata, dict):
                    return
            elif isinstance(data, (bytes, bytearray)) and 4 < len(data) <= MAX_SOURCE_BYTES:
                size = struct.unpack(">I", data[:4])[0]
                if size > 4096 or size + 4 >= len(data):
                    return
                metadata = json.loads(data[4:4 + size])
                if not isinstance(metadata, dict):
                    return
                data = data[4 + size:]
            else:
                return
            if metadata.get("prompt_id") != prompt_id:
                return
        if event == 1:
            if not isinstance(data, (bytes, bytearray)) or not 4 < len(data) <= MAX_SOURCE_BYTES:
                return
            if struct.unpack(">I", data[:4])[0] not in {1, 2}:
                return
            data = data[4:]
        if isinstance(data, (tuple, list)) and len(data) == 3:
            source = data[1]
            if not isinstance(source, Image.Image):
                return
        elif isinstance(data, (bytes, bytearray)) and len(data) <= MAX_SOURCE_BYTES:
            source = Image.open(BytesIO(data))
        else:
            return
        if source.width * source.height > MAX_SOURCE_PIXELS or source.width < 1 or source.height < 1:
            return
        thumbnail = ImageOps.contain(source, (512, 512)).convert("RGB")
        output = BytesIO()
        thumbnail.save(output, format="JPEG", quality=75)
        raw = output.getvalue()
        if len(raw) > MAX_PREVIEW_BYTES:
            return
        self.latest_preview = {"prompt_id": prompt_id, "node_id": metadata.get("node_id") if _identifier(metadata.get("node_id"))
                               else self.current.get("node_id", self.current.get("node")),
                               "mime_type": "image/jpeg", "data": base64.b64encode(raw).decode("ascii"),
                               "width": thumbnail.width, "height": thumbnail.height, "timestamp": time.time()}
        self.last_preview_at = time.monotonic()

    def snapshot(self):
        return copy.deepcopy({"current": self.current, "events": list(self.events),
                              "preview_available": self.latest_preview is not None, "last_sequence": self.sequence})

    def preview(self, prompt_id):
        if not _identifier(prompt_id):
            raise ValueError("Use the prompt_id returned when the job was queued.")
        if self.latest_preview is None or self.latest_preview["prompt_id"] != prompt_id:
            return None
        return dict(self.latest_preview)


def attach(server):
    """Observe this server only; existing clients retain the original send contract."""
    existing = getattr(server, "_arkennemasis_execution_events", None)
    if existing is not None:
        return existing
    observer = ExecutionEvents()
    original_send = server.send

    async def send(event, data, sid=None):
        result = await original_send(event, data, sid)
        try:
            observer.capture(event, data, sid)
        except Exception:
            # Optional diagnostics must never interrupt ComfyUI's message publisher.
            pass
        return result

    server.send = send
    server._arkennemasis_execution_events = observer
    return observer
