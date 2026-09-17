"""Explicit integration check: --run creates one tiny image through the real MCP endpoint."""

import argparse
import base64
import json
from pathlib import Path
import struct
import time
import urllib.request
import uuid

PACK = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true", help="Create a new two-node test workflow and run it once")
    args = parser.parse_args()
    config = json.loads((PACK / "mcp_service/.local/config.json").read_text())
    if config["auth_mode"] not in {"local_token", "connection_link"}:
        raise SystemExit("This check uses local-token mode. Test OAuth through the actual web client.")
    endpoint = f"http://127.0.0.1:{config['port']}/mcp"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def rpc(method, params):
        request = urllib.request.Request(endpoint,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
            headers={"Authorization": "Bearer " + config["local_token"], "Content-Type": "application/json",
                     "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"})
        with opener.open(request, timeout=45) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result["result"]

    def call(name, arguments=None, raw=False):
        result = rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise RuntimeError(result["content"])
        return result if raw else result.get("structuredContent", json.loads(result["content"][0]["text"]))

    initialized = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                      "clientInfo": {"name": "Arkennemasis local integration check", "version": "1"}})
    print(initialized["serverInfo"])
    print("Tools:", len(rpc("tools/list", {})["tools"]))
    status = call("server_status")
    print("ComfyUI connected:", status["connected"])
    print("Shared canvases:", len(call("list_canvases")["sessions"]))
    if not args.run:
        return
    if status["queue_running"] or status["queue_pending"]:
        raise SystemExit("ComfyUI is busy. Run this check when its queue is empty.")
    for node_type in ("EmptyImage", "SaveImage"):
        call("get_node_schema", {"class_type": node_type})
    run_id = str(uuid.uuid4())
    prefix = "arkennemasis_mcp_test/connection_" + run_id[:8]
    prompt = {"1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 4487082}},
              "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0], "filename_prefix": prefix}}}
    workflow = {"id": str(uuid.uuid4()), "revision": 0, "last_node_id": 2, "last_link_id": 1, "version": 0.4,
                "nodes": [
                    {"id": 1, "type": "EmptyImage", "pos": [80, 100], "size": [300, 150], "flags": {}, "order": 0, "mode": 0,
                     "inputs": [], "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [1], "slot_index": 0}],
                     "properties": {"Node name for S&R": "EmptyImage"}, "widgets_values": [64, 64, 1, 4487082]},
                    {"id": 2, "type": "SaveImage", "pos": [460, 100], "size": [340, 300], "flags": {}, "order": 1, "mode": 0,
                     "inputs": [{"name": "images", "type": "IMAGE", "link": 1}],
                     "outputs": [{"name": "images", "type": "IMAGE", "links": None}],
                     "properties": {"Node name for S&R": "SaveImage"}, "widgets_values": [prefix]}],
                "links": [[1, 1, 0, 2, 0, "IMAGE"]], "groups": [], "config": {}, "extra": {}}
    filename = "Arkennemasis MCP/Connection Test " + run_id[:8] + ".json"
    saved = call("save_workflow", {"name": filename, "workflow": workflow})
    edited = call("patch_workflow", {"name": filename, "expected_revision": saved["revision"],
                                     "operations": [{"op": "add", "path": "/nodes/0/title", "value": "MCP connection test"}]})
    assert edited["workflow"]["nodes"][0]["title"] == "MCP connection test"
    restored = call("restore_workflow", {"name": filename, "backup_id": edited["backup_id"], "expected_revision": edited["revision"]})
    assert restored["revision"] == saved["revision"]
    validation = call("validate_prompt", {"prompt": prompt})
    assert validation["valid"], validation
    arguments = {"prompt": prompt, "workflow": workflow, "request_id": run_id}
    queued = call("queue_prompt", arguments)
    replay = call("queue_prompt", arguments)
    assert queued["prompt_id"] == replay["prompt_id"] == run_id
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        job = call("get_job", {"prompt_id": run_id})
        if job["status"] in {"completed", "failed"}:
            break
        time.sleep(0.5)
    assert job["status"] == "completed", job
    outputs = call("list_outputs", {"prompt_id": run_id})
    assert len(outputs["assets"]) == 1, outputs
    image = call("get_output_image", {"prompt_id": run_id}, raw=True)["content"][0]
    data = base64.b64decode(image["data"])
    assert image["type"] == "image" and data.startswith(b"\x89PNG\r\n\x1a\n")
    assert struct.unpack(">II", data[16:24]) == (64, 64)
    record = {"workflow": filename, "prompt_id": run_id, "status": job["status"],
              "output": outputs["assets"][0], "image_bytes": len(data),
              "checks": ["MCP discovery", "workflow create/patch/restore", "core validation",
                         "real execution", "duplicate request reuse", "inline PNG bytes"]}
    (PACK / "mcp_service/.local/last-smoke.json").write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
