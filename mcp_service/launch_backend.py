"""Keep the BAT's backend process alive across explicit ComfyUI restarts."""

from collections import deque
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid


RESTART_EXIT_CODE = 75
SUPERVISOR_ENV = "ARK_MCP_SUPERVISOR_PID"


def pending_restart(path, pid, consumed):
    """A reserved exit alone must never turn a crash into a restart loop."""
    try:
        with path.open("rb") as handle:
            raw = handle.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            return None
        records = json.loads(raw)
        if not isinstance(records, list):
            return None
        for record in reversed(records):
            if not isinstance(record, dict):
                continue
            request_id = record.get("request_id")
            requested_at = record.get("requested_at")
            if (record.get("state") == "restarting" and record.get("accepted") is True
                    and type(record.get("pid")) is int and record["pid"] == pid
                    and isinstance(request_id, str) and str(uuid.UUID(request_id)) == request_id
                    and request_id not in consumed and type(requested_at) in (int, float)
                    and 0 <= time.time() - requested_at <= 180):
                return request_id
    except (OSError, ValueError, TypeError):
        pass
    return None


def main():
    service = Path(__file__).resolve().parent
    worker = service / "backend_worker.py"
    command = [sys.executable, "-s", str(worker), *sys.argv[1:]]
    env = {**os.environ, SUPERVISOR_ENV: str(os.getpid())}
    consumed = set()
    restarts = deque()
    while True:
        process = subprocess.Popen(command, env=env, shell=False)
        try:
            code = process.wait()
        except KeyboardInterrupt:
            # The console also delivers Ctrl+C to the backend. Give it time to
            # close before terminating only the child owned by this supervisor.
            try:
                return process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                return process.wait(timeout=5)
        if code != RESTART_EXIT_CODE:
            return code
        request_id = pending_restart(service / ".local" / "backend-restarts.json", process.pid, consumed)
        if request_id is None:
            print("[Arkennemasis MCP] Backend stopped without a valid restart request. Check the local log.", flush=True)
            return code
        now = time.monotonic()
        while restarts and now - restarts[0] >= 300:
            restarts.popleft()
        if len(restarts) >= 5:
            print("[Arkennemasis MCP] Restart limit reached. Check the local log and relaunch the BAT.", flush=True)
            return code
        consumed.add(request_id)
        restarts.append(now)
        print("[Arkennemasis MCP] Restarting ComfyUI; the AI connection stays open.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
