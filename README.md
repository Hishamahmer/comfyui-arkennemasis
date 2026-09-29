# arkennemasis — ComfyUI Nodes

> [!TIP]
> ### 🚀 Control local ComfyUI from your web AI client
> Arkennemasis MCP connects an MCP-capable web AI client to your local ComfyUI through a configured HTTPS tunnel. Your models and GPU stay on your computer.
>
> Set up the optional gateway from the local **MCP setup** screen:
> * **Create, Control & Edit:** Ask ChatGPT Web to construct entire workflows, wire nodes, tweak parameters, and fix graphs directly on your live canvas.
> * **Live Browser Sync:** Apply revision-checked changes to an explicitly shared ComfyUI tab, with acknowledgments and bounded undo history.
> * **Selected Node Development:** Enable source editing and maintenance for named custom-node folders, with backups and explicit execution controls.
>
> On Windows portable, **3. Create launchers** adds one MCP launcher: `run_nvidia_gpu_fast_fp16_accumulation_with_mcp.bat`. Use `run_nvidia_gpu_fast_fp16_accumulation.bat` for the same fast-FP16 mode without MCP. The standard GPU and CPU BATs remain ordinary ComfyUI launchers. Close the active launcher before switching.
>
> AI-client availability and usage limits still apply, as do any paid nodes' own credentials and charges. See the current [ChatGPT connection requirements](https://developers.openai.com/plugins/deploy/connect-chatgpt).
>
> 📖 **Full MCP Guide:** [Arkennemasis MCP Setup & Usage](setup_guides/01_arkennemasis_mcp_guide.md) · [All Setup Guides](setup_guides/README.md)

One pack, one menu (**arkennemasis**), many AI use cases. **76 nodes** today:

| Category | | |
|---|---|---|
| **Variation** | 35 | a client's spreadsheet plus one photo → a verified, consistently-framed product image library |
| **Utility** | 17 | web capture, masks, compositing, boards, captions, image and text helpers |
| **Video** | 11 | per-shot generation, dubbing, measured captions, narration-fitted assembly |
| **Avatar** | 6 | find a story, write it, speak it, and put a presenter in front of it |
| **Image Gen · LLM · Audio** | 7 | `gpt-image-2`, GPT-5 text + vision, local TTS |

Providers and use cases keep growing — each module loads independently, so nothing breaks
anything else. And if you already pay for ChatGPT, none of the image or text nodes need an
API key.

---

## What you can build

| You want | The pack gives you |
|---|---|
| **Live ComfyUI control via a web AI client** | Connect an MCP-capable web client to ComfyUI via Arkennemasis MCP. Inspect, edit and explicitly run a shared canvas, with optional access to selected custom-node source. |
| **A talking-head news video, every morning, by itself** | It finds a story, writes it, speaks it, animates a presenter, cuts them out and stands them in front of a screenshot of the article. It remembers what it already covered. |
| **A narrated film from one brief** | Write the idea once. It plans the scenes, renders each one, makes every shot last as long as its voice-over, then joins them with music and subtitles. |
| **A product photo library from a spreadsheet** | One photo of the product plus a list of colours or finishes in, a full set of images out — same object every time, only the named part changing. |
| **The same picture, many versions** | Different hair, different language, different pose. You supply the list of variants; the pack runs them and puts every result on one board to compare. |

**You bring the words, the pack brings the machine.** Nothing here tells a model what to
write — the prompts are yours. These nodes handle the parts that are the same every time:
looping, retrying, staying in budget, keeping the shape, timing captions to speech,
writing files where you expect them.

**It won't quietly spend your money.** You can run 3 of 30 branches and the other 27 never
execute, so a paid node upstream is never called. Every run gets its own folder and a log.

---

## ⭐ Generate images with your ChatGPT subscription — no API key

If you already pay for ChatGPT, **you can generate `gpt-image-2` images in ComfyUI without
buying any API credit.** Install the pack, run `codex login` once in a terminal, and the
**Codex Image Gen** node signs in with your existing ChatGPT account. Images bill against
your plan instead of per call.

**ChatGPT Plus at $20/month is enough** and is what this pack is developed against.

```sh
codex login      # once, in your own terminal — not inside ComfyUI
```

There is no API key field and no OAuth flow in ComfyUI — the node reads the Codex CLI's
own login. Drop in **Codex Login Status** to confirm the account and plan before you run
anything. Full details in [What each path needs](#what-each-path-needs).

## 🎬 Ready-made workflows

[`example workflows/`](example%20workflows/) ships complete production workflows ready to drag and drop into ComfyUI:
- **Property Walkthrough AI** — generates a narrated, captioned property walkthrough video from a folder of listing photos.
- **Story Creation using MiniMax H3** — plans scenes from a brief, renders each shot with MiniMax H3, dubs narration, times subtitles, and joins into a finished film.

---

## Nodes

| Menu | Node | What it does | Out |
|---|---|---|---|
| arkennemasis/**LLM** | arkennemasis Codex LLM (ChatGPT login) | GPT-5 text **and** vision through your **`codex login`** — no API key, billed to your ChatGPT plan. Reads images, and splits a long answer into batches so a 50-scene plan does not have to arrive in one reply | `STRING`, `STRING` |
| arkennemasis/**fal/Image/…** | 106 image models — **Flux** (1, 2 pro/max/flex/flash/turbo/klein, Kontext, LoRA, Fill, Redux, Canny/Depth, PuLID, LoRA galleries, upscaler), **Nano Banana** (1, 2, Pro, Lite + edits), **GPT Image 2 Edit**, **MiniMax Image 01**, **Qwen Image 3** | one node per model on fal.ai, with that model's own settings and a live price badge | `IMAGE`, `STRING` |
| arkennemasis/**fal/Video/…** | 61 video models — **MiniMax** (Hailuo 02 / 2.3 / Fast, Video 01, H3, H3 Max + styles), **Flux 3** video (+ drafts, extend, edit, upscale), **Seedance 2.5**, **Kling v3** motion control, **Topaz** video upscale | video, most with native audio | `VIDEO`, `STRING` |
| arkennemasis/**fal/Lip Sync/…** | Sync Lipsync 2 / 2 Pro / 3 / React-1, MiniMax H3 Max Lip Sync, VEED Fabric 1.0 (+ Fast, + Text) | talking video from a picture or a clip plus audio | `VIDEO`, `STRING` |
| arkennemasis/**fal/Audio/…** | 36 audio models — **ElevenLabs** (TTS v2.5/v3/v4, dialogue, music, sound effects, speech-to-text, alignment, voice changer, isolation, dubbing), **MiniMax** (Speech 02–2.8, voice clone/design, Music), **Chatterbox**, **Qwen Audio 3 TTS**, **xAI Grok Voice** | speech, music, sound, transcripts | `AUDIO`, `STRING` |
| arkennemasis/**fal/Tools** | fal · History (free) · Recover Result (free) | every fal run, loaded back for free; a finished request collected by id without paying again | `STRING`, `IMAGE`, `VIDEO` |
| arkennemasis/**Image Gen** | arkennemasis Image Gen Settings (shared) | one node driving `aspect_ratio` / `quality` / `run_mode` / `background` / `output_format` / `moderation` / `timeout_seconds` / `api_token` on **many** Image Gen nodes at once | `ARK_IMAGE_SETTINGS` |
| arkennemasis/**Image Gen** | arkennemasis Codex Image Gen (ChatGPT login) | `gpt-image-2` through your **`codex login`** — no API key, billed to your ChatGPT plan | `IMAGE`, `STRING` |
| arkennemasis/**Utility** | arkennemasis Codex Login Status | which ChatGPT account this machine will use, and when its token expires | `STRING` |
| arkennemasis/**Utility** | arkennemasis System Instructions | reusable system prompt for any LLM node | `STRING` |
| arkennemasis/**Utility** | arkennemasis Shot Selector (run N of M) | run only N of M expensive branches — the **first N in order**, or a random sample from a seed. Unselected branches **never execute**, so a paid API node upstream is never called | `IMAGE` |
| arkennemasis/**Utility** | arkennemasis Subject Line (gender + notes) | one `Subject: …` line from a gender choice plus free-text notes, wired into every prompt — so the prompts themselves stay gender-neutral and the subject is stated once | `STRING` |
| arkennemasis/**Utility** | arkennemasis Text File Save (caption sidecar) | writes `<folder>/<filename>.txt` next to a saved image — the image/caption pairing training toolkits expect | `STRING` |
| arkennemasis/**Utility** | arkennemasis Run Folder (auto-numbered) | `<parent_dir>/<folder_name>_001`, `_002`, … — one fresh output folder per run | `STRING`, `INT` |
| arkennemasis/**Utility** | arkennemasis Story Brief / Run Log / Contact Sheet | the brief form, a JSON run log that needs no spreadsheet, and every still of a run on one sheet | `STRING`, `IMAGE` |
| arkennemasis/**Video** | arkennemasis Scene List (the loop) | fans a scene plan out so one chain runs once per scene — 5 or 50, same canvas | lists |
| arkennemasis/**Video** | arkennemasis Hailuo Scene | one scene start to finish: condition → sample → decode video **and** audio → mux → save → free | `VIDEO` |
| arkennemasis/**Audio** | arkennemasis Qwen3-TTS (voice clone) | local Qwen3-TTS. Text in, speech out; give it 5–30 s of someone speaking and it clones that voice. Runs in a subprocess — see below | `AUDIO`, `STRING` |
| arkennemasis/**Video** | arkennemasis Video Dub (narration over a clip) | swaps a clip's own soundtrack for a narration track, per clip. MiniMax H3 always generates audio and cannot be asked for silence, so the voice has to *replace* it | `VIDEO`, `STRING` |
| arkennemasis/**Video** | arkennemasis Narration Length (fit the shot to the voice) | measures a rendered narration and returns the shot length that covers it, snapped to H3's frame grid. Wire between the TTS node and the scene node and every shot outlasts its own voice-over | `INT`, `FLOAT`, `STRING` |
| arkennemasis/**Video** | arkennemasis Narration Fit (stretch or compress speech) | stretches or compresses rendered voice-over via pitch-preserving ffmpeg atempo to land exactly on target_seconds | `AUDIO`, `STRING` |
| arkennemasis/**Video** | arkennemasis Caption Style (font + subtitle style) | one of five subtitle styles, any installed font, colours, outline, box, size, 3×3 position — and an on/off switch | `ARK_CAPTION_STYLE` |
| arkennemasis/**Video** | arkennemasis Video Assemble (clips + music + subs) | joins every clip, levels each one's speech, ducks a music bed, burns the captions | `STRING`, `VIDEO` |
| arkennemasis/**Video** | arkennemasis Load Clips (finished clips from disk) | reads a run's finished clips back as a VIDEO list — join a film whose render was interrupted, without re-rendering | `VIDEO` list |
| arkennemasis/**Video** | arkennemasis Scene Split / Scene At | pull one scene out of a JSON plan — by parsing it, or by index | `STRING` |
| arkennemasis/**Video** | arkennemasis Word Timings | transcribes the finished narration and returns **when each word is actually spoken**. Moving caption styles need real times; guessing from word count drifts | `STRING` |
| arkennemasis/**Video** | arkennemasis Video Model / Video Save | pick one video model without both loading, and write a clip then free the weights | `MODEL`, `VIDEO` |
| arkennemasis/**Utility** | arkennemasis Web Shot (page → picture, text, links) | drives a local Chrome and gives you a screenshot, the readable text, and the links. Free, and it can scroll to a selector first | `IMAGE`, `STRING` |
| arkennemasis/**Utility** | arkennemasis ScreenshotOne | the hosted version of the same job, with ads and cookie banners blocked. Some sites photograph as a consent dialog through a plain browser and correctly through this | `IMAGE`, `STRING` |
| arkennemasis/**Utility** | arkennemasis Mask Refine | turns a per-frame segmentation mask into one you can composite through — a segmenter is right frame by frame and jittery as a video | `MASK` |
| arkennemasis/**Utility** | arkennemasis Overlay Subject | stands a cut-out subject on a background at a chosen size and position. The part a hosted avatar service does for you and never lets you adjust | `IMAGE` |
| arkennemasis/**Utility** | arkennemasis Match Aspect | measures the image being edited and pins the generator's output to that shape. Without it an "auto" setting says nothing about shape and the model reframes your picture | `ARK_IMAGE_SETTINGS` |
| arkennemasis/**Utility** | arkennemasis Option Board | collects a fanned-out run onto one labelled `.excalidraw` board plus a preview — how you judge a set rather than a picture | `STRING`, `IMAGE` |
| arkennemasis/**Utility** | arkennemasis Purge VRAM | unloads models between stages, so a big image model and a big video model can share one canvas | passthrough |

### arkennemasis/**Avatar** — a story in, a presenter telling it out

Six nodes that turn "cover today's news about X" into a captioned vertical video, with no
one watching. They compose with the Video and Utility nodes above rather than replacing
them.

| Node | What it does |
|---|---|
| **Avatar Brief** | one labelled box per thing you actually decide: the beat, the sources, the voice, the length |
| **Story Pick** | reads the model's choice back out and checks it — a real article, from the list it was shown, not one it invented |
| **Story History** | what has already been covered, so tomorrow picks something else |
| **Story Record** | writes today's story into that history — **only once a video file exists**. A run that produced nothing covered nothing |
| **Avatar Script** | splits one answer into the spoken script, the caption and the headline |
| **Avatar Frames** | how long the clip must be, measured from the voice that will play over it |

### arkennemasis/**Variation** — the product-variation pipeline

A client's variation spreadsheet plus one locked base photograph in; a verified, consistently framed image library out. Any product, any number of variation axes. The guarantee is that **every delivered image shows the same physical object, differing only in the specified attribute**.

* **35 modular pipeline nodes:** Covers intake (`Sheet Probe`, `Variation Intake`), references (`Spec Library`), geometry (`Plate Lock`, `Region Mask`), recipe compilation, substitution prompt generation, CIELAB recoloring, automated ΔE2000 quality verification, and store export.
* **Two execution paths:** The full ten-stage pipeline for unformatted client sheets, and a shorter CSV catalogue path when prompts already exist.

👉 **Full architecture & setup guide:** See [setup_guides/06_product_variation_pipeline_guide.md](setup_guides/06_product_variation_pipeline_guide.md).

### arkennemasis/**Audio** — Qwen3-TTS Voice Cloning

Local, offline voice cloning. Input text and a 5–20s voice reference sample to clone any voice. Runs in an isolated child subprocess (`vendor/tts_env`) with pinned dependencies, completely preventing conflicts with ComfyUI's main Python packages.

👉 **Full setup & installation guide:** See [setup_guides/05_local_voice_cloning_qwen3_tts_guide.md](setup_guides/05_local_voice_cloning_qwen3_tts_guide.md).

### Captions

**Caption Style** feeds **Video Assemble**. Five styles:

| Style | On screen |
|---|---|
| `classic` | the whole line at once |
| `karaoke` | the fill sweeps across the line as it is spoken |
| `highlight` | the spoken word changes colour |
| `underline` | the spoken word is underlined |
| `word_by_word` | one word at a time, nothing else |

Everything but `classic` marks individual words, so it needs to know when each word is
spoken. A video model gives no word timestamps, so they are **estimated** from the script
and the clip's real duration, weighted by word length and by trailing punctuation. That
tracks speech closely; it is not frame-accurate, and it drifts if the model ad-libs.

Fonts come from [`fonts/`](fonts/) (bundled, listed first) and from the machine's own
installed fonts. See that folder's README for what ships and how to add more.

Toggle `enabled` off on the node for a video with no subtitles at all — every other
setting stays put.

## Install

```sh
cd ComfyUI/custom_nodes
git clone https://github.com/Hishamahmer/comfyui-arkennemasis
```

Install the dependencies, then restart ComfyUI:

```sh
# portable build:
python_embeded\python.exe -m pip install -r ComfyUI\custom_nodes\comfyui-arkennemasis\requirements.txt
# normal install:
pip install httpx
```

Or in **ComfyUI-Manager** → *Install via Git URL* → paste the repo URL (deps auto-install).

## What each path needs

Two ways to reach `gpt-image-2`, plus fal for everything else. Use whichever you already
pay for.

| Path | Nodes | What it requires |
|---|---|---|
| **fal.ai** | every `arkennemasis fal ·` node | A fal.ai account and an API key (`FAL_KEY`). Pay-as-you-go per image / second / minute — each node shows its price. |
| **Codex / ChatGPT** | Codex Image Gen, Codex Login Status | A **paid ChatGPT subscription** and the **Codex CLI already logged in on this machine**. No API key. Images bill against your ChatGPT plan instead of per call. |

### Codex path — read this before you try it

1. **A paid ChatGPT plan is required.** The free tier cannot call the hosted image tool.
   **ChatGPT Plus at $20/month is the recommended plan** and is what this pack is
   developed against. Business/Pro plans work too.
2. **Install the Codex CLI and sign in from your own terminal**, on the same machine and
   the same user account that runs ComfyUI:
   ```sh
   codex login
   ```
   This writes `~/.codex/auth.json`. The nodes read that file directly — **there is no
   OAuth flow inside ComfyUI and nowhere to paste a password.** If you have not run
   `codex login`, the Codex nodes will tell you so and stop.
3. **Check it worked** by dropping in the **Codex Login Status** node — it reports the
   signed-in account, the plan and when the token expires.

> Availability is account-dependent: not every ChatGPT plan or region can call the hosted
> image tool. The node says so plainly rather than failing cryptically.

Running several ChatGPT accounts? Give each its own `CODEX_HOME` folder and set the node's
`codex_home` per node. The Codex Image Gen node's `account` output names the signed-in
email, so you can see which login produced an image.

## fal.ai nodes

**One node per model.** Each fal model is its own node with exactly that model's inputs,
built from the model's file in [`fal_provider/models/`](fal_provider/models/). Under
`arkennemasis/fal/`: **Image**, **Video**, **Lip Sync** and **Tools**.

**The key.** Put one line in the `.env` file of your ComfyUI install — in the portable build
that is the folder holding `run_nvidia_gpu.bat`:

```
FAL_KEY=your-key-from-fal.ai/dashboard/keys
```

The nodes read it from there and nowhere else — there is no key box on the nodes, so a key
can never end up inside a workflow file or an image you share. Get a key at
https://fal.ai/dashboard/keys.

**Money.**
- Each node carries a **live price badge** that follows its settings (resolution, duration,
  number of images, quality…), and its title shows the model's rate.
- **`max_cost_usd`** (default $20) is a cap: when the estimate for a run is above it, the node
  stops **before** anything is uploaded or sent. `0` removes the cap.
- The estimates are tested against **fal's own prices and worked examples**, and the
  pre-run check measures the real clips and pictures you connect.
- **`max_concurrent`** (default 1) is how many paid fal calls may run at the same time,
  across every fal node in a run. At 1 they go one after another; raise it (up to 32) to run
  several side by side. If one fal node fails, the ones still waiting are not started.
- While it runs, the node shows its status (queued / running / seconds / estimate).
  ComfyUI's **Cancel** also cancels the request on fal.
- Every request is written to `output/fal/_requests.jsonl`. If ComfyUI is restarted while a
  job is running on fal, **fal Recover Result** collects it by its id — for free.
- Results are saved to `output/fal/<model>/` and shown on the node.

**Adding a model** is one command (free — it reads fal's public pages), then restart ComfyUI:

```sh
python_embeded\python.exe ComfyUI\custom_nodes\comfyui-arkennemasis\fal_provider\add_model.py https://fal.ai/models/<owner>/<model>
```

See [`fal_provider/README.md`](fal_provider/README.md) for how the model files and the
price rules work.

## Advanced Utilities & Execution Controls

Arkennemasis provides dedicated utility nodes to manage execution flow, prevent runaway costs, and organize outputs:

* **Shared Image Settings (`Image Gen Settings`)**: Drives aspect ratio, quality, moderation, and timeouts across multiple generator nodes simultaneously.
* **Rate Limits & Concurrency (`run_mode`)**: Switches between sequential execution (`one at a time`) to avoid 429 rate limit bans, and parallel execution (`all at once`, `max_concurrent`).
* **Automated Backoff Retries**: Automatically retries 429 rate limits, 5xx server drops, and interrupted network streams with exponential backoff.
* **Lazy Branch Gating (`Shot Selector`)**: Evaluates only the first N branches; unselected branches are never evaluated and never billed.
* **Auto-Numbered Output Folders (`Run Folder`)**: Generates dynamically incremented run folders (`run_001`, `run_002`) so outputs stay grouped.
* **Caption Sidecars (`Text File Save`)**: Writes `<filename>.txt` caption pairing files alongside generated images.

👉 **Full technical guide:** See [setup_guides/07_advanced_utilities_and_concurrency.md](setup_guides/07_advanced_utilities_and_concurrency.md).

## Notes

- fal calls are **paid** — each run bills your fal account (see the node's price badge).
- Long runs poll (no fixed timeout) and run off the UI thread, so ComfyUI stays responsive
  and **Cancel** works. A spinner + elapsed-time badge shows on the node while it runs.

## Example workflows

Canvas-format workflow exports live in [`example workflows/`](example%20workflows/). See the
README there for conventions — most importantly **never commit an `api_token`**, since
workflow JSON stores widget values verbatim.

## Developer & Contributing Guide

For the complete repository file tree, menu category architecture, adding new providers in 3 steps, reusable helpers, and developer safety rules:

👉 **Developer Guide:** See [setup_guides/08_developer_and_contributing_guide.md](setup_guides/08_developer_and_contributing_guide.md).

## License

MIT
