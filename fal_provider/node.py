"""One ComfyUI node per fal model, built from the model's JSON file.

A run, in order - and nothing reaches fal until every check before the upload has passed:

  1. the key          FAL_KEY from the install's .env                    (free)
  2. the inputs       required fields, lengths, clip lengths, extra_json   (free)
  3. the estimate     checked against max_cost_usd                        (free)
  4. uploads          the node's pictures / clips / audio to fal's CDN     (free)
  5. submit           the one billed call
  6. wait             status on the node; ComfyUI's Cancel cancels on fal too
  7. results          downloaded into output/fal/<model>/, shown on the node

Every submit and every finished result is written to output/fal/_requests.jsonl, so a
run whose ComfyUI was restarted mid-wait can still be collected (fal Recover Result)
instead of being paid for twice.
"""

from __future__ import annotations

import asyncio
import copy
import datetime
import json
import os
import re
import time

from comfy_api.latest import io

from . import client, media, pricing
from .schema_convert import NOT_SET

CATEGORY_ROOT = "arkennemasis/fal"
LEDGER_NAME = "_requests.jsonl"
DEFAULT_MAX_COST = 20.0

MEDIA_KINDS = ("image", "image_list", "mask", "video", "video_list", "audio", "audio_list")


# ---------------------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------------------

def socket_names(inp):
    if inp["kind"].endswith("_list"):
        return ["%s_%d" % (inp["name"], i) for i in range(1, inp["count"] + 1)]
    return [inp["name"]]


def _schema_inputs(spec):
    out = []
    for inp in spec["inputs"]:
        kind, name, tip = inp["kind"], inp["name"], inp.get("tooltip") or None
        # Only a SOCKET can be genuinely absent. A widget always carries a value (fal's
        # "optional" fields are handled by their not-set value), so every widget is declared
        # required - which keeps the widgets in fal's own order on the node.
        socket_optional = not inp.get("required")
        optional = False
        if kind in ("image", "mask", "video", "audio"):
            optional = socket_optional
            cls = {"image": io.Image, "mask": io.Mask, "video": io.Video, "audio": io.Audio}[kind]
            out.append(cls.Input(name, optional=optional, tooltip=tip))
        elif kind.endswith("_list"):
            cls = {"image_list": io.Image, "video_list": io.Video, "audio_list": io.Audio}[kind]
            for i, sock in enumerate(socket_names(inp)):
                out.append(cls.Input(sock, optional=(socket_optional or i > 0), tooltip=tip))
        elif kind in ("text", "string", "url", "json"):
            out.append(io.String.Input(name, multiline=(kind in ("text", "json")),
                                       default=inp.get("default", ""), optional=optional, tooltip=tip))
        elif kind in ("enum", "tribool"):
            out.append(io.Combo.Input(name, options=list(inp["options"]), default=inp["default"],
                                      optional=optional, tooltip=tip))
        elif kind == "bool":
            out.append(io.Boolean.Input(name, default=bool(inp["default"]), optional=optional,
                                        tooltip=tip))
        elif kind == "int":
            out.append(io.Int.Input(name, default=int(inp["default"]), min=inp.get("min"),
                                    max=inp.get("max"), optional=optional, tooltip=tip))
        elif kind == "float":
            out.append(io.Float.Input(name, default=float(inp["default"]), min=inp.get("min"),
                                      max=inp.get("max"), step=inp.get("step", 0.01),
                                      optional=optional, tooltip=tip))
        elif kind == "image_size":
            out.append(io.Combo.Input(name, options=list(inp["options"]), default=inp["default"],
                                      optional=optional, tooltip=tip))
            out.append(io.Int.Input(name + "_width", default=1024, min=16,
                                    max=int(inp.get("width_max", 8192)), step=16,
                                    tooltip="Width in pixels when %s is 'custom'." % name))
            out.append(io.Int.Input(name + "_height", default=1024, min=16,
                                    max=int(inp.get("height_max", 8192)), step=16,
                                    tooltip="Height in pixels when %s is 'custom'." % name))
        else:
            raise ValueError("fal: unknown input kind %r in %s" % (kind, spec["endpoint_id"]))
    labels = {inp["name"]: inp["label"] for inp in spec["inputs"] if inp.get("label")}
    for widget in out:
        if widget.id in labels:
            widget.display_name = labels[widget.id]
    out.append(io.Float.Input(
        "max_cost_usd", default=DEFAULT_MAX_COST, min=0.0, max=100000.0, step=0.5, optional=True,
        tooltip="Safety cap. If the estimated cost of this run is above this many US dollars "
                "the node stops BEFORE sending anything to fal. 0 = no cap."))
    out.append(io.Boolean.Input(
        "reuse_identical_run", default=True, optional=True,
        tooltip="If these exact inputs (the settings AND the same pictures/clips/audio) were "
                "already paid for and the files are still in output/fal, return them instead "
                "of paying again - also after a ComfyUI restart. Turn off for a fresh take "
                "with the same settings."))
    out.append(io.String.Input(
        "extra_json", multiline=True, default="", optional=True,
        tooltip="Advanced: a JSON object merged into the request last, for any fal field this "
                "node has no box for. Example: {\"seed\": 7}. Leave empty normally."))
    return out


def _schema_outputs(spec):
    out = []
    for o in spec["outputs"]:
        kind, field = o["kind"], o["field"]
        if kind == "images":
            out += [io.Image.Output("images"), io.String.Output("image_paths")]
        elif kind == "video":
            out += [io.Video.Output("video"), io.String.Output("video_path")]
        elif kind == "audio":
            out += [io.Audio.Output("audio"), io.String.Output("audio_path")]
        elif kind in ("files", "file"):
            out.append(io.String.Output(field + "_path"))
        elif kind in ("string", "json"):
            out.append(io.String.Output(field))
        elif kind == "int":
            out.append(io.Int.Output(field))
        elif kind == "float":
            out.append(io.Float.Output(field))
        elif kind == "bool":
            out.append(io.Boolean.Output(field))
    out.append(io.String.Output("info", tooltip="JSON: request id, cost estimate, saved files "
                                                "and fal's full answer."))
    return out


def _price_badge(spec, schema_inputs):
    expr = pricing.badge_expr(spec.get("pricing"))
    if not expr:
        return None
    widgets, sockets = pricing.badge_depends(spec.get("pricing"))
    known = {i.id for i in schema_inputs}
    missing = [w for w in widgets + sockets if w not in known]
    if missing:
        print("[arkennemasis] fal %s: price badge names unknown inputs %s - badge left off"
              % (spec["endpoint_id"], missing))
        return None
    return io.PriceBadge(expr=expr, depends_on=io.PriceBadgeDepends(widgets=widgets, inputs=sockets))


def display_name(spec):
    tag = (spec.get("pricing") or {}).get("tag")
    return "arkennemasis fal · %s%s" % (spec["title"], " · %s" % tag if tag else "")


def description(spec):
    p = spec.get("pricing") or {}
    parts = [spec.get("description", "").strip(),
             "Pricing (from fal, %s): %s" % (p.get("checked", "?"), p.get("text", "see the model page")),
             "Model page: %s" % spec["page"],
             "Key: FAL_KEY in the .env next to run_nvidia_gpu.bat. Results are saved in "
             "output/fal/%s/." % spec["folder"]]
    return "\n\n".join(x for x in parts if x)


# ---------------------------------------------------------------------------------------
# run helpers
# ---------------------------------------------------------------------------------------

def _set_path(payload, dotted, value):
    keys = dotted.split(".")
    cur = payload
    for k in keys[:-1]:
        cur = cur.setdefault(k, {})
    cur[keys[-1]] = value


def _merge(base, extra):
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def _ledger(event, **fields):
    try:
        import folder_paths
        folder = os.path.join(folder_paths.get_output_directory(), "fal")
        os.makedirs(folder, exist_ok=True)
        line = dict(time=datetime.datetime.now().isoformat(timespec="seconds"), event=event, **fields)
        with open(os.path.join(folder, LEDGER_NAME), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:                            # noqa: BLE001 - never fail a paid run over a log
        print("[arkennemasis] fal: could not write the request log (%s)" % exc)


class _Reporter:
    """Status text on the node itself (the frontend shows it under the title) + the console."""

    def __init__(self, node_id, title):
        self.node_id = node_id
        self.title = title

    def __call__(self, text, console=True):
        if console:
            print("[arkennemasis] fal %s: %s" % (self.title, text))
        if self.node_id is None:
            return
        try:
            from server import PromptServer
            PromptServer.instance.send_progress_text(text, self.node_id)
        except Exception:                               # noqa: BLE001 - cosmetic
            pass


def _cancelled():
    try:
        import comfy.model_management as mm
        return mm.processing_interrupted()
    except Exception:                                   # noqa: BLE001
        return False


def _interrupt():
    import comfy.model_management as mm
    raise mm.InterruptProcessingException()


def _save_target(folder, ext):
    """A fresh output path under output/fal/<folder>/, numbered like ComfyUI's saves."""
    import folder_paths
    out_root = folder_paths.get_output_directory()
    full, name, counter, subfolder, _ = folder_paths.get_save_image_path(
        "fal/%s/%s" % (folder, folder), out_root)
    os.makedirs(full, exist_ok=True)
    while True:
        filename = "%s_%05d_%s" % (name, counter, ext)
        path = os.path.join(full, filename)
        if not os.path.exists(path):
            return path, filename, subfolder
        counter += 1


def _files_in(value):
    """fal File objects in a result field (one object or a list)."""
    if isinstance(value, dict) and value.get("url"):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict) and v.get("url")]
    return []


# ---------------------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------------------

def _prepare(spec, kw, say):
    """Check every input and turn it into payload fields + files to upload. Nothing is sent."""
    payload, uploads, media_seconds = {}, [], {}
    first_image_size = None
    provided = set()

    def need(cond, msg):
        if not cond:
            raise media.InputError("fal %s: %s" % (spec["title"], msg))

    for inp in spec["inputs"]:
        kind, name, path = inp["kind"], inp["name"], inp["path"]

        if kind in ("image", "image_list"):
            items = []
            for sock in socket_names(inp):
                frames = media.image_frames(kw.get(sock))
                if kind == "image":
                    need(len(frames) <= 1, "'%s' takes one picture but got a batch of %d. Pick one "
                                           "frame first." % (sock, len(frames)))
                items += frames
                if frames:
                    provided.add(sock)
            if not items:
                need(not inp.get("required"), "'%s' is required - connect a picture."
                     % socket_names(inp)[0])
                continue
            if inp.get("max_items"):
                need(len(items) <= int(inp["max_items"]),
                     "at most %d pictures for '%s', got %d." % (inp["max_items"], name, len(items)))
            if first_image_size is None:
                first_image_size = media.image_size(items[0])
            for i, frame in enumerate(items):
                uploads.append({"path": path, "list": kind == "image_list", "index": i,
                                "data": media.png_bytes(frame), "type": "image/png",
                                "name": "%s_%d.png" % (name, i + 1)})
            continue

        if kind == "mask":
            value = kw.get(name)
            if value is None:
                need(not inp.get("required"), "'%s' is required." % name)
                continue
            provided.add(name)
            uploads.append({"path": path, "list": False, "index": 0, "late_mask": value,
                            "type": "image/png", "name": "mask.png"})
            continue

        if kind in ("video", "video_list", "audio", "audio_list"):
            is_video = kind.startswith("video")
            count = 0
            for i, sock in enumerate(socket_names(inp)):
                value = kw.get(sock)
                if value is None:
                    continue
                provided.add(sock)
                if is_video:
                    data, ctype, fname = media.video_file_bytes(value)
                    seconds = media.video_duration(value)
                else:
                    data, ctype, fname = media.audio_wav_bytes(value), "audio/wav", "%s.wav" % sock
                    seconds = media.audio_duration(value)
                if seconds:
                    media_seconds[sock] = seconds
                uploads.append({"path": path, "list": kind.endswith("_list"), "index": count,
                                "data": data, "type": ctype, "name": fname})
                count += 1
            if not count:
                need(not inp.get("required"), "'%s' is required - connect %s."
                     % (socket_names(inp)[0], "a video" if is_video else "audio"))
            continue

        value = kw.get(name)
        if kind in ("text", "string", "url"):
            text = "" if value is None else str(value)
            if kind == "url":
                text = text.strip()
            if not text.strip():
                need(not inp.get("required"), "'%s' is required - it is empty." % name)
                continue
            if inp.get("max_length"):
                need(len(text) <= int(inp["max_length"]), "'%s' is %d characters; the limit is %d."
                     % (name, len(text), inp["max_length"]))
            if inp.get("min_length"):
                need(len(text) >= int(inp["min_length"]), "'%s' is shorter than %d characters."
                     % (name, inp["min_length"]))
            provided.add(name)
            _set_path(payload, path, text)
        elif kind == "json":
            text = "" if value is None else str(value).strip()
            if not text:
                need(not inp.get("required"), "'%s' is required - it is empty (it takes JSON)." % name)
                continue
            try:
                parsed = json.loads(text)
            except ValueError as exc:
                raise media.InputError("fal %s: '%s' is not valid JSON (%s)."
                                       % (spec["title"], name, exc)) from None
            provided.add(name)
            _set_path(payload, path, parsed)
        elif kind == "enum":
            if value is None or value == NOT_SET:
                continue
            need(str(value) in inp["options"], "'%s' has an unknown value %r." % (name, value))
            if inp.get("value_type") == "int":
                value = int(value)
            elif inp.get("value_type") == "number":
                value = float(value)
            elif inp.get("value_type") == "mixed":
                text = str(value)
                if re.fullmatch(r"-?[0-9]+", text):
                    value = int(text)
                elif re.fullmatch(r"-?[0-9]*\.[0-9]+", text):
                    value = float(text)
            _set_path(payload, path, value)
        elif kind == "tribool":
            if value is None or value == NOT_SET:
                continue
            _set_path(payload, path, str(value).lower() == "true")
        elif kind == "bool":
            _set_path(payload, path, bool(value))
        elif kind in ("int", "float"):
            if value is None:
                continue
            if "not_set" in inp and float(value) == float(inp["not_set"]):
                continue
            lo, hi = inp.get("schema_min"), inp.get("schema_max")
            need((lo is None or float(value) >= float(lo) - 1e-9) and
                 (hi is None or float(value) <= float(hi) + 1e-9),
                 "'%s' is %s; fal takes %s to %s (or -1 to leave it to the model)."
                 % (name, value, "-inf" if lo is None else lo, "inf" if hi is None else hi))
            _set_path(payload, path, int(value) if kind == "int" else float(value))
        elif kind == "image_size":
            if value == "custom":
                _set_path(payload, path, {"width": int(kw.get(name + "_width") or 1024),
                                          "height": int(kw.get(name + "_height") or 1024)})
            elif value is not None:
                _set_path(payload, path, value)

    for mask_up in [u for u in uploads if "late_mask" in u]:
        mask_up["data"] = media.mask_png_bytes(mask_up.pop("late_mask"), first_image_size)

    checks = spec.get("checks") or {}
    for sock, limit in (checks.get("min_seconds") or {}).items():
        if sock in media_seconds:
            need(media_seconds[sock] >= float(limit) - 0.01, "'%s' is %.1f s; this model needs at "
                 "least %g s." % (sock, media_seconds[sock], limit))
    for sock, limit in (checks.get("max_seconds") or {}).items():
        if sock in media_seconds:
            need(media_seconds[sock] <= float(limit) + 0.01, "'%s' is %.1f s; this model takes at "
                 "most %g s." % (sock, media_seconds[sock], limit))
    any_of = checks.get("require_any")
    if any_of:
        need(any(n in provided for n in any_of),
             "connect at least one of: %s." % ", ".join(any_of))

    extra = (kw.get("extra_json") or "").strip()
    if extra:
        try:
            extra_obj = json.loads(extra)
        except ValueError as exc:
            raise media.InputError("fal %s: extra_json is not valid JSON (%s)."
                                   % (spec["title"], exc)) from None
        need(isinstance(extra_obj, dict), "extra_json must be a JSON object like {\"seed\": 7}.")
        _merge(payload, extra_obj)
    return payload, uploads, media_seconds


def _download(spec, handle, answer, say):
    """Download every result file into output/fal/<folder>/ -> {field: [paths]}."""
    folder = spec["folder"]
    field_files = {}
    for o in spec["outputs"]:
        kind, field = o["kind"], o["field"]
        if kind not in ("images", "video", "audio", "files", "file"):
            continue
        files = _files_in(answer.get(field))
        if not files and kind in ("images", "video", "audio"):
            raise client.FalError("fal %s: the answer has no '%s'. Request %s. Answer: %s"
                                  % (spec["title"], field, handle["request_id"],
                                     json.dumps(answer)[:800]))
        if kind in ("video", "audio"):
            files = files[:1]
        paths = []
        fallback = {"images": ".png", "video": ".mp4", "audio": ".mp3"}.get(kind, ".bin")
        for f in files:
            ext = media.extension_for(f, fallback)
            path, filename, _ = _save_target(folder, ext)
            size = client.download(f["url"], path, log=say)
            say("saved %s (%.1f MB)" % (filename, size / 1e6))
            paths.append(path)
        field_files[field] = paths
    return field_files


def _ui_entry(path):
    import folder_paths
    root = os.path.abspath(folder_paths.get_output_directory())
    full = os.path.abspath(path)
    if not full.startswith(root + os.sep):
        return None
    sub = os.path.relpath(os.path.dirname(full), root).replace("\\", "/")
    return {"filename": os.path.basename(full), "subfolder": "" if sub == "." else sub,
            "type": "output"}


def _build(spec, answer, field_files, say):
    """The node's output values and preview, from the answer and the files on disk."""
    import torch
    from comfy_api.latest import InputImpl

    values, ui_images, ui_video, ui_audio = [], [], None, None
    for o in spec["outputs"]:
        kind, field = o["kind"], o["field"]
        raw = answer.get(field)
        paths = field_files.get(field) or []
        if kind == "images":
            tensors = [media.load_image_tensor(p) for p in paths]
            same = [x for x in tensors if x.shape[1:] == tensors[0].shape[1:]]
            if len(same) != len(tensors):
                say("%d of %d pictures came back at another size - the IMAGE output carries "
                    "only the ones matching the first; all are saved"
                    % (len(tensors) - len(same), len(tensors)))
            values += [torch.cat(same, dim=0), "\n".join(paths)]
            ui_images += [e for e in (_ui_entry(p) for p in paths) if e]
        elif kind == "video":
            values += [InputImpl.VideoFromFile(paths[0]), paths[0]]
            ui_video = _ui_entry(paths[0])
        elif kind == "audio":
            from comfy_extras.nodes_audio import load as load_audio
            waveform, rate = load_audio(paths[0])
            values += [{"waveform": waveform.unsqueeze(0), "sample_rate": rate}, paths[0]]
            ui_audio = _ui_entry(paths[0])
        elif kind in ("files", "file"):
            values.append("\n".join(paths))
        elif kind == "string":
            values.append("" if raw is None else str(raw))
        elif kind == "json":
            values.append("" if raw is None else json.dumps(raw, ensure_ascii=False, indent=1))
        elif kind == "int":
            values.append(int(raw) if isinstance(raw, (int, float)) else 0)
        elif kind == "float":
            values.append(float(raw) if isinstance(raw, (int, float)) else 0.0)
        elif kind == "bool":
            values.append(bool(raw))
    if ui_video:
        ui = {"images": [ui_video], "animated": (True,)}
    elif ui_images:
        ui = {"images": ui_images}
    elif ui_audio:
        ui = {"audio": [ui_audio]}
    else:
        ui = None
    return values, ui


def ledger_entries():
    """Every line of output/fal/_requests.jsonl, oldest first. Never raises."""
    try:
        import folder_paths
        path = os.path.join(folder_paths.get_output_directory(), "fal", LEDGER_NAME)
        out = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        return out
    except OSError:
        return []


def _run_hash(spec, payload, uploads):
    """A fingerprint of exactly what would be sent: the fields AND the bytes of every file."""
    import hashlib
    files = [[u["path"], u["index"], hashlib.sha256(u["data"]).hexdigest()] for u in uploads]
    blob = json.dumps({"endpoint": spec["endpoint_id"], "payload": payload, "files": files},
                      sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _previous_run(run_hash):
    """The newest completed run with this fingerprint whose files are all still on disk."""
    for entry in reversed(ledger_entries()):
        if entry.get("event") == "completed" and entry.get("run_hash") == run_hash:
            files = [p for paths in (entry.get("field_files") or {}).values() for p in paths]
            if files and all(os.path.isfile(p) for p in files):
                return entry
    return None


def run_blocking(spec, kw, node_id):
    say = _Reporter(node_id, spec["title"])
    key = client.fal_key()                                   # 1
    payload, uploads, media_seconds = _prepare(spec, kw, say)  # 2

    widget_values = {k: v for k, v in kw.items() if not hasattr(v, "shape")}
    usd, upper, how = pricing.estimate(spec.get("pricing"), widget_values, media_seconds)  # 3
    cost_text = pricing.describe(spec.get("pricing"), usd, upper, how)

    run_hash = _run_hash(spec, payload, uploads)
    if kw.get("reuse_identical_run", True):
        prev = _previous_run(run_hash)
        if prev:
            values, ui = _build(spec, prev.get("result") or {}, prev.get("field_files") or {}, say)
            _ledger("reused", endpoint=spec["endpoint_id"], title=spec["title"],
                    request_id=prev.get("request_id"), run_hash=run_hash)
            say("same inputs as request %s (%s) - reused its result, nothing billed"
                % (prev.get("request_id"), prev.get("time")))
            info = {"endpoint": spec["endpoint_id"], "request_id": prev.get("request_id"),
                    "reused": True, "estimate_usd": 0.0, "estimate": "reused - nothing billed",
                    "files": [p for ps in prev["field_files"].values() for p in ps],
                    "result": prev.get("result")}
            return values + [json.dumps(info, ensure_ascii=False, indent=1)], ui

    cap = float(kw.get("max_cost_usd") if kw.get("max_cost_usd") is not None else DEFAULT_MAX_COST)
    if usd is not None and cap > 0 and usd > cap:
        raise media.InputError(
            "fal %s: estimated cost %s is above max_cost_usd ($%.2f). Nothing was sent. Raise "
            "max_cost_usd on the node (or set it to 0 for no cap) if this is intended."
            % (spec["title"], pricing._money(usd), cap))
    say("estimated %s" % cost_text)

    for i, up in enumerate(uploads):                         # 4
        if _cancelled():
            _interrupt()
        say("uploading %s (%d of %d, %.1f MB)" % (up["name"], i + 1, len(uploads),
                                                   len(up["data"]) / 1e6))
        url = client.upload(key, up["data"], up["type"], up["name"], log=say)
        if up["list"]:
            target = payload
            keys = up["path"].split(".")
            for k in keys[:-1]:
                target = target.setdefault(k, {})
            target.setdefault(keys[-1], []).append(url)
        else:
            _set_path(payload, up["path"], url)
    if _cancelled():
        _interrupt()

    shown = copy.deepcopy(payload)
    say("sending to fal: %s" % json.dumps(shown, ensure_ascii=False)[:600])
    handle = client.submit(key, spec["endpoint_id"], payload, log=say)   # 5
    _ledger("submitted", endpoint=spec["endpoint_id"], title=spec["title"],
            request_id=handle["request_id"], response_url=handle["response_url"],
            status_url=handle["status_url"], estimate_usd=usd, estimate=cost_text,
            run_hash=run_hash)
    started = time.time()

    def on_update(state):
        status = state.get("status")
        if status == "IN_QUEUE":
            where = "queued (position %s)" % state.get("queue_position", "?")
        elif status == "IN_PROGRESS":
            where = "running"
        else:
            where = str(status).lower()
        say("%s · %ds · %s" % (where, time.time() - started, cost_text), console=False)

    try:
        client.wait(key, handle, on_update=on_update, cancelled=_cancelled, log=say)   # 6
    except client.FalCancelled:
        _ledger("cancelled", endpoint=spec["endpoint_id"], request_id=handle["request_id"])
        _interrupt()
    except Exception as exc:                            # noqa: BLE001 - logged, then raised
        _ledger("failed", endpoint=spec["endpoint_id"], request_id=handle["request_id"],
                error=str(exc)[:500])
        raise
    say("finished in %ds - fetching the result" % (time.time() - started))
    try:
        answer = client.result(key, handle, log=say)
        field_files = _download(spec, handle, answer, say)                   # 7
        values, ui = _build(spec, answer, field_files, say)
    except Exception as exc:                            # noqa: BLE001 - logged, then raised
        _ledger("failed", endpoint=spec["endpoint_id"], request_id=handle["request_id"],
                error=str(exc)[:500])
        raise client.FalError(
            "%s\nThe job may still have finished on fal: collect it for free with fal Recover "
            "Result, request id %s." % (exc, handle["request_id"])) from None

    saved = [p for ps in field_files.values() for p in ps]
    info = {"endpoint": spec["endpoint_id"], "request_id": handle["request_id"],
            "estimate_usd": usd, "estimate": cost_text, "seconds": round(time.time() - started, 1),
            "files": saved, "result": answer}
    _ledger("completed", endpoint=spec["endpoint_id"], title=spec["title"],
            request_id=handle["request_id"], run_hash=run_hash, field_files=field_files,
            files=saved, estimate_usd=usd, estimate=cost_text, result=answer)
    say("done in %ds · %s · saved %d file(s)" % (time.time() - started, cost_text, len(saved)))
    return values + [json.dumps(info, ensure_ascii=False, indent=1)], ui


# ---------------------------------------------------------------------------------------
# the class
# ---------------------------------------------------------------------------------------

def build_node_class(spec):
    def define_schema(cls):
        inputs = _schema_inputs(spec)
        return io.Schema(
            node_id=spec["class_key"],
            display_name=display_name(spec),
            category="%s/%s" % (CATEGORY_ROOT, spec["category"]),
            description=description(spec),
            inputs=inputs,
            outputs=_schema_outputs(spec),
            hidden=[io.Hidden.unique_id],
            is_output_node=True,
            is_api_node=True,
            price_badge=_price_badge(spec, inputs),
            search_aliases=spec.get("aliases") or [],
        )

    async def execute(cls, **kwargs):
        node_id = None
        try:
            node_id = cls.hidden.unique_id if cls.hidden is not None else None
        except Exception:                               # noqa: BLE001
            pass
        values, ui = await asyncio.to_thread(run_blocking, spec, kwargs, node_id)
        return io.NodeOutput(*values, ui=ui)

    return type(spec["class_key"], (io.ComfyNode,), {
        "define_schema": classmethod(define_schema),
        "execute": classmethod(execute),
        "FAL_SPEC": spec,
        "__module__": __name__,
    })
