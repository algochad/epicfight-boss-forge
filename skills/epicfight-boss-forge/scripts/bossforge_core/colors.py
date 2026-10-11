"""Color helpers."""
from __future__ import annotations

import re
from typing import Dict, Tuple

RGB = Tuple[int, int, int]
_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def is_hex(s) -> bool:
    return isinstance(s, str) and bool(_HEX.match(s))


def hex_to_rgb(s: str) -> RGB:
    if not is_hex(s):
        raise ValueError("expected #rrggbb, got %r" % (s,))
    return (int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16))


def rgb_to_hex(c) -> str:
    return "#%02x%02x%02x" % (clamp8(c[0]), clamp8(c[1]), clamp8(c[2]))


def clamp8(v: float) -> int:
    v = int(round(v))
    return 0 if v < 0 else 255 if v > 255 else v


def mix(a, b, t: float) -> RGB:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return (clamp8(a[0] * (1 - t) + b[0] * t),
            clamp8(a[1] * (1 - t) + b[1] * t),
            clamp8(a[2] * (1 - t) + b[2] * t))


def shade(c, f: float) -> RGB:
    return (clamp8(c[0] * f), clamp8(c[1] * f), clamp8(c[2] * f))


def luminance(c) -> float:
    return (0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]) / 255.0


def resolve(ref, palette: Dict[str, str]) -> RGB:
    if isinstance(ref, str):
        if ref in palette:
            return hex_to_rgb(palette[ref])
        if is_hex(ref):
            return hex_to_rgb(ref)
    raise ValueError("unknown color %r (use a palette key %s or #rrggbb)" % (ref, sorted(palette)))
