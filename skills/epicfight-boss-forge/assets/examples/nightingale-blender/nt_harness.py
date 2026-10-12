"""Nightingale back apparatus: the mechanical harness between the shoulder blades and the wings, and the floating glyph tile.

The back view of the new reference (`anatomical_front_back.png`) shows a grey / brass rig on the back: a central yoke plate on the
shoulder blades with two gold clasps, a shoulder plate on each side, two parallel bars per side that rise from the plates to the wing's
leading edge, square rings at their ends, a strut up the leading edge with a clamp, a diagonal brace down to a second small ring, and
a floating light-blue square tile above-left of the head. From the front none of it shows (it sits behind the wings). Everything is on
the art-pixel grid (`nt_common.PIXEL`); coordinates are art pixels in the same wing frame as `nt_wings` (U outwards from the spine, Z above
the soles), built for the character's right and mirrored.

Depth (`nt_wings.back_planes`): the yoke plate and the gold uprights hug the torso back; the shoulder plates run from there to the
rear of the wing (the wings start outside them, `nt_wings.YOKE_U`); the bars and rings lie on the rear face of the wing (3 px thick,
the last pixel embedded in the rear plank tier), so from behind they cross OVER the feathers like in the sheet. All of it is weighted to
`Chest` (no extra bones); the glyph to `Head`.

Groups: `harness` (grey struts, plates, rings), `harness_gold` (uprights, clasps, ring corner caps, clamp collar), `glyph` (the tile:
front and back are separate UV islands, region `private`).

Parameters (`P.get`): harness (False = no harness), glyph (False = no tile), harness_density / glyph_density (UV density multipliers),
harness_bevel (chamfer in px, 0.5), glyph_x / glyph_z / glyph_y (centre of the tile in blocks; defaults read off the sheet).
"""
from __future__ import annotations

import nt_wings as W
from nt_common import K, PIXEL, px

BEVEL_PX = 1.0           # chamfer of the bigger pieces; thinner than MIN_BEVEL_PX stays square (a chamfer must stay a whole texel wide)
MIN_BEVEL_PX = 3.5
# --- yoke (centre piece, X is the signed distance from the spine in px)
CORE = ((-1.7, 1.7), (39.0, 43.5))              # dark centre plate
UPRIGHT = ((1.7, 3.5), (38.6, 43.8))            # gold uprights beside it
CLASP = ((2.0, 3.1), (39.3, 41.8))              # the gold clasps on the uprights
PLATE = ((3.4, 7.2), (36.5, 41.5))              # shoulder plates (grey), from the torso back to the rear of the wing
YOKE_BAR = ((-5.6, 5.6), (38.4, 39.8))          # the horizontal bar across the lower yoke (in front of the plates' rear face)
# --- bars, rings and struts of the right side: (U, Z) end points, in-plane width px
BARS = {
    "rail_a": ((7.2, 41.2), (10.4, 43.8), 2.6),
    "rail_b": ((7.2, 38.2), (10.4, 40.6), 2.4),
    "diag": ((7.0, 40.0), (14.0, 48.6), 2.2),      # the long diagonal brace from the plate up to the leading-edge column
    "strut": ((13.6, 45.0), (15.6, 49.6), 2.4),
    "brace": ((14.6, 41.0), (17.2, 38.4), 2.0),
}
RINGS = {"frame": ((12.5, 42.6), 5.0, 1.6), "ring_a": ((17.4, 37.4), 3.4, 1.3)}
CLAMP = ((14.3, 17.5), (49.4, 53.2))            # clamp block on top of the strut
CLAMP_LOW = ((14.5, 17.5), (46.0, 48.6))        # the second grey block of the column along the leading edge
COLLAR = ((14.8, 17.0), (48.6, 49.4))           # gold collar between the two blocks
# --- the floating tile (blocks): centre x / z of the character's LEFT-hand tile, y behind everything, size / thickness in px
GLYPH_PX = 7.0
GLYPH_THICK_PX = 1.0
GLYPH_X = -0.27
GLYPH_TOP_ABOVE_BUN = 0.01                      # blocks: tile top = bun top + this (landmarks: the tile top is 0.01 above the bun top)


def _bevel(bevel_px: float, *dims_px: float) -> float:
    """Chamfer in blocks: `bevel_px` when every dimension is at least MIN_BEVEL_PX, else none."""
    return bevel_px * PIXEL if bevel_px > 0 and min(dims_px) >= MIN_BEVEL_PX - 1e-9 else 0.0


def _box(m, u, z, y0, y1, group, w, bevel, dens, side=1):
    """Axis-aligned box (chamfered when big enough): U (px) and Z (px) ranges, y range in blocks; `side` -1 mirrors U."""
    ua, ub = sorted((side * u[0] * PIXEL, side * u[1] * PIXEL))
    dims = (abs(u[1] - u[0]), abs(z[1] - z[0]), (y1 - y0) / PIXEL)
    K.block(m, (ua, y0, z[0] * PIXEL), (ub, y1, z[1] * PIXEL), group, w, bevel=_bevel(bevel, *dims), density=dens)


def _bar(m, a, b, width_px, y, group, w, bevel, dens):
    """Square-section bar between two (U, Z) px points at depth `y` (centre), BAR_DEPTH_PX thick along y."""
    path = [(a[0] * PIXEL, y, a[1] * PIXEL), (b[0] * PIXEL, y, b[1] * PIXEL)]
    K.sweep(m, path, px(W.BAR_DEPTH_PX), px(width_px), group, w, side=(0.0, 1.0, 0.0), bevel=_bevel(bevel, W.BAR_DEPTH_PX, width_px), density=dens)


def _ring(m, c, size, wall, y, group, w, bevel, dens):
    """Square frame: top / bottom bars the full width, side bars between them (no overlapping volumes)."""
    (cu, cz), h = c, size / 2.0
    y0, y1 = y - px(W.BAR_DEPTH_PX) / 2.0, y + px(W.BAR_DEPTH_PX) / 2.0
    _box(m, (cu - h, cu + h), (cz + h - wall, cz + h), y0, y1, group, w, bevel, dens)
    _box(m, (cu - h, cu + h), (cz - h, cz - h + wall), y0, y1, group, w, bevel, dens)
    _box(m, (cu - h, cu - h + wall), (cz - h + wall, cz + h - wall), y0, y1, group, w, bevel, dens)
    _box(m, (cu + h - wall, cu + h), (cz - h + wall, cz + h - wall), y0, y1, group, w, bevel, dens)


def _build_harness(m: K.Model, P: dict) -> None:
    dens = float(P.get("harness_density", 1.0))
    bevel = float(P.get("harness_bevel", BEVEL_PX))
    planes = W.back_planes(m, P)
    yoke = planes["yoke"]
    bar_y = planes["bar"]
    bar_back = bar_y - px(W.BAR_DEPTH_PX) / 2.0
    # centre piece: core plate and the two gold uprights with their clasps
    _box(m, CORE[0], CORE[1], yoke - px(3.0), yoke, "harness", "Chest", bevel, dens)
    _box(m, YOKE_BAR[0], YOKE_BAR[1], bar_back - px(1.0), bar_back + px(1.0), "harness", "Chest", bevel, dens)
    mk = m.mark()
    # right half (mirrored below): uprights, clasps, shoulder plate, bars, rings, strut, clamp
    _box(m, UPRIGHT[0], UPRIGHT[1], yoke - px(3.5), yoke, "harness_gold", "Chest", bevel, dens)
    _box(m, CLASP[0], CLASP[1], yoke - px(4.5), yoke - px(3.5), "harness_gold", "Chest", bevel, dens)
    _box(m, PLATE[0], PLATE[1], bar_back, yoke, "harness", "Chest", bevel, dens)
    for a, b, wd in BARS.values():
        _bar(m, a, b, wd, bar_y, "harness", "Chest", bevel, dens)
    for c, size, wall in RINGS.values():
        _ring(m, c, size, wall, bar_y, "harness", "Chest", bevel, dens)
    (cu, cz), h = RINGS["frame"][0], RINGS["frame"][1] / 2.0
    for dx, dz in ((1, 1), (-1, -1)):                      # gold caps on two opposite corners of the big frame
        u0, z0 = cu + dx * (h - 0.6) - 0.7, cz + dz * (h - 0.6) - 0.7
        _box(m, (u0, u0 + 1.4), (z0, z0 + 1.4), bar_back - px(1.0), bar_back + px(0.5), "harness_gold", "Chest", 0.0, dens)
    _box(m, CLAMP[0], CLAMP[1], bar_back, bar_y + px(W.BAR_DEPTH_PX) / 2.0, "harness", "Chest", bevel, dens)
    _box(m, CLAMP_LOW[0], CLAMP_LOW[1], bar_back, bar_y + px(W.BAR_DEPTH_PX) / 2.0, "harness", "Chest", bevel, dens)
    _box(m, COLLAR[0], COLLAR[1], bar_back - px(0.5), bar_y + px(W.BAR_DEPTH_PX) / 2.0, "harness_gold", "Chest", bevel, dens)
    m.mirror(mk)


def glyph_box(P: dict = None):
    """(lo, hi) corners of the floating tile in blocks: above-left of the head's back (character's left, x < 0)."""
    P = P or {}
    from nt_hair import bun_top_z
    top = float(P["glyph_z"]) + px(GLYPH_PX) / 2.0 if "glyph_z" in P else bun_top_z() + GLYPH_TOP_ABOVE_BUN
    x = float(P.get("glyph_x", GLYPH_X))
    y = float(P.get("glyph_y", -0.30))
    s = px(GLYPH_PX)
    t = px(GLYPH_THICK_PX)
    return (x - s / 2.0, y - t / 2.0, top - s), (x + s / 2.0, y + t / 2.0, top)


def _build_glyph(m: K.Model, P: dict) -> None:
    lo, hi = glyph_box(P)
    outline = [(lo[0], hi[1], lo[2]), (hi[0], hi[1], lo[2]), (hi[0], hi[1], hi[2]), (lo[0], hi[1], hi[2])]
    K.prism(m, outline, (0.0, lo[1] - hi[1], 0.0), "glyph", "Head", up=(0.0, 0.0, 1.0), density=float(P.get("glyph_density", 1.0)))


def add(m: K.Model, P: dict) -> None:
    if P.get("harness", True) and P.get("wings", True):
        _build_harness(m, P)
    if P.get("glyph", True):
        _build_glyph(m, P)
