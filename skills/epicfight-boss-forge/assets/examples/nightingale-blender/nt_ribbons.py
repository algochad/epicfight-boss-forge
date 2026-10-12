"""Nightingale ribbons: two white ribbons per side hang from the character's hip and wrist and swing out beside the skirt with ragged,
stair-cut ends, plus two short hem tabs per side. (The pale-blue ring ribbon around the head is in `nt_hair`.) Group `ribbon`; thin flat
strips (1.2-1.6 px wide, 0.5 px thick) of 5-6 straight segments; the right side is built here and mirrored.

Measured on `anatomical_front_back.png` (front + back figure, 232 image px per block, centre line x 266.5 / 765.5, ground y 504 / 503; the white
pixels of rows y 296-464 on both sides, `/tmp/nightingale-ref2/landmarks.md` section 9):

    long  L   starts at the belt / hip side (|x| 0.2-0.3, z 0.9-1.0, hidden behind the sleeve), leaves the hip at (x 0.29, z 0.77), reaches
              x 0.54-0.565 at z 0.28-0.40 and ends at (0.53, 0.155): length ~0.75 below the hip.
    short S   starts at the wrist (x 0.41, z 0.81), bulges out to x 0.52-0.55 at z 0.65 and ends at (0.524, 0.55): length ~0.30.
    width     8-10 image px = 0.035-0.043 block (1.1-1.3 px), slightly S-curved, flat stair-cut end; the strips turn from edge-on (beside the
              torso) to facing front / back in the swing. The model's swing ends at |x| 0.51-0.535 (the reference: 0.57 on the character's
              right, 0.49 on the left in the front view; 0.47-0.49 back view).
    hem       two short thin tabs per side (front-side x 0.27-0.30, back x 0.20) hanging 0.15 below the hem.

L's hidden upper end sits behind the sleeve (y -0.075; the sleeve spans y -0.03..0.16) so it starts on the torso side, S is attached to the hand.

Skinning: L Torso -> Root -> Thigh (it hangs from the hip), S `Tool` + `Hand` (the wrist ribbon moves rigidly with the fist: no stretching and no
poke-through when the arm swings), hem tabs `Thigh` like the skirt hem.

Parameters (P.get): ribbon_width (px, 1.5: scales every strip), ribbon_density (UV multiplier, 1.0), ribbon_thick (px, 0.5), ribbon_dx (x offset
of the swing below z 0.8, -0.04: the front sheet puts the ribbons 0.07-0.09 further out than the back sheet; -0.04 maximised the mean fit IoU:
front 0.767, back 0.789, against 0.771 at 0 and 0.791 without ribbons).
"""
from __future__ import annotations

import math

from nt_common import K, PIXEL as U

# Right-side control polylines (x, y, z in blocks); widths in px and twists in degrees per point (0 = the ribbon faces sideways, its width
# runs along y; 90 = it faces front / back, its width runs along x).
L_PTS = [(0.255, -0.075, 0.95), (0.290, -0.060, 0.77), (0.400, -0.050, 0.60), (0.495, -0.050, 0.44), (0.535, -0.050, 0.31), (0.530, -0.045, 0.22),
         (0.515, -0.040, 0.155)]
L_WIDTH = [1.0, 1.0, 1.0, 1.1, 1.2, 1.2, 1.1]
L_TWIST = [0, 20, 50, 75, 80, 80, 80]
S_PTS = [(0.395, -0.050, 0.87), (0.425, -0.045, 0.80), (0.495, -0.040, 0.72), (0.530, -0.035, 0.65), (0.532, -0.030, 0.60), (0.520, -0.025, 0.55)]
S_WIDTH = [1.0, 1.0, 1.1, 1.2, 1.1, 1.0]
S_TWIST = [60, 70, 75, 80, 80, 80]
# split, stair-cut ends: (px back from the end of the main strip, px beyond it, sideways offset px, width px)
L_PRONGS = [(3.0, 2.0, 0.7, 0.7), (6.0, -1.0, -0.7, 0.7)]
S_PRONGS = [(2.0, 2.0, -0.6, 0.7), (4.0, -1.0, 0.7, 0.7)]
# hem tabs: (x, y, z) top on the outer face of the hem band, 4 points down, drifting outward
H_PTS = [[(0.326, 0.080, 0.440), (0.334, 0.080, 0.385), (0.342, 0.078, 0.330), (0.350, 0.076, 0.270)],
         [(0.198, -0.088, 0.440), (0.204, -0.092, 0.390), (0.210, -0.095, 0.340), (0.216, -0.098, 0.300)]]


def _side_dir(deg: float):
    a = math.radians(deg)
    return (math.sin(a), math.cos(a), 0.0)


def _strip(m, pts, widths, twists, w, density, thick):
    K.sweep(m, pts, [x * U for x in widths], thick, "ribbon", w, side=[_side_dir(a) for a in twists], density=density)


def _prongs(m, pts, twists, prongs, w, density, thick):
    """Ragged end: short narrow strips along the last segment, starting `back` px before the end of the main strip, reaching `beyond` px past
    it, shifted sideways by `off` px (a negative `beyond` ends before it)."""
    end, prev = pts[-1], pts[-2]
    t = K.vnorm(K.vsub(end, prev))
    side = _side_dir(twists[-1])
    side = K.vnorm(K.vsub(side, K.vmul(t, K.vdot(side, t))))
    for back, beyond, off, wd in prongs:
        a = K.vadd(K.vsub(end, K.vmul(t, back * U)), K.vmul(side, off * U))
        b = K.vadd(K.vadd(end, K.vmul(t, beyond * U)), K.vmul(side, off * U * 1.3))
        K.sweep(m, [a, K.vlerp(a, b, 0.5), b], [wd * U, wd * U, wd * U * 0.8], thick * 0.9, "ribbon", w, side=side, density=density)


def _ribbon(m: K.Model, P: dict, pts, widths, twists, w, prongs) -> None:
    wd = P.get("ribbon_width", 1.5) / 1.5
    dens, thick = P.get("ribbon_density", 1.0), P.get("ribbon_thick", 0.5) * U
    dx = P.get("ribbon_dx", -0.04)                             # swing offset in x: the front and back sheet disagree by 0.07-0.09 there
    pts = [(x + dx * K.smoothstep(0.80, 0.55, z), y, z) for x, y, z in pts]
    _strip(m, pts, [x * wd for x in widths], twists, w, dens, thick)
    _prongs(m, pts, twists, prongs, w, dens, thick)


def add(m: K.Model, P: dict) -> None:
    """Right ribbons + hem tabs, then mirrored."""
    wl = K.rule_chain(2, [(0.95, "Torso"), (0.70, "Torso"), (0.52, {"Root": 0.6, "Thigh_R": 0.4}), (0.25, {"Root": 0.4, "Thigh_R": 0.6})])
    ws = K.rule_chain(2, [(0.87, {"Tool_R": 0.6, "Hand_R": 0.4}), (0.72, {"Tool_R": 0.85, "Hand_R": 0.15}), (0.60, "Tool_R")])
    mk = m.mark()
    _ribbon(m, P, L_PTS, L_WIDTH, L_TWIST, wl, L_PRONGS)
    _ribbon(m, P, S_PTS, S_WIDTH, S_TWIST, ws, S_PRONGS)
    dens, thick = P.get("ribbon_density", 1.0), P.get("ribbon_thick", 0.5) * U
    for pts in H_PTS:
        _strip(m, pts, [1.0, 1.0, 1.0, 0.8], [0, 20, 40, 60], "Thigh_R", dens, thick)
    m.mirror(mk)
