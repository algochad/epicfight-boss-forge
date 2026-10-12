"""Character-modelling kit for the Epic Fight biped rig: build a game-ready mesh from code, deterministic, no operators.

Everything geometric (primitives, UV islands and atlas packing, weight rules) is PURE PYTHON (stdlib only, importable
and testable without bpy); only the functions in the last section (`to_blender`, `bind_rig`, `evaluated_points`,
`points_inside`, ...) import bpy lazily.

Space (the same as `bfk.rig`): Blender world / storage space, Z up, the character faces +Y, its right is +X, feet at
z = 0, 1 unit = 1 block. A primitive's vertex order is made OUTWARD-facing automatically (signed-volume / ring tests),
so callers never think about winding.

Workflow
--------
    m = Model(["skin", "robe", "wing"], atlas=1024, density={"skin": 96, "robe": 64, "wing": 96})
    block(m, (-.1, -.1, 0), (.1, .1, .4), "skin", "Thigh_R")                     # rigid to one joint
    mark = m.mark()
    sweep(m, path, widths, thick, "wing", chain_rule, side=(1, 0, 0))            # curved feather card, weight RULE
    m.mirror(mark)                                                               # x-mirrored copy (joints _R <-> _L)
    built = m.build()                                                            # normalise weights, UV islands, pack
    obj = to_blender(built, "Body", rig=rig_obj, colors={"skin": (.9, .7, .6)})  # Blender mesh + slots + groups + armature

Weights: a weight spec is a joint name ("Chest"), a dict {"Chest": .7, "Arm_R": .3}, or a RULE: a callable
`rule(point) -> dict` evaluated per vertex when the vertex is created. `rule_blend` / `rule_chain` / `rule_mix` combine
joints with smooth gradients (smoothstep); `normalize_weights` prunes to <= 3 influences and renormalises, so every
vertex sums to exactly 1.

UV: one island per planar face / per loft strip (unrolled with true lengths), upright in the image (image-up = world up;
for horizontal faces image-up = +Y front), packed into the atlas at a UNIFORM texel density per group (texels per block,
`Model.density[group]` x the primitive's `density` multiplier x one global fit factor), island sizes snapped to whole
texels with a margin. UVs are Blender UVs (v bottom-up); an EF export flips v.
"""
from __future__ import annotations

import math
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

Vec = Tuple[float, float, float]
WeightSpec = Union[str, Dict[str, float], Callable[[Vec], Dict[str, float]]]

EPS = 1e-9
MAX_INFLUENCES = 3  # stock EF meshes use <= 3; the exporter allows 4


# ============================================================================================ vector maths
def vadd(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vsub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vmul(a: Sequence[float], s: float) -> Vec:
    return (a[0] * s, a[1] * s, a[2] * s)


def vdot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def vlen(a: Sequence[float]) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def vnorm(a: Sequence[float], fallback: Vec = (0.0, 0.0, 1.0)) -> Vec:
    n = vlen(a)
    return (a[0] / n, a[1] / n, a[2] / n) if n > EPS else fallback


def vlerp(a: Sequence[float], b: Sequence[float], t: float) -> Vec:
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return lo if x < lo else hi if x > hi else x


def smoothstep(e0: float, e1: float, x: float) -> float:
    """0 at e0, 1 at e1, smooth (3t^2 - 2t^3) between; works for e0 > e1 (descending)."""
    if e0 == e1:
        return 1.0 if x >= e1 else 0.0
    t = clamp((x - e0) / (e1 - e0))
    return t * t * (3.0 - 2.0 * t)


def centroid(points: Sequence[Sequence[float]]) -> Vec:
    n = float(len(points))
    return (sum(p[0] for p in points) / n, sum(p[1] for p in points) / n, sum(p[2] for p in points) / n)


def newell(points: Sequence[Sequence[float]]) -> Vec:
    """Newell normal (unnormalised, length = 2 x area) of a polygon; counter-clockwise seen from the tip = positive."""
    nx = ny = nz = 0.0
    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        nx += (a[1] - b[1]) * (a[2] + b[2])
        ny += (a[2] - b[2]) * (a[0] + b[0])
        nz += (a[0] - b[0]) * (a[1] + b[1])
    return (nx, ny, nz)


def ear_clip(points: Sequence[Sequence[float]], eps: float = 1e-9) -> List[Tuple[int, int, int]]:
    """Triangulate a simple planar polygon (any winding, convex or concave) by ear clipping; returns index triples into
    `points` that keep the polygon's winding. Vertices that lie on the line between their neighbours are dropped (no zero-area
    triangles); n-gons with collinear runs (the C-shaped cap of a skirt wall, subdivided faces) therefore triangulate cleanly."""
    n = len(points)
    if n < 3:
        return []
    nrm = vnorm(newell(points))
    ax = (1.0, 0.0, 0.0) if abs(nrm[0]) < 0.9 else (0.0, 1.0, 0.0)
    ux = vnorm(vcross(nrm, ax))
    uy = vcross(nrm, ux)
    p2 = [(vdot(p, ux), vdot(p, uy)) for p in points]

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])

    scale = max(max(abs(c) for p in p2 for c in p), 1.0)
    idx = list(range(n))
    out: List[Tuple[int, int, int]] = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n * n:
        guard += 1
        m = len(idx)
        clipped = False
        for k in range(m):
            ia, ib, ic = idx[k - 1], idx[k], idx[(k + 1) % m]
            c = cross(p2[ia], p2[ib], p2[ic])
            if abs(c) <= eps * scale * scale:  # collinear: drop the middle vertex
                del idx[k]
                clipped = True
                break
            if c <= 0.0:  # reflex vertex
                continue
            tri = (p2[ia], p2[ib], p2[ic])
            if any(j not in (ia, ib, ic) and _in_tri(p2[j], tri) for j in idx):
                continue
            out.append((ia, ib, ic))
            del idx[k]
            clipped = True
            break
        if not clipped:  # numerically stuck: fan the rest
            break
    if len(idx) >= 3:
        out.extend((idx[0], idx[k], idx[k + 1]) for k in range(1, len(idx) - 1)
                   if abs(cross(p2[idx[0]], p2[idx[k]], p2[idx[k + 1]])) > eps * scale * scale)
    return out


def _in_tri(p, tri) -> bool:
    a, b, c = tri
    d1 = (p[0] - b[0]) * (a[1] - b[1]) - (a[0] - b[0]) * (p[1] - b[1])
    d2 = (p[0] - c[0]) * (b[1] - c[1]) - (b[0] - c[0]) * (p[1] - c[1])
    d3 = (p[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (p[1] - a[1])
    neg = d1 < -1e-12 or d2 < -1e-12 or d3 < -1e-12
    pos = d1 > 1e-12 or d2 > 1e-12 or d3 > 1e-12
    return not (neg and pos) and not (not neg and not pos)


def euler_matrix(rx: float, ry: float, rz: float) -> Tuple[Vec, Vec, Vec]:
    """Rotation matrix rows for Euler angles in DEGREES, applied X then Y then Z (Blender's default XYZ order)."""
    ax, ay, az = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    return ((cy * cz, sx * sy * cz - cx * sz, cx * sy * cz + sx * sz),
            (cy * sz, sx * sy * sz + cx * cz, cx * sy * sz - sx * cz),
            (-sy, sx * cy, cx * cy))


def rotate_points(points: Sequence[Sequence[float]], pivot: Sequence[float], rot: Sequence[float]) -> List[Vec]:
    """Rotate points about `pivot` by Euler degrees `rot` (x, y, z)."""
    if not any(rot):
        return [tuple(p) for p in points]  # type: ignore[misc]
    m = euler_matrix(*rot)
    out = []
    for p in points:
        d = (p[0] - pivot[0], p[1] - pivot[1], p[2] - pivot[2])
        out.append((pivot[0] + vdot(m[0], d), pivot[1] + vdot(m[1], d), pivot[2] + vdot(m[2], d)))
    return out


def bezier(ctrl: Sequence[Sequence[float]], n: int) -> List[Vec]:
    """n+1 points of the Bezier curve through control points `ctrl` (any degree), t = 0..1 evenly."""
    pts = []
    for k in range(n + 1):
        t = k / float(n)
        tmp = [tuple(c) for c in ctrl]
        while len(tmp) > 1:
            tmp = [vlerp(tmp[i], tmp[i + 1], t) for i in range(len(tmp) - 1)]
        pts.append(tmp[0])
    return pts


def polyline_length(points: Sequence[Sequence[float]]) -> float:
    return sum(vlen(vsub(points[i + 1], points[i])) for i in range(len(points) - 1))


# ============================================================================================ rig data
_JOINTS: Dict[str, Vec] = {}


def joint_positions() -> Dict[str, Vec]:
    """EF biped joint rest positions in storage space (x, -z, y of rigs.json), pure Python."""
    if not _JOINTS:
        import bfk  # noqa: F401  (puts scripts/ on sys.path)
        from bossforge_core.spec import data
        for j in data("rigs")["biped"]["joints"]:
            x, y, z = j["pos"]
            _JOINTS[j["name"]] = (x, -z, y)
    return dict(_JOINTS)


# ============================================================================================ weights
SWAP_SUFFIX = (("_R", "_L"), ("_L", "_R"))


def swap_joint(name: str) -> str:
    """Thigh_R <-> Thigh_L (any name ending in _R / _L); other names unchanged."""
    for a, b in SWAP_SUFFIX:
        if name.endswith(a):
            return name[: -len(a)] + b
    return name


def normalize_weights(w: Dict[str, float], max_influences: int = MAX_INFLUENCES, min_weight: float = 0.01) -> Dict[str, float]:
    """Keep the `max_influences` largest weights (ties: name order), drop those < min_weight, renormalise to sum 1."""
    items = [(k, float(v)) for k, v in w.items() if v > 0.0]
    if not items:
        raise ValueError("vertex without any positive weight")
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    top = items[:max_influences]
    tot = sum(v for _, v in top)
    kept = [(k, v / tot) for k, v in top if v / tot >= min_weight] or [top[0]]
    tot = sum(v for _, v in kept)
    return {k: v / tot for k, v in sorted(kept, key=lambda kv: kv[0])}


def blend_weights(a: Dict[str, float], b: Dict[str, float], t: float) -> Dict[str, float]:
    """(1 - t) * a + t * b (joint-wise); the result sums to 1 when both inputs do."""
    out: Dict[str, float] = {}
    if t < 1.0:
        for k, v in a.items():
            out[k] = out.get(k, 0.0) + v * (1.0 - t)
    if t > 0.0:
        for k, v in b.items():
            out[k] = out.get(k, 0.0) + v * t
    return out


def _as_rule(spec: WeightSpec) -> Callable[[Vec], Dict[str, float]]:
    if isinstance(spec, str):
        d = {spec: 1.0}
        return lambda p: d
    if isinstance(spec, dict):
        tot = sum(spec.values())
        d2 = {k: v / tot for k, v in spec.items()}
        return lambda p: d2
    return spec


def rule_blend(axis: int, v0: float, v1: float, a: WeightSpec, b: WeightSpec, smooth: bool = True):
    """Rule: `a` where coordinate[axis] <= v0 ... `b` where >= v1, a (smoothstep) gradient between. v0 > v1 is fine."""
    ra, rb = _as_rule(a), _as_rule(b)

    def rule(p: Vec) -> Dict[str, float]:
        t = smoothstep(v0, v1, p[axis]) if smooth else clamp((p[axis] - v0) / (v1 - v0) if v1 != v0 else float(p[axis] >= v1))
        return blend_weights(ra(p), rb(p), t)
    return rule


def rule_chain(axis: int, stops: Sequence[Tuple[float, WeightSpec]], smooth: bool = True):
    """Rule along one axis through several (coordinate, spec) stops sorted by coordinate (ascending or descending): the
    weights at a point are the gradient between the two surrounding stops, constant beyond the ends. Example, an arm along
    z (descending): [(1.50, "Chest"), (1.40, "Arm_R"), (1.12, "Arm_R"), (1.06, "Hand_R"), (0.85, "Hand_R"), (0.80, "Tool_R")]."""
    if len(stops) < 2:
        raise ValueError("rule_chain needs >= 2 stops")
    rules = [(float(c), _as_rule(s)) for c, s in stops]
    asc = rules[-1][0] >= rules[0][0]

    def rule(p: Vec) -> Dict[str, float]:
        x = p[axis]
        if (x <= rules[0][0]) if asc else (x >= rules[0][0]):  # beyond the first stop
            return rules[0][1](p)
        for i in range(len(rules) - 1):
            c0, r0 = rules[i]
            c1, r1 = rules[i + 1]
            lo, hi = (c0, c1) if asc else (c1, c0)
            if lo <= x <= hi:
                if c0 == c1:
                    return r1(p)
                t = (x - c0) / (c1 - c0)
                t = t * t * (3.0 - 2.0 * t) if smooth else t
                return blend_weights(r0(p), r1(p), t)
        return rules[-1][1](p)
    return rule


def rule_mix(rule_a: WeightSpec, rule_b: WeightSpec, t_of: Callable[[Vec], float]):
    """Rule: lerp(rule_a, rule_b, t_of(point)) for any scalar field t_of in 0..1 (left/right x gradient, radial, ...)."""
    ra, rb = _as_rule(rule_a), _as_rule(rule_b)
    return lambda p: blend_weights(ra(p), rb(p), clamp(t_of(p)))


def evaluate_weights(spec: WeightSpec, p: Vec) -> Dict[str, float]:
    return dict(_as_rule(spec)(p))


# ============================================================================================ UV packing
def pack_rects(sizes: Sequence[Tuple[int, int]], atlas: int, margin: int = 2) -> Optional[List[Tuple[int, int]]]:
    """Shelf-pack integer rectangles (w, h in texels) into an atlas x atlas square, `margin` texels around each rectangle.

    Deterministic (sorted by height, then width, then index). Returns the top-left (x, y) of each rectangle in input order
    (rect interior, margin excluded) or None when they do not fit."""
    n = len(sizes)
    order = sorted(range(n), key=lambda i: (-sizes[i][1], -sizes[i][0], i))
    pos: List[Optional[Tuple[int, int]]] = [None] * n
    x = y = margin
    shelf_h = 0
    for i in order:
        w, h = sizes[i]
        if w + 2 * margin > atlas or h + 2 * margin > atlas:
            return None
        if x + w + margin > atlas:  # next shelf
            y += shelf_h + margin
            x = margin
            shelf_h = 0
        if y + h + margin > atlas:
            return None
        pos[i] = (x, y)
        x += w + margin
        shelf_h = max(shelf_h, h)
    return pos  # type: ignore[return-value]


def island_texels(w_blocks: float, h_blocks: float, density: float) -> Tuple[int, int]:
    """Whole-texel size of an island of w x h blocks at `density` texels per block (>= 1 each)."""
    return (max(1, int(round(w_blocks * density))), max(1, int(round(h_blocks * density))))


def fit_scale(dims: Sequence[Tuple[float, float, float]], atlas: int, margin: int = 2,
              lo: float = 0.2, hi: float = 1.0, iters: int = 14) -> float:
    """Largest uniform factor f in [lo, hi] such that islands (w_blocks, h_blocks, density) packed at density x f fit the atlas.

    Returns hi when everything fits at hi; raises ValueError when even lo does not fit."""
    def fits(f: float) -> bool:
        return pack_rects([island_texels(w, h, d * f) for w, h, d in dims], atlas, margin) is not None
    if fits(hi):
        return hi
    if not fits(lo):
        raise ValueError("UV islands do not fit a %dx%d atlas even at %.0f%% of the requested texel density" % (atlas, atlas, lo * 100))
    a, b = lo, hi
    for _ in range(iters):
        mid = 0.5 * (a + b)
        if fits(mid):
            a = mid
        else:
            b = mid
    return a


# ============================================================================================ model container
class Built:
    """Result of `Model.build()`: plain lists, ready for `to_blender` or tests."""
    __slots__ = ("positions", "weights", "faces", "uvs", "groups", "tags", "smooth", "group_names", "atlas", "margin", "scale", "island_count", "density_report")

    def __init__(self):
        self.positions: List[Vec] = []
        self.weights: List[Dict[str, float]] = []        # normalised per vertex
        self.faces: List[Tuple[int, ...]] = []           # vertex index tuples (tris / quads / n-gons)
        self.uvs: List[List[Tuple[float, float]]] = []   # per face, per corner, Blender UV (v up)
        self.groups: List[int] = []                      # per face: index into `Model.groups`
        self.tags: List[str] = []                        # per face tag
        self.smooth: List[bool] = []                     # per face smooth-shading flag
        self.group_names: List[str] = []                 # material slot / group names in slot order
        self.atlas = 0
        self.margin = 0
        self.scale = 1.0
        self.island_count = 0
        self.density_report: Dict[str, Dict[str, float]] = {}

    def triangles(self) -> int:
        return sum(len(f) - 2 for f in self.faces)


class Model:
    """Accumulates vertices (with weight specs) and face records; `build()` resolves UVs / weights."""

    def __init__(self, groups: Sequence[str], atlas: int = 1024, margin: int = 2,
                 density: Optional[Dict[str, float]] = None, default_density: float = 64.0):
        self.groups = list(groups)
        self.atlas = int(atlas)
        self.margin = int(margin)
        self.density = dict(density or {})
        self.default_density = float(default_density)
        self.pos: List[Vec] = []
        self.raw_w: List[Dict[str, float]] = []
        self.recs: List[dict] = []
        self.tag = ""  # current tag, copied onto every record created while it is set (`to_blender(tags=...)` selects by it)

    # ---- bookkeeping
    def mark(self) -> Tuple[int, int]:
        return (len(self.pos), len(self.recs))

    def group_index(self, group: str) -> int:
        try:
            return self.groups.index(group)
        except ValueError:
            raise KeyError("unknown group %r (groups: %s)" % (group, ", ".join(self.groups)))

    def vert(self, p: Sequence[float], w: WeightSpec) -> int:
        p3 = (float(p[0]), float(p[1]), float(p[2]))
        self.pos.append(p3)
        self.raw_w.append(evaluate_weights(w, p3))
        return len(self.pos) - 1

    def verts(self, pts: Sequence[Sequence[float]], w: WeightSpec) -> List[int]:
        return [self.vert(p, w) for p in pts]

    # ---- records
    def poly(self, vis: Sequence[int], group: str, up: Optional[Vec] = None, density: float = 1.0, smooth: bool = False) -> None:
        self.group_index(group)
        self.recs.append({"k": "poly", "vis": list(vis), "g": group, "up": up, "d": density, "s": smooth, "t": self.tag})

    def strip(self, rings: Sequence[Sequence[int]], group: str, closed: bool = True, up: Vec = (0.0, 0.0, 1.0),
              density: float = 1.0, smooth: bool = False, flat_cols: Iterable[int] = ()) -> None:
        self.group_index(group)
        self.recs.append({"k": "strip", "rings": [list(r) for r in rings], "closed": closed, "g": group, "up": up,
                          "d": density, "s": smooth, "flat": frozenset(flat_cols), "t": self.tag})

    # ---- mirroring
    def mirror(self, mark: Tuple[int, int], axis: int = 0, group_map: Optional[Dict[str, str]] = None) -> Tuple[int, int]:
        """Append the mirror (x -> -x for axis 0) of everything created since `mark`: weights' joints swapped _R <-> _L,
        windings flipped, UV islands rebuilt from the mirrored positions (so the copy is upright, not mirrored).
        Returns a mark covering just the copy."""
        v0, r0 = mark
        new_mark = (len(self.pos), len(self.recs))
        vmap: Dict[int, int] = {}
        for i in range(v0, new_mark[0]):
            p = list(self.pos[i])
            p[axis] = -p[axis]
            self.pos.append((p[0], p[1], p[2]))
            self.raw_w.append({swap_joint(k): v for k, v in self.raw_w[i].items()})
            vmap[i] = len(self.pos) - 1
        gm = group_map or {}
        for rec in list(self.recs[r0:new_mark[1]]):
            nr = dict(rec)
            nr["g"] = gm.get(rec["g"], rec["g"])
            if rec["up"] is not None:
                u = list(rec["up"])
                u[axis] = -u[axis]
                nr["up"] = (u[0], u[1], u[2])
            if rec["k"] == "poly":
                nr["vis"] = [vmap[i] for i in reversed(rec["vis"])]
            else:
                nr["rings"] = [[vmap[i] for i in reversed(r)] for r in rec["rings"]]
                if rec["flat"]:
                    n = len(rec["rings"][0])
                    nr["flat"] = frozenset((n - 2 - c) % n for c in rec["flat"]) if rec["closed"] else rec["flat"]
            self.recs.append(nr)
        return new_mark

    # ---- build
    def _density(self, group: str) -> float:
        return self.density.get(group, self.default_density)

    def build(self, fit: bool = True, max_influences: int = MAX_INFLUENCES) -> Built:
        faces: List[dict] = []   # {"vis", "g", "s", "isl", "xy": [(x, y)]}
        islands: List[dict] = []  # {"g", "d", "w", "h", "minx", "miny"}
        for rec in self.recs:
            if rec["k"] == "poly":
                _poly_island(self.pos, rec, faces, islands)
            else:
                _strip_island(self.pos, rec, faces, islands)
        dims = [(max(i["w"], 1e-6), max(i["h"], 1e-6), self._density(i["g"]) * i["d"]) for i in islands]
        f = fit_scale(dims, self.atlas, self.margin) if fit else 1.0
        sizes = [island_texels(w, h, d * f) for w, h, d in dims]
        placed = pack_rects(sizes, self.atlas, self.margin)
        if placed is None:
            raise ValueError("UV islands do not fit the %dx%d atlas (texel density factor %.2f)" % (self.atlas, self.atlas, f))
        out = Built()
        out.atlas, out.margin, out.scale, out.island_count = self.atlas, self.margin, f, len(islands)
        out.group_names = list(self.groups)
        out.positions = list(self.pos)
        out.weights = [normalize_weights(w, max_influences) for w in self.raw_w]
        a = float(self.atlas)
        isl_xf = []
        for k, isl in enumerate(islands):
            tw, th = sizes[k]
            ox, oy = placed[k]
            isl_xf.append((ox, oy, tw / max(isl["w"], 1e-6), th / max(isl["h"], 1e-6), isl["minx"], isl["miny"]))
        rep: Dict[str, List[float]] = {}
        for fc in faces:
            ox, oy, sx, sy, mnx, mny = isl_xf[fc["isl"]]
            out.faces.append(tuple(fc["vis"]))
            out.groups.append(self.group_index(fc["g"]))
            out.tags.append(fc["t"])
            out.smooth.append(bool(fc["s"]))
            out.uvs.append([((ox + (x - mnx) * sx) / a, 1.0 - (oy + (y - mny) * sy) / a) for x, y in fc["xy"]])
        for k, isl in enumerate(islands):
            tw, th = sizes[k]
            if isl["w"] > 1e-4 and isl["h"] > 1e-4:
                rep.setdefault(isl["g"], []).extend([tw / isl["w"], th / isl["h"]])
        out.density_report = {g: {"min": round(min(v), 2), "max": round(max(v), 2), "mean": round(sum(v) / len(v), 2)}
                              for g, v in sorted(rep.items())}
        return out


# ---------------------------------------------------------------------------------- development helpers
def _clean_quad(vis: List[int], pos: Sequence[Vec]) -> Optional[List[int]]:
    """Drop corners coinciding with the next corner (collapsed edge -> triangle); None when degenerate."""
    keep: List[int] = []
    n = len(vis)
    for i in range(n):
        a, b = vis[i], vis[(i + 1) % n]
        if a != b and vlen(vsub(pos[a], pos[b])) > 1e-7:
            keep.append(a)
    if len(keep) < 3:
        return None
    if vlen(newell([pos[i] for i in keep])) < 1e-9:
        return None
    return keep


def _poly_island(pos: Sequence[Vec], rec: dict, faces: List[dict], islands: List[dict]) -> None:
    vis = _clean_quad(rec["vis"], pos)
    if vis is None:
        return
    pts = [pos[i] for i in vis]
    nrm = vnorm(newell(pts))
    up = rec["up"] or (0.0, 0.0, 1.0)
    if vlen(vcross(up, nrm)) < 0.15:  # face (nearly) perpendicular to the hint: use +Y as the "up" of the image
        up = (0.0, 1.0, 0.0)
        if vlen(vcross(up, nrm)) < 0.15:
            up = (1.0, 0.0, 0.0)
    upp = vnorm(vsub(up, vmul(nrm, vdot(up, nrm))))
    right = vcross(upp, nrm)
    p0 = pts[0]
    xy = [(vdot(vsub(p, p0), right), -vdot(vsub(p, p0), upp)) for p in pts]
    mnx, mxx = min(x for x, _ in xy), max(x for x, _ in xy)
    mny, mxy = min(y for _, y in xy), max(y for _, y in xy)
    islands.append({"g": rec["g"], "d": rec["d"], "w": mxx - mnx, "h": mxy - mny, "minx": mnx, "miny": mny})
    k = len(islands) - 1
    if len(vis) > 4:  # n-gon (caps): triangulate here, robustly, instead of leaving it to the exporter's tessellation
        for a, b, c in ear_clip(pts):
            faces.append({"vis": [vis[a], vis[b], vis[c]], "g": rec["g"], "s": rec["s"], "isl": k, "xy": [xy[a], xy[b], xy[c]], "t": rec["t"]})
        return
    faces.append({"vis": vis, "g": rec["g"], "s": rec["s"], "isl": k, "xy": xy, "t": rec["t"]})


def _strip_island(pos: Sequence[Vec], rec: dict, faces: List[dict], islands: List[dict]) -> None:
    rings = rec["rings"]
    closed = rec["closed"]
    nr, n = len(rings), len(rings[0])
    ncols = n if closed else n - 1
    # ring x tables (cumulative edge lengths, centred) and y per ring
    xs: List[List[float]] = []
    for ring in rings:
        pts = [pos[i] for i in ring] + ([pos[ring[0]]] if closed else [])
        acc = [0.0]
        for i in range(len(pts) - 1):
            acc.append(acc[-1] + vlen(vsub(pts[i + 1], pts[i])))
        half = acc[-1] / 2.0
        xs.append([a - half for a in acc])
    ys = [0.0]
    for r in range(1, nr):
        d = sum(vlen(vsub(pos[rings[r][c]], pos[rings[r - 1][c]])) for c in range(n)) / n
        ys.append(ys[-1] + d)
    # image-up: rows should run DOWN the image; if they run up in world space rotate the island 180 degrees
    up = rec["up"]
    c0 = centroid([pos[i] for i in rings[0]])
    c1 = centroid([pos[i] for i in rings[-1]])
    flip = vdot(vsub(c1, c0), up) > 1e-9
    if flip:
        xs = [[-x for x in row] for row in xs]
        ys = [-y for y in ys]
    allx = [x for row in xs for x in row]
    mnx, mxx, mny, mxy = min(allx), max(allx), min(ys), max(ys)
    islands.append({"g": rec["g"], "d": rec["d"], "w": mxx - mnx, "h": mxy - mny, "minx": mnx, "miny": mny})
    k = len(islands) - 1
    for r in range(nr - 1):
        for c in range(ncols):
            c2 = (c + 1) % n
            vis = [rings[r][c], rings[r + 1][c], rings[r + 1][c2], rings[r][c2]]
            xy = [(xs[r][c], ys[r]), (xs[r + 1][c], ys[r + 1]), (xs[r + 1][c + 1], ys[r + 1]), (xs[r][c + 1], ys[r])]
            keep = _clean_quad(vis, pos)
            if keep is None:
                continue
            if len(keep) != 4:  # collapsed edge: keep UVs of the surviving corners
                xy = [xy[vis.index(i)] for i in keep]
            faces.append({"vis": keep, "g": rec["g"], "s": rec["s"] and (c not in rec["flat"]), "isl": k, "xy": xy, "t": rec["t"]})


# ============================================================================================ primitives
def _closed_solid(m: Model, rings_pts: Sequence[Sequence[Vec]], group: str, w: WeightSpec, *, caps=(True, True),
                  loop: bool = False, up: Vec = (0.0, 0.0, 1.0), density: float = 1.0, smooth: bool = False,
                  flat_cols: Iterable[int] = (), cap_up: Vec = (0.0, 1.0, 0.0)) -> Tuple[int, int]:
    """Create vertices for rings (lists of 3D points, equal length, ring order arbitrary), orient them outward, add the strip
    and cap records. Rows run from ring 0 to the last ring. Returns the mark taken before creation."""
    mk = m.mark()
    n = len(rings_pts[0])
    pts_rings = [list(r) for r in rings_pts]
    # orientation: the Newell normal of the ring must point AGAINST the direction of travel for an outward quad winding
    nsum = (0.0, 0.0, 0.0)
    for r in pts_rings:
        nn = newell(r)
        nsum = vadd(nsum, nn)
    travel = vsub(centroid(pts_rings[-1]), centroid(pts_rings[0])) if len(pts_rings) > 1 else (0.0, 0.0, 1.0)
    if loop:
        travel = vsub(centroid(pts_rings[1]), centroid(pts_rings[0]))
    if vdot(nsum, travel) > 0.0:
        pts_rings = [list(reversed(r)) for r in pts_rings]
        flat_cols = [(n - 2 - c) % n for c in flat_cols]
    idx = [m.verts(r, w) for r in pts_rings]
    rings = idx + ([idx[0]] if loop else [])
    m.strip(rings, group, closed=True, up=up, density=density, smooth=smooth, flat_cols=flat_cols)
    if not loop:
        for end, do in ((0, caps[0]), (len(idx) - 1, caps[1])):
            if not do:
                continue
            ring = idx[end]
            nrm = newell([m.pos[i] for i in ring])
            outward = vsub(centroid(pts_rings[0]), centroid(pts_rings[1])) if end == 0 else vsub(centroid(pts_rings[-1]), centroid(pts_rings[-2]))
            vis = ring if vdot(nrm, outward) > 0 else list(reversed(ring))
            m.poly(_drop_collinear(vis, m.pos), group, up=cap_up, density=density, smooth=False)
    return mk


def _drop_collinear(vis: Sequence[int], pos: Sequence[Vec], tol: float = 1e-6) -> List[int]:
    """Polygon vertex indices without the vertices that lie on the straight line between their neighbours (cap polygons of lofts
    with subdivided faces: a fan triangulation of collinear points would give zero-area triangles)."""
    n = len(vis)
    keep = []
    for i in range(n):
        a, b, c = pos[vis[i - 1]], pos[vis[i]], pos[vis[(i + 1) % n]]
        ab, bc = vsub(b, a), vsub(c, b)
        if vlen(vcross(ab, bc)) <= tol * max(vlen(ab) * vlen(bc), EPS) and vdot(ab, bc) > 0.0:
            continue
        keep.append(vis[i])
    return keep if len(keep) >= 3 else list(vis)


def rect_ring(cx: float, cy: float, z: float, hx: float, hy: float, bevel: float = 0.0) -> List[Vec]:
    """Counter-clockwise (seen from above) axis-aligned rectangle ring at height z, half sizes hx / hy; with `bevel` > 0 the
    four corners are chamfered (8 points). Starts at the front-right corner region (+x, +y)."""
    b = min(bevel, hx * 0.95, hy * 0.95)
    if b <= 0.0:
        return [(cx + hx, cy + hy, z), (cx - hx, cy + hy, z), (cx - hx, cy - hy, z), (cx + hx, cy - hy, z)]
    return [(cx + hx - b, cy + hy, z), (cx - hx + b, cy + hy, z), (cx - hx, cy + hy - b, z), (cx - hx, cy - hy + b, z),
            (cx - hx + b, cy - hy, z), (cx + hx - b, cy - hy, z), (cx + hx, cy - hy + b, z), (cx + hx, cy + hy - b, z)]


def block(m: Model, lo: Sequence[float], hi: Sequence[float], group: str, w: WeightSpec, *, bevel: float = 0.0,
          rot: Sequence[float] = (0.0, 0.0, 0.0), pivot: Optional[Sequence[float]] = None, density: float = 1.0,
          smooth: bool = False, skip: Sequence[str] = (), top_scale: Tuple[float, float] = (1.0, 1.0),
          top_shift: Tuple[float, float] = (0.0, 0.0), segments: int = 1) -> Tuple[int, int]:
    """Axis-aligned box from `lo` to `hi` (optionally tapered: `top_scale` multiplies the x / y half sizes of the top face,
    `top_shift` moves its centre; chamfered with `bevel`; rotated by Euler degrees about `pivot` (default: its centre);
    `segments` > 1 cuts it into that many stacked sections along z, useful for smooth weights). With bevel = 0 and no skip the
    four side faces form ONE wrap-around UV strip (like Minecraft's cross) and top / bottom are separate islands. `skip` may
    contain "top" / "bottom" (the face is not created)."""
    cx, cy = (lo[0] + hi[0]) / 2.0, (lo[1] + hi[1]) / 2.0
    hx, hy = (hi[0] - lo[0]) / 2.0, (hi[1] - lo[1]) / 2.0
    rings: List[List[Vec]] = []
    z0, z1 = lo[2], hi[2]
    zs = [z0 + (z1 - z0) * k / segments for k in range(segments + 1)]
    if bevel > 0.0:
        b = min(bevel, hx * 0.9, hy * 0.9, (z1 - z0) * 0.45)
        stations = [(z0, b)] + [(z0 + b, 0.0)] + [(z, 0.0) for z in zs[1:-1] if z0 + b < z < z1 - b] + [(z1 - b, 0.0), (z1, b)]
    else:
        b = 0.0
        stations = [(z, 0.0) for z in zs]
    for z, inset in stations:
        t = (z - z0) / max(z1 - z0, EPS)
        rings.append(rect_ring(cx + top_shift[0] * t, cy + top_shift[1] * t, z, (hx - inset) * lerp(1.0, top_scale[0], t),
                               (hy - inset) * lerp(1.0, top_scale[1], t), b))
    piv = pivot if pivot is not None else (cx, cy, (z0 + z1) / 2.0)
    if any(rot):
        rings = [rotate_points(r, piv, rot) for r in rings]
    return _closed_solid(m, rings, group, w, caps=("bottom" not in skip, "top" not in skip), density=density, smooth=smooth,
                         cap_up=(0.0, 1.0, 0.0) if not any(rot) else rotate_points([(0.0, 1.0, 0.0)], (0.0, 0.0, 0.0), rot)[0])


def frustum(m: Model, z0: float, z1: float, c0: Tuple[float, float], h0: Tuple[float, float], c1: Tuple[float, float],
            h1: Tuple[float, float], group: str, w: WeightSpec, *, bevel: float = 0.0, sections: int = 1,
            density: float = 1.0, smooth: bool = False, caps: Tuple[bool, bool] = (True, True)) -> Tuple[int, int]:
    """Rectangular frustum from z0 (centre c0 = (x, y), half sizes h0 = (hx, hy)) to z1 (c1, h1), `sections` stacked rings."""
    rings = []
    for k in range(sections + 1):
        t = k / float(sections)
        z = lerp(z0, z1, t)
        rings.append(rect_ring(lerp(c0[0], c1[0], t), lerp(c0[1], c1[1], t), z, lerp(h0[0], h1[0], t), lerp(h0[1], h1[1], t), bevel))
    return _closed_solid(m, rings, group, w, caps=caps, density=density, smooth=smooth)


def stacked_rects(m: Model, specs: Sequence[Tuple[float, float, float, float, float]], group: str, w: WeightSpec, *,
                  bevel: float = 0.0, density: float = 1.0, smooth: bool = False,
                  caps: Tuple[bool, bool] = (True, True)) -> Tuple[int, int]:
    """Loft of rectangles given as (z, cx, cy, hx, hy), listed in ANY z order: torso / sleeve / skirt shells with arbitrary
    centre and size per section."""
    rings = [rect_ring(cx, cy, z, hx, hy, bevel) for z, cx, cy, hx, hy in specs]
    return _closed_solid(m, rings, group, w, caps=caps, density=density, smooth=smooth)


def loft(m: Model, rings: Sequence[Sequence[Sequence[float]]], group: str, w: WeightSpec, *, caps: Tuple[bool, bool] = (True, True),
         loop: bool = False, up: Vec = (0.0, 0.0, 1.0), density: float = 1.0, smooth: bool = False,
         flat_cols: Iterable[int] = (), cap_up: Vec = (0.0, 1.0, 0.0)) -> Tuple[int, int]:
    """Generic closed tube through equal-length point rings (any convex or concave cross-section, e.g. the C of a skirt wall).
    Rows run ring 0 -> last (`up` decides which end of the UV island is the image top). `loop` joins the last ring to the
    first (a ring-shaped tube, no caps). Winding is fixed automatically."""
    return _closed_solid(m, [[tuple(p) for p in r] for r in rings], group, w, caps=caps, loop=loop, up=up, density=density,
                         smooth=smooth, flat_cols=flat_cols, cap_up=cap_up)  # type: ignore[arg-type]


def frames_along(path: Sequence[Vec], side: Union[Vec, Sequence[Vec]]) -> List[Tuple[Vec, Vec, Vec]]:
    """(tangent, width_dir, normal) at every path point: tangent from neighbours, width_dir = `side` made perpendicular to the
    tangent (`side` may be one vector or one per point), normal = tangent x width_dir."""
    n = len(path)
    out = []
    for i in range(n):
        a = path[max(i - 1, 0)]
        b = path[min(i + 1, n - 1)]
        t = vnorm(vsub(b, a))
        s = side[i] if (len(side) == n and not isinstance(side[0], (int, float))) else side  # type: ignore[index]
        wd = vsub(s, vmul(t, vdot(s, t)))  # type: ignore[arg-type]
        if vlen(wd) < 1e-6:  # side parallel to the tangent: pick any perpendicular
            wd = vcross(t, (0.0, 0.0, 1.0) if abs(t[2]) < 0.9 else (1.0, 0.0, 0.0))
        wd = vnorm(wd)
        out.append((t, wd, vnorm(vcross(t, wd))))
    return out


def sweep(m: Model, path: Sequence[Sequence[float]], widths: Union[float, Sequence[float]], thick: Union[float, Sequence[float]],
          group: str, w: WeightSpec, *, side: Union[Vec, Sequence[Vec]] = (1.0, 0.0, 0.0), caps: Tuple[bool, bool] = (True, True),
          loop: bool = False, density: float = 1.0, smooth: bool = False, bevel: float = 0.0, offsets: Optional[Sequence[float]] = None,
          twist: Optional[Sequence[float]] = None) -> Tuple[int, int]:
    """Rectangular-section tube along a polyline: curved feather cards, ribbons, hair locks, cloth strips, sleeves.

    `widths[i]` / `thick[i]` (or one number) = full width along the frame's width direction (`side` projected perpendicular to
    the path) and thickness along the frame normal; `offsets[i]` shifts the section along the width direction (hair parting,
    asymmetric tips); `twist[i]` rotates the section about the tangent (degrees). Rows run along the path (path start = image
    top of the UV island unless the path rises: then the island is rotated so rows still read downward)."""
    pts = [tuple(p) for p in path]  # type: ignore[misc]
    n = len(pts)
    wd = list(widths) if isinstance(widths, (list, tuple)) else [float(widths)] * n
    th = list(thick) if isinstance(thick, (list, tuple)) else [float(thick)] * n
    fr = frames_along(pts, side)  # type: ignore[arg-type]
    rings = []
    for i in range(n):
        t, wdir, nrm = fr[i]
        if twist is not None and twist[i]:
            a = math.radians(twist[i])
            ca, sa = math.cos(a), math.sin(a)
            wdir, nrm = vadd(vmul(wdir, ca), vmul(nrm, sa)), vadd(vmul(nrm, ca), vmul(wdir, -sa))
        c = pts[i] if offsets is None else vadd(pts[i], vmul(wdir, offsets[i]))
        hw, ht = wd[i] / 2.0, th[i] / 2.0
        b = min(bevel, hw * 0.9, ht * 0.9)
        corners = ([(hw, ht), (-hw, ht), (-hw, -ht), (hw, -ht)] if b <= 0.0 else
                   [(hw - b, ht), (-hw + b, ht), (-hw, ht - b), (-hw, -ht + b), (-hw + b, -ht), (hw - b, -ht), (hw, -ht + b), (hw, ht - b)])
        rings.append([vadd(c, vadd(vmul(wdir, a), vmul(nrm, b2))) for a, b2 in corners])
    return _closed_solid(m, rings, group, w, caps=caps, loop=loop, up=(0.0, 0.0, 1.0), density=density, smooth=smooth,
                         flat_cols=(1, 3) if bevel <= 0.0 else (), cap_up=(0.0, 0.0, 1.0))


def path_with_bulge(p0: Sequence[float], p1: Sequence[float], bulge: Sequence[float], n: int = 6) -> List[Vec]:
    """Quadratic Bezier from p0 to p1 whose middle is pushed by the vector `bulge` (arc-like curl of feathers, ribbons)."""
    mid = vadd(vlerp(p0, p1, 0.5), vmul(bulge, 2.0))
    return bezier([p0, mid, p1], n)


def ring_path(center: Sequence[float], u: Sequence[float], v: Sequence[float], rx: float, ry: float, n: int = 16,
              a0: float = 0.0, a1: float = 360.0) -> List[Vec]:
    """Points on an ellipse in the plane spanned by unit vectors u, v (hair loops, ribbon bows, halos). Closed loops use the
    default full circle with the last point left out (pair with `sweep(..., loop=True)`)."""
    pts = []
    full = abs(a1 - a0 - 360.0) < 1e-6
    steps = n if full else n + 1
    for k in range(steps):
        a = math.radians(a0 + (a1 - a0) * k / float(n))
        pts.append(vadd(center, vadd(vmul(u, rx * math.cos(a)), vmul(v, ry * math.sin(a)))))
    return pts


# ============================================================================================ blender
def _bpy():
    import bpy  # noqa: WPS433 - lazy: the rest of the module is pure Python
    return bpy


def make_material(name: str, color: Sequence[float] = (0.8, 0.8, 0.8), image=None, roughness: float = 0.9):
    """Principled material named `name` (the group). With `image` the colour comes from a Closest-interpolated image texture."""
    bpy = _bpy()
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    if "Roughness" in bsdf.inputs:
        bsdf.inputs["Roughness"].default_value = roughness
    for n in list(nt.nodes):
        if n.type == "TEX_IMAGE":
            nt.nodes.remove(n)
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.interpolation = "Closest"
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = (color[0], color[1], color[2], 1.0)
    mat.diffuse_color = (color[0], color[1], color[2], 1.0)
    return mat


def to_blender(built: Built, name: str, groups: Optional[Sequence[str]] = None, *, rig=None, colors: Optional[Dict[str, Sequence[float]]] = None,
               image=None, tags: Optional[Iterable[str]] = None, collection=None, uv_name: str = "UVMap"):
    """Blender mesh object from a `Built` model: one material slot per group (`groups`, default `built.group_names` = `Model.groups`
    order; slot name = group),
    vertex groups named by EF joint with the normalised weights, UV map, per-face smooth flags, an Armature modifier +
    parent when `rig` is given. `tags` keeps only faces carrying one of these tags (vertices are remapped). Positions are in
    storage space; the object transform stays identity."""
    bpy = _bpy()
    keep = set(tags) if tags is not None else None
    fidx = [i for i in range(len(built.faces)) if keep is None or built.tags[i] in keep]
    used = sorted({v for i in fidx for v in built.faces[i]})
    remap = {v: k for k, v in enumerate(used)}
    me = bpy.data.meshes.new(name)
    me.from_pydata([built.positions[v] for v in used], [], [tuple(remap[v] for v in built.faces[i]) for i in fidx])
    me.update()
    uvl = me.uv_layers.new(name=uv_name)
    flat_uv = []
    for i in fidx:
        for u, v in built.uvs[i]:
            flat_uv.extend((u, v))
    uvl.uv.foreach_set("vector", flat_uv)
    me.polygons.foreach_set("use_smooth", [bool(built.smooth[i]) for i in fidx])
    for g in (groups if groups is not None else built.group_names):
        col = (colors or {}).get(g, (0.8, 0.8, 0.8))
        me.materials.append(make_material(g, col, image))
    me.polygons.foreach_set("material_index", [built.groups[i] for i in fidx])
    me.update()
    obj = bpy.data.objects.new(name, me)
    (collection or bpy.context.scene.collection).objects.link(obj)
    byjoint: Dict[str, Dict[float, List[int]]] = {}
    for v in used:
        for j, wt in built.weights[v].items():
            byjoint.setdefault(j, {}).setdefault(round(wt, 6), []).append(remap[v])
    for j in sorted(byjoint):
        vg = obj.vertex_groups.new(name=j)
        for wt, ids in sorted(byjoint[j].items()):
            vg.add(ids, wt, "REPLACE")
    if rig is not None:
        bind_rig(obj, rig)
    return obj


def bind_rig(obj, rig) -> None:
    """Armature modifier + parent (identity inverse: both sit at the origin) so posing the rig deforms the mesh."""
    mod = obj.modifiers.new("EF_Biped", "ARMATURE")
    mod.object = rig
    obj.parent = rig


def evaluated_points(obj) -> List[Vec]:
    """Vertex positions of `obj` with all modifiers (the armature pose) applied, world space."""
    bpy = _bpy()
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    eo = obj.evaluated_get(dg)
    me = eo.to_mesh()
    try:
        mw = obj.matrix_world
        return [tuple(mw @ v.co) for v in me.vertices]  # type: ignore[misc]
    finally:
        eo.to_mesh_clear()


def points_inside(target_obj, points: Sequence[Sequence[float]], margin: float = 0.0, with_depth: bool = False):
    """Indices of `points` that lie inside the (closed, outward-facing) evaluated mesh of `target_obj` (modifiers / armature
    pose applied), deeper than `margin` blocks. Nearest-surface test: inside when (p - nearest) . normal < -margin.
    `with_depth` returns [(index, depth_in_blocks)] instead."""
    bpy = _bpy()
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    dg = bpy.context.evaluated_depsgraph_get()
    dg.update()
    tree = BVHTree.FromObject(target_obj, dg)
    hits = []
    for i, p in enumerate(points):
        v = Vector(p)
        loc, nrm, _idx, _dist = tree.find_nearest(v)
        if loc is None:
            continue
        d = (v - loc).dot(nrm)
        if d < -max(margin, 1e-6):
            hits.append((i, -d) if with_depth else i)
    return hits


def weight_report(obj, joint_names: Sequence[str]) -> Dict[str, float]:
    """Numbers proving the skinning of a mesh object: vertices, unweighted, max influences, min / max total weight."""
    names = {g.index: g.name for g in obj.vertex_groups}
    jn = set(joint_names)
    unweighted = 0
    mx = 0
    lo, hi = 9.0, 0.0
    for v in obj.data.vertices:
        ws = [g.weight for g in v.groups if names.get(g.group) in jn]
        if not ws:
            unweighted += 1
            continue
        mx = max(mx, len(ws))
        s = sum(ws)
        lo, hi = min(lo, s), max(hi, s)
    return {"vertices": len(obj.data.vertices), "unweighted": unweighted, "max_influences": mx,
            "min_total": lo if unweighted < len(obj.data.vertices) else 0.0, "max_total": hi}
