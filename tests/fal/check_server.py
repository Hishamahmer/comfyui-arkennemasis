"""Through the REAL running ComfyUI: every fal node is registered, and a fal node queued with
no FAL_KEY stops at the key check. Refuses to run if a FAL_KEY exists, so it can never bill.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
COMFY = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
MODELS = os.path.join(HERE, "..", "..", "fal_provider", "models")
for env in (os.path.join(os.path.dirname(COMFY), ".env"), os.path.join(COMFY, ".env")):
    if os.path.exists(env) and any(l.strip().startswith("FAL_KEY") for l in open(env, encoding="utf-8-sig")):
        sys.exit("A FAL_KEY is present in %s - refusing: this check must not be able to bill." % env)

BASE = "http://127.0.0.1:8188"
defs = json.load(urllib.request.urlopen(BASE + "/object_info", timeout=120))
files = {json.load(open(os.path.join(MODELS, f), encoding="utf-8"))["class_key"]
         for f in os.listdir(MODELS) if f.endswith(".json")}
live = {k for k in defs if k.startswith("ArkFal")}
print("model files: %d | fal nodes live: %d | missing from the server: %s"
      % (len(files), len(live), sorted(files - live) or "none"))


def widgets(t, overrides):
    out = {}
    for section in ("required", "optional"):
        for name, spec in (defs[t]["input"].get(section) or {}).items():
            kind = spec[0] if isinstance(spec[0], str) else "COMBO"
            opts = spec[1] if len(spec) > 1 else {}
            if kind not in ("INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"):
                continue
            if "default" in opts:
                out[name] = opts["default"]
            elif kind == "COMBO":
                out[name] = (spec[0] if isinstance(spec[0], list) else opts["options"])[0]
            else:
                out[name] = {"INT": 0, "FLOAT": 0.0, "BOOLEAN": False}.get(kind, "")
    out.update(overrides)
    return out


def run(prompt):
    body = json.dumps({"prompt": prompt, "client_id": uuid.uuid4().hex}).encode()
    req = urllib.request.Request(BASE + "/prompt", data=body, headers={"Content-Type": "application/json"})
    pid = json.load(urllib.request.urlopen(req, timeout=60))["prompt_id"]
    for _ in range(120):
        h = json.load(urllib.request.urlopen(BASE + "/history/" + pid, timeout=30))
        if pid in h:
            return h[pid]["status"]
        time.sleep(1)
    return {"status_str": "timeout", "messages": []}


hist = run({"1": {"class_type": "ArkFalHistory", "inputs": widgets("ArkFalHistory", {})}})
g = "ArkFal_openai_gpt_image_2_edit"
st = run({"1": {"class_type": "LoadImage", "inputs": {"image": "fal_demo_picture.png"}},
          "2": {"class_type": g, "inputs": widgets(g, {"prompt": "keyless wiring test", "image_1": ["1", 0]})}})
err = [m[1] for m in st.get("messages", []) if m[0] == "execution_error"]
msg = err[0].get("exception_message", "") if err else ""
ok = (not (files - live) and hist.get("status_str") == "success" and st.get("status_str") == "error"
      and "FAL_KEY" in msg and "Nothing was sent" in msg)
print("fal History ->", hist.get("status_str"))
print("GPT Image 2 Edit without a key ->", st.get("status_str"), "|", msg[:160])
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
