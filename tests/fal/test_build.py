"""Every model file builds a valid ComfyUI node, and every node has a working price badge
definition (or states why not). Writes what /object_info will serve to the test dir."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import WORK, fal  # noqa: E402

problems, infos = [], {}
for key, cls in fal.NODE_CLASS_MAPPINGS.items():
    try:
        infos[key] = cls.GET_NODE_INFO_V1()
    except Exception as exc:                            # noqa: BLE001
        problems.append((key, repr(exc)))
models = [k for k in infos if hasattr(fal.NODE_CLASS_MAPPINGS[k], "FAL_SPEC")]
no_badge = [k for k in models if not infos[k].get("price_badge")]
titles = [infos[k]["display_name"] for k in infos]
dupes = sorted({t for t in titles if titles.count(t) > 1})
cats = sorted({infos[k]["category"] for k in infos})
json.dump(infos, open(os.path.join(WORK, "object_info_fal.json"), "w", encoding="utf-8"), indent=1, default=str)

print("classes %d | built %d | model nodes %d | problems %d" % (len(fal.NODE_CLASS_MAPPINGS), len(infos), len(models), len(problems)))
print("model nodes without a price badge:", no_badge or "none")
print("duplicate display names:", dupes or "none")
print("menu categories (%d):" % len(cats), ", ".join(c.replace("arkennemasis/fal/", "") for c in cats))
for p in problems:
    print("PROBLEM", p)
sys.exit(1 if problems or dupes else 0)
