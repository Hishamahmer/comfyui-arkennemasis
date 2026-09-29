"""Knock on the REAL fal endpoints with a deliberately invalid key - nothing can be billed.

Proves, from ComfyUI's own Python and its SSL setup, that every model's endpoint id routes
(fal answers 401/403 "who are you", not 404 "no such thing") and that the upload routes and
the readable error translation work against the real servers.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import MODEL_CLASSES, client  # noqa: E402

BAD = "invalid-key-id:invalid-key-secret"


def knock(label, method, url, headers, body):
    try:
        client._request(method, url, headers=headers, body=body, timeout=60)
        return label, "200?!"
    except client.FalError as exc:
        return label, exc.status
    except Exception as exc:                            # noqa: BLE001
        return label, "network: %s" % str(exc)[:80]


jobs = [(c.FAL_SPEC["endpoint_id"], "POST", "%s/%s" % (client.QUEUE_URL, c.FAL_SPEC["endpoint_id"]),
         {"Authorization": "Key " + BAD, "Content-Type": "application/json"}, b"{}")
        for c in MODEL_CLASSES.values()]
jobs += [("upload token", "POST", client.REST_URL + "/storage/auth/token?storage_type=fal-cdn-v3",
          {"Authorization": "Key " + BAD, "Content-Type": "application/json"}, b"{}"),
         ("storage initiate", "POST", client.REST_URL + "/storage/upload/initiate?storage_type=gcs",
          {"Authorization": "Key " + BAD, "Content-Type": "application/json"}, b'{"file_name":"x.png","content_type":"image/png"}'),
         ("cdn upload", "POST", client.CDN_URL + "/files/upload",
          {"Authorization": "Bearer invalid", "Content-Type": "image/png", "X-Fal-File-Name": "x.png"}, b"\x89PNG")]
with ThreadPoolExecutor(8) as pool:
    results = list(pool.map(lambda j: knock(*j), jobs))
bad = [(l, s) for l, s in results if s not in (401, 403)]
print("probes: %d | answered 401/403 (the route exists, the fake key was refused): %d"
      % (len(results), len(results) - len(bad)))
for l, s in bad:
    print("UNEXPECTED", l, s)
sys.exit(1 if bad else 0)
