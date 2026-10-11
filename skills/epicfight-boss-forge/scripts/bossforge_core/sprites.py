"""Procedural item sprites: weapons (7 categories, per-category shapes), boss essence and summon sigil.

All shapes are evaluated in a 32-px "design space" (origin bottom-left, blade axis pointing up-right at 45 degrees)
and sampled at the pixel centres of the target resolution, so a 16x16 render is the same weapon as the 32x32 one.
Coordinates: `a` runs along the weapon axis from the pommel to the tip, `b` across it (positive = down-right).
"""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

from .colors import clamp8, hex_to_rgb, is_hex, mix, resolve, shade
from .noise import hash01
from .pngio import Canvas
from .spec import GUARD_SHAPES, POMMEL_SHAPES, data

RGB = Tuple[int, int, int]
Pts = List[Tuple[float, float, float]]  # stroke samples: (a, b, radius)

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
_SPEAR = {"L": 44.0, "shaft_hw": 1.1, "head_lo": 0.72, "head_hw": 3.0, "pommel": 2.0}

# the tip (incl. curve) must keep this far from the canvas edge so the 1px outline fits
_TIP_MARGIN = 2.5
# `length` > 1 may push the tip this close to the edge (the outline is then clipped by the canvas)
_MAX_TIP_MARGIN = 1.2

# axial position (design px, from the pommel) where a hand holds each category; used for the in-hand display transform
HOLD_A: Dict[str, float] = {
    "sword": 3.5, "longsword": 4.5, "greatsword": 4.0, "tachi": 4.0, "dagger": 3.0, "axe": 5.0, "spear": 15.5,
}

DEFAULT_APPEARANCE = {
    "blade": "#cfd6e4", "guard": "#d4aa32", "grip": "#5a3a22", "accent": "#d4aa32", "gem": "#46a0ff",
    "glow": False, "motif": "fuller", "guard_shape": "bar", "pommel_shape": "disc", "length": 1.0, "width": 1.0,
}


class ShapeError(ValueError):
    """An appearance option (shape, guard_shape, pommel_shape, length, width) is not usable."""


def _fit_length(length: float, curve: float, margin: float = _TIP_MARGIN) -> float:
    """Largest axis length whose tip (with curve) stays inside the 32 px canvas on the 45 degree axis."""
    cap = min((32.0 - margin - _OX) / _D[0] - curve, (_OY - margin) / (-_D[1]) + curve)
    return min(length, cap)


def _resolve_appearance(appearance: Optional[dict], palette: Optional[dict], category: str) -> dict:
    ap = dict(DEFAULT_APPEARANCE)
    ap.update(appearance or {})
    pal = dict(palette or {})
    out: Dict[str, object] = {"glow": bool(ap.get("glow")), "motif": ap.get("motif") or "fuller"}
    for key in ("blade", "guard", "grip", "accent", "gem"):
        out[key] = resolve(ap[key], pal)
    shapes = data("weapon-categories")[category]["shapes"]
    shape = ap.get("shape") or shapes["default"]
    if shape not in shapes["allowed"]:
        raise ShapeError("shape %r is not allowed for %s; allowed values: %s" % (shape, category, ", ".join(shapes["allowed"])))
    guard, pommel = ap["guard_shape"], ap["pommel_shape"]
    if guard not in GUARD_SHAPES:
        raise ShapeError("guard_shape %r is not allowed; allowed values: %s" % (guard, ", ".join(GUARD_SHAPES)))
    if pommel not in POMMEL_SHAPES:
        raise ShapeError("pommel_shape %r is not allowed; allowed values: %s" % (pommel, ", ".join(POMMEL_SHAPES)))
    for key, lo, hi in (("length", 0.8, 1.15), ("width", 0.7, 1.4)):
        v = ap[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
            raise ShapeError("%s %r must be a number in %s..%s" % (key, v, lo, hi))
        out[key] = float(v)
    out.update(shape=shape, guard_shape=guard, pommel_shape=pommel)
    return out


# --------------------------------------------------------------------------------------------- small helpers
def _width_factor(width: float, res: int) -> float:
    """At 16 px the across-axis pixel lattice steps by 1.41 design px, so a plain multiplier would not change narrow
    blades at all; the 16 px render exaggerates the option (width ** 1.5) to keep it visible."""
    return width if res >= 32 else width ** 1.5


def _lerp_shade(c: RGB, t: float) -> RGB:
    return mix(shade(c, 0.8), shade(c, 1.15), t)


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def _bands(c: RGB, a: float, period: float) -> RGB:
    return shade(c, 0.8) if int(math.floor(a / period)) % 2 else c


def _ab(x: int, y: int, s: float) -> Tuple[float, float]:
    rx, ry = (x + 0.5) * s - _OX, (y + 0.5) * s - _OY
    return rx * _D[0] + ry * _D[1], rx * _N[0] + ry * _N[1]


def _point(a: float, b: float) -> Tuple[float, float]:
    """Design-space (32 px) screen position of axis coordinates (a, b)."""
    return _OX + a * _D[0] + b * _N[0], _OY + a * _D[1] + b * _N[1]


def _stroke_hit(a: float, b: float, pts: Pts) -> Optional[Tuple[float, float]]:
    """(b of the sample, its radius) of the first sample disc covering (a, b), else None."""
    for pa, pb, r in pts:
        da, db = a - pa, b - pb
        if da * da + db * db <= r * r:
            return pb, r
    return None


def _bezier(p0, p1, p2, r: float, n: int = 28) -> Pts:
    out = []
    for i in range(n + 1):
        u = i / float(n)
        w0, w1, w2 = (1 - u) * (1 - u), 2 * u * (1 - u), u * u
        out.append((w0 * p0[0] + w1 * p1[0] + w2 * p2[0], w0 * p0[1] + w1 * p1[1] + w2 * p2[1], r))
    return out


def _hook(a0: float, c0: float, radius: float, r0: float, r1: float, sweep: float = 150.0, n: int = 30) -> Pts:
    """Arc leaving (a0, c0) along +a and curling toward -b (up-left on the sprite) by `sweep` degrees."""
    out = []
    for i in range(n + 1):
        u = i / float(n)
        ph = math.radians(sweep) * u
        out.append((a0 + radius * math.sin(ph), c0 - radius + radius * math.cos(ph), r0 + (r1 - r0) * u))
    return out


def _curl(a0: float, b0: float, sigma: float, rc: float, r: float, n: int = 24) -> Pts:
    """Scroll: leaves (a0, sigma*b0) outward, turns toward +a and spirals back inward."""
    cx, cb = a0 + rc, sigma * b0
    out = []
    for i in range(n + 1):
        u = i / float(n)
        ph = math.radians(250.0) * u
        rr = rc * (1.0 - 0.35 * u)
        out.append((cx - rr * math.cos(ph), cb + sigma * rr * math.sin(ph), r))
    return out


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


# --------------------------------------------------------------------------------------------- pommel
def _pommel_px(kind: str, x: int, y: int, a: float, b: float, s: float, ap: dict, pr: float, pom_a: float):
    """Pommel colour at a pixel, False for a pixel that must stay empty (ring hole), None when not covered."""
    if kind == "none":
        return None
    if kind == "disc":
        if math.hypot(a - pom_a, b) <= pr:
            return _lerp_shade(ap["guard"], _clamp01(0.5 - b / (2.0 * pr)))
        return None
    if kind == "gem":
        rad = max(pr * 1.9, 3.4)
        cx, cy = _point(1.0 + 0.2 * rad, 0.0)
        dx, dy = (x + 0.5) * s - cx, (y + 0.5) * s - cy
        if abs(dx) + abs(dy) > rad:
            return None
        f = (1.3 if dx < 0 else 1.0) * (1.0 if dy < 0 else 0.78)
        if abs(dx) + abs(dy) < rad * 0.3:
            f *= 1.25
        return shade(ap["gem"], f)
    if kind == "spike":
        if -1.8 <= a <= 3.2 and abs(b) <= (pr + 0.4) * (a + 1.8) / 5.0:
            return _lerp_shade(ap["guard"], _clamp01(0.5 - b / (2.0 * pr)))
        return None
    # ring
    r = math.hypot(a - 2.6, b)
    if r < 1.2:
        return False
    if r <= 2.9:
        return _lerp_shade(ap["guard"], _clamp01(0.5 - b / 5.8))
    return None


# --------------------------------------------------------------------------------------------- blade-type weapons
def _shape_params(cat: str, shape: str, p: Dict[str, float]) -> dict:
    q = {"wm": 1.0, "taper": p["taper"], "curve": p["curve"], "wave": False, "leaf": False, "teeth": False, "hook": False,
         "square": False}
    if cat == "tachi":
        q["curve"] = {"curved": 5.0, "straight": 0.0, "hooked": 3.0}[shape]
    if shape == "broad" and cat != "greatsword":
        q["wm"], q["taper"] = 1.5, min(p["taper"], 0.78)
    elif shape == "leaf":
        q["leaf"] = True
    elif shape == "wavy":
        q["wave"], q["wm"] = True, 1.15
    elif shape == "serrated":
        q["teeth"] = True
        q["wm"] = 1.0 if cat == "greatsword" else 1.25
    elif shape == "hooked":
        q["hook"], q["taper"] = True, 0.6
    elif shape == "cleaver":
        q["square"], q["wm"], q["taper"] = True, 1.1, 1.0
    return q


def _paint_blade_weapon(cat: str, ap: dict, res: int, seed: int) -> Callable[[int, int], Optional[RGB]]:
    p = _BLADES[cat]
    s = 32.0 / res
    hw_min = _MIN_HW * s if res < 32 else 0.0
    q = _shape_params(cat, ap["shape"], p)
    curve = q["curve"]
    grip, gl, pr = p["grip"], p["guard_len"], p["pommel"]
    bs = grip + gl
    base_len = _fit_length(p["L"], curve)
    length = min(bs + (base_len - bs) * ap["length"], _fit_length(1e9, curve, _MAX_TIP_MARGIN))
    bhw = max(p["blade_hw"] * q["wm"] * _width_factor(ap["width"], res), hw_min)
    ghw = max(p["guard_hw"], hw_min, bhw + 1.2)
    gripw = max(1.3, hw_min)
    pom_a = 0.6 * pr  # centred slightly inside the axis so the disc is not clipped by the canvas corner
    guard_kind, pommel_kind = ap["guard_shape"], ap["pommel_shape"]
    serr_phase = int(hash01(seed & 0xFFFF, 5, 9, 2) * 2)

    # blade end: a hooked blade stops short, the hook arc supplies the rest of the length
    hook_pts: Pts = []
    blade_end = length
    if q["hook"]:
        rh = max(0.12 * length, 3.0)
        blade_end = max(length - 2.0 * rh, bs + 6.0)
        hook_pts = _hook(blade_end, curve, rh, max(0.8 * bhw, hw_min * 0.9, 0.7), max(0.5 * s * 0.5, 0.4))
    taper_a = q["taper"] * blade_end
    wave_amp = max(0.95, 0.55 * s) if q["wave"] else 0.0
    tooth_d = max(2.1, 1.3 * s) * (1.35 if cat == "greatsword" else 1.0)
    tooth_p = max(3.0, 2.4 * s)

    wave_n = (blade_end - bs) / 9.0 if q["wave"] else 0.0

    def center(t: float) -> float:
        if wave_amp:
            return wave_amp * math.sin(2.0 * math.pi * wave_n * t) * min(1.0, t * 5.0)
        return curve * t * t

    def hw_at(a: float, t: float) -> float:
        if q["leaf"]:
            v = bhw * 1.55 * max(math.sin(math.pi * t ** 0.62), 0.0) ** 0.8
            return max(v, 0.55 * bhw) if t < 0.5 else v
        if a <= taper_a:
            hw = bhw
        elif q["hook"]:
            hw = bhw * (1.0 - 0.2 * (a - taper_a) / max(blade_end - taper_a, 1e-6))
        else:
            hw = bhw * (blade_end - a) / max(blade_end - taper_a, 1e-6)
        if wave_amp:
            hw *= 1.0 + 0.12 * math.cos(2.0 * math.pi * wave_n * t)
        return hw

    # guard geometry
    gem_a = grip + gl / 2.0
    gem_px = (int(math.floor((_OX + gem_a * _D[0]) / s)), int(math.floor((_OY + gem_a * _D[1]) / s)))
    gem_r = 1.2
    ex = max(0.0, 2.4 - gl)  # extra thickness so swept guards stay visible at 16 px
    lw = 0.85 if res >= 32 else 1.15
    guard_pts: Pts = []
    if guard_kind == "ring":
        bulge = max(ghw * 1.35, 6.0)
        guard_pts = _bezier((grip + gl * 0.5, ghw * 0.85), (grip * 0.45, bulge), (pom_a + pr * 0.6 + 0.5, pr * 0.8), lw)
    elif guard_kind == "ornate":
        rc = 1.8 * max(1.0, s * 0.9)
        bar_hw = ghw * 0.8
        guard_pts = _curl(gem_a - 1.0, bar_hw, 1.0, rc, lw) + _curl(gem_a - 1.0, bar_hw, -1.0, rc, lw)
        gem_r = 1.7
    else:
        bar_hw = ghw

    def guard_t(a: float, b: float) -> Optional[float]:
        if guard_kind == "none":
            return None
        if guard_kind == "winged":
            sw = 0.55 * abs(b)
            if abs(b) <= ghw * 1.1 and grip - sw <= a <= bs + ex - sw:
                return _clamp01(0.5 + b / (2.0 * ghw * 1.1))
            return None
        if guard_kind == "curved":
            kq = 0.11 * b * b
            if abs(b) <= ghw and grip + kq <= a <= bs + ex + kq:
                return _clamp01(0.5 + b / (2.0 * ghw))
            return None
        lim = ghw if guard_kind in ("bar", "ring") else bar_hw
        if grip <= a <= bs and abs(b) <= lim:
            return _clamp01(0.5 + b / (2.0 * lim))
        return None

    def fn(x: int, y: int) -> Optional[RGB]:
        a, b = _ab(x, y, s)
        c = _pommel_px(pommel_kind, x, y, a, b, s, ap, pr, pom_a)
        if c is False:
            return None
        if c is not None:
            return c
        gt = guard_t(a, b)
        if gt is not None:
            if guard_kind != "none" and ((x, y) == gem_px or math.hypot(a - gem_a, b) <= gem_r):
                return ap["gem"]
            return _lerp_shade(ap["guard"], gt)
        if guard_pts:
            hit = _stroke_hit(a, b, guard_pts)
            if hit:
                return _lerp_shade(ap["guard"], _clamp01(0.5 + (b - hit[0]) / (2.0 * hit[1])))
            if guard_kind == "ornate" and math.hypot(a - gem_a, b) <= gem_r:
                return ap["gem"]
        if bs < a <= blade_end:
            t = (a - bs) / (blade_end - bs)
            bc = b - center(t)
            hw = hw_at(a, t)
            hwr = hw
            if q["teeth"] and t > 0.1:
                saw = ((a - bs) / tooth_p) % 1.0
                hwr = max(hw - tooth_d * saw * min(1.0, (blade_end - a) / 6.0), 0.3 * hw)
            if q["square"]:
                clip = 5.0 * _clamp01((bc + hw) / (2.0 * hw))
                if a > blade_end - clip:
                    return None
            if -hw <= bc <= hwr:
                if ap["motif"] == "serrated" and bc > hw - 1.0 and (int(math.floor(a / 1.5)) + serr_phase) % 2 == 1 and t > 0.12:
                    return None
                return _blade_color(ap, bc, hw, t, a, seed, bs)
        if hook_pts:
            hit = _stroke_hit(a, b, hook_pts)
            if hit:
                return _blade_color(ap, max(-hit[1], min(hit[1], b - hit[0])), hit[1], 1.0, a, seed, bs)
        if bs < a <= blade_end:
            return None
        top = bs if guard_kind == "none" else grip
        if 0.0 <= a < top and abs(b) <= gripw:
            return _bands(ap["grip"], a, 3.0)
        return None

    return fn


# --------------------------------------------------------------------------------------------- axe
def _paint_axe(ap: dict, res: int, seed: int) -> Callable[[int, int], Optional[RGB]]:
    p = _AXE
    shape = ap["shape"]
    s = 32.0 / res
    hw_min = _MIN_HW * s if res < 32 else 0.0
    length = min(_fit_length(p["L"], 0.0) * ap["length"], _fit_length(1e9, 0.0, _MAX_TIP_MARGIN))
    haft = max(p["haft_hw"], hw_min)
    reach = p["head_reach"] * _width_factor(ap["width"], res)
    if shape == "double":
        reach *= 0.88
    lo, hi = p["head_lo"] * length, p["head_hi"] * length
    mid, half_out = (lo + hi) / 2.0, (hi - lo) / 2.0
    half_in = p["head_eye"]
    pr = p["pommel"]
    pom_a = 0.6 * pr
    pommel_kind, guard_kind = ap["pommel_shape"], ap["guard_shape"]
    cres_out = reach * 1.2
    cres_in = cres_out * 0.74
    cres_off = cres_out * 0.42

    def head(a: float, b: float) -> Optional[RGB]:
        if shape in ("broad", "double"):
            bb = abs(b) if shape == "double" else max(b, 0.0)
            if shape == "broad" and b < -haft:
                return None
            half = half_in + (half_out - half_in) * (bb / reach) ** 0.8
            off = (a - mid) / half_out
            edge = reach * (1.0 - 0.3 * off * off)
            if abs(a - mid) <= half and (abs(b) if shape == "double" else b) <= edge:
                return _axe_head_color(ap, abs(b) if shape == "double" else b, edge, a - lo, haft)
            return None
        if shape == "cleaver":
            hh = half_out * 0.85
            edge = reach * 0.92 - 0.9 * max(0.0, abs(a - mid) - (hh - 2.4))
            if abs(a - mid) <= hh and -haft <= b <= edge:
                return _axe_head_color(ap, b, edge, a - lo, haft)
            return None
        # crescent: a half moon whose convex edge faces out, horns resting on the haft
        if b >= -haft:
            ro = math.hypot(a - mid, b)
            if ro <= cres_out and math.hypot(a - mid, b + cres_off) > cres_in:
                return _axe_head_color(ap, ro - haft - 1.0, cres_out - haft - 1.0, a - lo, haft)
        return None

    def fn(x: int, y: int) -> Optional[RGB]:
        a, b = _ab(x, y, s)
        c = _pommel_px(pommel_kind, x, y, a, b, s, ap, pr, pom_a)
        if c is False:
            return None
        if c is not None:
            return c
        c = head(a, b)
        if c is not None:
            return c
        if 0.0 <= a <= length and abs(b) <= haft:
            if guard_kind != "none" and mid - half_in - 1.8 <= a < mid - half_in:
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


# --------------------------------------------------------------------------------------------- spear
def _paint_spear(ap: dict, res: int, seed: int) -> Callable[[int, int], Optional[RGB]]:
    p = _SPEAR
    shape = ap["shape"]
    s = 32.0 / res
    hw_min = _MIN_HW * s if res < 32 else 0.0
    base_len = _fit_length(p["L"], 0.0)
    head_lo = {"leaf": 0.72, "broad": 0.68, "trident": 0.66, "hooked": 0.62}[shape]
    a_hs = head_lo * base_len
    length = min(a_hs + (base_len - a_hs) * ap["length"], _fit_length(1e9, 0.0, _MAX_TIP_MARGIN))
    shaft = max(p["shaft_hw"], hw_min)
    pr = p["pommel"]
    pom_a = 0.6 * pr + 0.4
    pommel_kind, guard_kind = ap["pommel_shape"], ap["guard_shape"]
    if pommel_kind == "disc":
        pommel_kind = "none"  # a plain butt; gem/spike/ring add a butt cap
    head_hw = {"leaf": 3.0, "broad": 4.5, "trident": 1.6, "hooked": 1.7}[shape] * _width_factor(ap["width"], res)
    head_hw = max(head_hw, hw_min)
    wp = max(1.15, hw_min)
    bp = max(4.9 * _width_factor(ap["width"], res), head_hw + wp + 2.6)
    lw = 0.85 if res >= 32 else 1.15

    # hooked: the spike ends early and the tip curls back
    hook_pts: Pts = []
    spike_end = length
    if shape == "hooked":
        rh = max(0.13 * length, 3.0)
        spike_end = max(length - 2.0 * rh, a_hs + 5.0)
        hook_pts = _hook(spike_end, 0.0, rh, max(0.75 * head_hw, 0.7), 0.4)
    prong_len = 0.72 * (length - a_hs)

    def head_w(x01: float) -> float:
        if shape == "broad":
            return head_hw * max(math.sin(math.pi * x01 ** 0.9), 0.0) ** 0.6
        if shape == "trident":
            return head_hw * max(math.sin(math.pi * x01 ** 0.8), 0.0) ** 0.7
        if shape == "hooked":
            return head_hw * max(math.sin(math.pi * x01 ** 0.6), 0.0) ** 0.6
        return head_hw * max(math.sin(math.pi * x01 ** 0.75), 0.0) ** 0.7

    def fn(x: int, y: int) -> Optional[RGB]:
        a, b = _ab(x, y, s)
        c = _pommel_px(pommel_kind, x, y, a, b, s, ap, pr, pom_a)
        if c is False:
            return None
        if c is not None:
            return c
        if a < 0.0 or a > length:
            return None
        if hook_pts:
            hit = _stroke_hit(a, b, hook_pts)
            if hit:
                return _blade_color(ap, max(-hit[1], min(hit[1], b - hit[0])), hit[1], 1.0, a, seed, a_hs)
        if a >= a_hs:
            if shape == "trident":
                # crossbar with forward-curling ends, two outer prongs and the centre spike
                kq = 0.045 * b * b
                if abs(b) <= bp + wp and a_hs - 0.5 + kq <= a <= a_hs + 2.3 + kq:
                    return _lerp_shade(ap["guard"], _clamp01(0.5 + b / (2.0 * (bp + wp))))
                for sg in (-1.0, 1.0):
                    pa0 = a_hs + 1.5 + 0.045 * bp * bp
                    if pa0 <= a <= pa0 + prong_len:
                        u = (a - pa0) / prong_len
                        if abs(b - sg * bp * (1.0 - 0.1 * u)) <= wp * (1.0 - u ** 1.5):
                            return _blade_color(ap, b - sg * bp, wp, u, a, seed, a_hs)
            if shape == "hooked" and a > spike_end:
                return None
            end = spike_end if shape == "hooked" else length
            x01 = (a - a_hs) / max(end - a_hs, 1e-6)
            w = head_w(min(x01, 1.0))
            if x01 < 0.10:
                w = max(w, shaft)  # socket: the shaft runs into the head
            if abs(b) <= w:
                return _blade_color(ap, b, max(w, 0.05), x01, a, seed, a_hs)
            return None
        if abs(b) <= shaft:
            if guard_kind != "none" and a_hs - 0.05 * base_len <= a < a_hs:
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


def hold_point(category: str) -> Tuple[float, float]:
    """Where a hand grips the item sprite, as an item-model offset from the model centre in 1/16 block units."""
    if category not in HOLD_A:
        raise ValueError("unknown weapon category %r; allowed: %s" % (category, ", ".join(CATEGORIES)))
    px, py = _point(HOLD_A[category], 0.0)
    return px / 2.0 - 8.0, 8.0 - py / 2.0


def render_weapon(category: str, appearance: Optional[dict], res: int, seed: int, palette: Optional[dict] = None) -> Canvas:
    """Render one weapon sprite (res 16 or 32). Colours may be #rrggbb or keys of `palette`.

    Appearance options beyond colours: shape (per category, see weapon-categories.json), guard_shape, pommel_shape,
    length (0.8-1.15) and width (0.7-1.4). Invalid values raise ShapeError (a ValueError); bad colours raise ValueError.
    """
    if category not in CATEGORIES:
        raise ValueError("unknown weapon category %r; allowed: %s" % (category, ", ".join(CATEGORIES)))
    if res not in (16, 32):
        raise ValueError("sprite resolution must be 16 or 32, got %r" % (res,))
    ap = _resolve_appearance(appearance, palette, category)
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
