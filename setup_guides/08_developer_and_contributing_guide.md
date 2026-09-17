# Developer & Contributing Guide

Architecture rules and extension patterns for adding new nodes and providers to the **Arkennemasis** pack.

## 1. Repository Structure

```
comfyui-arkennemasis/
│
├── __init__.py              THE HUB — loads every module and merges their node maps.
│                            The only file you touch when adding something.
│
├── common/                  SHARED CODE — written once, reused by every module
│   ├── keys.py                 resolve_key(): node field → env var → .env
│   ├── image_utils.py          tensor ↔ data-URI ↔ bytes, text normalising
│   ├── throttle.py             serial_lock(), concurrency_gate(), with_retry()
│   ├── banner.py               the load-time ASCII banner (ARK_BANNER=0 silences it)
│   ├── system_instructions.py  the System Instructions node
│   ├── shot_selector.py        the Shot Selector node (lazy input + ExecutionBlocker)
│   ├── subject_line.py         the Subject Line node (gender stated once, not per prompt)
│   ├── text_file_save.py       the Text File Save node (caption sidecars)
│   ├── run_folder.py           the Run Folder node (auto-numbered per run)
│   ├── scene_list.py           the loop: OUTPUT_IS_LIST fans a scene plan out
│   ├── hailuo_scene.py         one scene start to finish, freed before the next
│   ├── ass_captions.py         font discovery + the five subtitle styles as ASS
│   ├── caption_style.py        the Caption Style node
│   └── video_assemble.py       the aggregate end: join, level, duck, burn
│
├── codex_provider/          ChatGPT/Codex OAuth — no API key
│   ├── auth.py                 reads `codex login` creds, refreshes + persists them
│   └── nodes.py                Codex Image Gen + Codex Login Status
│
├── replicate_provider/      ONE PROVIDER = ONE FOLDER
│   ├── nodes.py                Replicate LLM + Image Gen nodes
│   └── settings.py             the shared Image Gen Settings node
│
├── variation/               PRODUCT-VARIATION PIPELINE — a use case, not a provider
│   ├── schema.py               the canonical 3-table schema + every validator
│   ├── colour.py               sRGB↔Lab, ΔE2000, robust sampling, shading-preserving recolour
│   ├── intake.py               Sheet Probe + Variation Intake
│   ├── spec_library.py         download, cache and hash every reference
│   ├── plate_lock.py           freeze and measure the base plate; Region Mask
│   ├── recipe.py               Tier 0 brief, compile+validate, and the human gate
│   ├── cells.py                the N-axis cartesian product, and one cell by index
│   ├── prompt_build.py         substitution-only prompts + the constancy audit
│   ├── job_store.py            durable job records, lazy resume, the run report
│   ├── recolour.py             the non-generative path and the per-axis router
│   ├── verify.py               identity / frame / colour / bleed, and calibration
│   └── deliver.py              format ladder, review board, store import file
│
├── fonts/                   CAPTION FONTS — 13 OFL/Apache families, see fonts/README.md
│
├── example workflows/       canvas .json exports demonstrating the nodes
│
├── setup_guides/            modular setup guides and documentation
│
├── web/                     FRONT-END (auto-served via WEB_DIRECTORY)
│   └── activity.js             running / "cooking" badge on the node
│
├── requirements.txt         dependencies
├── pyproject.toml           ComfyUI-Manager metadata
├── .env.example             key template users copy to .env
├── README.md
└── LICENSE
```

---

## 2. Menu Category Architecture

Menu categories are set per node class, so **one provider folder can feed several categories** (Replicate serves both `LLM` and `Image Gen`):

```
arkennemasis/
├── LLM         ← replicate_provider · codex_provider
├── Image Gen   ← replicate_provider · codex_provider · (fal_provider · …)
├── Video       ← common/ modules (Scene List, Hailuo Scene, Video Assemble, …)
├── Audio       ← common/qwen_tts_node
├── Variation   ← variation/ (a USE CASE, not a provider — it calls the others)
└── Utility     ← common/ modules (System Instructions, Shot Selector, Run Folder, …)
```

`variation/` is the first sub-package organised around a **use case** rather than a backend. It owns no model and no API client: it calls `codex_provider`'s LLM and image nodes like any other consumer would. That is the shape to copy for the next pipeline — providers stay thin and swappable, use cases compose them.

---

## 3. Adding a New Provider in 3 Steps

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

## 4. Core Reusable Utilities

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

## 5. Four Golden Rules

1. **Class keys are permanent:** `"OllamaLLM"` is the ID stored in workflow JSON files. Never rename a class key; display names and categories are cosmetic and safe to modify.
2. **Import dependencies inside functions:** Avoid top-level module imports for heavy or optional third-party packages so missing dependencies do not crash startup.
3. **Append new widgets at the end:** ComfyUI serializes widget values positionally. Inserting a widget into the middle shifts indexes and corrupts saved user workflows. Adding *sockets* is always safe.
4. **Register long-running nodes for activity badges:** If a node executes network requests or takes more than a moment, add its class key to `ANIMATED_NODES` in `web/activity.js` to render the active cooking spinner.
