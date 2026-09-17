# Story Creation Workflow (MiniMax H3) — Setup & Run Guide

**Workflow file:** [`example workflows/story creation workflow using minimaxh3.json`](../example%20workflows/story%20creation%20workflow%20using%20minimaxh3.json)

Takes a high-level creative brief, plans a multi-scene storyboard, renders each shot with MiniMax H3 (Hailuo), dubs narration, synchronizes captions, and joins everything into a finished film.

---

## 1. Prerequisites & Models

1. **MiniMax H3 Model:** Ensure MiniMax H3 (Hailuo) model weights and text encoders are placed in your ComfyUI models directory (`models/checkpoints/` or `models/diffusion_models/`).
2. **Qwen3-TTS:** Requires the isolated TTS environment in `vendor/tts_env` and Qwen3-TTS weights under `models/qwen-tts/`.
3. **FFmpeg:** Ensure `ffmpeg` is available on your system path (bundled automatically in the portable install).

---

## 2. Setting the Story Brief

1. Locate the **`arkennemasis Story Brief`** group at the left side of the canvas.
2. Fill in your creative direction:
   * **Premise:** The core plot or topic of the film.
   * **Visual Style:** Cinematic, anime, photorealistic 35mm, vintage documentary, etc.
   * **Scene Count:** How many scenes to generate (e.g., 5 to 12 scenes).
   * **Target Pacing:** Duration targets per shot.
3. The LLM story agent compiles the brief into an execution plan containing visual descriptions, camera motions, and voice-over narration lines for every shot.

---

## 3. How the Scene Loop Operates

Unlike traditional workflows that require copying and pasting dozens of node chains for each scene, Arkennemasis uses **`Scene List (the loop)`**:

* **One Canvas Chain:** Only a single MiniMax sampling chain exists on the canvas.
* **Fan-Out Execution:** `Scene List` iterates through each scene in the storyboard sequentially.
* **Automatic VRAM Cleanup:** After each scene renders and dubs, intermediate tensors are cleared before the next scene starts.

---

## 4. Narration Pacing & Dubbing Nodes

MiniMax H3 generates video along with its own native ambient audio. To place voice-overs cleanly over each shot:

1. **`arkennemasis Qwen3-TTS`**: Generates speech audio for that scene's line.
2. **`arkennemasis Video Dub`**: Swaps the clip's raw audio for the rendered narration track.
3. **`arkennemasis Narration Length`**: Computes the required video frame count snapped to MiniMax's frame grid so the shot outlasts the voice.
4. **`arkennemasis Narration Fit`**: Uses pitch-preserving `ffmpeg atempo` to slightly stretch or compress speech to land on exact second marks, eliminating uneven pauses.

---

## 5. Synchronized Subtitles & Final Video Assemble

1. **`arkennemasis Caption Style`**: Select your typography, colours, positioning, and style (Classic, Karaoke, Highlight, Underline, or Word-by-word).
2. **`arkennemasis Word Timings`**: Uses Whisper to get precise timestamp bounds for each spoken word.
3. **`arkennemasis Video Assemble`**:
   * Concatenates all rendered scene clips.
   * Normalizes speech to standard broadcast levels (-16 LUFS).
   * Ducks background music beds under narration.
   * Burns subtitles into the final video export.
