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
                 "fu", "fv", "eh", "ev", "tb", "m", "vr", "u", "v", "ru", "rv", "rnu", "rnv", "styled", "covered",
                 "base_rgb", "accent_rgb", "rgb")

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
        self.tb = 0.03125  # texture texel size in blocks (measured per triangle for custom shapes)
        self.m = 1  # texel-aligned pattern step: texels per 1/32 block (>= 1); keeps weave patterns the same size in blocks
        self.styled = False  # the box this texel belongs to carries its own material (shape part_style)
        self.covered = False  # an overlay entry was baked into this base texel: it stands for armor over the body
        self.vr = 1.0  # sign of world x along the viewer's right on front/back faces
        self.u = 0.0  # absolute horizontal face coordinate in blocks (viewer-right on front/back faces, toward the front on side faces)
        self.v = 0.0  # absolute height in blocks (model y)
        self.ru = 0.5  # position across the atlas rectangle of this face (0 left .. 1 right, texel centres)
        self.rv = 0.5  # position down the atlas rectangle of this face (0 top .. 1 bottom); a box face's top row is the box's top
        self.rnu = 1.0  # one texel across / down that rectangle (1 / columns, 1 / rows)
        self.rnv = 1.0
        self.base_rgb = None  # type: Optional[RGB]  # colour / accent the base material was shaded with (feature `repaint` reuses them)
        self.accent_rgb = None  # type: Optional[RGB]
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


def skin_soft(t: Texel, c, a, s: int) -> RGB:
    """Smooth skin for stylised faces: a few percent of low-frequency shading, no per-texel grain."""
    px, py, pz = t.p
    r, g, b = mix(shade(c, 0.97), shade(c, 1.03), fbm(px * 0.7, py * 0.7, pz * 0.7, 2, s + 22))
    return _scale(r, g, b, 1.04 if t.face == "top" else 1.0)


def hair(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    streak = vnoise(px * 3, py * 0.5, pz * 3, s + 11)
    out = mix(shade(c, 0.8), shade(c, 1.25), streak)
    if luminance(c) < 0.5:
        grey = fbm(px * 4, py * 0.6, pz * 4, 2, s + 13)
        if grey > 0.72:
            out = mix(out, (214, 214, 220), (grey - 0.72) * 5.0)
    return out


def hair_soft(t: Texel, c, a, s: int) -> RGB:
    """Hair in the base colour only (no grey flecks): one tone per strand (about 1.6 texels wide, drifting a little along its
    length), a few bright streaks, a lighter gloss band across the upper fifth of the box and a darker underside."""
    strand = int(math.floor(t.u / max(1.6 * t.tb, 1e-4)))
    tone = 0.62 * hash01(strand, 11, 0, s + 11) + 0.38 * vnoise(strand * 0.7, t.v * 5.0, 0.5, s + 12)
    out = mix(shade(c, 0.74), shade(c, 1.2), tone)
    if hash01(strand, 13, 0, s + 13) > 0.86:
        out = mix(out, shade(c, 1.55), 0.55)
    gloss = max(0.0, 1.0 - abs(t.ly - 0.8) / 0.09)
    if gloss > 0.0:
        out = mix(out, shade(c, 1.65), 0.5 * gloss)
    if t.ly < 0.14 or t.face == "bottom":
        out = shade(out, 0.82)
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
    return _scale(r, g, b, 0.93 + 0.14 * ((t.x // t.m + t.y // t.m) % 2))


def chainmail(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 2, py * 2, pz * 2, 2, s + 6)
    ring = (t.x // t.m + (t.y // t.m) % 2) % 2
    r, g, b = mix(shade(c, 0.5), shade(c, 1.2), n)
    return _scale(r, g, b, 0.72 + 0.4 * ring)


def cloth(t: Texel, c, a, s: int) -> RGB:
    px, py, pz = t.p
    n = fbm(px * 1.8, py * 1.8, pz * 1.8, 3, s + 30)
    r, g, b = mix(shade(c, 0.8), shade(c, 1.15), n)
    f = 0.88 + 0.12 * ((t.x // t.m + t.y // t.m) % 2)
    if math.sin(px * 2.4 + 2.0 * fbm(px * 0.5, py * 0.5, pz * 0.5, 1, s + 50)) > 0.55:
        f *= 0.82
    return _scale(r, g, b, f)


def silk(t: Texel, c, a, s: int) -> RGB:
    """Smooth fabric for pale robes: soft large-scale tone shifts and a faint vertical drape, no checker grain."""
    px, py, pz = t.p
    n = fbm(px * 0.8, py * 0.45, pz * 0.8, 2, s + 36)
    r, g, b = mix(shade(c, 0.93), shade(c, 1.06), n)
    drape = 0.985 + 0.03 * math.sin((px + pz) * 1.1 + 2.0 * fbm(px * 0.4, py * 0.2, pz * 0.4, 1, s + 38))
    f = drape * (0.99 + 0.02 * vnoise(px * 4, py * 4, pz * 4, s + 37))
    return _scale(r, g, b, f)


def lace(t: Texel, c, a, s: int) -> RGB:
    """Light openwork fabric for frills: smooth tone with a faint diagonal mesh (no checker grain)."""
    px, py, pz = t.p
    r, g, b = mix(shade(c, 0.95), shade(c, 1.05), fbm(px * 0.9, py * 0.6, pz * 0.9, 2, s + 39))
    step = max(1, t.m)
    d1 = (t.x // step + t.y // step) % 4
    d2 = (t.x // step - t.y // step) % 4
    return _scale(r, g, b, 0.95 if d1 == 0 or d2 == 0 else 1.0)


def silk_feather(t: Texel, c, a, s: int) -> RGB:
    """One feather vane per box face: a smooth tone that is a little darker at the root than at the tip, soft shading toward
    the vane edges, a faint rib down the middle (lighter on tinted feathers, a touch darker on white ones), very fine
    chevron barbs and a rare single bright sparkle texel. Uses the face rectangle coordinates (ru, rv), so it follows
    every feather however it is rotated."""
    px, py, pz = t.p
    out = mix(shade(c, 0.985), shade(c, 1.015), fbm(px * 0.7, py * 0.5, pz * 0.7, 2, s + 41))
    out = shade(out, 0.96 + 0.06 * t.rv)
    edge = min(t.ru, 1.0 - t.ru)  # 0 at a vane edge .. 0.5 at the rib
    if edge < 0.2:
        out = shade(out, 0.93 + 0.07 * edge / 0.2)
    off = abs(t.ru - 0.5)
    if off <= 0.5 * t.rnu + 1e-9:  # the rib: one texel (two when the face has an even number of columns)
        out = shade(out, 0.96) if luminance(c) > 0.86 else mix(out, (255, 255, 255), 0.35)
    elif (int(t.rv / t.rnv) - int(off / t.rnu)) % 6 == 0:  # chevron barbs
        out = shade(out, 0.975)
    if hash01(t.x, t.y, 17, s + 303) > 0.996:
        out = mix(out, (255, 255, 255), 0.85)
    return out


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
    "skin_soft": skin_soft,
    "hair": hair,
    "hair_soft": hair_soft,
    "plate_steel": plate_steel,
    "plate_gold": plate_gold,
    "leather": leather,
    "chainmail": chainmail,
    "cloth": cloth,
    "silk": silk,
    "lace": lace,
    "silk_feather": silk_feather,
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
