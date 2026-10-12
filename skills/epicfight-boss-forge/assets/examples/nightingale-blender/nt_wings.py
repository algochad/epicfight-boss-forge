"""Nightingale wings: two big stair-stepped angel wings made of layered feather cards (the character's RIGHT wing first, then mirrored).

Every measurement is a sheet pixel of the FRONT orthographic view (`sheet_ortho_front_back.png`; the character's right wing is the
image-left one) converted through `REF`, so the numbers can be checked against the sheet. Two layouts: `WING_R` was hill-climbed in 2D against the
front sheet's right wing, `WING_L` against the front sheet's left wing (mirrored into the same frame; the sheet's left wing is ~9 px
wider) with the back-sheet left wing as a second target at weight 0.3 (IoU_front + 0.3 x IoU_back, normalised): the front view is the
primary view, the back view is kept near 0.84. The left wing is built from `WING_L` (x negated); `wing_symmetric=True` builds `WING_R`
and mirrors it instead. Rows, front to back (the sheet shows every
row lying over the one below it, like roof tiles); the colour fades white -> lavender -> violet with the group:

    A  wing_a  white leading-edge slab: the wrist cap (2 blocks) and 5 fanned strips whose tips run along the white / lavender diagonal
    B  wing_b  white inner coverts hanging from the humerus line (stair-stepped outline up to the shoulder) + 1 hidden root covert
    C  wing_c  lavender secondaries, tips y 220-285
    D  wing_d  the long lavender-violet primaries, tips stepping up from the outer tip (x 20, y 383) to the sleeve (x 117, y 283)

Depth: the front layer sits just behind the robe back (y -0.080; its front face is 0.006 clear of the pauldron back, more of the torso /
sleeves). Roof tiles: every path point of a feather lies `LAYER_STEP` further back for each feather of the rows above that covers it in the
front view (roots buried deep, free tips in front: the wing stays thin where it is not layered), overlapping neighbours of a row alternate
by `PHASE_STEP` (no coplanar faces anywhere). The whole wing slides back a little with the lateral distance (`wing_back`), hangs back
below hip height (`wing_sweep`: the sheet's side view has ribbons / hair behind the back down there) and each feather arches back by
`wing_curl`.

Parameters (all optional, `P.get`): wing_spread (lateral scale about the spine, 1.0 = sheet), wing_curl (depth arch of every feather),
wing_split (strips per cover feather, 2; 1 = plain), wing_density (UV density multiplier), wing_weight ('chest' | 'shoulder': Chest ->
Arm blend over the first 0.1 block of the wing root, share `wing_arm_weight`), wing_back (rearward slide per block of lateral distance),
wing_sweep (rearward hang of the lower wing), wing_shift (whole wing outward, sheet px), wing_segments (path points per feather),
wings (False = no wings).
"""
from __future__ import annotations

import copy
import math

from nt_common import REF as R, K

# --------------------------------------------------------------------------------------------------------- layout (sheet px)
# One feather: (root (x, y), tip (x, y), width, bow): the tip is the centre of the flat bottom edge, `bow` pushes the middle of the
# centre line sideways in px (negative = towards the outer edge, image-left), width is measured across the feather.
def _tables(a, b, c, roots, cap, d):
    return {"A": a, "B": b, "C": c, "ROOTS": roots, "CAP": cap, "D": d}


WING_R = _tables(
    # A: leading-edge slab strips [root x, root y, tip x, tip y, width, bow]; left outline (94,76) -> (22,219), tips on the white / lavender diagonal
    [[99.5, 86.5, 36.5, 224, 19, -9], [111, 88, 41.5, 220, 22, -9], [111, 88, 62, 190, 17, 4], [126, 88, 80, 176, 20.5, 2.5], [126, 93, 96, 164, 16, -3]],
    # B: white inner coverts, same fields; tips (104,188) (116,204) (128,212) (141,226) (152,224), right outline (135,120) -> (143,138)
    [[114.5, 98, 103, 182, 16, -6], [122.5, 108, 116, 201, 20, 0], [126.5, 118, 131, 207, 16, 8], [134, 131.5, 136, 227, 16, 6.5], [138, 144, 146, 221, 16, 5], [146, 154, 161, 224, 16, 0]],
    # C: lavender secondaries [tip x, tip y, width, lean (deg, tip towards the outer side), root y]
    [[51, 248, 16, 11, 185], [66, 281, 20, 9, 175], [81, 273, 17, 9, 170], [101, 246, 19, 7, 165], [116, 228, 14, 6, 165], [130, 220, 14, 5, 165]],
    # ROOTS: inner root covert behind the pauldron / sleeve, same fields as B (the wing stops at x ~0.2 so the back ribbons, x 0.17-0.19, run free)
    [[156, 158, 164, 232, 16, 0]],
    # CAP: wrist cap blocks (x0, y0, x1, y1): top ridge y 75-77 over x 100-124, right shoulder y 80-90
    [[95, 76, 127, 90], [122, 81, 135, 92]],
    # D: long primaries, same fields as C
    [[16.5, 321, 12.5, 13.5, 215], [20.5, 382, 19.5, 8, 216], [34.5, 373.5, 8, 6, 225], [46.5, 362, 19.5, 7.5, 205], [53, 345, 18.5, 8, 200], [66.5, 339, 22, 16, 185], [79, 331, 9, 6, 175], [94.5, 315, 22, 7.5, 170], [108, 300, 9, 8.5, 180], [117, 283, 17, 5, 185], [130, 282, 14, 5, 190]])
WING_L = _tables(   # character-left wing, same fields as WING_R, fitted to the front sheet's left wing (mirrored into the right-wing frame)
    [[101, 83, 31.5, 218, 18, -12], [106, 84, 33.5, 217, 22, -1.5], [114, 88, 61, 190, 17, 4], [126, 88, 80, 178, 20, 2], [126, 93, 96, 164, 16, -3]],
    [[114.5, 98, 103, 182, 16, -6], [122.5, 107, 116, 201, 20, 0], [127, 117, 127, 212, 16, 6.5], [135, 131, 136, 229, 16, 3], [138, 143.5, 143, 221, 16.5, 5], [146, 150, 161, 224, 16, 0]],
    [[51, 248, 16, 11, 184], [66, 281, 20, 9, 172], [81, 273, 17, 9, 170], [101, 246, 19, 7, 165], [116, 231, 14, 6, 165], [130, 220, 14, 5, 165]],
    [[156, 158, 164, 232, 16, 0]],
    [[99, 76, 127, 90], [122, 81, 135, 91]],
    [[12, 320.5, 22, 13, 210], [17, 381.5, 22, 5, 200.5], [35, 362, 14, 6, 225], [38, 358, 18, 4.5, 205], [52, 345, 12.5, 5, 200], [61.5, 336.5, 19.5, 12, 182], [69, 324.5, 8, 8, 175], [88, 314, 22, 9.5, 170], [107.5, 281.5, 15, 8.5, 180], [117, 280, 12, 5, 185], [130, 280, 14, 5, 190]])
TOOTH = 5.0      # px: every other strip of a split feather is this much shorter (jagged cover feather ends)
TIP_STEP = 3.5   # px: the last bit of every feather runs straight down so the bottom edge is a flat horizontal step

ROWS = ("a", "b", "c", "d", "ra", "rb")                  # front layers A..D, then the rear covert layer (seen from behind)
GROUP = {"a": "wing_a", "b": "wing_b", "c": "wing_c", "d": "wing_d", "ra": "wing_a", "rb": "wing_b"}


def _bezier2(p0, p1, p2, n):
    return [((1 - t) ** 2 * p0[0] + 2 * (1 - t) * t * p1[0] + t * t * p2[0],
             (1 - t) ** 2 * p0[1] + 2 * (1 - t) * t * p1[1] + t * t * p2[1]) for t in (i / float(n) for i in range(n + 1))]


def _polyline(root, tip, bow, n):
    mid = ((root[0] + tip[0]) / 2.0 + bow, (root[1] + tip[1]) / 2.0)
    pts = _bezier2(root, (2 * mid[0] - (root[0] + tip[0]) / 2.0, mid[1]), tip, n)
    pts = pts[:-1] + [(tip[0], tip[1] - TIP_STEP), tip]
    return pts


def _strips(row, i, root, tip, w, bow, n, split):
    """One feather of the tables as `split` side-by-side strips (the covert rows have jagged, stair-stepped ends in the sheet): the
    strips share the feather's width and alternate by TOOTH px in length."""
    if split <= 1:
        return [{"row": row, "i": i, "pts": _polyline(root, tip, bow, n), "w": w}]
    dx, dy = tip[0] - root[0], tip[1] - root[1]
    ln = math.hypot(dx, dy)
    ux, uy = dx / ln, dy / ln
    nx, ny = uy, -ux                   # across the feather, towards the inner side (+x)
    out = []
    for j in range(split):
        o = (j - (split - 1) / 2.0) * w / split
        cut = TOOTH * ((i + j) % 2)
        r = (root[0] + nx * o, root[1] + ny * o)
        t = (tip[0] + nx * o - ux * cut, tip[1] + ny * o - uy * cut)
        out.append({"row": row, "i": i, "pts": _polyline(r, t, bow, n), "w": w / split})
    return out


def layout_px(P: dict, tab=None):
    """All feathers of one wing (right-wing pixel frame): dicts {row, i, pts (px polyline root -> tip), w} and the cap blocks ({cap}).
    Rows 'ra' / 'rb' are the rear covert layer: copies of the A strips / B coverts that lie behind the primaries (the sheet draws the
    white covert rows over the lavender ones from behind as well), built with fewer path points. Pure data, also used by 2D tests."""
    tab = tab or WING_R
    n = int(P.get("wing_segments", 4))
    split = int(P.get("wing_split", 2))
    out = []
    for c in tab["CAP"]:
        out.append({"row": "a", "i": -1, "pts": [((c[0] + c[2]) / 2.0, c[1]), ((c[0] + c[2]) / 2.0, c[3])], "w": c[2] - c[0], "cap": c})
    for row, items in (("a", tab["A"]), ("b", tab["B"]), ("b", tab["ROOTS"])):
        for i, (rx, ry, tx, ty, w, bow) in enumerate(items):
            out += _strips(row, i, (rx, ry), (tx, ty), w, bow, n, split)
    for row, items in (("c", tab["C"]), ("d", tab["D"])):
        for i, (tx, ty, w, lean, ry) in enumerate(items):
            root = (tx + (ty - ry) * math.tan(math.radians(lean)), ry)
            out += _strips(row, i, root, (tx, ty), w, 0.0, n, split if row == "c" else 1)
    if P.get("wing_rear", True):
        nr = int(P.get("wing_rear_segments", 3))
        for row, items in (("ra", tab["A"]), ("rb", tab["B"]), ("rb", tab["ROOTS"])):
            for i, (rx, ry, tx, ty, w, bow) in enumerate(items):
                out += _strips(row, i, (rx, ry), (tx, ty), w, bow, nr, split)
    return out


# --------------------------------------------------------------------------------------------------------- depth layering
def _hull(pts):
    pts = sorted(set((round(x, 3), round(y, 3)) for x, y in pts))
    if len(pts) < 3:
        return pts

    def half(seq):
        h = []
        for p in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (p[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (p[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(p)
        return h
    lo, up = half(pts), half(reversed(pts))
    return lo[:-1] + up[:-1]


def _outline(f):
    """Convex hull (px) of a feather's rings: used to find which feathers overlap in the front view."""
    pts, w = f["pts"], f["w"]
    ring = []
    for i, p in enumerate(pts):
        a, b = pts[max(i - 1, 0)], pts[min(i + 1, len(pts) - 1)]
        tx, ty = b[0] - a[0], b[1] - a[1]
        n = math.hypot(tx, ty) or 1.0
        nx, ny = ty / n, -tx / n
        ring += [(p[0] + nx * w / 2.0, p[1] + ny * w / 2.0), (p[0] - nx * w / 2.0, p[1] - ny * w / 2.0)]
    return _hull(ring)


def _overlap(h1, h2, depth=1.5):
    """True when two convex polygons interpenetrate by more than `depth` px (separating-axis test)."""
    for poly in (h1, h2):
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            nx, ny = b[1] - a[1], a[0] - b[0]
            n = math.hypot(nx, ny) or 1.0
            nx, ny = nx / n, ny / n
            p1 = [nx * x + ny * y for x, y in h1]
            p2 = [nx * x + ny * y for x, y in h2]
            if min(max(p1) - min(p2), max(p2) - min(p1)) <= depth:
                return False
    return True


def _inside(h, p):
    """Point in a convex counter-clockwise polygon."""
    for i in range(len(h)):
        a, b = h[i], h[(i + 1) % len(h)]
        if (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) < 0:
            return False
    return True


def assign_depth(feathers):
    """Roof-tile layering, per feather and per path point (front view). `front[k]` = how many FRONT rows (A, B, C, D) above this feather's
    row cover path point k (the point is buried under that many layers: roots lie deep under the rows above, free tips come forward); a
    rear covert feather gets (rows covering the point) + 1, i.e. it lies behind every layer there. `phase` (0, 1, 2 ...) alternates
    overlapping feathers of the same row so no two faces are ever coplanar."""
    hulls = {}
    for f in feathers:
        if "cap" in f:
            x0, y0, x1, y1 = f["cap"]
            f["hull"] = _hull([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
        else:
            f["hull"] = _outline(f)
        hulls.setdefault(f["row"], []).append(f["hull"])

    def covered_by(row, p):
        return any(_inside(h, p) for h in hulls.get(row, ()))
    done = []
    for ri, row in enumerate(ROWS):
        for f in [g for g in feathers if g["row"] == row and "cap" not in g]:
            if row in ("ra", "rb"):
                f["front"] = [1 + sum(1 for r in ROWS[:4] if covered_by(r, p)) for p in f["pts"]]
            else:
                f["front"] = [sum(1 for r in ROWS[:ri] if covered_by(r, p)) for p in f["pts"]]
            used = {g["phase"] for g in done if g["row"] == row and _overlap(f["hull"], g["hull"])}
            f["phase"] = min(k for k in range(len(used) + 1) if k not in used)
            done.append(f)
    return feathers


# --------------------------------------------------------------------------------------------------------- 3D build
Y_FRONT = -0.080          # centre of the front-most feather layer; its front face (+0.008) is 0.0058 behind the pauldron back (-0.0662), more behind the robe / sleeves
LAYER_STEP = 0.016        # a feather point buried under n front rows lies n x this behind the front layer
PHASE_STEP = 0.004        # alternate overlapping feathers of a row differ by this (never coplanar; shrunk so all phases stay < LAYER_STEP)
THICK = {"a": 0.016, "b": 0.014, "c": 0.014, "d": 0.014, "ra": 0.010, "rb": 0.010}
A_ROOT_THICK = 0.034      # the leading-edge strips are the wing's bone: thick at the cap, thinning to THICK["a"] at 2/3 of their length
CAP_DEPTH = (0.011, -0.020)    # cap blocks: front / back face relative to the front layer (the strips' front face is at +0.008)
WING_ROOT_PX = (152, 160)      # where the wing meets the shoulder blade (front view px): the skinning root of the `shoulder` mode
SHIFT_PX = 0.0           # whole wing outward by this many sheet px
X_SPINE = 0.16            # wing_spread scales x about the spine line (behind the sleeve edge)
Z_HANG = (0.85, 0.45)     # below z 0.85 the wing hangs back (full at z 0.45): the sheet's side view has ribbons behind the back there


def _weights(P: dict, side: int):
    """Chest only, or Chest -> Arm blended over the first 0.1 block around the wing root (raised arms drag the root slightly):
    the arm share is `wing_arm_weight` (0.5) at the root point and fades to nothing 0.1 block away (nearest feathers: the hidden
    root covert and the inner white coverts, behind the pauldron). side +1 = right wing (Arm_R), -1 = left wing (Arm_L)."""
    if str(P.get("wing_weight", "chest")).lower() != "shoulder":
        return "Chest"
    arm_w = float(P.get("wing_arm_weight", 0.5))
    arm = "Arm_R" if side > 0 else "Arm_L"
    rx, ry, rz = side * R.x(WING_ROOT_PX[0]), Y_FRONT, R.z(WING_ROOT_PX[1])

    def rule(p):
        d = math.sqrt((p[0] - rx) ** 2 + (p[1] - ry) ** 2 + (p[2] - rz) ** 2)
        a = arm_w * (1.0 - K.smoothstep(0.0, 0.10, d))
        return {"Chest": 1.0 - a, arm: a}
    return rule


def _build_wing(m: K.Model, P: dict, tab: dict, side: int) -> None:
    """One wing from the tables `tab`; side +1 = character's right (+x), -1 = left (x negated, joints _L)."""
    spread = float(P.get("wing_spread", 1.0))
    curl = float(P.get("wing_curl", 1.0))
    dens = float(P.get("wing_density", 1.0))
    back = float(P.get("wing_back", 0.02))       # rearward slide per block of lateral distance from the spine
    sweep = float(P.get("wing_sweep", 0.09))     # extra rearward hang of the lower wing
    shift = float(P.get("wing_shift", SHIFT_PX))

    def base_y(x, z):
        return Y_FRONT - back * max(abs(x) - X_SPINE, 0.0) - sweep * K.smoothstep(Z_HANG[0], Z_HANG[1], z)

    def px_x(px):
        return side * (X_SPINE + (R.x(px - shift) - X_SPINE) * spread)

    weights = _weights(P, side)
    feathers = assign_depth(layout_px(P, tab))
    pstep = min(PHASE_STEP, 0.75 * LAYER_STEP / max(1, max(f["phase"] for f in feathers if "phase" in f)))   # phases never reach a full layer
    for f in feathers:
        group = GROUP[f["row"]]
        if "cap" in f:
            x0, y0, x1, y1 = f["cap"]
            xa, xb = sorted((px_x(x1), px_x(x0)))
            yc = base_y((xa + xb) / 2.0, R.z((y0 + y1) / 2.0))
            K.block(m, (xa, yc + CAP_DEPTH[1], R.z(y1)), (xb, yc + CAP_DEPTH[0], R.z(y0)), group, weights, density=dens)
            continue
        npt = len(f["pts"])
        path = []
        for k, (px, py) in enumerate(f["pts"]):
            x, z = px_x(px), R.z(py)
            arch = -0.012 * curl * math.sin(math.pi * k / float(npt - 1))
            path.append((x, base_y(x, z) - f["front"][k] * LAYER_STEP - f["phase"] * pstep + arch, z))
        w = f["w"] * R.k
        thick = [THICK[f["row"]]] * npt
        if f["row"] == "a":     # thicker towards the cap; the front face stays planar (the extra thickness grows backwards)
            for k in range(npt):
                t = THICK["a"] + (A_ROOT_THICK - THICK["a"]) * (1.0 - K.smoothstep(0.0, 0.66, k / float(npt - 1)))
                thick[k] = t
                path[k] = (path[k][0], path[k][1] - (t - THICK["a"]) / 2.0, path[k][2])
        K.sweep(m, path, [w * 0.9] + [w] * (npt - 1), thick, group, weights, side=(1.0, 0.0, 0.0), density=dens)


def add(m: K.Model, P: dict) -> None:
    if not P.get("wings", True):
        return
    mk = m.mark()
    _build_wing(m, P, WING_R, 1)
    if P.get("wing_symmetric", False):
        m.mirror(mk)                      # exact mirror of the right wing (the compromise between the sheet's two wings)
    else:
        _build_wing(m, P, WING_L, -1)     # the character-left wing, fitted to its own (wider) outline
