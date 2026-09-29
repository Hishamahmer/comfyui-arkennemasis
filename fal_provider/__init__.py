"""fal.ai nodes - one node per model, built from the JSON files in ``models/``.

Adding a model is one command (see README.md):

    python_embeded\\python.exe ComfyUI\\custom_nodes\\comfyui-arkennemasis\\fal_provider\\add_model.py <fal model link>

then restart ComfyUI. Each file is loaded on its own, so one broken file costs only its
own node.
"""

import glob
import json
import os

from .history import ArkFalHistory
from .node import build_node_class, display_name
from .recover import ArkFalRecover

MODELS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models")

NODE_CLASS_MAPPINGS = {"ArkFalHistory": ArkFalHistory, "ArkFalRecover": ArkFalRecover}
NODE_DISPLAY_NAME_MAPPINGS = {"ArkFalHistory": "arkennemasis fal · History (free)",
                              "ArkFalRecover": "arkennemasis fal · Recover Result (free)"}

for _path in sorted(glob.glob(os.path.join(MODELS_DIR, "*.json"))):
    try:
        with open(_path, "r", encoding="utf-8") as _handle:
            _spec = json.load(_handle)
        _cls = build_node_class(_spec)
        _cls.GET_SCHEMA()                      # fail here, per model, not in the menu later
        NODE_CLASS_MAPPINGS[_spec["class_key"]] = _cls
        NODE_DISPLAY_NAME_MAPPINGS[_spec["class_key"]] = display_name(_spec)
    except Exception as _exc:                  # noqa: BLE001 - one bad file must not stop the rest
        print("[arkennemasis] fal model %s not loaded: %s" % (os.path.basename(_path), _exc))

ArkFalHistory.MODEL_TITLES = ["all models"] + sorted(
    {c.FAL_SPEC["title"] for c in NODE_CLASS_MAPPINGS.values() if hasattr(c, "FAL_SPEC")})
