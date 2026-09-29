"""How many paid fal calls may run at the same time, across every fal node in one run.

ComfyUI runs async nodes concurrently, so a canvas with twenty fal nodes would otherwise
send twenty paid requests in the same second. Each node carries ``max_concurrent``
(default 1). A node holds a slot only for the part that costs money (upload, submit, wait,
download) and starts that part only while FEWER than its own ``max_concurrent`` fal calls
are running:

* everything at 1 (the default): one paid call at a time, one after another.
* raise it on the nodes you want side by side: at 3, up to three run together.
* mixed values stay well defined: a node set to 1 waits until nothing else is running.

The free checks (key, inputs, price, cap, reusing an identical earlier run) run before a
slot is taken, so a refused or reused node never waits in the line. Waiting happens in
asyncio, not in a thread, so a long line of waiting nodes holds no worker threads.

**Once a fal node fails, no fal call that has not started yet will start in that run.**
ComfyUI stops the run on the first failure but does not cancel the other async nodes until
the run is over, and a failing node frees its slot a moment before that - long enough for
the next node in line to upload and submit. :func:`mark_failed` closes that gap.

ComfyUI gives every run its own event loop (``asyncio.run`` per prompt), so "per loop" here
means "per run": each run starts with an empty line and no failure recorded.
"""

import asyncio

DEFAULT = 1
MAXIMUM = 32

_state = None


class FalRunStopped(RuntimeError):
    """A fal node was not started because another fal node in the same run failed."""


def _current():
    global _state
    loop = asyncio.get_running_loop()
    s = _state
    if s is None or s["loop"] is not loop:
        s = _state = {"loop": loop, "cond": asyncio.Condition(), "running": 0, "failed": None}
    return s


def clamp(limit):
    try:
        return min(MAXIMUM, max(1, int(limit)))
    except (TypeError, ValueError):
        return DEFAULT


def running():
    """Paid fal calls running right now in the current run (0 outside a run)."""
    s = _state
    return s["running"] if s is not None else 0


def mark_failed(title):
    """Record that a fal node in this run failed, so the ones still waiting never start."""
    s = _current()
    if s["failed"] is None:
        s["failed"] = title


class slot:
    """``async with slot(max_concurrent, title, on_wait):`` holds one fal slot for the billed part.

    ``on_wait(running, limit)`` is called once if the node has to wait for its turn. A node
    that leaves its slot with an error records the failure BEFORE it wakes the line.
    """

    def __init__(self, limit, title="a fal node", on_wait=None):
        self.limit = clamp(limit)
        self.title = title
        self.on_wait = on_wait
        self.state = None

    def _check(self):
        failed = self.state["failed"]
        if failed is not None:
            raise FalRunStopped(
                "fal: not started - '%s' failed earlier in this run, so no further fal call "
                "was sent (nothing billed for this node)." % failed)

    async def __aenter__(self):
        s = self.state = _current()
        async with s["cond"]:
            self._check()
            if s["running"] >= self.limit and self.on_wait:
                self.on_wait(s["running"], self.limit)
            await s["cond"].wait_for(lambda: s["running"] < self.limit or s["failed"] is not None)
            self._check()
            s["running"] += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        s = self.state
        if exc_type is not None and not issubclass(exc_type, asyncio.CancelledError):
            if s["failed"] is None:
                s["failed"] = self.title
        async with s["cond"]:
            s["running"] -= 1
            s["cond"].notify_all()
        return False
