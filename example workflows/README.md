# Example workflows

Drop workflow `.json` files here — ComfyUI **canvas** exports (Workflow → Export), not
API-format graphs, so they open by drag-and-drop onto the canvas.

## Conventions

- One `.json` per workflow, named after what it does.
- Add a row to the table below.
- **Never commit an API key.** Leave every `api_token` field blank — the nodes fall back
  to environment variables or `.env` files.
- Absolute paths from your own machine leak your folder layout; prefer relative
  `filename_prefix` values where a node allows it.

## Workflows

| Workflow | Nodes it demonstrates | Notes |
|---|---|---|
| **Property Walkthrough AI** | Scene List · Video Assemble · Qwen3-TTS · Caption Style · Load Clips | Generates a narrated, captioned property walkthrough film from a folder of listing photos. |
| **Story Creation Workflow using MiniMax H3** | Story Brief · Hailuo Scene · Scene List · Video Assemble · Caption Style · Qwen3-TTS | Plans scenes from a brief, renders each shot with MiniMax H3, dubs narration, times subtitles, and joins into a finished video. |

---

### 1. Property Walkthrough AI

One folder of listing photos in, a **narrated, subtitled walkthrough video** out.

- **Photos:** Put your property photos in a folder named in sequence (`01-front.jpg`, `02-living.jpg`, etc.) and point the loader node to that folder.
- **Story / Shot Plan:** Uses an LLM to generate a shot plan matching each photograph with a descriptive narration script.
- **Voice Narration:** Synthesizes narration via local Qwen3-TTS (optional reference voice sample).
- **Assembly:** Automatically fits shot lengths to voice narration, normalizes speech audio, and burns synchronized captions.

### 2. Story Creation Workflow using MiniMax H3

A story idea in, a **multi-scene rendered video** out.

- **Story Brief:** Fill out the brief form (the premise, visual style, character details, pacing).
- **Scene List Loop:** Breaks the story into a sequence of scenes executed one-by-one on the same canvas loop.
- **Hailuo / MiniMax Scene:** Conditions and renders each scene sequentially with MiniMax H3.
- **Voice-over & Assembly:** Dubs each scene with Qwen3-TTS narration, fits duration, times subtitles, ducks music, and renders the final film.
