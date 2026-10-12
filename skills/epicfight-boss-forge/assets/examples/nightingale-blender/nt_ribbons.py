"""Nightingale long ribbons: the tails of the two hair-ribbon loops (`nt_hair`) hang down the back, fan out behind the hip, pass outside the
sleeves and swing out beside the skirt with ragged, split ends. Two ribbons per side, layered (right side built here, then mirrored).
Group `ribbon`.

Reference measurements (sheet pixels through `REF`; side = side view of `sheet_front_side_back.png`):

    R1 short  front view: emerges beside the fist at (157, 317), curves out to (118, 388); side view: runs down the back at x 495-505
              (hugging the robe back) from the head (y 130) to y 310, then flares back to x 460 at y 380.
              3D: loop bottom (0.155, -0.165, 1.41) -> behind the neck (x 0.09, y -0.108, z 1.15) -> out behind the hip to (0.275, -0.108, 0.80)
              -> swing to (0.44, -0.22, 0.52).
    R2 long   front view: hidden behind the skirt until (165, 400), swings out to (112, 470); side view: from (503, 310) back to x 460
              (y 390), returns forward to (520, 480).  3D: inboard of R1 down the back (x 0.06), behind the hip at y -0.115 .. -0.14,
              ends at (0.47, -0.03, 0.17).

The wings (`nt_wings`) fill the volume behind the shoulders at |x| >= 0.10, z 1.1-1.6 (y -0.074 .. -0.155), so down the back the tails keep
to |x| <= 0.09 (hugging the robe back, side view x 495-505 like the sheet) and only leave that column below z 1.1, where the wing roots
end. The ribbon is thin (0.009) and 0.05 wide (12 px); down the back it faces sideways (width along y, so the side view shows it wide and
the back view only as a thin line, like the sheet). In the swing it widens to ~0.14 and twists 16-20 deg: the side view then shows the
big flaps of the sheet (~0.13 wide) while the front view still shows 14 px.

Skinning: the upper end is Head (it ties into the loop), then Chest; R1 hands over to Chest + Arm + Hand (swinging arms push it instead
of cutting through it), R2 to Torso / Root / Thigh.

Parameters (P.get): ribbon_segments (path samples per control interval = value // 3, default 10), ribbon_width (0.05), ribbon_density (1.5),
ribbon_wave (waviness amplitude, 0.008), ribbon_swing (swing width / tail width, 3.0), ribbon_twist (swing twist in degrees, 24), seed.
"""
from __future__ import annotations

import math
import random

from nt_common import REF as R, K

from nt_hair import loop_tail_anchor

THICK = 0.009   # ribbon thickness (2 px)


# ------------------------------------------------------------------------------------------------------------------ curves
def _spline(ctrl, per_seg: int):
    """Catmull-Rom through the control points, each interval as a cubic Bezier (K.bezier). Returns the dense polyline."""
    n = len(ctrl)
    out = []
    for i in range(n - 1):
        p0, p1, p2, p3 = ctrl[max(i - 1, 0)], ctrl[i], ctrl[i + 1], ctrl[min(i + 2, n - 1)]
        c1 = K.vadd(p1, K.vmul(K.vsub(p2, p0), 1.0 / 6.0))
        c2 = K.vsub(p2, K.vmul(K.vsub(p3, p1), 1.0 / 6.0))
        seg = K.bezier([p1, c1, c2, p2], per_seg)
        out.extend(seg if i == 0 else seg[1:])
    return out


def _arc(points):
    """Normalised arc length 0..1 per point."""
    acc = [0.0]
    for a, b in zip(points, points[1:]):
        acc.append(acc[-1] + K.vlen(K.vsub(b, a)))
    tot = acc[-1] or 1.0
    return [d / tot for d in acc]


def _profile(table, s):
    """Piecewise-linear value at s from [(s0, v0), (s1, v1), ...]."""
    if s <= table[0][0]:
        return table[0][1]
    for (a, va), (b, vb) in zip(table, table[1:]):
        if a <= s <= b:
            return K.lerp(va, vb, (s - a) / (b - a) if b > a else 1.0)
    return table[-1][1]


def _wave(ctrl, amp: float, rng: random.Random, skip: int = 2, sx: float = 1.0):
    """Deterministic waviness: the control points after the first `skip` slide along x by a sine of amplitude `amp` (the side view stays clean)."""
    out = []
    ph = rng.uniform(0.0, 6.28)
    for i, p in enumerate(ctrl):
        if i < skip:
            out.append(p)
            continue
        a = amp * math.sin(i * 1.7 + ph)
        out.append((p[0] + sx * a, p[1], p[2]))
    return out


# --------------------------------------------------------------------------------------------------------------- ribbons
def _width_dirs(angles, sx=1.0):
    """Width direction per point: 0 deg = along y (faces sideways), 90 deg = along x (faces front / back); `sx` = -1 mirrors it."""
    return [(sx * math.sin(math.radians(a)), math.cos(math.radians(a)), 0.0) for a in angles]


def _build(m, ctrl, width_tab, twist_tab, w, per_seg, density, group="ribbon", prongs=(), sx=1.0):
    path = _spline(ctrl, per_seg)
    s = _arc(path)
    widths = [_profile(width_tab, v) for v in s]
    sides = _width_dirs([_profile(twist_tab, v) for v in s], sx)
    K.sweep(m, path, widths, [THICK * (1.0 - 0.3 * v) for v in s], group, w, side=sides, density=density)
    frames = K.frames_along(path, sides)
    for s0, off, w_end, s_end, ang in prongs:
        # split end: follows the main path from s0, drifts sideways by `off`, ends early at s_end and tapers to w_end
        idx = [i for i, v in enumerate(s) if s0 <= v <= s_end]
        if len(idx) < 3:
            continue
        pp, ww, aa = [], [], []
        for i in idx:
            t = (s[i] - s0) / max(s_end - s0, 1e-6)
            wd = frames[i][1]
            pp.append(K.vadd(K.vadd(path[i], K.vmul(wd, off * t)), K.vmul(frames[i][2], 0.004 * (1 + t))))
            ww.append(K.lerp(_profile(width_tab, s[i]) * 0.55, w_end, t))
            aa.append(_profile(twist_tab, s[i]) + ang * t)
        K.sweep(m, pp, ww, THICK * 0.8, group, w, side=_width_dirs(aa, sx), density=density)


# ---------------------------------------------------------------------------------------------------------------- layout tables
# Control points (x, y, z) of the RIGHT ribbons in blocks; the first point of each is replaced by the loop's tail anchor. Width tables are
# (arc fraction, width / tail width) and twist tables (arc fraction, degrees from "faces sideways").
R1_CTRL = [None, (0.115, -0.114, 1.364), (0.092, -0.100, 1.280), (0.090, -0.100, 1.150), (0.115, -0.108, 1.050),
           (0.170, -0.108, 0.960), (0.235, -0.108, 0.870), (0.290, -0.105, 0.790), (0.345, -0.119, 0.700),
           (0.372, -0.145, 0.620), (0.376, -0.155, 0.540)]
# width tables: (arc fraction, factor, swing blend 0..1): width = ribbon_width x factor x (1 + (ribbon_swing - 1) x blend); twist tables:
# (arc fraction, factor of ribbon_twist). Arc fractions: R1 reaches z 0.8 at 0.72 and z 0.6 at 0.94; R2 reaches z 0.8 at 0.50, z 0.4 at 0.80.
R1_WIDTH = [(0.0, 0.64, 0.0), (0.10, 1.0, 0.0), (0.68, 1.0, 0.0), (0.84, 1.0, 1.0), (0.95, 1.1, 1.0), (1.0, 0.45, 1.0)]
R1_TWIST = [(0.0, 0.0), (0.68, 0.0), (0.84, 0.5), (1.0, 0.575)]      # the short ribbon is only 10 px wide in the front view
R2_CTRL = [None, (0.085, -0.128, 1.334), (0.062, -0.100, 1.250), (0.060, -0.104, 1.120), (0.090, -0.108, 1.030),
           (0.140, -0.108, 0.950), (0.190, -0.108, 0.860), (0.225, -0.122, 0.760), (0.245, -0.142, 0.640),
           (0.262, -0.157, 0.540), (0.289, -0.159, 0.460), (0.338, -0.158, 0.370), (0.389, -0.140, 0.290),
           (0.430, -0.110, 0.226), (0.455, -0.071, 0.172)]
R2_WIDTH = [(0.0, 0.6, 0.0), (0.10, 0.9, 0.0), (0.50, 1.0, 0.0), (0.58, 1.0, 0.0), (0.72, 1.0, 1.0), (0.90, 1.05, 1.0), (1.0, 0.4, 1.0)]
R2_TWIST = [(0.0, 0.0), (0.50, 0.0), (0.65, 0.8), (1.0, 1.1)]


def _scaled(table, base, swing=1.0):
    """Width table -> [(s, width)] (3-tuples: blend towards the swing width) or twist table -> [(s, degrees)] (2-tuples)."""
    return [(e[0], base * e[1] * (1.0 + (swing - 1.0) * e[2]) if len(e) == 3 else base * e[1]) for e in table]


# The sheet's character-LEFT ribbons (image right of the front view, image left of the back view) are drawn ~0.008 further out than the right
# ones: the front view puts the lower tip at x 336 instead of the mirrored 325-337, the upper one at x 317. Same shape otherwise.
L1_CTRL = R1_CTRL[:8] + [(0.352, -0.119, 0.700), (0.380, -0.145, 0.620), (0.384, -0.155, 0.540)]
L2_CTRL = [p if p is None or p[2] > 0.55 else (p[0] + 0.008, p[1], p[2]) for p in R2_CTRL]


def _ribbons(m: K.Model, P: dict, rng: random.Random, suf: str, sx: float, ctrl1, ctrl2) -> None:
    """Both ribbons of one side: control points are given in the right-hand frame (x > 0), `sx` = -1 builds them on the left with the
    left joints and the width directions mirrored (no `Model.mirror`, so the two sides may differ)."""
    per_seg = max(3, int(P.get("ribbon_segments", 10)) // 3)
    wd = P.get("ribbon_width", R.d(12.0))
    amp = P.get("ribbon_wave", 0.008)
    dens = P.get("ribbon_density", 1.5)
    sw = P.get("ribbon_swing", 3.0)      # width of the swinging part, in units of the tail width
    tw = P.get("ribbon_twist", 24.0)     # twist (deg from "faces sideways") of the swinging part
    anchor = loop_tail_anchor(P)
    up = (anchor[0], anchor[1], anchor[2] + 0.012)          # the tails start inside the one-cell slot of the loop (nt_hair.loop_cells)
    r1 = [up if p is None else p for p in ctrl1]
    r2 = [(anchor[0] - 0.02, anchor[1] - 0.005, anchor[2] - 0.01) if p is None else p for p in ctrl2]
    r1, r2 = [(sx * x, y, z) for x, y, z in _wave(r1, amp, rng)], [(sx * x, y, z) for x, y, z in _wave(r2, amp * 0.8, rng)]
    # R1: from the loop down the back (hugging the robe back), outside the sleeve, out beside the fist
    w1 = K.rule_chain(2, [(1.42, "Head"), (1.26, "Chest"), (1.05, "Chest"), (0.90, {"Chest": 0.5, "Arm_" + suf: 0.5}),
                          (0.55, {"Chest": 0.3, "Arm_" + suf: 0.4, "Hand_" + suf: 0.3})])
    _build(m, r1, _scaled(R1_WIDTH, wd, sw), _scaled(R1_TWIST, tw), w1, per_seg, dens, sx=sx,
           prongs=[(0.72, 0.030, 0.014, 0.96, 25.0), (0.80, -0.024, 0.012, 0.90, -20.0)])
    # R2: longer, behind the hip, swinging out and forward beside the skirt (the side view shows it wide, the front view 14 px)
    w2 = K.rule_chain(2, [(1.42, "Head"), (1.26, "Chest"), (1.15, "Chest"), (0.92, "Torso"), (0.75, "Root"),
                          (0.45, {"Root": 0.6, "Thigh_" + suf: 0.4})])
    _build(m, r2, _scaled(R2_WIDTH, wd, sw), _scaled(R2_TWIST, tw), w2, per_seg, dens, sx=sx,
           prongs=[(0.80, 0.034, 0.014, 0.97, -30.0), (0.86, -0.026, 0.012, 0.93, 30.0)])


def add(m: K.Model, P: dict) -> None:
    """Right ribbons from R1 / R2; the left ones from L1 / L2 (`ribbon_symmetric` True: the mirror image of the right ones)."""
    rng = random.Random(P.get("seed", 7) + 101)
    mk = m.mark()
    _ribbons(m, P, rng, "R", 1.0, R1_CTRL, R2_CTRL)
    if P.get("ribbon_symmetric", False):
        m.mirror(mk)
    else:
        _ribbons(m, P, random.Random(P.get("seed", 7) + 102), "L", -1.0, L1_CTRL, L2_CTRL)
