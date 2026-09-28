"""The HyperFrames CLI, found or installed, and one render call around it.

HyperFrames (github.com/heygen-com/hyperframes, Apache-2.0) renders an HTML page to MP4:
headless Chrome seeks every frame, FFmpeg encodes them, and the same project always gives
the same video. It is a Node tool, not a ComfyUI pack, so it is NOT cloned into
``custom_nodes`` — ComfyUI would try to import it as Python and log a failure. It lives
here as a pinned npm dependency instead: ``runtime/package.json`` and its lock file are
committed, ``runtime/node_modules`` is not, and the first render on a new machine runs
``npm ci`` to reproduce exactly the versions tested here.

Needs Node.js 22+ and FFmpeg/FFprobe on PATH. Chrome is fetched by HyperFrames itself on
first render (chrome-headless-shell, into the user's puppeteer cache).

Two measured facts shape the render call:

* **Media must live inside the project.** An absolute path or a ``file://`` URL is looked
  up relative to the project and fails as ``VIDEO_SOURCE_UNRENDERABLE`` — so callers copy
  or hard-link their clips into ``<project>/assets`` first.
* **Output goes to a FILE, never a pipe.** The render prints a progress bar per frame; an
  undrained ``stdout=PIPE`` fills its buffer and the child blocks forever. Same root cause
  as the Chrome and ffmpeg hangs recorded for ArkWebShot.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

RUNTIME_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runtime")
_BIN = os.path.join(RUNTIME_DIR, "node_modules", ".bin")
GSAP_JS = os.path.join(RUNTIME_DIR, "node_modules", "gsap", "dist", "gsap.min.js")
QUALITIES = ["standard", "draft", "looks", "high", "delivery"]


def _cli():
    return os.path.join(_BIN, "hyperframes.cmd" if os.name == "nt" else "hyperframes")


def _env():
    env = dict(os.environ)
    # Anonymous usage counters are on by default upstream; a render farm has no business
    # reporting home.
    env["HYPERFRAMES_NO_TELEMETRY"] = "1"
    return env


def _node_major():
    node = shutil.which("node")
    if not node:
        return None
    out = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    match = re.match(r"v(\d+)", out)
    return int(match.group(1)) if match else None


def ensure_runtime():
    """Path to the CLI, installing the pinned runtime first if this machine lacks it."""
    if os.path.isfile(_cli()) and os.path.isfile(GSAP_JS):
        return _cli()
    major = _node_major()
    if major is None or major < 22:
        raise RuntimeError(
            "HyperFrames needs Node.js 22 or newer on PATH (found: %s). Install it from "
            "nodejs.org, restart ComfyUI, and run again — the CLI then installs itself."
            % ("none" if major is None else "v%d" % major))
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise RuntimeError("HyperFrames needs %s on PATH." % tool)
    npm = shutil.which("npm")
    if not npm:
        raise RuntimeError("HyperFrames: node is installed but npm is not on PATH.")
    command = "ci" if os.path.isfile(os.path.join(RUNTIME_DIR, "package-lock.json")) else "install"
    log = os.path.join(RUNTIME_DIR, "install.log")
    print("[arkennemasis] HyperFrames: first use on this machine — npm %s in %s"
          % (command, RUNTIME_DIR))
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        proc = subprocess.run([npm, command, "--no-audit", "--no-fund"], cwd=RUNTIME_DIR,
                              stdout=fh, stderr=subprocess.STDOUT, env=_env(), timeout=900)
    if proc.returncode != 0 or not os.path.isfile(_cli()):
        raise RuntimeError("HyperFrames: npm %s failed — see %s" % (command, log))
    return _cli()


def render(project_dir, output_path, fps=25, quality="standard", workers=0, timeout=1800):
    """Render ``<project_dir>/index.html`` to ``output_path``. Returns the CLI's summary."""
    cli = ensure_runtime()
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    args = [cli, "render", "-o", os.path.abspath(output_path), "--fps", str(int(fps)),
            "--quality", quality]
    if int(workers) > 0:
        args += ["--workers", str(int(workers))]
    log = os.path.join(project_dir, "render.log")
    started = time.time()
    with open(log, "w", encoding="utf-8", errors="replace") as fh:
        try:
            proc = subprocess.run(args, cwd=project_dir, stdout=fh, stderr=subprocess.STDOUT,
                                  stdin=subprocess.DEVNULL, env=_env(), timeout=int(timeout))
        except subprocess.TimeoutExpired:
            raise RuntimeError("HyperFrames render passed %ds — see %s" % (timeout, log))
    with open(log, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    if proc.returncode != 0 or not os.path.isfile(output_path):
        tail = "\n".join(text.strip().splitlines()[-12:])
        raise RuntimeError("HyperFrames render failed (exit %s):\n%s\nfull log: %s"
                           % (proc.returncode, tail, log))
    # The CLI's own one-line summary: capture path, GPU mode and per-stage timings.
    stages = next((ln.strip() for ln in text.splitlines() if "capture ·" in ln), "")
    return "%.1fs wall | %s" % (time.time() - started, stages)
