"""Nightingale hair: the brown hair shell over the skin head cube, the bun, the bangs, the side locks, the leaf sprig pins and the two
white ribbon loops behind the head (the long ribbons that hang down the body are in `nt_ribbons`).

Every number is a sheet pixel (front view `sheet_ortho_front_back.png` unless "side" = the side view of `sheet_front_side_back.png`, or
"back") converted through `REF`; heights of the head features always use the front mapping `R.z` (the head bottom is y 165 in all views).

    * dome        3 stair-stepped tiers: plateau +-29 px (y 89-91), step +-34 px (y 91-94), full shell +-39 px down to y 112; back wall at
                  side x 500, front wall at side x 576 (the bangs stand 0.0166 in front of the face cube). Closed solid over the cube top.
    * C panels    back + side hair from y 112 down to the cube bottom y 165 as C-shaped (U) rings, so nothing lies inside the head cube:
                  side view back x 500, panel end x 543; down to y 125 the hair reaches the face front, below it the cheek is bare.
    * fringe      bangs on the face front (0.0126 thick), parted: the face shows through a stepped notch (+-6.5 px at y 112-122, +-10.5 px
                  down to y 141) and two eye windows (17.5-24 px off centre, y 135-141); full width below y 141 (the painter puts the
                  face on the skin cube: forehead hidden, eyes at y 135-141).
    * side locks  front view x 186-193 (|dx| 32-38 px) narrowing to x 191-196 at the tip y 187; side view: strand x 553-561 down to y 163,
                  x 555-567 to y 177, tip x 566-573 (here 569-574: 0.004 in front of the robe), the upper end is the end of the bangs.
    * bun         tiered ball behind the crown: side view x 463-500, y 74-135; front / back view +-12 px at the top (y 74) to +-30 px (y 93).
    * sprig       19 leaf / vine tiles per side (cream + teal squares 5 px = 0.02, side view x 492-573, y 92-129), group `ornament`;
                  they stand 0.02 off the hair side (front view strip x 180-186).
    * loops       ring ribbon (8.5 px wide) at each side behind the head: back view x 629-672, y 120-178 (centre |dx| 30.5 px), plane
                  0.14 behind the face, tilted 16 deg, the outer side bowing forward; the tops tuck into the bun like a tie.

Weights: everything on the head follows `Head`; the side locks blend Head -> Chest below the head cube.
Parameters (P.get): hair_density (UV multiplier, 2.0), hair_hx (shell half width), hair_bun (bun scale about its centre, 1.0),
loop_scale (ribbon loop size, 1.0), loop_width.
"""
from __future__ import annotations

import math

from nt_common import REF as R, K

# ------------------------------------------------------------------------------------------------------------- head cube
HX_C = R.d(31.5)
Y0_C, Y1_C = R.y(517), R.y(572)            # cube back / front (+y = face)
Z0_C = R.z(165)                            # cube bottom
CLR = 0.004                                # clearance between hair and the skin cube

# ------------------------------------------------------------------------------------------------------------- hair shell
HAIR_HX = R.d(39.0)                        # shell half width (front view: edge x 186 / 264)
SHELL_BACK = R.y(500)                      # back wall of the dome (side view x 500)
SHELL_FRONT = Y1_C + R.d(4.0)              # front wall: side view x 576 (bangs stand 0.0166 off the face)
Z_TOP = R.z(89)                            # top of the dome (front view y 87-90 plateau)
Z_DOME_BOT = R.z(112)                      # the dome closes here; the fringe / C panels continue below
Z_FRINGE_BOT = R.z(141)                    # bottom of the bangs
Z_SIDE_A = R.z(125)                        # below this the cheek is bare (side view: skin from y 126)
PANEL_END = R.y(543)                       # the back / side panel ends here (side view x 543)
BEV = 0.03                                 # plan-view corner chamfer of the shell

# bun tiers (stair-stepped like the sheet silhouette): (y top, y bottom, half width px, side-view back x, side-view front x)
BUN = [
    (74.5, 77.5, 12.0, 476, 497),
    (77.5, 81.5, 15.0, 474, 498),
    (81.5, 85.0, 18.5, 472, 500),
    (85.0, 89.0, 22.5, 469, 503),
    (89.0, 93.0, 27.0, 466, 506),
    (93.0, 99.0, 30.0, 463.5, 510),
    (99.0, 118.0, 30.0, 462.5, 510),
    (118.0, 122.0, 29.0, 465, 510),
    (122.0, 126.0, 26.0, 468, 509),
    (126.0, 130.0, 22.0, 472, 508),
    (130.0, 133.0, 18.0, 477, 507),
    (133.0, 136.0, 13.0, 481, 506),
]

# leaf sprig tiles, side-view pixel boxes (x0, y0, x1, y1 inclusive) and kind (c = cream leaf, t = teal square)
SPRIG = [
    ("c", 492, 96, 496, 101), ("t", 497, 125, 501, 129), ("c", 503, 119, 507, 124), ("c", 509, 107, 513, 111),
    ("t", 509, 113, 513, 117), ("c", 509, 119, 513, 120), ("t", 514, 125, 519, 129), ("t", 515, 101, 519, 105),
    ("t", 515, 113, 520, 117), ("t", 526, 108, 534, 112), ("t", 526, 119, 535, 124), ("t", 537, 114, 545, 119),
    ("t", 546, 120, 553, 125), ("t", 547, 103, 555, 113), ("c", 557, 115, 566, 119), ("c", 562, 109, 566, 113),
    ("c", 563, 103, 567, 107), ("c", 568, 92, 572, 102), ("c", 570, 109, 573, 113),
]

# ribbon loop (back view: centre-line ellipse of the right loop, character's right = image right)
LOOP_CENTER = (R.d(30.5), R.zb(149))       # (|x|, z) of the loop centre: back view (650, 149) vs the head axis x 681
LOOP_RX, LOOP_RZ = R.d(17.5), R.d(25.5)    # centre-line radii (outer ring 43 x 58 px, ribbon 8.5 px wide)
LOOP_TILT = 16.0                           # the top of the loop leans towards the head axis (apex x 657, y 120)
LOOP_Y = -0.14                             # plane of the loop behind the hair (back wall -0.091) ...
LOOP_SLOPE = 0.25                          # ... leaning back at the bottom: dy per dz
LOOP_KX = 0.45                             # ... and bowing forward on the outer side: dy per dx
LOOP_CELL = R.d(3.4)                       # pixel size of the stair-stepped outline (the sheet's steps are 3-4 px)
LOOP_THICK = R.d(2.6)                      # ribbon thickness


# ---------------------------------------------------------------------------------------------------------- shell helpers
def _hx(P: dict) -> float:
    return P.get("hair_hx", HAIR_HX)


def _c_ring(z, hx_o, yb_o, hx_i, yb_i, y_end, b):
    """Closed U-shaped polygon (open at the front): outer path front-right -> back -> front-left, inner path back again."""
    bo, bi = b, max(b - (hx_o - hx_i), 0.004)
    outer = [(hx_o, y_end, z), (hx_o, yb_o + bo, z), (hx_o - bo, yb_o, z), (-hx_o + bo, yb_o, z), (-hx_o, yb_o + bo, z), (-hx_o, y_end, z)]
    inner = [(-hx_i, y_end, z), (-hx_i, yb_i + bi, z), (-hx_i + bi, yb_i, z), (hx_i - bi, yb_i, z), (hx_i, yb_i + bi, z), (hx_i, y_end, z)]
    return outer + inner


def _loop_plane(P: dict):
    """(y0, kx, kz, xc, zc): the plane y = y0 + kx (x - xc) + kz (z - zc) of the right loop."""
    xc, zc = LOOP_CENTER
    return (P.get("loop_y", LOOP_Y), LOOP_KX, LOOP_SLOPE, xc, zc)


def loop_centerline(P: dict, n: int = 96):
    """(x, z) points of the right loop's centre line (a tilted ellipse), closed (the last point is left out)."""
    s = P.get("loop_scale", 1.0)
    xc, zc = LOOP_CENTER
    rx, rz = LOOP_RX * s, LOOP_RZ * s
    cp, sp = math.cos(math.radians(LOOP_TILT)), math.sin(math.radians(LOOP_TILT))
    pts = []
    for k in range(n):
        t = 2.0 * math.pi * k / n
        u, v = rx * math.cos(t), rz * math.sin(t)
        pts.append((xc + u * cp - v * sp, zc + u * sp + v * cp))
    return pts


def loop_tail_anchor(P: dict):
    """Lowest point of the right loop's centre line, on its plane (where the long ribbons leave it): (x, y, z)."""
    x, z = min(loop_centerline(P), key=lambda p: p[1])
    y0, kx, kz, xc, zc = _loop_plane(P)
    return (x, y0 + kx * (x - xc) + kz * (z - zc), z)


def loop_cells(P: dict):
    """Pixel cells (i, j) of the right loop: a band of `loop_width` round the centre line on the grid of `LOOP_CELL` (cell (i, j) spans
    x i..i+1, z j..j+1), cut by a one-cell slot at the lowest point where the ribbon tails leave: a C instead of a closed ring."""
    c = LOOP_CELL
    half = P.get("loop_width", R.d(8.5)) / 2.0
    line = loop_centerline(P)
    xa, za = min(line, key=lambda p: p[1])
    xs = [p[0] for p in line]
    zs = [p[1] for p in line]
    zc = LOOP_CENTER[1]
    cells = set()
    for i in range(int((min(xs) - half) / c) - 1, int((max(xs) + half) / c) + 2):
        for j in range(int((min(zs) - half) / c) - 1, int((max(zs) + half) / c) + 2):
            cx, cz = (i + 0.5) * c, (j + 0.5) * c
            if min((cx - px) ** 2 + (cz - pz) ** 2 for px, pz in line) <= half * half and not (abs(cx - xa) < 0.9 * c and cz < zc):
                cells.add((i, j))
    return cells


def cell_outlines(cells):
    """Boundary polygons of a set of grid cells: counter-clockwise (x right, z up) lists of (i, j) corner points, collinear points
    dropped. Cells touching only at a corner give separate polygons. Raises when the set has a hole."""
    out = {}
    for i, j in cells:
        if (i, j - 1) not in cells:
            out.setdefault((i, j), []).append((i + 1, j))
        if (i + 1, j) not in cells:
            out.setdefault((i + 1, j), []).append((i + 1, j + 1))
        if (i, j + 1) not in cells:
            out.setdefault((i + 1, j + 1), []).append((i, j + 1))
        if (i - 1, j) not in cells:
            out.setdefault((i, j + 1), []).append((i, j))
    loops = []
    while out:
        start = min(out)
        loop, cur, d_in = [start], start, None
        while True:
            nxt = out[cur]
            if len(nxt) == 1 or d_in is None:
                end = nxt[0]
            else:  # corner contact: take the leftmost turn so each component stays one simple polygon
                end = max(nxt, key=lambda e: d_in[0] * (e[1] - cur[1]) - d_in[1] * (e[0] - cur[0]))
            nxt.remove(end)
            if not nxt:
                del out[cur]
            d_in = (end[0] - cur[0], end[1] - cur[1])
            cur = end
            if cur == start:
                break
            loop.append(cur)
        corners = [p for k, p in enumerate(loop)
                   if (p[0] - loop[k - 1][0]) * (loop[(k + 1) % len(loop)][1] - p[1]) != (p[1] - loop[k - 1][1]) * (loop[(k + 1) % len(loop)][0] - p[0])]
        area = sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(corners, corners[1:] + corners[:1]))
        if area <= 0:
            raise ValueError("loop cells enclose a hole")
        loops.append(corners)
    return loops


# ---------------------------------------------------------------------------------------------------------------- parts
def _dome(m: K.Model, P: dict, w: str, dens: float) -> None:
    """Closed stair-stepped cap: plateau (+-29 px, y 87-91), step (+-34 px, y 91-94), then the full shell down to the fringe line."""
    hx = _hx(P)
    cy, hy = (SHELL_BACK + SHELL_FRONT) / 2.0, (SHELL_FRONT - SHELL_BACK) / 2.0
    z_a, z_b = R.z(91.0), R.z(94.0)
    K.stacked_rects(m, [(Z_TOP, 0.0, cy, hx - R.d(9.5), hy - R.d(5.0)), (z_a, 0.0, cy, hx - R.d(9.5), hy - R.d(5.0))],
                    "hair", w, bevel=BEV, density=dens)
    K.stacked_rects(m, [(z_a, 0.0, cy, hx - R.d(5.0), hy - R.d(2.0)), (z_b, 0.0, cy, hx - R.d(5.0), hy - R.d(2.0))],
                    "hair", w, bevel=BEV, density=dens)
    K.stacked_rects(m, [(z_b, 0.0, cy, hx, hy), (Z_DOME_BOT, 0.0, cy, hx, hy)], "hair", w, bevel=BEV, density=dens)


def _panels(m: K.Model, P: dict, w: str, dens: float) -> None:
    """Back + side hair as two C-shaped lofts: upper (to the face front, y 112-125) and lower (ends at the cheek, y 125-165)."""
    hx = _hx(P)
    hx_i, yb_i = HX_C + CLR, Y0_C - CLR
    upper = [_c_ring(z, hx, SHELL_BACK, hx_i, yb_i, SHELL_FRONT, BEV) for z in (Z_DOME_BOT, Z_SIDE_A)]
    K.loft(m, upper, "hair", w, density=dens, cap_up=(0.0, 1.0, 0.0))
    lower = [_c_ring(Z_SIDE_A, hx, SHELL_BACK, hx_i, yb_i, PANEL_END, BEV),
             _c_ring(R.z(150), hx - 0.003, SHELL_BACK, hx_i, yb_i, PANEL_END, BEV),
             _c_ring(Z0_C, hx - 0.012, SHELL_BACK + 0.006, hx_i, yb_i, PANEL_END, BEV)]
    K.loft(m, lower, "hair", w, density=dens, cap_up=(0.0, 1.0, 0.0))


def _fringe(m: K.Model, P: dict, w: str, dens: float) -> None:
    """Right half of the bangs (mirrored by the caller): four stepped blocks leaving the parted face window."""
    y0, y1 = Y1_C + CLR, SHELL_FRONT
    xs = lambda px: R.d(px)  # noqa: E731 - |dx| in px -> |x|
    blocks = [
        (xs(24.0), HX_C + CLR, Z_FRINGE_BOT, Z_DOME_BOT),            # outer block, down to the lower hem
        (xs(6.5), xs(24.0), R.z(122), Z_DOME_BOT),                   # upper part of the fringe, notch +-6.5 px
        (xs(10.5), xs(24.0), R.z(135), R.z(122)),                    # middle part, notch +-10.5 px, above the eye window
        (xs(10.5), xs(17.5), Z_FRINGE_BOT, R.z(135)),                    # strand between face window and eye window
    ]
    for xa, xb, za, zb in blocks:
        K.block(m, (xa, y0, za), (xb, y1, zb), "hair", w, density=dens)


def _lock(m: K.Model, P: dict, dens: float) -> None:
    """Right side lock beside the cheek and in front of the shoulder (side view: strand x 553-561 to y 163, then x 555-567, tip x 566-573
    at y 187 - moved 4 px forward onto the robe front; front view x 186-193 narrowing to x 191-196 at the tip), then mirrored. Upper block = the end of the bangs."""
    xi, xo = HX_C + CLR, _hx(P)
    K.block(m, (xi, R.y(553), R.z(146)), (xo, SHELL_FRONT, Z_SIDE_A), "hair", "Head", density=dens)
    stops = [  # (y px, x in, x out, y back px, y front px); below the robe top (y 172) the lock lies on the robe front (y 0.188)
        (146, xi, xo, 553, 561),
        (163, xi, xo, 553, 561),
        (166, xi, xo, 555, 567),
        (170, xi, xo - 0.002, 558, 571),
        (173, xi - 0.004, xo - 0.006, 569, 574),
        (180, xi - 0.008, xo - 0.012, 569, 574),
        (187, xi - 0.012, xo - 0.021, 570, 574),
    ]
    specs = [(R.z(yp), (a + b) / 2.0, (R.y(yb) + R.y(yf)) / 2.0, (b - a) / 2.0, (R.y(yf) - R.y(yb)) / 2.0) for yp, a, b, yb, yf in stops]
    w = K.rule_blend(2, Z0_C + 0.02, Z0_C - 0.08, "Head", "Chest")
    K.stacked_rects(m, specs, "hair", w, density=dens)


def _bun(m: K.Model, P: dict, w: str, dens: float) -> None:
    sc = P.get("hair_bun", 1.0)
    zc = R.z(104)
    for yt, yb, hxp, xb, xf in BUN:
        z0, z1 = zc + (R.z(yb) - zc) * sc, zc + (R.z(yt) - zc) * sc
        K.block(m, (-R.d(hxp) * sc, R.y(xb), z0), (R.d(hxp) * sc, R.y(xf), z1), "hair", w, density=dens)


def _bun_hx(z: float) -> float:
    for yt, yb, hxp, _, _ in BUN:
        if R.z(yb) <= z <= R.z(yt):
            return R.d(hxp)
    return R.d(BUN[-1][2])


def _sprig(m: K.Model, P: dict) -> None:
    """Cream / teal leaf tiles on the right side of the head (mirrored by the caller)."""
    hx = _hx(P)
    thick = R.d(5.0)
    for kind, x0, y0, x1, y1 in SPRIG:
        ya, yb = R.y(x0), R.y(x1 + 1)
        za, zb = R.z(y1 + 1), R.z(y0)
        yc, zc = (ya + yb) / 2.0, (za + zb) / 2.0
        x_in = hx - 0.003 if yc > SHELL_BACK else max(_bun_hx(zc) - 0.003, 0.05)   # the two rearmost tiles sit on the bun
        K.block(m, (x_in, ya, za), (x_in + thick, yb, zb), "ornament", "Head", density=1.0)


def _loops(m: K.Model, P: dict, dens: float) -> None:
    """Right ribbon loop as a stair-stepped plate: front and back face (one polygon island each) and one wall strip round the outline."""
    y0, kx, kz, xc, zc = _loop_plane(P)
    nrm = K.vnorm((-kx, 1.0, -kz))
    half = P.get("loop_thick", LOOP_THICK) / 2.0
    for poly in cell_outlines(loop_cells(P)):
        back, front = [], []
        for i, j in poly:
            x, z = i * LOOP_CELL, j * LOOP_CELL
            p = (x, y0 + kx * (x - xc) + kz * (z - zc), z)
            back.append(m.vert(K.vsub(p, K.vmul(nrm, half)), "Head"))
            front.append(m.vert(K.vadd(p, K.vmul(nrm, half)), "Head"))
        m.poly(back, "ribbon", up=(0.0, 0.0, 1.0), density=dens)                 # faces away from the head (-y); CCW seen from behind
        m.poly(list(reversed(front)), "ribbon", up=(0.0, 0.0, 1.0), density=dens)
        m.strip([back, front], "ribbon", closed=True, up=(0.0, 0.0, 1.0), density=dens)


def add(m: K.Model, P: dict) -> None:
    dens = P.get("hair_density", 2.0)
    _dome(m, P, "Head", dens)
    _panels(m, P, "Head", dens)
    _bun(m, P, "Head", dens)
    mk = m.mark()
    _fringe(m, P, "Head", dens)
    m.mirror(mk)
    mk = m.mark()
    _lock(m, P, dens)
    m.mirror(mk)
    mk = m.mark()
    _sprig(m, P)
    m.mirror(mk)
    mk = m.mark()
    _loops(m, P, P.get("loop_density", 3.0))
    m.mirror(mk)
