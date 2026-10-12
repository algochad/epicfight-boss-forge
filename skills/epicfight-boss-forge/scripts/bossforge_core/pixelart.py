"""Pixel-art helpers shared by the painter's materials and features (pure Python, deterministic).

* ordered 4x4 Bayer dithering and stepped bands (`band`), used by `gradient` (`steps` / `dither`), `iridescent` and
  `posterize`;
* the iridescent sheen (`PASTEL`, `sheen_color`, `sheen_over`): soft pastel colours driven by world position and, optionally,
  the face normal, quantised to a few steps so they stay chunky;
* colour quantisation (`snap_levels`, `snap_palette`) for `posterize` / `palette_snap`.

Everything here works on texel coordinates (`x`, `y` of the atlas) or world positions, never on random per-texel values.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
# pink, peach, yellow, lilac, aqua: the soft sheen of a pearly feather
PASTEL = ((255, 200, 230), (255, 218, 185), (255, 244, 176), (216, 200, 255), (184, 240, 240))
DEFAULT_IRIDESCENT_STEPS = 5
IRIDESCENT_DIR = (0.55, 1.0, 0.4)  # world direction the sheen phase advances along


def bayer(x: int, y: int) -> float:
    """Ordered-dither threshold of texel (x, y), in (0, 1): the 4x4 Bayer matrix."""
    return (BAYER4[y & 3][x & 3] + 0.5) / 16.0


def band(v: float, steps: int, dither: float = 0.0, x: int = 0, y: int = 0) -> float:
    """Quantise v (0..1) into `steps` equally wide bands and return the band's level (0..1, `k / (steps - 1)`). `dither`
    (0..1) spreads each band edge over `dither` of a band with the Bayer threshold of texel (x, y), so a smooth ramp becomes
    a stipple pattern between two neighbouring levels. steps < 2 returns v unchanged."""
    if steps < 2:
        return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v
    t = v * steps
    if dither > 0.0:
        t += (bayer(x, y) - 0.5) * dither
    k = int(math.floor(t))
    if k < 0:
        k = 0
    elif k > steps - 1:
        k = steps - 1
    return k / float(steps - 1)


def _mix(a: RGB, b: RGB, t: float) -> RGB:
    t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    return (int(a[0] * (1.0 - t) + b[0] * t + 0.5), int(a[1] * (1.0 - t) + b[1] * t + 0.5), int(a[2] * (1.0 - t) + b[2] * t + 0.5))


def sheen_color(palette: Sequence[RGB], phase: float, steps: int = 0, dither: float = 0.0, x: int = 0, y: int = 0) -> RGB:
    """Colour of the cyclic `palette` at `phase` (any real number, 1.0 = one full cycle). With `steps` > 0 the cycle is cut into
    that many bands: with steps == len(palette) every band is exactly one palette colour, with more steps the bands in between
    are blends. `dither` stipples band edges (Bayer)."""
    n = len(palette)
    f = phase - math.floor(phase)
    if steps > 0:
        s = f * steps
        if dither > 0.0:
            s += (bayer(x, y) - 0.5) * dither
        f = (math.floor(s) % steps) / float(steps)
    pos = f * n
    i = int(pos) % n
    return _mix(palette[i], palette[(i + 1) % n], pos - math.floor(pos))


def sheen_phase(pos: Tuple[float, float, float], n: Tuple[float, float, float], scale: float, direction: Sequence[float],
                normal: float = 0.0, offset: float = 0.0) -> float:
    """Sheen phase of a surface point: the position along `direction` over `scale` (blocks per colour cycle), plus `normal` x
    the normal's component along the same direction (so faces turned differently get different colours), plus `offset`."""
    dx, dy, dz = direction
    ln = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    dx, dy, dz = dx / ln, dy / ln, dz / ln
    along = pos[0] * dx + pos[1] * dy + pos[2] * dz
    ph = along / max(scale, 1e-6) + offset
    if normal:
        ph += normal * (n[0] * dx + n[1] * dy + n[2] * dz) * 0.5
    return ph


def _luma(c: RGB) -> float:
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def sheen_over(base: RGB, sheen: RGB, strength: float, mode: str = "mix") -> RGB:
    """Lay the sheen over the base colour. `mix`: blend toward the sheen colour; `tint`: take the sheen's hue at the base's
    brightness (the base keeps its shading, so white stays white-ish and a lavender feather turns peach-lavender)."""
    if strength <= 0.0:
        return base
    if mode == "tint":
        lb, ls = _luma(base), _luma(sheen)
        k = lb / ls if ls > 1.0 else 1.0
        tinted = (min(255, int(sheen[0] * k + 0.5)), min(255, int(sheen[1] * k + 0.5)), min(255, int(sheen[2] * k + 0.5)))
        return _mix(base, tinted, strength)
    return _mix(base, sheen, strength)


# --------------------------------------------------------------------------- colour quantisation
def snap_levels(c: RGB, levels: int, mode: str = "luma", dither: float = 0.0, x: int = 0, y: int = 0) -> RGB:
    """Quantise a colour to `levels` steps. `luma` bands the brightness and keeps the hue (the colour is scaled to the banded
    brightness); `rgb` bands every channel on its own (hue shifts, like the classic posterize filter)."""
    if levels < 2:
        return c
    if mode == "rgb":
        out = []
        for v in c:
            q = band(v / 255.0, levels, dither, x, y)
            out.append(int(q * 255.0 + 0.5))
        return (out[0], out[1], out[2])
    lum = _luma(c)
    if lum < 1.0:
        return c
    q = band(lum / 255.0, levels, dither, x, y) * 255.0
    if q <= 0.0:
        return (0, 0, 0)
    k = q / lum
    top = max(c)
    if top * k <= 255.0:
        return (int(c[0] * k + 0.5), int(c[1] * k + 0.5), int(c[2] * k + 0.5))
    # a brighter band than the colour can reach by scaling: scale to the brightest channel, then lighten toward white to the band's brightness
    s = 255.0 / top
    base = (c[0] * s, c[1] * s, c[2] * s)
    lb = _luma(base)
    w = min(1.0, max(0.0, (q - lb) / max(255.0 - lb, 1e-6)))
    return (int(base[0] + (255.0 - base[0]) * w + 0.5), int(base[1] + (255.0 - base[1]) * w + 0.5), int(base[2] + (255.0 - base[2]) * w + 0.5))


def _dist2(a: RGB, b: RGB) -> float:
    return 3.0 * (a[0] - b[0]) ** 2 + 4.0 * (a[1] - b[1]) ** 2 + 2.0 * (a[2] - b[2]) ** 2


def snap_palette(c: RGB, palette: Sequence[RGB], dither: float = 0.0, x: int = 0, y: int = 0) -> RGB:
    """Nearest palette colour (weighted RGB distance; first entry wins ties). `dither` > 0 picks the second nearest colour on a
    Bayer pattern when the two are almost equally near (stippled transitions instead of hard contours)."""
    best = None  # type: Optional[Tuple[float, int]]
    second = None  # type: Optional[Tuple[float, int]]
    for i, p in enumerate(palette):
        d = _dist2(c, p)
        if best is None or d < best[0]:
            second, best = best, (d, i)
        elif second is None or d < second[0]:
            second = (d, i)
    if best is None:
        return c
    if dither > 0.0 and second is not None:
        a, b = palette[best[1]], palette[second[1]]
        span = _dist2(a, b)
        if span > 0.0:
            # share of the way from the nearest toward the second nearest colour (projection on their axis)
            ax = ((c[0] - a[0]) * (b[0] - a[0]) * 3.0 + (c[1] - a[1]) * (b[1] - a[1]) * 4.0 + (c[2] - a[2]) * (b[2] - a[2]) * 2.0) / span
            if ax > 0.0 and ax + (bayer(x, y) - 0.5) * dither >= 0.5:
                return b
    return palette[best[1]]
