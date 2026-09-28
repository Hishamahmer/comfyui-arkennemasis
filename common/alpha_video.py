"""Frames plus a mask in, a transparent video out: VP9 WebM with alpha.

The cut-out of a presenter is only useful if it survives to the editor with its
transparency. VP9 with `yuva420p` is the format Chrome plays with alpha natively, which is
what HyperFrames renders through, and it is small — measured 3.3 MB for 15 s of 1080x1920.

Frames go to ffmpeg's stdin as raw RGBA; ffmpeg's own output goes to a log file, never to
a pipe nobody drains (a full pipe blocks the encoder forever).
"""

from __future__ import annotations

import os
import subprocess


class ArkAlphaVideoSave:
    CATEGORY = "arkennemasis/Video"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("path",)
    DESCRIPTION = ("Write frames with a mask as a transparent VP9 WebM (alpha = mask). For "
                   "cut-outs that must keep their transparency into an editor.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE",),
                "mask": ("MASK", {"tooltip": "1 = keep, 0 = transparent. Resized to the "
                                             "frames if it differs."}),
                "fps": ("FLOAT", {"default": 25.0, "min": 1.0, "max": 120.0}),
                "folder": ("STRING", {"forceInput": True,
                                      "tooltip": "Relative to ComfyUI's output directory."}),
                "name": ("STRING", {"default": "cutout"}),
            },
        }

    def run(self, images, mask, fps, folder, name):
        import folder_paths
        import torch

        frames, height, width = images.shape[0], images.shape[1], images.shape[2]
        if mask.dim() == 2:
            mask = mask.unsqueeze(0)
        if mask.shape[-2:] != (height, width):
            mask = torch.nn.functional.interpolate(mask.unsqueeze(1).float(), size=(height, width),
                                                   mode="bilinear", align_corners=False)[:, 0]
        out_dir = os.path.join(folder_paths.get_output_directory(), folder)
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "%s.webm" % name)
        log = path + ".log"
        args = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                "-s", "%dx%d" % (width, height), "-r", str(fps), "-i", "-",
                "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", "-b:v", "0", "-crf", "28",
                "-deadline", "realtime", "-cpu-used", "6", "-row-mt", "1", path]
        with open(log, "w", encoding="utf-8", errors="replace") as fh:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=fh, stderr=subprocess.STDOUT)
            for i in range(frames):
                alpha = mask[min(i, mask.shape[0] - 1)].clamp(0, 1).unsqueeze(-1)
                rgba = torch.cat([images[i, ..., :3], alpha], dim=-1)
                proc.stdin.write((rgba * 255).round().to(torch.uint8).cpu().numpy().tobytes())
            proc.stdin.close()
            code = proc.wait()
        if code != 0 or not os.path.isfile(path):
            with open(log, encoding="utf-8", errors="replace") as fh:
                raise RuntimeError("ArkAlphaVideoSave: ffmpeg failed: %s" % fh.read()[-800:])
        os.remove(log)
        print("[arkennemasis] alpha video: %d frames %dx%d -> %s (%.1f MB)"
              % (frames, width, height, os.path.basename(path), os.path.getsize(path) / 1e6))
        return (path,)


NODE_CLASS_MAPPINGS = {"ArkAlphaVideoSave": ArkAlphaVideoSave}
NODE_DISPLAY_NAME_MAPPINGS = {"ArkAlphaVideoSave": "arkennemasis Alpha Video Save (transparent WebM)"}
