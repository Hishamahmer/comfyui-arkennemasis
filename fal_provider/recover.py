"""fal Recover Result - collect a finished fal request without paying for it again.

A fal job keeps running on fal's side even if ComfyUI is restarted or the node is stopped
while it waits. Every submit is written to output/fal/_requests.jsonl with its request id;
give this node that id and it downloads whatever fal produced into
output/fal/recovered/<request id>/. Nothing is submitted, so nothing is billed.
"""

from __future__ import annotations

import asyncio
import json
import os

from comfy_api.latest import io

from . import client
from .media import extension_for
from .node import CATEGORY_ROOT, LEDGER_NAME, _Reporter


def _ledger_entry(request_id):
    try:
        import folder_paths
        path = os.path.join(folder_paths.get_output_directory(), "fal", LEDGER_NAME)
        with open(path, "r", encoding="utf-8") as handle:
            lines = [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError):
        return None
    for entry in reversed(lines):
        if entry.get("request_id") == request_id and entry.get("response_url"):
            return entry
    return None


def _all_files(value, found):
    if isinstance(value, dict):
        url = value.get("url")
        if isinstance(url, str) and url.startswith(("http://", "https://", "data:")):
            found.append(value)
        for v in value.values():
            if isinstance(v, (dict, list)):
                _all_files(v, found)
    elif isinstance(value, list):
        for v in value:
            _all_files(v, found)
    return found


def _recover(request_id, endpoint_id, node_id):
    import folder_paths

    say = _Reporter(node_id, "Recover")
    request_id = request_id.strip()
    if not request_id:
        raise ValueError("fal Recover: paste a request id (see output/fal/_requests.jsonl).")
    key = client.fal_key()
    entry = _ledger_entry(request_id)
    if entry:
        handle = {"request_id": request_id, "response_url": entry["response_url"],
                  "status_url": entry.get("status_url") or entry["response_url"] + "/status"}
    else:
        parts = [p for p in endpoint_id.strip().split("/") if p]
        if len(parts) < 2:
            raise ValueError("fal Recover: request %s is not in the request log - also give the "
                             "model's endpoint id (e.g. fal-ai/sync-lipsync/v3)." % request_id)
        base = "%s/%s/%s/requests/%s" % (client.QUEUE_URL, parts[0], parts[1], request_id)
        handle = {"request_id": request_id, "response_url": base, "status_url": base + "/status"}

    state = client.status(key, handle, log=say)
    if state.get("status") != "COMPLETED":
        raise RuntimeError("fal Recover: request %s is still %s - try again later."
                           % (request_id, state.get("status")))
    answer = client.result(key, handle, log=say)

    out_root = folder_paths.get_output_directory()
    folder = os.path.join(out_root, "fal", "recovered", request_id)
    os.makedirs(folder, exist_ok=True)
    subfolder = os.path.relpath(folder, out_root).replace("\\", "/")
    paths, shown, video = [], [], None
    for i, file_obj in enumerate(_all_files(answer, [])):
        ext = extension_for(file_obj, ".bin")
        filename = "%s_%02d_%s" % (request_id[:8], i + 1, ext)
        path = os.path.join(folder, filename)
        client.download(file_obj["url"], path, log=say)
        paths.append(path)
        entry_ui = {"filename": filename, "subfolder": subfolder, "type": "output"}
        if ext in (".mp4", ".mov", ".webm") and video is None:
            video = entry_ui
        elif ext in (".png", ".jpg", ".webp", ".gif"):
            shown.append(entry_ui)
    with open(os.path.join(folder, "result.json"), "w", encoding="utf-8") as handle_out:
        json.dump(answer, handle_out, ensure_ascii=False, indent=1)
    say("recovered %d file(s) into %s" % (len(paths), folder))
    ui = ({"images": [video], "animated": (True,)} if video else
          {"images": shown} if shown else None)
    return "\n".join(paths), json.dumps(answer, ensure_ascii=False, indent=1), ui


class ArkFalRecover(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ArkFalRecover",
            display_name="arkennemasis fal · Recover Result (free)",
            category="%s/Tools" % CATEGORY_ROOT,
            description=__doc__,
            inputs=[
                io.String.Input("request_id", default="",
                                tooltip="The fal request id - every run logs it in "
                                        "output/fal/_requests.jsonl."),
                io.String.Input("endpoint_id", default="", optional=True,
                                tooltip="Only needed when the id is not in the request log, "
                                        "e.g. fal-ai/sync-lipsync/v3."),
            ],
            outputs=[io.String.Output("file_paths"), io.String.Output("result_json")],
            hidden=[io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    async def execute(cls, request_id, endpoint_id=""):
        node_id = cls.hidden.unique_id if cls.hidden is not None else None
        paths, answer, ui = await asyncio.to_thread(_recover, request_id, endpoint_id or "", node_id)
        return io.NodeOutput(paths, answer, ui=ui)
