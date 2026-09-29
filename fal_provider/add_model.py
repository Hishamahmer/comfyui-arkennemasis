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
text it prints - see README.md for the rule format.
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


def category_for(endpoint, fal_category):
    e = endpoint.lower()
    if "lipsync" in e or "lip-sync" in e or e.startswith("veed/fabric"):
        return "Lip Sync"
    c = (fal_category or "").lower()
    if c.endswith("-to-image") or c == "image-to-image":
        return "Image"
    if c.endswith("-to-video") or c == "video-to-video":
        return "Video"
    if "audio" in c or "speech" in c:
        return "Audio"
    return "Other"


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


def default_estimate(billing, inputs):
    """A first guess at the estimate rules from fal's billing unit."""
    if not billing:
        return None, ""
    unit, price = billing["unit"], billing["price"]
    names = {i["name"]: i for i in inputs}
    sockets = []
    for i in inputs:
        if i["kind"] in ("audio", "video"):
            sockets.append(i["name"])
    per = {"seconds": "second", "minutes": "minute", "images": "image"}.get(unit)
    if not per:
        return None, ""
    est = {"per": per, "rate": price}
    if per == "image":
        if "num_images" in names:
            est["quantity"] = {"widget": "num_images"}
        tag = "$%g/image" % price
    else:
        dur = names.get("duration")
        if dur and dur["kind"] in ("enum", "int"):
            numeric = [float(o) for o in dur.get("options", []) if re.fullmatch(r"[0-9.]+", str(o))]
            est["quantity"] = {"widget": "duration", "auto_max": max(numeric) if numeric else 10}
        elif sockets:
            audio = [s for s in sockets if s.startswith("audio")]
            est["quantity"] = {"media": audio or sockets}
            est["label"] = " of %s" % ("audio" if audio else "video")
        tag = "$%g/%s" % (price, "s" if per == "second" else "min")
    return est, tag


def build(endpoint, args, old):
    root = json.loads(fetch(OPENAPI_URL % urllib.parse.quote(endpoint, safe="/")))
    meta = root.get("info", {}).get("x-fal-metadata", {})
    catalog = {}
    try:
        data = json.loads(fetch(CATALOG_URL % urllib.parse.quote(endpoint, safe="/")))
        for m in data.get("models", []):
            if m.get("endpoint_id") == endpoint:
                catalog = m.get("metadata") or {}
    except Exception as exc:                            # noqa: BLE001
        print("   (catalog lookup failed: %s)" % exc)
    inputs, omitted = schema_convert.convert_inputs(root)
    if old and not args.reset:
        inputs, omitted = schema_convert.apply_overrides(inputs, omitted, old.get("hide"),
                                                         old.get("labels"))
    outputs = schema_convert.convert_outputs(root)
    billing, text = page_pricing(endpoint)
    est, tag = default_estimate(billing, inputs)

    spec = {
        "endpoint_id": endpoint,
        "class_key": class_key(endpoint),
        "title": args.title or catalog.get("display_name") or endpoint,
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
        for field in ("estimate", "tag"):
            if (old.get("pricing") or {}).get(field) is not None:
                spec["pricing"][field] = old["pricing"][field]
    return spec


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("links", nargs="+", help="fal model links or endpoint ids")
    parser.add_argument("--category", help="Image | Video | Lip Sync | Audio | Other")
    parser.add_argument("--title", help="node title (single link only)")
    parser.add_argument("--reset", action="store_true",
                        help="regenerate hand-tuned fields too (changes nothing else)")
    parser.add_argument("--checked", default=None, help="date to stamp on the pricing")
    args = parser.parse_args()
    if args.title and len(args.links) > 1:
        parser.error("--title works with one link at a time")
    os.makedirs(MODELS_DIR, exist_ok=True)
    import datetime
    checked = args.checked or datetime.date.today().isoformat()

    for link in args.links:
        endpoint = endpoint_from(link)
        path = os.path.join(MODELS_DIR, file_stem(endpoint) + ".json")
        old = None
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                old = json.load(handle)
        print("== %s%s" % (endpoint, "  (refresh)" if old else ""))
        spec = build(endpoint, args, old)
        spec["pricing"]["checked"] = checked
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(spec, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        print("   node   : %s   [%s]" % (spec["title"], spec["category"]))
        print("   inputs : %s" % ", ".join("%s(%s)" % (i["name"], i["kind"]) for i in spec["inputs"]))
        if spec["omitted"]:
            print("   left out: %s" % "; ".join("%s - %s" % (o["path"], o["why"]) for o in spec["omitted"]))
        print("   outputs: %s" % ", ".join("%s(%s)" % (o["field"], o["kind"]) for o in spec["outputs"]))
        print("   price  : %s" % spec["pricing"]["text"])
        print("   estimate rules: %s" % json.dumps(spec["pricing"]["estimate"]))
        print("   written: %s" % path)
    print("\nRestart ComfyUI to load new or changed models.")


if __name__ == "__main__":
    main()
