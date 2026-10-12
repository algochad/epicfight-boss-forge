"""Fit a boss `shape` to its reference views: rasterise the generated boxes in each view's projection, compare with the
view mask (IoU), write colour-coded overlays, produce human-readable hints per 0.2-block band and, on request, search the
calibration (px_per_block, center_x, ground_y) that maximises the IoU. See references/reference-fit.md."""
from __future__ import annotations

import fnmatch
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import refmask
from .pngio import Canvas, read_png, write_png
from .reference import VIEW_NAMES, View
from .spec import BossForgeError

BAND = 0.2  # model blocks per hint band
HINT_MIN = 0.04  # blocks: smaller edge differences are not reported
MAX_HINTS = 24
AUTO_TIME_BUDGET = 30.0  # seconds
AUTO_MAX_EVALS = 600
Poly = List[Tuple[float, float]]

# ---------------------------------------------------------------------------------------------- tiny bitmap font
_FONT = {
    "0": ("###", "#.#", "#.#", "#.#", "###"), "1": (".#.", "##.", ".#.", ".#.", "###"),
    "2": ("###", "..#", "###", "#..", "###"), "3": ("###", "..#", "###", "..#", "###"),
    "4": ("#.#", "#.#", "###", "..#", "..#"), "5": ("###", "#..", "###", "..#", "###"),
    "6": ("###", "#..", "###", "#.#", "###"), "7": ("###", "..#", "..#", "..#", "..#"),
    "8": ("###", "#.#", "###", "#.#", "###"), "9": ("###", "#.#", "###", "..#", "###"),
    "-": ("...", "...", "###", "...", "..."), ".": ("...", "...", "...", "...", ".#."),
    " ": ("...", "...", "...", "...", "..."), "x": ("...", "#.#", ".#.", "#.#", "..."),
    "y": ("#.#", "#.#", ".#.", ".#.", ".#."), "z": ("###", "..#", ".#.", "#..", "###"),
}


def draw_text(c: Canvas, x: int, y: int, text: str, scale: int = 2, fg=(255, 255, 255, 255), bg=(0, 0, 0, 200)) -> None:
    """Draw `text` (digits - . x y z) with its top-left corner at (x, y) on a translucent plate."""
    w = (len(text) * 4 + 1) * scale
    h = 7 * scale
    for yy in range(max(0, y), min(c.h, y + h)):
        for xx in range(max(0, x), min(c.w, x + w)):
            i = (yy * c.w + xx) * 4
            a = bg[3] / 255.0
            for k in range(3):
                c.data[i + k] = int(c.data[i + k] * (1 - a) + bg[k] * a)
    for n, ch in enumerate(text):
        glyph = _FONT.get(ch, _FONT[" "])
        for gy, row in enumerate(glyph):
            for gx, bit in enumerate(row):
                if bit == "#":
                    for dy in range(scale):
                        for dx in range(scale):
                            c.set(x + (n * 4 + gx + 1) * scale + dx, y + (gy + 1) * scale + dy, fg)


# ---------------------------------------------------------------------------------------------- rasterising
def _hull(points: List[Tuple[float, float]]) -> Poly:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lo: Poly = []
    for p in pts:
        while len(lo) >= 2 and cross(lo[-2], lo[-1], p) <= 0:
            lo.pop()
        lo.append(p)
    up: Poly = []
    for p in reversed(pts):
        while len(up) >= 2 and cross(up[-2], up[-1], p) <= 0:
            up.pop()
        up.append(p)
    return lo[:-1] + up[:-1]


def model_polys(model, view: View) -> List[Tuple[str, Poly]]:
    """In-plane (u, y) convex hull of every box of `model` (a shape.ShapeModel) for this view's projection axis."""
    axis = 0 if view._axis == 0 else 2
    out = []
    pos = model.mesh.positions
    for i, b in enumerate(model.boxes):
        corners = pos[8 * i:8 * i + 8]
        out.append((b["id"], _hull([(c[axis], c[1]) for c in corners])))
    return out


def _fill_convex(poly: Poly, buf: bytearray, w: int, h: int) -> None:
    n = len(poly)
    if n < 3:
        return
    ys = [p[1] for p in poly]
    ya = max(0, int(math.ceil(min(ys) - 0.5)))
    yb = min(h - 1, int(math.floor(max(ys) - 0.5)))
    if yb < ya:
        return
    edges = []
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        if y0 == y1:
            continue
        if y0 > y1:
            x0, y0, x1, y1 = x1, y1, x0, y0
        edges.append((x0, y0, (x1 - x0) / (y1 - y0), y1))
    for y in range(ya, yb + 1):
        cy = y + 0.5
        lo, hi = 1e18, -1e18
        for x0, y0, k, y1 in edges:
            if y0 <= cy < y1:
                x = x0 + (cy - y0) * k
                if x < lo:
                    lo = x
                if x > hi:
                    hi = x
        if hi < lo:
            continue
        a = max(0, int(math.ceil(lo - 0.5)))
        b = min(w, int(math.ceil(hi - 0.5)))
        if b > a:
            buf[y * w + a:y * w + b] = b"\x01" * (b - a)


def rasterize(polys: Sequence[Tuple[str, Poly]], sign: float, s: float, cx: float, gy: float, w: int, h: int,
              f: int = 1) -> bytearray:
    """0/1 coverage of the hulls at (w, h) = image size / f; pixel centres decide."""
    buf = bytearray(w * h)
    k = s / f
    ox, oy = cx / f, gy / f
    for _id, poly in polys:
        _fill_convex([(ox + sign * k * u, oy - k * v) for u, v in poly], buf, w, h)
    return buf


def reduce_mask(mask: bytearray, w: int, h: int, f: int) -> Tuple[bytearray, int, int]:
    """0/255 or 0/1 mask -> 0/1 mask at 1/f resolution (a cell is foreground when >= half its pixels are)."""
    t01 = bytes([0] + [1] * 255)
    if f == 1:
        return bytearray(bytes(mask).translate(t01)), w, h
    cw, ch = w // f, h // f
    out = bytearray(cw * ch)
    half = f * f / 2.0
    for cy in range(ch):
        acc = [0] * cw
        for r in range(f):
            row = bytes(mask[(cy * f + r) * w:(cy * f + r) * w + cw * f]).translate(t01)
            for c in range(f):
                acc = [a + b for a, b in zip(acc, row[c::f])]
        out[cy * cw:(cy + 1) * cw] = bytes(1 if a >= half else 0 for a in acc)
    return out, cw, ch


def iou_counts(ref: bytearray, mod: bytearray) -> Tuple[int, int, int, int]:
    """(intersection, union, ref_only, model_only) of two 0/1 masks of equal size."""
    a = int.from_bytes(ref, "little")
    b = int.from_bytes(mod, "little")
    inter = bin(a & b).count("1")
    ref_n, mod_n = bin(a).count("1"), bin(b).count("1")
    return inter, ref_n + mod_n - inter, ref_n - inter, mod_n - inter


def _score(ref01: bytearray, polys, sign: float, s: float, cx: float, gy: float, w: int, h: int, f: int) -> float:
    inter, union, _a, _b = iou_counts(ref01, rasterize(polys, sign, s, cx, gy, w, h, f))
    return inter / union if union else 0.0


# ---------------------------------------------------------------------------------------------- calibration search
def auto_calibrate(view: View, polys: Sequence[Tuple[str, Poly]], budget: float = AUTO_TIME_BUDGET) -> Dict[str, Any]:
    """Pattern search for (px_per_block, center_x, ground_y) maximising the silhouette IoU. The search variables are the
    scale s and the offsets (dx, dy) from the bounding-box alignment of model and reference at that scale (the model's
    lowest point on the reference's lowest row, the centres of the two extents above each other), so changing s moves the
    model about its own box instead of about the image origin. Starts from the best of the current calibration and
    alignments over a range of scales; coarse stage at 1/4 resolution, then full resolution. Returns the calibration plus
    the IoU before and after, notes and the number of evaluations."""
    t0 = time.monotonic()
    sign = view._sign
    W, H = view.width, view.height
    box = view.mask_box()
    if box is None or not polys:
        raise BossForgeError("/reference/views/" + view.name, "fit_empty", "nothing to calibrate: the view mask or the model is empty")
    us = [u for _i, p in polys for u, _v in p]
    vs = [v for _i, p in polys for _u, v in p]
    mu0, mu1, mv0, mv1 = min(us), max(us), min(vs), max(vs)
    ref_full = bytearray(1 if v else 0 for v in view.mask)
    evals = [0]
    refs: Dict[int, Tuple[bytearray, int, int]] = {}

    def get_ref(f: int):
        if f not in refs:
            refs[f] = reduce_mask(ref_full, W, H, f) if f > 1 else (ref_full, W, H)
        return refs[f]

    ref_h = box[3] - box[1]
    tol_y = 0.02 * ref_h + 3.0
    cu = (mu0 + mu1) / 2.0
    ref_cx = (box[0] + box[2]) / 2.0
    s_lo, s_hi = 0.6 * view.px_per_block, 1.7 * view.px_per_block

    def cal(p):
        """(s, dx, dy) -> (px_per_block, center_x, ground_y)."""
        s, dx, dy = p
        return s, ref_cx - sign * s * cu + dx, box[3] + s * mv0 + dy

    def allowed(p) -> bool:
        """The scale stays within 0.6..1.7 of the spec's, the model's lowest point within 2% of the height of the
        reference's lowest row (the feet are on the ground) and the centre line inside the silhouette's width."""
        return s_lo <= p[0] <= s_hi and abs(p[2]) <= tol_y and abs(p[1]) <= 0.5 * (box[2] - box[0])

    def score(p, f):
        if not allowed(p):
            return -1.0
        r, w, h = get_ref(f)
        evals[0] += 1
        s, cx, gy = cal(p)
        return _score(r, polys, sign, s, cx, gy, w, h, f)

    cur = (view.px_per_block, view.center_x, view.ground_y)
    before = _score(ref_full, polys, sign, cur[0], cur[1], cur[2], W, H, 1)
    c0 = cal((view.px_per_block, 0.0, 0.0))
    start = (view.px_per_block, cur[1] - c0[1], cur[2] - c0[2])
    s_bbox = ref_h / max(mv1 - mv0, 1e-6)
    cands = [start]
    for mult in (0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4):
        for base in (s_bbox, view.px_per_block):
            cands.append((base * mult, 0.0, 0.0))
    best, best_sc = start, -1.0
    for p in cands:
        sc = score(p, 4)
        if sc > best_sc:
            best, best_sc = p, sc

    def descend(p, f, steps, floor):
        """Coordinate pattern search; the steps (relative s, px, px) halve whenever no neighbour improves, until the
        scale step is below `floor` x its start."""
        sc = score(p, f)
        ds, dx, dy = steps
        while evals[0] < AUTO_MAX_EVALS and time.monotonic() - t0 < budget:
            moved = False
            for q in ((p[0] * (1 + ds), p[1], p[2]), (p[0] * (1 - ds), p[1], p[2]), (p[0], p[1] + dx, p[2]),
                      (p[0], p[1] - dx, p[2]), (p[0], p[1], p[2] + dy), (p[0], p[1], p[2] - dy)):
                qs = score(q, f)
                if qs > sc + 1e-9:
                    p, sc, moved = q, qs, True
            if not moved:
                if ds < steps[0] * floor:
                    break
                ds, dx, dy = ds / 2.0, dx / 2.0, dy / 2.0
        return p, sc

    best, _ = descend(best, 4, (0.04, 8.0, 8.0), 1 / 8.0)
    best, after = descend(best, 1, (0.01, 2.0, 2.0), 1 / 16.0)
    best = cal(best)
    notes = []
    if abs(best[0] / cur[0] - 1.0) > 0.1:
        notes.append("px_per_block moved %.0f%% from the spec value: either the spec's calibration is off or the model's overall proportions differ from the reference (measure px_per_block from an anatomy landmark, see reference-fit.md)" % (100 * (best[0] / cur[0] - 1.0)))
    if abs(best[2] - cur[2]) > 0.02 * ref_h:
        notes.append("ground_y moved %.0f px from the spec value: check the soles line of the reference" % (best[2] - cur[2]))
    if after < 0.8:
        notes.append("IoU stays below 0.8 after calibration: the geometry (not the calibration) is what differs; read the hints")
    return {"px_per_block": round(best[0], 3), "center_x": round(best[1], 2), "ground_y": round(best[2], 2),
            "iou_before": round(before, 4), "iou_after": round(after, 4), "evaluations": evals[0],
            "seconds": round(time.monotonic() - t0, 2), "notes": notes}


# ---------------------------------------------------------------------------------------------- hints
def _axis_names(view: View) -> Tuple[str, str, str, str]:
    """(axis letter, name of the + side, name of the - side, 'x'|'z') for in-plane coordinate u."""
    if view._axis == 0:
        return "x", "the character's right (x+)", "the character's left (x-)", "x"
    return "z", "the back (z+)", "the front (z-)", "z"


def _row_extents(mask: bytearray, w: int, y0: int, y1: int) -> Optional[Tuple[int, int]]:
    """(first column, last column + 1) of the foreground in rows [y0, y1) of a 0/1 mask, None when empty."""
    lo, hi = w, 0
    for y in range(max(0, y0), y1):
        row = bytes(mask[y * w:(y + 1) * w])
        a = len(row) - len(row.lstrip(b"\x00"))
        if a >= len(row):
            continue
        lo, hi = min(lo, a), max(hi, len(row.rstrip(b"\x00")))
    return (lo, hi) if hi > lo else None


def _band_hints(view: View, ref01: bytearray, mod01: bytearray, polys, w: int, h: int) -> Tuple[List[str], Dict[str, Any], Dict[str, Any]]:
    s, sign = view.px_per_block, view._sign
    letter, plus, minus, _ = _axis_names(view)
    ref_box = refmask.bbox(ref01, w, h)
    mod_box = refmask.bbox(mod01, w, h)

    def to_model(box):
        if box is None:
            return None
        ua, va = view.unproject(box[0], box[1])
        ub, vb = view.unproject(box[2], box[3])
        return {letter: [round(min(ua, ub), 3), round(max(ua, ub), 3)], "y": [round(min(va, vb), 3), round(max(va, vb), 3)]}

    bbox_ref, bbox_mod = to_model(ref_box), to_model(mod_box)
    cand = []  # (severity, y_top, text)
    if ref_box is None:
        return ["the reference mask is empty"], bbox_ref or {}, bbox_mod or {}
    y_top_m = view.unproject(0, ref_box[1])[1]
    if mod_box is not None:
        y_top_m = max(y_top_m, view.unproject(0, mod_box[1])[1])
    n_bands = int(math.ceil(max(y_top_m, BAND) / BAND))
    box_ext = []
    for bid, poly in polys:
        us = [p[0] for p in poly]
        vs = [p[1] for p in poly]
        box_ext.append((bid, min(us), max(us), min(vs), max(vs)))
    for bi in range(n_bands):
        y_lo, y_hi = bi * BAND, (bi + 1) * BAND
        py_top, py_bot = view.ground_y - s * y_hi, view.ground_y - s * y_lo
        r0, r1 = int(math.floor(py_top + 0.5)), int(math.floor(py_bot + 0.5))
        r0c, r1c = max(0, r0), min(h, r1)
        if r1c <= r0c:
            continue
        a_int = int.from_bytes(ref01[r0c * w:r1c * w], "little")
        b_int = int.from_bytes(mod01[r0c * w:r1c * w], "little")
        missing = bin(a_int & ~b_int).count("1")
        extra = bin(b_int & ~a_int).count("1")
        er, em = _row_extents(ref01, w, r0c, r1c), _row_extents(mod01, w, r0c, r1c)
        label = "y %.1f-%.1f" % (y_lo, y_hi)
        if er is None and em is None:
            continue
        if em is None:
            ua, ub = view.unproject(er[0], 0)[0], view.unproject(er[1], 0)[0]
            cand.append((missing, -y_lo, "%s: the model has nothing here but the reference spans %s %.2f..%.2f (%d px missing): add geometry" % (label, letter, min(ua, ub), max(ua, ub), missing)))
            continue
        if er is None:
            cand.append((extra, -y_lo, "%s: the reference is empty here but the model has %d px of geometry: remove or shrink it" % (label, extra)))
            continue
        # u coordinates of the extreme edges; sign = d(px)/d(u): u = sign*(px-cx)/s
        def u_of(px):
            return sign * (px - view.center_x) / s
        ref_u = sorted((u_of(er[0]), u_of(er[1])))
        mod_u = sorted((u_of(em[0]), u_of(em[1])))
        msgs = []
        d_hi = ref_u[1] - mod_u[1]
        d_lo = mod_u[0] - ref_u[0]
        for d, side, mod_edge, ref_edge, which in ((d_hi, plus, mod_u[1], ref_u[1], 1), (d_lo, minus, mod_u[0], ref_u[0], 0)):
            if d >= HINT_MIN:
                nb = _extreme_box(box_ext, y_lo, y_hi, which)
                msgs.append("reference is %.2f blocks wider than the model on %s (%s: reference %.2f, model %.2f)%s" % (
                    d, side, letter, ref_edge, mod_edge, " [outermost model box there: %s]" % nb if nb else ""))
            elif d <= -HINT_MIN:
                nb = _extreme_box(box_ext, y_lo, y_hi, which)
                msgs.append("the model sticks out %.2f blocks beyond the reference on %s (%s: model %.2f, reference %.2f)%s" % (
                    -d, side, letter, mod_edge, ref_edge, " [outermost model box there: %s]" % nb if nb else ""))
        if msgs:
            cand.append((missing + extra, -y_lo, "%s: %s" % (label, "; ".join(msgs))))
        elif missing + extra >= max(40, 0.15 * bin(a_int).count("1")):
            cand.append((missing + extra, -y_lo, "%s: silhouette widths agree within %.2f blocks but %d px are missing and %d px are extra inside (gaps / notches / holes differ)" % (label, HINT_MIN, missing, extra)))
    cand.sort(key=lambda t: (-t[0], t[1]))
    hints = [c[2] for c in cand[:MAX_HINTS]]
    # extents
    ext = []
    if bbox_ref and bbox_mod:
        dy = bbox_ref["y"][1] - bbox_mod["y"][1]
        if abs(dy) >= HINT_MIN:
            tb = _top_box(box_ext)
            ext.append("top: the reference reaches y=%.2f, the model y=%.2f (%s by %.2f blocks)%s" % (
                bbox_ref["y"][1], bbox_mod["y"][1], "reference is higher" if dy > 0 else "model is higher", abs(dy),
                " [topmost model box: %s]" % tb if tb else ""))
        db = bbox_ref["y"][0] - bbox_mod["y"][0]
        if abs(db) >= HINT_MIN:
            ext.append("bottom: the reference reaches y=%.2f, the model y=%.2f (the soles should be at y=0)" % (bbox_ref["y"][0], bbox_mod["y"][0]))
        hi_d = bbox_ref[letter][1] - bbox_mod[letter][1]
        lo_d = bbox_mod[letter][0] - bbox_ref[letter][0]
        for d, side, r, m in ((hi_d, plus, bbox_ref[letter][1], bbox_mod[letter][1]), (lo_d, minus, bbox_ref[letter][0], bbox_mod[letter][0])):
            if abs(d) >= HINT_MIN:
                ext.append("%s extent: reference %s=%.2f, model %s=%.2f (%s by %.2f blocks)" % (
                    side, letter, r, letter, m, "reference reaches further out" if d > 0 else "model reaches further out", abs(d)))
    return ext + hints, bbox_ref or {}, bbox_mod or {}


def _extreme_box(box_ext, y_lo, y_hi, which: int) -> Optional[str]:
    best, bid = None, None
    for b, u0, u1, v0, v1 in box_ext:
        if v1 <= y_lo or v0 >= y_hi:
            continue
        val = u1 if which else -u0
        if best is None or val > best:
            best, bid = val, b
    return bid


def _top_box(box_ext) -> Optional[str]:
    return max(box_ext, key=lambda t: t[4])[0] if box_ext else None


def mask_png(view: View, ref01: bytearray, w: int, h: int, crop: Tuple[int, int, int, int]) -> Canvas:
    """The reference cropped to the view's crop with everything outside the mask darkened and the mask contour in red:
    shows what the mask treats as the character."""
    x0, y0, x1, y1 = crop
    edge = refmask.land(ref01, refmask.invert(refmask.erode(ref01, w, h, 1)))
    out = Canvas(x1 - x0, y1 - y0)
    for y in range(y1 - y0):
        for x in range(x1 - x0):
            i = (y0 + y) * w + x0 + x
            si = i * 4
            di = (y * out.w + x) * 4
            if edge[i]:
                out.data[di:di + 4] = b"\xff\x20\x20\xff"
            elif ref01[i]:
                out.data[di:di + 4] = view.rgba[si:si + 3] + b"\xff"
            else:
                out.data[di:di + 4] = bytes((view.rgba[si] // 4, view.rgba[si + 1] // 4, view.rgba[si + 2] // 4 + 24, 255))
    return out


# ---------------------------------------------------------------------------------------------- overlay
def overlay_png(view: View, ref01: bytearray, mod01: bytearray, w: int, h: int, crop: Tuple[int, int, int, int],
                scale: int = 1) -> Canvas:
    """The reference darkened; both = green, model only = red, reference only = blue; ground line, centre line and
    1-block height ticks with labels."""
    x0, y0, x1, y1 = crop
    cw, ch = x1 - x0, y1 - y0
    out = Canvas(cw, ch)
    d = out.data
    src = view.rgba
    for y in range(ch):
        row = (y0 + y) * w + x0
        for x in range(cw):
            i = row + x
            si = i * 4
            di = (y * cw + x) * 4
            r, g, b = src[si] // 3, src[si + 1] // 3, src[si + 2] // 3
            a, m = ref01[i], mod01[i]
            if a and m:
                g = min(255, g + 150)
            elif m:
                r = min(255, r + 190)
            elif a:
                b = min(255, b + 190)
                g = min(255, g + 20)
            d[di], d[di + 1], d[di + 2], d[di + 3] = r, g, b, 255
    if scale > 1:
        out = out.scaled(scale)
    # guides: ground, centre line, 1-block ticks
    gy = view.ground_y - y0
    for y_blk in range(0, int(max(1, (view.ground_y - y0) // view.px_per_block)) + 1):
        py = int(round((gy - y_blk * view.px_per_block) * scale))
        if 0 <= py < out.h:
            for px in range(0, min(out.w, 14 * scale)):
                out.set(px, py, (255, 230, 80, 255))
            if y_blk:
                draw_text(out, 16 * scale if scale > 1 else 16, max(0, py - 7), str(y_blk), 2)
    cx = int(round((view.center_x - x0) * scale))
    if 0 <= cx < out.w:
        for py in range(0, out.h, 4):
            out.set(cx, py, (255, 230, 80, 255))
    return out


# ---------------------------------------------------------------------------------------------- driver
def fit_views(model, views: Dict[str, View], names: Optional[Sequence[str]], out_dir, auto: bool = False,
              exclude: Sequence[str] = ()) -> Dict[str, Any]:
    """Fit `model` (shape.ShapeModel) to the chosen views; writes DIR/fit_<view>.png and returns the JSON result.
    `exclude`: fnmatch patterns of box ids left out of the calibration search only (e.g. wings that do not match the
    reference yet); the IoU, hints and overlay always use every box."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    chosen = [v for v in (names or [n for n in VIEW_NAMES if n in views])]
    for nm in chosen:
        if nm not in views:
            raise BossForgeError("/reference/views/" + nm, "reference_unknown_view",
                                 "view %r is not defined in reference.views (defined: %s)" % (nm, ", ".join(views) or "none"))
    result: Dict[str, Any] = {"ok": True, "views": {}, "overlays": []}
    ious = []
    for nm in chosen:
        view = views[nm]
        polys = model_polys(model, view)
        cal_info = None
        if auto:
            keep = [(i, p) for i, p in polys if not any(fnmatch.fnmatchcase(i, pat) for pat in exclude)]
            cal_info = auto_calibrate(view, keep or polys)
            cal_info["boxes_used"] = len(keep or polys)
            view = View(view.name, view.width, view.height, view.rgba, view.mask, view.crop, cal_info["px_per_block"],
                        cal_info["ground_y"], cal_info["center_x"])
        w, h = view.width, view.height
        ref01 = bytearray(1 if v else 0 for v in view.mask)
        mod01 = rasterize(polys, view._sign, view.px_per_block, view.center_x, view.ground_y, w, h)
        inter, union, ref_only, mod_only = iou_counts(ref01, mod01)
        hints, bbox_ref, bbox_mod = _band_hints(view, ref01, mod01, polys, w, h)
        crop = view.crop
        bb = refmask.bbox(mod01, w, h)
        if bb is not None:  # show the model even where it leaves the crop
            crop = (max(0, min(crop[0], bb[0])), max(0, min(crop[1], bb[1])), min(w, max(crop[2], bb[2])), min(h, max(crop[3], bb[3])))
        scale = 2 if (crop[2] - crop[0]) * (crop[3] - crop[1]) <= 160000 else 1
        path = out_dir / ("fit_%s.png" % nm)
        write_png(path, overlay_png(view, ref01, mod01, w, h, crop, scale))
        mpath = out_dir / ("mask_%s.png" % nm)
        write_png(mpath, mask_png(view, ref01, w, h, view.crop))
        iou = inter / union if union else 0.0
        ious.append(iou)
        res = {"iou": round(iou, 4), "ref_px": inter + ref_only, "model_px": inter + mod_only, "missing_px": ref_only,
               "extra_px": mod_only, "bbox_ref": bbox_ref, "bbox_model": bbox_mod,
               "calibration": {"px_per_block": round(view.px_per_block, 3), "ground_y": round(view.ground_y, 2),
                               "center_x": round(view.center_x, 2)},
               "overlay": str(path), "mask_preview": str(mpath), "hints": hints}
        if cal_info:
            res["auto_calibrate"] = cal_info
        result["views"][nm] = res
        result["overlays"].append(str(path))
    result["mean_iou"] = round(sum(ious) / len(ious), 4) if ious else 0.0
    return result


# ---------------------------------------------------------------------------------------------- reference-info
def reference_info(image, grid: int = 50, out_path=None, crop: Optional[Sequence[int]] = None, zoom: int = 1) -> Dict[str, Any]:
    """Write a copy of the image with labelled pixel gridlines (labels are ORIGINAL image pixel coordinates), return its
    size and the connected figures found by the auto mask (their crop boxes)."""
    from . import automask
    src = Path(image)
    try:
        canvas = read_png(src)
    except (ValueError, OSError) as e:
        raise BossForgeError("", "reference_bad_image", "%s is not a readable PNG (%s); convert it first, e.g. `magick %s out.png`" % (src, e, src.name))
    w, h = canvas.w, canvas.h
    cr = tuple(int(v) for v in crop) if crop else (0, 0, w, h)
    if not (0 <= cr[0] < cr[2] <= w and 0 <= cr[1] < cr[3] <= h):
        raise BossForgeError("/crop", "reference_bad_crop", "crop %s is outside the %dx%d image" % (list(cr), w, h))
    mask = automask.auto_mask(canvas.data, w, h, cr, min_island=0.0005)
    comps = refmask.Components(mask, w, h)
    order = sorted(range(len(comps)), key=lambda k: -comps.area[k])
    figures = [{"bbox": list(comps.box[k]), "area": comps.area[k]} for k in order[:16] if comps.area[k] >= 200]
    box = refmask.bbox(mask, w, h)
    zoom = max(1, int(zoom))
    view = Canvas(cr[2] - cr[0], cr[3] - cr[1])
    for y in range(view.h):
        view.data[y * view.w * 4:(y + 1) * view.w * 4] = canvas.data[((cr[1] + y) * w + cr[0]) * 4:((cr[1] + y) * w + cr[2]) * 4]
    if zoom > 1:
        view = view.scaled(zoom)
    grid = max(5, int(grid))
    gx = (cr[0] + grid - 1) // grid * grid
    while gx < cr[2]:
        px = (gx - cr[0]) * zoom
        major = gx % (grid * 2) == 0
        for y in range(view.h):
            if (y // 3) % 2 == 0 or major:
                view.set(px, y, (255, 60, 60, 255) if major else (255, 200, 0, 255))
        gx += grid
    gy = (cr[1] + grid - 1) // grid * grid
    while gy < cr[3]:
        py = (gy - cr[1]) * zoom
        major = gy % (grid * 2) == 0
        for x in range(view.w):
            if (x // 3) % 2 == 0 or major:
                view.set(x, py, (255, 60, 60, 255) if major else (255, 200, 0, 255))
        gy += grid
    gx = (cr[0] + grid - 1) // grid * grid
    while gx < cr[2]:
        gy = (cr[1] + grid - 1) // grid * grid
        while gy < cr[3]:
            if gx % (grid * 2) == 0 and gy % (grid * 2) == 0:
                draw_text(view, (gx - cr[0]) * zoom + 2, (gy - cr[1]) * zoom + 2, "%d" % gx, 2, (255, 255, 255, 255), (150, 0, 0, 215))
                draw_text(view, (gx - cr[0]) * zoom + 2, (gy - cr[1]) * zoom + 18, "%d" % gy, 2, (255, 255, 0, 255), (0, 0, 120, 215))
            gy += grid
        gx += grid
    out = Path(out_path) if out_path else Path("%s_grid.png" % src.stem)
    write_png(out, view)
    return {"ok": True, "image": str(src), "width": w, "height": h, "grid": grid, "grid_image": str(out), "zoom": zoom,
            "crop": list(cr), "auto_mask_bbox": list(box) if box else None, "figures": figures,
            "note": "grid labels are original image pixels: white = x (column), yellow = y (row); red lines every %d px, yellow every %d px" % (grid * 2, grid)}
