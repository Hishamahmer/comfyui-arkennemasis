"""Fixed ComfyUI worker started by the installation's stable BAT supervisor."""

import os
from pathlib import Path
import runpy
import sys


def main():
    service = Path(__file__).resolve().parent
    comfy_root = service.parents[2]
    sys.path.insert(0, str(service.parent))
    from mcp_service.launch_backend import SUPERVISOR_ENV
    from mcp_service.launch_config import apply_overrides

    parent = os.environ.pop(SUPERVISOR_ENV, "")
    supervised = parent.isdecimal() and int(parent) == os.getppid()
    sys._arkennemasis_supervised = supervised
    sys._arkennemasis_supervisor_pid = int(parent) if supervised else None
    base_args = list(sys.argv[1:])
    sys._arkennemasis_base_comfy_args = base_args
    sys.argv = [str(comfy_root / "main.py"), *apply_overrides(base_args, service / ".local", comfy_root)]
    sys.path.insert(0, str(comfy_root))
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
