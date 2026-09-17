# Local Voice Cloning (Qwen3-TTS) Setup Guide

Run offline local voice cloning and text-to-speech inside ComfyUI with zero cloud fees.

---

## 1. Why Qwen3-TTS Uses an Isolated Environment

Qwen3-TTS requires `transformers==4.57.3`. However, standard ComfyUI installations use `transformers 5.x`, which a dozen other custom nodes depend upon. Downgrading the main ComfyUI environment causes breaks across other packs.

To resolve this conflict permanently:
* Arkennemasis isolates Qwen3-TTS inside a dedicated child subprocess in `vendor/tts_env/`.
* The main ComfyUI environment stays on its modern packages, while the TTS node spins up its isolated worker using the same GPU and CUDA drivers without conflicts.

---

## 2. One-Time Setup

Run this one-time command using your portable Python environment from the `comfyui-arkennemasis` directory:

```powershell
..\..\..\python_embeded\python.exe -m pip install --target vendor/tts_env transformers==4.57.3 soundfile
```

Once installed, the `vendor/tts_env` directory is ignored by Git and will persist across runs.

---

## 3. Model Weights

Place Qwen3-TTS models in:
```
ComfyUI/models/qwen-tts/<model_folder>/
```

* **Base Model (Clone-Only):**
  Has no preset voices. Requires an input reference audio clip (`reference_audio`).
* **CustomVoice Model:**
  Includes preset voice options in addition to voice cloning support.

---

## 4. Reference Voice Audio Best Practices

When providing a reference voice clip to the `reference_audio` input:

1. **Duration:** Provide **5 to 20 seconds** of clean speech. Clips longer than 30 seconds multiply synthesis latency across every shot in a multi-scene film.
2. **Audio Quality:** Use clean 16-bit 44.1kHz mono audio with minimal background noise or reverb.
3. **Pacing:** Select a reference clip that matches the tone, cadence, and emotion intended for the narration.
