"""Run this installation's ComfyUI with owner-controlled, validated overrides."""

from pathlib import Path
import runpy
import sys


def main():
    service = Path(__file__).resolve().parent
    comfy_root = service.parents[2]
    sys.path.insert(0, str(service.parent))
    from mcp_service.launch_config import apply_overrides

    base_args = list(sys.argv[1:])
    # Preserve the launcher's original values when a saved override is later removed.
    sys._arkennemasis_base_comfy_args = base_args
    sys.argv = [str(comfy_root / "main.py"), *apply_overrides(base_args, service / ".local", comfy_root)]
    sys.path.insert(0, str(comfy_root))
    runpy.run_path(sys.argv[0], run_name="__main__")


if __name__ == "__main__":
    main()
