"""fal History - every fal run this install has made, and any finished one loaded back for free.

Reads output/fal/_requests.jsonl (written by every fal node). The node shows the most recent
runs - when, which model, the estimate, what happened, how many files - and loads the one
you pick (``pick`` 0 = the newest) straight from output/fal, so a result you already paid
for can be used again anywhere in the graph without calling fal. A run that was submitted
but never collected (ComfyUI restarted, the download failed) is listed with its request id
for fal Recover Result.
"""

from __future__ import annotations

import asyncio
import json
import os

import torch
from comfy_api.latest import io

from . import media
from .node import CATEGORY_ROOT, _Reporter, _ui_entry, ledger_entries

ALL = "all models"
SHOWN = 25


def runs(model=ALL):
    """One row per request, newest first, merged from the log's events."""
    by_id, order = {}, []
    for e in ledger_entries():
        rid = e.get("request_id")
        if not rid or e.get("event") == "reused":
            continue
        row = by_id.get(rid)
        if row is None:
            row = by_id[rid] = {"request_id": rid, "endpoint": e.get("endpoint"),
                                "title": e.get("title") or e.get("endpoint"), "status": "sent",
                                "time": e.get("time"), "estimate": e.get("estimate"),
                                "estimate_usd": e.get("estimate_usd"), "files": [],
                                "result": None, "error": None}
            order.append(rid)
        event = e.get("event")
        if event == "completed":
            row.update(status="completed", files=e.get("files") or [], result=e.get("result"),
                       field_files=e.get("field_files") or {})
        elif event in ("cancelled", "failed"):
            if row["status"] != "completed":
                row["status"] = event
                row["error"] = e.get("error")
        if e.get("title"):
            row["title"] = e["title"]
    rows = [by_id[r] for r in reversed(order)]
    if model != ALL:
        rows = [r for r in rows if r["title"] == model or r["endpoint"] == model]
    return rows


def table(rows):
    lines = []
    for i, r in enumerate(rows[:SHOWN]):
        usd = r.get("estimate_usd")
        cost = "?" if usd is None else ("$%.3f" % usd if usd < 0.1 else "$%.2f" % usd)
        state = r["status"]
        if state != "completed":
            state += " - Recover: %s" % r["request_id"]
        lines.append("#%-2d %s  %-34s ~%-7s %s  (%d file%s)"
                     % (i, (r.get("time") or "")[5:16].replace("T", " "), (r["title"] or "")[:34],
                        cost, state, len(r["files"]), "" if len(r["files"]) == 1 else "s"))
    return "\n".join(lines) or "No fal runs recorded yet."


def _pick(pick, model, node_id):
    say = _Reporter(node_id, "History")
    rows = runs(model)
    text = table(rows)
    say(text, console=False)
    if not rows:
        return text, None, None, "", "{}", None
    if pick >= len(rows):
        raise ValueError("fal History: pick %d, but there are only %d runs%s."
                         % (pick, len(rows), "" if model == ALL else " for " + model))
    row = rows[pick]
    if row["status"] != "completed":
        raise ValueError("fal History: run #%d (%s) was never collected - put request id %s into "
                         "fal Recover Result to download it for free."
                         % (pick, row["title"], row["request_id"]))
    present = [p for p in row["files"] if os.path.isfile(p)]
    images = [p for p in present if os.path.splitext(p)[1].lower() in (".png", ".jpg", ".jpeg", ".webp")]
    videos = [p for p in present if os.path.splitext(p)[1].lower() in (".mp4", ".mov", ".webm")]
    missing = len(row["files"]) - len(present)
    if missing:
        say("%d file(s) of run #%d are no longer on disk - fal Recover Result can fetch them again"
            % (missing, pick))

    image_out = None
    if images:
        tensors = [media.load_image_tensor(p) for p in images]
        same = [t for t in tensors if t.shape[1:] == tensors[0].shape[1:]]
        image_out = torch.cat(same, dim=0)
    video_out = None
    if videos:
        from comfy_api.latest import InputImpl
        video_out = InputImpl.VideoFromFile(videos[0])
    shown = [e for e in (_ui_entry(p) for p in (videos[:1] or images)) if e]
    ui = ({"images": shown, "animated": (True,)} if videos and shown else
          {"images": shown} if shown else None)
    detail = {k: row.get(k) for k in ("request_id", "endpoint", "title", "time", "estimate",
                                      "files", "result")}
    return text, image_out, video_out, "\n".join(present), json.dumps(detail, indent=1), ui


class ArkFalHistory(io.ComfyNode):
    MODEL_TITLES = [ALL]          # filled in by the package once every model file has loaded

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ArkFalHistory",
            display_name="arkennemasis fal · History (free)",
            category="%s/Tools" % CATEGORY_ROOT,
            description=__doc__,
            inputs=[
                io.Int.Input("pick", default=0, min=0, max=100000,
                             tooltip="Which run to load: 0 = the newest, 1 = the one before..."),
                io.Combo.Input("model", options=cls.MODEL_TITLES, default=ALL,
                               tooltip="Only list runs of this model."),
            ],
            outputs=[io.String.Output("history"), io.Image.Output("images"),
                     io.Video.Output("video"), io.String.Output("file_paths"),
                     io.String.Output("run_json")],
            hidden=[io.Hidden.unique_id],
            is_output_node=True,
        )

    @classmethod
    def fingerprint_inputs(cls, pick=0, model=ALL):
        # re-run whenever the log changes, so the list is never a stale cached copy
        try:
            import folder_paths
            path = os.path.join(folder_paths.get_output_directory(), "fal", "_requests.jsonl")
            return "%s|%s|%s" % (pick, model, os.path.getmtime(path))
        except OSError:
            return "%s|%s|none" % (pick, model)

    @classmethod
    async def execute(cls, pick=0, model=ALL):
        node_id = cls.hidden.unique_id if cls.hidden is not None else None
        text, images, video, paths, detail, ui = await asyncio.to_thread(_pick, int(pick), model, node_id)
        if images is None:
            images = torch.zeros(1, 8, 8, 3)          # the pack's "no picture" tile
        return io.NodeOutput(text, images, video, paths, detail, ui=ui)
