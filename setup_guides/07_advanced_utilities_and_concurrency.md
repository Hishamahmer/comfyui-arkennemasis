# Advanced Utilities, Rate Limits & Concurrency Guide

Technical reference for Arkennemasis batch utilities, concurrency locks, and cost-prevention nodes.

---

## 1. Driving Multiple Image Gen Nodes (`Image Gen Settings`)

ComfyUI natively rejects linking a `STRING` output into a `COMBO` widget. To control `aspect_ratio`, `quality`, or `timeout_seconds` across dozens of generator nodes without configuring each individually:

1. Add one **`arkennemasis Image Gen Settings`** node.
2. Connect its `ARK_IMAGE_SETTINGS` output to the optional `settings` input socket of downstream `Codex Image Gen` or `Replicate Image Gen` nodes.
3. Any setting left as `"use node's own"` falls back to that specific node's widgets, allowing you to establish global defaults with selective per-node overrides.

---

## 2. Rate Limits & Concurrency (`run_mode`)

ComfyUI executes asynchronous nodes concurrently. When triggering 10–30 branches at once, parallel API calls can exceed provider rate limits (e.g. Replicate accounts under $5 credit drop to 6 req/min, returning 429 errors).

Both API generator nodes provide a **`run_mode`** widget:

| `run_mode` | Behavior |
| :--- | :--- |
| **`one at a time`** (Default) | Uses an internal `asyncio.Lock` to serialize every Arkennemasis API call in the graph sequentially. |
| **`all at once`** | Concurrently executes branches up to the threshold set in `max_concurrent` (default: 2; `0` = uncapped). |

---

## 3. Automated Retries & Fault Tolerance

A standard Python exception crashes an entire ComfyUI execution queue. To prevent transient network hiccups from ruining a long batch run, Arkennemasis includes automatic backoff retries (`common/throttle.py`):

| Error Type | Behavior |
| :--- | :--- |
| **`429 Rate Limit`** | Backs off exponentially, honoring `Retry-After` response headers. |
| **`500 / 502 / 503 / 504`** | Automatically retries up to 6 times. |
| **Dropped connections / timeouts** | Retries immediately after a brief pause. |
| **Content moderation / 400 bad request** | Fails immediately without retrying. |
| **Authentication failure (401 / 403)** | Fails immediately. |

---

## 4. Selective Branch Execution (`Shot Selector`)

When building complex workflows with 20–30 branches, you often want to test only 2 or 3 shots before running a full production batch.

* **Lazy Evaluation:** `Shot Selector` uses ComfyUI's lazy evaluation protocol. Unselected branches return `ExecutionBlocker(None)`, which prevents upstream generator nodes from executing. **Unselected branches are never evaluated and never billed.**
* **Modes:**
  * `first N in order`: Runs branches `1..N` (no seed needed).
  * `random from seed`: Generates an unbiased sample of N shots across the entire set.

---

## 5. Output Organization (`Run Folder`)

The **`arkennemasis Run Folder`** node evaluates `<parent_dir>/<folder_name>_<NNN>` dynamically:
* Automatically scans sibling folders and increments to the next available number (`run_001`, `run_002`).
* Overcomes the limitation of native timestamp tokens, ensuring that a 30-minute render lands all files inside a single cohesive folder rather than scattering files across multiple minute-based directories.

---

## 6. Caption Sidecars (`Text File Save`)

The **`arkennemasis Text File Save`** node writes `<folder_path>/<filename>.<extension>`.
Give it the same folder and filename stem that the image save node uses, and it writes paired files alongside each image:
```
shot_001.png
shot_001.txt
```

* **Execution Safety:** Wire the generating image into `images`: this binds the text save to that specific branch. If a branch is skipped by an upstream gate or shot selector, no orphan `.txt` file is ever written.
* **Atomic Writes:** Writes to a `.part` temporary file and performs an atomic replace (`os.replace`) to ensure no corrupted or partially written files appear on disk.
