"""The fal concurrency limit: how many paid fal calls run at once, across every fal node.

Runs the REAL node classes' execute() and the real limiter on real asyncio event loops (one
asyncio.run per scenario, exactly as ComfyUI gives each run its own loop). Only the two
halves of a run are replaced: check_run (the free checks) and bill_run (the paid part)
become stand-ins that record how many paid calls overlap. test_end_to_end.py already
proves those two halves against the local fal stand-in, through run_blocking.
"""
import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import MODEL_CLASSES, fal, node  # noqa: E402

limit = sys.modules["arkpack.fal_provider.limit"]
report = []


def check(ok, label, detail=""):
    report.append(("ok" if ok else "FAIL", label, detail))


# ---- 0. every node carries the widget, default 1, as its LAST input --------------------
bad = []
for key, cls in MODEL_CLASSES.items():
    opt = cls.GET_NODE_INFO_V1()["input"].get("optional", {})
    names = list(opt)
    spec = opt.get("max_concurrent")
    if not spec or spec[0] != "INT" or spec[1].get("default") != 1 or spec[1].get("min") != 1 \
            or names[-1] != "max_concurrent":
        bad.append(key)
check(not bad, "every fal node has max_concurrent (INT, default 1, min 1, last input)",
      "%d nodes checked%s" % (len(MODEL_CLASSES), ("; bad: %s" % bad[:5]) if bad else ""))
check("max_concurrent" not in fal.NODE_CLASS_MAPPINGS["ArkFalHistory"].GET_NODE_INFO_V1()["input"].get("optional", {})
      and "max_concurrent" not in fal.NODE_CLASS_MAPPINGS["ArkFalRecover"].GET_NODE_INFO_V1()["input"].get("optional", {}),
      "History and Recover (free) have no max_concurrent")


# ---- stand-ins for the two halves ---------------------------------------------------------
class Tracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.now = self.peak = 0
        self.billed = []            # (label, paid calls running when it started)
        self.said = []

    def check_run(self, spec, kw, node_id):
        if kw.get("_check_fails"):
            raise ValueError("cap exceeded (test)")
        if kw.get("_reused"):
            return {"done": (["reused"], {})}
        label = kw["_label"]
        return {"spec": spec, "say": lambda text, console=True: self.said.append((label, text)),
                "_label": label, "_kw": kw}

    def bill_run(self, ready):
        kw = ready["_kw"]
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)
            self.billed.append((kw["_label"], self.now))
        try:
            time.sleep(kw.get("_seconds", 0.15))
            if kw.get("_bill_fails"):
                raise RuntimeError("fal said no (test)")
            return ["ok"], {}
        finally:
            with self.lock:
                self.now -= 1


CLS = MODEL_CLASSES["ArkFal_openai_gpt_image_2_edit"]


def scenario(nodes, stagger=0.02):
    """nodes: list of kw dicts. Starts them like ComfyUI does: one task each, same loop."""
    t = Tracker()
    node.check_run, node.bill_run = t.check_run, t.bill_run

    async def main():
        tasks = []
        for kw in nodes:
            tasks.append(asyncio.create_task(CLS.execute(**kw)))
            await asyncio.sleep(stagger)
        return await asyncio.gather(*tasks, return_exceptions=True)

    started = time.time()
    results = asyncio.run(main())
    return t, results, time.time() - started


real_check, real_bill = node.check_run, node.bill_run
try:
    # 1. default 1: strictly one after another
    t, res, took = scenario([{"_label": "n%d" % i, "max_concurrent": 1} for i in range(5)])
    check(t.peak == 1 and len(t.billed) == 5 and all(not isinstance(r, BaseException) for r in res),
          "5 nodes at max_concurrent 1 -> one at a time, all 5 run", "peak %d, took %.2fs" % (t.peak, took))
    check([b[0] for b in t.billed] == ["n0", "n1", "n2", "n3", "n4"], "they run in the order they were started",
          str([b[0] for b in t.billed]))
    check(any("waiting for its turn - 1 fal call running, max_concurrent 1" in s for _, s in t.said),
          "a waiting node says so on the node", next((s for _, s in t.said), ""))

    # 2. widget missing entirely (an old saved canvas) -> the default, 1
    t, res, _ = scenario([{"_label": "n%d" % i} for i in range(4)])
    check(t.peak == 1 and len(t.billed) == 4, "no max_concurrent value at all -> 1", "peak %d" % t.peak)

    # 3. raised to 3
    t, res, took = scenario([{"_label": "n%d" % i, "max_concurrent": 3} for i in range(6)])
    check(t.peak == 3 and len(t.billed) == 6, "6 nodes at max_concurrent 3 -> 3 together", "peak %d, took %.2fs" % (t.peak, took))

    # 4. raised to 32 (the maximum) with 8 nodes -> all 8 together
    t, res, _ = scenario([{"_label": "n%d" % i, "max_concurrent": 32} for i in range(8)], stagger=0)
    check(t.peak == 8, "8 nodes at max_concurrent 32 -> all 8 together", "peak %d" % t.peak)

    # 5. mixed values: the node set to 1 only starts when nothing else runs
    t, res, _ = scenario([{"_label": "a", "max_concurrent": 3}, {"_label": "b", "max_concurrent": 3},
                          {"_label": "solo", "max_concurrent": 1}, {"_label": "c", "max_concurrent": 3}])
    solo = [n for lbl, n in t.billed if lbl == "solo"]
    check(t.peak <= 3 and solo == [1] and len(t.billed) == 4,
          "mixed 3/3/1/3 -> the '1' node runs alone, never more than 3", str(t.billed))

    # 6. a paid call fails at max_concurrent 1 -> the ones waiting are never started
    t, res, _ = scenario([{"_label": "boom", "max_concurrent": 1, "_bill_fails": True}] +
                         [{"_label": "n%d" % i, "max_concurrent": 1} for i in range(3)])
    stopped = [r for r in res[1:] if isinstance(r, limit.FalRunStopped)]
    check(len(t.billed) == 1 and len(stopped) == 3 and isinstance(res[0], RuntimeError),
          "first paid call fails -> the 3 waiting are not sent (nothing billed)",
          "billed %s; %s" % ([b[0] for b in t.billed], str(stopped[0])[:90] if stopped else res))

    # 7. same at 2: the one already running finishes, the waiting two never start
    t, res, _ = scenario([{"_label": "boom", "max_concurrent": 2, "_bill_fails": True, "_seconds": 0.1},
                          {"_label": "running", "max_concurrent": 2, "_seconds": 0.4},
                          {"_label": "w1", "max_concurrent": 2}, {"_label": "w2", "max_concurrent": 2}])
    check([b[0] for b in t.billed] == ["boom", "running"] and
          not isinstance(res[1], BaseException) and all(isinstance(r, limit.FalRunStopped) for r in res[2:]),
          "at 2: a failure lets the running one finish, stops the waiting ones", str([b[0] for b in t.billed]))

    # 8. a FREE check fails (e.g. over max_cost_usd) -> the waiting ones are not started either
    t, res, _ = scenario([{"_label": "long", "max_concurrent": 1, "_seconds": 0.4},
                          {"_label": "w1", "max_concurrent": 1},
                          {"_label": "capped", "max_concurrent": 1, "_check_fails": True},
                          {"_label": "w2", "max_concurrent": 1}])
    check([b[0] for b in t.billed] == ["long"] and isinstance(res[2], ValueError)
          and isinstance(res[1], limit.FalRunStopped) and isinstance(res[3], limit.FalRunStopped),
          "a node refused by its free checks -> the waiting ones are not sent", str([b[0] for b in t.billed]))

    # 9. a reused (already paid) run never waits in the line
    t = Tracker()
    node.check_run, node.bill_run = t.check_run, t.bill_run

    async def reuse_while_busy():
        busy = asyncio.create_task(CLS.execute(_label="busy", max_concurrent=1, _seconds=0.5))
        await asyncio.sleep(0.05)
        t0 = time.time()
        out = await CLS.execute(_reused=True, max_concurrent=1)
        waited = time.time() - t0
        await busy
        return out, waited
    out, waited = asyncio.run(reuse_while_busy())
    check(waited < 0.2 and [b[0] for b in t.billed] == ["busy"],
          "a reused run returns at once, even while a paid call holds the only slot", "%.2fs" % waited)

    # 10. a new run starts clean after a failed one (ComfyUI: one event loop per run)
    t, res, _ = scenario([{"_label": "n%d" % i, "max_concurrent": 1} for i in range(3)])
    check(len(t.billed) == 3, "the next run after a failure is not blocked by it", str([b[0] for b in t.billed]))

    # 11. a waiting node cancelled (the run ended) leaks no slot
    t = Tracker()
    node.check_run, node.bill_run = t.check_run, t.bill_run

    async def cancel_waiter():
        a = asyncio.create_task(CLS.execute(_label="a", max_concurrent=1, _seconds=0.3))
        await asyncio.sleep(0.05)
        b = asyncio.create_task(CLS.execute(_label="b", max_concurrent=1))
        await asyncio.sleep(0.05)
        b.cancel()
        await asyncio.gather(a, b, return_exceptions=True)
        after = limit.running()
        c = await CLS.execute(_label="c", max_concurrent=1)
        return after, c
    after, c = asyncio.run(cancel_waiter())
    check(after == 0 and [b[0] for b in t.billed] == ["a", "c"],
          "a cancelled waiter is never billed and leaves no slot taken", "running after: %d, billed %s" % (after, [b[0] for b in t.billed]))

    # 12. out-of-range values are clamped, not trusted
    check(limit.clamp(0) == 1 and limit.clamp(-5) == 1 and limit.clamp(999) == 32 and limit.clamp("x") == 1
          and limit.clamp(None) == 1, "max_concurrent is clamped to 1..32")
finally:
    node.check_run, node.bill_run = real_check, real_bill

fails = [r for r in report if r[0] != "ok"]
for status, label, detail in report:
    print("%-4s %s%s" % (status, label, ("  [%s]" % detail) if detail else ""))
print("\n%d checks, %d failures" % (len(report), len(fails)))
sys.exit(1 if fails else 0)
