"""The load-time message: one line, "comfyui-arkennemasis · N nodes loaded".

Purely decorative and deliberately harmless: `show()` swallows everything, so a terminal
that dislikes it cannot stop the pack loading.

It prints in ONE write. Not a micro-optimisation: ComfyUI-Manager wraps stdout with a
lock and wandb patches it again, and this install has already lost a run to those
contending inside an ordinary print().

Turn it off with  ARK_BANNER=0  in the environment.
"""

import os
import sys

NAME = "comfyui-arkennemasis"

_GOLD = "\x1b[1;38;2;250;200;36m"
_RESET = "\x1b[0m"


def _console_available():
    """Is a real console attached, and can it do ANSI?

    Asks the OS handle, NOT sys.stdout.isatty(). isatty() cannot answer this inside
    ComfyUI: ComfyUI-Manager replaces sys.stdout with a logger whose isatty() is a
    hardcoded `return False` (its prestartup_script.py), so anything gating colour on
    it loses colour for reasons that have nothing to do with the terminal. Measured -
    a bare console reports isatty True, and the same console under Manager reports
    False.

    The handle is also still the right answer for the case isatty() was guarding:
    with stdout redirected to a file GetConsoleMode fails, and the plain text is used.
    """
    if os.name != "nt":
        try:
            return sys.__stdout__.isatty()
        except Exception:
            return False
    try:
        import ctypes

        handle = ctypes.windll.kernel32.GetStdHandle(-11)   # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if not ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False                                     # redirected, not a console
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        # The return value matters and must not be dropped: a console older than
        # Windows 10 1511 does not know the VT flag and fails here, and emitting the
        # escape codes anyway prints them as literal garbage.
        return bool(ctypes.windll.kernel32.SetConsoleMode(
            handle, mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING))
    except Exception:
        return False


def _encodable(text, stream):
    """Can this stream actually write these characters?

    Not a formality. The line uses a middle dot, which a stream on a pure-ASCII
    encoding (or one that reports no encoding at all) cannot write — it raises
    UnicodeEncodeError mid-write, which under a bare `except` looks exactly like the
    message having been switched off. Checked up front so the ASCII fallback is used
    instead of nothing being printed at all.
    """
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        text.encode(encoding)
        return True
    except (UnicodeEncodeError, LookupError, TypeError):
        return False


def _wants_colour(stream):
    if os.environ.get("NO_COLOR"):
        return False
    return _console_available()


def _line(colour, node_count, sep="·"):
    text = ("%s nodes loaded" % NAME if node_count is None
            else "%s %s %d nodes loaded" % (NAME, sep, node_count))
    return (_GOLD + text + _RESET + "\n") if colour else (text + "\n")


def show(node_count=None, stream=None):
    """Print it. Never raises, and never half-prints."""
    if os.environ.get("ARK_BANNER", "1").strip().lower() in ("0", "false", "no", "off"):
        return
    stream = stream or sys.stdout
    try:
        # Best first, each a step less demanding of the terminal: colour, then plain,
        # then pure ASCII for a stream that cannot write the middle dot.
        candidates = []
        if _wants_colour(stream):
            candidates.append(_line(True, node_count))
        candidates.append(_line(False, node_count))
        candidates.append(_line(False, node_count, sep="-"))

        for text in candidates:
            if _encodable(text, stream):
                stream.write(text)      # ONE write - see the module docstring
                stream.flush()
                return
    except Exception:
        pass


if __name__ == "__main__":
    show(node_count=None)
