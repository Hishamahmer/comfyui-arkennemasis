"""Every fal node's REAL run code against a local fal stand-in - no key, nothing billed.

For every model: its defaults, then each dropdown value (up to 5 per dropdown), each toggle
flipped, each "-1 = not set" number set, extra_json. Then the money paths (cap, 429,
connection lost after submit, fal-side error, 422, Cancel, CDN down, no key), identical-run
reuse, fal History, fal Recover Result, and reading FAL_KEY only from .env.

Only the three fal base URLs and the key are pointed at the mock; everything else is the
node code ComfyUI runs. Every request body the mock receives is saved for test_schema.py.
Needs fetch_schemas.py to have run (the mock shapes each result from fal's own schema).
"""
import importlib
import io
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import traceback
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import MODEL_CLASSES, WORK, client, fal, history, media, node, recover, schema_path  # noqa: E402
from _mockfal import KEY, MockFal  # noqa: E402

import comfy.model_management as mm  # noqa: E402
import folder_paths  # noqa: E402
import torch  # noqa: E402
from comfy_api.latest import InputImpl  # noqa: E402
from PIL import Image  # noqa: E402

MEDIA = os.path.join(WORK, "media")
OUT = os.path.join(WORK, "out")
os.makedirs(MEDIA, exist_ok=True)
if os.path.isdir(OUT):
    import shutil
    shutil.rmtree(OUT)
os.makedirs(OUT)
folder_paths.set_output_directory(OUT)


def ffmpeg(args, out):
    if not os.path.exists(out):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args, out], check=True)
    return out


CLIP = ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=6", "-f", "lavfi", "-i",
               "sine=frequency=440:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"],
              os.path.join(MEDIA, "test_6s.mp4"))
RESULT_MP4 = open(ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=480x848:rate=25:duration=4", "-c:v", "libx264",
                          "-pix_fmt", "yuv420p"], os.path.join(MEDIA, "result_4s.mp4")), "rb").read()
RESULT_MP3 = open(ffmpeg(["-f", "lavfi", "-i", "sine=frequency=500:duration=3", "-c:a", "libmp3lame", "-b:a", "128k"],
                         os.path.join(MEDIA, "result_3s.mp3")), "rb").read()
_a = io.BytesIO(); Image.new("RGB", (640, 480), (200, 30, 30)).save(_a, "PNG"); PNG_A = _a.getvalue()
_b = io.BytesIO(); Image.new("RGBA", (640, 480), (30, 200, 30, 128)).save(_b, "PNG"); PNG_B = _b.getvalue()


def _ref(s, root):
    while isinstance(s, dict) and "$ref" in s:
        s = root["components"]["schemas"][s["$ref"].split("/")[-1]]
    if isinstance(s, dict) and s.get("allOf"):
        merged = {k: v for k, v in s.items() if k != "allOf"}
        for part in s["allOf"]:
            part = _ref(part, root)
            for k, v in part.items():
                if k == "properties":
                    merged.setdefault("properties", {}).update(v)
                else:
                    merged.setdefault(k, v)
        return merged
    return s


def _file_for(mock, name):
    n = name.lower()
    if "video" in n:
        return {"url": mock.add_file(RESULT_MP4, "video/mp4", "out.mp4"), "content_type": "video/mp4",
                "file_name": "out.mp4", "file_size": len(RESULT_MP4)}
    if "audio" in n or "speech" in n or "music" in n:
        return {"url": mock.add_file(RESULT_MP3, "audio/mpeg", "out.mp3"), "content_type": "audio/mpeg",
                "file_name": "out.mp3"}
    if "image" in n or "mask" in n:
        return {"url": mock.add_file(PNG_A, "image/png", "a.png"), "content_type": "image/png"}
    return {"url": mock.add_file(b"mock file", "application/octet-stream", "out.bin")}


def result_factory(mock, endpoint, payload):
    """A result shaped like the endpoint's own OpenAPI output schema."""
    root = json.load(open(schema_path(endpoint), encoding="utf-8"))
    out = {}
    for p, ops in root["paths"].items():
        if p.endswith("/requests/{request_id}") and "get" in ops:
            out = _ref(ops["get"]["responses"]["200"]["content"]["application/json"]["schema"], root)
    res = {}
    for name, prop in (out.get("properties") or {}).items():
        prop = _ref(prop, root)
        variants = [_ref(v, root) for v in prop.get("anyOf", [prop]) if _ref(v, root).get("type") != "null"]
        v = variants[0] if variants else {}
        if v.get("type") == "array" and "url" in (_ref(v.get("items", {}), root).get("properties") or {}):
            if "image" in name:
                res[name] = [{"url": mock.add_file(PNG_A, "image/png", "a.png"), "content_type": "image/png",
                              "width": 640, "height": 480},
                             {"url": mock.add_file(PNG_B, "image/png", "b.png"), "content_type": "image/png"}]
            else:
                res[name] = [_file_for(mock, name)]
        elif "url" in (v.get("properties") or {}):
            res[name] = _file_for(mock, name)
        elif v.get("type") == "integer":
            res[name] = 424242
        elif v.get("type") == "number":
            res[name] = 4.0
        elif v.get("type") == "string":
            res[name] = "draft-abc123" if "draft" in name else "mock text"
        elif v.get("type") == "boolean":
            res[name] = True
        elif v.get("type") == "array":
            res[name] = [{"mock": 1}]
        else:
            res[name] = {"mock": True}
    return res


mock = MockFal(result_factory)
client.QUEUE_URL, client.REST_URL, client.CDN_URL = mock.base + "/queue", mock.base + "/rest", mock.base + "/cdn"
client.POLL_SECONDS = 0.01
real_fal_key = client.fal_key
client.fal_key = lambda: KEY
client._sleep_backoff = lambda *a, **k: time.sleep(0.01)

IMG = torch.rand(1, 512, 512, 3)
IMG2 = torch.rand(2, 512, 512, 3)
MASK = torch.zeros(1, 512, 512)
MASK[:, 100:300, 100:300] = 1.0
VIDEO = InputImpl.VideoFromFile(CLIP)
_t = torch.linspace(0, 6.0, 6 * 44100)
AUDIO = {"waveform": torch.stack([torch.sin(2 * math.pi * 220 * _t), torch.sin(2 * math.pi * 330 * _t)])[None] * 0.3,
         "sample_rate": 44100}
PROMPT = "A calm test prompt about a red square."


def default_kwargs(cls):
    spec = cls.FAL_SPEC
    kinds = {i["name"]: i for i in spec["inputs"]}
    info = cls.GET_NODE_INFO_V1()
    kw = {}
    for section in ("required", "optional"):
        for name, s in info["input"].get(section, {}).items():
            t0 = s[0] if isinstance(s[0], str) else "COMBO"
            opts = s[1] if len(s) > 1 else {}
            if t0 == "IMAGE":
                if section == "required" or name.endswith("_1") or name in ("image", "start_image", "end_image",
                                                                            "human_image", "garment_image", "scene_image"):
                    kw[name] = IMG
                if name.endswith("_2"):
                    kw[name] = IMG2
            elif t0 == "MASK":
                kw[name] = MASK
            elif t0 == "VIDEO":
                if section == "required" or name in ("video", "video_1"):
                    kw[name] = VIDEO
            elif t0 == "AUDIO":
                if section == "required" or name in ("audio", "audio_1"):
                    kw[name] = AUDIO
            elif t0 in ("COMBO", "INT", "FLOAT", "BOOLEAN"):
                kw[name] = opts.get("default")
            elif t0 == "STRING":
                value = opts.get("default", "")
                inp = kinds.get(name) or {}
                if not value and inp.get("required") and inp.get("kind") in ("text", "string", "url"):
                    value = "https://example.com/input.bin" if inp["kind"] == "url" else PROMPT
                kw[name] = value
    kw["reuse_identical_run"] = False
    kw["max_cost_usd"] = 0
    return kw


def has_media_output(spec):
    return any(o["kind"] in ("images", "video", "audio") for o in spec["outputs"])


def check_outputs(cls, values, ui):
    info = cls.GET_NODE_INFO_V1()
    assert len(values) == len(info["output"]), (len(values), info["output"])
    for v, typ in zip(values, info["output"]):
        if typ == "IMAGE":
            assert isinstance(v, torch.Tensor) and v.dim() == 4 and v.shape[-1] == 3, getattr(v, "shape", v)
        elif typ == "VIDEO":
            assert type(v).__name__ == "VideoFromFile" and os.path.isfile(v.get_stream_source())
            assert abs(v.get_duration() - 4.0) < 0.2, v.get_duration()
        elif typ == "AUDIO":
            assert isinstance(v, dict) and v["waveform"].dim() == 3 and v["sample_rate"] > 0, type(v)
        elif typ == "STRING":
            assert isinstance(v, str)
        elif typ == "INT":
            assert isinstance(v, int)
        elif typ == "FLOAT":
            assert isinstance(v, float)
    info_json = json.loads(values[-1])
    for f in info_json["files"]:
        assert os.path.isfile(f) and os.path.getsize(f) > 0, f
        assert "_." in os.path.basename(f), f              # name_00001_.ext
    if has_media_output(cls.FAL_SPEC):
        assert ui and (ui.get("images") or ui.get("audio")), ui
    return info_json


captured, report = [], []


def run(key, kw, label, expect_error=None):
    cls = fal.NODE_CLASS_MAPPINGS[key]
    spec = cls.FAL_SPEC
    try:
        values, ui = node.run_blocking(spec, kw, None)
    except BaseException as exc:                        # noqa: BLE001 - Cancel is not an Exception
        if expect_error and expect_error(exc):
            report.append(("ok", key, label, "raised as expected: %s" % str(exc)[:120]))
            return exc
        report.append(("FAIL", key, label, "%s: %s" % (type(exc).__name__, str(exc)[:300])))
        if not expect_error:
            traceback.print_exc(limit=3)
        return exc
    if expect_error:
        report.append(("FAIL", key, label, "expected an error, got success"))
        return None
    job = [j for j in mock.jobs.values() if j["endpoint"] == spec["endpoint_id"]]
    info = json.loads(values[-1])
    if not info.get("reused") and job:
        captured.append({"endpoint": spec["endpoint_id"], "label": label, "payload": job[-1]["payload"]})
    try:
        info = check_outputs(cls, values, ui)
        report.append(("ok", key, label, "files=%d %s" % (len(info["files"]), info["estimate"][:60])))
    except AssertionError as exc:
        report.append(("FAIL", key, label, "output check: %s" % exc))
    return values


# ---- 1. every model: defaults, then variants -------------------------------------------
started = time.time()
for key, cls in sorted(MODEL_CLASSES.items()):
    base = default_kwargs(cls)
    run(key, dict(base), "defaults")
    info = cls.GET_NODE_INFO_V1()
    for section in ("required", "optional"):
        for name, s in info["input"].get(section, {}).items():
            t0 = s[0] if isinstance(s[0], str) else "COMBO"
            if name in ("max_cost_usd", "reuse_identical_run", "extra_json", "max_concurrent"):
                continue
            if t0 == "COMBO":
                options = s[1]["options"] if isinstance(s[0], str) else s[0]
                picks = [o for o in options if o != base.get(name)]
                picks = picks if len(picks) <= 5 else picks[:3] + picks[-2:]
                for opt in picks:
                    run(key, dict(base, **{name: opt}), "%s=%s" % (name, opt))
            elif t0 == "BOOLEAN":
                run(key, dict(base, **{name: not base.get(name)}), "%s=%s" % (name, not base.get(name)))
            elif t0 in ("INT", "FLOAT") and s[1].get("min") == -1:
                inp = next((i for i in cls.FAL_SPEC["inputs"] if i["name"] == name), {})
                hi = inp.get("schema_max", s[1].get("max"))
                lo = inp.get("schema_min")
                val = lo if lo is not None else (min(7, hi) if hi else 7) if t0 == "INT" else \
                    (lo if lo is not None else min(0.5, hi) if hi is not None else 0.5)
                run(key, dict(base, **{name: val}), "%s set" % name)
                if lo is not None and lo > 0:
                    run(key, dict(base, **{name: lo / 2.0 if t0 == "FLOAT" else 0}),
                        "%s below fal's minimum -> refused" % name,
                        expect_error=lambda e: isinstance(e, media.InputError))
    run(key, dict(base, extra_json='{"mock_extra": {"a": 1}}'), "extra_json")
print("section 1: %d runs in %.0f s" % (len(report), time.time() - started))

# ---- 2. input checks (refused before anything is uploaded) ----------------------------------
G = "ArkFal_openai_gpt_image_2_edit"
run(G, dict(default_kwargs(MODEL_CLASSES[G]), image_size="custom", image_size_width=1536, image_size_height=1024), "custom size")
kw = default_kwargs(MODEL_CLASSES[G]); kw["image_1"] = None; kw.pop("image_2", None)
run(G, kw, "missing required picture -> refused", expect_error=lambda e: isinstance(e, media.InputError))
S = "ArkFal_bytedance_seedance_2_5_text_to_video"
kw = dict(default_kwargs(MODEL_CLASSES[S]), prompt="   ")
run(S, kw, "empty required prompt -> refused", expect_error=lambda e: isinstance(e, media.InputError))
kw = dict(default_kwargs(MODEL_CLASSES[S]), resolution="1080p", duration="auto", max_cost_usd=20)
n0, f0 = mock.submits.get("bytedance/seedance-2.5/text-to-video", 0), len(mock.files)
run(S, kw, "estimate over the cap -> refused, nothing uploaded or sent",
    expect_error=lambda e: isinstance(e, media.InputError) and "max_cost_usd" in str(e)
    and mock.submits.get("bytedance/seedance-2.5/text-to-video", 0) == n0 and len(mock.files) == f0)
run(S, dict(kw, extra_json="{not json"), "bad extra_json -> refused", expect_error=lambda e: isinstance(e, media.InputError))
N = "ArkFal_fal_ai_nano_banana_2_edit"
kw = default_kwargs(MODEL_CLASSES[N])
for k in ("image_1", "image_2", "video", "audio"):
    kw.pop(k, None)
run(N, kw, "nothing connected -> refused", expect_error=lambda e: isinstance(e, media.InputError))
H = "ArkFal_minimax_h3_max_lip_sync_image_to_video"
run(H, dict(default_kwargs(MODEL_CLASSES[H]), audio={"waveform": AUDIO["waveform"][..., :44100 * 3], "sample_rate": 44100}),
    "3 s audio -> refused (min 5 s)", expect_error=lambda e: isinstance(e, media.InputError))
R = "ArkFal_fal_ai_sync_lipsync_react_1"
run(R, dict(default_kwargs(MODEL_CLASSES[R]), audio={"waveform": torch.zeros(1, 2, 44100 * 20), "sample_rate": 44100}),
    "20 s audio -> refused (max 15 s)", expect_error=lambda e: isinstance(e, media.InputError))
F = "ArkFal_veed_fabric_1_0"
run(F, dict(default_kwargs(MODEL_CLASSES[F]), image=IMG2), "batch into a single picture -> refused",
    expect_error=lambda e: isinstance(e, media.InputError))
T = "ArkFal_fal_ai_elevenlabs_text_to_dialogue_eleven_v3"
run(T, dict(default_kwargs(MODEL_CLASSES[T]), inputs="[not json"), "bad JSON field -> refused",
    expect_error=lambda e: isinstance(e, media.InputError))
V3 = "ArkFal_fal_ai_sync_lipsync_v3"
run(V3, dict(default_kwargs(MODEL_CLASSES[V3]), video=VIDEO.as_trimmed(start_time=1.0, duration=3.0)), "trimmed video -> re-encoded")

# ---- 3. money paths ----------------------------------------------------------------------
E, ep = "ArkFal_veed_fabric_1_0_fast", "veed/fabric-1.0/fast"
mock.modes[ep] = {"submit_429_once"}
c0 = mock.submits.get(ep, 0)
run(E, default_kwargs(MODEL_CLASSES[E]), "429 on submit -> retried")
report.append(("ok" if mock.submits.get(ep, 0) - c0 == 2 else "FAIL", E, "429: exactly one resend", str(mock.submits.get(ep, 0) - c0)))
mock.modes[ep] = {"drop_after_submit"}
c0 = mock.submits.get(ep, 0)
run(E, default_kwargs(MODEL_CLASSES[E]), "connection lost after submit -> NOT resent",
    expect_error=lambda e: isinstance(e, client.FalError) and "may have been accepted" in str(e) and mock.submits.get(ep, 0) - c0 == 1)
mock.modes[ep] = {"completed_error"}
run(E, default_kwargs(MODEL_CLASSES[E]), "fal-side error -> clear message",
    expect_error=lambda e: isinstance(e, client.FalError) and "mock model crashed" in str(e))
mock.modes[ep] = {"result_422"}
run(E, default_kwargs(MODEL_CLASSES[E]), "422 on result -> clear message, points to Recover",
    expect_error=lambda e: isinstance(e, client.FalError) and "422" in str(e) and "Recover" in str(e))
mock.modes[ep] = {"never_complete"}
c_before, calls = mock.submits.get(ep, 0), {"n": 0}


def cancel_after_submit():
    calls["n"] += 1
    return mock.submits.get(ep, 0) > c_before and calls["n"] > 3


orig = node._cancelled
node._cancelled = cancel_after_submit
run(E, default_kwargs(MODEL_CLASSES[E]), "Cancel while waiting -> cancelled on fal",
    expect_error=lambda e: isinstance(e, mm.InterruptProcessingException))
node._cancelled = orig
job = [j for j in mock.jobs.values() if j["endpoint"] == ep][-1]
report.append(("ok" if job.get("cancelled") else "FAIL", E, "the cancel reached fal", str(job.get("cancelled"))))
mock.modes[ep] = set()
mock.modes["*"] = {"cdn_down"}
run(E, default_kwargs(MODEL_CLASSES[E]), "CDN down -> storage upload fallback")
mock.modes["*"] = set()
client.fal_key = lambda: (_ for _ in ()).throw(client.FalKeyMissing("fal: no FAL_KEY found (test)"))
c0, f0 = sum(mock.submits.values()), len(mock.files)
run(E, default_kwargs(MODEL_CLASSES[E]), "no key -> refused, nothing sent",
    expect_error=lambda e: isinstance(e, client.FalKeyMissing) and sum(mock.submits.values()) == c0 and len(mock.files) == f0)
client.fal_key = lambda: KEY

# ---- 4. reuse, History, Recover ------------------------------------------------------------
RU, rep = "ArkFal_veed_fabric_1_0", "veed/fabric-1.0"
kw = dict(default_kwargs(MODEL_CLASSES[RU]), reuse_identical_run=True, image=torch.rand(1, 512, 512, 3))
c0 = mock.submits.get(rep, 0)
first = run(RU, dict(kw), "reuse: first run pays")
second = run(RU, dict(kw), "reuse: identical second run")
c2 = mock.submits.get(rep, 0)
same = isinstance(first, list) and isinstance(second, list) and first[1] == second[1] and json.loads(second[-1]).get("reused")
report.append(("ok" if c2 - c0 == 1 and same else "FAIL", RU, "identical inputs -> reused, NOT billed", "submits=%d" % (c2 - c0)))
third = run(RU, dict(kw, reuse_identical_run=False), "reuse off -> pays again")
os.remove(first[1]); os.remove(third[1])
c3 = mock.submits.get(rep, 0)
run(RU, dict(kw), "every copy deleted -> pays again")
report.append(("ok" if mock.submits.get(rep, 0) - c3 == 1 else "FAIL", RU, "deleted result is not reused", ""))
text, images, video, paths, detail, ui = history._pick(0, history.ALL, None)
report.append(("ok" if json.loads(detail)["endpoint"] == rep and video is not None else "FAIL", "-", "History: newest run loads", ""))
rows = history.runs()
cancelled = [i for i, r in enumerate(rows) if r["status"] == "cancelled"]
try:
    history._pick(cancelled[0], history.ALL, None)
    report.append(("FAIL", "-", "History on a cancelled run", "no error"))
except ValueError as exc:
    report.append(("ok" if "Recover" in str(exc) else "FAIL", "-", "History: uncollected run points to Recover", ""))
vals, _ = node.run_blocking(MODEL_CLASSES[V3].FAL_SPEC, default_kwargs(MODEL_CLASSES[V3]), None)
rid = json.loads(vals[-1])["request_id"]
s0 = sum(mock.submits.values())
p_out, _, ui3 = recover._recover(rid, "", None)
report.append(("ok" if p_out and all(os.path.isfile(p) for p in p_out.split("\n")) and sum(mock.submits.values()) == s0
               else "FAIL", "-", "Recover: collects by id, submits nothing", ""))

# ---- 5. the key comes only from the install's .env -------------------------------------------
real_client = importlib.reload(importlib.import_module("arkpack.fal_provider.client"))
tmp = tempfile.mkdtemp()
root_env, comfy_env = os.path.join(tmp, ".env"), os.path.join(tmp, "ComfyUI", ".env")
os.makedirs(os.path.dirname(comfy_env))
real_client._env_files = lambda: [root_env, comfy_env]
os.environ["FAL_KEY"] = "from-os-env-must-be-ignored"
try:
    real_client.fal_key()
    report.append(("FAIL", "-", "no .env -> refused", "a key came back"))
except real_client.FalKeyMissing:
    report.append(("ok", "-", "no .env -> refused (OS environment ignored)", ""))
open(comfy_env, "w").write("FAL_KEY=comfy-folder-key\n")
report.append(("ok" if real_client.fal_key() == "comfy-folder-key" else "FAIL", "-", "key in ComfyUI\\.env", ""))
open(root_env, "w", encoding="utf-8-sig").write('# keys\nexport FAL_KEY="root-key:secret"\n')
report.append(("ok" if real_client.fal_key() == "root-key:secret" else "FAIL", "-", "portable-root .env wins", ""))
del os.environ["FAL_KEY"]

# ---- 6. what was uploaded ------------------------------------------------------------------
kinds = {}
for data, ctype, name in mock.files.values():
    kinds.setdefault(ctype, []).append((name, data))
for name, data in kinds.get("audio/wav", [])[:1]:
    w = wave.open(io.BytesIO(data))
    report.append(("ok" if (w.getnchannels(), w.getframerate(), w.getsampwidth()) == (2, 44100, 2) else "FAIL", "-", "uploaded WAV", ""))
masks = [d for n, d in kinds.get("image/png", []) if n == "mask.png"]
if masks:
    a = Image.open(io.BytesIO(masks[0])).getchannel("A")
    report.append(("ok" if a.getpixel((200, 200)) == 0 and a.getpixel((10, 10)) == 255 else "FAIL", "-", "uploaded mask", ""))
ledger = [json.loads(l) for l in open(os.path.join(OUT, "fal", "_requests.jsonl"), encoding="utf-8")]
assert all("test-secret" not in json.dumps(e) for e in ledger), "the key leaked into the request log"
mock.stop()

json.dump(captured, open(os.path.join(WORK, "payloads.json"), "w", encoding="utf-8"), indent=1)
fails = [r for r in report if r[0] != "ok"]
models_ok = {r[1] for r in report if r[0] == "ok" and r[2] == "defaults"}
print("models run with defaults: %d of %d" % (len(models_ok), len(MODEL_CLASSES)))
print("checks: %d | failures: %d | request bodies captured: %d | %.0f s"
      % (len(report), len(fails), len(captured), time.time() - started))
for r in fails:
    print("FAIL", r[1], "|", r[2], "|", r[3])
sys.exit(1 if fails else 0)
