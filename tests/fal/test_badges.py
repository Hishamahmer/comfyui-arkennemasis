"""The live price badge (JSONata, run in the real jsonata engine via Node) must equal the
Python estimate the node enforces max_cost_usd with, for the same settings.

Needs Node.js on PATH; installs the jsonata npm package into the test dir on first run.
The frontend normalises widget values before evaluating (COMBO/STRING -> trimmed
lower-case, numbers and booleans as-is); the contexts here are built the same way.
"""
import itertools
import json
import os
import random
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import MODEL_CLASSES, WORK, pricing  # noqa: E402

TOOLS = os.path.join(WORK, "tools")
if not os.path.isdir(os.path.join(TOOLS, "node_modules", "jsonata")):
    os.makedirs(TOOLS, exist_ok=True)
    subprocess.run("npm init -y >nul 2>&1 & npm install jsonata@2 --silent", cwd=TOOLS, shell=True, check=True)

random.seed(7)
PER_MODEL = 60
cases, meta = [], []
for key, cls in sorted(MODEL_CLASSES.items()):
    spec, info = cls.FAL_SPEC, cls.GET_NODE_INFO_V1()
    badge = info.get("price_badge")
    est = (spec.get("pricing") or {}).get("estimate") or {}
    if not badge or not est.get("per"):          # no estimate by design: the badge is a label
        continue
    inputs = dict(info["input"].get("required", {}), **info["input"].get("optional", {}))
    widgets = [w["name"] for w in badge["depends_on"]["widgets"]]
    types = {w["name"]: w["type"] for w in badge["depends_on"]["widgets"]}
    choices = {}
    for w in widgets:
        s, t = inputs[w], types[w]
        if t == "COMBO":
            choices[w] = list(s[1]["options"]) if isinstance(s[0], str) else list(s[0])
        elif t == "BOOLEAN":
            choices[w] = [True, False]
        elif t == "INT":
            lo, hi = s[1].get("min", 0), s[1].get("max", 4)
            pool = {lo, hi, (lo + hi) // 2, 1, 3, 1024, 1536, 700, 2000, 5, 8, 12}
            choices[w] = sorted(v for v in pool if lo <= v <= hi)
        elif t == "FLOAT":
            lo, hi = s[1].get("min", 0.0), s[1].get("max", 10.0)
            choices[w] = sorted({lo, hi, (lo + hi) / 2, 1.0, 5.0} & {x for x in (lo, hi, (lo + hi) / 2, 1.0, 5.0) if lo <= x <= hi})
        else:                                           # STRING: text lengths
            choices[w] = ["", "hello there", "x" * 1500 + "  "]
    socks = badge["depends_on"]["inputs"]
    combos = list(itertools.product(*[choices[w] for w in widgets])) or [()]
    random.shuffle(combos)
    for combo in combos[:PER_MODEL]:
        for connected in ([False, True] if socks else [False]):
            values = dict(zip(widgets, combo))
            ctx_w = {w: (v if types[w] in ("INT", "FLOAT", "BOOLEAN") else str(v).strip().lower())
                     for w, v in values.items()}
            ctx = {"widgets": ctx_w, "inputs": {s: {"connected": connected and s == socks[0]} for s in socks},
                   "inputGroups": {}}
            q = est.get("quantity") or {}
            media_mode = est.get("per") in ("second", "minute", "hour", "kchar") and \
                ("media" in q or "words" in q or "unknown" in q)
            media_seconds = {socks[0]: 1e-9} if connected else {}
            if media_mode:            # the badge shows the RATE: compare with one unit's cost
                unit = {"second": 1.0, "minute": 60.0, "hour": 3600.0}.get(est["per"], 1000.0)
                probe = dict(est, quantity={"media": ["__probe"]}, round_up=False)
                probe.pop("first", None)
                media_seconds["__probe"] = unit
                usd, upper, _ = pricing.estimate({"estimate": probe}, values, media_seconds)
            else:
                usd, upper, _ = pricing.estimate(spec["pricing"], values, media_seconds)
            cases.append({"expr": badge["expr"], "ctx": ctx})
            meta.append({"key": key, "values": values, "usd": usd, "upper": upper, "media": media_mode})

inp = os.path.join(WORK, "badge_cases.json")
json.dump(cases, open(inp, "w", encoding="utf-8"))
runner = os.path.join(WORK, "badge_eval.js")
open(runner, "w", encoding="utf-8").write(r"""
const jsonata = require(process.argv[2] + '/node_modules/jsonata');
const cases = require(process.argv[3]);
(async () => {
  const out = [];
  for (const c of cases) {
    try { out.push(await jsonata(c.expr).evaluate(c.ctx)); } catch (e) { out.push({error: String(e.message || e)}); }
  }
  process.stdout.write(JSON.stringify(out));
})();
""")
res = json.loads(subprocess.run(["node", runner, TOOLS, inp], capture_output=True, text=True,
                                encoding="utf-8", check=True).stdout)
fails = []
for m, r in zip(meta, res):
    got = re.search(r"\$([0-9,]+\.[0-9]+)", r.get("text", "")) if isinstance(r, dict) else None
    if not got or ("$" + got.group(1)) != pricing._money(m["usd"]):
        fails.append((m, r))
    elif not m["media"] and not r["text"].startswith("≤" if m["upper"] else "≈"):
        fails.append((m, r))
models = len({m["key"] for m in meta})
print("badge cases: %d across %d models | failures: %d" % (len(meta), models, len(fails)))
for m, r in fails[:20]:
    print("FAIL", m["key"], m["values"], "python=%s" % pricing._money(m["usd"]), "badge=%s" % r)
sys.exit(1 if fails else 0)
