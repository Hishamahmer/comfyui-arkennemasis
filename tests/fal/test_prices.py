"""The price estimates against fal's OWN numbers - not against our own rules.

test_badges proves the badge and the Python estimate agree with each other; that cannot
catch a rule that is wrong in both (2026-09-30: fourteen MiniMax H3 tables were shifted by
one resolution, Nano Banana 2 estimated $0 at 1K, megapixels were divided by 1,000,000).
This test holds the estimate to prices written down from fal's pricing pages and to their
own worked examples ("a 5-second 768p video costs $0.40"), plus rules that must hold for
every model:

  * every option of a priced dropdown has a price (a missing one used to count as $0)
  * no paid model estimates $0 at its default settings
  * no rule still carries a "review" marker from add_model.py
  * the add_model.py price reader pairs each resolution with ITS price, or refuses
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import MODEL_CLASSES, pricing  # noqa: E402

add_model = sys.modules.get("arkpack.fal_provider.add_model")
if add_model is None:
    import importlib
    add_model = importlib.import_module("arkpack.fal_provider.add_model")

BY_ENDPOINT = {c.FAL_SPEC["endpoint_id"]: c for c in MODEL_CLASSES.values()}
report = []


def check(ok, label, detail=""):
    report.append(("ok" if ok else "FAIL", label, detail))


def defaults(cls):
    info = cls.GET_NODE_INFO_V1()
    values = {}
    for section in ("required", "optional"):
        for name, spec in info["input"].get(section, {}).items():
            opts = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if "default" in opts:
                values[name] = opts["default"]
            elif isinstance(spec[0], list) and spec[0]:
                values[name] = spec[0][0]
    return values


def price(endpoint, widgets=None, seconds=None, pictures=None):
    cls = BY_ENDPOINT[endpoint]
    values = dict(defaults(cls), **(widgets or {}))
    media = dict(seconds or {})
    if pictures:
        media[pricing.PICTURES] = pictures
    usd, _, _ = pricing.estimate(cls.FAL_SPEC["pricing"], values, media)
    return usd


MP_512, MP_1024 = 512 * 512 / pricing.MEGAPIXEL, 1.0

# (endpoint, widgets, clip seconds, pictures, fal's price, where the number comes from)
CASES = [
    # MiniMax H3 Max - post-launch-discount rates (discount ended 2026-09-30)
    ("minimax/h3-max/image-to-video", {"resolution": "1080P", "duration": 5}, None, None, 0.80, "$0.16/s at 1080p"),
    ("minimax/h3-max/image-to-video", {"resolution": "480P", "duration": 5}, None, None, 0.25, "$0.05/s at 480p"),
    ("minimax/h3-max/image-to-video", {"resolution": "768P", "duration": 10}, None, None, 0.80, "$0.08/s at 768p"),
    ("minimax/h3-max/text-to-video", {"resolution": "1080P", "duration": 5}, None, None, 0.80, "$0.16/s at 1080p"),
    ("minimax/h3-max/camera-controls", {"resolution": "480P", "duration": 5}, None, None, 0.25, "$0.05/s at 480p"),
    ("minimax/h3-max-turbo/text-to-video", {"resolution": "1080P", "duration": 5}, None, None, 0.40, "$0.08/s at 1080p"),
    ("minimax/h3-max-turbo/image-to-video", {"resolution": "480P", "duration": 5}, None, None, 0.125, "$0.025/s at 480p"),
    ("minimax/h3-max/extend-video", {"resolution": "2K", "duration": 5}, None, None, 1.60, "$0.32/s at 2K"),
    ("minimax/h3-max/extend-video", {"resolution": "480P", "duration": 5}, None, None, 0.25, "$0.05/s at 480p"),
    ("minimax/h3-max/insert-video", {"resolution": "480p", "duration": 5}, None, None, 0.25, "$0.05/s at 480p"),
    ("minimax/h3-max/reference-to-video", {"resolution": "768P", "duration": 5}, None, None, 0.40, "fal: '5-second 768p output costs $0.40'"),
    ("minimax/h3-max/lip-sync/image-to-video", {"resolution": "768P"}, {"audio": 5.0}, None, 0.40, "fal: '5-second video at 768p costs $0.40'"),
    ("minimax/h3-max/lip-sync/image-to-video", {"resolution": "768P"}, {"audio": 30.0}, None, 2.88, "fal: '30-second video at 768p costs $2.88'"),
    ("minimax/h3-max/styles/vhs", {"duration": 15}, None, None, 1.20, "fal: '15-second video costs $1.20'"),
    # MiniMax H3
    ("minimax/h3/text-to-video", {"resolution": "768P", "duration": 5}, None, None, 0.30, "$0.06/s at 768p"),
    ("minimax/h3/image-to-video", {"resolution": "2K", "duration": 5}, None, None, 0.65, "$0.13/s at 2K"),
    ("minimax/h3/reference-to-video", {"resolution": "480P", "duration": 5}, None, None, 0.25, "$0.05/s at 480p"),
    ("minimax/h3/text-to-video/lora", {"resolution": "4K", "duration": 5}, None, None, 1.00, "$0.20/s at 4K"),
    ("minimax/h3/image-to-video/lora", {"resolution": "480P", "duration": 5}, None, None, 0.3125, "$0.0625/s at 480p"),
    # Hailuo
    ("fal-ai/minimax/hailuo-02/standard/image-to-video", {"resolution": "768P", "duration": "6"}, None, None, 0.27, "fal: '6 second 768p video will cost $0.27'"),
    ("fal-ai/minimax/hailuo-2.3/standard/image-to-video", {"duration": "10"}, None, None, 0.56, "$0.56 per 10 second video"),
    # Nano Banana
    ("fal-ai/nano-banana-2", {"resolution": "0.5K", "num_images": 1}, None, None, 0.06, "0.75 x $0.08"),
    ("fal-ai/nano-banana-2", {"resolution": "1K", "num_images": 1}, None, None, 0.08, "$0.08 per image"),
    ("fal-ai/nano-banana-2", {"resolution": "2K", "num_images": 1}, None, None, 0.12, "1.5 x $0.08"),
    ("fal-ai/nano-banana-2", {"resolution": "4K", "num_images": 2}, None, None, 0.32, "2 x $0.08, two images"),
    ("fal-ai/nano-banana-2", {"resolution": "1K", "num_images": 1, "enable_web_search": True}, None, None, 0.095, "+$0.015 web search"),
    ("fal-ai/nano-banana-2/edit", {"resolution": "0.5K", "num_images": 1}, None, None, 0.06, "0.75 x $0.08"),
    ("fal-ai/nano-banana-pro", {"resolution": "1K", "num_images": 1}, None, None, 0.15, "$0.15 per image"),
    ("fal-ai/nano-banana-pro", {"resolution": "4K", "num_images": 1}, None, None, 0.30, "4K is double"),
    ("fal-ai/nano-banana-pro/edit", {"resolution": "4K", "num_images": 1, "enable_web_search": True}, None, None, 0.315, "double + $0.015"),
    # Flux 2 - fal's own worked examples (megapixel = 1024 x 1024)
    ("fal-ai/flux-2-pro", {"image_size": "square_hd"}, None, None, 0.03, "fal: '1024x1024 image will cost $0.03'"),
    ("fal-ai/flux-2-pro", {"image_size": "custom", "image_size_width": 1920, "image_size_height": 1080}, None, None, 0.045, "fal: '1920x1080 image will cost $0.045'"),
    ("fal-ai/flux-2-flex", {"image_size": "custom", "image_size_width": 1920, "image_size_height": 1080}, None, None, 0.10, "fal: '1920x1080 image will cost $0.10'"),
    ("fal-ai/flux-2-flex", {"image_size": "square"}, None, None, 0.05, "fal: '512x512 output will cost $0.05'"),
    ("fal-ai/flux-2-max", {"image_size": "square_hd"}, None, None, 0.07, "first megapixel $0.07"),
    ("fal-ai/flux-2/edit", {"image_size": "square_hd", "num_images": 1}, None, {"image": [MP_512]}, 0.024, "fal: '1024x1024 with 512*512 input will cost 0.024'"),
    ("fal-ai/flux-2/klein/9b/edit", {"image_size": "square_hd", "num_images": 1}, None, {"image": [MP_512]}, 0.022, "fal: '... will cost $0.022'"),
    ("fal-ai/flux-2/lora/edit", {"image_size": "square_hd", "num_images": 1}, None, {"image": [MP_512]}, 0.042, "fal: '... will cost 0.042'"),
    ("fal-ai/flux-pro/v1/vto", {}, None, {"human_image": [MP_1024], "garment_image": [MP_1024]}, 0.0475, "fal: 'two 1024x1024 inputs ... cost $0.0475'"),
    ("fal-ai/flux-pro/v1/erase", {}, None, {"image": [MP_1024]}, 0.042, "$0.03 first MP + 3 MP reference minimum x $0.004"),
    ("fal-ai/flux-vision-upscaler", {"upscale_factor": 2}, None, {"image": [MP_1024]}, 0.40, "4 output MP x $0.10"),
    ("fal-ai/flux/dev/image-to-image", {"num_images": 1}, None, {"image": [4.0]}, 0.12, "4 MP picture x $0.03/MP"),
    # Flux 3 video
    ("blackforestlabs/flux-3/image-to-video", {"resolution": "1080p", "duration": "10"}, None, None, 2.90, "$0.29/s at 1080p"),
    # Seedance 2.5 US (the user's cost slide)
    ("bytedance/seedance-2.5/us/image-to-video", {"resolution": "720p", "duration": "5"}, None, None, 2.838, "$0.5676/s at 720p"),
    ("bytedance/seedance-2.5/us/reference-to-video", {"resolution": "1080p", "duration": "5"}, None, None, 6.98139, "$1.396278/s at 1080p"),
    # Avatars / lip sync (the user's cost slide)
    ("veed/fabric-1.0", {"resolution": "720p"}, {"audio": 30.0}, None, 4.50, "slide: 30 s at 720p = $4.50"),
    ("veed/fabric-1.0", {"resolution": "480p"}, {"audio": 30.0}, None, 2.40, "$0.08/s at 480p"),
    ("fal-ai/sync-lipsync/v3", {}, {"video": 30.0, "audio": 30.0}, None, 4.00, "slide: $8/min, 30 s = $4.00"),
    ("fal-ai/sync-lipsync/v2/pro", {}, {"video": 30.0, "audio": 30.0}, None, 2.50, "slide: $5/min, 30 s = $2.50"),
    ("fal-ai/sync-lipsync/react-1", {}, {"video": 30.0, "audio": 30.0}, None, 5.00, "$10/min"),
    ("fal-ai/sam-3-1/video", {}, {"video": 30.0}, None, 0.5625, "$0.01 per 16 frames, 30 s at 30 fps"),
    ("fal-ai/kling-video/v3/pro/motion-control", {}, {"video": 10.0}, None, 1.68, "$0.168/s"),
    # Audio
    ("fal-ai/elevenlabs/tts/eleven-v3", {"text": "x" * 1000}, None, None, 0.10, "$0.10 per 1000 characters"),
    ("fal-ai/elevenlabs/music", {"music_length_ms": 30000}, None, None, 0.60, "fal: 30 s billed as 1 minute"),
    ("fal-ai/minimax/speech-2.8-hd", {"prompt": "x" * 2000}, None, None, 0.20, "$0.10 per 1000 characters"),
]

for ep, widgets, seconds, pics, want, source in CASES:
    if ep not in BY_ENDPOINT:
        check(False, "%s is installed" % ep)
        continue
    got = price(ep, widgets, seconds, pics)
    check(got is not None and abs(got - want) < 1e-6, "%s %s" % (ep, widgets or ""),
          "estimate %s, fal %s (%s)" % ("none" if got is None else "%.6g" % got, want, source))

# every option of a priced dropdown has a price; no review markers; no $0 at the defaults
gaps, reviews, zeros, checked = [], [], [], 0
for cls in MODEL_CLASSES.values():
    spec = cls.FAL_SPEC
    est = (spec.get("pricing") or {}).get("estimate") or {}
    if est.get("review"):
        reviews.append("%s: %s" % (spec["endpoint_id"], est["review"]))
    if not est.get("per"):
        continue
    inputs = {i["name"]: i for i in spec["inputs"]}
    for rate in [est.get("rate")] + [m for m in est.get("multiply") or []]:
        if isinstance(rate, dict) and "map" in rate and rate is est.get("rate"):
            opts = (inputs.get(rate["widget"]) or {}).get("options") or []
            missing = [o for o in opts if pricing._norm(o) not in rate["map"]
                       and pricing._norm(o) not in ("auto", "(not set)")]
            if missing:
                gaps.append("%s: %s %s" % (spec["endpoint_id"], rate["widget"], missing))
    q = est.get("quantity") or {}
    seconds = {m: 10.0 for m in q.get("media", [])}
    values = defaults(cls)
    for w in ("chars", "words"):
        if q.get(w):
            values[q[w]] = "a test sentence of some length for the estimate"
    usd, _, _ = pricing.estimate(spec["pricing"], values, seconds)
    checked += 1
    if usd is not None and usd <= 0:
        zeros.append(spec["endpoint_id"])
check(not gaps, "every option of every priced dropdown has a price", "; ".join(gaps[:6]))
check(not reviews, "no price rule is waiting for a manual review", "; ".join(reviews[:4]))
check(not zeros, "no paid model estimates $0 at its defaults (%d checked)" % checked, ", ".join(zeros[:8]))

# an option missing from a table costs the table's highest price - never $0
fake = {"estimate": {"per": "second", "rate": {"widget": "resolution", "map": {"480p": 0.05, "1080p": 0.16}},
                     "quantity": {"widget": "duration"}}}
usd, _, _ = pricing.estimate(fake, {"resolution": "4K", "duration": 10}, {})
check(abs(usd - 1.6) < 1e-9, "an unpriced option falls back to the highest price, not $0", "%s" % usd)

# the add_model.py reader: each resolution gets ITS price, or nothing
R = add_model._rates_from_text
h3 = "Video costs $0.05 per second at 480p, $0.06 per second at 768p, $0.13 per second at 2K and $0.16 per second at 4K."
check(R(h3, ["480P", "768P", "2K", "4K"]) == {"480p": 0.05, "768p": 0.06, "2k": 0.13, "4k": 0.16},
      "reader: '$X per second at OPTION' pairs correctly", str(R(h3, ["480P", "768P", "2K", "4K"])))
veed = "480p - $0.08 per second, 720p - $0.15 per second"
check(R(veed, ["720p", "480p"]) == {"480p": 0.08, "720p": 0.15}, "reader: 'OPTION - $X' pairs correctly",
      str(R(veed, ["720p", "480p"])))
hailuo = ("For each second of video generated at 768P, it will cost $0.045/sec. For each second of video "
          "generated at 512P, it will cost roughly $0.017/sec.")
check(R(hailuo, ["512P", "768P"]) == {"768p": 0.045, "512p": 0.017}, "reader: 'at OPTION, it will cost $X'",
      str(R(hailuo, ["512P", "768P"])))
discount = ("Video costs $0.025 per second at 480p, $0.04 per second at 768p, and $0.08 per second at 1080p. "
            "The discount ends September 30, after which 480p is $0.05/second, 768p is $0.08/second, and "
            "1080p is $0.16/second.")
check(R(discount, ["480P", "768P", "1080P"]) == {}, "reader: two prices for one option -> refuses to guess",
      str(R(discount, ["480P", "768P", "1080P"])))
est = {"per": "second", "rate": 0.08}
check(add_model.review_reason(discount, est) is not None, "reader: a discount text is marked for review",
      str(add_model.review_reason(discount, est)))
check(add_model.review_reason("Your request will cost $0.08 per image.", {"per": "image", "rate": 0.08}) is None,
      "reader: a one-price text is trusted")

fails = [r for r in report if r[0] != "ok"]
for status, label, detail in report:
    if status != "ok" or "-v" in sys.argv:
        print("%-4s %s%s" % (status, label, ("  [%s]" % detail) if detail else ""))
print("\n%d price checks against fal's own numbers, %d failures" % (len(report), len(fails)))
sys.exit(1 if fails else 0)
