"""Bounded local diagnostics and owner-requested public connectivity checks."""

import http.client
import ipaddress
import json
from pathlib import Path
import math
import re
import socket
import ssl
import time
import urllib.parse
import urllib.request

from .maintenance import _redact


def read_json(path, limit=65536):
    try:
        with Path(path).open(encoding="utf-8") as handle:
            data = json.loads(handle.read(limit))
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def connection_state(settings):
    state = read_json(Path(settings.state_dir) / "session-status.json")
    updated = state.get("updated_at", 0)
    age = time.time() - updated if type(updated) in (int, float) and math.isfinite(updated) else float("inf")
    public = read_json(Path(settings.state_dir) / "public-probe.json")
    return {"gateway": "online", "session_state": state.get("state", "unknown") if 0 <= age <= 30 else "unknown",
            "session_message": state.get("message", "No companion heartbeat"),
            "heartbeat_age_seconds": round(age, 1) if state and math.isfinite(age) else None,
            "layers": state.get("layers", {}), "layer_success_at": state.get("layer_success_at", {}),
            "public_check": public, "enabled_scopes": settings.enabled_scopes,
            "development_packs": settings.allowed_node_packs,
            "note": "A configured tunnel is not proof of public reachability. Public checks are separate, explicit operations."}


def redact(text, settings):
    for secret in (settings.local_token, settings.bridge_token, settings.connection_token):
        if secret:
            text = text.replace(secret, "[redacted]")
    text = _redact(text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:bearer\s+)?[^\s,;]+", r"\1[redacted]", text)
    text = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*)[^\s,;]+", r"\1[redacted]", text)
    return re.sub(r"https?://[^\s\"<>]+", "[url omitted]", text)


def read_log(settings, source="comfyui", lines=100):
    if type(lines) is not int or not 1 <= lines <= 500:
        raise ValueError("Read between 1 and 500 log lines.")
    paths = {"comfyui": Path(settings.comfy_root) / "user/comfyui.log",
             "connection": Path(settings.state_dir) / "connection-events.jsonl",
             "gateway": Path(settings.state_dir) / "web.log",
             "launcher": Path(settings.state_dir) / "companion.log"}
    if source not in paths:
        raise ValueError("Choose comfyui, connection, gateway or launcher logs.")
    path = paths[source]
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 128 * 1024))
            text = handle.read(128 * 1024).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return {"source": source, "available": False, "lines": []}
    return {"source": source, "available": True, "lines": redact(text, settings).splitlines()[-lines:]}


def public_probe(settings, samples=3):
    """Test a configured origin over verified TLS; never expose the private MCP URL."""
    if type(samples) is not int or not 1 <= samples <= 6:
        raise ValueError("Use 1 to 6 public connectivity samples.")
    target = urllib.parse.urlsplit(settings.public_url)
    if target.scheme != "https" or not target.hostname:
        raise ValueError("Configure a public HTTPS origin first.")
    if target.hostname.endswith(".ts.net"):
        query = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": target.hostname, "type": "A"})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(query, timeout=10) as response:
            answers = json.load(response).get("Answer", [])
        addresses = [item["data"] for item in answers if item["type"] == 1 and ipaddress.ip_address(item["data"]).is_global]
    else:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(target.hostname, target.port or 443, type=socket.SOCK_STREAM)
                            if ipaddress.ip_address(item[4][0]).is_global})
    if not addresses:
        raise ValueError("The public hostname has no global address.")
    context = ssl.create_default_context()
    results = []
    for index in range(samples):
        started = time.monotonic()
        stage = "tcp"
        raw = secured = None
        try:
            raw = socket.create_connection((addresses[index % len(addresses)], target.port or 443), timeout=8)
            stage = "tls"
            secured = context.wrap_socket(raw, server_hostname=target.hostname)
            stage = "http"
            connection = http.client.HTTPConnection(target.hostname, target.port or 443, timeout=8)
            connection.sock = secured
            connection.request("GET", "/health", headers={"Host": target.netloc, "Connection": "close"})
            response = connection.getresponse()
            payload = response.read(4096)
            healthy = response.status == 200 and json.loads(payload).get("service") == "Arkennemasis MCP"
            results.append({"ok": healthy, "stage": "complete", "http_status": response.status,
                            "seconds": round(time.monotonic() - started, 2)})
        except (OSError, ValueError, http.client.HTTPException) as exc:
            results.append({"ok": False, "stage": stage, "error": type(exc).__name__,
                            "seconds": round(time.monotonic() - started, 2)})
        finally:
            if secured is not None:
                secured.close()
            elif raw is not None:
                raw.close()
    report = {"checked_at": time.time(), "samples": results, "passed": sum(item["ok"] for item in results),
              "total": samples, "source": "local host via public DNS/relay with verified TLS",
              "note": "This checks the public path from this PC; AI-provider reachability is a separate observation."}
    path = Path(settings.state_dir) / "public-probe.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report), encoding="utf-8")
    temporary.replace(path)
    return report
