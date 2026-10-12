"""Reference baking: paint the real pixels of reference views into the skin atlas (pure Python, deterministic).

`reference.bake` (see references/skin-forge.md, "Reference baking") turns the skin painter texel-driven: every atlas
texel knows its 3D position, normal and tangents (blocks per texel along atlas u and v), so it can be projected
into each calibrated orthographic reference view (`reference.View`) and coloured with the pixels it covers there.

Per texel and view the baker applies, in this order:

1. facing: the face normal must look at the camera (`facing >= min_facing`). Views are tried best facing first (ties
   within 0.01 broken by the order of `bake.views`), so all texels of one face prefer the same view and a face is
   never mixed from two views unless its best view cannot see some texels.
2. occlusion: the model is rendered into a depth buffer per view (every triangle, at the reference resolution); the
   texel (its centre and the four corners of its footprint) must not lie more than `depth_tolerance` blocks (plus the
   slope of its own plane over half a pixel) behind that buffer. An occluder's pixels never colour a hidden texel.
3. footprint: the texel's own footprint (its atlas square mapped onto the face and projected, so rotated boxes get
   a correct anisotropic parallelogram, sampled with sub-boxes) is averaged over the FOREGROUND pixels of the view
   (exact area-weighted box filter from summed-area tables; background pixels are never averaged). With at least half of
   the footprint on foreground the result is a direct sample.
4. silhouette edge: a footprint that is mostly background takes the colour of the nearest interior foreground pixel
   (so wall or sheet background never leaks into edges); counted as `edge`.

Texels no view can sample directly are, when `smear` is on, sampled at grazing facing (0.2 <= facing < min_facing,
only when the surface is visible there) and then filled along the surface from baked texels of the same part (graph
of atlas neighbours plus the two sides of every box edge; breadth-first search carrying the colours of the two nearest
source faces, weights 1 / hops^2, within 0.3 block, fading into the procedural colour over the second half of the
reach): a side face takes the blend of the colours of the faces it joins, a face half hidden behind an arm takes its
visible neighbours. Everything else keeps the procedural colour. Optional median-cut palette quantisation and a
`blend` factor finish the pass; `footprint` < 1 samples only the centre of each texel (crisper pixel art).

`BakePlan.compare` renders the model through every bake view with the final texture and measures it against the
reference (review PNG + numbers), so the agent never has to judge the result by eye alone.
"""
from __future__ import annotations

import math
from collections import deque
from itertools import accumulate, chain, repeat
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .features import RegionSel
from .pngio import Canvas, hconcat
from .spec import BossForgeError, Err

RGB = Tuple[int, int, int]

BAKE_KEYS = ("views", "min_facing", "depth_tolerance", "smear", "palette", "regions", "exclude", "order", "blend", "footprint", "sample", "snap", "snap_origin")
VIEW_NAMES = ("front", "back", "right", "left")
ORDERS = ("first", "last")
SAMPLES = ("footprint", "center")
MIN_FACING_RANGE = (0.1, 1.0)
DEPTH_TOLERANCE_RANGE = (0.0, 0.5)
PALETTE_RANGE = (8, 64)
BLEND_RANGE = (0.0, 1.0)
FOOTPRINT_RANGE = (0.25, 1.0)
SNAP_MAX = 64  # largest art pixel (in reference pixels) `snap` accepts
DEFAULTS = {"views": None, "min_facing": 0.5, "depth_tolerance": 0.06, "smear": True, "palette": 0, "regions": ["*"],
            "exclude": [], "order": "last", "blend": 1.0, "footprint": 1.0, "sample": "footprint", "snap": 0, "snap_origin": [0, 0]}

KIND_CODE = {"direct": 1, "edge": 2, "smeared": 3}  # per-texel bake map codes (4 = filled)
SMEAR_MIN_FACING = 0.2  # grazing views below this never contribute
MIN_DIRECT_FG = 0.5  # share of the footprint on foreground for a direct sample
LOW_COVERAGE = 0.6  # direct / visible below this warns bake_low_coverage
FILL_BLOCKS = 0.3  # reach of the 3D fill of texels no view sampled
FILL_GROUPS = 2  # distinct baked faces whose colours a filled texel blends
MARGIN = 8  # pixels around the model's projection kept in the depth buffer
MIN_HALF = 0.5  # footprints are never smaller than one pixel
SUBDIV_AT = 1.5  # px per sub-box of a rotated footprint
SUBDIV_MAX = 4
_NEG = -1e30


# --------------------------------------------------------------------------- validation
def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _check_targets(errs: List[Err], path: str, v: Any) -> None:
    from .spec import BOX_REGION_RE, REGION_KEYS
    if not isinstance(v, list):
        errs.append(Err(path, "bad_type", "%s must be a list of region names" % path.rsplit("/", 1)[-1]))
        return
    for i, r in enumerate(v):
        p = "%s/%d" % (path, i)
        if not isinstance(r, str):
            errs.append(Err(p, "bad_type", "region names are strings, got %r" % (r,)))
        elif r == "*" or r in REGION_KEYS or BOX_REGION_RE.match(r):
            continue
        else:
            errs.append(Err(p, "unknown_region", "unknown region %r; allowed: *, %s, or box:<id> / part:<id> (globs like box:skirt_fr* allowed)"
                            % (r, ", ".join(REGION_KEYS))))


def validate_bake(bake: Any, base_path: str = "/reference/bake", defined_views: Optional[Sequence[str]] = None) -> List[Err]:
    """Problems of a raw `reference.bake` block (the spec calls this with `list(reference.views)` as `defined_views`)."""
    errs = []  # type: List[Err]
    p = base_path
    if not isinstance(bake, dict):
        errs.append(Err(p, "bad_type", "bake must be an object"))
        return errs
    for k in bake:
        if k not in BAKE_KEYS:
            errs.append(Err("%s/%s" % (p, k), "unknown_key", "unknown bake key %r; allowed: %s" % (k, ", ".join(BAKE_KEYS))))
    declared = list(defined_views) if defined_views is not None else None
    if "views" in bake:
        v = bake["views"]
        if not isinstance(v, list) or not v:
            errs.append(Err(p + "/views", "bad_type", "views must be a non-empty list of view names (%s)" % ", ".join(VIEW_NAMES)))
        else:
            seen = set()
            for i, name in enumerate(v):
                vp = "%s/views/%d" % (p, i)
                if name not in VIEW_NAMES:
                    errs.append(Err(vp, "reference_unknown_view", "%r is not a view name; allowed: %s" % (name, ", ".join(VIEW_NAMES))))
                elif declared is not None and name not in declared:
                    errs.append(Err(vp, "reference_unknown_view", "view %r is not defined in reference.views (defined: %s)" % (name, ", ".join(declared) or "none")))
                elif name in seen:
                    errs.append(Err(vp, "duplicate_view", "view %r is listed twice" % name))
                seen.add(name)
    elif declared is not None and not declared:
        errs.append(Err(p + "/views", "reference_unknown_view", "bake needs at least one view in reference.views"))
    for key, (lo, hi) in (("min_facing", MIN_FACING_RANGE), ("depth_tolerance", DEPTH_TOLERANCE_RANGE), ("blend", BLEND_RANGE),
                          ("footprint", FOOTPRINT_RANGE)):
        if key in bake:
            v = bake[key]
            if not _is_num(v):
                errs.append(Err("%s/%s" % (p, key), "bad_type", "%s must be a number, got %r" % (key, v)))
            elif v < lo or v > hi:
                errs.append(Err("%s/%s" % (p, key), "out_of_range", "%r is outside the allowed range %s..%s" % (v, lo, hi)))
    if "smear" in bake and not isinstance(bake["smear"], bool):
        errs.append(Err(p + "/smear", "bad_type", "smear must be true or false"))
    if "palette" in bake:
        v = bake["palette"]
        if isinstance(v, bool) or not isinstance(v, int):
            errs.append(Err(p + "/palette", "bad_type", "palette must be an integer (0 = off, or %d..%d colours)" % PALETTE_RANGE))
        elif v != 0 and not (PALETTE_RANGE[0] <= v <= PALETTE_RANGE[1]):
            errs.append(Err(p + "/palette", "out_of_range", "%r is outside the allowed range: 0 (off) or %d..%d" % ((v,) + PALETTE_RANGE)))
    if "order" in bake and bake["order"] not in ORDERS:
        errs.append(Err(p + "/order", "unknown_value", "%r is not allowed; allowed values: %s" % (bake["order"], ", ".join(ORDERS))))
    if "sample" in bake and bake["sample"] not in SAMPLES:
        errs.append(Err(p + "/sample", "unknown_value", "%r is not allowed; allowed values: %s" % (bake["sample"], ", ".join(SAMPLES))))
    if "snap" in bake:
        v = bake["snap"]
        if isinstance(v, bool) or not isinstance(v, int):
            errs.append(Err(p + "/snap", "bad_type", "snap must be an integer: the art pixel size in reference pixels (0 = off)"))
        elif not 0 <= v <= SNAP_MAX:
            errs.append(Err(p + "/snap", "out_of_range", "%r is outside the allowed range 0..%d reference pixels" % (v, SNAP_MAX)))
    if "snap_origin" in bake:
        v = bake["snap_origin"]
        if not (isinstance(v, list) and len(v) == 2 and all(_is_num(c) for c in v)):
            errs.append(Err(p + "/snap_origin", "bad_type", "snap_origin must be [x, y] in reference pixels (a corner of an art pixel)"))
    for key in ("regions", "exclude"):
        if key in bake:
            _check_targets(errs, "%s/%s" % (p, key), bake[key])
            if key == "regions" and isinstance(bake[key], list) and not bake[key]:
                errs.append(Err(p + "/regions", "required", "regions must name at least one region (default [\"*\"] = everything)"))
    return errs


def normalize_bake(raw: dict, defined_views: Optional[Sequence[str]] = None) -> dict:
    """The bake block with every default filled (assumes it validated). Without `views` all defined views are used,
    in the order front, back, right, left."""
    out = dict(DEFAULTS)
    out.update({k: raw[k] for k in BAKE_KEYS if k in raw})
    if out["views"] is None:
        out["views"] = [v for v in VIEW_NAMES if defined_views is None or v in defined_views]
    out["views"] = list(out["views"])
    out["regions"] = list(out["regions"])
    out["exclude"] = list(out["exclude"])
    out["min_facing"] = float(out["min_facing"])
    out["depth_tolerance"] = float(out["depth_tolerance"])
    out["blend"] = float(out["blend"])
    out["footprint"] = float(out["footprint"])
    out["palette"] = int(out["palette"])
    out["smear"] = bool(out["smear"])
    out["snap"] = int(out["snap"])
    out["snap_origin"] = [float(out["snap_origin"][0]), float(out["snap_origin"][1])]
    return out


# --------------------------------------------------------------------------- palette
_CHANNEL_WEIGHT = (3.0, 4.0, 2.0)


def _box_stats(items: List[Tuple[RGB, int]]):
    tot = 0
    sums = [0.0, 0.0, 0.0]
    for rgb, c in items:
        tot += c
        sums[0] += rgb[0] * c
        sums[1] += rgb[1] * c
        sums[2] += rgb[2] * c
    mean = [s / tot for s in sums]
    var = [0.0, 0.0, 0.0]
    for rgb, c in items:
        for k in range(3):
            d = rgb[k] - mean[k]
            var[k] += c * d * d
    var = [var[k] * _CHANNEL_WEIGHT[k] for k in range(3)]
    return tot, mean, var


def _dist2(a: RGB, b: RGB) -> float:
    return (_CHANNEL_WEIGHT[0] * (a[0] - b[0]) ** 2 + _CHANNEL_WEIGHT[1] * (a[1] - b[1]) ** 2 + _CHANNEL_WEIGHT[2] * (a[2] - b[2]) ** 2)


def median_cut(hist: Dict[RGB, int], n: int) -> Dict[RGB, RGB]:
    """Colour -> palette colour for at most `n` colours. Median cut: repeatedly split the box with the largest weighted
    squared error at its weighted median along its widest channel, into n - n // 6 boxes (the palette entry is the box's
    weighted mean). The remaining n // 6 entries rescue outliers: each takes the colour whose error against the palette so
    far, times the square root of its count, is largest (a small glowing emblem or an eye keeps its own colour instead of
    being averaged into the surroundings). Deterministic: stable sorts and colour tie-breaks, no randomness."""
    if len(hist) <= n:
        return {c: c for c in hist}
    rescue = n // 6
    boxes = []  # type: List[Tuple[List[Tuple[RGB, int]], float, List[float]]]
    items = sorted(hist.items())
    tot, mean, var = _box_stats(items)
    boxes.append((items, sum(var), var))
    while len(boxes) < n - rescue:
        best_i, best = -1, 0.0
        for i, (its, score, _v) in enumerate(boxes):
            if len(its) >= 2 and score > best:
                best_i, best = i, score
        if best_i < 0:
            break
        its, _score, var = boxes.pop(best_i)
        axis = 0
        for k in (1, 2):
            if var[k] > var[axis]:
                axis = k
        its.sort(key=lambda it: (it[0][axis], it[0]))
        total = sum(c for _, c in its)
        acc = 0
        cut = 1
        for idx, (_rgb, c) in enumerate(its):
            acc += c
            if acc * 2 >= total:
                cut = idx + 1
                break
        cut = min(max(cut, 1), len(its) - 1)
        for part in (its[cut:], its[:cut]):
            _t, _m, v = _box_stats(part)
            boxes.insert(best_i, (part, sum(v), v))
    entries = []  # type: List[RGB]
    owner = {}  # type: Dict[RGB, int]
    err = {}  # type: Dict[RGB, float]
    for its, _score, _v in boxes:
        _t, mean, _var = _box_stats(its)
        entry = (int(mean[0] + 0.5), int(mean[1] + 0.5), int(mean[2] + 0.5))
        entries.append(entry)
        for rgb, _c in its:
            owner[rgb] = len(entries) - 1
            err[rgb] = _dist2(rgb, entry)
    colours = sorted(hist)
    for _ in range(max(0, n - len(entries))):  # normally n // 6 rescue entries; more when the splitting ran out of boxes
        pick, pick_score = None, 0.0
        for c in colours:
            s = err[c] * math.sqrt(hist[c])
            if s > pick_score:
                pick, pick_score = c, s
        if pick is None:
            break
        entries.append(pick)
        idx = len(entries) - 1
        for c in colours:
            d = _dist2(c, pick)
            if d < err[c]:
                err[c] = d
                owner[c] = idx
    return {c: entries[owner[c]] for c in colours}


# --------------------------------------------------------------------------- rasterization helpers
def _spans(x0, y0, d0, x1, y1, d1, x2, y2, d2, bw, bh):
    """Pixel spans of a triangle (coordinates relative to the buffer origin, pixel i centre = i + 0.5):
    returns (gx, spans) with spans = [(row, i0, i1, depth at pixel i0)] and depth(i) = depth(i0) + gx * (i - i0)."""
    det = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)
    if abs(det) < 1e-9:
        return 0.0, []
    gx = ((d1 - d0) * (y2 - y0) - (d2 - d0) * (y1 - y0)) / det
    gy = ((d2 - d0) * (x1 - x0) - (d1 - d0) * (x2 - x0)) / det
    j0 = max(0, int(math.ceil(min(y0, y1, y2) - 0.5)))
    j1 = min(bh - 1, int(math.floor(max(y0, y1, y2) - 0.5)))
    edges = ((x0, y0, x1, y1), (x1, y1, x2, y2), (x2, y2, x0, y0))
    out = []
    for j in range(j0, j1 + 1):
        yc = j + 0.5
        xs = []
        for ax, ay, bx, by in edges:
            if ay == by:
                continue
            if (ay <= yc <= by) or (by <= yc <= ay):
                xs.append(ax + (yc - ay) * (bx - ax) / (by - ay))
        if len(xs) < 2:
            continue
        i0 = max(0, int(math.ceil(min(xs) - 0.5)))
        i1 = min(bw - 1, int(math.floor(max(xs) - 0.5)))
        if i1 < i0:
            continue
        out.append((j, i0, i1, d0 + gx * (i0 + 0.5 - x0) + gy * (yc - y0)))
    return gx, out


def _span_depths(zs: float, gx: float, n: int):
    return accumulate(chain((zs,), repeat(gx, n - 1)))


def _box_sums(tab, x0: float, y0: float, x1: float, y1: float):
    """[fg area, sum r, sum g, sum b] over the image rectangle (foreground pixels only), from the View's summed-area
    tables (bilinear at fractional edges = exact box integral of the piecewise constant image)."""
    ox, oy, bw, bh, stride, sats = tab
    x0 -= ox
    x1 -= ox
    y0 -= oy
    y1 -= oy
    if x0 < 0.0:
        x0 = 0.0
    if y0 < 0.0:
        y0 = 0.0
    if x1 > bw:
        x1 = float(bw)
    if y1 > bh:
        y1 = float(bh)
    if x1 <= x0 or y1 <= y0:
        return (0.0, 0.0, 0.0, 0.0)
    pairs = []
    for xx, sx in ((x1, 1.0), (x0, -1.0)):
        i = int(xx)
        a = xx - i
        if i >= bw:
            i, a = bw - 1, 1.0
        for yy, sy in ((y1, 1.0), (y0, -1.0)):
            j = int(yy)
            b = yy - j
            if j >= bh:
                j, b = bh - 1, 1.0
            s = sx * sy
            o = j * stride + i
            pairs.append((o, s * (1.0 - a) * (1.0 - b)))
            if a:
                pairs.append((o + 1, s * a * (1.0 - b)))
            if b:
                pairs.append((o + stride, s * (1.0 - a) * b))
                if a:
                    pairs.append((o + stride + 1, s * a * b))
    return tuple([sum([sat[o] * w for o, w in pairs]) for sat in sats])


# --------------------------------------------------------------------------- one reference view
class _ViewBake:
    """Projection, depth buffer, footprints and samplers of one reference view."""

    def __init__(self, name: str, view, positions: List[Tuple[float, float, float]], tris, normals_t):
        self.name = name
        self.view = view
        o = view.project(0.0, 0.0, 0.0)
        ex, ey, ez = view.project(1.0, 0.0, 0.0), view.project(0.0, 1.0, 0.0), view.project(0.0, 0.0, 1.0)
        self.o = o
        self.mx = (ex[0] - o[0], ey[0] - o[0], ez[0] - o[0])
        self.my = (ex[1] - o[1], ey[1] - o[1], ez[1] - o[1])
        self.md = (ex[2] - o[2], ey[2] - o[2], ez[2] - o[2])
        mx, my, md = self.mx, self.my, self.md
        self.pxs = [o[0] + mx[0] * x + mx[1] * y + mx[2] * z for x, y, z in positions]
        self.pys = [o[1] + my[0] * x + my[1] * y + my[2] * z for x, y, z in positions]
        self.pds = [o[2] + md[0] * x + md[1] * y + md[2] * z for x, y, z in positions]
        w, h = view.width, view.height
        if self.pxs:
            self.bx0 = max(0, int(math.floor(min(self.pxs))) - MARGIN)
            self.by0 = max(0, int(math.floor(min(self.pys))) - MARGIN)
            bx1 = min(w, int(math.ceil(max(self.pxs))) + MARGIN)
            by1 = min(h, int(math.ceil(max(self.pys))) + MARGIN)
        else:
            self.bx0 = self.by0 = bx1 = by1 = 0
        self.bw = max(0, bx1 - self.bx0)
        self.bh = max(0, by1 - self.by0)
        self.tab = view._tables()
        self.mask = view.mask
        self.W = w
        self.H = h
        self.fp = {}  # type: Dict[int, tuple]
        self.shrink = 1.0
        self.center = False  # sample: center = the single reference pixel at the texel centre, no averaging
        self.snap = 0  # art pixel size in reference pixels: sample points snap to the middle of their art pixel (0 = off)
        self.snap_origin = (0.0, 0.0)  # reference pixel coordinates of a corner of the art pixel grid
        self._px_sum = 0.0
        self._px_n = 0
        self.stat = {"facing": 0, "direct": 0, "edge": 0, "smeared": 0, "occluded": 0, "mask_miss": 0}
        self.tris = []  # front-facing triangle indices (cull by the mesh normal)
        self.tri_facing = {}  # type: Dict[int, float]
        for ti, tri in enumerate(tris):
            n = normals_t[tri.n[0]]
            f = n[0] * md[0] + n[1] * md[1] + n[2] * md[2]
            if f > 1e-4:
                self.tris.append(ti)
                self.tri_facing[ti] = f
        self.zb = [_NEG] * (self.bw * self.bh)
        self._render_depth(tris)

    # ---- depth buffer
    def tri_screen(self, tri):
        a, b, c = tri.p
        pxs, pys, pds = self.pxs, self.pys, self.pds
        bx, by = self.bx0, self.by0
        return (pxs[a] - bx, pys[a] - by, pds[a], pxs[b] - bx, pys[b] - by, pds[b], pxs[c] - bx, pys[c] - by, pds[c])

    def _render_depth(self, tris) -> None:
        bw, bh, zb = self.bw, self.bh, self.zb
        if not zb:
            return
        for ti in self.tris:
            gx, spans = _spans(*self.tri_screen(tris[ti]), bw, bh)
            for j, i0, i1, zs in spans:
                a = j * bw + i0
                b = j * bw + i1 + 1
                if gx == 0.0:
                    zb[a:b] = [z if z > zs else zs for z in zb[a:b]]
                else:
                    zb[a:b] = [o if o > z else z for o, z in zip(zb[a:b], _span_depths(zs, gx, i1 - i0 + 1))]

    # ---- per triangle footprint
    def footprint(self, ti: int, tu, tv):
        """(sub-boxes [(ox, oy, hx, hy)], visibility probes [(ox, oy, od)], depth slope per pixel) of a triangle."""
        got = self.fp.get(ti)
        if got is not None:
            return got
        mx, my, md = self.mx, self.my, self.md
        ax = tu[0] * mx[0] + tu[1] * mx[1] + tu[2] * mx[2]
        ay = tu[0] * my[0] + tu[1] * my[1] + tu[2] * my[2]
        ad = tu[0] * md[0] + tu[1] * md[1] + tu[2] * md[2]
        bx = tv[0] * mx[0] + tv[1] * mx[1] + tv[2] * mx[2]
        by = tv[0] * my[0] + tv[1] * my[1] + tv[2] * my[2]
        bd = tv[0] * md[0] + tv[1] * md[1] + tv[2] * md[2]
        det = ax * by - ay * bx
        gx = gy = 0.0
        if abs(det) > 1e-9:
            gx = (by * ad - ay * bd) / det
            gy = (ax * bd - bx * ad) / det
            slope = 0.5 * (abs(gx) + abs(gy))
        else:
            slope = 1.0
        la = math.hypot(ax, ay)
        lb = math.hypot(bx, by)
        k = self.shrink  # the sampled footprint is the texel's parallelogram scaled about its centre (1 = the whole texel)
        sax, say, sbx, sby = ax * k, ay * k, bx * k, by * k
        sdet = sax * sby - say * sbx
        hx = max(MIN_HALF, (abs(sax) + abs(sbx)) * 0.5)
        hy = max(MIN_HALF, (abs(say) + abs(sby)) * 0.5)
        subs = [(0.0, 0.0, hx, hy)]
        if max(la, lb) * k > SUBDIV_AT and 4.0 * hx * hy > 1.3 * max(abs(sdet), 1e-9):  # rotated: sample the parallelogram
            n = min(SUBDIV_MAX, max(1, int(math.ceil(la * k / SUBDIV_AT))))
            m = min(SUBDIV_MAX, max(1, int(math.ceil(lb * k / SUBDIV_AT))))
            shx = max(MIN_HALF, (abs(sax) / n + abs(sbx) / m) * 0.5)
            shy = max(MIN_HALF, (abs(say) / n + abs(sby) / m) * 0.5)
            subs = []
            for i in range(n):
                s = (i + 0.5) / n - 0.5
                for j in range(m):
                    t = (j + 0.5) / m - 0.5
                    subs.append((s * sax + t * sbx, s * say + t * sby, shx, shy))
        probes = [(0.0, 0.0, 0.0)]
        for sa, sb in ((-0.45, -0.45), (0.45, -0.45), (-0.45, 0.45), (0.45, 0.45)):  # probes stay inside the texel's own pixels
            probes.append((sa * ax + sb * bx, sa * ay + sb * by, sa * ad + sb * bd))
        got = (subs, probes, slope)
        self.fp[ti] = got
        self._px_sum += max(la, lb)
        self._px_n += 1
        return got

    def px_per_texel(self) -> float:
        """Mean length in reference pixels of a texel's longer footprint side (over the triangles sampled so far)."""
        return self._px_sum / self._px_n if self._px_n else 1.0

    # ---- queries
    def visible(self, cx: float, cy: float, cd: float, probes, tol: float) -> bool:
        zb, bw, bh = self.zb, self.bw, self.bh
        bx0, by0 = self.bx0, self.by0
        for ox, oy, od in probes:
            x = cx + ox - bx0
            y = cy + oy - by0
            if x < 0.0 or y < 0.0:
                continue
            ix, iy = int(x), int(y)
            if ix >= bw or iy >= bh:
                continue
            if zb[iy * bw + ix] > cd + od + tol:
                return False
        return True

    def sample(self, cx: float, cy: float, subs) -> Tuple[float, Optional[RGB]]:
        """(foreground share of the footprint, mean foreground colour or None). `snap` moves the sample point to the middle of the
        art pixel (an `snap` x `snap` cell of the reference grid) that contains it and, unless `center`, averages that whole cell;
        `center` reads the one reference pixel at the sample point instead of a footprint average."""
        if self.snap:
            n = float(self.snap)
            ox, oy = self.snap_origin
            cx = (math.floor((cx - ox) / n) + 0.5) * n + ox
            cy = (math.floor((cy - oy) / n) + 0.5) * n + oy
            if not self.center:
                subs = [(0.0, 0.0, n * 0.5, n * 0.5)]
        if self.center:
            ix, iy = int(math.floor(cx)), int(math.floor(cy))
            if 0 <= ix < self.W and 0 <= iy < self.H and self.mask[iy * self.W + ix]:
                return 1.0, self.view.color_at(ix + 0.5, iy + 0.5)
            return 0.0, None
        tab = self.tab
        cnt = sr = sg = sb = area = 0.0
        for ox, oy, hx, hy in subs:
            x, y = cx + ox, cy + oy
            m, r, g, b = _box_sums(tab, x - hx, y - hy, x + hx, y + hy)
            cnt += m
            sr += r
            sg += g
            sb += b
            area += 4.0 * hx * hy
        if cnt < 1e-6:
            return 0.0, None
        return min(1.0, cnt / area), (min(255, int(sr / cnt + 0.5)), min(255, int(sg / cnt + 0.5)), min(255, int(sb / cnt + 0.5)))

    def _interior(self, ix: int, iy: int) -> bool:
        W, H, mask = self.W, self.H, self.mask
        if not (0 < ix < W - 1 and 0 < iy < H - 1):
            return False
        o = iy * W + ix
        return bool(mask[o] and mask[o - 1] and mask[o + 1] and mask[o - W] and mask[o + W])

    def nearest_interior(self, cx: float, cy: float, hx: float, hy: float) -> Optional[RGB]:
        """Colour of the foreground pixel nearest to (cx, cy), preferring pixels whose four neighbours are foreground too
        (so no edge-blended colour is used), searched in rings up to a few footprints away."""
        W, H, mask = self.W, self.H, self.mask
        reach = min(12, int(max(hx, hy) * 2.0) + 3)
        ix0, iy0 = int(math.floor(cx)), int(math.floor(cy))
        plain = None  # type: Optional[Tuple[float, int, int]]
        for r in range(reach + 1):
            best = None  # type: Optional[Tuple[float, int, int]]
            if r == 0:
                ring = [(ix0, iy0)]
            else:
                ring = [(x, iy0 - r) for x in range(ix0 - r, ix0 + r + 1)] + [(x, iy0 + r) for x in range(ix0 - r, ix0 + r + 1)]
                ring += [(ix0 - r, y) for y in range(iy0 - r + 1, iy0 + r)] + [(ix0 + r, y) for y in range(iy0 - r + 1, iy0 + r)]
            for ix, iy in ring:
                if not (0 <= ix < W and 0 <= iy < H) or not mask[iy * W + ix]:
                    continue
                d2 = (ix + 0.5 - cx) ** 2 + (iy + 0.5 - cy) ** 2
                cand = (d2, ix, iy)
                if plain is None or cand < plain:
                    plain = cand
                if self._interior(ix, iy) and (best is None or cand < best):
                    best = cand
            if best is not None:
                plain = best
                break
        if plain is None:
            return None
        _d2, ix, iy = plain
        x, y = ix + 0.5, iy + 0.5
        m, r, g, b = _box_sums(self.tab, x - hx, y - hy, x + hx, y + hy)
        if m < 1e-6:
            c = self.view.color_at(x, y)
            return (c[0], c[1], c[2])
        return (min(255, int(r / m + 0.5)), min(255, int(g / m + 0.5)), min(255, int(b / m + 0.5)))


# --------------------------------------------------------------------------- the plan
class BakePlan:
    """A validated bake configuration plus its views; `apply` bakes into painter texels."""

    def __init__(self, views: Dict[str, Any], cfg: dict):
        self.views = views  # name -> reference.View (only the baked ones, in bake order)
        self.cfg = cfg
        self.vbs = []  # type: List[_ViewBake]
        self._ctx = None  # type: Optional[tuple]
        self.stats = None  # type: Optional[dict]
        self.kinds = None  # type: Optional[bytearray]

    # ---- targeting
    def _eligible(self, texels) -> list:
        cfg = self.cfg
        sel = None if "*" in cfg["regions"] else RegionSel(cfg["regions"])
        excl = RegionSel(cfg["exclude"]) if cfg["exclude"] else None
        out = []
        for t in texels:
            if t.rgb is None:
                continue
            if sel is not None and not sel.match(t):
                continue
            if excl is not None and excl.match(t):
                continue
            out.append(t)
        return out

    # ---- the bake
    def apply(self, texels, cover, mesh, atlas: Tuple[int, int], uv_origin: str, front_sign: int, right_sign: int) -> dict:
        """Bake into `texels` (their `rgb` is replaced / blended); returns the statistics dict (incl. `warnings`)."""
        from . import efmesh
        cfg = self.cfg
        fx = 1.0 if right_sign >= 0 else -1.0  # model space of the contract: character right = +x, faces -z
        fz = -1.0 if front_sign >= 0 else 1.0
        positions = [(fx * p[0], p[1], fz * p[2]) for p in mesh.positions]
        normals_t = [(fx * n[0], n[1], fz * n[2]) for n in mesh.normals]
        self._ctx = (positions, normals_t, atlas, uv_origin, fx, fz)
        self.vbs = [_ViewBake(name, self.views[name], positions, mesh.tris, normals_t) for name in cfg["views"]]
        for vb in self.vbs:
            vb.shrink = cfg["footprint"]
            vb.center = cfg["sample"] == "center"
            vb.snap = cfg["snap"]
            vb.snap_origin = (cfg["snap_origin"][0], cfg["snap_origin"][1])
        vbs = self.vbs
        eligible = self._eligible(texels)
        min_facing = cfg["min_facing"]
        cutoff = min(min_facing, SMEAR_MIN_FACING) if cfg["smear"] else min_facing
        tol = cfg["depth_tolerance"]
        aw, ah = atlas
        uvs = mesh.uvs
        tang = {}  # type: Dict[int, Optional[tuple]]

        def tangents(ti: int):
            got = tang.get(ti, 0)
            if got != 0:
                return got
            tri = mesh.tris[ti]
            p0, p1, p2 = (positions[i] for i in tri.p)
            (u0, v0), (u1, v1), (u2, v2) = [efmesh.uv_to_pixel(uvs[i][0], uvs[i][1], aw, ah, uv_origin) for i in tri.t]
            du1, dv1, du2, dv2 = u1 - u0, v1 - v0, u2 - u0, v2 - v0
            det = du1 * dv2 - du2 * dv1
            if abs(det) < 1e-12:
                tang[ti] = None
                return None
            e1 = (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2])
            e2 = (p2[0] - p0[0], p2[1] - p0[1], p2[2] - p0[2])
            tu = tuple((e1[k] * dv2 - e2[k] * dv1) / det for k in range(3))
            tv = tuple((e2[k] * du1 - e1[k] * du2) / det for k in range(3))
            tang[ti] = (tu, tv)
            return tang[ti]

        baked = []  # type: List[Tuple[Any, RGB]]
        baked_src = []  # type: List[Tuple[str, int]]  # (kind, view index) of every baked entry
        todo = []  # type: List[Any]
        state = {"direct": 0, "edge": 0, "smeared": 0, "mask_miss": 0, "occluded": 0, "unseen": 0}
        kinds = bytearray(aw * ah)  # per atlas texel: 1 direct, 2 edge, 3 smeared, 4 filled, 0 left procedural
        for t in eligible:
            ti = cover[(t.x, t.y)][0]
            tg = tangents(ti)
            n = t.n
            nx, ny, nz = fx * n[0], n[1], fz * n[2]
            cands = []
            for vi, vb in enumerate(vbs):
                md = vb.md
                f = nx * md[0] + ny * md[1] + nz * md[2]
                if f >= cutoff:
                    cands.append((-round(f, 2), vi, f))
            if tg is None or not cands:
                state["unseen"] += 1
                todo.append(t)
                continue
            cands.sort()
            p = t.pos
            X, Y, Z = fx * p[0], p[1], fz * p[2]
            result = None  # (kind, rgb, vi)
            edge = None
            seen_visible = False
            seen_occluded = False
            for _k, vi, f in cands:
                vb = vbs[vi]
                grazing = f < min_facing
                if grazing and (edge is not None):
                    break  # a front-facing edge sample beats a grazing direct sample
                if not grazing:
                    vb.stat["facing"] += 1
                subs, probes, slope = vb.footprint(ti, tg[0], tg[1])
                mx, my, md = vb.mx, vb.my, vb.md
                cx = vb.o[0] + mx[0] * X + mx[1] * Y + mx[2] * Z
                cy = vb.o[1] + my[0] * X + my[1] * Y + my[2] * Z
                cd = vb.o[2] + md[0] * X + md[1] * Y + md[2] * Z
                if not vb.visible(cx, cy, cd, probes, tol + slope):
                    seen_occluded = True
                    if not grazing:
                        vb.stat["occluded"] += 1
                    continue
                seen_visible = True
                frac, col = vb.sample(cx, cy, subs)
                if col is not None and frac >= MIN_DIRECT_FG:
                    result = ("smeared" if grazing else "direct", col, vi)
                    break
                if edge is None:
                    hx, hy = subs[0][2], subs[0][3]
                    if len(subs) > 1:
                        hx, hy = max(s[2] for s in subs) + max(abs(s[0]) for s in subs), max(s[3] for s in subs) + max(abs(s[1]) for s in subs)
                    ecol = vb.nearest_interior(cx, cy, hx, hy)
                    if ecol is not None:
                        edge = ("smeared" if grazing else "edge", ecol, vi)
                    elif not grazing:
                        vb.stat["mask_miss"] += 1
            if result is None:
                result = edge
            if result is not None:
                kind, col, vi = result
                vbs[vi].stat[kind] += 1
                state[kind] += 1
                kinds[t.y * aw + t.x] = KIND_CODE[kind]
                baked.append((t, col))
                baked_src.append((kind, vi))
            else:
                todo.append(t)
                if seen_visible:
                    state["mask_miss"] += 1
                elif seen_occluded:
                    state["occluded"] += 1
                else:
                    state["unseen"] += 1
        fills = {}  # type: Dict[int, Tuple[RGB, float]]
        if cfg["smear"] and todo and baked:
            fills = _fill_graph(todo, baked)
        filled = []
        for t in todo:
            got = fills.get(id(t))
            if got is not None:
                c, s = got
                old = t.rgb
                if s < 1.0 and old is not None:
                    c = (int(old[0] + (c[0] - old[0]) * s + 0.5), int(old[1] + (c[1] - old[1]) * s + 0.5), int(old[2] + (c[2] - old[2]) * s + 0.5))
                filled.append((t, c))
                kinds[t.y * aw + t.x] = 4
        results = baked + filled
        if cfg["palette"]:
            hist = {}  # type: Dict[RGB, int]
            for _t, c in results:
                hist[c] = hist.get(c, 0) + 1
            # sqrt weights: a face's few eye / mouth / ornament colours keep palette entries against the wing gradient's thousands
            table = median_cut({c: max(1, int(math.sqrt(n) + 0.5)) for c, n in hist.items()}, cfg["palette"])
            results = [(t, table[c]) for t, c in results]
        blend = cfg["blend"]
        for t, c in results:
            old = t.rgb
            if blend >= 1.0 or old is None:
                t.rgb = c
            else:
                t.rgb = (int(old[0] + (c[0] - old[0]) * blend + 0.5), int(old[1] + (c[1] - old[1]) * blend + 0.5),
                         int(old[2] + (c[2] - old[2]) * blend + 0.5))
        self.kinds = kinds
        self.stats = self._stats(len(eligible), state, len(filled))
        self.stats["seams"] = _seam_stats(baked, baked_src)
        return self.stats

    def _stats(self, total: int, state: dict, filled: int) -> dict:
        views = {}
        for vb in self.vbs:
            s = vb.stat
            views[vb.name] = {"facing": s["facing"], "direct": s["direct"], "edge": s["edge"], "smeared": s["smeared"],
                              "occluded": s["occluded"], "mask_miss": s["mask_miss"],
                              "left_procedural": max(0, s["facing"] - s["direct"] - s["edge"])}
        baked = state["direct"] + state["edge"] + state["smeared"]
        visible = state["direct"] + state["edge"] + state["mask_miss"]
        ratio = state["direct"] / visible if visible else 0.0
        procedural = total - baked - filled
        warnings = []  # type: List[dict]
        if visible == 0:
            warnings.append(Err("/reference/bake", "bake_nothing_visible",
                                "no texel is visible to any bake view (check the view calibration with `bossforge fit`, the bake `views` and `regions`)").to_dict())
        elif ratio < LOW_COVERAGE:
            warnings.append(Err("/reference/bake", "bake_low_coverage",
                                "only %d%% of the %d visible texels got a direct sample (%d fell on reference background, i.e. the model sticks out of the "
                                "reference silhouette there, %d took the nearest silhouette colour): the calibration or the geometry does not match the "
                                "reference; run `bossforge fit` and fix px_per_block / center_x / ground_y or the shape first"
                                % (int(ratio * 100 + 0.5), visible, state["mask_miss"], state["edge"])).to_dict())
        for vb in self.vbs:
            s = vb.stat
            if s["facing"] and s["direct"] == 0:
                warnings.append(Err("/reference/bake/views", "bake_view_unused",
                                    "view %r sampled no texel directly (%d texels face it, %d occluded, %d on background): check its calibration with `bossforge fit`"
                                    % (vb.name, s["facing"], s["occluded"], s["mask_miss"])).to_dict())
        return {
            "views": views,
            "total": {"texels": total, "direct": state["direct"], "edge": state["edge"], "smeared": state["smeared"],
                      "filled": filled, "procedural": procedural, "visible": visible, "mask_miss": state["mask_miss"],
                      "occluded": state["occluded"], "unseen": state["unseen"]},
            "direct_ratio": round(ratio, 4),
            "settings": {k: self.cfg[k] for k in BAKE_KEYS},
            "warnings": warnings,
        }

    # ---- review images + numbers
    def compare(self, mesh, canvas: Canvas, base_id: str):
        """(files, metrics): for every bake view a PNG [reference | model with the final texture | difference] and the
        colour error of the pixels both show (only pixels of faces with facing >= min_facing). Needs `apply` first."""
        from .spec import GenFile
        from .pngio import encode_png
        files = []
        metrics = {}
        if self._ctx is None:
            return files, metrics
        positions, _normals_t, atlas, uv_origin, _fx, _fz = self._ctx
        for vb in self.vbs:
            panel, m = _compare_view(vb, mesh, canvas, atlas, uv_origin, self.cfg["min_facing"], self.kinds)
            rel = "bossforge/previews/%s_bake_%s.png" % (base_id, vb.name)
            files.append(GenFile(rel, encode_png(panel), "preview"))
            metrics[vb.name] = dict(m, image=rel)
        return files, metrics


# --------------------------------------------------------------------------- seams and fill
def _seam_stats(baked, src) -> dict:
    """Mean colour difference (0-255 per channel) across box edges between directly baked texels, split into edges both
    of whose sides came from the SAME view (what the reference itself has) and from DIFFERENT views (what baking from two
    images adds: lighting or registration differences between the views)."""
    items = [(t, c, vi) for (t, c), (kind, vi) in zip(baked, src) if kind in ("direct", "edge")]
    out = {"same_view_pairs": 0, "same_view_mean_delta": None, "cross_view_pairs": 0, "cross_view_mean_delta": None}
    if len(items) < 2:
        return out
    adj, grp = _surface_graph([it[0] for it in items])
    acc = {True: [0, 0.0], False: [0, 0.0]}  # same view? -> [pairs, sum of deltas]
    for i, lst in enumerate(adj):
        ci, vi = items[i][1], items[i][2]
        for j in lst:
            if j <= i or grp[j] == grp[i]:
                continue
            cj = items[j][1]
            a = acc[items[j][2] == vi]
            a[0] += 1
            a[1] += (abs(ci[0] - cj[0]) + abs(ci[1] - cj[1]) + abs(ci[2] - cj[2])) / 3.0
    for key, same in (("same_view", True), ("cross_view", False)):
        out[key + "_pairs"] = acc[same][0]
        out[key + "_mean_delta"] = round(acc[same][1] / acc[same][0], 2) if acc[same][0] else None
    return out


def _surface_graph(nodes):
    """(adjacency lists, face group ids) of the texels `nodes`: 8-neighbours inside one face rectangle of the atlas, plus
    links between border texels of different faces of one part that are closer than 1.75 texels in 3D (the two sides of
    a box edge). A face group is one (part, normal)."""
    idx = {(t.x, t.y): i for i, t in enumerate(nodes)}
    gids = {}  # type: Dict[tuple, int]
    grp = []  # type: List[int]
    for t in nodes:
        n = t.n
        key = (t.part, round(n[0], 2), round(n[1], 2), round(n[2], 2))
        g = gids.get(key)
        if g is None:
            g = gids[key] = len(gids)
        grp.append(g)
    adj = [[] for _ in nodes]  # type: List[List[int]]
    border = []  # type: List[int]
    for i, t in enumerate(nodes):
        x, y, g = t.x, t.y, grp[i]
        for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
            j = idx.get((x + dx, y + dy))
            if j is not None and grp[j] == g:
                adj[i].append(j)
                adj[j].append(i)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            j = idx.get((x + dx, y + dy))
            if j is None or grp[j] != g:
                border.append(i)
                break
    # links across box edges: border texels of different faces of one part that touch in 3D
    part_tb = {}  # type: Dict[str, float]
    for i in border:
        t = nodes[i]
        if t.tb > part_tb.get(t.part, 0.0):
            part_tb[t.part] = t.tb
    cells = {}  # type: Dict[tuple, List[int]]
    for i in border:
        t = nodes[i]
        inv = 1.0 / (2.0 * part_tb[t.part])
        p = t.pos
        cells.setdefault((t.part, int(math.floor(p[0] * inv)), int(math.floor(p[1] * inv)), int(math.floor(p[2] * inv))), []).append(i)
    for i in border:
        t = nodes[i]
        inv = 1.0 / (2.0 * part_tb[t.part])
        p = t.pos
        reach2 = (1.75 * t.tb) ** 2
        ci, cj, ck = int(math.floor(p[0] * inv)), int(math.floor(p[1] * inv)), int(math.floor(p[2] * inv))
        for a in (ci - 1, ci, ci + 1):
            for b in (cj - 1, cj, cj + 1):
                for c in (ck - 1, ck, ck + 1):
                    lst = cells.get((t.part, a, b, c))
                    if not lst:
                        continue
                    for j in lst:
                        if j <= i or grp[j] == grp[i]:
                            continue
                        q = nodes[j].pos
                        if (q[0] - p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2 <= reach2:
                            adj[i].append(j)
                            adj[j].append(i)
    return adj, grp


def _fill_graph(todo, baked) -> Dict[int, Tuple[RGB, float]]:
    """id(texel) -> (colour, strength 0..1) for the unbaked texels reachable from baked texels of the same part along the
    surface (`_surface_graph`). A multi-source breadth-first search carries the colour of up to FILL_GROUPS baked faces
    (distinct source faces) to every texel within FILL_BLOCKS blocks; a texel blends its labels with weight 1 / hops^2. So
    a face half hidden behind an arm takes its visible neighbours, and a side face takes the blend of the colours of the
    two faces it joins."""
    nodes = [t for t, _c in baked] + list(todo)
    nb = len(baked)
    adj, grp = _surface_graph(nodes)
    rad = [max(2, int(FILL_BLOCKS / max(t.tb, 1e-6) + 0.5)) for t in nodes]
    labels = [None] * len(nodes)  # type: List[Optional[list]]
    queue = deque()
    for i in range(nb):
        queue.append((i, grp[i], 0, baked[i][1]))
    while queue:
        i, g, d, c = queue.popleft()
        for j in adj[i]:
            if j < nb or d + 1 > rad[j]:
                continue
            lab = labels[j]
            if lab is None:
                lab = labels[j] = []
            elif len(lab) >= FILL_GROUPS or any(l[0] == g for l in lab):
                continue
            lab.append((g, d + 1, c))
            queue.append((j, g, d + 1, c))
    out = {}  # type: Dict[int, Tuple[RGB, float]]
    for j in range(nb, len(nodes)):
        lab = labels[j]
        if not lab:
            continue
        wsum = r_ = g_ = b_ = 0.0
        for _g, d, c in lab:
            w = 1.0 / (d * d)
            wsum += w
            r_ += w * c[0]
            g_ += w * c[1]
            b_ += w * c[2]
        # full strength up to half the reach, then fading into the procedural colour (no hard seam at the limit)
        strength = min(1.0, 2.0 * (1.0 - (lab[0][1] - 1) / float(rad[j])))
        out[id(nodes[j])] = ((int(r_ / wsum + 0.5), int(g_ / wsum + 0.5), int(b_ / wsum + 0.5)), strength)
    return out


# --------------------------------------------------------------------------- review rendering
def _render_model(vb: _ViewBake, mesh, canvas: Canvas, atlas, uv_origin: str, min_facing: float, kinds, rx0: int, ry0: int, rw: int, rh: int):
    """The model seen through the view with `canvas` as its texture, as RGBA bytes of the image rectangle
    (rx0, ry0, rw, rh) (alpha 255 where a front-facing surface is visible), plus a byte per pixel that is 1 where the
    surface shown is a texel baked directly (or from the silhouette edge) on a face that looks at the view
    (>= min_facing). `kinds` is the per-texel bake map of `BakePlan.kinds` (None: facing alone decides)."""
    from . import efmesh
    bw, bh, zb = vb.bw, vb.bh, vb.zb
    out = bytearray(rw * rh * 4)
    good = bytearray(rw * rh)
    aw, ah = atlas
    uvs = mesh.uvs
    cw, ch, cdata = canvas.w, canvas.h, canvas.data
    ox, oy = vb.bx0 - rx0, vb.by0 - ry0
    for ti in vb.tris:
        tri = mesh.tris[ti]
        scr = vb.tri_screen(tri)
        gx, spans = _spans(*scr, bw, bh)
        if not spans:
            continue
        sx0, sy0, _d0, sx1, sy1, _d1, sx2, sy2, _d2 = scr
        (u0, v0), (u1, v1), (u2, v2) = [efmesh.uv_to_pixel(uvs[i][0], uvs[i][1], aw, ah, uv_origin) for i in tri.t]
        det = (sx1 - sx0) * (sy2 - sy0) - (sx2 - sx0) * (sy1 - sy0)
        dudx = ((u1 - u0) * (sy2 - sy0) - (u2 - u0) * (sy1 - sy0)) / det
        dudy = ((u2 - u0) * (sx1 - sx0) - (u1 - u0) * (sx2 - sx0)) / det
        dvdx = ((v1 - v0) * (sy2 - sy0) - (v2 - v0) * (sy1 - sy0)) / det
        dvdy = ((v2 - v0) * (sx1 - sx0) - (v1 - v0) * (sx2 - sx0)) / det
        ok = 1 if vb.tri_facing[ti] >= min_facing else 0
        for j, i0, i1, zs in spans:
            n = i1 - i0 + 1
            zrow = [zs] * n if gx == 0.0 else list(_span_depths(zs, gx, n))
            zref = zb[j * bw + i0:j * bw + i1 + 1]
            dy = j + 0.5 - sy0
            row = (j + oy) * rw + ox + i0
            for k in range(n):
                if zrow[k] < zref[k] - 1e-9:
                    continue
                dx = i0 + k + 0.5 - sx0
                tx = min(max(int(u0 + dudx * dx + dudy * dy), 0), cw - 1)
                ty = min(max(int(v0 + dvdx * dx + dvdy * dy), 0), ch - 1)
                s = (ty * cw + tx) * 4
                pix = row + k
                o = pix * 4
                out[o:o + 3] = cdata[s:s + 3]
                out[o + 3] = 255
                good[pix] = ok if kinds is None or kinds[ty * cw + tx] in (1, 2) else 0
    return out, good


def _compare_view(vb: _ViewBake, mesh, canvas: Canvas, atlas, uv_origin: str, min_facing: float, kinds):
    view = vb.view
    # region shown: the model's projection united with the reference crop, inside the image
    cx0, cy0, cx1, cy1 = view.crop
    rx0 = max(0, min(vb.bx0, cx0))
    ry0 = max(0, min(vb.by0, cy0))
    rx1 = min(view.width, max(vb.bx0 + vb.bw, cx1))
    ry1 = min(view.height, max(vb.by0 + vb.bh, cy1))
    rw, rh = max(1, rx1 - rx0), max(1, ry1 - ry0)
    out, good = _render_model(vb, mesh, canvas, atlas, uv_origin, min_facing, kinds, rx0, ry0, rw, rh)
    rgba, mask, W = view.rgba, view.mask, view.width
    ref_only = model_only = both = 0
    err_sum = 0
    close = 0
    n_good = 0
    diff = bytearray(rw * rh * 4)
    gm = bytearray(rw * rh)  # 1: baked texel shown where the reference is foreground
    ref_panel = bytearray(rw * rh * 4)
    for y in range(rh):
        for x in range(rw):
            src = (y + ry0) * W + (x + rx0)
            i = (y * rw + x) * 4
            fg = mask[src] != 0
            si = src * 4
            if fg:
                ref_panel[i:i + 3] = rgba[si:si + 3]
            else:
                ref_panel[i] = rgba[si] >> 2
                ref_panel[i + 1] = rgba[si + 1] >> 2
                ref_panel[i + 2] = rgba[si + 2] >> 2
            ref_panel[i + 3] = 255
            has = out[i + 3] != 0
            if fg and has:
                both += 1
                e = max(abs(out[i] - rgba[si]), abs(out[i + 1] - rgba[si + 1]), abs(out[i + 2] - rgba[si + 2]))
                if good[y * rw + x]:
                    n_good += 1
                    gm[y * rw + x] = 1
                    err_sum += (abs(out[i] - rgba[si]) + abs(out[i + 1] - rgba[si + 1]) + abs(out[i + 2] - rgba[si + 2]))
                    if e <= 24:
                        close += 1
                v = min(255, e * 3)
                diff[i] = diff[i + 1] = diff[i + 2] = v
            elif fg:
                ref_only += 1
                diff[i], diff[i + 1], diff[i + 2] = 40, 80, 255
            elif has:
                model_only += 1
                diff[i], diff[i + 1], diff[i + 2] = 255, 60, 60
            diff[i + 3] = 255
            if not has:
                out[i], out[i + 1], out[i + 2] = ref_panel[i] >> 1, ref_panel[i + 1] >> 1, ref_panel[i + 2] >> 1
                out[i + 3] = 255
    # resolution-matched error: the mean colour of every texel-sized block (blocks fully on baked texels over foreground);
    # detail finer than a texel cannot be copied, so this is the number that says whether the COLOURS are right
    tk = max(1, int(round(vb.px_per_texel())))
    blur_sum = 0
    blur_n = 0
    for ty0 in range(0, rh - tk + 1, tk):
        for tx0 in range(0, rw - tk + 1, tk):
            ps = [(ty0 + yy) * rw + tx0 + xx for yy in range(tk) for xx in range(tk)]
            if not all(gm[p] for p in ps):
                continue
            for c in range(3):
                blur_sum += abs(sum(out[p * 4 + c] for p in ps) - sum(ref_panel[p * 4 + c] for p in ps))
            blur_n += 3 * len(ps)
    panels = [Canvas(rw, rh, ref_panel), Canvas(rw, rh, out), Canvas(rw, rh, diff)]
    k = max(1, int(math.ceil(max(rw, rh) / 720.0)))
    if k > 1:
        panels = [_decimate(p, k) for p in panels]
    sheet = hconcat(panels, gap=4, bg=(20, 22, 28, 255))
    metrics = {"compared_px": both, "baked_px": n_good, "unbaked_px": both - n_good,
               "mean_abs_error": round(err_sum / (3.0 * n_good), 2) if n_good else None,
               "within_24_share": round(close / n_good, 4) if n_good else None,
               "texel_block_px": tk, "texel_block_mean_abs_error": round(blur_sum / float(blur_n), 2) if blur_n else None,
               "reference_only_px": ref_only, "model_only_px": model_only, "scale_down": k}
    return sheet, metrics


def _decimate(c: Canvas, k: int) -> Canvas:
    w, h = c.w // k, c.h // k
    out = Canvas(max(1, w), max(1, h))
    d, o = c.data, out.data
    for y in range(out.h):
        for x in range(out.w):
            s = ((y * k) * c.w + x * k) * 4
            i = (y * out.w + x) * 4
            o[i:i + 4] = d[s:s + 4]
    return out


# --------------------------------------------------------------------------- entry points
def plan_for(n: dict) -> Optional[BakePlan]:
    """The bake plan of a normalised spec, None when it has no `reference.bake`. Raises BossForgeError when a bake view
    is not defined or the reference cannot be loaded."""
    ref = n.get("reference")
    if not isinstance(ref, dict) or not isinstance(ref.get("bake"), dict):
        return None
    from . import reference
    views = reference.views_for(n)
    cfg = normalize_bake(ref["bake"], list(views))
    if isinstance(n.get("mesh"), dict):  # imported mesh: `box:<glob>` targets name its groups
        from . import meshsrc
        cfg["regions"] = meshsrc.retarget(cfg["regions"])
        cfg["exclude"] = meshsrc.retarget(cfg["exclude"])
    missing = [v for v in cfg["views"] if v not in views]
    if missing:
        raise BossForgeError("/reference/bake/views", "reference_unknown_view",
                             "bake view(s) %s are not defined in reference.views (defined: %s)" % (", ".join(missing), ", ".join(views) or "none"))
    return BakePlan({v: views[v] for v in cfg["views"]}, cfg)
