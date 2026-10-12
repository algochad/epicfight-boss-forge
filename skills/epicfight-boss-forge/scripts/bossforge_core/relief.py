"""Relief parts: boxes generated from reference silhouettes (preset `relief`, see references/shape-forge.md).

A boss is boxes on the EF biped rig; what boxes and presets cannot express (stair-stepped wings, hair masses, hems,
ornaments) is cut out of the reference views instead:

    extrude  one view + a pixel `rect`: the mask is sampled on a grid of `cell` model blocks in the view plane, a cell
             is foreground when at least half of it is, and the foreground cells are merged into rectangles by greedy
             meshing (row-first and column-first, the smaller result wins). With `align` (default) the grid is
             shifted by 0, 1/4, 1/2 or 3/4 of a cell in u and v and the shift with the highest IoU that fits
             `max_boxes` wins (stair-stepped art differs by several IoU points between shifts at the same box
             count); without it the grid lines are multiples of `cell` (y = 0 and x = 0 / z = 0 are grid lines).
             One slab box per rectangle: `thickness` along the view axis, centred on `z`. `mirror` meshes only the
             half on the character's right (+x) and mirrors it.
    hull     `views` = {"front": rect, "right": rect}: per horizontal band of `cell` blocks the front mask gives x
             intervals and the side mask z intervals; their products are the boxes (two-view visual hull), bands with
             identical intervals are merged vertically.

Everything is measured: `build` reports (through `notes`) the cell actually used, the box count and the IoU between
the planar projection of the generated boxes and the reference mask inside the rect(s), at image resolution.
Coordinates: model space of the biped bind pose (y up, feet at 0, character faces -z, its right is +x); the View
class (`reference.py`) owns the projection. Box limits are those of `shape` (|x| <= 4, y in [-1, 6], |z| <= 4).
"""
from __future__ import annotations

import copy
import json
import math
import weakref
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .spec import BossForgeError, Err

MODES = ("extrude", "hull")
FRONT_VIEWS = ("front", "back")  # in-plane u = x, depth axis z
SIDE_VIEWS = ("right", "left")  # in-plane u = z, depth axis x
VIEW_NAMES = FRONT_VIEWS + SIDE_VIEWS
COARSEN_STEP = 1.05
COARSEN_TRIES = 90
MIN_CELL_PX = 2.0  # a cell smaller than this many reference pixels is noise
SLAB_AXIS = {"front": "z", "back": "z", "right": "x", "left": "x"}
# grid phases tried by `align` (fractions of a cell by which the grid is shifted in u and v), phase 0 first
PHASES = tuple((fu, fv) for fv in (0.0, 0.25, 0.5, 0.75) for fu in (0.0, 0.25, 0.5, 0.75))
MIN_BOX_SIZE = 0.01  # shape.MIN_SIZE: a clipped sliver at the rect edge must not fail the box validation
PHASE_SLACK = 1.3  # box counts of the phases of one cell differ by about +-15 %
Rect = Tuple[float, float, float, float]
Cells = Tuple[int, int, int, int]  # (c0, c1, r0, r1), inclusive column / row indices of a grid


def _r5(v: float) -> float:
    return round(float(v), 5) + 0.0


def _warn(notes: Optional[dict], code: str, message: str) -> None:
    if notes is not None:
        notes.setdefault("warnings", []).append(Err("", code, message))


# --------------------------------------------------------------------------- views and rects
def need_view(views: Optional[Dict[str, Any]], name: str, path: str) -> Any:
    if not views:
        raise BossForgeError(path, "relief_no_reference",
                             "the relief part needs the spec's `reference` block (views with image and calibration); none is loaded")
    if name not in views:
        raise BossForgeError(path, "relief_unknown_view",
                             "view %r is not in the reference (defined: %s)" % (name, ", ".join(sorted(views)) or "none"))
    return views[name]


def check_rect(view: Any, rect: Any, path: str) -> Rect:
    """The pixel rect (x0, y0, x1, y1) as floats; it must lie inside the full image."""
    try:
        x0, y0, x1, y1 = (float(c) for c in rect)
    except (TypeError, ValueError):
        raise BossForgeError(path, "relief_bad_rect", "rect must be [x0, y0, x1, y1] in image pixels, got %r" % (rect,))
    if not (x0 < x1 and y0 < y1):
        raise BossForgeError(path, "relief_bad_rect", "rect %s is empty: need x0 < x1 and y0 < y1" % ([x0, y0, x1, y1],))
    if x0 < 0 or y0 < 0 or x1 > view.width or y1 > view.height:
        raise BossForgeError(path, "relief_bad_rect",
                             "rect %s reaches outside the %dx%d image of view %r" % ([x0, y0, x1, y1], view.width, view.height, view.name))
    return x0, y0, x1, y1


def plane_range(view: Any, rect: Rect) -> Tuple[float, float, float, float]:
    """(u_lo, u_hi, v_lo, v_hi): the rect in the view plane (u across = x for front/back and z for right/left, v = y)."""
    ua, va = view.unproject(rect[0], rect[1])
    ub, vb = view.unproject(rect[2], rect[3])
    return min(ua, ub), max(ua, ub), min(va, vb), max(va, vb)


def plane_px(view: Any, u: float, v: float) -> Tuple[float, float]:
    """Image position of the in-plane point (u, v)."""
    if view.name in SIDE_VIEWS:
        px, py, _ = view.project(0.0, v, u)
    else:
        px, py, _ = view.project(u, v, 0.0)
    return px, py


def _footprint(view: Any, u0: float, u1: float, v0: float, v1: float) -> Tuple[float, float, float, float]:
    pa, qa = plane_px(view, u0, v1)
    pb, qb = plane_px(view, u1, v0)
    return min(pa, pb), min(qa, qb), max(pa, pb), max(qa, qb)


def _hex(view: Any, u0: float, u1: float, v0: float, v1: float, fallback: str) -> str:
    col = view.average(*_footprint(view, u0, u1, v0, v1))
    if col is None:
        return fallback
    return "#%02x%02x%02x" % col


def _check_cell(view: Any, cell: float, path: str) -> None:
    if cell * view.px_per_block < MIN_CELL_PX:
        raise BossForgeError(path, "relief_cell_too_small",
                             "cell %s is %.2f px in view %r (%.1f px per block); use a cell of at least %.3f"
                             % (cell, cell * view.px_per_block, view.name, view.px_per_block, MIN_CELL_PX / view.px_per_block))


# --------------------------------------------------------------------------- sampling and meshing
def _span(lo: float, hi: float, cell: float, off: float = 0.0) -> Tuple[int, int]:
    """Index range of the grid lines at off + k*cell that covers [lo, hi]."""
    return int(math.floor((lo - off) / cell + 1e-9)), int(math.ceil((hi - off) / cell - 1e-9))


def sample_band(view: Any, cell: float, k0: int, k1: int, j: int, bounds: Rect, ou: float = 0.0, ov: float = 0.0) -> bytearray:
    """One horizontal band j (v from ov + j*cell to ov + (j+1)*cell) of the cell grid (columns at ou + k*cell): 1 where
    at least half of the cell is foreground; only the part of a cell inside `bounds` (u_lo, u_hi, v_lo, v_hi) counts."""
    u_lo, u_hi, v_lo, v_hi = bounds
    row = bytearray(max(0, k1 - k0))
    va, vb = max(ov + j * cell, v_lo), min(ov + (j + 1) * cell, v_hi)
    if vb <= va + 1e-12:
        return row
    area = cell * cell
    hv = vb - va
    for k in range(k0, k1):
        ua, ub = max(ou + k * cell, u_lo), min(ou + (k + 1) * cell, u_hi)
        if ub <= ua + 1e-12:
            continue
        ratio = (ub - ua) * hv / area
        if ratio < 0.5 - 1e-12:
            continue
        if view.mask_cells(ua, va, ub, vb) * ratio >= 0.5 - 1e-12:
            row[k - k0] = 1
    return row


def _greedy(rows: List[bytearray]) -> List[Cells]:
    """Greedy meshing, row first: every rectangle is as wide as possible at its first cell, then grows downwards
    while the whole span below is free foreground. Disjoint cover of the foreground cells."""
    if not rows:
        return []
    R, C = len(rows), len(rows[0])
    avail = [bytearray(r) for r in rows]
    out: List[Cells] = []
    for r in range(R):
        row = avail[r]
        c = 0
        while c < C:
            if not row[c]:
                nxt = row.find(1, c)
                if nxt < 0:
                    break
                c = nxt
            end = row.find(0, c)
            c2 = (C if end < 0 else end) - 1
            n = c2 - c + 1
            ones = b"\x01" * n
            r2 = r
            while r2 + 1 < R and avail[r2 + 1][c:c2 + 1] == ones:
                r2 += 1
            zeros = bytes(n)
            for rr in range(r, r2 + 1):
                avail[rr][c:c2 + 1] = zeros
            out.append((c, c2, r, r2))
            c = c2 + 1
    return out


def mesh_rects(rows: Sequence[Sequence[int]]) -> List[Cells]:
    """Rectangles (c0, c1, r0, r1; inclusive) that tile exactly the foreground cells of `rows` (a list of equally long
    0/1 rows, top first). Tries row-first and column-first greedy meshing and keeps the smaller result (ties: rows)."""
    grid = [bytearray(r) for r in rows]
    if not grid or not grid[0]:
        return []
    a = _greedy(grid)
    cols = [bytearray(col) for col in zip(*grid)]
    b = [(r0, r1, c0, c1) for c0, c1, r0, r1 in _greedy(cols)]
    best = b if len(b) < len(a) else a
    return sorted(best, key=lambda t: (t[2], t[0], t[3], t[1]))


def _runs(row: Sequence[int]) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    start = -1
    for i, v in enumerate(row):
        if v and start < 0:
            start = i
        elif not v and start >= 0:
            out.append((start, i - 1))
            start = -1
    if start >= 0:
        out.append((start, len(row) - 1))
    return out


# --------------------------------------------------------------------------- measuring
def silhouette_stats(view: Any, rect: Rect, plane_rects: Sequence[Tuple[float, float, float, float]]) -> Dict[str, Any]:
    """IoU between the union of the in-plane rectangles (u0, u1, v0, v1) rasterised at image resolution and the view
    mask inside the pixel `rect`. Rects are rasterised on the pixel grid (a pixel belongs to a rect when its centre does)."""
    X0, Y0 = int(round(rect[0])), int(round(rect[1]))
    X1, Y1 = int(round(rect[2])), int(round(rect[3]))
    W, H = max(0, X1 - X0), max(0, Y1 - Y0)
    rows = [bytearray(W) for _ in range(H)]
    for u0, u1, v0, v1 in plane_rects:
        fx0, fy0, fx1, fy1 = _footprint(view, u0, u1, v0, v1)
        xa, xb = max(X0, int(math.floor(fx0 + 0.5))), min(X1, int(math.floor(fx1 + 0.5)))
        ya, yb = max(Y0, int(math.floor(fy0 + 0.5))), min(Y1, int(math.floor(fy1 + 0.5)))
        if xb <= xa or yb <= ya:
            continue
        seg = b"\x01" * (xb - xa)
        for y in range(ya, yb):
            rows[y - Y0][xa - X0:xb - X0] = seg
    t01 = bytes([0] + [1] * 255)
    ref = model = inter = 0
    for y in range(H):
        m = bytes(rows[y])
        g = bytes(view.mask[(Y0 + y) * view.width + X0:(Y0 + y) * view.width + X1]).translate(t01)
        model += m.count(1)
        ref += g.count(1)
        inter += bin(int.from_bytes(m, "big") & int.from_bytes(g, "big")).count("1")
    union = ref + model - inter
    return {"iou": round(inter / union, 4) if union else 1.0, "ref_px": ref, "model_px": model, "missing_px": ref - inter,
            "extra_px": model - inter}


# --------------------------------------------------------------------------- boxes
class _Maker:
    """Builds the box dicts of one relief part (common keys, rotation, bone, style)."""

    def __init__(self, p: dict, style: Tuple[str, str, str], mode: str):
        self.bone = p["bone"]
        self.material, self.color, self.accent = style
        self.rotate = [float(a) for a in (p.get("rotate") or [0.0, 0.0, 0.0])]
        self.rotated = any(abs(a) > 1e-9 for a in self.rotate)
        self.pivot = p.get("pivot")
        self.ref_color = bool(p["ref_color"])
        self.edge = int(p["edge_texels"])
        self.slab = mode == "extrude" and self.edge > 0
        self.mirror = bool(p["mirror"])
        # a box that spans the centre line may be ONE centred box, unless the bone is a sided one or rotated
        self.can_merge = (self.mirror and not self.rotated and not (self.bone.endswith("_r") or self.bone.endswith("_l")))
        self.boxes: List[dict] = []

    def add(self, lid: str, mn: Sequence[float], sz: Sequence[float], color_view: Any, foot: Tuple[float, float, float, float],
            mirror_flag: bool, skip: Sequence[str], slab_axis: Optional[str]) -> None:
        b: Dict[str, Any] = {"id": lid, "bone": self.bone, "min": [_r5(c) for c in mn], "size": [max(_r5(c), MIN_BOX_SIZE) for c in sz]}
        if self.rotated:
            b["rotate"] = list(self.rotate)
        if mirror_flag:
            b["mirror"] = True
        col = self.color
        if self.ref_color and color_view is not None:
            col = _hex(color_view, foot[0], foot[1], foot[2], foot[3], self.color)
        b["material"], b["color"], b["accent"] = self.material, col, self.accent
        if skip:
            b["skip_faces"] = list(skip)
        if self.slab and slab_axis:
            b["slab"] = slab_axis
            b["edge_texels"] = self.edge
        self.boxes.append(b)

    def finish_pivot(self) -> None:
        """Rotation about ONE pivot for the whole part: the given pivot or the centre of the part's bounding box."""
        if not self.rotated:
            return
        if self.pivot is not None:
            pv = [float(c) for c in self.pivot]
        else:
            lo = [min(b["min"][i] for b in self.boxes) for i in range(3)]
            hi = [max(b["min"][i] + b["size"][i] for b in self.boxes) for i in range(3)]
            pv = [_r5((lo[i] + hi[i]) / 2.0) for i in range(3)]
        for b in self.boxes:
            b["pivot"] = list(pv)


def _check_limits(boxes: Sequence[dict], rotated: bool, path: str, mirror: bool) -> None:
    """The model limits of shape.py for axis-aligned parts (rotated parts get shape's own out_of_bounds check)."""
    if rotated or not boxes:
        return
    from . import shape
    lo = [min(b["min"][i] for b in boxes) for i in range(3)]
    hi = [max(b["min"][i] + b["size"][i] for b in boxes) for i in range(3)]
    if mirror:
        lo[0], hi[0] = min(lo[0], -hi[0]), max(hi[0], -lo[0])
    bad = []
    if max(abs(lo[0]), abs(hi[0])) > shape.LIMIT_X + 1e-9:
        bad.append("x %.2f..%.2f (limit |x| <= %s)" % (lo[0], hi[0], shape.LIMIT_X))
    if lo[1] < shape.LIMIT_Y[0] - 1e-9 or hi[1] > shape.LIMIT_Y[1] + 1e-9:
        bad.append("y %.2f..%.2f (limit %s..%s)" % (lo[1], hi[1], shape.LIMIT_Y[0], shape.LIMIT_Y[1]))
    if max(abs(lo[2]), abs(hi[2])) > shape.LIMIT_Z + 1e-9:
        bad.append("z %.2f..%.2f (limit |z| <= %s)" % (lo[2], hi[2], shape.LIMIT_Z))
    if bad:
        raise BossForgeError(path, "relief_out_of_bounds",
                             "the relief part leaves the model limits: %s; check px_per_block / center_x / ground_y of the view or shrink `rect`"
                             % "; ".join(bad))


def _fit(p: dict, attempt, notes: Optional[dict], what: str):
    """Run `attempt(cell) -> (state, box_count)` with growing cells until the count fits `max_boxes`; coarser cells
    that lose the whole shape do not count as a fit."""
    cell0 = float(p["cell"])
    limit = int(p["max_boxes"])
    cell = cell0
    first = None
    count = 0
    for _ in range(COARSEN_TRIES):
        state, count = attempt(cell)
        if first is None:
            first = count
        if count <= limit and (count > 0 or first == 0):
            if cell > cell0 * 1.0000001:
                _warn(notes, "relief_coarsened",
                      "%s: cell raised from %g to %g blocks (%d boxes) to fit max_boxes %d" % (what, cell0, round(cell, 4), count, limit))
            return state, cell, count
        if count == 0:
            break
        cell *= COARSEN_STEP
    raise BossForgeError("/max_boxes", "relief_max_boxes",
                         "%s: %d boxes are needed at cell %g and coarser cells lose the shape; raise max_boxes, raise cell or shrink the rect"
                         % (what, first or 0, cell0))


# --------------------------------------------------------------------------- extrude
def _extrude(p: dict, views, style, att_id: str, notes) -> List[dict]:
    name = p["view"]
    view = need_view(views, name, "/view")
    if p["rect"] is None:
        raise BossForgeError("/rect", "relief_bad_rect", "mode extrude needs `rect` [x0, y0, x1, y1] in image pixels of view %r" % name)
    rect = check_rect(view, p["rect"], "/rect")
    mirror = bool(p["mirror"])
    if mirror and name in SIDE_VIEWS:
        raise BossForgeError("/mirror", "relief_bad_mirror",
                             "mirror splits a front/back view at x = 0; for the %s view use two attachments (x = +z and -z) or mirror false" % name)
    _check_cell(view, float(p["cell"]), "/cell")
    u_lo, u_hi, v_lo, v_hi = plane_range(view, rect)
    if mirror:
        u_lo = max(u_lo, 0.0)
        if u_hi <= u_lo + 1e-9:
            raise BossForgeError("/rect", "relief_bad_rect",
                                 "mirror builds the character's right half (x >= 0) but rect covers x %.3f..%.3f" % (plane_range(view, rect)[0], u_hi))
    bounds = (u_lo, u_hi, v_lo, v_hi)
    layers = int(p["layers"])
    mk = _Maker(p, style, "extrude")

    limit = int(p["max_boxes"])
    phases = PHASES if p["align"] else ((0.0, 0.0),)

    def plane_rects(rects, k0, j1, cell, ou, ov):
        """(u0, u1, v0, v1) of every rectangle (the mirrored copies and centre-merged boxes included in `full`)."""
        out = []
        for (c0, c1, r0, r1) in rects:
            out.append((max(ou + (k0 + c0) * cell, u_lo), min(ou + (k0 + c1 + 1) * cell, u_hi),
                        max(ov + (j1 - 1 - r1) * cell, v_lo), min(ov + (j1 - r0) * cell, v_hi)))
        return out

    def silhouette(rects, k0, j1, cell, ou, ov):
        full = []
        for (u0, u1, v0, v1) in plane_rects(rects, k0, j1, cell, ou, ov):
            if mirror and mk.can_merge and abs(u0) < 1e-9:
                full.append((-u1, u1, v0, v1))
            else:
                full.append((u0, u1, v0, v1))
                if mirror:
                    full.append((-u1, -u0, v0, v1))
        return full

    def attempt(cell: float):
        """The best grid phase at this cell: highest IoU among the phases that fit `max_boxes` (the fewest boxes of
        them on a tie); when none fits, the phase with the fewest boxes (the caller coarsens)."""
        best = None
        fewest = None
        for pi, (fu, fv) in enumerate(phases):
            ou, ov = fu * cell, fv * cell
            k0, k1 = _span(u_lo, u_hi, cell, ou)
            j0, j1 = _span(v_lo, v_hi, cell, ov)
            rows = [sample_band(view, cell, k0, k1, j, bounds, ou, ov) for j in range(j1 - 1, j0 - 1, -1)]
            rects = mesh_rects(rows)
            plane = plane_rects(rects, k0, j1, cell, ou, ov)
            merged = sum(1 for u0, _u1, _v0, _v1 in plane if mk.can_merge and abs(u0) < 1e-9)
            n = (len(rects) * (2 if mirror else 1) - merged) * layers
            state = (k0, j1, rows, rects, ou, ov, (fu, fv))
            if fewest is None or (n > 0 and (fewest[1] == 0 or n < fewest[1])):
                fewest = (state, n)
            if pi == 0 and n > limit * PHASE_SLACK:  # far over budget: no other phase gets close, coarsen first
                break
            if n > limit or n == 0:
                continue
            iou = silhouette_stats(view, rect, silhouette(rects, k0, j1, cell, ou, ov))["iou"] if len(phases) > 1 else 0.0
            if best is None or iou > best[0] + 1e-9 or (abs(iou - best[0]) <= 1e-9 and n < best[2]):
                best = (iou, state, n)
        if best is not None:
            return best[1], best[2]
        return fewest

    (k0, j1, rows, rects, ou, ov, phase), cell, count = _fit(p, attempt, notes, "relief %s" % att_id)
    if not rects:
        raise BossForgeError("/rect", "relief_empty",
                             "no foreground cell in rect %s of view %r at cell %g: check the mask (reference-info) and the calibration"
                             % ([round(c, 1) for c in rect], name, cell))
    step = float(p["layer_step"]) or float(p["thickness"])
    t = float(p["thickness"])
    R, C = len(rows), len(rows[0])
    plane = []  # type: List[Tuple[float, float, float, float]]
    idx = 0
    lo_face, hi_face = ("left", "right") if name in FRONT_VIEWS else ("front", "back")
    for (c0, c1, r0, r1) in rects:
        u0 = max(ou + (k0 + c0) * cell, u_lo)
        u1 = min(ou + (k0 + c1 + 1) * cell, u_hi)
        v1 = min(ov + (j1 - r0) * cell, v_hi)
        v0 = max(ov + (j1 - 1 - r1) * cell, v_lo)
        ones = b"\x01" * (c1 - c0 + 1)
        skip = []
        lo_cov = c0 > 0 and all(rows[r][c0 - 1] for r in range(r0, r1 + 1))
        hi_cov = c1 < C - 1 and all(rows[r][c1 + 1] for r in range(r0, r1 + 1))
        if r0 > 0 and rows[r0 - 1][c0:c1 + 1] == ones:
            skip.append("top")
        if r1 < R - 1 and rows[r1 + 1][c0:c1 + 1] == ones:
            skip.append("bottom")
        touch = mirror and abs(u0) < 1e-9
        merged = touch and mk.can_merge
        if touch:
            lo_cov = True  # the mirrored copy continues across x = 0
        if lo_cov:
            skip.append(lo_face)
        if hi_cov:
            skip.append(hi_face)
        if merged:
            skip = [f for f in skip if f not in (lo_face, hi_face)] + ([lo_face, hi_face] if hi_cov else [])
            bu0, bu1 = -u1, u1
        else:
            bu0, bu1 = u0, u1
        for layer in range(layers):
            d = float(p["z"]) + layer * step
            lid = "%ss%d%s" % ("l%d" % layer if layers > 1 else "", idx, "_c" if merged else ("_r" if mirror else ""))
            if name in FRONT_VIEWS:
                mn, sz = (bu0, v0, d - t / 2.0), (bu1 - bu0, v1 - v0, t)
            else:
                mn, sz = (d - t / 2.0, v0, bu0), (t, v1 - v0, bu1 - bu0)
            mk.add(lid, mn, sz, view, (u0, u1, v0, v1), mirror and not merged, skip, SLAB_AXIS[name])
        if merged:
            plane.append((-u1, u1, v0, v1))
        else:
            plane.append((u0, u1, v0, v1))
            if mirror:
                plane.append((-u1, -u0, v0, v1))
        idx += 1
    mk.finish_pivot()
    _check_limits(mk.boxes, mk.rotated, "/rect", mirror)
    stats = silhouette_stats(view, rect, plane)
    if notes is not None:
        info = {"id": att_id, "mode": "extrude", "view": name, "cell_requested": float(p["cell"]), "cell": round(cell, 4),
                "phase": list(phase), "rects": len(rects), "boxes": count}
        info.update(stats)
        notes.setdefault("relief", []).append(info)
    return mk.boxes


# --------------------------------------------------------------------------- hull
def _hull(p: dict, views, style, att_id: str, notes) -> List[dict]:
    vd = p["views"]
    if not isinstance(vd, dict) or len(vd) != 2:
        raise BossForgeError("/views", "relief_bad_views",
                             "mode hull needs views {\"front\": rect, \"right\": rect}: exactly one of front/back and one of right/left")
    fkeys = [k for k in vd if k in FRONT_VIEWS]
    skeys = [k for k in vd if k in SIDE_VIEWS]
    if len(fkeys) != 1 or len(skeys) != 1:
        raise BossForgeError("/views", "relief_bad_views",
                             "mode hull needs exactly one of front/back and one of right/left, got %s" % ", ".join(map(str, vd)))
    fname, sname = fkeys[0], skeys[0]
    fv = need_view(views, fname, "/views/" + fname)
    sv = need_view(views, sname, "/views/" + sname)
    frect = check_rect(fv, vd[fname], "/views/" + fname)
    srect = check_rect(sv, vd[sname], "/views/" + sname)
    mirror = bool(p["mirror"])
    _check_cell(fv, float(p["cell"]), "/cell")
    _check_cell(sv, float(p["cell"]), "/cell")
    x_lo, x_hi, fv_lo, fv_hi = plane_range(fv, frect)
    z_lo, z_hi, sv_lo, sv_hi = plane_range(sv, srect)
    if mirror:
        x_lo = max(x_lo, 0.0)
        if x_hi <= x_lo + 1e-9:
            raise BossForgeError("/views/" + fname, "relief_bad_rect", "mirror builds the character's right half (x >= 0) but the rect has none")
    v_lo, v_hi = max(fv_lo, sv_lo), min(fv_hi, sv_hi)
    if v_hi - v_lo < float(p["cell"]) * 0.5:
        raise BossForgeError("/views", "relief_bad_rect",
                             "the front rect (y %.2f..%.2f) and the side rect (y %.2f..%.2f) share no height; check both rects and calibrations"
                             % (fv_lo, fv_hi, sv_lo, sv_hi))
    fb, sb = (x_lo, x_hi, v_lo, v_hi), (z_lo, z_hi, v_lo, v_hi)
    mk = _Maker(p, style, "hull")

    def attempt(cell: float):
        kx0, kx1 = _span(x_lo, x_hi, cell)
        kz0, kz1 = _span(z_lo, z_hi, cell)
        j0, j1 = _span(v_lo, v_hi, cell)
        open_: Dict[Tuple[int, int, int, int], int] = {}
        out: List[Tuple[int, int, int, int, int, int]] = []
        for j in range(j0, j1):
            xr = _runs(sample_band(fv, cell, kx0, kx1, j, fb))
            zr = _runs(sample_band(sv, cell, kz0, kz1, j, sb))
            cur = {(a, b, c, d) for a, b in xr for c, d in zr}
            for key in [k for k in open_ if k not in cur]:
                out.append(key + (open_.pop(key), j - 1))
            for key in sorted(cur):
                open_.setdefault(key, j)
        for key in sorted(open_):
            out.append(key + (open_[key], j1 - 1))
        out.sort(key=lambda t: (t[4], t[0], t[2], t[5]))
        merged = sum(1 for t in out if mk.can_merge and abs(max((kx0 + t[0]) * cell, x_lo)) < 1e-9)
        n = len(out) * (2 if mirror else 1) - merged
        return (kx0, kz0, out), n

    (kx0, kz0, boxes), cell, count = _fit(p, attempt, notes, "relief %s" % att_id)
    if not boxes:
        raise BossForgeError("/views", "relief_empty",
                             "the two silhouettes share no foreground at cell %g: check both masks and calibrations" % cell)
    fplane: List[Tuple[float, float, float, float]] = []
    splane: List[Tuple[float, float, float, float]] = []
    for idx, (c0, c1, d0, d1, j0, j1) in enumerate(boxes):
        x0 = max((kx0 + c0) * cell, x_lo)
        x1 = min((kx0 + c1 + 1) * cell, x_hi)
        z0 = max((kz0 + d0) * cell, z_lo)
        z1 = min((kz0 + d1 + 1) * cell, z_hi)
        y0 = max(j0 * cell, v_lo)
        y1 = min((j1 + 1) * cell, v_hi)
        touch = mirror and abs(x0) < 1e-9
        merged = touch and mk.can_merge
        bx0, bx1 = (-x1, x1) if merged else (x0, x1)
        mk.add("h%d%s" % (idx, "_c" if merged else ("_r" if mirror else "")), (bx0, y0, z0), (bx1 - bx0, y1 - y0, z1 - z0),
               fv, (x0, x1, y0, y1), mirror and not merged, (), None)
        if merged:
            fplane.append((-x1, x1, y0, y1))
        else:
            fplane.append((x0, x1, y0, y1))
            if mirror:
                fplane.append((-x1, -x0, y0, y1))
        splane.append((z0, z1, y0, y1))
    mk.finish_pivot()
    _check_limits(mk.boxes, mk.rotated, "/views", mirror)
    if notes is not None:
        fs = silhouette_stats(fv, frect, fplane)
        ss = silhouette_stats(sv, srect, splane)
        notes.setdefault("relief", []).append({
            "id": att_id, "mode": "hull", "views": {fname: list(vd[fname]), sname: list(vd[sname])},
            "cell_requested": float(p["cell"]), "cell": round(cell, 4), "boxes": count,
            "iou": round(min(fs["iou"], ss["iou"]), 4), "iou_" + fname: fs["iou"], "iou_" + sname: ss["iou"],
            "front": fs, "side": ss})
    return mk.boxes


# --------------------------------------------------------------------------- entry point
_CACHE: Dict[str, tuple] = {}  # key -> (weakrefs of the views, boxes, warnings, relief infos); a spec is built several times per command
_CACHE_MAX = 12


def _used_views(p: dict, views: Optional[Dict[str, Any]]) -> Optional[list]:
    names = [p["view"]] if p["mode"] != "hull" else (list(p["views"]) if isinstance(p.get("views"), dict) else [])
    if not views or not names or any(n not in views for n in names):
        return None
    return [views[n] for n in names]


def _merge_notes(notes: Optional[dict], warnings: list, infos: list) -> None:
    if notes is None:
        return
    if warnings:
        notes.setdefault("warnings", []).extend(warnings)
    if infos:
        notes.setdefault("relief", []).extend(infos)


def build(p: dict, views: Optional[Dict[str, Any]], style: Tuple[str, str, str], att_id: str,
          notes: Optional[dict] = None) -> List[dict]:
    """Box dicts (local ids; the preset layer prefixes the attachment id) of one relief attachment with the resolved
    parameters `p`. `style` = (material, color, accent) of the main role. Raises BossForgeError relief_*."""
    used = _used_views(p, views)
    key = None
    if used is not None:
        key = json.dumps([p, list(style), att_id, [(id(v), v.px_per_block, v.ground_y, v.center_x) for v in used]], sort_keys=True, default=str)
        hit = _CACHE.get(key)
        if hit is not None and all(r() is v for r, v in zip(hit[0], used)):
            _merge_notes(notes, hit[2], copy.deepcopy(hit[3]))
            return copy.deepcopy(hit[1])
    local: Dict[str, list] = {}
    boxes = _hull(p, views, style, att_id, local) if p["mode"] == "hull" else _extrude(p, views, style, att_id, local)
    _merge_notes(notes, local.get("warnings", []), local.get("relief", []))
    if key is not None:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[key] = (tuple(weakref.ref(v) for v in used), copy.deepcopy(boxes), list(local.get("warnings", [])),
                       copy.deepcopy(local.get("relief", [])))
    return boxes
