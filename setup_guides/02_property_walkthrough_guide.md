# Property Walkthrough AI — Setup & Run Guide

**Workflow file:** [`example workflows/Property Walkthrough AI.json`](../example%20workflows/Property%20Walkthrough%20AI.json)

Generates a complete, narrated, and subtitled property walkthrough film from a folder of listing photos.

---

## 1. Preparing Property Photos

1. Create a dedicated folder for the property photos.
2. Name photos sequentially so they sort in the exact order you want the walkthrough to flow:
   * `01-exterior-front.jpg`
   * `02-foyer.jpg`
   * `03-living-room.jpg`
   * `04-kitchen.jpg`
   * `05-master-bedroom.jpg`
3. Enter the absolute path of this folder into the **THE PROPERTY PHOTOS** loader box at the left of the canvas.
4. **Any number of photos is supported** (e.g. 5 to 30+). The loop processes shots one-by-one and releases VRAM after each shot, keeping memory usage constant.

---

## 2. Choosing the LLM Script Writer

The workflow offers two options for analyzing the photos and drafting the narration script:

* **OpenAI (Recommended):**
  * Uses ChatGPT vision via your existing **Codex login** (`codex login`).
  * No API key required; billed to your ChatGPT Plus subscription.
  * Enable the OpenAI LLM group box and disable the local LLM box.
* **Local Gemma 3 (Offline):**
  * Free and completely offline (requires ~8 GB VRAM).
  * Requires `gemma-3-12b-it-Q4_K_M.gguf` and `mmproj-F16.gguf` inside `ComfyUI/models/LLM/gemma-3-12b/`.

> **Important:** Leave **exactly one** LLM branch enabled.

---

## 3. Narrator Voice Setup

1. For voice cloning, drop a clean mono voice recording (**under 20 seconds**, `.wav` or `.mp3`) into `ComfyUI/input/`.
2. Select that audio file on the voice cloning node (`arkennemasis Qwen3-TTS`).
3. If no custom voice sample is provided, the node will use the default stock voice.
4. *Tip:* Keep the sample short. Long voice samples slow down inference across every shot.

---

## 4. Video Rendering & Assembly

1. **Video Model Selection:**
   * Uses LTX-2.5 or MiniMax H3 via the `arkennemasis Video Model` node.
   * Make sure the corresponding video weights and **Audio VAE** are installed.
2. **Pre-flight Check:**
   * Check **HOW MANY PHOTOS** vs **HOW MANY SHOTS THE PLAN WROTE**.
   * Both numbers must match before generating video.
3. **Captions (On / Off):**
   * Toggle the **CAPTIONS — ON / OFF** switch next to the assemble node if you want burned-in subtitles.
4. **Outputs:**
   * Finished walkthrough videos land in:
     `ComfyUI/output/realestate/video-workflows/property-walkthrough/`
   * Each execution receives an auto-incremented `run_XXX` folder with individual shot clips and the final combined `walkthrough_ltx.mp4`.
