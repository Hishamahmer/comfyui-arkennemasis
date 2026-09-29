# fal.ai nodes

One ComfyUI node per fal.ai model. Every node is built from one JSON file in `models/`,
so a new model is a new file, never new code.

## Add a model (free, one command)

```
python_embeded\python.exe ComfyUI\custom_nodes\comfyui-arkennemasis\fal_provider\add_model.py https://fal.ai/models/<owner>/<model>
```

Several links at once are fine. It reads three public things - fal's OpenAPI schema for the
endpoint, fal's model catalog entry, and the model page's pricing - and writes
`models/<endpoint>.json`. Restart ComfyUI; the node appears under
`arkennemasis/fal/<category>` as **arkennemasis fal · <title> · <price>**.

Then open the new JSON and check `pricing.estimate` against the `pricing.text` it printed
(see *Price rules* below). The first guess is right for simple per-second / per-minute /
per-image models; resolution-dependent prices need the rate map filling in.

Re-running `add_model.py` on a model already added refreshes its inputs, outputs,
description and pricing text from fal, and **keeps** the hand-tuned parts: the class key
(saved canvases depend on it), `title`, `category`, `checks`, `hide`, `labels` and the
price rules. `--reset` regenerates those too.

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
`audio`, `audio_list`, `url`, `text`, `string`, `enum`, `bool`, `tribool` ("(not set)" /
true / false), `int`, `float` (an optional one with no default uses **-1 = not set**),
`image_size` (fal's presets + `custom` with width/height).

Left out on purpose everywhere: the yes/no `sync_mode` (it would return files inline and
drop them from fal's request history), `end_user_id`, and constants.

## Price rules (`pricing.estimate`)

One description drives both the Python estimate (checked against `max_cost_usd`, shown
while running) and the live badge on the node (JSONata the frontend evaluates as widgets
change). The browser cannot measure a connected clip, so a price that depends on one shows
as a rate there (`$8.00/min of video`) and as a dollar figure at run time.

```json
{
  "per": "second",                         // second | minute | image | run | table
  "rate": {"widget": "resolution", "map": {"480p": 0.08, "720p": 0.15}},   // or a number
  "rate_if": [{"widget": "draft", "equals": true, "rate": 0.2205}],
  "quantity": {"widget": "duration", "auto_max": 30},   // or {"media": ["audio"]}
                                                          // or {"words": "text", "per_second": 2.5}
                                                          // or {"unknown": true}
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
| `recover.py` | fal Recover Result |
