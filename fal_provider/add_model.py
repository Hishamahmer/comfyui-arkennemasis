"""Add (or refresh) a fal model as a node. Free - it only reads fal's public pages.

    python_embeded\\python.exe ComfyUI\\custom_nodes\\comfyui-arkennemasis\\fal_provider\\add_model.py ^
        https://fal.ai/models/veed/fabric-1.0  [more links...]  [--category "Lip Sync"] [--title "..."]

For each link it reads three public things and writes ``models/<endpoint>.json``:

  * fal's OpenAPI schema for the endpoint -> the node's inputs and outputs
  * fal's model catalog entry             -> title, category, description
  * the model page                         -> the price fal bills and its pricing text

Then restart ComfyUI and the node is in the menu under arkennemasis/fal/<category>.

Re-running it on a model already added refreshes the inputs, outputs, description and
pricing text, and KEEPS what was tuned by hand: the node's class key (so saved canvases
keep working), title, category, the cost-estimate rules and the input checks. Pass
``--reset`` to regenerate those too.

The estimate rules written for a NEW model are a first guess from fal's billing unit
(per second / per minute / per image). Open the JSON and check them against the pricing
text it prints - see README.md for the rule format. When fal's text has more than one price,
a multiplier, an extra, a discount or a minimum, the rule gets a "review" marker and
tests/fal/test_prices.py FAILS until a person has priced it and removed the marker.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import schema_convert  # noqa: E402  (standalone script: no package context)

MODELS_DIR = os.path.join(HERE, "models")
OPENAPI_URL = "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=%s"
CATALOG_URL = "https://api.fal.ai/v1/models?endpoint_id=%s"
PAGE_URL = "https://fal.ai/models/%s"
PAGE_TABS = {"api", "playground", "examples", "requests", "analytics", "llms.txt"}
KEEP_ON_REFRESH = ("class_key", "title", "category", "aliases", "checks", "hide", "labels")


def fetch(url, attempts=6):
    """GET a public fal page, waiting politely when fal says too many requests (429)."""
    import time
    import urllib.error
    for attempt in range(attempts):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (arkennemasis add_model)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == attempts - 1:
                raise
            wait = float(exc.headers.get("retry-after") or 5 * (attempt + 1))
            print("   (fal says slow down - waiting %.0f s)" % wait)
            time.sleep(wait)


def endpoint_from(link):
    link = link.strip()
    if "://" in link:
        path = urllib.parse.urlparse(link).path
        parts = [p for p in path.split("/") if p]
        if parts and parts[0] == "models":
            parts = parts[1:]
        while parts and parts[-1] in PAGE_TABS:
            parts = parts[:-1]
        return "/".join(parts)
    return link.strip("/")


def file_stem(endpoint):
    return re.sub(r"[^A-Za-z0-9.-]+", "_", endpoint).strip("_")


def class_key(endpoint):
    return "ArkFal_" + re.sub(r"[^A-Za-z0-9]+", "_", endpoint).strip("_")


FAMILIES = [  # (substring of the endpoint id, menu family) - first match wins
    ("elevenlabs", "ElevenLabs"), ("minimax", "MiniMax"), ("hailuo", "MiniMax"),
    ("flux", "Flux"), ("seedance", "Seedance"), ("gpt-image", "OpenAI"),
    ("nano-banana", "Google"), ("sync-lipsync", "Sync"), ("veed/", "VEED"),
    ("chatterbox", "Chatterbox"), ("xai/", "xAI"), ("kling", "Kling"), ("topaz/", "Topaz"),
    ("qwen", "Qwen"),
]


def family_for(endpoint):
    e = endpoint.lower()
    for needle, family in FAMILIES:
        if needle in e:
            return family
    return endpoint.split("/")[0]


OWNER_WORDS = {"fal-ai": "", "bytedance": "", "blackforestlabs": "", "alibaba": "", "google": "",
               "openai": "", "resemble-ai": "Resemble", "xai": "xAI", "minimax": "MiniMax",
               "elevenlabs": "ElevenLabs", "topaz": "Topaz", "veed": "VEED", "chatterbox": "Chatterbox"}
WORD_CASE = {"tts": "TTS", "lora": "LoRA", "hd": "HD", "3d": "3D", "4b": "4B", "9b": "9B", "srpo": "SRPO",
             "vto": "VTO", "rf": "RF", "pulid": "PuLID", "hdr": "HDR", "sdr": "SDR", "so101": "SO-101",
             "minimax": "MiniMax", "elevenlabs": "ElevenLabs", "chatterboxhd": "ChatterboxHD",
             "lucidflux": "LucidFlux", "to": "to", "and": "and", "of": "of", "kling": "Kling",
             "h3": "H3", "01": "01", "02": "02", "ai": "AI", "flf2v": "FLF2V", "vhs": "VHS",
             "16bit": "16-bit"}


def humanize(endpoint):
    """A readable, unique node title from the endpoint id:
    fal-ai/minimax/hailuo-2.3/pro/image-to-video -> MiniMax Hailuo 2.3 Pro Image to Video."""
    parts = endpoint.split("/")
    words = []
    for i, seg in enumerate(parts):
        if i == 0 and seg in OWNER_WORDS:
            if OWNER_WORDS[seg]:
                words.append(OWNER_WORDS[seg])
            continue
        if seg == "kling-video":
            words.append("Kling")
            continue
        for w in seg.split("-"):
            low = w.lower()
            if low in WORD_CASE:
                words.append(WORD_CASE[low])
            elif re.fullmatch(r"v[0-9.]+", low):
                words.append(low)
            elif re.fullmatch(r"[0-9.]+", low):
                words.append(low)
            else:
                words.append(low[:1].upper() + low[1:])
    out = []
    for w in words:                      # "ElevenLabs ElevenLabs" -> "ElevenLabs"
        if not out or out[-1].lower() != w.lower():
            out.append(w)
    return " ".join(out)


def category_for(endpoint, fal_category):
    """`<type>/<family>` - the menu becomes arkennemasis/fal/Video/MiniMax and so on."""
    e = endpoint.lower()
    c = (fal_category or "").lower()
    if "lipsync" in e or "lip-sync" in e or e.startswith("veed/fabric"):
        kind = "Lip Sync"
    elif "audio" in c or "speech" in c or "elevenlabs" in e:
        kind = "Audio"
    elif c.endswith("-to-image") or c == "image-to-image":
        kind = "Image"
    elif c.endswith("-to-video") or c == "video-to-video":
        kind = "Video"
    else:
        kind = "Other"
    return "%s/%s" % (kind, family_for(endpoint))


def _clean(text):
    text = text.replace("\\\\n", " ").replace("\\n", " ").replace("**", "")
    text = html.unescape(re.sub(r"\s+", " ", text)).strip()
    return text


def page_pricing(endpoint):
    """(billing dict or None, pricing text or None) from the public model page."""
    page = fetch(PAGE_URL % endpoint).replace('\\"', '"')
    billing = None
    for m in re.finditer(r'"endpointBilling":\{"endpoint":"([^"]+)","billing_unit":"([^"]+)",'
                         r'"price":([0-9.eE+-]+)', page):
        if m.group(1) == endpoint:
            billing = {"unit": m.group(2), "price": float(m.group(3))}
            break
    text = None
    for m in re.finditer(r'"pricingInfoOverride":"((?:[^"\\]|\\.)*)"', page):
        before = page[max(0, m.start() - 3000):m.start()]
        ids = re.findall(r'"(?:id|endpointId|endpoint_id)":"([a-z0-9._-]+/[^"]+)"', before)
        if ids and ids[-1] == endpoint and "$undefined" not in m.group(1):
            text = _clean(m.group(1))
            break
    if text is None and billing:
        unit = billing["unit"].rstrip("s")
        text = "$%g per %s (fal's billing unit)." % (billing["price"], unit)
    return billing, text


UNIT_PER = {"second": "second", "seconds": "second", "minute": "minute", "minutes": "minute",
            "hour": "hour", "hours": "hour", "image": "image", "images": "image",
            "megapixel": "megapixel", "megapixels": "megapixel", "processed megapixel": "megapixel",
            "processed megapixels": "megapixel", "1000 characters": "kchar",
            "1k characters": "kchar", "character": "char", "characters": "char",
            "video": "run", "videos": "run", "request": "run", "requests": "run",
            "generation": "run", "generations": "run", "audio": "run", "audios": "run",
            "song": "run", "songs": "run"}
DURATION_WIDGETS = ("duration", "duration_seconds", "seconds", "length", "audio_length",
                    "video_length", "num_seconds", "music_length_ms", "length_ms", "duration_ms")
TEXT_WIDGETS = ("text", "prompt", "input", "script", "lyrics")
COUNT_WIDGETS = ("num_images", "num_outputs", "n", "number_of_images")


_PRICE = r"\$\s*([0-9]+(?:\.[0-9]+)?)"


def _rates_from_text(text, options):
    """{option: $ rate} ONLY when fal's text pairs one price with each option unambiguously.

    Two phrasings count: "$0.05 per second at 480p" (price, unit, then at/for OPTION) and
    "480p - $0.08" / "for 720p, ... roughly $0.47" (OPTION, then the next price, with no other
    option in between and no price already claimed by the first phrasing). An option given
    two different prices (a launch discount, a token rate beside a per-second rate) makes the
    whole table ambiguous -> {} and the caller marks the rule for review.

    The first version took the NEAREST price in either direction, so "$0.05 per second at
    480p, $0.06 per second at 768p" gave 480p the $0.06: every MiniMax H3 table shifted by
    one resolution (found 2026-09-30).
    """
    if not text or not options:
        return {}
    low = text.lower()
    opts = [str(o).lower() for o in options
            if str(o).lower() not in ("auto", "(not set)") and len(str(o)) >= 2]
    if len(opts) < 2:
        return {}
    alt = "|".join(re.escape(o) for o in sorted(opts, key=len, reverse=True))
    opt_re = r"(?<![a-z0-9.])(%s)(?![a-z0-9])" % alt
    seen = {}
    claimed = set()
    for m in re.finditer(_PRICE + r"\s*(?:/\s*[a-z]+|per\s+[a-z]+)?\s+(?:at|for)\s+" + opt_re, low):
        seen.setdefault(m.group(2), set()).add(float(m.group(1)))
        claimed.add(m.start())
    for m in re.finditer(opt_re, low):
        pm = re.compile(_PRICE).search(low, m.end())
        if not pm or pm.start() in claimed or pm.start() - m.end() > 60:
            continue
        if re.search(opt_re, low[m.end():pm.start()]):      # another option sits in between
            continue
        seen.setdefault(m.group(1), set()).add(float(pm.group(1)))
    if any(len(v) > 1 for v in seen.values()):
        return {}
    found = {k: next(iter(v)) for k, v in seen.items()}
    return found if len(found) >= 2 else {}


_NEEDS_REVIEW = re.compile(r"\b(times|double|multipl\w*|additional|extra|plus|discount|promotional|"
                           r"launch|minimum|first|rounded|tokens?|each additional)\b", re.I)


def review_reason(text, est):
    """Why an automatic price rule must be checked by a person, or None.

    A rule is trusted only when fal's text states ONE price (or exactly the prices in the
    per-option table) and no multiplier, extra, discount, minimum or token clause.
    """
    if not text:
        return "fal gives no price text - confirm the billing unit on the model page"
    prices = set(float(p) for p in re.findall(_PRICE, text))
    rate = est.get("rate")
    listed = set(float(v) for v in rate["map"].values()) if isinstance(rate, dict) else {float(rate)}
    if prices - listed:
        return "fal's text names prices the rule does not use: %s" % sorted(prices - listed)
    word = _NEEDS_REVIEW.search(text)
    if word:
        return "fal's text says '%s' - a multiplier, extra, discount or minimum may apply" % word.group(0)
    return None


def default_estimate(billing, inputs, text=""):
    """A first guess at the price rules from fal's billing unit, pricing text and the inputs.

    Marked ``"auto": true`` - re-running add_model (or --reprice) regenerates it. Remove that
    flag after tuning a rule by hand and it is kept from then on.
    """
    if not billing:
        return None, ""
    unit, price = billing["unit"].strip().lower(), float(billing["price"])
    per = UNIT_PER.get(unit)
    if per is None:
        return None, "$%g per %s" % (price, unit)
    names = {i["name"]: i for i in inputs}
    lower = (text or "").lower()
    est = {"auto": True, "per": per, "rate": price}
    if per == "char":
        est["per"], est["rate"] = "kchar", price * 1000
    if re.search(r"round(?:ed|ing)?\s+up|rounded upwards|nearest (?:whole )?(?:minute|second|megapixel|hour)", lower):
        est["round_up"] = True

    # a rate that depends on the resolution / quality the node sends
    for field in ("resolution", "quality", "video_quality", "mode"):
        inp = names.get(field)
        if inp and inp["kind"] == "enum":
            rates = _rates_from_text(text, inp["options"])
            if rates:
                est["rate"] = {"widget": field, "map": rates}
                break

    if est["per"] in ("second", "minute", "hour"):
        dur = next((names[n] for n in DURATION_WIDGETS if n in names
                    and names[n]["kind"] in ("enum", "int", "float")), None)
        if dur:
            q = {"widget": dur["name"]}
            numeric = [float(o) for o in dur.get("options", []) if re.fullmatch(r"[0-9.]+", str(o).rstrip("s"))]
            if dur["kind"] == "enum":
                q["auto_max"] = max(numeric) if numeric else 10
            elif "not_set" in dur:          # -1 = the model decides: count the longest it can be
                q["auto_max"] = float(dur.get("max") or 30)
                if dur["name"].endswith("_ms"):
                    q["auto_max"] = q["auto_max"] / 1000.0
            if dur["name"].endswith("_ms"):
                q["scale"] = 0.001
            est["quantity"] = q
        else:
            sockets = [i["name"] for i in inputs if i["kind"] in ("audio", "video")]
            audio = [s for s in sockets if s.startswith("audio")]
            if sockets:
                est["quantity"] = {"media": audio or sockets}
                est["label"] = " of %s" % ("audio" if audio else "video")
            else:
                est["quantity"] = {"unknown": True}
                est["label"] = " of output"
    elif est["per"] == "kchar":
        field = next((n for n in TEXT_WIDGETS if n in names and names[n]["kind"] in ("text", "string")), None)
        est["quantity"] = {"chars": field} if field else {"unknown": True}
    elif est["per"] == "megapixel":
        size = next((i["name"] for i in inputs if i["kind"] == "image_size"), None)
        count = next((n for n in COUNT_WIDGETS if n in names), None)
        # no size box: the output follows the input picture (the run measures it)
        est["quantity"] = {"megapixels": size or ""}
        if count:
            est["quantity"]["count"] = count
    elif est["per"] == "image":
        count = next((n for n in COUNT_WIDGETS if n in names), None)
        if count:
            est["quantity"] = {"widget": count}

    why = review_reason(text, est)
    if why:
        est["review"] = why            # tests/fal/test_prices.py fails until a person prices it

    unit_text = {"second": "/s", "minute": "/min", "hour": "/hour", "kchar": "/1k chars",
                 "megapixel": "/MP", "image": "/image", "run": "/run"}[est["per"]]
    rate = est["rate"]
    if isinstance(rate, dict):
        vals = sorted(rate["map"].values())
        tag = "$%g-%g%s" % (vals[0], vals[-1], unit_text)
    else:
        tag = "$%g%s" % (rate, unit_text)
    return est, tag


def reprice(spec):
    """Regenerate an auto (or missing) price rule from the file's own billing + text + inputs."""
    old = (spec.get("pricing") or {}).get("estimate")
    if old is not None and not old.get("auto"):
        return False
    est, tag = default_estimate((spec.get("pricing") or {}).get("billing"), spec["inputs"],
                                (spec.get("pricing") or {}).get("text") or "")
    spec["pricing"]["estimate"] = est
    spec["pricing"]["tag"] = tag
    return True


def build(endpoint, args, old):
    root = json.loads(fetch(OPENAPI_URL % urllib.parse.quote(endpoint, safe="/")))
    meta = root.get("info", {}).get("x-fal-metadata", {})
    catalog = (args.catalog_cache or {}).get(endpoint)
    if catalog is None:
        catalog = {}
        try:
            data = json.loads(fetch(CATALOG_URL % urllib.parse.quote(endpoint, safe="/")))
            for m in data.get("models", []):
                if m.get("endpoint_id") == endpoint:
                    catalog = m.get("metadata") or {}
        except Exception as exc:                        # noqa: BLE001
            print("   (catalog lookup failed for %s: %s)" % (endpoint, exc))
    inputs, omitted = schema_convert.convert_inputs(root)
    if old and not args.reset:
        inputs, omitted = schema_convert.apply_overrides(inputs, omitted, old.get("hide"),
                                                         old.get("labels"))
    outputs = schema_convert.convert_outputs(root)
    billing, text = page_pricing(endpoint)
    est, tag = default_estimate(billing, inputs, text or "")

    spec = {
        "endpoint_id": endpoint,
        "class_key": class_key(endpoint),
        "title": args.title or humanize(endpoint),
        "category": args.category or category_for(endpoint, catalog.get("category") or meta.get("category")),
        "folder": file_stem(endpoint),
        "page": PAGE_URL % endpoint,
        "description": catalog.get("description") or meta.get("about") or "",
        "pricing": {"billing": billing, "text": text, "tag": tag, "estimate": est},
        "checks": {},
        "inputs": inputs,
        "omitted": omitted,
        "outputs": outputs,
    }
    if old and not args.reset:
        for field in KEEP_ON_REFRESH:
            if field in old:
                spec[field] = old[field]
        old_est = (old.get("pricing") or {}).get("estimate")
        if old_est is not None and not old_est.get("auto"):      # hand-tuned: keep it
            for field in ("estimate", "tag"):
                if (old.get("pricing") or {}).get(field) is not None:
                    spec["pricing"][field] = old["pricing"][field]
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("links", nargs="*", help="fal model links or endpoint ids")
    parser.add_argument("--reprice", action="store_true",
                        help="no network: regenerate every auto price rule from the saved files")
    parser.add_argument("--category", help="Image | Video | Lip Sync | Audio | Other")
    parser.add_argument("--title", help="node title (single link only)")
    parser.add_argument("--reset", action="store_true",
                        help="regenerate hand-tuned fields too (changes nothing else)")
    parser.add_argument("--checked", default=None, help="date to stamp on the pricing")
    parser.add_argument("--catalog", action="append", default=[],
                        help="a saved catalog file {endpoint: metadata} to use instead of "
                             "asking fal's catalog (which rate-limits) for every model")
    parser.add_argument("--skip-existing", action="store_true",
                        help="leave models that already have a file untouched")
    parser.add_argument("--workers", type=int, default=1, help="models fetched at once")
    args = parser.parse_args()
    args.catalog_cache = {}
    for path in args.catalog:
        with open(path, "r", encoding="utf-8") as handle:
            args.catalog_cache.update(json.load(handle))
    if args.reprice:
        changed = 0
        for name in sorted(os.listdir(MODELS_DIR)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(MODELS_DIR, name)
            with open(path, "r", encoding="utf-8") as handle:
                spec = json.load(handle)
            if reprice(spec):
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(spec, handle, ensure_ascii=False, indent=1)
                    handle.write("\n")
                changed += 1
                print("%-58s %-18s %s" % (spec["endpoint_id"], spec["pricing"]["tag"],
                                         json.dumps(spec["pricing"]["estimate"])[:150]))
        print("\nrepriced %d model(s); hand-tuned rules were left alone." % changed)
        return
    if not args.links:
        parser.error("give one or more fal model links (or --reprice)")
    if args.title and len(args.links) > 1:
        parser.error("--title works with one link at a time")
    os.makedirs(MODELS_DIR, exist_ok=True)
    import datetime
    checked = args.checked or datetime.date.today().isoformat()

    def one(link):
        endpoint = endpoint_from(link)
        path = os.path.join(MODELS_DIR, file_stem(endpoint) + ".json")
        old = None
        if os.path.exists(path):
            if args.skip_existing:
                return None
            with open(path, "r", encoding="utf-8") as handle:
                old = json.load(handle)
        spec = build(endpoint, args, old)
        spec["pricing"]["checked"] = checked
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(spec, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        os.replace(tmp, path)                  # never leave a half-written model file
        return endpoint, path, spec, old is not None

    from concurrent.futures import ThreadPoolExecutor
    failures = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [(link, pool.submit(one, link)) for link in args.links]
        for link, future in futures:
            try:
                done = future.result()
            except Exception as exc:                    # noqa: BLE001 - report, carry on
                failures.append((link, exc))
                print("== %s  FAILED: %s" % (link, exc))
                continue
            if done is None:
                continue
            endpoint, path, spec, refreshed = done
            print("== %s%s" % (endpoint, "  (refresh)" if refreshed else ""))
            print("   node   : %s   [%s]" % (spec["title"], spec["category"]))
            print("   inputs : %s" % ", ".join("%s(%s)" % (i["name"], i["kind"]) for i in spec["inputs"]))
            if spec["omitted"]:
                print("   left out: %s" % "; ".join("%s - %s" % (o["path"], o["why"]) for o in spec["omitted"]))
            print("   outputs: %s" % ", ".join("%s(%s)" % (o["field"], o["kind"]) for o in spec["outputs"]))
            print("   price  : %s" % spec["pricing"]["text"])
            print("   estimate rules: %s" % json.dumps(spec["pricing"]["estimate"]))
            why = (spec["pricing"].get("estimate") or {}).get("review")
            if why:
                print("   !! CHECK THE PRICE BY HAND: %s" % why)
                print("   !! then remove \"review\" from its estimate; tests/fal/test_prices.py fails until then")
            print("   written: %s" % path)
    if failures:
        print("\n%d model(s) failed - run them again: %s" % (len(failures), " ".join(l for l, _ in failures)))
    print("\nRestart ComfyUI to load new or changed models.")


if __name__ == "__main__":
    main()
