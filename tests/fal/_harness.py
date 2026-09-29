"""Load the pack's fal_provider exactly as ComfyUI would, outside the running server.

Run every fal test with ComfyUI's own Python from the portable root, e.g.
    python_embeded\\python.exe ComfyUI\\custom_nodes\\comfyui-arkennemasis\\tests\\fal\\test_build.py

A fake parent package stands in for the hyphenated pack folder so the relative imports
(..common) resolve without running the pack's __init__ (which would load every other
module). The GPU is hidden so a test can never touch a running ComfyUI's VRAM. Test
output goes to %TEMP%\\arkennemasis_fal_tests, never into the install.
"""
import importlib
import os
import sys
import tempfile
import types

os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
HERE = os.path.dirname(os.path.abspath(__file__))
PACK = os.path.abspath(os.path.join(HERE, "..", ".."))
COMFY = os.path.abspath(os.path.join(PACK, "..", ".."))
WORK = os.path.join(tempfile.gettempdir(), "arkennemasis_fal_tests")
os.makedirs(WORK, exist_ok=True)

sys.argv = [sys.argv[0], "--cpu"]
if COMFY not in sys.path:
    sys.path.insert(0, COMFY)
os.chdir(COMFY)
if "arkpack" not in sys.modules:
    pkg = types.ModuleType("arkpack")
    pkg.__path__ = [PACK]
    sys.modules["arkpack"] = pkg

fal = importlib.import_module("arkpack.fal_provider")
node = importlib.import_module("arkpack.fal_provider.node")
client = importlib.import_module("arkpack.fal_provider.client")
media = importlib.import_module("arkpack.fal_provider.media")
pricing = importlib.import_module("arkpack.fal_provider.pricing")
history = importlib.import_module("arkpack.fal_provider.history")
recover = importlib.import_module("arkpack.fal_provider.recover")

MODEL_CLASSES = {k: c for k, c in fal.NODE_CLASS_MAPPINGS.items() if hasattr(c, "FAL_SPEC")}


def schema_path(endpoint):
    """Where fetch_schemas.py saved fal's OpenAPI for an endpoint."""
    return os.path.join(WORK, "schemas", endpoint.replace("/", "_") + ".json")
