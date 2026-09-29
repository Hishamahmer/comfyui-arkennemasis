"""A local stand-in for fal.ai speaking the protocol fal-client 1.0.3 uses.

  POST /rest/storage/auth/token?storage_type=fal-cdn-v3   -> CDN token
  POST /cdn/files/upload                                   -> {"access_url"}
  POST /rest/storage/upload/initiate?storage_type=gcs      -> {"upload_url", "file_url"}
  PUT  /put/<id>                                           -> store bytes
  GET  /media/<id>/<name>                                  -> stored bytes
  POST /queue/<endpoint...>                                -> handle
  GET  /queue/<owner>/<alias>/requests/<rid>/status        -> IN_QUEUE, IN_PROGRESS, COMPLETED
  GET  /queue/<owner>/<alias>/requests/<rid>               -> result
  PUT  /queue/<owner>/<alias>/requests/<rid>/cancel        -> cancelled

Behaviour switches per endpoint live in MockFal.modes: submit_429_once, drop_after_submit,
completed_error, result_422, never_complete; and "*": cdn_down.
"""
import datetime
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

KEY = "test-id:test-secret"


class MockFal:
    def __init__(self, result_factory):
        self.files, self.jobs, self.modes, self.submits = {}, {}, {}, {}
        self.result_factory = result_factory
        self.lock = threading.Lock()
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def send(self, code, obj=None, raw=None, ctype="application/json", headers=None):
                data = raw if raw is not None else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):
                mock.handle(self, "POST")

            def do_GET(self):
                mock.handle(self, "GET")

            def do_PUT(self):
                mock.handle(self, "PUT")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def add_file(self, data, ctype, name):
        fid = uuid.uuid4().hex[:12]
        self.files[fid] = (data, ctype, name)
        return "%s/media/%s/%s" % (self.base, fid, name)

    def handle(self, h, method):
        path = urlparse(h.path).path
        body = h.body() if method in ("POST", "PUT") else b""
        auth = h.headers.get("Authorization", "")

        if path == "/rest/storage/auth/token":
            if auth != "Key " + KEY:
                return h.send(401, {"detail": "bad key"})
            if "cdn_down" in self.modes.get("*", set()):
                return h.send(503, {"detail": "cdn token down"}, headers={"x-fal-request-id": "x"})
            exp = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=1)).isoformat()
            return h.send(200, {"token": "cdntok", "token_type": "Bearer",
                                "base_url": self.base + "/cdn", "expires_at": exp})
        if path == "/cdn/files/upload":
            if auth != "Bearer cdntok":
                return h.send(401, {"detail": "bad cdn token"})
            return h.send(200, {"access_url": self.add_file(body, h.headers.get("Content-Type"),
                                                            h.headers.get("X-Fal-File-Name") or "file")})
        if path == "/rest/storage/upload/initiate":
            if auth != "Key " + KEY:
                return h.send(401, {"detail": "bad key"})
            req = json.loads(body)
            fid = uuid.uuid4().hex[:12]
            self.files[fid] = (b"", req["content_type"], req["file_name"])
            return h.send(200, {"upload_url": "%s/put/%s" % (self.base, fid),
                                "file_url": "%s/media/%s/%s" % (self.base, fid, req["file_name"])})
        if path.startswith("/put/") and method == "PUT":
            fid = path.split("/")[2]
            _, ctype, name = self.files[fid]
            self.files[fid] = (body, h.headers.get("Content-Type") or ctype, name)
            return h.send(200, {})
        if path.startswith("/media/"):
            data, ctype, _ = self.files[path.split("/")[2]]
            return h.send(200, raw=data, ctype=ctype or "application/octet-stream")

        if not path.startswith("/queue/"):
            return h.send(404, {"detail": "unknown route " + path})
        parts = path[len("/queue/"):].split("/")
        if "requests" in parts:
            i = parts.index("requests")
            job = self.jobs.get(parts[i + 1])
            if not job:
                return h.send(404, {"detail": "no such request"})
            if auth != "Key " + KEY:
                return h.send(401, {"detail": "bad key"})
            tail = parts[i + 2:]
            modes = self.modes.get(job["endpoint"], set())
            if tail == ["status"]:
                job["polls"] += 1
                if job.get("cancelled"):
                    return h.send(200, {"status": "COMPLETED", "logs": [], "error": "cancelled"})
                if "never_complete" in modes or job["polls"] == 1:
                    return h.send(200, {"status": "IN_QUEUE", "queue_position": 2})
                if job["polls"] == 2:
                    return h.send(200, {"status": "IN_PROGRESS", "logs": [{"message": "mock working"}]})
                st = {"status": "COMPLETED", "logs": [{"message": "mock working"}, {"message": "done"}]}
                if "completed_error" in modes:
                    st.update(error="mock model crashed", error_type="internal")
                return h.send(200, st)
            if tail == ["cancel"] and method == "PUT":
                job["cancelled"] = True
                return h.send(202, {"status": "CANCELLATION_REQUESTED"})
            if not tail and method == "GET":
                if "result_422" in modes:
                    return h.send(422, {"detail": [{"msg": "mock validation failed"}]})
                return h.send(200, job["result"])
            return h.send(404, {"detail": "bad route"})

        endpoint = "/".join(parts)
        if auth != "Key " + KEY:
            return h.send(401, {"detail": "No user found for Key ID and Secret"})
        modes = self.modes.get(endpoint, set())
        with self.lock:
            self.submits[endpoint] = self.submits.get(endpoint, 0) + 1
        if "submit_429_once" in modes:
            modes.discard("submit_429_once")
            return h.send(429, {"detail": "slow down"}, headers={"retry-after": "1"})
        if "drop_after_submit" in modes:
            self.jobs[uuid.uuid4().hex] = {"endpoint": endpoint, "payload": json.loads(body),
                                           "polls": 0, "result": {}}
            h.close_connection = True
            h.connection.shutdown(2)
            return
        payload = json.loads(body)
        rid = uuid.uuid4().hex
        base = "%s/queue/%s/requests/%s" % (self.base, "/".join(parts[:2]), rid)
        self.jobs[rid] = {"endpoint": endpoint, "payload": payload, "polls": 0,
                          "result": self.result_factory(self, endpoint, payload)}
        return h.send(200, {"request_id": rid, "response_url": base, "status_url": base + "/status",
                            "cancel_url": base + "/cancel", "queue_position": 0})

    def stop(self):
        self.server.shutdown()
