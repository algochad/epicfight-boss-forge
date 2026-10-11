"""Appearance features painted on top of the base/overlay materials (eyes, hair, emblems, trim, ...).

Every feature is ``fn(state, feature_dict)``; it edits ``Texel.rgb`` in place. Face-local coordinates are
``fu`` (0 viewer-left .. 1 viewer-right, looking at the face from outside) and ``fv`` (0 top .. 1 bottom).
Features that name a region the template does not have simply match no texel. Texels of custom-shape boxes
that carry their own style live in a private ``box:<id>`` region: feature defaults ("all regions") never touch
them, only a feature that names that region does.
"""
from __future__ import annotations

import fnmatch
import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from . import colors, ornament
from .materials import MATERIAL_FUNCS, Texel, plate_gold, shade_texel
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
    """Texel selector for a list of region names (arms/legs expanded, hands/feet are the lower texels of the limbs).

    A private custom-shape box is named ``box:<id>``; a name with ``*``, ``?`` or ``[..]`` is a glob over the box
    ids (``box:skirt_fr*`` = every front skirt panel box). ``part:<glob>`` matches the mesh part (the box id of a
    custom shape, whatever its region: ``part:fist_*`` selects the hands, ``part:arm_?_upper`` the upper arms)."""

    def __init__(self, names: Optional[Iterable[str]]):
        self.full = set()  # type: set
        self.globs = []  # type: List[str]
        self.parts = []  # type: List[str]
        self.hands = False
        self.feet = False
        for n in expand_regions(names):
            if n == "hands":
                self.hands = True
            elif n == "feet":
                self.feet = True
            elif n.startswith("part:"):
                self.parts.append(n[5:])
            elif n.startswith("box:") and any(c in n for c in "*?["):
                self.globs.append(n)
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
        if self.globs and r is not None and r.startswith("box:"):
            for g in self.globs:
                if fnmatch.fnmatchcase(r, g):
                    return True
        for g in self.parts:
            if fnmatch.fnmatchcase(t.part, g):
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


def _hair_material(f: dict) -> str:
    """`hair` (default; dark hair greys in flecks) or `hair_soft` (no greying)."""
    return "hair_soft" if f.get("material") == "hair_soft" else "hair"


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
    if style == "anime":
        _eyes_anime(st, texels, f, size, col)
        return
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


LASH = (52, 34, 30)
SCLERA = (244, 238, 224)
HIGHLIGHT = (255, 252, 244)
ROSE = (206, 120, 128)
BLUSH = (240, 140, 150)


def _eyes_anime(st: PaintState, texels: List[Texel], f: dict, size: float, col: RGB) -> None:
    """Anime eyes drawn on the head-front texel grid, mirror-symmetric: a dark lash line on the upper lid, a dark iris
    on the inner side, a small cream patch of eye white on the outer side and one white highlight texel."""
    tfu, tfv = _tex_face(st, texels[0])
    nu, nv = max(1, int(round(1.0 / tfu))), max(1, int(round(1.0 / tfv)))
    wn = max(4, int(round(0.20 * size * nu)))  # eye width in texels
    hn = max(3, int(round(0.13 * size * nv)))  # eye height in texels
    y0 = _num(f.get("y"), 0.55, 0.3, 0.8)
    lash = st.color(f.get("lash"), "hair") if f.get("lash") is not None else LASH
    white = max(1, int(round(0.4 * wn)))  # eye-white columns on the outer side
    r0 = int(round(y0 * nv - hn / 2.0))
    c0 = int(round(0.26 * nu - wn / 2.0))
    boxes = ((c0, 1), (nu - c0 - wn, -1))  # (first column, +1 when the inner side is to the right)
    top, low = _bright(col, 0.7), _bright(col, 1.25)
    for t in texels:
        iu = min(nu - 1, int(t.fu * nu))
        iv = min(nv - 1, int(t.fv * nv))
        for first, inner in boxes:
            k = iu - first  # 0 .. wn-1 across the eye, left to right
            if not 0 <= k < wn:
                if iv == r0 - 1 and (k == -1 if inner > 0 else k == wn):
                    t.rgb = lash  # the lash line runs one texel past the outer end
                continue
            j = iv - r0
            if j == -1:
                t.rgb = lash
            elif 0 <= j < hn:
                side = k if inner > 0 else wn - 1 - k  # 0 at the outer end
                if side < white:
                    t.rgb = SCLERA
                elif j == 0:
                    t.rgb = top
                elif j == hn - 1 and hn >= 3:
                    t.rgb = low
                else:
                    t.rgb = col
                if j == 0 and side == white + (1 if wn - white >= 3 else 0):
                    t.rgb = HIGHLIGHT


def mouth(st: PaintState, f: dict) -> None:
    """A small rose mouth (and a faint nose mark) on the head front."""
    texels = _head_front(st)
    if not texels:
        return
    style = f.get("style", "small")
    col = st.color(f.get("color"), "skin") if f.get("color") is not None else ROSE
    y = _num(f.get("y"), 0.80, 0.5, 0.95)
    nose = bool(f.get("nose", True))
    tfu, tfv = _tex_face(st, texels[0])
    hw = max(_num(f.get("width"), 0.14, 0.05, 0.4) / 2.0, tfu)
    th = max(0.045, tfv)
    for t in texels:
        dx = abs(t.fu - 0.5)
        dy = t.fv - y
        if style == "smile" and dx > hw * 0.55:
            dy += tfv  # the corners curl up by a texel
        if dx <= hw and abs(dy) <= th * 0.5:
            t.rgb = colors.mix(col, (60, 30, 36), 0.45) if style == "line" else col
        elif nose and dx <= max(0.045, tfu) and abs(t.fv - (y - 0.14)) <= max(0.022, 0.5 * tfv):
            t.rgb = colors.mix(t.rgb, col, 0.35)


def blush(st: PaintState, f: dict) -> None:
    """Soft rose cheeks: the skin blends toward the colour in an ellipse under each eye."""
    texels = _head_front(st)
    if not texels:
        return
    col = st.color(f.get("color"), "skin") if f.get("color") is not None else BLUSH
    amount = _num(f.get("amount"), 0.5, 0.0, 1.0)
    size = _num(f.get("size"), 1.0, 0.5, 2.0)
    y = _num(f.get("y"), 0.72, 0.4, 0.95)
    tfu, tfv = _tex_face(st, texels[0])
    rx = max(0.12 * size, 2.0 * tfu)
    ry = max(0.07 * size, 1.5 * tfv)
    for t in texels:
        for cx in (0.2, 0.8):
            d2 = ((t.fu - cx) / rx) ** 2 + ((t.fv - y) / ry) ** 2
            if d2 < 1.0:
                t.rgb = colors.mix(t.rgb, col, amount * 0.75 * (1.0 - d2))
                break


def bangs(st: PaintState, f: dict) -> None:
    """A fringe across the forehead (shorter at the parting, strands of uneven length) plus a lock at each temple.
    The fringe never reaches below `clear` (default 0.42), so the eyes stay free."""
    texels = _head_front(st)
    if not texels:
        return
    col = st.color(f.get("color"), "hair")
    depth = _num(f.get("depth"), 0.30, 0.12, 0.42)
    clear = _num(f.get("clear"), 0.42, 0.15, 0.6)
    pivot = {"left": 0.3, "right": 0.7, "none": None}.get(f.get("part", "center"), 0.5)
    locks = bool(f.get("locks", True))
    lock_w = _num(f.get("lock_width"), 0.12, 0.04, 0.25)
    lock_len = _num(f.get("lock_length"), 0.92, 0.3, 1.0)
    tfu, tfv = _tex_face(st, texels[0])
    seed = st.seed
    for t in texels:
        if t.styled:
            continue
        fu, fv = t.fu, t.fv
        if pivot is None:
            shape = 0.8
        else:
            s = min(1.0, abs(fu - pivot) / max(pivot, 1.0 - pivot))
            shape = 0.55 + 0.45 * s ** 0.8
        strand = (hash01(int(fu / tfu), 7, 0, seed + 150) - 0.5) * 0.07
        bottom = min(clear, depth * shape + strand)
        hit = fv <= bottom
        tip = hit and bottom - fv < tfv
        if locks:
            w = lock_w if fv < 0.58 else lock_w * 0.6
            if (fu < w or fu > 1.0 - w) and fv <= lock_len:
                hit, tip = True, False
        if hit:
            r, g, b = shade_texel(_hair_material(f), t, col, col, seed)
            k = 0.84 if tip else 1.0
            t.rgb = (colors.clamp8(r * k), colors.clamp8(g * k), colors.clamp8(b * k))


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
    length = _num(f.get("length"), 1.0, 0.1, 1.0)  # `long`: how far down the back and sides of the head the hair reaches
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
        if style == "long" and t.face in ("back", "left", "right", "side") and (length >= 1.0 or t.ly > 1.0 - length):
            hit = True
        if hit:
            t.rgb = shade_texel(_hair_material(f), t, col, col, seed)


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
                t.rgb = shade_texel(_hair_material(f), t, col, col, seed)
        elif length == "long" and t.region == "torso" and t.face == "front" and t.ly > 0.88 and t.rgb is not None:
            t.rgb = shade_texel(_hair_material(f), t, col, col, seed)


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


WING_COLOR = (222, 234, 250)
_RING_DIRS = tuple((math.cos(k * math.pi / 6.0), math.sin(k * math.pi / 6.0)) for k in range(12))


def _winged_cross(a: float, b: float) -> int:
    """1 = the Latin cross, 2 = the stepped feather wings flanking it (plane coordinates, b grows downward)."""
    if abs(a) > 1.0 or abs(b) > 1.0:
        return 0
    if (abs(a) < 0.12 and -0.78 < b < 0.8) or (abs(b + 0.3) < 0.11 and abs(a) < 0.36):
        return 1
    sa = abs(a)
    if sa > 0.46:
        for k in range(2):  # two stepped feather lines rising outward, the lower one longer
            reach = 0.78 + 0.22 * k
            centre = (-0.12 + 0.36 * k) - 0.7 * (sa - 0.46)
            if sa <= reach and abs(b - centre) < 0.085:
                return 2
    return 0


def _emblem_part(shape: str, a: float, b: float) -> int:
    """0 = outside, 1 = the emblem, 2 = its secondary part (the wings of `winged_cross`)."""
    if shape == "winged_cross":
        return _winged_cross(a, b)
    if shape == "latin_cross":  # the cross of `winged_cross` without its wings
        return 1 if _winged_cross(a, b) == 1 else 0
    return 1 if _in_emblem(shape, a, b) else 0


def _glow_emblem(st: PaintState, f: dict, shape: str, sel: RegionSel, face: str, col: RGB, size: float,
                 cx: float, cy: float) -> None:
    """Soft halo around the emblem and a white-hot body: halo texels blend toward the colour with the distance
    to the emblem, emblem texels are lightened (their inner texels more)."""
    raw = f.get("glow")
    strength = 1.0 if raw is True else _num(raw, 0.0, 0.0, 1.0)
    if strength <= 0.0:
        return
    halo = int(_num(f.get("halo"), 3, 1, 8))
    hot = _num(f.get("hot"), 0.55, 0.0, 1.0)
    halo_col = st.color(f.get("glow_color"), "accent") if f.get("glow_color") is not None else col
    texels = [t for t in st.texels if t.rgb is not None and t.face == face and sel.match(t)]
    if not texels:
        return
    tfu, _tfv = _tex_face(st, texels[0])
    pu = 2.0 * tfu / size  # one texel in plane units

    def depth(t: Texel, sign: int) -> int:
        """Texels to the nearest texel of the other kind (emblem vs not), 0 if none within the halo reach."""
        a0 = 2.0 * (t.fu - cx) / size
        b0 = 2.0 * (t.fv - (1.0 - cy)) / size * (t.ev / max(t.eh, 1e-6))
        for k in range(1, halo + 1):
            for dx, dy in _RING_DIRS:
                inside = _emblem_part(shape, a0 + dx * k * pu, b0 + dy * k * pu) == 1
                if inside == (sign > 0):
                    return k
        return 0

    white = (255, 255, 255)
    for t in texels:
        a = 2.0 * (t.fu - cx) / size
        b = 2.0 * (t.fv - (1.0 - cy)) / size * (t.ev / max(t.eh, 1e-6))
        if _emblem_part(shape, a, b) == 1:
            d = depth(t, -1)  # distance to the nearest outside texel
            whiteness = hot + (0.35 if d == 0 or d >= 2 else 0.0)
            t.rgb = colors.mix(col, white, min(1.0, whiteness * strength))
        else:
            d = depth(t, 1)  # distance to the nearest emblem texel
            if d:
                w = strength * (1.0 - (d - 1.0) / float(halo)) ** 2 * 0.75
                t.rgb = colors.mix(t.rgb, halo_col, w)


def emblem(st: PaintState, f: dict) -> None:
    shape = f.get("shape", "cross")
    sel = RegionSel([f.get("region", "torso")])
    face = f.get("face", "front")
    col = st.color(f.get("color"), "accent")
    wing = st.color(f.get("wing_color"), "accent") if f.get("wing_color") is not None else WING_COLOR
    size = _num(f.get("size"), 0.6, 0.05, 1.5)
    cx = _num(f.get("cx"), 0.5)
    cy = _num(f.get("cy"), 0.55)
    for t in st.texels:
        if t.rgb is None or t.face != face or not sel.match(t):
            continue
        a = 2.0 * (t.fu - cx) / size
        b = 2.0 * (t.fv - (1.0 - cy)) / size * (t.ev / max(t.eh, 1e-6))
        part = _emblem_part(shape, a, b)
        if part == 1:
            st.paint_decor(t, col)
        elif part == 2:
            t.rgb = wing
    if f.get("glow"):
        _glow_emblem(st, f, shape, sel, face, col, size, cx, cy)


# --------------------------------------------------------------------------- surface features
def trim(st: PaintState, f: dict) -> None:
    sel = RegionSel(_names(f, "torso"))
    where = f.get("where", "hem")
    width = _num(f.get("width"), 0.10, 0.03, 0.25)
    at = _num(f.get("at"), 0.0, 0.0, 1.0)  # hem / cuff: the band starts this far up the region (0 = at the bottom)
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
            hit = at <= ly < at + w
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


CREAM = (243, 236, 210)


def _names(f: dict, default: Optional[str]) -> List[str]:
    """Region names of a feature: `regions` (list) wins over `region`."""
    regions = f.get("regions")
    if isinstance(regions, list) and regions:
        return list(regions)
    one = f.get("region", default)
    return [one] if one is not None else []


def _faces(f: dict, default: Sequence[str]) -> List[str]:
    """Face names of a feature: `faces` (list) or `face`; `sides` stands for left + right + side."""
    raw = f.get("faces")
    if raw is None:
        raw = f.get("face", list(default))
    if isinstance(raw, str):
        raw = [raw]
    out = []  # type: List[str]
    for n in raw:
        for name in (("left", "right", "side") if n == "sides" else (n,)):
            if name not in out:
                out.append(name)
    return out


def _tint(c: RGB, base: RGB, mean_lum: float, texture: float) -> RGB:
    """Colour `c` carrying the brightness variation of the base texel `base` (texture 0 = flat .. 1 = full)."""
    n = colors.luminance(base) / max(mean_lum, 1e-3)
    n = 1.0 + (min(1.5, max(0.6, n)) - 1.0) * texture
    return _bright(c, n)


def _mean_lum(texels: Iterable[Texel]) -> float:
    vals = [colors.luminance(t.rgb) for t in texels if t.rgb is not None]
    return sum(vals) / len(vals) if vals else 0.5


def filigree(st: PaintState, f: dict) -> None:
    """Pixel-art ornament: `pattern` meander|key|lozenge|cross_band|wing as a border along the outline of the
    targeted faces, a band across a height range, or one centred panel motif. Flat colours, 1-cell strokes."""
    pattern = f.get("pattern", "meander")
    where = f.get("where", "border")
    if pattern not in ornament.PATTERNS or where not in ornament.WHERES:
        return
    sel = RegionSel(_names(f, "torso"))
    col = st.color(f.get("color"), "primary") if f.get("color") is not None else CREAM
    fill = st.color(f["fill"], "primary") if f.get("fill") is not None else None
    outline = st.color(f["outline"], "primary") if f.get("outline") is not None else None
    cell = int(_num(f.get("cell"), 0, 0, 8))
    size = f.get("size", "auto")
    if size not in ornament.SIZES:
        size = "auto"
    inset = int(_num(f.get("inset"), 0, 0, 8))
    mirror = bool(f.get("mirror", True))
    sides = [e for e in (f.get("sides") or ornament.EDGES) if e in ornament.EDGES] or list(ornament.EDGES)
    texture = _num(f.get("texture"), 0.5, 0.0, 1.0)
    anchor = f.get("anchor", "center")
    if anchor not in ornament.ANCHORS:
        anchor = "center"
    for face in _faces(f, ("front",)):
        texels = [t for t in st.texels if t.rgb is not None and t.face == face and sel.match(t)]
        for panel in ornament.build_panels(texels, cell):
            if where == "panel":
                fills, edge_cells, motif = ornament.panel_layers(
                    panel, pattern, _num(f.get("cx"), 0.5, 0.0, 1.0), _num(f.get("cy"), 0.5, 0.0, 1.0),
                    _num(f.get("width"), 0.5, 0.1, 1.0), _num(f.get("height"), 0.6, 0.1, 1.0),
                    fill is not None, outline is not None, inset, _num(f.get("motif_height"), 1.0, 0.2, 1.0),
                    anchor, size, sides)
                if fill is not None:
                    lum = _mean_lum(t for c in fills for t in panel.cells.get(c, ()))
                    for c in sorted(fills):
                        for t in panel.cells.get(c, ()):
                            t.rgb = _tint(fill, t.rgb, lum, texture)
                layers = ((edge_cells, outline), (motif, col))
            else:
                rows = ornament.strip_rows(pattern, size, panel.w, panel.h)
                if where == "border":
                    cells = ornament.border_cells(panel.occ, panel.w, panel.h, rows, inset, sides, mirror)
                else:
                    r0, r1 = ornament.band_rows(panel, _num(f.get("lo"), 0.0, 0.0, 1.0), _num(f.get("hi"), 0.2, 0.0, 1.0))
                    cells = ornament.band_cells(panel.occ, panel.w, panel.h, rows, r0, r1, inset, mirror)
                layers = ((cells, col),)
            for cells, rgb in layers:
                if rgb is None:
                    continue
                for c in sorted(cells):
                    for t in panel.cells.get(c, ()):
                        t.rgb = rgb


def gradient(st: PaintState, f: dict) -> None:
    """Vertical colour blend over the targeted texels: `from` at height `lo`, `to` at `hi` (fractions of the
    targeted height, 0 = bottom), clamped beyond. An omitted end keeps the base colour, so a hem fade is just
    `from` + `lo`/`hi`. The base texture's brightness variation is kept (`texture`)."""
    c_from = st.color(f["from"], "primary") if f.get("from") is not None else None
    c_to = st.color(f["to"], "primary") if f.get("to") is not None else None
    if c_from is None and c_to is None:
        return
    names = _names(f, None)
    sel = RegionSel(names) if names else None
    faces = _faces(f, ()) if (f.get("faces") is not None or f.get("face") is not None) else None
    lo = _num(f.get("lo"), 0.0, 0.0, 1.0)
    hi = _num(f.get("hi"), 1.0, 0.0, 1.0)
    if hi < lo:
        lo, hi = hi, lo
    amount = _num(f.get("amount"), 1.0, 0.0, 1.0)
    texture = _num(f.get("texture"), 0.7, 0.0, 1.0)
    smooth = f.get("curve", "smooth") != "linear"
    texels = [t for t in st.texels if t.rgb is not None and (sel.match(t) if sel is not None else default_region(t))
              and (faces is None or t.face in faces)]
    if not texels:
        return
    ymin = min(t.v for t in texels)
    ext = max(max(t.v for t in texels) - ymin, 1e-6)
    lum = _mean_lum(texels)
    for t in texels:
        v = (t.v - ymin) / ext
        s = 0.0 if v <= lo else 1.0 if v >= hi else (v - lo) / (hi - lo)
        if smooth:
            s = s * s * (3.0 - 2.0 * s)
        base = t.rgb
        cf = _tint(c_from, base, lum, texture) if c_from is not None else base
        ct = _tint(c_to, base, lum, texture) if c_to is not None else base
        out = colors.mix(cf, ct, s)
        t.rgb = colors.mix(base, out, amount) if amount < 1.0 else out


def repaint(st: PaintState, f: dict) -> None:
    """Re-shade the targeted texels with another `material`. The box keeps its own colour and accent unless `color` /
    `accent` are given, so any attachment or body part can get silk, lace, silk_feather, hair_soft, ... without the
    preset knowing about it. Needs an explicit target (`region` / `regions`, including `box:<glob>` and `part:<glob>`)."""
    material = f.get("material")
    names = _names(f, None)
    if material not in MATERIAL_FUNCS or not names:
        return
    sel = RegionSel(names)
    faces = _faces(f, ()) if (f.get("faces") is not None or f.get("face") is not None) else None
    color = st.color(f["color"], "primary") if f.get("color") is not None else None
    accent = st.color(f["accent"], "accent") if f.get("accent") is not None else None
    for t in st.texels:
        if t.rgb is None or not sel.match(t) or (faces is not None and t.face not in faces):
            continue
        t.rgb = shade_texel(material, t, color or t.base_rgb or st.color(None, "primary"),
                            accent or t.accent_rgb or st.accent, st.seed)


def sparkle(st: PaintState, f: dict) -> None:
    """Single bright texels sprinkled over the targeted faces (`density` = share of texels, `strength` = blend toward
    `color`, white by default). Deterministic per texel, so the same boss always sparkles in the same places."""
    names = _names(f, None)
    sel = RegionSel(names) if names else None
    faces = _faces(f, ("front", "back", "left", "right", "side"))
    density = _num(f.get("density"), 0.012, 0.0, 0.2)
    strength = _num(f.get("strength"), 0.85, 0.0, 1.0)
    col = st.color(f["color"], "primary") if f.get("color") is not None else (255, 255, 255)
    for t in st.texels:
        if t.rgb is None or t.face not in faces or not (sel.match(t) if sel is not None else default_region(t)):
            continue
        if hash01(t.x, t.y, 29, st.seed + 310) < density:
            t.rgb = colors.mix(t.rgb, col, strength)


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
    "filigree": filigree,
    "gradient": gradient,
    "mouth": mouth,
    "blush": blush,
    "bangs": bangs,
    "repaint": repaint,
    "sparkle": sparkle,
}  # type: Dict[str, Callable[[PaintState, dict], None]]


def apply_feature(st: PaintState, f: dict) -> None:
    fn = FEATURES.get(f.get("type"))
    if fn is not None:
        fn(st, f)
