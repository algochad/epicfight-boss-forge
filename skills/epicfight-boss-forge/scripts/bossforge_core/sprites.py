"""Procedural item sprites: weapons (7 categories), boss essence and summon sigil.

All shapes are evaluated in a 32-px "design space" (origin bottom-left, blade axis pointing up-right at 45 degrees)
and sampled at the pixel centres of the target resolution, so a 16x16 render is the same weapon as the 32x32 one.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Optional, Tuple

from .colors import clamp8, hex_to_rgb, is_hex, mix, resolve, shade
from .noise import hash01
from .pngio import Canvas

RGB = Tuple[int, int, int]

CATEGORIES = ("sword", "axe", "dagger", "longsword", "greatsword", "tachi", "spear")

# axis frame (design space, 32 px): origin, along-axis unit d (up-right), perpendicular unit n (down-right)
_OX, _OY = 2.0, 30.0
_D = (0.7071067811865476, -0.7071067811865476)
_N = (0.7071067811865476, 0.7071067811865476)
_MIN_HW = 0.9  # real pixels, applied at res < 32

# blade-type categories: L axis length, pommel radius, grip length, guard length, guard half width,
# blade half width, taper start (fraction of L), curve (lateral tip offset)
_BLADES: Dict[str, Dict[str, float]] = {
    "sword": {"L": 38.0, "pommel": 2.0, "grip": 7.0, "guard_len": 2.0, "guard_hw": 5.0, "blade_hw": 2.2, "taper": 0.82, "curve": 0.0},
    "longsword": {"L": 42.0, "pommel": 2.2, "grip": 9.0, "guard_len": 2.0, "guard_hw": 5.5, "blade_hw": 2.4, "taper": 0.85, "curve": 0.0},
    "greatsword": {"L": 44.0, "pommel": 2.5, "grip": 8.0, "guard_len": 3.0, "guard_hw": 7.0, "blade_hw": 4.0, "taper": 0.78, "curve": 0.0},
    "tachi": {"L": 44.0, "pommel": 1.8, "grip": 8.0, "guard_len": 1.5, "guard_hw": 3.2, "blade_hw": 1.8, "taper": 0.90, "curve": 5.0},
    "dagger": {"L": 26.0, "pommel": 1.8, "grip": 5.0, "guard_len": 1.5, "guard_hw": 3.5, "blade_hw": 1.8, "taper": 0.70, "curve": 0.0},
}
_AXE = {"L": 36.0, "haft_hw": 1.2, "pommel": 1.6, "head_lo": 0.60, "head_hi": 0.95, "head_reach": 7.0, "head_eye": 2.2}
_SPEAR = {"L": 44.0, "shaft_hw": 1.1, "head_lo": 0.72, "head_hw": 3.0}

# the tip (incl. curve) must keep this far from the canvas edge so the 1px outline fits
_TIP_MARGIN = 2.5

DEFAULT_APPEARANCE = {
    "blade": "#cfd6e4", "guard": "#d4aa32", "grip": "#5a3a22", "accent": "#d4aa32", "gem": "#46a0ff",
    "glow": False, "motif": "fuller",
}


def _fit_length(length: float, curve: float) -> float:
    """Largest axis length whose tip (with curve) stays inside the 32 px canvas on the 45 degree axis."""
    cap = min((32.0 - _TIP_MARGIN - _OX) / _D[0] - curve, (_OY - _TIP_MARGIN) / (-_D[1]) + curve)
    return min(length, cap)


def _resolve_appearance(appearance: Optional[dict], palette: Optional[dict]) -> dict:
    ap = dict(DEFAULT_APPEARANCE)
    ap.update(appearance or {})
    pal = dict(palette or {})
    out: Dict[str, object] = {"glow": bool(ap.get("glow")), "motif": ap.get("motif") or "fuller"}
    for key in ("blade", "guard", "grip", "accent", "gem"):
        out[key] = resolve(ap[key], pal)
    return out


def _lerp_shade(c: RGB, t: float) -> RGB:
    return mix(shade(c, 0.8), shade(c, 1.15), t)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _bands(c: RGB, a: float, period: float) -> RGB:
    return shade(c, 0.8) if int(math.floor(a / period)) % 2 else c


# --------------------------------------------------------------------------------------------- blade paint
def _blade_color(ap: dict, b: float, hw: float, t: float, a: float, seed: int, bs: float) -> RGB:
    """Colour of a blade/leaf pixel. b is the lateral offset from the blade centre line, hw its local half width."""
    blade = ap["blade"]
    hw = max(hw, 0.05)
    col = _lerp_shade(blade, _clamp01(0.5 + b / (2.0 * hw)))
    if abs(b) > hw - 0.8:
        col = shade(col, 1.2)
    motif = ap["motif"]
    if motif == "fuller" and abs(b) < 0.7 and 0.05 <= t <= 0.85:
        col = shade(col, 0.85)
    elif motif == "runes":
        phase = hash01(seed & 0xFFFF, 3, 7, 1) * 2.0
        if abs(b) <= 0.75 and ((a - bs - phase) % 6.0) < 1.1 and 0.12 <= t <= 0.88:
            col = ap["accent"]
    return col


# --------------------------------------------------------------------------------------------- shapes
def _paint_blade_weapon(cat: str, ap: dict, res: int, seed: int) -> Callable[[int, int], Optional[RGB]]:
    p = _BLADES[cat]
    s = 32.0 / res
    hw_min = _MIN_HW * s if res < 32 else 0.0
    curve = p["curve"]
    length = _fit_length(p["L"], curve)
    grip, gl, pr = p["grip"], p["guard_len"], p["pommel"]
    ghw = max(p["guard_hw"], hw_min)
    gripw = max(1.3, hw_min)
    bhw = max(p["blade_hw"], hw_min)
    bs = grip + gl
    taper_a = p["taper"] * length
    gem_a = grip + gl / 2.0
    gem_px = (int(math.floor((_OX + gem_a * _D[0]) / s)), int(math.floor((_OY + gem_a * _D[1]) / s)))
    serr_phase = int(hash01(seed & 0xFFFF, 5, 9, 2) * 2)
    pom_a = 0.6 * pr  # centred slightly inside the axis so the disc is not clipped by the canvas corner

    def hw_at(a: float) -> float:
        if a <= taper_a:
            return bhw
        return bhw * (length - a) / max(length - taper_a, 1e-6)

    def fn(x: int, y: int) -> Optional[RGB]:
        rx, ry = (x + 0.5) * s - _OX, (y + 0.5) * s - _OY
        a = rx * _D[0] + ry * _D[1]
        b = rx * _N[0] + ry * _N[1]
        if math.hypot(a - pom_a, b) <= pr:
            return _lerp_shade(ap["guard"], _clamp01(0.5 - b / (2.0 * pr)))
        if grip <= a <= bs and abs(b) <= ghw:
            if (x, y) == gem_px or math.hypot(a - gem_a, b) <= 1.2:
                return ap["gem"]
            return _lerp_shade(ap["guard"], _clamp01(0.5 + b / (2.0 * ghw)))
        if bs < a <= length:
            t = (a - bs) / (length - bs)
            bc = b - curve * t * t
            hw = hw_at(a)
            if abs(bc) <= hw:
                if ap["motif"] == "serrated" and bc > hw - 1.0 and (int(math.floor(a / 1.5)) + serr_phase) % 2 == 1 and t > 0.12:
                    return None
                return _blade_color(ap, bc, hw, t, a, seed, bs)
            return None
        if 0.0 <= a < grip and abs(b) <= gripw:
            return _bands(ap["grip"], a, 3.0)
        return None

    return fn


def _paint_axe(ap: dict, res: int, seed: int) -> Callable[[int, int], Optional[RGB]]:
    p = _AXE
    s = 32.0 / res
    hw_min = _MIN_HW * s if res < 32 else 0.0
    length = _fit_length(p["L"], 0.0)
    haft = max(p["haft_hw"], hw_min)
    reach = p["head_reach"]
    lo, hi = p["head_lo"] * length, p["head_hi"] * length
    mid, half_out = (lo + hi) / 2.0, (hi - lo) / 2.0
    half_in = p["head_eye"]
    pom_a = 0.6 * p["pommel"]

    def fn(x: int, y: int) -> Optional[RGB]:
        rx, ry = (x + 0.5) * s - _OX, (y + 0.5) * s - _OY
        a = rx * _D[0] + ry * _D[1]
        b = rx * _N[0] + ry * _N[1]
        if math.hypot(a - pom_a, b) <= p["pommel"]:
            return _lerp_shade(ap["guard"], _clamp01(0.5 - b / (2.0 * p["pommel"])))
        # head: fans out from the haft (eye) to the cutting edge, one side only; the edge is a shallow arc
        if b >= -haft:
            bb = max(b, 0.0)
            half = half_in + (half_out - half_in) * (bb / reach) ** 0.8
            off = (a - mid) / half_out
            edge = reach * (1.0 - 0.3 * off * off)
            if abs(a - mid) <= half and b <= edge:
                return _axe_head_color(ap, b, edge, a - lo, haft)
        if 0.0 <= a <= length and abs(b) <= haft:
            if mid - half_in - 1.8 <= a < mid - half_in:
                return _lerp_shade(ap["guard"], _clamp01(0.5 + b / (2.0 * haft)))
            return _bands(ap["grip"], a, 3.0)
        return None

    return fn


def _axe_head_color(ap: dict, b: float, edge: float, along: float, haft: float) -> RGB:
    col = _lerp_shade(ap["blade"], _clamp01((b + haft) / (edge + haft)))
    if b > edge - 1.0:
        col = shade(col, 1.2)
    if ap["motif"] == "fuller" and -haft <= b < 1.2:
        col = shade(col, 0.8)
    elif ap["motif"] == "runes" and 2.8 <= b <= 3.8 and abs(along - 6.3) < 0.9:
        col = ap["accent"]
    return col


def _paint_spear(ap: dict, res: int, seed: int) -> Callable[[int, int], Optional[RGB]]:
    p = _SPEAR
    s = 32.0 / res
    hw_min = _MIN_HW * s if res < 32 else 0.0
    length = _fit_length(p["L"], 0.0)
    shaft = max(p["shaft_hw"], hw_min)
    head_len_u = 1.0 - p["head_lo"]

    def fn(x: int, y: int) -> Optional[RGB]:
        rx, ry = (x + 0.5) * s - _OX, (y + 0.5) * s - _OY
        a = rx * _D[0] + ry * _D[1]
        b = rx * _N[0] + ry * _N[1]
        u = a / length
        if u < 0.0 or u > 1.0:
            return None
        if u >= p["head_lo"]:
            x01 = (u - p["head_lo"]) / head_len_u
            w = p["head_hw"] * max(math.sin(math.pi * x01 ** 0.75), 0.0) ** 0.7
            if x01 < 0.10:
                w = max(w, shaft)  # socket: the shaft runs into the leaf
            if abs(b) <= w:
                return _blade_color(ap, b, max(w, 0.05), x01, a, seed, p["head_lo"] * length)
            return None
        if abs(b) <= shaft:
            if p["head_lo"] - 0.05 <= u < p["head_lo"]:
                return _lerp_shade(ap["guard"], _clamp01(0.5 + b / (2.0 * shaft)))
            return _bands(ap["grip"], a, 3.0)
        return None

    return fn


# --------------------------------------------------------------------------------------------- post passes
_NEIGH = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))


def _grow(cv: Canvas, rgba: Tuple[int, int, int, int]) -> None:
    """Paint every transparent pixel that is 8-adjacent to a non-transparent one (measured before the pass)."""
    w, h = cv.w, cv.h
    solid = [cv.data[i * 4 + 3] > 0 for i in range(w * h)]
    for y in range(h):
        for x in range(w):
            if solid[y * w + x]:
                continue
            for dx, dy in _NEIGH:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h and solid[ny * w + nx]:
                    cv.set(x, y, rgba)
                    break


def render_weapon(category: str, appearance: Optional[dict], res: int, seed: int, palette: Optional[dict] = None) -> Canvas:
    """Render one weapon sprite (res 16 or 32). Colours may be #rrggbb or keys of `palette`."""
    if category not in CATEGORIES:
        raise ValueError("unknown weapon category %r; allowed: %s" % (category, ", ".join(CATEGORIES)))
    if res not in (16, 32):
        raise ValueError("sprite resolution must be 16 or 32, got %r" % (res,))
    ap = _resolve_appearance(appearance, palette)
    if category == "axe":
        fn = _paint_axe(ap, res, seed)
    elif category == "spear":
        fn = _paint_spear(ap, res, seed)
    else:
        fn = _paint_blade_weapon(category, ap, res, seed)
    cv = Canvas(res, res)
    for y in range(res):
        for x in range(res):
            c = fn(x, y)
            if c is not None:
                cv.set(x, y, (c[0], c[1], c[2], 255))
    outline = shade(ap["blade"], 0.25)
    _grow(cv, (outline[0], outline[1], outline[2], 255))
    if ap["glow"]:
        acc = ap["accent"]
        _grow(cv, (acc[0], acc[1], acc[2], 140))
    return cv


# --------------------------------------------------------------------------------------------- boss items
def _pal(palette: dict, key: str, default: str) -> RGB:
    v = (palette or {}).get(key, default)
    return hex_to_rgb(v) if is_hex(v) else hex_to_rgb(default)


def render_essence(palette: dict, glow: bool, seed: int) -> Canvas:
    """16x16 faceted diamond in the boss accent colour."""
    accent = _pal(palette, "accent", "#d4aa32")
    white = (255, 255, 255)
    cv = Canvas(16, 16)
    quad = {(-1, -1): 1.12, (1, -1): 1.0, (-1, 1): 0.86, (1, 1): 0.72}
    for y in range(16):
        for x in range(16):
            dx, dy = x - 7.5, y - 7.5
            if abs(dx) + abs(dy) > 6.5:
                continue
            base = mix(shade(accent, 0.7), mix(accent, white, 0.5), (7.5 - y) / 15.0 + 0.5)
            col = shade(base, quad[(-1 if dx < 0 else 1, -1 if dy < 0 else 1)])
            cv.set(x, y, (col[0], col[1], col[2], 255))
    # facet edges and a specular glint
    for i in range(1, 5):
        for (px, py) in ((7 - i, 7 - i), (8 + i, 7 - i)):
            c = cv.get(px, py)
            if c[3]:
                lit = shade(c[:3], 1.18)
                cv.set(px, py, (lit[0], lit[1], lit[2], 255))
    cv.set(5, 5, (255, 255, 255, 255))
    cv.set(6, 4, (mix(accent, white, 0.75) + (255,)))
    edge = shade(accent, 0.3)
    _grow(cv, (edge[0], edge[1], edge[2], 255))
    if glow:
        _grow(cv, (accent[0], accent[1], accent[2], 140))
    return cv


def _rune_circle(x: int, y: int) -> bool:
    a, b = (x - 7.5) / 6.5, (y - 7.5) / 6.5
    r = math.hypot(a, b)
    th = math.atan2(b, a)
    if 0.70 < r < 0.88:
        return True
    if 0.30 < r < 0.60 and abs(((th + math.pi) % (math.pi / 3.0)) - math.pi / 6.0) < 0.20:
        return True
    return r < 0.16


def render_sigil(palette: dict, seed: int) -> Canvas:
    """16x16 dark disc carrying an accent rune circle."""
    primary = _pal(palette, "primary", "#9aa5b8")
    accent = _pal(palette, "accent", "#d4aa32")
    cv = Canvas(16, 16)
    for y in range(16):
        for x in range(16):
            if math.hypot(x - 7.5, y - 7.5) > 6.5:
                continue
            if _rune_circle(x, y):
                col = accent
            else:
                col = shade(primary, 0.4 * (0.92 + 0.16 * hash01(x, y, 0, seed & 0xFFFF)))
            cv.set(x, y, (col[0], col[1], col[2], 255))
    edge = shade(primary, 0.15)
    _grow(cv, (edge[0], edge[1], edge[2], 255))
    return cv
