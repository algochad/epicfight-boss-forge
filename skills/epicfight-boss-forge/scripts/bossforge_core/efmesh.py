"""Epic Fight entity mesh loader (positions/normals/uvs/triangles by part)."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .spec import BossForgeError

Vec = Tuple[float, float, float]
GRID_SIZES = (16, 32, 64, 128, 256, 512)


class Tri:
    __slots__ = ("part", "p", "t", "n")

    def __init__(self, part: str, p, t, n):
        self.part = part
        self.p = p  # 3 position indices
        self.t = t  # 3 uv indices
        self.n = n  # 3 normal indices


class Mesh:
    def __init__(self, positions: List[Vec], normals: List[Vec], uvs: List[Tuple[float, float]], tris: List[Tri],
                 part_regions: Dict[str, str]):
        self.positions = positions
        self.normals = normals
        self.uvs = uvs
        self.tris = tris
        self.part_regions = part_regions

    def region_of(self, part: str) -> Optional[str]:
        return self.part_regions.get(part)

    def parts(self) -> List[str]:
        seen = []
        for t in self.tris:
            if t.part not in seen:
                seen.append(t.part)
        return seen

    def part_vertices(self, parts: Optional[Iterable[str]] = None) -> List[Vec]:
        want = set(parts) if parts is not None else None
        out = []
        for t in self.tris:
            if want is None or t.part in want:
                for i in t.p:
                    out.append(self.positions[i])
        return out

    def bounds(self, parts: Optional[Iterable[str]] = None) -> Tuple[Vec, Vec]:
        vs = self.part_vertices(parts)
        if not vs:
            return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
        return ((min(v[0] for v in vs), min(v[1] for v in vs), min(v[2] for v in vs)),
                (max(v[0] for v in vs), max(v[1] for v in vs), max(v[2] for v in vs)))


def convert_axis(v: Vec, axis_mode: str) -> Vec:
    x, y, z = v
    if axis_mode == "x,z,-y":
        return (x, z, -y)
    if axis_mode == "x,-z,y":
        return (x, -z, y)
    raise BossForgeError("/mesh", "bad_axis_mode", "unknown axis_mode %r" % (axis_mode,))


def read_raw(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))["vertices"]


def _triples(arr: List[float], stride: int) -> List[Tuple[float, ...]]:
    return [tuple(arr[i:i + stride]) for i in range(0, len(arr) - stride + 1, stride)]


def load_mesh(path, template: dict) -> Mesh:
    """template needs: axis_mode, unit_scale, index_order (default pos,uv,nrm), part_regions."""
    vtx = read_raw(path)
    axis_mode = template["axis_mode"]
    scale = float(template["unit_scale"])
    order = template.get("index_order") or "pos,uv,nrm"
    raw_pos = _triples(vtx["positions"]["array"], 3)
    raw_nrm = _triples(vtx["normals"]["array"], 3)
    uvs = [(u[0], u[1]) for u in _triples(vtx["uvs"]["array"], 2)]
    positions = [tuple(c * scale for c in convert_axis(p, axis_mode)) for p in raw_pos]
    normals = [convert_axis(n, axis_mode) for n in raw_nrm]
    tris: List[Tri] = []
    for part, obj in vtx["parts"].items():
        arr = obj["array"]
        if len(arr) % 9 != 0:
            raise BossForgeError("/mesh/parts/" + part, "bad_part_array", "part %s index array length %d is not a multiple of 9" % (part, len(arr)))
        for i in range(0, len(arr), 9):
            idx = arr[i:i + 9]
            if order == "pos,uv,nrm":
                p = (idx[0], idx[3], idx[6]); t = (idx[1], idx[4], idx[7]); n = (idx[2], idx[5], idx[8])
            else:
                p = (idx[0], idx[3], idx[6]); n = (idx[1], idx[4], idx[7]); t = (idx[2], idx[5], idx[8])
            tris.append(Tri(part, p, t, n))
    return Mesh(positions, normals, uvs, tris, dict(template.get("part_regions") or {}))


def infer_authored_size(uvs: Sequence[Tuple[float, float]]) -> Tuple[int, int]:
    res = []
    for axis in (0, 1):
        comps = [u[axis] for u in uvs]
        found = None
        for n in GRID_SIZES:
            ok = sum(1 for c in comps if abs(c * n - round(c * n)) < 0.05)
            if comps and ok / len(comps) >= 0.99:
                found = n
                break
        if found is None:
            raise BossForgeError("/mesh/uvs", "no_uv_grid", "UV coordinates do not lie on a 16..512 texel grid")
        res.append(found)
    return res[0], res[1]


def estimate_authored_size(mesh: Mesh) -> Tuple[int, int]:
    """Texture size from texel density, assuming 1 texel = 1/16 block (Minecraft model convention).

    infer_authored_size() only yields a lower bound (all UVs may sit on a coarser sub-grid than the
    real texture, e.g. the biped lies on a 16x32 grid but is a 64x64 texture). Here every axis-aligned
    UV edge votes for W = 16*world_length/uv_length, snapped to the nearest power-of-two size; the mode wins.
    """
    votes: Tuple[Dict[int, int], Dict[int, int]] = ({}, {})
    for tr in mesh.tris:
        for i in range(3):
            a, b = tr.p[i], tr.p[(i + 1) % 3]
            ua, ub = mesh.uvs[tr.t[i]], mesh.uvs[tr.t[(i + 1) % 3]]
            length = math.dist(mesh.positions[a], mesh.positions[b])
            du, dv = abs(ua[0] - ub[0]), abs(ua[1] - ub[1])
            if length < 1e-6:
                continue
            for axis, d, o in ((0, du, dv), (1, dv, du)):
                if d > 1e-5 and o < 1e-5:
                    est = 16.0 * length / d
                    snapped = min(GRID_SIZES, key=lambda n: abs(math.log2(n) - math.log2(est)))
                    votes[axis][snapped] = votes[axis].get(snapped, 0) + 1
    if not votes[0] or not votes[1]:
        raise BossForgeError("/mesh/uvs", "no_uv_grid", "cannot estimate texture size: no axis-aligned UV edges")
    return (max(votes[0], key=votes[0].get), max(votes[1], key=votes[1].get))


def _mean_x(mesh: Mesh, parts: Sequence[str]) -> float:
    vs = mesh.part_vertices(parts)
    return sum(v[0] for v in vs) / len(vs) if vs else 0.0


def frame(mesh: Mesh, template: dict) -> Tuple[int, int]:
    """(front_sign, right_sign). front_sign comes from the template; right_sign is the sign of
    +x for the character's own right side, measured from rightArm/rightLeg vs leftArm/leftLeg."""
    front = int(template["front_sign"])
    d = _mean_x(mesh, ("rightArm", "rightLeg")) - _mean_x(mesh, ("leftArm", "leftLeg"))
    return front, (1 if d >= 0 else -1)


def uv_to_pixel(u: float, v: float, w: int, h: int, uv_origin: str) -> Tuple[float, float]:
    return (u * w, v * h) if uv_origin == "top" else (u * w, (1.0 - v) * h)


def tri_normal(a: Vec, b: Vec, c: Vec) -> Vec:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    l = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return (nx / l, ny / l, nz / l)


def mesh_info(mesh: Mesh, template: dict, mesh_name: str) -> dict:
    """Diagnostics printed by `mesh-info`."""
    parts = {}
    for t in mesh.tris:
        parts.setdefault(t.part, 0)
        parts[t.part] += 1
    pinfo = {}
    for name, count in parts.items():
        lo, hi = mesh.bounds([name])
        pinfo[name] = {"triangles": count, "region": mesh.region_of(name),
                       "min": [round(c, 4) for c in lo], "max": [round(c, 4) for c in hi]}
    overlay = set(template.get("overlay_parts") or [])
    lo, hi = mesh.bounds([p for p in parts if p not in overlay])
    uvw, uvh = estimate_authored_size(mesh)
    try:
        grid = list(infer_authored_size(mesh.uvs))
    except BossForgeError:
        grid = None
    front, right = frame(mesh, template)
    # fraction of texture area covered per region (UV triangle area sum)
    cov: Dict[str, float] = {}
    for t in mesh.tris:
        a, b, c = (mesh.uvs[i] for i in t.t)
        area = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2.0
        r = mesh.region_of(t.part) or "?"
        cov[r] = cov.get(r, 0.0) + area
    return {
        "template_mesh": mesh_name, "parts": pinfo,
        "height": round(hi[1] - lo[1], 4), "width": round(hi[0] - lo[0], 4), "depth": round(hi[2] - lo[2], 4),
        "model_height": round(hi[1] - lo[1], 4),
        "authored_uv_inferred": [uvw, uvh], "uv_grid_lower_bound": grid, "axis_mode": template["axis_mode"], "unit_scale": template["unit_scale"],
        "index_order": template.get("index_order"), "uv_origin": template.get("uv_origin"),
        "front_sign": front, "right_sign": right,
        "region_uv_coverage": {k: round(v, 4) for k, v in sorted(cov.items())},
    }


def load_for_template(ctx, template_name: str):
    """(Mesh, template dict) for a BuildContext; honours ctx.mesh_files, ctx.ef_jar, ctx.offline, ctx.cache_dir."""
    from . import efassets
    from .spec import data
    tpl = data("templates")[template_name]
    override = (getattr(ctx, "mesh_files", None) or {}).get(template_name)
    if override:
        path = Path(override)
    else:
        path = efassets.mesh_file(tpl["mesh_name"], ctx.cache_dir, ctx.versions["epicfight"]["assets_ref"],
                                  getattr(ctx, "ef_jar", None), ctx.offline)
    return load_mesh(path, tpl), tpl
