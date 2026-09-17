"""Web gateway lifecycle; owns only the children it starts."""

import json
import os
from pathlib import Path
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from . import settings as configuration


QUICK_URL = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com(?=[\s/|\"']|$)", re.IGNORECASE)


def find_cloudflared():
    executable = shutil.which("cloudflared")
    if executable:
        return Path(executable)
    bundled = configuration.SERVICE_DIR / ".runtime" / "bin" / ("cloudflared.exe" if os.name == "nt" else "cloudflared")
    if bundled.is_file():
        return bundled
    raise ValueError("cloudflared is missing. Install the official Cloudflare executable in mcp_service/.runtime/bin or add it to PATH.")


def port_in_use(host, port):
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def start_child(arguments, *, stderr=subprocess.DEVNULL):
    return subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=stderr, text=True, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            shell=False, cwd=configuration.PACK_DIR)


def stop_child(process):
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def check_children(gateway, tunnel=None):
    for name, process in (("Local MCP gateway", gateway), ("HTTPS tunnel", tunnel)):
        if process is not None:
            code = process.poll()
            if code is not None:
                raise RuntimeError(f"{name} stopped (exit {code}). Run the web command again after checking its installation and network connection.")


def wait_for_gateway(gateway, host, port, timeout=25):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        check_children(gateway)
        try:
            with opener.open(f"http://{host}:{port}/health", timeout=1) as response:
                if json.load(response).get("service") == "Arkennemasis MCP":
                    check_children(gateway)
                    return
        except (OSError, urllib.error.URLError, ValueError):
            pass
        time.sleep(0.2)
    raise RuntimeError("The local MCP gateway did not become ready. Run doctor and check the optional MCP dependencies.")


def read_tunnel_output(stream, events):
    """Drain child output without printing request URLs or keeping raw logs."""
    for line in stream:
        match = QUICK_URL.search(line)
        if match:
            events.put(("url", match.group(0)))
        if "Registered tunnel connection" in line:
            events.put(("connected", None))


def wait_for_public_url(gateway, tunnel, events, timeout=90):
    deadline = time.monotonic() + timeout
    public_url = None
    connected = False
    while time.monotonic() < deadline:
        check_children(gateway, tunnel)
        try:
            kind, value = events.get(timeout=0.25)
        except queue.Empty:
            continue
        if kind == "url":
            public_url = value
        elif kind == "connected":
            connected = True
        if public_url and connected:
            return public_url
    raise RuntimeError("The HTTPS tunnel did not become ready. Check internet access and whether an existing .cloudflared configuration prevents Quick Tunnels.")


def write_connection_file(path, public_url, endpoint):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection_url = public_url.rstrip("/") + urlsplit(endpoint).path
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(connection_url + "\n")
    return path


def monitor_children(gateway, tunnel, stop_file):
    while not stop_file.exists():
        check_children(gateway, tunnel)
        time.sleep(0.5)


def run_web(config_path):
    config_path = Path(config_path).resolve()
    current = configuration.load_settings(config_path, transport="stdio")
    if current.host != "127.0.0.1":
        raise ValueError("The web connection requires the local gateway host 127.0.0.1.")
    if port_in_use(current.host, current.port):
        raise ValueError(f"Gateway port {current.port} is already in use. Stop its existing serve/web process before running web.")
    fixed_url = current.auth_mode == "connection_link" and bool(current.public_url)
    if fixed_url:
        configured = current.validate()
    else:
        cloudflared = find_cloudflared()
        configured = configuration.enable_connection_link(config_path)
    connection_file = Path(configured.state_dir) / "connection.txt"
    stop_file = Path(configured.state_dir) / "web.stop"
    stop_file.unlink(missing_ok=True)
    gateway = tunnel = None
    try:
        gateway = start_child([sys.executable, str(configuration.SERVICE_DIR / "launch.py"), "serve", "--config", str(config_path)])
        wait_for_gateway(gateway, configured.host, configured.port)
        if fixed_url:
            print("Local MCP gateway ready. Using the configured fixed HTTPS connection.", flush=True)
            public_url = configured.public_url
        else:
            print("Local MCP gateway ready. Starting a temporary HTTPS connection.", flush=True)
            origin = f"http://127.0.0.1:{configured.port}"
            tunnel = start_child([str(cloudflared), "tunnel", "--url", origin,
                                  "--http-host-header", f"127.0.0.1:{configured.port}", "--no-autoupdate"],
                                 stderr=subprocess.PIPE)
            events = queue.Queue()
            reader = threading.Thread(target=read_tunnel_output, args=(tunnel.stderr, events), daemon=True)
            reader.start()
            public_url = wait_for_public_url(gateway, tunnel, events)
        write_connection_file(connection_file, public_url, configured.endpoint)
        print(f"Private connection URL saved to: {connection_file}", flush=True)
        print("Choose No Auth in the AI connector and paste the URL from that file. Keep the URL private.", flush=True)
        print("Leave this process running. Ctrl+C or the stop-web command stops the local gateway and any tunnel started here.", flush=True)
        monitor_children(gateway, tunnel, stop_file)
        return 0
    except KeyboardInterrupt:
        print("Stopping the web connection.", flush=True)
        return 0
    finally:
        try:
            stop_child(tunnel)
        finally:
            stop_child(gateway)
