"""ComfyUI values <-> files fal can read, and fal's files -> ComfyUI values.

Inputs go out as files, never re-encoded more than needed:
  IMAGE  -> one lossless PNG per picture in the batch
  MASK   -> an RGBA PNG whose TRANSPARENT pixels mark the area to edit (the OpenAI edit
            convention gpt-image-2 follows); ComfyUI's mask is 1 where it is masked
  VIDEO  -> the original file when the VIDEO is an untrimmed, uncropped file on disk,
            otherwise one MP4 written by ComfyUI's own encoder
  AUDIO  -> a 16-bit PCM WAV

Durations are measured here as well, because most of these models bill by the second.
"""

from __future__ import annotations

import io
import os
import tempfile
import wave

import numpy as np
import torch
from PIL import Image

from ..common.image_utils import is_placeholder, tensor_to_pil

VIDEO_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
               ".m4v": "video/x-m4v"}


class InputError(ValueError):
    """An input the model cannot take. Raised before anything is uploaded or billed."""


# ---------------------------------------------------------------------------------------
# images
# ---------------------------------------------------------------------------------------

def image_frames(images):
    """An IMAGE socket's value -> list of (H,W,C) tensors. A placeholder tile is no image."""
    if images is None or is_placeholder(images):
        return []
    if images.dim() == 3:
        images = images.unsqueeze(0)
    return [images[i] for i in range(images.shape[0])]


def png_bytes(frame):
    buf = io.BytesIO()
    tensor_to_pil(frame).convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def image_size(frame):
    """(width, height) of one (H,W,C) frame."""
    return int(frame.shape[1]), int(frame.shape[0])


def mask_png_bytes(mask, size=None):
    """A ComfyUI MASK -> RGBA PNG: transparent where the mask is set (the area to edit).

    ``size`` is the (width, height) of the picture being edited; the mask is resized to it
    so a mask drawn at another resolution still lines up.
    """
    if mask.dim() == 3:
        mask = mask[0]
    arr = mask.detach().cpu().float().numpy()
    alpha = np.where(arr > 0.5, 0, 255).astype(np.uint8)
    img = Image.new("RGBA", (alpha.shape[1], alpha.shape[0]), (0, 0, 0, 255))
    img.putalpha(Image.fromarray(alpha, mode="L"))
    if size and img.size != tuple(size):
        img = img.resize(tuple(size), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def load_image_tensor(path):
    """A saved result image -> (1,H,W,3) float tensor. Transparency is dropped (the file keeps it)."""
    with Image.open(path) as img:
        img.load()
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGBA", img.size, (0, 0, 0, 255))
            img = Image.alpha_composite(background, img)
        img = img.convert("RGB")
        arr = np.asarray(img).astype(np.float32) / 255.0
    return torch.from_numpy(arr)[None, ...]


# ---------------------------------------------------------------------------------------
# video
# ---------------------------------------------------------------------------------------

def video_duration(video):
    try:
        return float(video.get_duration())
    except Exception:                                   # noqa: BLE001 - estimate only
        return None


def _untouched_file(video):
    """The on-disk path of a VIDEO that is exactly its file (no trim, no crop), else None."""
    if type(video).__name__ != "VideoFromFile":
        return None
    try:
        source = video.get_stream_source()
    except Exception:                                   # noqa: BLE001
        return None
    if not isinstance(source, str) or not os.path.isfile(source):
        return None
    if os.path.splitext(source)[1].lower() not in VIDEO_TYPES:
        return None
    try:
        start, length = video.get_active_trim_window()
        if start or length:
            return None
    except Exception:                                   # noqa: BLE001
        pass
    if getattr(video, "_VideoFromFile__crop", None) is not None:
        return None
    return source


def video_file_bytes(video):
    """A VIDEO -> (bytes, content_type, file_name)."""
    path = _untouched_file(video)
    if path:
        with open(path, "rb") as handle:
            data = handle.read()
        ext = os.path.splitext(path)[1].lower()
        return data, VIDEO_TYPES[ext], "input%s" % ext
    tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    tmp.close()
    try:
        video.save_to(tmp.name)
        with open(tmp.name, "rb") as handle:
            data = handle.read()
    finally:
        try:
            os.remove(tmp.name)
        except OSError:
            pass
    if not data:
        raise InputError("fal: the video input could not be encoded to MP4.")
    return data, "video/mp4", "input.mp4"


# ---------------------------------------------------------------------------------------
# audio
# ---------------------------------------------------------------------------------------

def audio_duration(audio):
    try:
        waveform = audio["waveform"]
        return float(waveform.shape[-1]) / float(audio["sample_rate"])
    except Exception:                                   # noqa: BLE001 - estimate only
        return None


def audio_wav_bytes(audio):
    """A ComfyUI AUDIO dict -> 16-bit PCM WAV bytes (first item of a batch, up to stereo)."""
    try:
        waveform = audio["waveform"]
        rate = int(audio["sample_rate"])
    except (KeyError, TypeError):
        raise InputError("fal: the audio input is not a ComfyUI AUDIO value.") from None
    if waveform.dim() == 3:
        waveform = waveform[0]
    if waveform.dim() == 1:
        waveform = waveform.unsqueeze(0)
    waveform = waveform[:2].detach().cpu().float().clamp(-1.0, 1.0)
    if waveform.shape[-1] == 0:
        raise InputError("fal: the audio input is empty.")
    pcm = (waveform.numpy().T * 32767.0).round().astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(pcm.shape[1])
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(pcm.tobytes())
    return buf.getvalue()


# ---------------------------------------------------------------------------------------
# result files
# ---------------------------------------------------------------------------------------

_EXT_BY_TYPE = {"image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
                "image/webp": ".webp", "image/gif": ".gif", "video/mp4": ".mp4",
                "video/quicktime": ".mov", "video/webm": ".webm", "audio/mpeg": ".mp3",
                "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
                "audio/ogg": ".ogg", "audio/flac": ".flac", "application/json": ".json"}


def extension_for(file_obj, fallback):
    """File extension for a fal File object: from its content_type, then its name/URL."""
    ctype = (file_obj.get("content_type") or "").split(";")[0].strip().lower()
    if ctype in _EXT_BY_TYPE:
        return _EXT_BY_TYPE[ctype]
    for candidate in (file_obj.get("file_name") or "", (file_obj.get("url") or "").split("?")[0]):
        ext = os.path.splitext(candidate)[1].lower()
        if ext and len(ext) <= 6 and ext[1:].isalnum():
            return ".jpg" if ext == ".jpeg" else ext
    return fallback
