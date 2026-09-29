# fal.ai nodes

One ComfyUI node per fal.ai model. Every node is built from one JSON file in `models/`,
so a new model is a new file, never new code.

## Add a model (free, one command)

```
python_embeded\python.exe ComfyUI\custom_nodes\comfyui-arkennemasis\fal_provider\add_model.py https://fal.ai/models/<owner>/<model>
```

Several links at once are fine (`--workers 4` fetches four at a time; `--catalog <file>`
reuses a saved catalog instead of asking fal's rate-limited catalog API per model). It reads
three public things - fal's OpenAPI schema for the endpoint, fal's model catalog entry, and
the model page's pricing - and writes `models/<endpoint>.json`. Restart ComfyUI; the node
appears under `arkennemasis/fal/<Type>/<Family>` (e.g. `Video/MiniMax`, `Audio/ElevenLabs`) as
**arkennemasis fal · <title> · <price>**. The title is made from the endpoint id, so it is
always unique (`fal-ai/minimax/hailuo-2.3/pro/image-to-video` -> *MiniMax Hailuo 2.3 Pro Image to Video*).

The price rule is generated too, marked `"auto": true`: from fal's billing unit (per second,
minute, hour, 1000 characters, megapixel, image or per video), the duration / text / size /
count widget it depends on, "rounded up" in the pricing text, and a rate per resolution when
the text names one. Check it against the `pricing.text` it printed; fal's structured price
and its page sometimes disagree (Flux 3 video's page charges twice the structured figure),
and the page is what you pay. Edit the rule and delete `"auto"` to keep your version.

Re-running `add_model.py` on a model already added refreshes its inputs, outputs,
description and pricing text from fal, regenerates `auto` price rules, and **keeps** the
hand-tuned parts: the class key (saved canvases depend on it), `title`, `category`,
`checks`, `hide`, `labels` and any price rule without `auto`. `--reset` regenerates those
too; `--reprice` regenerates only the `auto` price rules, offline.

## What every node does on Run

1. Reads `FAL_KEY` from the install's `.env` (the folder with `run_nvidia_gpu.bat`, or the
   `ComfyUI` folder). No key box on the node - a typed key would be saved into every
   workflow and image.
2. Checks every input: required fields, text lengths, clip lengths (`checks`), batches
   where one picture is expected, `extra_json`.
3. Estimates the cost and stops if it is above **`max_cost_usd`** (default $20, 0 = off).
4. Uploads the pictures / clips / audio to fal's CDN (free).
5. Submits - the one billed call. It is retried only when fal certainly did not accept it.
6. Waits, showing queue position / running / seconds / estimate on the node. ComfyUI's
   **Cancel** cancels on fal too.
7. Downloads the results into `output/fal/<model>/`, shows them on the node, and returns
   them (`IMAGE` / `VIDEO` + the file paths + every scalar fal returns + an `info` JSON).

Steps 1-4 cost nothing; a failure there never bills. Every submit and every finished
result is logged in `output/fal/_requests.jsonl`.

**How the estimate sizes a run.** Seconds come from the connected clip or the duration box;
characters from the text; pictures are measured: when the size box says "auto" (or the model
has none) the output follows the input picture, an upscaler multiplies by its factor squared,
and models that bill input pictures too ("per megapixel of input and output") count them.
fal's megapixel is 1024 x 1024 pixels. The badge cannot see pictures, so it shows the price
for a 1 MP picture; the pre-run cost check uses the real one. An option missing from a price
table costs the table's highest price, never $0.

**The prices are checked against fal's own numbers** (`tests/fal/test_prices.py`): prices
written down from fal's pricing pages and their worked examples ("a 5-second 768p video costs
$0.40"). `add_model.py` only fills a per-option table when fal's text pairs each option with
one price unambiguously; any text with several prices, a multiplier, an extra, a discount or a
minimum gets a `"review"` marker, and `test_prices.py` fails until a person prices the model.

**One paid call at a time by default (`max_concurrent`, `limit.py`).** ComfyUI runs async
nodes side by side, so without a limit a canvas of twenty fal nodes would send twenty paid
requests in the same second. Steps 4-7 run inside a slot: a node starts them only while
fewer than its own `max_concurrent` (default **1**, up to 32) fal calls are running in that
run, and shows "waiting for its turn" meanwhile. Raise it on the nodes you want side by
side; a node left at 1 waits until nothing else runs. Steps 1-3 and a reused run never
wait. **Once any fal node in the run fails, the ones still waiting are never started** -
ComfyUI stops the run on a failure but does not cancel the other nodes until it ends, and
the failed node's freed slot would otherwise let the next one submit first.

**If ComfyUI restarts mid-wait** the job keeps running on fal. Put the request id from the
log into **fal Recover Result** (menu `arkennemasis/fal/Tools`) to download it - free.

## The model file

| key | what it is |
|---|---|
| `endpoint_id` | fal's id, e.g. `fal-ai/sync-lipsync/v3` |
| `class_key` | the ComfyUI node type - **frozen**, saved canvases use it |
| `title`, `category` | menu name and `arkennemasis/fal/<category>` |
| `folder` | results go to `output/fal/<folder>/` |
| `inputs` | generated from the schema - one entry per field, in fal's order |
| `omitted` | fields deliberately left off, each with the reason |
| `outputs` | generated - files first, then plain values |
| `pricing` | `billing` (fal's unit + price), `text` (fal's own words), `tag` (short, in the title), `estimate` (the rules), `checked` (date) |
| `checks` | `min_seconds` / `max_seconds` per socket, `require_any` (at least one connected) |
| `hide` | `{field path: reason}` - fields to leave off even though the schema has them |
| `labels` | `{input name: label}` - a nicer label on a widget |

Input kinds: `image`, `image_list` (sockets `image_1..N`, each may carry a batch; sent in
socket order), `mask` (transparent PNG where the mask is set), `video`, `video_list`,
`audio`, `audio_list`, `url`, `text`, `string`, `enum` (numbers sent as numbers, including
mixed lists like `auto, 5, 6 ... 20`), `bool`, `tribool` ("(not set)" / true / false), `int`,
`float` (an optional one with no default uses **-1 = not set**), `image_size` (fal's presets +
`custom` with width/height), and `json` - a box holding JSON for lists and nested structures
(LoRA lists, dialogue lines, composition plans, keyframes), pre-filled with fal's own example.
A nested object whose parts are plain values becomes one box per part instead.

Outputs: `IMAGE` (+ paths), `VIDEO` (+ path), `AUDIO` (a real waveform, + path, with a player on
the node), every plain value fal returns, lists/objects (word timings...) as JSON text, and
`info`.

Left out on purpose everywhere: the yes/no `sync_mode` (it would return files inline and
drop them from fal's request history), `end_user_id`, and constants.

## Price rules (`pricing.estimate`)

One description drives both the Python estimate (checked against `max_cost_usd`, shown
while running) and the live badge on the node (JSONata the frontend evaluates as widgets
change). The browser cannot measure a connected clip, so a price that depends on one shows
as a rate there (`$8.00/min of video`) and as a dollar figure at run time.

```json
{
  "per": "second",                         // second | minute | hour | kchar | megapixel | image | run | table
  "rate": {"widget": "resolution", "map": {"480p": 0.08, "720p": 0.15}},   // or a number
  "rate_if": [{"widget": "draft", "equals": true, "rate": 0.2205}],
  "quantity": {"widget": "duration", "auto_max": 30},   // "auto" or -1 counts as auto_max;
                                                          //   "scale": 0.001 for a _ms widget
                                                          // or {"media": ["audio"]}
                                                          // or {"chars": "text"}  (per: "kchar")
                                                          // or {"megapixels": "image_size", "count": "num_images"}
                                                          // or {"words": "text", "per_second": 2.5}
                                                          // or {"unknown": true}
  "round_up": true,                        // whole minutes / whole megapixels ...
  "first": 0.07,                           // "$0.07 for the first MP, $0.03 per extra": rate 0.03
  "multiply": [{"widget": "resolution", "map": {"2k": 1.5, "4k": 2}}],
  "media_factor": {"inputs": ["video_1"], "factor": 0.6, "add_seconds": true},
  "over": {"seconds": 15, "factor": 1.2},
  "add": [{"widget": "enable_web_search", "equals": true, "usd": 0.015}],
  "label": " of audio"                     // badge suffix when the quantity is a clip
}
```

Map keys are lower-case (the frontend lower-cases dropdown values). `per: "table"` is the
size x quality table used by GPT Image 2 Edit - see its file.

## Files

| file | job |
|---|---|
| `add_model.py` | link -> model file |
| `schema_convert.py` | fal's OpenAPI -> inputs / outputs |
| `node.py` | model file -> ComfyUI node; the run |
| `client.py` | key, uploads, queue, downloads - standard library only |
| `media.py` | IMAGE / MASK / VIDEO / AUDIO <-> files |
| `pricing.py` | estimate + badge formula |
| `limit.py` | `max_concurrent` - how many paid fal calls run at once |
| `recover.py` | fal Recover Result |
| `history.py` | fal History |

Tests: `../tests/fal/` (README there) - no key, nothing billed.
