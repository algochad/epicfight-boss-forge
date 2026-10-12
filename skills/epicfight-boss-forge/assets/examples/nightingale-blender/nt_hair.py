"""Nightingale head items: brown hair (dome, tall stepped bun, parted bangs, side locks, back curtain), the silver leaf LAUREL with its two
temple pins, and the pale-blue ribbon LOOPS beside the head. The long ribbons that hang down the body are in `nt_ribbons`.

Everything is on the art pixel grid (`nt_common.px`, 1 px = 1 texel = 0.0325 blocks; the head cube is 8 x 8 x 8 px, `HEAD_BOX`). Numbers
below are pixels: x from the centre line, y from the head centre (+ = face), z above the chin plane (the cube spans 0..8). They were
measured on `anatomical_front_back.png` (front + back figure, one art pixel = 6.6 image px there):

    * dome          10 px wide (1 px of hair beyond the cube each side), 11 deep (back wall 2 px behind the cube), 3 px above the cube top
                    (z 8-11): the front view's hair outline is full width up to z 10.5-11.7 (1.79 blocks).
    * bun           stepped tower on the top-back of the dome: widths 8 / 6 / 4 px at z 11-12 / 12-13 / 13-14 (3 px deep at the top: the 3/4
                    view), flat top at z 14 = 1.907 blocks (RefStage2: bun top 1.90). The drawing has top row 3 px, then 4.5, 6.4, 8, 9.5 px.
    * bangs         2 rows tall only (z 6-8: the painted face carries the side-swept hair below, its row 4 holds the eyes), 1 px in front of
                    the face: |x| 1-4 at z 7-8, |x| 2-4 at z 6-7, a V-shaped parting 2 px wide at the top and 4 px at z 6-7; nothing lower.
    * side hair     1 px shell on both sides (x 4-5) from the dome down to the shoulder line, back curtain (y -6..-4) down to the collar;
                    the locks continue 3 px below the chin, standing 0.012 in front of the robe.
    * laurel        ring of leaf combs round the back and sides at z 6-9 (back view: z 6.5-9.2), 1 px outside the hair (|x| 5-6, y -7..-6):
                    a mid-height spine with 2-px tines every 3 px (the chevron leaves point away from the back centre), a 2 px gap at the back
                    centre; at each temple (y 2-3) a 1 x 1 px pin rises to z 12 (front view: pins 6.9-12.3).
    * loops         C-shaped flat ribbon about 1.3 px wide (the reference's ring ribbon is 0.03-0.035 thick) on each side, in a plane behind the
                    head (y -6.5): from the laurel corner (z 6.5 px = 1.66 blocks) out to |x| 8 px (z 3) and back in to |x| 5 (z -0.4 px =
                    1.44 blocks); front view outer half-width 0.252 blocks, back view 0.226-0.231.

Weights: everything on the head follows `Head`; the locks and the loops blend Head -> Chest below the chin.
Parameters (P.get): hair_density (UV multiplier, 1.0), loop_density (1.0), loop_width (px, 1.3), loop_scale (size about the laurel corner, 1.0).
"""
from __future__ import annotations

from nt_common import K, PIXEL as U, HEAD_BOX, Z_COLLAR

HX, Y0, Y1, Z0, Z1 = HEAD_BOX
YC = (Y0 + Y1) / 2.0                        # head centre (y)

DOME_TOP = 11.0                             # hair dome top (px above the chin plane)
BACK = -6.0                                 # back wall of the hair (2 px behind the head cube)
BUN = [  # (z0, z1, half width, y back, y front) in px: the bun tower on the top-back of the dome, flat top at z 14
    (11.0, 12.0, 4.0, BACK, -2.0),
    (12.0, 13.0, 3.0, BACK, -3.0),
    (13.0, 14.0, 2.0, BACK, -3.0),
]
BUN_TOP = BUN[-1][1]
LAUREL_Z = (6, 9)                           # laurel rows (z cells 6, 7, 8); the spine is the middle row
PIN_TOP = 12.0
LOOP_Y = -6.5                               # plane of the ribbon loops (centre of their thickness)
LOOP_ARC = ((6.0, 6.5), (9.0, 3.0), (5.2, -0.4))   # quadratic Bezier (x, z) px of the right loop's centre line


def X(a: float) -> float:
    return a * U


def Y(a: float) -> float:
    return YC + a * U


def Z(a: float) -> float:
    return Z0 + a * U


def bun_top_z() -> float:
    """World z of the flat top of the bun (what floats above the head clears)."""
    return Z(BUN_TOP)


def _curtain_bottom() -> float:
    """Bottom of the back curtain / side hair in px: on the collar of the robe (it must not enter the shoulder shell)."""
    return (Z_COLLAR + 0.003 - Z0) / U


def _box(m: K.Model, x0, x1, y0, y1, z0, z1, group, w, dens, skip=()):
    K.block(m, (X(x0), Y(y0), Z(z0)), (X(x1), Y(y1), Z(z1)), group, w, density=dens, skip=skip)


# --------------------------------------------------------------------------------------------------------------------- hair
def _hair_centre(m: K.Model, dens: float) -> None:
    """Dome, bun tower and back curtain: boxes symmetric about x = 0."""
    zb = _curtain_bottom()
    _box(m, -5, 5, BACK, 5, 8, DOME_TOP, "hair", "Head", dens, skip=("bottom",))
    for z0, z1, hw, yb, yf in BUN:
        _box(m, -hw, hw, yb, yf, z0, z1, "hair", "Head", dens, skip=("bottom",))
    _box(m, -4, 4, BACK, -4, zb, 8, "hair", "Head", dens)


def _hair_side(m: K.Model, dens: float) -> None:
    """Right half (mirrored by the caller): side hair shell, the two bang blocks and the lock in front of the shoulder."""
    zb = _curtain_bottom()
    _box(m, 4, 5, BACK, 5, zb, 8, "hair", "Head", dens)                     # side shell, from the dome to the shoulder line
    _box(m, 1, 4, 4, 5, 7, 8, "hair", "Head", dens)                       # fringe row 1 (z 7-8): 2 px open at the centre parting
    _box(m, 2, 4, 4, 5, 6, 7, "hair", "Head", dens)                       # fringe row 2 (z 6-7): 4 px open (the V); below z 6 the painted face shows
    w = K.rule_blend(2, Z(zb), Z(-4.0), "Head", "Chest")
    _box(m, 4, 5, 4, 5, -3, zb, "hair", w, dens)                          # lock: 3 px below the chin, in front of the robe


# --------------------------------------------------------------------------------------------------------------------- laurel
def _leaf_cell(s: int, row: int) -> bool:
    """Is cell `row` (0 = bottom, 1 = spine, 2 = top) of laurel column `s` filled: spine everywhere, 2-px tines on columns s % 3 in (0, 1)."""
    return row == 1 or s % 3 != 2


def _laurel_side(m: K.Model, dens: float) -> None:
    """Right half of the laurel (mirrored by the caller): leaf comb on the back wall (columns s 0-3, x 1-5), corner post (s 4), leaf comb on
    the side wall (s 5-12, y -6..2) and the pin (s 13) that rises to z 12."""
    z0, z1 = LAUREL_Z
    back = {(s, z0 + r) for s in range(0, 4) for r in range(3) if _leaf_cell(s, r)}
    side = {(s - 5, z0 + r) for s in range(5, 13) for r in range(3) if _leaf_cell(s, r)}
    K.cell_plate(m, back, "laurel", "Head", origin=(X(1.0), Y(BACK - 0.5), Z0), es=(U, 0.0, 0.0), ez=(0.0, 0.0, U), thickness=U, density=dens)
    K.cell_plate(m, side, "laurel", "Head", origin=(X(5.5), Y(BACK), Z0), es=(0.0, U, 0.0), ez=(0.0, 0.0, U), thickness=U, density=dens)
    _box(m, 5, 6, BACK - 1, BACK, z0, z1, "laurel", "Head", dens)       # corner post
    _box(m, 5, 6, 2, 3, z0, PIN_TOP, "laurel", "Head", dens)            # temple pin: leaf-ring base z 6-9, shaft up to z 12


# --------------------------------------------------------------------------------------------------------------------- loops
def _bezier2(p0, p1, p2, t):
    a, b = (1.0 - t) ** 2, 2.0 * (1.0 - t) * t
    c = t * t
    return (a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1])


def loop_centerline(P: dict, n: int = 64):
    """(x, z) in px of the right loop's centre line (a flat arc), scaled about its top end by `loop_scale`."""
    s = P.get("loop_scale", 1.0)
    p0, p1, p2 = LOOP_ARC
    out = []
    for k in range(n + 1):
        x, z = _bezier2(p0, p1, p2, k / float(n))
        out.append((p0[0] + (x - p0[0]) * s, p0[1] + (z - p0[1]) * s))
    return out


def loop_cells(P: dict):
    """Pixel cells (i, j) of the right loop: every cell whose centre is within `loop_width / 2` px of the centre line, plus the cells the
    line crosses (a band thinner than two cells would otherwise break at the stair steps, and cells that touch only at a corner would
    be separate floating plates): the result is one 4-connected piece."""
    half = P.get("loop_width", 1.3) / 2.0
    line = loop_centerline(P)
    xs, zs = [p[0] for p in line], [p[1] for p in line]
    cells = set()
    for i in range(int(min(xs) - half) - 1, int(max(xs) + half) + 2):
        for j in range(int(min(zs) - half) - 1, int(max(zs) + half) + 2):
            if min((i + 0.5 - px) ** 2 + (j + 0.5 - pz) ** 2 for px, pz in line) <= half * half + 1e-9:
                cells.add((i, j))
    prev = None
    for px, pz in line:
        cur = (int(px // 1), int(pz // 1))
        if prev is not None and prev[0] != cur[0] and prev[1] != cur[1]:   # diagonal step: add the corner cell the line passes nearer
            a, b = (cur[0], prev[1]), (prev[0], cur[1])
            cells.add(min((a, b), key=lambda c: (c[0] + 0.5 - px) ** 2 + (c[1] + 0.5 - pz) ** 2))
        cells.add(cur)
        prev = cur
    return cells


def _loop(m: K.Model, P: dict) -> None:
    w = K.rule_blend(2, Z(-0.5), Z(-4.0), "Head", "Chest")
    K.cell_plate(m, loop_cells(P), "ribbon", w, origin=(0.0, Y(LOOP_Y), Z0), es=(U, 0.0, 0.0), ez=(0.0, 0.0, U), thickness=0.5 * U,
                 density=P.get("loop_density", 1.0))


def add(m: K.Model, P: dict) -> None:
    dens = P.get("hair_density", 1.0)
    _hair_centre(m, dens)
    for fn in (lambda: _hair_side(m, dens), lambda: _laurel_side(m, dens), lambda: _loop(m, P)):
        mk = m.mark()
        fn()
        m.mirror(mk)
