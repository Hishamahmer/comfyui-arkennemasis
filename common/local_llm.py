"""A local LLM, no account: any GGUF under ``ComfyUI/models/LLM`` through llama.cpp.

**Why a child process.** llama.cpp allocates its own CUDA memory, outside ComfyUI's model
management, and does not give it all back when the Python object is deleted. A 26B model
left resident would sit on 16+ GB while the video models that follow try to load. So each
call runs ``local_llm_worker.py`` in a fresh interpreter, which exits when it has answered.
The worker's output goes to a file, never a pipe (an undrained pipe blocks the child).

**The chat template comes from the GGUF itself.** llama.cpp reads the template embedded in
the file, which is the only one guaranteed to match the model — pairing a model with the
wrong template produces fluent-looking nonsense rather than an error.

``schema`` (optional) constrains decoding to that JSON schema, so the answer cannot be
prose. It costs speed: measured on Gemma 4 26B-A4B, 81 tokens/s free-running against 7.6
tokens/s under a JSON grammar. A caller that can check and re-ask should prefer asking
for JSON in the prompt.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

_WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local_llm_worker.py")


def _llm_dir():
    import folder_paths
    return os.path.join(folder_paths.models_dir, "LLM")


def model_choices():
    root = _llm_dir()
    found = []
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if name.lower().endswith(".gguf") and not name.lower().startswith("mmproj"):
                found.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(found) or ["<no .gguf under ComfyUI/models/LLM>"]


def chat(model, system, prompt, schema=None, max_tokens=4096, temperature=0.7, seed=0,
         n_ctx=16384, timeout=900):
    """One chat turn in a child process. Returns the worker's result dict."""
    model_path = os.path.join(_llm_dir(), model)
    if not os.path.isfile(model_path):
        raise RuntimeError("ArkLocalLLM: no such model file: %s" % model_path)
    handle, job_path = tempfile.mkstemp(suffix=".json", prefix="ark_llm_job_")
    os.close(handle)
    out_path, log_path = job_path[:-5] + "_out.json", job_path[:-5] + ".log"
    job = {"model_path": model_path, "system": system, "prompt": prompt,
           "schema": schema, "max_tokens": max_tokens, "temperature": temperature,
           "seed": seed, "n_ctx": n_ctx, "out_path": out_path}
    with open(job_path, "w", encoding="utf-8") as fh:
        json.dump(job, fh, ensure_ascii=False)
    try:
        with open(log_path, "w", encoding="utf-8", errors="replace") as log:
            subprocess.run([sys.executable, _WORKER, job_path], stdout=log,
                           stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                           timeout=int(timeout))
        if not os.path.isfile(out_path):
            with open(log_path, encoding="utf-8", errors="replace") as log:
                tail = log.read()[-1500:]
            raise RuntimeError("ArkLocalLLM: the worker died without answering:\n%s" % tail)
        with open(out_path, encoding="utf-8") as fh:
            result = json.load(fh)
        if not result.get("ok"):
            raise RuntimeError("ArkLocalLLM failed: %s" % result.get("error"))
        return result
    finally:
        for path in (job_path, out_path, log_path):
            if os.path.exists(path):
                os.remove(path)


class ArkLocalLLM:
    CATEGORY = "arkennemasis/LLM"
    FUNCTION = "run"
    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("text", "report")
    DESCRIPTION = ("Ask a local GGUF model (llama.cpp, GPU) and get text back. Runs in its "
                   "own process so all of its VRAM is freed before the next node. Wire a "
                   "JSON schema to force a JSON answer.")

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": (model_choices(), {
                    "tooltip": "Any .gguf under ComfyUI/models/LLM. Instruction-tuned "
                               "models only; the chat template is read from the file.",
                }),
                "system": ("STRING", {"multiline": True, "default": "", "forceInput": True}),
                "prompt": ("STRING", {"multiline": True, "default": "", "forceInput": True}),
                "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 2.0,
                                          "step": 0.05}),
                "max_tokens": ("INT", {"default": 4096, "min": 64, "max": 32768}),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0x7fffffff}),
            },
            "optional": {
                "json_schema": ("STRING", {
                    "forceInput": True,
                    "tooltip": "A JSON schema. When wired, decoding is constrained to it, "
                               "so the answer is always parseable JSON of that shape.",
                }),
                "context_tokens": ("INT", {"default": 16384, "min": 2048, "max": 131072,
                                           "step": 1024}),
            },
        }

    def run(self, model, system, prompt, temperature, max_tokens, seed, json_schema="",
            context_tokens=16384):
        from .video_save import release_everything

        # The video models of a previous run may still be resident; the LLM needs the room.
        release_everything("before local LLM")
        schema = json.loads(json_schema) if (json_schema or "").strip() else None
        result = chat(model, system, prompt, schema=schema, max_tokens=max_tokens,
                      temperature=temperature, seed=seed, n_ctx=context_tokens)
        report = ("%s | %s tokens in %.1fs (load %.1fs) | finish: %s"
                  % (os.path.basename(model), result.get("tokens"), result.get("seconds"),
                     result.get("load_seconds"), result.get("finish_reason")))
        print("[arkennemasis] local LLM: %s" % report)
        if result.get("finish_reason") == "length":
            print("[arkennemasis] local LLM: the answer hit max_tokens and is cut off.")
        return (result["text"], report)


NODE_CLASS_MAPPINGS = {"ArkLocalLLM": ArkLocalLLM}
NODE_DISPLAY_NAME_MAPPINGS = {"ArkLocalLLM": "arkennemasis Local LLM (GGUF, llama.cpp)"}
