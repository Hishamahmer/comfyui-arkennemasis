"""Installation, diagnostics and explicit startup for Arkennemasis MCP."""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .settings import DEFAULT_CONFIG, enable_connection_link, initialize, load_settings


def main():
    parser = argparse.ArgumentParser(prog="Arkennemasis MCP")
    parser.add_argument("command", choices=["init", "serve", "web", "configure-web", "stop-web", "doctor", "test"])
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--transport", choices=["http", "stdio"], default="http")
    parser.add_argument("--public-url", help="Fixed HTTPS origin for an externally managed tunnel")
    args = parser.parse_args()
    if args.command == "configure-web":
        if not args.public_url:
            parser.error("configure-web requires --public-url with your fixed HTTPS origin")
        try:
            settings = enable_connection_link(args.config, public_url=args.public_url)
        except (ValueError, TypeError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        print(f"Fixed HTTPS origin saved: {settings.public_url}")
        print("Run web to start the gateway. The external tunnel must forward to its loopback port.")
        return 0
    if args.command == "init":
        try:
            initialize(args.config)
        except FileExistsError:
            print(f"Existing configuration preserved: {args.config}")
        else:
            print(f"Created private configuration: {args.config}")
            print("Start ComfyUI to load the canvas bridge. Run serve to start MCP.")
        return 0
    if args.command == "test":
        import unittest
        suite = unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parents[1] / "tests" / "mcp"))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1
    if args.command == "web":
        from .tunnel import run_web
        try:
            return run_web(args.config)
        except (ValueError, RuntimeError, OSError) as exc:
            print(str(exc), file=sys.stderr)
            return 1
    try:
        settings = load_settings(args.config, args.transport)
    except (ValueError, TypeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.command == "stop-web":
        stop_file = Path(settings.state_dir) / "web.stop"
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.touch()
        print("Stop requested. The web launcher will close its tunnel and gateway; ComfyUI stays running.")
        return 0
    if args.command == "doctor":
        report = {"config": str(args.config), "endpoint": settings.display_endpoint,
                  "authentication": settings.auth_mode, "workflow_folder": settings.workflow_root,
                  "comfyui": "offline", "gateway": "offline", "bridge": "unavailable", "canvas_sessions": 0,
                  "web_configured": settings.auth_mode == "oauth" and bool(settings.public_url)}
        if settings.auth_mode == "connection_link":
            report["connection_file"] = str(Path(settings.state_dir) / "connection.txt")
            report["web_configured"] = Path(report["connection_file"]).is_file()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        gateway_host = f"[{settings.host}]" if ":" in settings.host else settings.host
        try:
            with opener.open(f"http://{gateway_host}:{settings.port}/health", timeout=3) as response:
                if json.load(response).get("service") == "Arkennemasis MCP":
                    report["gateway"] = "online"
        except (urllib.error.URLError, OSError, ValueError):
            pass
        try:
            with opener.open(settings.comfy_url + "/system_stats", timeout=3):
                report["comfyui"] = "online"
            request = urllib.request.Request(settings.comfy_url + "/arkennemasis/mcp/sessions",
                                             headers={"X-Ark-Bridge-Token": settings.bridge_token})
            with opener.open(request, timeout=3) as response:
                result = json.load(response)
                report["bridge"] = "online"
                report["canvas_sessions"] = len(result.get("sessions", []))
        except (urllib.error.URLError, OSError, ValueError):
            pass
        if settings.auth_mode == "connection_link":
            report["web_address"] = "fixed" if settings.public_url else "temporary"
            report["web_note"] = "Use the private URL in connection.txt with No Auth. The gateway and tunnel must both be running."
        elif settings.auth_mode == "oauth":
            report["web_note"] = "Public reachability and OAuth sign-in still require testing from the web client."
        else:
            report["web_note"] = "Local access only. Run web to create a private HTTPS connection."
        print(json.dumps(report, indent=2))
        return 0 if report["comfyui"] == "online" and report["bridge"] == "online" else 1
    try:
        from .server import serve
        serve(settings, args.transport)
    except ModuleNotFoundError as exc:
        print(f"Missing optional MCP dependency: {exc.name}. Install mcp_service/requirements.txt into mcp_service/.runtime.",
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
