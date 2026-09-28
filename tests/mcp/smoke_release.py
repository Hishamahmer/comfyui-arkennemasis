"""Explicit Windows integration run; owns and cleans up its temporary BAT session."""

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

PACK = Path(__file__).resolve().parents[2]
PORTABLE = PACK.parents[2]
STATE = PACK / "mcp_service/.local"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url, body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    with OPENER.open(request, timeout=8) as response:
        return json.load(response)


def wait_ready(process, config, timeout=180, previous_pid=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The owned launcher exited before readiness; inspect release-live-console.log.")
        try:
            backend = fetch(config["comfy_url"] + "/arkennemasis/mcp/runtime", headers={"X-Ark-Bridge-Token": config["bridge_token"]})
            gateway = fetch(f"http://127.0.0.1:{config['port']}/health")
            if gateway.get("status") == "ready" and backend["pid"] != previous_pid:
                return backend
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise RuntimeError("Timed out waiting for the test session; inspect release-live-console.log.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start-portable", action="store_true", help="Start the portable BAT, generate one tiny image, restart, then stop the owned session")
    mode.add_argument("--use-running-comfy", action="store_true", help="Use an existing idle backend; start and stop only a temporary gateway, without restarting ComfyUI")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("This integration launcher currently supports Windows only.")
    config = json.loads((STATE / "config.json").read_text())
    if config["auth_mode"] not in {"local_token", "connection_link"}:
        raise RuntimeError("This local integration check requires local/private-link mode; test provider login separately.")
    for port in ((8188, config["port"]) if args.start_portable else (config["port"],)):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError("An existing service is running. This test never takes over a live session.")
    endpoint = f"http://127.0.0.1:{config['port']}/mcp"
    def rpc(method, params):
        return fetch(endpoint, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                     {"Authorization": "Bearer " + config["local_token"], "Accept": "application/json, text/event-stream",
                      "MCP-Protocol-Version": "2025-11-25"})["result"]
    def call(name, arguments=None):
        response = rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if response.get("isError"):
            raise RuntimeError(f"{name} returned a tool error; inspect local logs.")
        return response.get("structuredContent") or json.loads(response["content"][0]["text"])

    with (STATE / "release-live-console.log").open("w", encoding="utf-8") as log:
        # The exact process handle is retained; cleanup targets only this created tree.
        command = (["cmd.exe", "/d", "/c", str(PORTABLE / "run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat")] if args.start_portable else
                   [sys.executable, str(PACK / "mcp_service/launch.py"), "serve"])
        process = subprocess.Popen(command, cwd=PORTABLE,
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
        report = {"started_at": time.time(), "launcher_pid": process.pid}
        try:
            print("Starting owned test session...", flush=True)
            before = wait_ready(process, config)
            report["version"] = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                                     "clientInfo": {"name": "release-check", "version": "1"}})["serverInfo"]["version"]
            report["tool_count"] = len(rpc("tools/list", {})["tools"])
            call("list_node_files", {"pack": "comfyui-arkennemasis", "limit": 5})
            call("get_launch_settings")
            report["source_and_maintenance_access"] = True
            print("MCP ready; checking real execution and retry identity...", flush=True)
            subprocess.run([sys.executable, str(PACK / "tests/mcp/smoke_local.py"), "--run"], cwd=PACK, check=True, timeout=100)
            if args.use_running_comfy:
                report.update(restart_state="not_tested_existing_session", finished_at=time.time())
                (STATE / "release-live.json").write_text(json.dumps(report, indent=2))
                print(json.dumps(report), flush=True)
                return
            request_id = str(uuid.uuid4())
            print("Requesting controlled restart...", flush=True)
            call("restart_comfyui", {"request_id": request_id})
            after = wait_ready(process, config, previous_pid=before["pid"])
            deadline = time.monotonic() + 25
            result = {}
            while time.monotonic() < deadline:
                result = call("get_restart_status", {"request_id": request_id})
                if result.get("state") in {"ready", "failed"}:
                    break
                time.sleep(1)
            if result.get("state") != "ready":
                raise RuntimeError("Controlled restart did not report ready.")
            report["backend_pid_changed"] = before["pid"] != after["pid"]
            report["launcher_still_running"] = process.poll() is None
            report["restart_state"] = result["state"]
            print("Restart ready; checking owner-only OAuth setup...", flush=True)
            oauth = fetch(config["comfy_url"] + "/arkennemasis/mcp/oauth/status", {},
                          {"Origin": config["comfy_url"], "X-Ark-Canvas": "1"})
            report["oauth_setup_available"] = isinstance(oauth, dict)
            report["finished_at"] = time.time()
            (STATE / "release-live.json").write_text(json.dumps(report, indent=2))
            print(json.dumps(report), flush=True)
        finally:
            if process.poll() is None:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=20)
                process.wait(timeout=20)
            print("Owned test session stopped.", flush=True)


if __name__ == "__main__":
    main()
