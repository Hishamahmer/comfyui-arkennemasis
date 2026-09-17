"""Read-only HTTPS check against the current private tunnel URL; never prints the URL."""

import argparse
import base64
from contextlib import contextmanager
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import urllib.error
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-dns", action="store_true", help="Use public DNS to test Funnel's internet route from a Tailscale device")
    parser.add_argument("--skip-image", action="store_true", help="Check connection and route protection without requiring a retained generation")
    args = parser.parse_args()
    state = Path(__file__).resolve().parents[2] / "mcp_service/.local"
    endpoint = re.search(r"https://[^\s]+", (state / "connection.txt").read_text()).group()
    parsed = urllib.parse.urlsplit(endpoint)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if args.public_dns:
        query = "https://dns.google/resolve?" + urllib.parse.urlencode({"name": parsed.hostname, "type": "A"})
        with opener.open(query, timeout=20) as response:
            answers = json.load(response).get("Answer", [])
        public_addresses = [answer["data"] for answer in answers
                            if answer["type"] == 1 and ipaddress.ip_address(answer["data"]).is_global]
        if not public_addresses:
            raise RuntimeError("The fixed hostname has no public IPv4 address")

        class InternetConnection(http.client.HTTPSConnection):
            def connect(self):
                for address in public_addresses:
                    sock = None
                    try:
                        sock = socket.create_connection((address, self.port), min(self.timeout, 10))
                        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                        self.sock.settimeout(self.timeout)
                        return
                    except OSError as exc:
                        if sock is not None:
                            sock.close()
                        error = exc
                raise error

        class InternetOpener:
            @contextmanager
            def open(self, request, timeout=30):
                request = urllib.request.Request(request) if isinstance(request, str) else request
                target = urllib.parse.urlsplit(request.full_url)
                if target.scheme != "https" or target.netloc != parsed.netloc:
                    raise ValueError("Public check must stay on its configured HTTPS origin")
                connection = InternetConnection(target.hostname, target.port or 443, timeout=timeout)
                try:
                    connection.request(request.get_method(), target.path or "/", body=request.data,
                                       headers=dict(request.header_items()))
                    response = connection.getresponse()
                    if response.status >= 400:
                        raise urllib.error.HTTPError(request.full_url, response.status, response.reason, response.headers, None)
                    yield response
                finally:
                    connection.close()

        opener = InternetOpener()
        print("Testing through the public internet relay with TLS hostname verification")

    def rpc(method, params):
        request = urllib.request.Request(endpoint,
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                     "MCP-Protocol-Version": "2025-11-25"})
        with opener.open(request, timeout=30) as response:
            result = json.load(response)
        if "error" in result:
            raise RuntimeError("MCP protocol returned an error")
        return result["result"]

    def call(name, arguments=None):
        result = rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise RuntimeError(f"{name} failed: " + str(result["content"]))
        return result

    server = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                "clientInfo": {"name": "Arkennemasis public connection check", "version": "1"}})
    count = len(rpc("tools/list", {})["tools"])
    status = json.loads(call("server_status")["content"][0]["text"])
    assert status["connected"], "Local ComfyUI is offline"
    print("Public HTTPS server:", server["serverInfo"]["name"])
    print("Public tool count:", count)
    print("Public connection reaches local ComfyUI:", status["connected"])

    image_bytes = prompt_id = None
    if not args.skip_image:
        smoke = json.loads((state / "last-smoke.json").read_text())
        prompt_id = smoke["prompt_id"]
        image = call("get_output_image", {"prompt_id": prompt_id})["content"][0]
        data = base64.b64decode(image["data"])
        assert image["type"] == "image" and data.startswith(b"\x89PNG\r\n\x1a\n")
        image_bytes = len(data)
        print("Generated PNG retrieved through HTTPS:", image_bytes, "bytes")

    for path in ("/mcp", "/connect/wrong/mcp", "/view", "/object_info"):
        try:
            with opener.open(origin + path, timeout=30):
                raise AssertionError("Private route was accessible: " + path)
        except urllib.error.HTTPError as exc:
            assert exc.code == 404, f"Unexpected status for {path}: {exc.code}"
        print("Private route rejected:", path)
    record = {"server": server["serverInfo"], "tools": count, "comfyui_connected": True,
              "image_bytes": image_bytes, "prompt_id": prompt_id, "private_routes_rejected": True}
    (state / "public-check.json").write_text(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
