"""Download fal's public OpenAPI schema for every model file (free, no key) into the test dir.

The end-to-end test shapes its fake results from these, and test_schema.py validates every
request the nodes sent against them.
"""
import glob
import json
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "..", "..", "fal_provider", "models")
import tempfile
OUT = os.path.join(tempfile.gettempdir(), "arkennemasis_fal_tests", "schemas")
os.makedirs(OUT, exist_ok=True)


def get(endpoint):
    path = os.path.join(OUT, endpoint.replace("/", "_") + ".json")
    if os.path.exists(path) and os.path.getsize(path) > 500:
        return endpoint, "cached"
    url = "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=" + urllib.parse.quote(endpoint, safe="/")
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                open(path, "wb").write(resp.read())
            return endpoint, "ok"
        except Exception:                               # noqa: BLE001
            time.sleep(3 * (attempt + 1))
    return endpoint, "FAILED"


endpoints = [json.load(open(p, encoding="utf-8"))["endpoint_id"]
             for p in glob.glob(os.path.join(MODELS, "*.json"))]
with ThreadPoolExecutor(6) as pool:
    results = list(pool.map(get, endpoints))
bad = [e for e, s in results if s == "FAILED"]
print("schemas ready: %d of %d%s" % (len(results) - len(bad), len(results),
                                     " | FAILED: %s" % bad if bad else ""))
