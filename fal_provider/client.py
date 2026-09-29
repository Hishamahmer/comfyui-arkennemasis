"""Talking to fal.ai: the key, file uploads, the request queue and downloads.

Standard library only (urllib), so the fal nodes add no dependency to ComfyUI's Python.

The protocol is fal's own, copied from their official Python client (fal-client 1.0.3):

  * **Queue.** ``POST https://queue.fal.run/<endpoint>`` with ``Authorization: Key <FAL_KEY>``
    answers ``request_id``, ``status_url``, ``response_url`` and ``cancel_url``. Poll
    ``GET status_url`` until ``COMPLETED``, then ``GET response_url`` for the result.
    ``PUT cancel_url`` cancels. The returned URLs are used as given, never rebuilt - an
    endpoint with a sub-path (``fal-ai/sync-lipsync/v3``) is served from its base app id.
  * **Uploads.** ``POST https://rest.fal.ai/storage/auth/token?storage_type=fal-cdn-v3``
    buys a short-lived CDN token; ``POST https://v3.fal.media/files/upload`` with that
    token stores the bytes and answers ``access_url``. If that fails, the fallback is
    ``POST https://rest.fal.ai/storage/upload/initiate?storage_type=gcs`` followed by a
    ``PUT`` of the bytes to the signed ``upload_url``; the file is then at ``file_url``.

Money rules, because every successful submit is billed:

  * The **submit** is retried only when fal certainly did not accept it: the connection
    was never made, a 429, or a gateway error with no ``x-fal-request-id`` (fal's own
    client uses the same ingress test). A timeout or reset *after* sending is not
    retried - the job may already be running, and a second submit would bill twice.
  * Everything after the submit (status, result, downloads) is retried hard, because the
    work is already paid for and a network blip must not throw it away.
"""

from __future__ import annotations

import json
import os
import random
import socket
import ssl
import time
import urllib.error
import urllib.request

QUEUE_URL = "https://queue.fal.run"
REST_URL = "https://rest.fal.ai"
CDN_URL = "https://v3.fal.media"
USER_AGENT = "comfyui-arkennemasis-fal/1.0"

KEY_NAMES = ("FAL_KEY",)

POLL_SECONDS = 2.0
STATUS_TIMEOUT = 60
SUBMIT_TIMEOUT = 120
RESULT_TIMEOUT = 120
UPLOAD_TIMEOUT = 600
DOWNLOAD_TIMEOUT = 600


class FalError(RuntimeError):
    """A fal call failed. ``status`` is the HTTP status when there was one."""

    def __init__(self, message, status=None, body="", headers=None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.headers = headers or {}


class FalKeyMissing(FalError):
    pass


# ----------------------------------------------------------------------------------------
# The key - read ONLY from the .env file of this ComfyUI install
# ----------------------------------------------------------------------------------------

def _env_files():
    """The .env files this install keeps its keys in, in the order they are read.

    The portable root (the folder holding ``run_nvidia_gpu.bat``, which is ComfyUI's
    working directory) first, then the ``ComfyUI`` folder itself. Deliberately nothing
    else: not the OS environment, not a node field, not a file inside this pack.
    """
    try:
        import folder_paths
        comfy_dir = os.path.abspath(folder_paths.base_path)
    except Exception:                                   # outside ComfyUI (tests, tools)
        here = os.path.dirname(os.path.abspath(__file__))
        comfy_dir = os.path.abspath(os.path.join(here, "..", "..", ".."))
    return [os.path.join(os.path.dirname(comfy_dir), ".env"),
            os.path.join(comfy_dir, ".env")]


def _read_env_file(path):
    values = {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            for raw in handle:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                name, _, value = line.partition("=")
                values[name.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    return values


def fal_key():
    """The fal API key from the install's .env, or FalKeyMissing naming where to put it."""
    files = _env_files()
    for path in files:
        values = _read_env_file(path)
        for name in KEY_NAMES:
            key = (values.get(name) or "").strip()
            if key:
                return key
    raise FalKeyMissing(
        "fal: no FAL_KEY found. Add a line  FAL_KEY=<your key>  to the .env file at %s "
        "(the folder with run_nvidia_gpu.bat). Keys come from https://fal.ai/dashboard/keys. "
        "Nothing was sent to fal and nothing was billed." % files[0])


# ----------------------------------------------------------------------------------------
# One HTTP call
# ----------------------------------------------------------------------------------------

_SSL = ssl.create_default_context()


def _request(method, url, *, headers=None, body=None, timeout=60):
    """One HTTP exchange. Returns (status, headers, body_bytes); raises FalError on >= 400.

    Network failures propagate as the original urllib / socket exception so the retry
    policy can tell "never connected" from "lost after sending".
    """
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, data=body, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read()
        except Exception:
            pass
        text = raw.decode("utf-8", "replace")
        raise FalError(_describe_http_error(exc.code, text, url), status=exc.code, body=text,
                       headers={k.lower(): v for k, v in dict(exc.headers or {}).items()}) from None


def _describe_http_error(status, text, url):
    detail = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            detail = parsed.get("detail", parsed.get("error", parsed))
            if not isinstance(detail, str):
                detail = json.dumps(detail, ensure_ascii=False)
    except ValueError:
        pass
    detail = (detail or "(no message)")[:1500]
    if status == 401 or status == 403:
        return ("fal refused the key (HTTP %d). Check FAL_KEY in the .env file - it should be "
                "the full key from https://fal.ai/dashboard/keys. Detail: %s" % (status, detail))
    if status == 422:
        return "fal rejected the inputs (HTTP 422): %s" % detail
    return "fal HTTP %d from %s: %s" % (status, url.split("?")[0], detail)


def _never_connected(exc):
    """True when the request certainly never reached fal, so retrying cannot bill twice."""
    reason = getattr(exc, "reason", exc)
    if isinstance(reason, (socket.gaierror, ConnectionRefusedError)):
        return True
    text = str(reason).lower()
    return any(s in text for s in ("getaddrinfo failed", "name or service not known",
                                   "nodename nor servname", "connection refused",
                                   "no route to host", "network is unreachable"))


def _is_ingress_error(exc):
    """A gateway error that never reached a fal app (fal-client's own heuristic)."""
    if not isinstance(exc, FalError) or exc.status not in (502, 503, 504):
        return False
    if "x-fal-request-id" in exc.headers:
        return False
    return "nginx" in (exc.body or "").lower() or not exc.body


def _retry_after(exc):
    try:
        value = exc.headers.get("retry-after")
        return float(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


def _sleep_backoff(attempt, base=1.0, cap=30.0, hint=None):
    delay = hint if hint is not None else min(cap, base * (2 ** attempt))
    time.sleep(delay * random.uniform(0.75, 1.25))


def _retry(call, *, attempts, retry_if, log=None, what="request", cancelled=None):
    """Run ``call()`` and retry while ``retry_if(exc)`` says it is safe and useful."""
    for attempt in range(attempts):
        if cancelled is not None and cancelled():
            raise FalCancelled()
        try:
            return call()
        except FalCancelled:
            raise
        except Exception as exc:                         # noqa: BLE001 - classified below
            if attempt == attempts - 1 or not retry_if(exc):
                raise
            if log:
                log("fal %s failed (%s) - retrying %d/%d"
                    % (what, str(exc)[:160], attempt + 1, attempts - 1))
            _sleep_backoff(attempt, hint=_retry_after(exc))


class FalCancelled(Exception):
    """The ComfyUI run was cancelled while waiting on fal."""


def _safe_to_repeat(exc):
    """For calls that bill nothing or are already paid for: repeat anything transient."""
    if isinstance(exc, FalError):
        return exc.status in (408, 409, 425, 429, 500, 502, 503, 504) or _is_ingress_error(exc)
    return isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError,
                            ConnectionError, ssl.SSLError, OSError))


def _safe_to_resubmit(exc):
    """For the billed submit: repeat only what fal certainly did not accept."""
    if isinstance(exc, FalError):
        return exc.status == 429 or _is_ingress_error(exc)
    return _never_connected(exc)


# ----------------------------------------------------------------------------------------
# Uploads
# ----------------------------------------------------------------------------------------

def _json(body_bytes):
    return json.loads(body_bytes.decode("utf-8"))


def _upload_cdn(key, data, content_type, file_name, log):
    def token():
        _, _, body = _request("POST", REST_URL + "/storage/auth/token?storage_type=fal-cdn-v3",
                              headers={"Authorization": "Key " + key,
                                       "Content-Type": "application/json"},
                              body=b"{}", timeout=60)
        return _json(body)

    tok = _retry(token, attempts=4, retry_if=_safe_to_repeat, log=log, what="upload token")

    def send():
        _, _, body = _request("POST", CDN_URL + "/files/upload",
                              headers={"Authorization": "%s %s" % (tok["token_type"], tok["token"]),
                                       "Content-Type": content_type,
                                       "X-Fal-File-Name": file_name},
                              body=data, timeout=UPLOAD_TIMEOUT)
        return _json(body)["access_url"]

    return _retry(send, attempts=4, retry_if=_safe_to_repeat, log=log, what="upload")


def _upload_storage(key, data, content_type, file_name, log):
    def initiate():
        _, _, body = _request("POST", REST_URL + "/storage/upload/initiate?storage_type=gcs",
                              headers={"Authorization": "Key " + key,
                                       "Content-Type": "application/json"},
                              body=json.dumps({"file_name": file_name,
                                               "content_type": content_type}).encode("utf-8"),
                              timeout=60)
        return _json(body)

    init = _retry(initiate, attempts=4, retry_if=_safe_to_repeat, log=log, what="upload start")

    def put():
        _request("PUT", init["upload_url"], headers={"Content-Type": content_type},
                 body=data, timeout=UPLOAD_TIMEOUT)

    _retry(put, attempts=4, retry_if=_safe_to_repeat, log=log, what="upload")
    return init["file_url"]


CDN_SINGLE_LIMIT = 100 * 1024 * 1024        # fal-client switches to multipart above this


def upload(key, data, content_type, file_name, log=None):
    """Store bytes on fal's CDN and return a URL a model can read. Uploads are not billed."""
    if len(data) <= CDN_SINGLE_LIMIT:
        try:
            return _upload_cdn(key, data, content_type, file_name, log)
        except FalError as exc:
            if exc.status in (401, 403):
                raise
            if log:
                log("fal CDN upload failed (%s) - using the storage upload instead" % str(exc)[:160])
        except Exception as exc:                          # noqa: BLE001
            if log:
                log("fal CDN upload failed (%s) - using the storage upload instead" % str(exc)[:160])
    return _upload_storage(key, data, content_type, file_name, log)


# ----------------------------------------------------------------------------------------
# The queue
# ----------------------------------------------------------------------------------------

def submit(key, endpoint_id, payload, log=None):
    """Queue one request. Billed. Returns fal's handle dict (request_id and the three URLs)."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def post():
        _, _, raw = _request("POST", "%s/%s" % (QUEUE_URL, endpoint_id),
                             headers={"Authorization": "Key " + key,
                                      "Content-Type": "application/json"},
                             body=body, timeout=SUBMIT_TIMEOUT)
        return _json(raw)

    try:
        handle = _retry(post, attempts=6, retry_if=_safe_to_resubmit, log=log, what="submit")
    except FalError:
        raise
    except Exception as exc:                              # noqa: BLE001
        if _never_connected(exc):
            raise FalError("fal: could not reach fal (%s). Nothing was sent and nothing was "
                           "billed - check the internet connection and run again." % exc) from None
        raise FalError(
            "fal: the request was sent but the connection failed before fal answered (%s). "
            "It may have been accepted - check https://fal.ai/dashboard/requests before "
            "running again, so it is not paid for twice." % exc) from None
    for field in ("request_id", "status_url", "response_url", "cancel_url"):
        if not handle.get(field):
            raise FalError("fal: unexpected answer to the submit (no %s): %s"
                           % (field, json.dumps(handle)[:500]))
    return handle


def status(key, handle, log=None, cancelled=None):
    def get():
        url = handle["status_url"]
        _, _, raw = _request("GET", url + ("&" if "?" in url else "?") + "logs=1",
                             headers={"Authorization": "Key " + key}, timeout=STATUS_TIMEOUT)
        return _json(raw)
    return _retry(get, attempts=12, retry_if=_safe_to_repeat, log=log, what="status check",
                  cancelled=cancelled)


def result(key, handle, log=None):
    def get():
        _, _, raw = _request("GET", handle["response_url"],
                             headers={"Authorization": "Key " + key}, timeout=RESULT_TIMEOUT)
        return _json(raw)
    return _retry(get, attempts=10, retry_if=_safe_to_repeat, log=log, what="result fetch")


def cancel(key, handle, log=None):
    """Best effort: ask fal to drop the request. Never raises."""
    try:
        _request("PUT", handle["cancel_url"], headers={"Authorization": "Key " + key},
                 timeout=30)
        if log:
            log("fal: cancel sent for request %s" % handle.get("request_id"))
    except Exception as exc:                              # noqa: BLE001
        if log:
            log("fal: cancel for %s did not go through (%s)" % (handle.get("request_id"), exc))


def wait(key, handle, *, on_update=None, cancelled=None, log=None, poll=None):
    """Poll until fal says COMPLETED. Raises FalCancelled (after cancelling) on ComfyUI cancel."""
    poll = POLL_SECONDS if poll is None else poll
    seen_logs = 0
    while True:
        if cancelled is not None and cancelled():
            cancel(key, handle, log)
            raise FalCancelled()
        try:
            state = status(key, handle, log=log, cancelled=cancelled)
        except FalCancelled:
            cancel(key, handle, log)
            raise
        logs = state.get("logs") or []
        if log and len(logs) > seen_logs:
            for entry in logs[seen_logs:]:
                message = entry.get("message") if isinstance(entry, dict) else str(entry)
                if message:
                    log("fal log: %s" % str(message)[:300])
            seen_logs = len(logs)
        if on_update is not None:
            on_update(state)
        if state.get("status") == "COMPLETED":
            if state.get("error"):
                raise FalError("fal: the request failed on fal's side: %s%s"
                               % (state.get("error"),
                                  " (%s)" % state["error_type"] if state.get("error_type") else ""))
            return state
        time.sleep(poll)


# ----------------------------------------------------------------------------------------
# Downloads
# ----------------------------------------------------------------------------------------

def download(url, path, log=None):
    """Stream a result file to ``path``. Paid work - retried hard. Returns bytes written."""
    if url.startswith("data:"):
        import base64
        header, _, payload = url.partition(",")
        data = base64.b64decode(payload) if ";base64" in header else payload.encode("utf-8")
        with open(path, "wb") as handle:
            handle.write(data)
        return len(data)

    def get():
        tmp = path + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT, context=_SSL) as resp, \
                    open(tmp, "wb") as out:
                total = 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    total += len(chunk)
        except urllib.error.HTTPError as exc:
            raise FalError("fal: download failed (HTTP %d) for %s" % (exc.code, url),
                           status=exc.code) from None
        os.replace(tmp, path)
        return total

    return _retry(get, attempts=8, retry_if=_safe_to_repeat, log=log, what="download")
