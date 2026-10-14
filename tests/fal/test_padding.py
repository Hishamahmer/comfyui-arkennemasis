r"""fal videos padded past their real end (13 Oct, VEED Fabric 1.0): a 60 s talking video came back as a 206.8 s file,
its last frame held for 146.8 s over silence, and Sync Lipsync 2 Pro then read it as a 7 fps video and priced it as
206.8 s. media.padded_end / cut_padding / video_upload and node._unpad, on files made here with ffmpeg (no key,
nothing sent):

  padded            3.04 s at 25 fps, the last frame held 7 s over silence -> cut to 3.04 s, 76 frames, 3 s of sound
  held, sound on    the same hold with a tone going on under it -> left alone (a real still with real sound)
  normal            3 s at 25 fps with sound -> left alone, its bytes unchanged
  padded, no sound  the hold with no audio stream at all -> cut
  upload            video_upload sends the cut bytes and prices 3.04 s; a normal video goes as its own bytes
  reuse/download    node._unpad on a saved padded result cuts it in place and says so

    python_embeded\python.exe -s ComfyUI\custom_nodes\comfyui-arkennemasis\tests\fal\test_padding.py
"""
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import WORK, media, node  # noqa: E402

from comfy_api.latest import InputImpl  # noqa: E402

OUT = os.path.join(WORK, "padding")
FFMPEG, FFPROBE = media._tools()
FAILS = []


def check(cond, what):
    print("%s  %s" % ("ok  " if cond else "FAIL", what))
    if not cond:
        FAILS.append(what)


def run(args):
    subprocess.run([FFMPEG, "-v", "error", "-y"] + args, check=True)


def packets(path):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "packet=pts_time,duration_time", "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return sorted((float(a), float(b)) for a, b in (l.split(",")[:2] for l in out.split() if "," in l))


def seconds(path, stream):
    out = subprocess.run([FFPROBE, "-v", "error", "-select_streams", stream, "-show_entries", "stream=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout.strip()
    return float(out) if out else None


def held_video(dst, sound):
    """76 frames at 25 fps (3.04 s), the LAST one held for 7 s (its packet's duration set with the setts filter, the
    shape of VEED Fabric's 13 Oct file, whose last frame lasts 146.8 s). sound: "pad" (a tone for 3.04 s, then silence
    to 10.04 s), "tone" (a tone all 10.04 s) or None (no audio stream)."""
    base = os.path.join(OUT, "base.mp4")
    if not os.path.isfile(base):
        run(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25", "-frames:v", "76", "-c:v", "libx264",
             "-bf", "0", "-pix_fmt", "yuv420p", base])                # no B-frames: frames from 0 s
    args = ["-i", base]
    if sound == "pad":
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=3.04", "-filter:a", "apad=whole_dur=10.04"]
    elif sound == "tone":
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=10.04"]
    args += ["-map", "0:v", "-c:v", "copy", "-bsf:v", r"setts=duration=if(eq(N\,75)\,7/TB\,DURATION)"]
    if sound:
        args += ["-map", "1:a", "-c:a", "aac"]
    run(args + [dst])


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    check(bool(FFMPEG and FFPROBE), "ffmpeg and ffprobe found (%s, %s)" % (FFMPEG, FFPROBE))

    padded = os.path.join(OUT, "padded.mp4")
    held_video(padded, "pad")
    pk = packets(padded)
    check(len(pk) == 76 and abs(pk[-1][1] - 7.0) < 0.05, "fixture: 76 packets, the last held %.2f s" % pk[-1][1])
    found = media.padded_end(padded)
    check(found is not None and abs(found[0] - 3.04) < 0.01 and abs(found[1] - 10.04) < 0.05,
          "padded: found real end %s of a %s s file" % (found and found[0], found and found[1]))
    cut = media.cut_padding(padded)
    pk = packets(padded)
    check(cut is not None and len(pk) == 76 and max(d for _, d in pk) < 0.05,
          "padded: cut in place to %d frames at 25 fps (longest frame %.3f s)" % (len(pk), max(d for _, d in pk)))
    check(abs(seconds(padded, "v:0") - 3.04) < 0.05 and abs(seconds(padded, "a:0") - 3.04) < 0.06,
          "padded: picture %.2f s, sound %.2f s" % (seconds(padded, "v:0"), seconds(padded, "a:0")))
    check(media.padded_end(padded) is None, "padded: once cut, nothing more to cut")

    tone = os.path.join(OUT, "held_tone.mp4")
    held_video(tone, "tone")
    before = open(tone, "rb").read()
    check(media.padded_end(tone) is None and media.cut_padding(tone) is None and open(tone, "rb").read() == before,
          "held frame with sound going on under it: left alone, bytes unchanged")

    normal = os.path.join(OUT, "normal.mp4")
    run(["-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3", "-f", "lavfi", "-i",
         "sine=frequency=330:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", normal])
    before = open(normal, "rb").read()
    check(media.padded_end(normal) is None and open(normal, "rb").read() == before, "normal video: left alone")

    mute = os.path.join(OUT, "padded_mute.mp4")
    held_video(mute, None)
    cut = media.cut_padding(mute)
    check(cut is not None and len(packets(mute)) == 76, "padded with no sound at all: cut to 76 frames")

    padded2 = os.path.join(OUT, "padded_upload.mp4")
    held_video(padded2, "pad")
    data, ctype, fname, secs = media.video_upload(InputImpl.VideoFromFile(padded2))
    sent = os.path.join(OUT, "sent.mp4")
    with open(sent, "wb") as fh:
        fh.write(data)
    check(abs(secs - 3.04) < 0.01 and len(packets(sent)) == 76 and ctype == "video/mp4",
          "upload: a padded video goes out cut (%d frames) and is priced at %.2f s" % (len(packets(sent)), secs))
    check(abs(packets(padded2)[-1][1] - 7.0) < 0.05, "upload: the input file itself is untouched (still held)")
    data, ctype, fname, secs = media.video_upload(InputImpl.VideoFromFile(normal))
    check(data == open(normal, "rb").read() and abs(secs - 3.0) < 0.05, "upload: a normal video goes as its own bytes")

    saved = os.path.join(OUT, "saved_result.mp4")
    held_video(saved, "pad")
    said = []
    node._unpad(saved, said.append)
    check(len(packets(saved)) == 76 and said and "cut to 3.0 s" in said[0], "download/reuse: %s" % (said or ["nothing said"])[0])
    said = []
    node._unpad(normal, said.append)
    check(not said, "download/reuse: a normal video says nothing")

    # the node's own download step, fal's file fetched from disk instead of the network, saved under %TEMP%
    import folder_paths
    folder_paths.set_output_directory(os.path.join(WORK, "out"))
    src = os.path.join(OUT, "download_src.mp4")
    held_video(src, "pad")
    real_download = node.client.download
    node.client.download = lambda url, path, log=None: (shutil.copy2(src, path), os.path.getsize(path))[1]
    try:
        said = []
        spec = {"folder": "padding_test", "title": "padding test", "outputs": [{"kind": "video", "field": "video"}]}
        files = node._download(spec, {"request_id": "test"},
                               {"video": {"url": "http://127.0.0.1/none.mp4", "content_type": "video/mp4"}}, said.append)
        got = files["video"][0]
        check(len(packets(got)) == 76 and any("cut to 3.0 s" in s for s in said),
              "download: the saved result is cut (%d frames): %s" % (len(packets(got)), said[-1] if said else ""))
    finally:
        node.client.download = real_download

    # The 13 Oct file itself, when it is on this machine: its 146.8 s hold is followed by one more 0.02 s packet
    # with no picture in it, so the hold is not the last packet (the first version of padded_end missed it).
    real = os.environ.get("FAL_PADDED_SAMPLE") or r"E:\ComfyUI_windows_portable_nvidia\ComfyUI_windows_portable" \
        r"\ComfyUI\output\fal\veed_fabric-1.0\veed_fabric-1.0_00012_.mp4"
    if os.path.isfile(real) and len(packets(real)) == 1501:
        copy = os.path.join(OUT, "real_fabric_13oct.mp4")
        shutil.copy2(real, copy)
        found = media.padded_end(copy)
        check(found is not None and abs(found[0] - 60.0) < 0.01 and abs(found[1] - 206.82) < 0.05,
              "VEED Fabric's 13 Oct file: real end %s of %s s" % (found and found[0], found and found[1]))
        data, ctype, fname, secs = media.video_upload(InputImpl.VideoFromFile(copy))
        check(abs(secs - 60.0) < 0.01, "VEED Fabric's 13 Oct file: priced at %.2f s when sent on" % secs)
        media.cut_padding(copy)
        pk = packets(copy)
        check(len(pk) == 1500 and max(d for _, d in pk) < 0.05 and abs(seconds(copy, "a:0") - 60.0) < 0.06,
              "VEED Fabric's 13 Oct file: cut to %d frames, sound %.2f s" % (len(pk), seconds(copy, "a:0")))
    else:
        print("skip  VEED Fabric's 13 Oct file is not on this machine (set FAL_PADDED_SAMPLE to test one)")

    print("\n%d failed" % len(FAILS) if FAILS else "\nall passed")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
