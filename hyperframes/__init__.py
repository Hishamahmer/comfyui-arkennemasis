"""HyperFrames in ComfyUI: render HTML video projects to MP4 from a node.

A composer node upstream writes one or more HyperFrames projects (``index.html`` plus an
``assets`` folder) and hands over a list of render jobs; this node renders them. Keeping
the two apart is what lets an agent, or a person, edit a project between composing and
rendering without either node knowing.
"""

from __future__ import annotations

import json
import os

from .runtime import QUALITIES, render


class ArkHyperFramesRender:
    CATEGORY = "arkennemasis/Video"
    FUNCTION = "run"
    OUTPUT_NODE = True
    RETURN_TYPES = ("VIDEO", "STRING", "STRING")
    RETURN_NAMES = ("video", "paths", "report")
    DESCRIPTION = ("Render HyperFrames projects to MP4. `jobs` is a JSON list of "
                   "{project_dir, output_path} — one per format. Installs the pinned "
                   "HyperFrames CLI on first use (needs Node.js 22+ and FFmpeg).")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "jobs": ("STRING", {
                    "forceInput": True,
                    "tooltip": "JSON list of {project_dir, output_path}, from the node that "
                               "wrote the HyperFrames projects.",
                }),
                "fps": ("INT", {
                    "default": 25, "min": 12, "max": 60,
                    "tooltip": "Output frame rate. The reference ads are 25 fps.",
                }),
                "quality": (QUALITIES, {
                    "tooltip": "standard for delivery; draft renders faster for checking "
                               "a cut.",
                }),
                "workers": ("INT", {
                    "default": 0, "min": 0, "max": 32,
                    "tooltip": "Parallel Chrome capture workers. 0 lets HyperFrames "
                               "decide. Each costs ~256 MB of RAM.",
                }),
            },
        }

    def run(self, jobs, fps, quality, workers):
        import folder_paths
        from comfy_api.latest._input_impl.video_types import VideoFromFile

        items = json.loads(jobs)
        if not items:
            raise RuntimeError("ArkHyperFramesRender: no render jobs were given.")
        paths, lines = [], []
        for job in items:
            summary = render(job["project_dir"], job["output_path"], fps=fps,
                             quality=quality, workers=workers)
            size = os.path.getsize(job["output_path"]) / 1e6
            line = "%s  %.1f MB | %s" % (os.path.basename(job["output_path"]), size, summary)
            print("[arkennemasis] HyperFrames: %s" % line)
            paths.append(job["output_path"])
            lines.append(line)
        # Show the renders on the node, as SaveVideo does, when they sit under output/.
        root = folder_paths.get_output_directory()
        shown = [{"filename": os.path.basename(p), "type": "output",
                  "subfolder": os.path.relpath(os.path.dirname(p), root).replace("\\", "/")}
                 for p in paths if os.path.abspath(p).startswith(os.path.abspath(root))]
        return {"ui": {"images": shown, "animated": (True,)},
                "result": (VideoFromFile(paths[0]), json.dumps(paths), "\n".join(lines))}


NODE_CLASS_MAPPINGS = {"ArkHyperFramesRender": ArkHyperFramesRender}
NODE_DISPLAY_NAME_MAPPINGS = {
    "ArkHyperFramesRender": "arkennemasis HyperFrames Render (HTML project -> MP4)",
}
