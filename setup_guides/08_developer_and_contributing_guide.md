# Developer & Contributing Guide

Architecture rules and extension patterns for adding new nodes and providers to the **Arkennemasis** pack.

---

## 1. Adding a New Provider in 3 Steps

### Step 1: Create the Sub-Package
Create a new directory (e.g. `ollama_provider/`) containing a `nodes.py` file exposing class mappings:

```python
NODE_CLASS_MAPPINGS = {"OllamaLLM": OllamaLLM}
NODE_DISPLAY_NAME_MAPPINGS = {"OllamaLLM": "arkennemasis Ollama LLM"}
```

### Step 2: Set Node Category
Assign the node's menu placement under the `arkennemasis/` hierarchy:

```python
class OllamaLLM:
    CATEGORY = "arkennemasis/LLM"  # or /Image Gen, /Utility, /Video, /Audio
```

### Step 3: Register in `__init__.py`
Add an isolated loader function in `__init__.py`:

```python
def _ollama():
    from .ollama_provider.nodes import (
        NODE_CLASS_MAPPINGS as c, NODE_DISPLAY_NAME_MAPPINGS as d,
    )
    return c, d

_load("ollama provider", _ollama)
```

The defensive `_load()` wrapper catches any import exceptions, ensuring that an issue in an optional module never prevents other nodes from loading.

---

## 2. Core Reusable Utilities

Avoid reinventing boilerplate; use the shared modules in `common/`:

| Need | Reusable Helper |
| :--- | :--- |
| **API key resolution** | `from ..common.keys import resolve_key` (field → env → `.env`) |
| **ComfyUI Image ↔ API Data** | `collect_images_to_data_uris()`, `bytes_list_to_image_tensor()` |
| **Stream / text normalization** | `output_to_text()` |
| **Concurrency throttle** | `async with serial_lock(): ...` (`common/throttle.py`) |
| **Retry logic** | `with_retry(fn)` (retries transient 429/5xx errors) |
| **Lazy branch skipping** | `ExecutionBlocker(None)` (`common/shot_selector.py`) |
| **Auto-numbering output dirs** | `next_run_folder(parent, name)` (`common/run_folder.py`) |

---

## 3. Four Golden Rules

1. **Class keys are permanent:** `"OllamaLLM"` is the ID stored in workflow JSON files. Never rename a class key; display names and categories are cosmetic and safe to modify.
2. **Import dependencies inside functions:** Avoid top-level module imports for heavy or optional third-party packages so missing dependencies do not crash startup.
3. **Append new widgets at the end:** ComfyUI serializes widget values positionally. Inserting a widget into the middle shifts indexes and corrupts saved user workflows. Adding *sockets* is always safe.
4. **Register long-running nodes for activity badges:** If a node executes network requests or takes more than a moment, add its class key to `ANIMATED_NODES` in `web/activity.js` to render the active cooking spinner.
