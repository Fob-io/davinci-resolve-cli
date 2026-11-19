"""Colored logo shown at REPL start (logo.ans: colored ASCII, 24-bit ANSI)."""
import os
import re
import sys
from pathlib import Path

_TRUECOLOR = re.compile(r"\b(38|48);2;(\d+);(\d+);(\d+)")


def _to_256(match):
    """Quantize an RGB color to the 6x6x6 cube of the 256-color palette."""
    layer, *rgb = match.groups()
    r, g, b = (round(int(c) / 255 * 5) for c in rgb)
    return f"{layer};5;{16 + 36 * r + 6 * g + b}"


def render_logo(truecolor: bool) -> str:
    text = Path(__file__).with_name("logo.ans").read_text(encoding="utf-8").rstrip("\n")
    return text if truecolor else _TRUECOLOR.sub(_to_256, text)


def print_logo(stream=sys.stdout):
    """Print the logo on a color terminal; skip it otherwise (the colors carry the shape)."""
    if not stream.isatty() or "NO_COLOR" in os.environ:
        return
    truecolor = os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")
    print(render_logo(truecolor), file=stream)
