"""Appearance features painted on top of the base/overlay materials (eyes, hair, emblems, trim, ...).

Every feature is ``fn(state, feature_dict)``; it edits ``Texel.rgb`` in place. Face-local coordinates are
``fu`` (0 viewer-left .. 1 viewer-right, looking at the face from outside) and ``fv`` (0 top .. 1 bottom).
Features that name a region the template does not have simply match no texel. Texels of custom-shape boxes
that carry their own style live in a private ``box:<id>`` region: feature defaults ("all regions") never touch
them, only a feature that names that region does.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import colors
from .materials import Texel, plate_gold, shade_texel
from .noise import fbm, hash01, vnoise, worley

RGB = Tuple[int, int, int]

ARM_REGIONS = ("arm_l", "arm_r")
LEG_REGIONS = ("leg_l", "leg_r")
HANDS_MAX_LY = 0.22
FEET_MAX_LY = 0.18

# 3 columns x 5 rows glyph bitmaps for the `runes` feature ('#' = painted)
GLYPHS = (
    (".#.", "###", ".#.", ".#.", ".#."),  # G1
    ("#.#", "#.#", "###", "..#", "..#"),  # G2
    ("###", "#..", "###", "..#", "###"),  # G3
    ("#..", "##.", "#.#", ".##", "..#"),  # G4
    (".##", "#..", ".#.", "..#", "##."),  # G5
    ("###", ".#.", "###", ".#.", "###"),  # G6
)
RUNE_PIXEL = 0.0625  # block size of one glyph pixel (1/16 block, never below one texel)
RUNE_CELL_COLS = 4  # a cell is 4 x 6 glyph pixels: the 3 x 5 glyph plus a one-pixel margin
RUNE_CELL_ROWS = 6


def expand_regions(names: Optional[Iterable[str]]) -> List[str]:
    """arms -> arm_l+arm_r, legs -> leg_l+leg_r; other names pass through (hands/feet are handled by RegionSel)."""
    out: List[str] = []
    for n in names or []:
        if n == "arms":
            out.extend(ARM_REGIONS)
        elif n == "legs":
            out.extend(LEG_REGIONS)
        else:
            out.append(n)
    return out


class RegionSel:
    """Texel selector for a list of region names (arms/legs expanded, hands/feet are the lower texels of the limbs)."""

    def __init__(self, names: Optional[Iterable[str]]):
        self.full = set()  # type: set
        self.hands = False
        self.feet = False
        for n in expand_regions(names):
            if n == "hands":
                self.hands = True
            elif n == "feet":
                self.feet = True
            else:
                self.full.add(n)

    def match(self, t: Texel) -> bool:
        r = t.region
        if r in self.full:
            return True
        if self.hands and r in ARM_REGIONS and t.ly < HANDS_MAX_LY:
            return True
        if self.feet and r in LEG_REGIONS and t.ly < FEET_MAX_LY:
            return True
        return False


def is_private_region(region: Optional[str]) -> bool:
    return region is not None and region.startswith("box:")


def default_region(t: Texel) -> bool:
    """Default target of features without a `regions` list: everything except private custom-box regions."""
    return not is_private_region(t.region)


class PaintState:
    def __init__(self, texels: Sequence[Texel], palette: Dict[str, str], seed: int, tb: float):
        self.texels = texels
        pal = dict(palette)
        for k in ("primary", "secondary", "accent", "skin", "hair", "eyes"):
            pal.setdefault(k, "#808080")
        self.palette = pal
        self.seed = seed
        self.tb = tb
        self.accent = colors.hex_to_rgb(pal["accent"])

    def color(self, ref, default_key: str) -> RGB:
        key = ref if ref is not None else default_key
        try:
            return colors.resolve(key, self.palette)
        except ValueError:
            return colors.resolve(default_key, self.palette)

    def paint_decor(self, t: Texel, rgb: RGB) -> None:
        """Gold-shaded paint when the colour is the palette accent, otherwise flat colour with 12% noise."""
        if rgb == self.accent:
            t.rgb = plate_gold(t, rgb, rgb, self.seed)
        else:
            px, py, pz = t.p
            f = 0.94 + 0.12 * vnoise(px * 3, py * 3, pz * 3, self.seed + 130)
            t.rgb = (colors.clamp8(rgb[0] * f), colors.clamp8(rgb[1] * f), colors.clamp8(rgb[2] * f))

    def visible(self) -> List[Texel]:
        return [t for t in self.texels if t.rgb is not None]


def _num(v, default: float, lo: Optional[float] = None, hi: Optional[float] = None) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        v = default
    v = float(v)
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def _head_front(st: PaintState) -> List[Texel]:
    """Base-layer head texels facing front; texels an overlay entry covers (a crown, a helm) are armor, not skin."""
    return [t for t in st.texels if t.layer == "base" and t.region == "head" and t.face == "front" and not t.covered]


def _tex_face(st: PaintState, t: Texel) -> Tuple[float, float]:
    """Texel size in face-local (fu, fv) units."""
    return t.tb / max(t.eh, 1e-6), t.tb / max(t.ev, 1e-6)


def _bright(c: RGB, f: float) -> RGB:
    return (colors.clamp8(c[0] * f), colors.clamp8(c[1] * f), colors.clamp8(c[2] * f))


# --------------------------------------------------------------------------- face features
def eyes(st: PaintState, f: dict) -> None:
    texels = _head_front(st)
    if not texels:
        return
    style = f.get("style", "human")
    glow = bool(f.get("glow", False))
    size = _num(f.get("size"), 1.0, 0.5, 1.5)
    col = st.color(f.get("color"), "eyes")
    col = _bright(col, 1.15) if glow else _bright(col, 0.92)
    tfu, tfv = _tex_face(st, texels[0])
    hx = max(0.10 * size, 0.5 * tfu)
    hy = max(0.055 * size, 0.5 * tfv)
    centers = (0.27, 0.73)
    # per-eye candidate lists for the single-texel pupil / dot
    per_eye = [[], []]  # type: List[List[Tuple[float, Texel]]]
    for t in texels:
        fu, fv = t.fu, t.fv
        for i, cx in enumerate(centers):
            dx = fu - cx
            dy = fv - 0.55
            inner = 1.0 if cx < 0.5 else -1.0
            if style == "human":
                if abs(dx) <= hx and abs(dy) <= hy:
                    t.rgb = (226, 238, 252)
                    if dx * inner > 0:
                        t.rgb = col
                    icx = cx + inner * hx * 0.5
                    per_eye[i].append(((fu - icx) ** 2 + dy * dy, t))
                elif abs(fv - 0.47) <= max(0.012, 0.5 * tfv) and abs(dx) <= hx:
                    t.rgb = (110, 70, 55)
            elif style == "slit":
                hys = max(hy * 1.6, 0.5 * tfv)
                if (dx / hx) ** 2 + (dy / hys) ** 2 <= 1.0:
                    if abs(dx) <= 0.5 * tfu:
                        t.rgb = (8, 8, 10)
                    else:
                        t.rgb = col
            elif style == "hollow":
                hyh = hy * 1.3
                if abs(dx) <= hx and abs(dy) <= hyh:
                    t.rgb = (6, 6, 10)
                    per_eye[i].append((dx * dx + dy * dy, t))
            elif style == "visor":
                if i == 0 and 0.10 <= fu <= 0.90 and abs(fv - 0.56) <= max(0.06, 0.5 * tfv):
                    t.rgb = col
    if style == "human":
        for cand in per_eye:
            if cand:
                min(cand, key=lambda e: (e[0], e[1].y, e[1].x))[1].rgb = (10, 20, 50)
    elif style == "hollow":
        for cand in per_eye:
            if cand:
                min(cand, key=lambda e: (e[0], e[1].y, e[1].x))[1].rgb = col


def brow(st: PaintState, f: dict) -> None:
    base = st.color(f.get("color"), "hair")
    for t in _head_front(st):
        tfu, tfv = _tex_face(st, t)
        thick = max(0.05, tfv)
        for cx in (0.27, 0.73):
            if abs(t.fu - cx) > 0.17:
                continue
            if cx < 0.5:
                s = (t.fu - (cx - 0.17)) / 0.34
            else:
                s = ((cx + 0.17) - t.fu) / 0.34
            fvb = 0.43 + 0.03 * s
            if abs(t.fv - fvb) <= thick * 0.5:
                r, g, b = shade_texel("hair", t, base, base, st.seed)
                t.rgb = (colors.clamp8(r * 0.75), colors.clamp8(g * 0.75), colors.clamp8(b * 0.75))
                break


def scar(st: PaintState, f: dict) -> None:
    col = st.color(f.get("color"), "skin") if f.get("color") is not None else (232, 176, 168)
    side = f.get("side", "left")
    ex = 0.73 if side == "left" else 0.27  # viewer-left = the character's right
    for t in _head_front(st):
        tfu, tfv = _tex_face(st, t)
        if 0.30 <= t.fv <= 0.75 and abs((t.fu - ex) + 0.55 * (t.fv - 0.5)) < max(0.04, 0.5 * tfu):
            t.rgb = col


def hair(st: PaintState, f: dict) -> None:
    style = f.get("style", "short")
    if style == "bald":
        return
    col = st.color(f.get("color"), "hair")
    seed = st.seed
    for t in st.texels:
        if t.layer != "base" or t.region != "head" or t.styled or t.covered:
            continue
        front = t.face == "front"
        if style == "hood":
            if front and 0.15 <= t.fu <= 0.85 and 0.30 <= t.fv <= 0.95:
                continue
            t.rgb = shade_texel("cloth", t, col, col, seed)
            continue
        if front and 0.12 <= t.fu <= 0.88 and t.fv > 0.30:
            continue
        bx, by, bz = t.pos
        thr = 0.72 + 0.04 * fbm(bx * 6, by * 6, bz * 6, 2, seed + 140)
        hit = t.ly > thr
        if style == "long" and t.face in ("back", "left", "right", "side"):
            hit = True
        if hit:
            t.rgb = shade_texel("hair", t, col, col, seed)


def beard(st: PaintState, f: dict) -> None:
    length = f.get("length", "short")
    col = st.color(f.get("color"), "hair")
    limit = 0.60 if length == "long" else 0.68
    seed = st.seed
    for t in st.texels:
        if t.styled or t.covered:
            continue
        if t.region == "head" and t.layer == "base":
            hit = False
            if t.face == "front" and t.fv > limit and 0.12 <= t.fu <= 0.88:
                hit = True
            elif t.face in ("left", "right", "side") and t.fv > 0.7:
                hit = True
            if hit:
                t.rgb = shade_texel("hair", t, col, col, seed)
        elif length == "long" and t.region == "torso" and t.face == "front" and t.ly > 0.88 and t.rgb is not None:
            t.rgb = shade_texel("hair", t, col, col, seed)


# --------------------------------------------------------------------------- emblem
def _in_emblem(shape: str, a: float, b: float) -> bool:
    if abs(a) > 1.0 or abs(b) > 1.0:
        return False
    r = math.hypot(a, b)
    th = math.atan2(b, a)
    if shape == "cross":
        return max(abs(a), abs(b)) < 0.9 and (abs(a) < 0.2 or abs(b) < 0.2)
    if shape == "sun":
        c8 = math.cos(8 * th)
        if 0.38 < r < 0.5 or r < 0.22:
            return True
        return 0.6 < r < 0.95 - 0.3 * (1 - c8) / 2 and c8 > 0.5
    if shape == "star":
        return r < 0.9 * (0.4 + 0.6 * abs(math.cos(2.5 * (th + math.pi / 2))) ** 3)
    if shape == "crescent":
        return r < 0.8 and math.hypot(a - 0.35, b) > 0.7
    if shape == "rune_circle":
        if 0.7 < r < 0.85 or r < 0.1:
            return True
        return 0.35 < r < 0.6 and abs(((th + math.pi) % (math.pi / 3)) - math.pi / 6) < 0.09
    if shape == "skull":
        if ((a / 0.7) ** 2 + ((b + 0.1) / 0.8) ** 2) < 1.0:
            if math.hypot(abs(a) - 0.28, b + 0.1) < 0.18:
                return False
            if 0.15 <= b <= 0.35 and abs(a) < (b - 0.15) * 0.5:
                return False
            return True
        if abs(a) < 0.4 and 0.45 < b < 0.8:
            x = (a + 0.4) * 5
            return (x - math.floor(x)) >= 0.15
        return False
    return False


def emblem(st: PaintState, f: dict) -> None:
    shape = f.get("shape", "cross")
    sel = RegionSel([f.get("region", "torso")])
    face = f.get("face", "front")
    col = st.color(f.get("color"), "accent")
    size = _num(f.get("size"), 0.6, 0.05, 1.5)
    cx = _num(f.get("cx"), 0.5)
    cy = _num(f.get("cy"), 0.55)
    for t in st.texels:
        if t.rgb is None or t.face != face or not sel.match(t):
            continue
        a = 2.0 * (t.fu - cx) / size
        b = 2.0 * (t.fv - (1.0 - cy)) / size * (t.ev / max(t.eh, 1e-6))
        if _in_emblem(shape, a, b):
            st.paint_decor(t, col)


# --------------------------------------------------------------------------- surface features
def trim(st: PaintState, f: dict) -> None:
    sel = RegionSel([f.get("region", "torso")])
    where = f.get("where", "hem")
    width = _num(f.get("width"), 0.10, 0.03, 0.25)
    col = st.color(f.get("color"), "accent")
    for t in st.texels:
        if t.rgb is None or not sel.match(t):
            continue
        w = max(width, t.tb / max(t.ev, 1e-6))
        ly = t.ly
        if where == "collar":
            hit = ly > 1.0 - w
        elif where == "belt":
            hit = abs(ly - 0.18) < w * 0.5
        else:  # hem and cuff
            hit = ly < w
        if hit:
            st.paint_decor(t, col)


def plates(st: PaintState, f: dict) -> None:
    regions = f.get("regions")
    if regions:
        sel = RegionSel(regions)
        selected = sel.match
    else:
        def selected(t: Texel) -> bool:
            return t.region is not None and t.region != "head" and not is_private_region(t.region)
    spacing = _num(f.get("spacing"), 0.35, 0.05, 2.0)
    seam_ref = f.get("color")
    seam = st.color(seam_ref, "primary") if seam_ref is not None else None
    period_v = spacing * 1.4
    for t in st.texels:
        if t.rgb is None or not selected(t):
            continue
        tb = t.tb
        half_y = 0.5 * max(0.06 * spacing, tb)
        half_x = 0.5 * max(0.05 * period_v, tb)
        bx, by, bz = t.pos
        dy = abs(by - math.floor(by / spacing + 0.5) * spacing)
        sxz = bx + bz
        dx = abs(sxz - math.floor(sxz / period_v + 0.5) * period_v)
        if dy < 0.5 * tb and dx < 0.5 * tb:
            r, g, b = t.rgb
            t.rgb = (colors.clamp8(r * 1.35), colors.clamp8(g * 1.35), colors.clamp8(b * 1.35))
        elif dy < half_y or dx < half_x:
            if seam is not None:
                t.rgb = seam
            else:
                r, g, b = t.rgb
                t.rgb = (colors.clamp8(r * 0.6), colors.clamp8(g * 0.6), colors.clamp8(b * 0.6))


def cracks(st: PaintState, f: dict) -> None:
    sel = RegionSel(f["regions"]) if f.get("regions") else None
    col = st.color(f.get("color"), "accent")
    density = _num(f.get("density"), 0.5, 0.0, 1.0)
    glow = (colors.clamp8(col[0] * 1.1), colors.clamp8(col[1] * 1.1), colors.clamp8(col[2] * 1.1))
    seed = st.seed
    thr0 = 0.02 + 0.05 * density
    patch_limit = 0.15 + 0.45 * density
    for t in st.texels:
        if t.rgb is None or not (sel.match(t) if sel is not None else default_region(t)):
            continue
        bx, by, bz = t.pos
        if fbm(bx * 1.7, by * 1.7, bz * 1.7, 2, seed + 102) > patch_limit:
            continue
        f1, f2 = worley(bx * 3.5, by * 3.5, bz * 3.5, seed + 100)
        if f2 - f1 < max(thr0, t.tb * 2.0):
            t.rgb = glow


def wear(st: PaintState, f: dict) -> None:
    amount = _num(f.get("amount"), 0.5, 0.0, 1.0)
    seed = st.seed
    for t in st.texels:
        if t.rgb is None or not default_region(t):
            continue
        bx, by, bz = t.pos
        k = 1.0
        if vnoise(bx * 18, by * 1, bz * 18, seed + 110) > 1.0 - 0.1 * amount:
            k *= 0.8
        if fbm(bx * 1.3, by * 1.3, bz * 1.3, 2, seed + 111) > 0.78:
            k *= 0.85
        if k != 1.0:
            r, g, b = t.rgb
            t.rgb = (colors.clamp8(r * k), colors.clamp8(g * k), colors.clamp8(b * k))


def runes(st: PaintState, f: dict) -> None:
    sel = RegionSel(f["regions"]) if f.get("regions") else RegionSel(["torso", "arms"])
    col = st.color(f.get("color"), "accent")
    density = _num(f.get("density"), 0.4, 0.0, 1.0)
    seed = st.seed
    for t in st.texels:
        if t.rgb is None or t.face not in ("front", "back") or not sel.match(t):
            continue
        # glyph pixel: 1/16 block, a whole number of texels when the texture is dense enough
        px = t.tb * max(1, round(RUNE_PIXEL / t.tb))
        cell_w = px * RUNE_CELL_COLS
        cell_h = px * RUNE_CELL_ROWS
        bx, by, bz = t.pos
        u = bx * t.vr  # world x along the viewer's right for this face
        v = -by
        cu = math.floor(u / cell_w)
        cv = math.floor(v / cell_h)
        iz = 0 if t.face == "front" else 1
        if hash01(cu, cv, iz, seed + 120) >= density:
            continue
        gx = math.floor((u - cu * cell_w - (cell_w - 3 * px) * 0.5) / px)
        gy = math.floor((v - cv * cell_h - (cell_h - 5 * px) * 0.5) / px)
        if not (0 <= gx < 3 and 0 <= gy < 5):
            continue
        glyph = GLYPHS[min(5, int(hash01(cu, cv, iz, seed + 121) * 6))]
        if glyph[gy][gx] == "#":
            t.rgb = col


def stripes(st: PaintState, f: dict) -> None:
    sel = RegionSel([f.get("region", "torso")])
    orient = f.get("orient", "v")
    count = int(_num(f.get("count"), 3, 1, 8))
    width = _num(f.get("width"), 0.06, 0.02, 0.2)
    col = st.color(f.get("color"), "accent")
    for t in st.texels:
        if t.rgb is None or not sel.match(t):
            continue
        tfu, tfv = _tex_face(st, t)
        if orient == "h":
            if t.face in ("top", "bottom"):
                continue
            half = max(width, tfv * 1.0001) * 0.5
            pos = t.fv
        else:
            if t.face not in ("front", "back"):
                continue
            half = max(width, tfu * 1.0001) * 0.5
            pos = t.fu
        for i in range(count):
            if abs(pos - (i + 0.5) / count) < half:
                st.paint_decor(t, col)
                break


FEATURES = {
    "eyes": eyes,
    "brow": brow,
    "scar": scar,
    "hair": hair,
    "beard": beard,
    "emblem": emblem,
    "trim": trim,
    "plates": plates,
    "cracks": cracks,
    "wear": wear,
    "runes": runes,
    "stripes": stripes,
}  # type: Dict[str, Callable[[PaintState, dict], None]]


def apply_feature(st: PaintState, f: dict) -> None:
    fn = FEATURES.get(f.get("type"))
    if fn is not None:
        fn(st, f)
