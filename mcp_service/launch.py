"""Bootstrap the gateway using its own optional dependency directory."""

import runpy
import os
import site
import sys
from pathlib import Path

service_dir = Path(__file__).resolve().parent
runtime = service_dir / ".runtime"
versioned_runtime = runtime / f"py{sys.version_info.major}{sys.version_info.minor}"
if versioned_runtime.is_dir():
    runtime = versioned_runtime
if runtime.is_dir():
    site.addsitedir(str(runtime))
sys.path.insert(0, str(service_dir.parent))

if __name__ == "__main__":
    if sys.stdout is None or sys.stderr is None:
        log_dir = service_dir / ".local"
        log_dir.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(log_dir / "web.log", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        log = os.fdopen(descriptor, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log
    runpy.run_module("mcp_service", run_name="__main__")
