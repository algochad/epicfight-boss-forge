"""Procedural surface materials for the boss skin painter (pure Python, deterministic).

Every material is ``f(texel, base_rgb, accent_rgb, seed) -> (r, g, b)`` returning clamped ints.

Coordinates: ``texel.p`` is the model position in model pixels (16 per block; the unit the original
Minecraft pixel-art noise was tuned in) and ``texel.pos`` is the same position in blocks. Materials that
describe pixel grain (skin, hair, steel streaks, leather, mail, cloth) use ``p``; materials that describe
patterns with a physical size (seams, fur, bone, stone, scales, ember cracks) use ``pos``.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Optional, Tuple

from .colors import clamp8, luminance, mix, shade
from .noise import fbm, hash01, vnoise

RGB = Tuple[int, int, int]


class Texel:
    """One painted texture texel with the surface context features and materials need."""
    __slots__ = ("x", "y", "pos", "p", "n", "part", "region", "layer", "lx", "ly", "lz", "face",
                 "fu", "fv", "eh", "ev", "tb", "vr", "rgb")

    def __init__(self, x: int, y: int):
        self.x = x
        self.y = y
        self.pos = (0.0, 0.0, 0.0)  # blocks
        self.p = (0.0, 0.0, 0.0)  # model pixels (16 per block)
        self.n = (0.0, 1.0, 0.0)
        self.part = ""
        self.region = None  # type: Optional[str]
        self.layer = "base"  # base | overlay
        self.lx = 0.5  # viewer-left -> right across the region base bbox (looking at the front)
        self.ly = 0.5  # 0 bottom .. 1 top of the region base bbox
        self.lz = 0.5  # back -> front across the region base bbox
        self.face = "side"  # front back left right top bottom side
        self.fu = 0.5  # viewer-left -> right across the face (side faces: increases toward the front)
        self.fv = 0.5  # 0 top .. 1 bottom
        self.eh = 1.0  # region extent along fu, blocks
        self.ev = 1.0  # region extent along fv, blocks
        self.tb = 0.03125  # texture texel size in blocks
        self.vr = 1.0  # sign of world x along the viewer's right on front/back faces
        self.rgb = None  # type: Optional[RGB]


def _scale(r: float, g: float, b: float, f: float) -> RGB:
    return (clamp8(r * f), clamp8(g * f), clamp8(b * f))


def _round_half_up(v: float) -> float:
    return math.floor(v + 0.5)


# --------------------------------------------------------------------------- materials
def skin(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 2.5, py * 2.5, pz * 2.5, 3, s + 20)
    r, g, b = mix(shade(c, 0.92), shade(c, 1.10), n)
    f = 0.97 + 0.06 * vnoise(px * 6, py * 6, pz * 6, s + 21)
    if t.face == "top":
        f *= 1.04
    return _scale(r, g, b, f)


def hair(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    streak = vnoise(px * 3, py * 0.5, pz * 3, s + 11)
    out = mix(shade(c, 0.8), shade(c, 1.25), streak)
    if luminance(c) < 0.5:
        grey = fbm(px * 4, py * 0.6, pz * 4, 2, s + 13)
        if grey > 0.72:
            out = mix(out, (214, 214, 220), (grey - 0.72) * 5.0)
    return out


def plate_steel(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 1.1, py * 1.1, pz * 1.1, 3, s + 1)
    k = 0.9 + 0.2 * vnoise(px * 1.5, py * 7, pz * 1.5, s + 5)
    r, g, b = mix(shade(c, 0.55), shade(c, 1.15), n * 1.15)
    bx, by, bz = t.pos
    period_y, period_xz = 0.33, 0.5
    tb = t.tb
    # distance (blocks) to the nearest horizontal / diagonal seam line; seams are at least one texel thick
    dy = abs(by - _round_half_up(by / period_y) * period_y)
    sxz = bx + bz
    dxz = abs(sxz - _round_half_up(sxz / period_xz) * period_xz)
    half_y = 0.5 * max(0.05 * period_y, tb)
    half_x = 0.5 * max(0.04 * period_xz, tb)
    f = k
    if dy < half_y or dxz < half_x:
        f = k * 0.6
    if dy < 0.5 * tb and dxz < 0.5 * tb:  # rivet: a single texel at the seam crossing
        f = k * 1.35
    return _scale(r, g, b, f)


def plate_gold(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 1.6, py * 1.6, pz * 1.6, 3, s + 3)
    r, g, b = mix(shade(c, 0.62), mix(c, (255, 255, 255), 0.35), n * 1.2)
    if fbm(px * 3, py * 3, pz * 3, 2, s + 8) > 0.68:
        r, g, b = r * 0.78, g * 0.78, b * 0.78
    return (clamp8(r), clamp8(g), clamp8(b))


def leather(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 2, py * 2, pz * 2, 3, s + 4)
    r, g, b = mix(shade(c, 0.6), shade(c, 1.1), n)
    return _scale(r, g, b, 0.93 + 0.14 * ((t.x + t.y) % 2))


def chainmail(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 2, py * 2, pz * 2, 2, s + 6)
    ring = (t.x + (t.y % 2)) % 2
    r, g, b = mix(shade(c, 0.5), shade(c, 1.2), n)
    return _scale(r, g, b, 0.72 + 0.4 * ring)


def cloth(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 1.8, py * 1.8, pz * 1.8, 3, s + 30)
    r, g, b = mix(shade(c, 0.8), shade(c, 1.15), n)
    f = 0.88 + 0.12 * ((t.x + t.y) % 2)
    if math.sin(px * 2.4 + 2.0 * fbm(px * 0.5, py * 0.5, pz * 0.5, 1, s + 50)) > 0.55:
        f *= 0.82
    return _scale(r, g, b, f)


def fur(t: Texel, c, a, s: int) -> RGB:
    bx, by, bz = t.pos
    r, g, b = mix(shade(c, 0.7), shade(c, 1.2), fbm(bx * 3, by * 5, bz * 3, 3, s + 40))
    f = 1.15 if vnoise(bx * 8, by * 1, bz * 8, s + 41) > 0.55 else 1.0
    return _scale(r, g, b, f)


def bone(t: Texel, c, a, s: int) -> RGB:
    bx, by, bz = t.pos
    base = mix((168, 159, 136), (216, 210, 192), fbm(bx * 1.5, by * 1.5, bz * 1.5, 3, s + 60))
    r, g, b = mix(base, c, 0.25)
    f1, f2 = _worley(bx * 5, by * 5, bz * 5, s + 61)[:2]
    if f2 - f1 < 0.04:
        return _scale(r, g, b, 0.65)
    return (clamp8(r), clamp8(g), clamp8(b))


def stone(t: Texel, c, a, s: int) -> RGB:
    bx, by, bz = t.pos
    r, g, b = mix(shade(c, 0.7), shade(c, 1.2), fbm(bx * 1.2, by * 1.2, bz * 1.2, 4, s + 70))
    f = 1.0
    if abs(fbm(bx * 2, by * 2, bz * 2, 3, s + 71) - 0.5) < 0.025:
        f = 0.6
    elif vnoise(bx * 14, by * 14, bz * 14, s + 72) > 0.93:
        f = 1.1
    return _scale(r, g, b, f)


def scales(t: Texel, c, a, s: int) -> RGB:
    bx, by, bz = t.pos
    f1, f2, cell = _worley(bx * 8, by * 8, bz * 8, s + 80)
    h = hash01(cell[0], cell[1], cell[2], s + 83)
    r, g, b = shade(c, 0.75 + 0.5 * h)
    thr = max(0.05, t.tb * 8.0)  # borders stay at least about one texel wide
    if f2 - f1 < thr:
        return _scale(r, g, b, 0.6)
    return (r, g, b)


def ember(t: Texel, c, a, s: int) -> RGB:
    bx, by, bz = t.pos
    if abs(fbm(bx * 2.5, by * 2.5, bz * 2.5, 3, s + 90) - 0.5) < 0.035:
        return _scale(a[0], a[1], a[2], 1.2)
    return shade(c, 0.25)


def _worley(x: float, y: float, z: float, seed: int):
    """Worley noise returning (f1, f2, nearest cell). Same field as noise.worley plus the cell id."""
    ix, iy, iz = math.floor(x), math.floor(y), math.floor(z)
    f1 = f2 = 1e9
    best = (ix, iy, iz)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                cx, cy, cz = ix + dx, iy + dy, iz + dz
                px = cx + hash01(cx, cy, cz, seed)
                py = cy + hash01(cx, cy, cz, seed + 1)
                pz = cz + hash01(cx, cy, cz, seed + 2)
                d = math.sqrt((px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2)
                if d < f1:
                    f2 = f1
                    f1 = d
                    best = (cx, cy, cz)
                elif d < f2:
                    f2 = d
    return f1, f2, best


MATERIAL_FUNCS = {
    "skin": skin,
    "hair": hair,
    "plate_steel": plate_steel,
    "plate_gold": plate_gold,
    "leather": leather,
    "chainmail": chainmail,
    "cloth": cloth,
    "fur": fur,
    "bone": bone,
    "stone": stone,
    "scales": scales,
    "ember": ember,
}  # type: Dict[str, Callable]


def shade_texel(material: str, t: Texel, c, a, seed: int) -> RGB:
    """Evaluate a named material; unknown names fall back to cloth (validation reports them elsewhere)."""
    fn = MATERIAL_FUNCS.get(material) or cloth
    return fn(t, c, a, seed)
