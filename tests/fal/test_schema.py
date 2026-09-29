"""Every request body test_end_to_end.py captured must be valid against fal's own OpenAPI
input schema for that endpoint, and must send no field the schema does not know.

Runs with any Python 3.10+ that can pip-install jsonschema into the test dir (ComfyUI's
embedded Python is fine too). Nothing is installed into ComfyUI.
"""
import copy
import json
import os
import subprocess
import sys
import tempfile

WORK = os.path.join(tempfile.gettempdir(), "arkennemasis_fal_tests")
LIB = os.path.join(WORK, "py")
if not os.path.isdir(os.path.join(LIB, "jsonschema")):
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--target", LIB, "jsonschema"], check=True)
sys.path.insert(0, LIB)
import jsonschema  # noqa: E402

payloads = json.load(open(os.path.join(WORK, "payloads.json"), encoding="utf-8"))


def deref(n, root, seen=()):
    if isinstance(n, dict):
        if "$ref" in n:
            name = n["$ref"].split("/")[-1]
            return {} if name in seen else deref(root["components"]["schemas"][name], root, seen + (name,))
        return {k: deref(v, root, seen) for k, v in n.items()
                if k not in ("examples", "x-fal-order-properties", "ui", "_fal_ui_field")}
    if isinstance(n, list):
        return [deref(v, root, seen) for v in n]
    return n


schemas, fails, per = {}, [], {}
for item in payloads:
    ep = item["endpoint"]
    if ep not in schemas:
        root = json.load(open(os.path.join(WORK, "schemas", ep.replace("/", "_") + ".json"), encoding="utf-8"))
        post = next(o["post"] for o in root["paths"].values() if "post" in o)
        schemas[ep] = deref(post["requestBody"]["content"]["application/json"]["schema"], root)
    body = copy.deepcopy(item["payload"])
    body.pop("mock_extra", None)                     # the extra_json test's own made-up field
    errors = sorted(jsonschema.Draft7Validator(schemas[ep]).iter_errors(body), key=str)
    per[ep] = per.get(ep, 0) + 1
    if errors:
        fails.append((ep, item["label"], [e.message[:160] + " @ " + "/".join(map(str, e.path)) for e in errors[:3]]))
    extra = set(body) - set(schemas[ep].get("properties", {}))
    if extra:
        fails.append((ep, item["label"], ["fields fal does not know: %s" % sorted(extra)]))
print("request bodies checked: %d across %d endpoints | invalid: %d" % (len(payloads), len(per), len(fails)))
for f in fails[:40]:
    print("INVALID", f[0], "|", f[1], "|", f[2])
sys.exit(1 if fails else 0)
