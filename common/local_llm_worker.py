"""Child process for ArkLocalLLM: load one GGUF, answer one chat, write the result, exit.

Run as ``python local_llm_worker.py job.json``. The job names the model, the messages and
an optional JSON schema; the answer goes to ``job["out_path"]``. Exiting is the point —
it returns every byte of VRAM the model held, which an in-process llama.cpp does not.
"""

import json
import sys
import time


def main(job_path):
    with open(job_path, encoding="utf-8") as fh:
        job = json.load(fh)
    result = {"ok": False}
    try:
        from llama_cpp import Llama

        started = time.time()
        llm = Llama(model_path=job["model_path"], n_ctx=int(job["n_ctx"]), n_gpu_layers=-1,
                    flash_attn=True, seed=int(job["seed"]), verbose=False)
        loaded = time.time() - started
        kwargs = {}
        if job.get("schema"):
            kwargs["response_format"] = {"type": "json_object", "schema": job["schema"]}
        reply = llm.create_chat_completion(
            messages=[{"role": "system", "content": job["system"]},
                      {"role": "user", "content": job["prompt"]}],
            max_tokens=int(job["max_tokens"]), temperature=float(job["temperature"]),
            seed=int(job["seed"]), **kwargs)
        choice = reply["choices"][0]
        result = {"ok": True, "text": choice["message"]["content"] or "",
                  "finish_reason": choice.get("finish_reason"),
                  "tokens": (reply.get("usage") or {}).get("completion_tokens"),
                  "load_seconds": round(loaded, 1),
                  "seconds": round(time.time() - started, 1)}
    except Exception as exc:  # reported to the parent, which raises with the reason
        result = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    with open(job["out_path"], "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False)


if __name__ == "__main__":
    main(sys.argv[1])
