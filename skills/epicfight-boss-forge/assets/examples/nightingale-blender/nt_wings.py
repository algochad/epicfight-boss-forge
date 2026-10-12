"""Nightingale wings: two big angel wings made of broad PLANK feathers on the art-pixel grid (character's right wing first, mirrored).

The new reference (`anatomical_front_back.png`, `hero_front.png`) draws Minecraft pixel art: every feather is a flat plank 3-4 art pixels
wide with a flat, stair-stepped tip, the planks lie in overlapping tiers (roof tiles) and fan out from the shoulder. All numbers
below are ART PIXELS (`nt_common.PIXEL` = 0.0325 model blocks, 1 texel each) in the WING FRAME `(U, Z)`: U = distance outwards from the spine
(the wing's own centre line), Z = height above the soles; the curves were read from the silhouette mask of the sheet's front figure
(px_per_block 232, ground y 504, the wings are mirror-symmetric about sheet x 258.5 there) and rounded:

    OUTER   outer outline, wing top -> outermost tip           UPPER   upper envelope, spine -> wing top -> outer outline
    TIP[r]  tip line of tier r (the planks of the tier end on it, 1 px stairs added); a tier's planks start where its line meets OUTER

Tiers, front to back (every tier lies `STEP_PX` further back; the group = the painter's material):

    le  wing_a  leading-edge planks: their tips step down the outline (the stair-stepped white edge), the front-most tier
    a0  wing_a  short white top coverts under the leading edge (tips ~5 px below the upper envelope)
    a1  wing_a  white coverts along the whole leading edge (tips at Z 41-47)
    b   wing_b  white / pale lavender tier, flat tips at Z 38
    c   wing_c  lavender tier, tips at Z 30 (the inner wing ends here)
    d   wing_d  long pale-blue primaries of the outer half, tips down to Z 17 (thigh / knee height)

A plank is a parallelogram with horizontal top and tip edges whose sides lean along the fan direction (steep near the leading edge,
nearly vertical at the shoulder); its front and back faces are the polygon itself (`K.prism`: two separate UV islands per plank, so the
painter can colour the front white -> lavender -> blue and the back iridescent) and the walls one strip. Every second plank of a tier
sits `PHASE_PX` (a quarter pixel) deeper so overlapping neighbours never share a plane. The planks are 2 px thick, the back face is the
front face moved straight back; the front faces have a slight pitch (tips lean back) and sweep back with the distance from the spine.

Parameters (`P.get`): wings (False = no wings, no harness), wing_density (UV density multiplier), wing_spread (lateral scale about the
spine; the harness does not follow), wing_back (rearward sweep of the outer wing per block of U, 0.05), wing_pitch (tips lean back, block
per block of height, 0.05), wing_gap_px (gap between the torso back and the front tier, 1.5), wing_arm_clear (False puts the planks back
into the pocket the sleeve hides, where a swung arm would plough through them), wing_weight ('chest' | 'shoulder': Chest -> Arm blend over
the first 0.1 block of the wing root, share `wing_arm_weight`), seed (stair jitter of the tips).
"""
from __future__ import annotations

import math
import random

from nt_common import K, PIXEL, px

# --------------------------------------------------------------------------------------------------------- wing frame curves (art px)
OUTER = [(16.5, 59.2), (18.7, 57.4), (20.7, 55.0), (23.6, 51.5), (26.6, 47.5), (27.7, 44.0), (29.3, 40.0), (30.4, 36.0), (32.0, 30.0),
         (33.0, 25.0), (33.3, 20.0), (33.0, 16.5)]
UPPER = [(5.0, 45.0), (8.0, 45.5), (12.0, 49.0), (12.9, 53.5), (13.3, 56.5), (15.0, 59.0)] + OUTER[:]
SPINE_U = 5.0            # the wing's inner end (behind the torso edge, U = 4.8 px)
YOKE_Z = 35.5            # above this height the harness' shoulder plates fill U 3.4-7.2 (see nt_harness): the wing starts outside them
YOKE_U = 7.5
ROWS = ("le", "a0", "a1", "b", "c", "d")
GROUP = {"le": "wing_a", "a0": "wing_a", "a1": "wing_a", "b": "wing_b", "c": "wing_c", "d": "wing_d"}
LE = {"z0": 41.0, "z1": 57.0, "step": 2.8, "width": 3.0, "span": 8.0, "max_len": 14.0}   # leading-edge planks: tips ON the outline, stair steps
TIP = {
    "a0": [(SPINE_U, 48.0), (12.0, 49.5), (14.5, 52.5), (17.0, 53.0), (21.0, 50.5), (25.0, 47.5), (28.5, 44.5)],
    "a1": [(SPINE_U, 44.0), (11.0, 45.0), (15.0, 46.5), (20.0, 45.0), (25.0, 42.5), (29.8, 41.0)],
    "b": [(SPINE_U, 38.5), (13.0, 38.5), (20.0, 39.5), (30.0, 37.5)],
    "c": [(SPINE_U, 31.0), (12.0, 29.5), (20.0, 30.0), (25.0, 31.0), (31.5, 27.0)],
    "d": [(16.5, 31.5), (19.0, 30.8), (20.5, 27.0), (24.0, 24.5), (26.2, 23.3), (28.1, 20.3), (31.2, 16.8), (33.0, 17.0)],
}
WIDTH = {"le": 3, "a0": 4, "a1": 4, "b": 3, "c": 3, "d": 3}          # px, horizontal
OVERLAP = {"a0": 0.0, "a1": 3.0, "b": 3.0, "c": 3.0, "d": 3.0}   # px each plank reaches up under the tier above
LEAN_INNER = {"a0": 8.0, "a1": 8.0, "b": 6.0, "c": 5.0, "d": 4.0}   # degrees at the spine; the outer edge follows the outline
LEAN_MIN = {"b": 8.0, "c": 6.0, "d": 6.0}                        # degrees: the outermost plank of a tier never leans less
LEAN_MAX = 48.0
MAX_LEN = {"a0": 40.0, "a1": 40.0, "b": 40.0, "c": 40.0, "d": 40.0}  # px
D_U_MIN = 20.0
SPACING = 0.92                                              # plank spacing / plank width (neighbours overlap a little)
THICK_PX = 2.0
STEP_PX = 0.5                                               # depth step between tiers
PHASE_PX = 0.25                                             # odd planks of a tier lie this much deeper (px)


_U_GRID = [SPINE_U + i for i in range(0, 31)]       # U samples of a tier's tip line (ray tests)


def _interp(poly, x):
    if x <= poly[0][0]:
        return poly[0][1]
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * ((x - x0) / (x1 - x0) if x1 != x0 else 0.0)
    return poly[-1][1]


def outer_u(z: float) -> float:
    """Outer outline U at height Z (outline runs top -> bottom: Z decreases)."""
    pts = OUTER
    if z >= pts[0][1]:
        return pts[0][0]
    for (u0, z0), (u1, z1) in zip(pts, pts[1:]):
        if z >= z1:
            return u0 + (u1 - u0) * ((z0 - z) / (z0 - z1) if z0 != z1 else 0.0)
    return pts[-1][0]


def outline_lean(z: float) -> float:
    """Lean (degrees from vertical, tip outwards) of the outer outline at height Z."""
    d = 1.5
    a, b = outer_u(z + d), outer_u(z - d)
    return math.degrees(math.atan2(b - a, 2.0 * d))


def _ray_hit(p, d, poly):
    """Smallest t > 0 where p + t d crosses the polyline (points (U, Z))."""
    best = None
    for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
        ex, ey = x1 - x0, y1 - y0
        den = d[0] * ey - d[1] * ex
        if abs(den) < 1e-9:
            continue
        t = ((x0 - p[0]) * ey - (y0 - p[1]) * ex) / den
        s = ((x0 - p[0]) * d[1] - (y0 - p[1]) * d[0]) / den
        if t > 1e-6 and -1e-9 <= s <= 1 + 1e-9:
            best = t if best is None else min(best, t)
    return best


def _upper_z(u: float) -> float:
    """Z of the upper envelope above U (the highest outline point there)."""
    best = None
    for (u0, z0), (u1, z1) in zip(UPPER, UPPER[1:]):
        lo, hi = min(u0, u1), max(u0, u1)
        if lo - 1e-9 <= u <= hi + 1e-9:
            z = z0 + (z1 - z0) * ((u - u0) / (u1 - u0) if u1 != u0 else 0.0)
            best = z if best is None else max(best, z)
    return best if best is not None else UPPER[0][1]


def _tip_z(row: str, u: float) -> float:
    return _interp(TIP[row], u)


def chord_lean(z: float) -> float:
    """Lean of the straight line from the outline at height Z up to the wing's top tip (the long white leading-edge planks of the top
    tiers run along this chord)."""
    ua, za = OUTER[0]
    return math.degrees(math.atan2(outer_u(z) - ua, za - z))


def lean_at(row: str, u: float, z: float) -> float:
    """Fan: the outermost planks follow the outline (the top tiers: the chord to the tip, the others: the local slope), towards the
    spine the lean fades to the tier's inner value."""
    uo = outer_u(z)
    t = K.smoothstep(0.0, 14.0, uo - u)            # 0 at the outline, 1 fourteen px inside
    if row in ("a0", "a1"):
        edge = chord_lean(z)
    else:   # the outline's slope over a plank length (its local slope can be negative where the outline bulges), never below LEAN_MIN
        edge = max(math.degrees(math.atan2(uo - outer_u(z + 14.0), 14.0)), LEAN_MIN[row])
    return min(K.lerp(edge, LEAN_INNER[row], t), LEAN_MAX)


def spine_u(z: float) -> float:
    """Inner end of the wing at height Z: the spine, or the outer face of the harness' shoulder plates above yoke height."""
    return YOKE_U if z >= YOKE_Z else SPINE_U


ARM_CLEAR = (12.0, 37.5)   # (U, Z) px: no plank below this height inside this distance of the spine (the sleeve hides the spot at rest; an arm
#                            swung back 30-60 degrees (EF has no wing bones) would plough through it) -- `wing_arm_clear` = 0 puts them back


def _inside(u: float, z: float, tol: float = 0.3, clear: bool = True) -> bool:
    """Point inside the wing silhouette (outer outline, upper envelope, inner end, arm clearance pocket)."""
    if clear and u < ARM_CLEAR[0] and z < ARM_CLEAR[1]:
        return False
    return spine_u(z) - tol <= u <= outer_u(z) + tol and z <= _upper_z(u) + tol


def inside(u: float, z: float, tol: float = 0.3) -> bool:
    return _inside(u, z, tol)


def layout(P: dict = None):
    """All planks of ONE wing (art px, wing frame): dicts {row, j, poly [(U, Z)...] front face outline (4 or 6 corners), up (dU, dZ)
    unit axis from tip to top, w, lean (deg), len (px)}. Deterministic (`seed`), pure data (also used by tests and the harness).
    Every plank starts on its tier's tip line and runs up along the fan direction until it would leave the silhouette or reach the
    tier above (`OVERLAP` px under its tip line)."""
    P = P or {}
    rng = random.Random(int(P.get("seed", 7)))
    clear = bool(P.get("wing_arm_clear", True))
    inside = lambda u, z: _inside(u, z, clear=clear)  # noqa: E731
    out = []
    # leading-edge planks: their tips step down the outline, every plank hugs it (stair-stepped leading edge)
    w = float(WIDTH["le"])
    z = LE["z0"]
    j = 0
    while z <= LE["z1"] + 1e-6:
        zt = round(z)
        u = outer_u(zt) - w / 2.0
        ang = math.atan2(outer_u(zt) - outer_u(zt + LE["span"]), LE["span"])
        up = (-math.sin(ang), math.cos(ang))
        length = 3.0
        while length + 0.5 <= LE["max_len"]:
            nl = length + 0.5
            ut, zz = u + up[0] * nl, zt + up[1] * nl
            if not (inside(ut - w / 2.0, zz) and inside(ut + w / 2.0, zz)):
                break
            length = nl
        ztop = math.floor(zt + up[1] * length)
        utop = round((u + (ztop - zt) * (up[0] / up[1])) * 2.0) / 2.0
        ux = round(u * 2.0) / 2.0
        out.append(_plank("le", j, ux, zt, utop, ztop, w, math.degrees(ang), step=False))
        z += LE["step"]
        j += 1
    for ri, row in enumerate(ROWS):
        if row == "le":
            continue
        w0 = float(WIDTH[row])
        # first tip: the plank whose outer edge touches the outline at the tip height (fixed point: the tip height depends on U)
        u = outer_u(_tip_z(row, 20.0)) - w0 / 2.0
        for _ in range(6):
            u = outer_u(_tip_z(row, u)) - w0 / 2.0
        j = 0
        u0 = SPINE_U if row != "d" else D_U_MIN                      # (inside U 20 the d tier would only poke 1 px below the c tier)
        while u >= u0 - 1e-6:
            sp = w0 * SPACING
            zt = round(_tip_z(row, u) + rng.choice((-1, 0, 0, 1)))        # stair jitter: whole pixels
            ang = math.radians(lean_at(row, u, zt))
            up = (-math.sin(ang), math.cos(ang))                          # towards the top: inwards (U decreases), Z increases
            for w in (w0, w0 - 1.0, w0 - 2.0):                            # narrower planks where the silhouette tapers
                length = 0.0
                uu = min(u, outer_u(zt + 2.0) + 0.05 - w / 2.0)               # the outermost plank sits on the outline, not outside it
                if w >= 2.0 and inside(uu, zt) and inside(uu - w / 2.0, zt + 2.0) and inside(uu + w / 2.0, zt + 2.0):
                    length = 3.0
                    while length + 0.5 <= MAX_LEN[row]:
                        nl = length + 0.5
                        ut, zz = uu + up[0] * nl, zt + up[1] * nl
                        if not (inside(ut - w / 2.0, zz) and inside(ut + w / 2.0, zz)):
                            break
                        if row != "a0" and zz > _tip_z(ROWS[ri - 1], ut) + OVERLAP[row]:
                            break
                        length = nl
                if length >= 3.0:
                    break
            if length >= 3.0:
                ztop = math.floor(zt + up[1] * length)
                utop = round((uu + (ztop - zt) * (up[0] / up[1])) * 2.0) / 2.0
                ux = round(uu * 2.0) / 2.0
                out.append(_plank(row, j, ux, zt, utop, ztop, w, math.degrees(ang), step=(j % 2 == 0)))
            u -= sp
            j += 1
    return out


def _plank(row, j, ub, zb, ut, zt, w, lean, step):
    """Parallelogram (horizontal top / tip edges), optional 1 px tip step (the inner half 1 px higher)."""
    k = (ut - ub) / (zt - zb) if zt != zb else 0.0            # U shift per unit Z
    def edge(side, z):                                         # outline U at height z, side -1 outer / +1 inner
        return ub + side * w / 2.0 + (z - zb) * k
    if step and zt - zb > 3:
        zs = zb + 1.0
        poly = [(edge(-1, zb), zb), (ub, zb), (ub + k, zs), (edge(1, zs), zs), (edge(1, zt), zt), (edge(-1, zt), zt)]
    else:
        poly = [(edge(-1, zb), zb), (edge(1, zb), zb), (edge(1, zt), zt), (edge(-1, zt), zt)]
    n = math.hypot(ut - ub, zt - zb) or 1.0
    return {"row": row, "j": j, "poly": poly, "up": ((ut - ub) / n, (zt - zb) / n), "w": w, "lean": lean, "len": n}


# --------------------------------------------------------------------------------------------------------- 3D build
BODY_GROUPS = ("robe", "pauldron", "trim", "leather")      # what the harness and the wings must stay behind
WING_GAP_PX = 1.5                                          # gap between the body's back and the front plank tier
BAR_DEPTH_PX = 3.0                                         # harness bars / rings: thickness along y
BAR_EMBED_PX = 1.0                                         # ... of which this much reaches into the rear plank tier


def back_planes(m: K.Model, P: dict = None) -> dict:
    """Depth planes behind the body (blocks, y). `body` = back-most point of the torso / sleeves / pauldrons at shoulder-blade height
    (z 1.15 .. 1.45, |x| <= 0.5), read from the model built so far (so the wings follow the body when the body changes); `yoke` = front
    face of the harness yoke (half a pixel behind it); `wing` = front face of the front-most plank tier (a gap of `wing_gap_px` behind
    the body); `rear` = back face of the deepest tier; `bar` = centre of the harness bars and rings, which sit on the rear of the wing
    (the reference shows the grey bars lying OVER the iridescent back of the wing and hides them from the front)."""
    P = P or {}
    ys = []
    for rec in m.recs:
        if rec["g"] not in BODY_GROUPS:
            continue
        for i in (rec["vis"] if rec["k"] == "poly" else [v for r in rec["rings"] for v in r]):
            x, y, z = m.pos[i]
            if abs(x) <= 0.5 and 1.15 <= z <= 1.45:
                ys.append(y)
    body = min(ys) if ys else -0.066
    wing = body - float(P.get("wing_gap_px", WING_GAP_PX)) * PIXEL
    rear = wing - px((len(ROWS) - 1) * STEP_PX + PHASE_PX + THICK_PX)
    return {"body": body, "yoke": body - 0.5 * PIXEL, "wing": wing, "rear": rear, "bar": rear + px(BAR_EMBED_PX - BAR_DEPTH_PX / 2.0)}


def root_point(P: dict = None):
    """The wing root in the right-wing frame (x, z) blocks: where the wing meets the shoulder blade (skinning anchor of `wing_weight`)."""
    return (px(YOKE_U), px(46.0))


def _weights(P: dict, side: int, y: float):
    """Chest only, or Chest -> Arm blended over the first 0.1 block around the wing root (side +1 = right wing, Arm_R)."""
    if str(P.get("wing_weight", "chest")).lower() != "shoulder":
        return "Chest"
    arm_w = float(P.get("wing_arm_weight", 0.5))
    arm = "Arm_R" if side > 0 else "Arm_L"
    rx, rz = root_point(P)
    rx = side * rx

    def rule(p):
        d = math.sqrt((p[0] - rx) ** 2 + (p[1] - y) ** 2 + (p[2] - rz) ** 2)
        a = arm_w * (1.0 - K.smoothstep(0.0, 0.10, d))
        return {"Chest": 1.0 - a, arm: a}
    return rule


def plank_outline_3d(f: dict, y0: float, spread: float = 1.0, back: float = 0.0, pitch: float = 0.0, side: int = 1):
    """Front-face outline (3D points) of a plank dict of `layout`: x = side * U, z = Z (blocks), y = `y0` (the tier plane) minus the
    sweep (`back` per block of U at the plank centre) and the pitch (tips lean back, `pitch` blocks per block of height)."""
    pts = f["poly"]
    uc = sum(p[0] for p in pts) / len(pts)
    ztop = max(p[1] for p in pts)
    yy = y0 - back * max(uc - SPINE_U, 0.0) * PIXEL * spread
    return [(side * u * PIXEL * spread, yy - pitch * (ztop - z) * PIXEL, z * PIXEL) for u, z in pts]


def tier_y(planes: dict, f: dict) -> float:
    """Front plane of a plank: its tier's depth, odd planks of a tier a quarter pixel deeper."""
    return planes["wing"] - px(ROWS.index(f["row"]) * STEP_PX + (PHASE_PX if f["j"] % 2 else 0.0))


def _build_wing(m: K.Model, P: dict) -> None:
    dens = float(P.get("wing_density", 1.0))
    spread = float(P.get("wing_spread", 1.0))
    back = float(P.get("wing_back", 0.05))
    pitch = float(P.get("wing_pitch", 0.05))
    planes = back_planes(m, P)
    weights = _weights(P, 1, planes["wing"])
    depth = (0.0, -px(THICK_PX), 0.0)
    for f in layout(P):
        up = (f["up"][0], 0.0, f["up"][1])
        K.prism(m, plank_outline_3d(f, tier_y(planes, f), spread, back, pitch), depth, GROUP[f["row"]], weights, up=up, density=dens)


def add(m: K.Model, P: dict) -> None:
    if not P.get("wings", True):
        return
    mk = m.mark()
    _build_wing(m, P)
    m.mirror(mk)
