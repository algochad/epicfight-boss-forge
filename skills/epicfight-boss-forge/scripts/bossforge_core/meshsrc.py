"""Imported meshes: a Blender-made body for a boss (the `mesh` spec block).

A boss normally gets its body from boxes (`shape.py`). With a `mesh` block the body is a *bundle* made in Blender
(or by `bundle_from_shape`): `<stem>.efmesh.json` (the exact Epic Fight mesh JSON, Blender Z-up storage space,
UV v from the top row, weights on the EF biped joints of `rigs.json`) plus the sidecar `<stem>.meshmeta.json`
(`bossforge-mesh/1`: group names, the group of every triangle of every EF part, optional height / group bones).

This module loads a bundle and builds the object the rest of the pipeline consumes (`MeshModel`, the equivalent of
`shape.ShapeModel`; `shape.build_model` dispatches here for specs with `mesh`):

* `mesh`: an `efmesh.Mesh` of the REST pose (weights are only used to derive each triangle's body region), one
  triangle part per GROUP so `part:<glob>` / `box:<glob>` feature targets select groups,
* `tpl`: the painter template (`custom`, per-group styles, atlas = `mesh.texture.size`),
* `mesh_json`: the JSON the game loads (`animmodels/entity/<id>.json`),
* `height` / `bounds` (model space, y up, feet at 0, front = -z), `info` (per-group counts, bbox, texel density,
  UV coverage) and `warnings`.

Regions. The body features (`eyes`, `brow`, `emblem`, `trim`, ...) address regions (`head torso arm_l arm_r leg_l
leg_r`). Every triangle gets its region from the dominant joint of its vertices (`JOINT_REGION`), unless the group
fixes it with `mesh.groups.<name>.region`; `"private"` makes the group a region of its own (`box:<group>`), like the
overlay boxes of a shape: default features skip it, its own bounding box is its frame. All non-private groups of a
region share one frame (bounding box of all their triangles), so give hair, crowns, wings and capes `private`.
"""
from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import efmesh, efmodel
from .colors import is_hex
from .spec import MATERIALS, PALETTE_KEYS, QUALITIES, BossForgeError, Err, GenFile, data

Vec = Tuple[float, float, float]

META_SCHEMA = "bossforge-mesh/1"
EFMESH_SUFFIX = ".efmesh.json"
META_SUFFIX = ".meshmeta.json"
MESH_KEYS = ("file", "groups", "texture")
GROUP_KEYS = ("material", "color", "accent", "region", "quality")
TEXTURE_KEYS = ("size", "density", "padding")
REGIONS = ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r")
GROUP_REGIONS = REGIONS + ("private",)
GROUP_RE = re.compile(r"^[a-z0-9_]{1,64}$")
TEXTURE_SIZES = (64, 128, 256, 512, 1024, 2048)
DEFAULT_SIZE = 512
DEFAULT_DENSITY = 32
DENSITY_RANGE = (8.0, 64.0)
DEFAULT_PADDING = 4  # texels the island borders are dilated by (mipmaps / bilinear filtering must not bleed the background)
PADDING_RANGE = (0, 16)
MIN_DENSITY = 8.0  # texels per block below which a group is `mesh_low_density`
UV_TOLERANCE = 1e-4
OVERLAP_RES = 256  # resolution of the UV overlap / coverage measurement
OVERLAP_WARN = 0.02  # fraction of covered texels that more than one triangle covers
MAX_EDGE = 2048
GROUND_TOLERANCE = (-0.02, 0.05)  # allowed min y of the mesh (feet at y=0)
HEIGHT_TOLERANCE = 0.02
DEGENERATE_AREA2 = 1e-14  # squared cross product below which a triangle is an anchor / degenerate
MAX_WIDE_WARNINGS = 8
# stock extents (blocks) of a region: [x, y, z]; a group wider than WIDE_FACTOR x (and +WIDE_MARGIN) of it distorts the frame
STOCK_EXTENT = {"head": (0.5, 0.5, 0.5), "torso": (0.5, 0.75, 0.25), "arm_l": (0.25, 0.75, 0.25), "arm_r": (0.25, 0.75, 0.25),
                "leg_l": (0.25, 0.76, 0.25), "leg_r": (0.25, 0.76, 0.25)}
WIDE_FACTOR = 2.5
WIDE_MARGIN = 0.6

_JOINT_REGION = {
    "Root": "torso", "Torso": "torso", "Chest": "torso", "Shoulder_R": "torso", "Shoulder_L": "torso", "Head": "head",
    "Arm_R": "arm_r", "Hand_R": "arm_r", "Tool_R": "arm_r", "Elbow_R": "arm_r",
    "Arm_L": "arm_l", "Hand_L": "arm_l", "Tool_L": "arm_l", "Elbow_L": "arm_l",
    "Thigh_R": "leg_r", "Leg_R": "leg_r", "Knee_R": "leg_r", "Thigh_L": "leg_l", "Leg_L": "leg_l", "Knee_L": "leg_l",
}


def joint_names() -> List[str]:
    return [j["name"] for j in sorted(data("rigs")["biped"]["joints"], key=lambda j: j["index"])]


def joint_regions() -> List[str]:
    """Region of every joint index of the biped armature."""
    return [_JOINT_REGION[n] for n in joint_names()]


# --------------------------------------------------------------------------- spec block
def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def normalize_mesh(m: Any) -> Any:
    """Default the `mesh` block (tolerates invalid input: `validate` reports the problems)."""
    if not isinstance(m, dict):
        return m
    out = copy.deepcopy(m)
    if not isinstance(out.get("groups"), dict):
        out["groups"] = {} if out.get("groups") is None else out["groups"]
    tex = out.get("texture")
    tex = dict(tex) if isinstance(tex, dict) else ({} if tex is None else tex)
    if isinstance(tex, dict):
        tex.setdefault("size", DEFAULT_SIZE)
        tex.setdefault("density", DEFAULT_DENSITY)
        tex.setdefault("padding", DEFAULT_PADDING)
    out["texture"] = tex
    return out


def texture_of(mesh: dict) -> Tuple[int, float, int]:
    """(size, density, padding) of a normalized (or raw) mesh block, defaults filled."""
    tex = mesh.get("texture") if isinstance(mesh, dict) else None
    tex = tex if isinstance(tex, dict) else {}
    return (int(tex.get("size", DEFAULT_SIZE)), float(tex.get("density", DEFAULT_DENSITY)), int(tex.get("padding", DEFAULT_PADDING)))


def resolve_file(mesh: dict, spec_dir: Optional[str] = None) -> Path:
    """Absolute path of `mesh.file`: relative to the spec's directory (or the working directory without one)."""
    f = Path(str(mesh.get("file", "")))
    if f.is_absolute():
        return f
    return (Path(spec_dir) if spec_dir else Path(".")) / f


def meta_path_of(efmesh_path) -> Path:
    """The sidecar of a bundle: `<stem>.meshmeta.json` next to `<stem>.efmesh.json` (or `<stem>.json`)."""
    p = Path(efmesh_path)
    name = p.name
    if name.endswith(EFMESH_SUFFIX):
        stem = name[:-len(EFMESH_SUFFIX)]
    elif name.endswith(".json"):
        stem = name[:-5]
    else:
        stem = name
    return p.with_name(stem + META_SUFFIX)


def _color_ok(v) -> bool:
    return isinstance(v, str) and (is_hex(v) or v in PALETTE_KEYS)


def validate_mesh(mesh: Any, spec: dict, n: Optional[dict] = None) -> List[Err]:
    """Problems of a raw `mesh` block (paths absolute: /mesh/...) plus its cross-checks with the spec and the bundle.
    `spec` supplies `shape`, `body` and `_spec_dir`."""
    errs: List[Err] = []
    if "shape" in spec:
        errs.append(Err("/mesh", "mesh_and_shape", "a spec has either `shape` (boxes) or `mesh` (a Blender bundle), not both"))
    if (spec.get("body") or {}).get("template", "auto") not in ("auto", "biped"):
        errs.append(Err("/body/template", "mesh_requires_biped", "an imported mesh is skinned to the EF biped rig; use body.template auto or biped"))
    if not isinstance(mesh, dict):
        errs.append(Err("/mesh", "bad_type", "mesh must be an object"))
        return errs
    structural = len(errs)
    for k in mesh:
        if k not in MESH_KEYS:
            errs.append(Err("/mesh/" + str(k), "unknown_key", "unknown mesh key %r; allowed: %s" % (k, ", ".join(MESH_KEYS))))
    f = mesh.get("file")
    if not isinstance(f, str) or not f:
        errs.append(Err("/mesh/file", "required", "mesh.file (the .efmesh.json bundle, relative to the spec) is required"))
    groups = mesh.get("groups", {})
    if not isinstance(groups, dict):
        errs.append(Err("/mesh/groups", "bad_type", "groups must be an object {group name: {material, color, accent, region}}"))
        groups = {}
    for gname, g in groups.items():
        p = "/mesh/groups/%s" % gname
        if not isinstance(g, dict):
            errs.append(Err(p, "bad_type", "a group entry must be an object"))
            continue
        for k in g:
            if k not in GROUP_KEYS:
                errs.append(Err(p + "/" + str(k), "unknown_key", "unknown group key %r; allowed: %s" % (k, ", ".join(GROUP_KEYS))))
        if "material" in g and g["material"] not in MATERIALS:
            errs.append(Err(p + "/material", "unknown_material", "%r is not allowed; allowed values: %s" % (g["material"], ", ".join(MATERIALS))))
        for ck in ("color", "accent"):
            if ck in g and not _color_ok(g[ck]):
                errs.append(Err(p + "/" + ck, "bad_color", "%s %r must be a palette key (%s) or #rrggbb" % (ck, g[ck], ", ".join(PALETTE_KEYS))))
        if "region" in g and g["region"] not in GROUP_REGIONS:
            errs.append(Err(p + "/region", "unknown_region", "region %r is not allowed; allowed: %s (or omit it: derived from the weights)" % (g["region"], ", ".join(GROUP_REGIONS))))
        if "quality" in g and g["quality"] not in QUALITIES:
            errs.append(Err(p + "/quality", "unknown_value", "%r is not allowed; allowed values: %s" % (g["quality"], ", ".join(QUALITIES))))
    tex = mesh.get("texture")
    if tex is not None:
        if not isinstance(tex, dict):
            errs.append(Err("/mesh/texture", "bad_type", "texture must be an object"))
        else:
            for k in tex:
                if k not in TEXTURE_KEYS:
                    errs.append(Err("/mesh/texture/" + str(k), "unknown_key", "unknown texture key %r; allowed: %s" % (k, ", ".join(TEXTURE_KEYS))))
            size = tex.get("size", DEFAULT_SIZE)
            if isinstance(size, bool) or not isinstance(size, int):
                errs.append(Err("/mesh/texture/size", "bad_type", "size must be an integer, got %r" % (size,)))
            elif size not in TEXTURE_SIZES:
                errs.append(Err("/mesh/texture/size", "out_of_range", "size %r must be a power of two within %d..%d" % (size, TEXTURE_SIZES[0], TEXTURE_SIZES[-1])))
            d = tex.get("density", DEFAULT_DENSITY)
            if not _is_num(d):
                errs.append(Err("/mesh/texture/density", "bad_type", "density must be a number, got %r" % (d,)))
            elif d < DENSITY_RANGE[0] or d > DENSITY_RANGE[1]:
                errs.append(Err("/mesh/texture/density", "out_of_range", "%r is outside the allowed range %s..%s texels per block" % (d, DENSITY_RANGE[0], DENSITY_RANGE[1])))
            pad = tex.get("padding", DEFAULT_PADDING)
            if isinstance(pad, bool) or not isinstance(pad, int):
                errs.append(Err("/mesh/texture/padding", "bad_type", "padding must be an integer, got %r" % (pad,)))
            elif pad < PADDING_RANGE[0] or pad > PADDING_RANGE[1]:
                errs.append(Err("/mesh/texture/padding", "out_of_range", "%r is outside the allowed range %d..%d texels" % (pad, PADDING_RANGE[0], PADDING_RANGE[1])))
    if len(errs) > structural or not isinstance(f, str) or not f:
        return errs
    try:
        bundle = load_bundle(resolve_file(mesh, spec.get("_spec_dir")))
    except BossForgeError as e:
        errs.append(e.err)
        return errs
    for gname in groups:
        if gname not in bundle.groups:
            errs.append(Err("/mesh/groups/%s" % gname, "mesh_bad_group", "the bundle has no group %r; its groups: %s" % (gname, ", ".join(bundle.groups))))
    return errs


def group_names(n: dict) -> Optional[List[str]]:
    """Group names of the bundle of normalized spec `n`; None when it cannot be loaded."""
    m = n.get("mesh")
    if not isinstance(m, dict):
        return []
    try:
        return list(load_bundle(resolve_file(m, n.get("_spec_dir"))).groups)
    except BossForgeError:
        return None


def stamp(n: dict) -> Optional[Tuple]:
    """Identity of the bundle files of spec `n` (path, mtime, size), for caches keyed on the spec."""
    m = n.get("mesh")
    if not isinstance(m, dict) or not isinstance(m.get("file"), str):
        return None
    p = resolve_file(m, n.get("_spec_dir"))
    return _stamp(p)


def _stamp(p: Path) -> Tuple:
    out = [str(p)]
    for q in (p, meta_path_of(p)):
        try:
            st = q.stat()
            out.extend((st.st_mtime_ns, st.st_size))
        except OSError:
            out.extend((None, None))
    return tuple(out)


# --------------------------------------------------------------------------- feature targets
def retarget(names: Sequence[str]) -> List[str]:
    """`box:<glob>` targets select GROUPS of an imported mesh (a shape's private box regions do not exist here): they
    become `part:<glob>`, which matches the group name of any texel; everything else passes through."""
    return [("part:" + r[4:]) if isinstance(r, str) and r.startswith("box:") else r for r in names]


def retarget_features(features: Optional[Sequence[Any]]) -> List[Any]:
    out: List[Any] = []
    for f in features or []:
        if isinstance(f, dict) and ("region" in f or "regions" in f):
            g = dict(f)
            if isinstance(g.get("region"), str):
                g["region"] = retarget([g["region"]])[0]
            if isinstance(g.get("regions"), list):
                g["regions"] = retarget(g["regions"])
            out.append(g)
        else:
            out.append(f)
    return out


# --------------------------------------------------------------------------- bundle
@dataclass
class Bundle:
    path: Path
    meta_path: Path
    meta: dict
    mesh_json: dict
    positions: List[Vec]  # model space (y up, front = -z)
    normals: List[Vec]
    uvs: List[Tuple[float, float]]
    influences: List[Tuple[Tuple[int, float], ...]]  # per position: (joint index, weight)
    groups: List[str]
    tris: List[Tuple[str, Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int], int]]  # (EF part, p, t, n, group index)
    degenerate: int  # anchor / zero-area triangles left out
    bounds: Tuple[Vec, Vec]
    tri_regions: List[str] = field(default_factory=list)  # region derived from the weights, per triangle
    cache: Dict[Any, Any] = field(default_factory=dict)

    @property
    def max_influences(self) -> int:
        return max((len(i) for i in self.influences), default=0)


_BUNDLES: Dict[Tuple, Bundle] = {}
_CACHE_LIMIT = 4


def _err(path: str, code: str, msg: str) -> BossForgeError:
    return BossForgeError(path, code, msg)


def _read_json(p: Path, path: str, code: str, what: str) -> Any:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as e:
        raise _err(path, code, "cannot read %s %s: %s" % (what, p, e))
    except ValueError as e:
        raise _err(path, code, "%s %s is not valid JSON: %s" % (what, p, e))


def load_bundle(efmesh_path) -> Bundle:
    """Load + validate a bundle. Raises BossForgeError (paths under /mesh/file): `mesh_missing_file`, `mesh_bad_json`,
    `mesh_missing_meta`, `mesh_bad_meta`, `mesh_bad_group`."""
    p = Path(efmesh_path)
    key = _stamp(p)
    got = _BUNDLES.get(key)
    if got is not None:
        return got
    if not p.is_file():
        raise _err("/mesh/file", "mesh_missing_file", "mesh file %s not found (mesh.file is relative to the spec file)" % p)
    mj = _read_json(p, "/mesh/file", "mesh_bad_json", "mesh file")
    problems = efmodel.check_mesh_json(mj)
    if problems:
        raise _err("/mesh/file", "mesh_bad_json", "%s is not a valid Epic Fight mesh JSON: %s" % (p.name, "; ".join(problems[:5])))
    mp = meta_path_of(p)
    if not mp.is_file():
        raise _err("/mesh/file", "mesh_missing_meta", "sidecar %s not found: a bundle is <stem>.efmesh.json + <stem>.meshmeta.json (written by `bossforge blender export`)" % mp.name)
    meta = _read_json(mp, "/mesh/file", "mesh_bad_meta", "sidecar")
    bundle = _build_bundle(p, mp, mj, meta)
    while len(_BUNDLES) >= _CACHE_LIMIT:
        _BUNDLES.pop(next(iter(_BUNDLES)))
    _BUNDLES[key] = bundle
    return bundle


def _bad_meta(mp: Path, msg: str) -> BossForgeError:
    return _err("/mesh/file", "mesh_bad_meta", "%s: %s" % (mp.name, msg))


def _build_bundle(p: Path, mp: Path, mj: dict, meta: Any) -> Bundle:
    if not isinstance(meta, dict) or meta.get("schema") != META_SCHEMA:
        raise _bad_meta(mp, "must be an object with schema %r" % META_SCHEMA)
    groups = meta.get("groups")
    if not isinstance(groups, list) or not groups or not all(isinstance(g, str) for g in groups):
        raise _bad_meta(mp, "`groups` must be a non-empty list of group names")
    if len(set(groups)) != len(groups):
        raise _bad_meta(mp, "`groups` has duplicate names")
    for g in groups:
        if not GROUP_RE.match(g):
            raise _err("/mesh/file", "mesh_bad_group", "group name %r must match %s (rename the material slot in Blender: lowercase snake_case)" % (g, GROUP_RE.pattern))
    names = joint_names()
    if "joint_names" in meta and meta["joint_names"] != names:
        raise _bad_meta(mp, "`joint_names` differs from the EF biped joint order of rigs.json (%s)" % ", ".join(names))
    if "height" in meta and (not _is_num(meta["height"]) or meta["height"] <= 0):
        raise _bad_meta(mp, "`height` must be a positive number of blocks")
    gb = meta.get("group_bones", {})
    if not isinstance(gb, dict) or any(g not in groups or b not in names for g, b in gb.items()):
        raise _bad_meta(mp, "`group_bones` must map group names to EF joint names")
    tg = meta.get("tri_groups")
    if not isinstance(tg, dict):
        raise _bad_meta(mp, "`tri_groups` must map every EF part to the group index of each of its triangles")
    v = mj["vertices"]
    flat = v["positions"]["array"]
    nflat = v["normals"]["array"]
    uflat = v["uvs"]["array"]
    positions = [efmodel.from_blender(flat[i:i + 3]) for i in range(0, len(flat), 3)]
    normals = [efmodel.from_blender(nflat[i:i + 3]) for i in range(0, len(nflat), 3)]
    uvs = [(uflat[i], uflat[i + 1]) for i in range(0, len(uflat), 2)]
    weights = v["weights"]["array"]
    vcounts, vind = v["vcounts"]["array"], v["vindices"]["array"]
    infl: List[Tuple[Tuple[int, float], ...]] = []
    k = 0
    for c in vcounts:
        infl.append(tuple((vind[k + 2 * i], weights[vind[k + 2 * i + 1]]) for i in range(c)))
        k += 2 * c
    tris: List[Tuple[str, Tuple[int, int, int], Tuple[int, int, int], Tuple[int, int, int], int]] = []
    degenerate = 0
    used = set()
    for part in efmodel.EF_PARTS:
        arr = v["parts"][part]["array"]
        ntri = len(arr) // 9
        gl = tg.get(part, [])
        if not isinstance(gl, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in gl):
            raise _bad_meta(mp, "tri_groups.%s must be a list of integers" % part)
        real = []
        for i in range(ntri):
            idx = arr[9 * i:9 * i + 9]
            pi = (idx[0], idx[3], idx[6])
            a, b, c = (positions[q] for q in pi)
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            gx, gy, gz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            if gx * gx + gy * gy + gz * gz < DEGENERATE_AREA2:
                degenerate += 1
                continue
            real.append((i, pi, (idx[1], idx[4], idx[7]), (idx[2], idx[5], idx[8])))
        if real and len(gl) != ntri:
            raise _bad_meta(mp, "tri_groups.%s has %d entries but the part has %d triangles" % (part, len(gl), ntri))
        for i, pi, ti, ni in real:
            g = gl[i]
            if g < 0 or g >= len(groups):
                raise _bad_meta(mp, "tri_groups.%s[%d] = %d is outside 0..%d" % (part, i, g, len(groups) - 1))
            tris.append((part, pi, ti, ni, g))
            used.update(pi)
    if not tris:
        raise _err("/mesh/file", "mesh_bad_json", "%s has no triangles (only the degenerate anchors of empty parts)" % p.name)
    pts = [positions[i] for i in used]
    bounds = ((min(q[0] for q in pts), min(q[1] for q in pts), min(q[2] for q in pts)),
              (max(q[0] for q in pts), max(q[1] for q in pts), max(q[2] for q in pts)))
    b = Bundle(path=p, meta_path=mp, meta=meta, mesh_json=mj, positions=positions, normals=normals, uvs=uvs, influences=infl,
               groups=list(groups), tris=tris, degenerate=degenerate, bounds=bounds)
    b.tri_regions = _derive_regions(b)
    return b


def _derive_regions(b: Bundle) -> List[str]:
    """Region of every triangle: the one that carries most of the vertex weights of its three vertices."""
    jr = joint_regions()
    order = {r: i for i, r in enumerate(REGIONS)}
    vreg: Dict[int, Dict[str, float]] = {}

    def of(vi: int) -> Dict[str, float]:
        got = vreg.get(vi)
        if got is None:
            got = {}
            for j, w in b.influences[vi]:
                r = jr[j]
                got[r] = got.get(r, 0.0) + w
            vreg[vi] = got
        return got

    out = []
    for _part, pi, _ti, _ni, _g in b.tris:
        acc: Dict[str, float] = {}
        for vi in pi:
            for r, w in of(vi).items():
                acc[r] = acc.get(r, 0.0) + w
        out.append(max(acc, key=lambda r: (round(acc[r], 9), -order[r])) if acc else "torso")
    return out


# --------------------------------------------------------------------------- analysis
def _cross_len(a: Vec, b: Vec, c: Vec) -> float:
    ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
    gx, gy, gz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    return math.sqrt(gx * gx + gy * gy + gz * gz)


def _raster_counts(tri_uvs: Sequence[Tuple[Tuple[float, float], ...]], res: int) -> bytearray:
    """Per texel of a res x res atlas: how many triangles contain its centre strictly inside (capped at 255)."""
    counts = bytearray(res * res)
    for pts in tri_uvs:
        (x0, y0), (x1, y1), (x2, y2) = [(u * res, vv * res) for u, vv in pts]
        det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(det) < 1e-12:
            continue
        minx = max(0, int(math.floor(min(x0, x1, x2))))
        maxx = min(res - 1, int(math.ceil(max(x0, x1, x2))))
        miny = max(0, int(math.floor(min(y0, y1, y2))))
        maxy = min(res - 1, int(math.ceil(max(y0, y1, y2))))
        for py in range(miny, maxy + 1):
            cy = py + 0.5
            row = py * res
            for px in range(minx, maxx + 1):
                cx = px + 0.5
                l0 = ((y1 - y2) * (cx - x2) + (x2 - x1) * (cy - y2)) / det
                if l0 <= 1e-9:
                    continue
                l1 = ((y2 - y0) * (cx - x2) + (x0 - x2) * (cy - y2)) / det
                if l1 <= 1e-9 or 1.0 - l0 - l1 <= 1e-9:
                    continue
                if counts[row + px] < 255:
                    counts[row + px] += 1
    return counts


def analyze(b: Bundle, size: int) -> dict:
    """Measurements of a bundle for an atlas of `size` texels (cached on the bundle): per group triangle / surface /
    UV figures, texel density (texels per block), UV bounds problems, UV coverage and overlap."""
    key = ("analysis", size)
    got = b.cache.get(key)
    if got is not None:
        return got
    pos, uvs = b.positions, b.uvs
    groups: Dict[str, dict] = {}
    outside = 0
    outside_groups: Dict[str, int] = {}
    unmapped = 0
    tri_uvs = []
    uv_total = 0.0
    for (part, pi, ti, _ni, g), region in zip(b.tris, b.tri_regions):
        name = b.groups[g]
        a, bb, c = (pos[q] for q in pi)
        surface = 0.5 * _cross_len(a, bb, c)
        (u0, v0), (u1, v1), (u2, v2) = (uvs[q] for q in ti)
        uv_area = abs((u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0)) * 0.5
        st = groups.get(name)
        if st is None:
            st = groups[name] = {"triangles": 0, "surface": 0.0, "uv_area": 0.0, "min": [1e18] * 3, "max": [-1e18] * 3, "regions": {}}
        st["triangles"] += 1
        st["surface"] += surface
        st["uv_area"] += uv_area
        st["regions"][region] = st["regions"].get(region, 0) + 1
        for q in pi:
            pp = pos[q]
            for k in range(3):
                if pp[k] < st["min"][k]:
                    st["min"][k] = pp[k]
                if pp[k] > st["max"][k]:
                    st["max"][k] = pp[k]
        uv_total += uv_area
        if any(u < -UV_TOLERANCE or u > 1.0 + UV_TOLERANCE or vv < -UV_TOLERANCE or vv > 1.0 + UV_TOLERANCE for u, vv in ((u0, v0), (u1, v1), (u2, v2))):
            outside += 1
            outside_groups[name] = outside_groups.get(name, 0) + 1
        if uv_area < 1e-12 and surface > 1e-9:
            unmapped += 1
        tri_uvs.append(((u0, v0), (u1, v1), (u2, v2)))
    for name, st in groups.items():
        texels = st["uv_area"] * size * size
        st["density"] = math.sqrt(texels / st["surface"]) if st["surface"] > 1e-12 and texels > 0 else 0.0
        st["uv_coverage"] = st["uv_area"]
    res = min(size, OVERLAP_RES)
    counts = _raster_counts(tri_uvs, res)
    zero, one = counts.count(0), counts.count(1)
    covered = res * res - zero
    overlapped = res * res - zero - one
    out = {"groups": groups, "uv_outside": outside, "uv_outside_groups": outside_groups, "uv_unmapped": unmapped,
           "uv_area": uv_total, "uv_covered": covered / float(res * res), "uv_overlap": (overlapped / float(covered)) if covered else 0.0,
           "resolution": res}
    b.cache[key] = out
    return out


def _warnings_for(b: Bundle, an: dict, size: int, group_regions: Dict[str, str]) -> List[Err]:
    warns: List[Err] = []
    if an["uv_outside"]:
        parts = ", ".join("%s (%d)" % (g, c) for g, c in sorted(an["uv_outside_groups"].items()))
        warns.append(Err("/mesh/file", "mesh_uv_outside", "%d of %d triangles have UV coordinates outside 0..1 (%s): the texture only exists inside the unit square, "
                         "so the parts of those triangles outside it stay empty; fit every island into the unit square (Blender: UV editor, Pack Islands)" % (an["uv_outside"], len(b.tris), parts)))
    if an["uv_unmapped"]:
        warns.append(Err("/mesh/file", "mesh_uv_degenerate", "%d triangles have no UV area (collapsed in the UV map): they stay unpainted" % an["uv_unmapped"]))
    if an["uv_overlap"] > OVERLAP_WARN:
        warns.append(Err("/mesh/file", "mesh_uv_overlap", "%.1f%% of the covered texels belong to more than one triangle (overlapping or mirrored UV islands): "
                         "the painter colours a texel from the last triangle that covers it, so the other copies get wrong shading and features. Unwrap every part into its own space"
                         % (100.0 * an["uv_overlap"])))
    for name in b.groups:
        st = an["groups"].get(name)
        if st is None:
            continue
        if st["density"] < MIN_DENSITY:
            warns.append(Err("/mesh/groups/%s" % name, "mesh_low_density", "group %r has %.1f texels per block (%d triangles, %.2f blocks^2 of surface on %.1f%% of the atlas); "
                             "at least %g are needed for readable detail: enlarge its UV islands (or raise mesh.texture.size)"
                             % (name, st["density"], st["triangles"], st["surface"], 100.0 * st["uv_coverage"], MIN_DENSITY)))
    lo, hi = b.bounds
    if lo[1] < GROUND_TOLERANCE[0] or lo[1] > GROUND_TOLERANCE[1]:
        warns.append(Err("/mesh/file", "mesh_not_on_ground", "the lowest point of the mesh is at y=%.3f; the feet must stand on y=0 (the entity scale is height / the mesh's top y)" % lo[1]))
    mh = b.meta.get("height")
    if _is_num(mh) and abs(mh - (hi[1] - lo[1])) > HEIGHT_TOLERANCE * max(hi[1] - lo[1], 1e-6):
        warns.append(Err("/mesh/file", "mesh_height_mismatch", "%s says height %.3f but the geometry is %.3f blocks tall: re-export the bundle" % (b.meta_path.name, mh, hi[1] - lo[1])))
    # arms the wrong way round: the character's right arm (joint Arm_R) must be at +x
    sums = {"arm_r": [0.0, 0], "arm_l": [0.0, 0]}
    for (_p, pi, _t, _n, _g), region in zip(b.tris, b.tri_regions):
        if region in sums:
            for q in pi:
                sums[region][0] += b.positions[q][0]
                sums[region][1] += 1
    if sums["arm_r"][1] and sums["arm_l"][1] and sums["arm_r"][0] / sums["arm_r"][1] < sums["arm_l"][0] / sums["arm_l"][1]:
        warns.append(Err("/mesh/file", "mesh_left_right_swapped", "the vertices weighted to Arm_R lie at smaller x than those of Arm_L: the character's right side is +x (front = -z); "
                         "check the export axes and the weight names"))
    # groups that stretch a shared region frame
    wide = 0
    for name in b.groups:
        st = an["groups"].get(name)
        if st is None or group_regions.get(name) == "private" or wide >= MAX_WIDE_WARNINGS:
            continue
        for region in sorted(st["regions"]):
            if group_regions.get(name) not in (None, region):
                continue
            ext = _group_region_extent(b, name, region)
            stock = STOCK_EXTENT[region]
            if any(ext[k] > max(WIDE_FACTOR * stock[k], stock[k] + WIDE_MARGIN) for k in range(3)):
                warns.append(Err("/mesh/groups/%s" % name, "mesh_region_frame_wide",
                                 "group %r spans %.2f x %.2f x %.2f blocks in region %s (stock %s %.2f x %.2f x %.2f): all non-private groups of a region share one frame for "
                                 "eyes / emblem / trim / gradient, so this one distorts it. Set mesh.groups.%s.region to \"private\" (wings, capes, hair, crowns, staffs)"
                                 % (name, ext[0], ext[1], ext[2], region, region, stock[0], stock[1], stock[2], name)))
                wide += 1
                break
    return warns


def _group_region_extent(b: Bundle, name: str, region: str) -> Vec:
    lo = [1e18] * 3
    hi = [-1e18] * 3
    gi = b.groups.index(name)
    for (_p, pi, _t, _n, g), r in zip(b.tris, b.tri_regions):
        if g != gi or r != region:
            continue
        for q in pi:
            pp = b.positions[q]
            for k in range(3):
                if pp[k] < lo[k]:
                    lo[k] = pp[k]
                if pp[k] > hi[k]:
                    hi[k] = pp[k]
    return (hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2])


# --------------------------------------------------------------------------- model
class _Tri(efmesh.Tri):
    __slots__ = ("region",)


class RegionMesh(efmesh.Mesh):
    """efmesh.Mesh whose triangles carry their own region; `tri_island[i]` identifies the UV island of triangle i."""

    def __init__(self, positions, normals, uvs, tris, part_regions, tri_island):
        super().__init__(positions, normals, uvs, tris, part_regions)
        self.tri_island = tri_island

    def region_of_tri(self, tri) -> Optional[str]:
        return tri.region


def _islands(b: Bundle) -> List[int]:
    """UV island (connected through shared UV points) of every triangle, numbered in order of first appearance."""
    parent: Dict[Tuple[int, int], Tuple[int, int]] = {}

    def find(k):
        root = k
        while parent[root] != root:
            root = parent[root]
        while parent[k] != root:
            parent[k], k = root, parent[k]
        return root

    keys = []
    for _part, _pi, ti, _ni, _g in b.tris:
        ks = []
        for q in ti:
            u, v = b.uvs[q]
            k = (int(round(u * 10000.0)), int(round(v * 10000.0)))
            parent.setdefault(k, k)
            ks.append(k)
        for k in ks[1:]:
            ra, rb = find(ks[0]), find(k)
            if ra != rb:
                parent[rb] = ra
        keys.append(ks[0])
    numbering: Dict[Tuple[int, int], int] = {}
    out = []
    for k in keys:
        r = find(k)
        out.append(numbering.setdefault(r, len(numbering)))
    return out


@dataclass
class MeshModel:
    """What the pipeline consumes for a `mesh` boss (see the module docstring); mirrors `shape.ShapeModel`."""
    mesh: RegionMesh
    tpl: dict
    atlas: Tuple[int, int]
    mesh_json: dict
    height: float
    bounds: Tuple[Vec, Vec]
    warnings: List[Err]
    boxes: List[dict]  # one entry per group (the fit / shape commands count boxes)
    proportions: dict = field(default_factory=dict)
    box_counts: Dict[str, int] = field(default_factory=dict)  # triangles per group
    relief: List[dict] = field(default_factory=list)
    atlas_fill: float = 0.0  # fraction of the atlas covered by triangles
    info: dict = field(default_factory=dict)

    def silhouette_polys(self, axis: int) -> List[Tuple[str, List[Tuple[float, float]]]]:
        """Screen-space polygons (u, y) of the mesh seen along the other horizontal axis (`axis` 0 = x, 2 = z): one
        triangle each (identified by its group), edge-on and duplicate triangles left out. `fit` rasterizes them."""
        pos = self.mesh.positions
        seen = set()
        out = []
        for tri in self.mesh.tris:
            pts = [(pos[i][axis], pos[i][1]) for i in tri.p]
            (x0, y0), (x1, y1), (x2, y2) = pts
            if abs((x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0)) < 1e-9:
                continue
            key = tuple(sorted((round(x, 5), round(y, 5)) for x, y in pts))
            if key in seen:
                continue
            seen.add(key)
            out.append((tri.part, pts))
        return out


_MODELS: Dict[Tuple, MeshModel] = {}


def build_model(n: dict, ctx=None) -> MeshModel:
    """Build the model of a normalized spec `n` (uses n["mesh"], n["id"], n["mod_id"], n["_spec_dir"]). Raises
    BossForgeError (`mesh_*`)."""
    m = n.get("mesh")
    if not isinstance(m, dict):
        raise BossForgeError("/mesh", "required", "the spec has no mesh object")
    if isinstance(n.get("shape"), dict):
        raise BossForgeError("/mesh", "mesh_and_shape", "a spec has either `shape` (boxes) or `mesh` (a Blender bundle), not both")
    path = resolve_file(m, n.get("_spec_dir"))
    size, density, padding = texture_of(m)
    model_id = "%s:entity/%s" % (n.get("mod_id", "mod"), n.get("id", "boss"))
    key = (_stamp(path), json.dumps(m.get("groups") or {}, sort_keys=True, default=str), size, density, padding, model_id)
    got = _MODELS.get(key)
    if got is not None:
        return got
    b = load_bundle(path)
    cfg = m.get("groups") or {}
    for gname in cfg:
        if gname not in b.groups:
            raise BossForgeError("/mesh/groups/%s" % gname, "mesh_bad_group", "the bundle has no group %r; its groups: %s" % (gname, ", ".join(b.groups)))
    model = _make_model(n, m, b, cfg, size, density, padding, model_id)
    while len(_MODELS) >= _CACHE_LIMIT:
        _MODELS.pop(next(iter(_MODELS)))
    _MODELS[key] = model
    return model


def _make_model(n: dict, m: dict, b: Bundle, cfg: dict, size: int, density: float, padding: int, model_id: str) -> MeshModel:
    group_regions = {g: c["region"] for g, c in cfg.items() if isinstance(c, dict) and c.get("region")}
    tris: List[efmesh.Tri] = []
    for (part, pi, ti, ni, g), derived in zip(b.tris, b.tri_regions):
        name = b.groups[g]
        fixed = group_regions.get(name)
        t = _Tri(name, pi, ti, ni)
        t.region = ("box:" + name) if fixed == "private" else (fixed or derived)
        tris.append(t)
    islands = _islands(b)
    styles = {}
    part_regions = {}
    for name in b.groups:
        c = cfg.get(name) or {}
        if any(c.get(k) is not None for k in ("material", "color", "accent", "quality")):
            styles[name] = {"material": c.get("material"), "color": c.get("color"), "accent": c.get("accent"), "quality": c.get("quality")}
        fixed = group_regions.get(name)
        if fixed is not None:
            part_regions[name] = ("box:" + name) if fixed == "private" else fixed
    mesh = RegionMesh(b.positions, b.normals, b.uvs, tris, part_regions, islands)
    lo, hi = b.bounds
    height = hi[1]
    tpl = {
        "label": "Imported mesh (EF biped rig)", "humanoid": True, "mesh_name": None, "model_id": model_id,
        "armature_id": "epicfight:entity/biped", "renderer": "player",
        "part_regions": part_regions, "part_style": styles, "overlay_parts": [],
        "overlay_to_base": True, "authored_uv": [size, size], "uv_origin": "top", "front_sign": -1,
        "right_sign": 1, "axis_mode": "x,z,-y", "unit_scale": 1.0, "index_order": "pos,uv,nrm",
        "model_height": height, "weapon_capable": True, "custom": True,
        "mesh_source": True, "max_edge": MAX_EDGE, "dilate": padding,
    }
    an = analyze(b, size)
    warnings = _warnings_for(b, an, size, group_regions)
    gi = {}
    for name in b.groups:
        st = an["groups"].get(name)
        if st is None:
            gi[name] = {"triangles": 0}
            continue
        regions = dict(sorted(st["regions"].items()))
        fixed = group_regions.get(name)
        gi[name] = {
            "triangles": st["triangles"], "surface_blocks2": round(st["surface"], 4), "texels_per_block": round(st["density"], 2),
            "uv_coverage": round(st["uv_coverage"], 4),
            "region": ("private" if fixed == "private" else fixed) if fixed else "derived",
            "derived_regions": regions,
            "bbox": {"min": [round(c, 3) for c in st["min"]], "max": [round(c, 3) for c in st["max"]]},
        }
        if name in styles:
            gi[name]["style"] = {k: v for k, v in styles[name].items() if v is not None}
    region_tris: Dict[str, int] = {}
    for t in tris:
        region_tris[t.region] = region_tris.get(t.region, 0) + 1
    info = {
        "file": str(b.path), "meta": str(b.meta_path), "vertices": len(b.positions), "triangles": len(b.tris), "degenerate_triangles": b.degenerate,
        "max_influences": b.max_influences, "height": round(height, 4),
        "bounds": {"min": [round(c, 3) for c in lo], "max": [round(c, 3) for c in hi]},
        "texture": {"size": size, "padding": padding, "uv_coverage": round(an["uv_covered"], 4), "uv_overlap": round(an["uv_overlap"], 4),
                    "uv_outside_triangles": an["uv_outside"], "measured_at": an["resolution"]},
        "regions": dict(sorted(region_tris.items())), "groups": gi,
    }
    boxes = [{"id": name, "bone": None, "min": gi[name].get("bbox", {}).get("min"), "size": None} for name in b.groups]
    return MeshModel(mesh=mesh, tpl=tpl, atlas=(size, size), mesh_json=b.mesh_json, height=height, bounds=b.bounds, warnings=warnings,
                     boxes=boxes, proportions={}, box_counts={name: gi[name]["triangles"] for name in b.groups}, relief=[],
                     atlas_fill=round(an["uv_covered"], 4), info=info)


# --------------------------------------------------------------------------- staging into a project
def stage_bundle(raw_bytes: bytes, spec_path) -> Tuple[bytes, List[GenFile]]:
    """`add-boss`: copy the bundle files (`.efmesh.json`, `.meshmeta.json`, and `<stem>.blend` when it sits next to
    them) to `bossforge/models/<boss id>.*` and rewrite `mesh.file` in the project copy of the spec (which lives in
    `bossforge/bosses/`) to `../models/<boss id>.efmesh.json`. Specs without a `mesh` block pass through unchanged."""
    try:
        d = json.loads(raw_bytes.decode("utf-8"))
    except ValueError:
        return raw_bytes, []
    m = d.get("mesh") if isinstance(d, dict) else None
    if not isinstance(m, dict) or not isinstance(m.get("file"), str) or not isinstance(d.get("id"), str):
        return raw_bytes, []
    src = resolve_file(m, str(Path(spec_path).resolve().parent))
    meta = meta_path_of(src)
    if not src.is_file() or not meta.is_file():
        return raw_bytes, []  # validation reports it
    stem = src.name[:-len(EFMESH_SUFFIX)] if src.name.endswith(EFMESH_SUFFIX) else src.stem
    out = [GenFile("bossforge/models/%s%s" % (d["id"], EFMESH_SUFFIX), src.read_bytes(), "model"),
           GenFile("bossforge/models/%s%s" % (d["id"], META_SUFFIX), meta.read_bytes(), "model")]
    blend = src.with_name(stem + ".blend")
    if blend.is_file():
        out.append(GenFile("bossforge/models/%s.blend" % d["id"], blend.read_bytes(), "model"))
    m["file"] = "../models/%s%s" % (d["id"], EFMESH_SUFFIX)
    return (json.dumps(d, indent=2, ensure_ascii=False) + "\n").encode("utf-8"), out


# --------------------------------------------------------------------------- bundle writer
_REGION_PART = {"head": "head", "torso": "torso", "arm_r": "rightArm", "arm_l": "leftArm", "leg_r": "rightLeg", "leg_l": "leftLeg"}


def write_bundle(out_dir, stem: str, positions: Sequence[Sequence[float]], normals: Sequence[Sequence[float]],
                 uvs: Sequence[Sequence[float]], tris: Sequence[Tuple[Sequence[Tuple[int, int, int]], int]],
                 groups: Sequence[str], influences: Sequence[Sequence[Tuple[int, float]]], source: str = "python") -> dict:
    """Write a bundle from plain Python data (the Blender exporter is the normal route; this is for generators and tests).

    `positions` / `normals`: model space (y up, front = -z, feet at 0); `uvs`: (u, v) with v from the TOP row; `tris`:
    `(((position, uv, normal) x 3), group index)`, counter-clockwise seen from outside; `influences[i]`: `[(joint index,
    weight)]` of position i (normalised to sum 1). Every triangle goes to the EF part of the region its vertex weights
    favour; empty parts get the degenerate anchor triangle. Returns {"efmesh", "meshmeta"} paths."""
    names = joint_names()
    jr = joint_regions()
    nj = len(names)
    if len(influences) != len(positions):
        raise BossForgeError("/mesh", "bad_mesh", "influences has %d entries for %d positions" % (len(influences), len(positions)))
    pos = [tuple(p) for p in positions]
    nrm = [tuple(q) for q in normals]
    uv = [tuple(u) for u in uvs]
    norm_infl = []
    for i, infl in enumerate(influences):
        total = float(sum(w for _j, w in infl))
        if not infl or total <= 0.0 or any(not 0 <= j < nj for j, _w in infl):
            raise BossForgeError("/mesh", "bad_mesh", "position %d needs joint influences within 0..%d with a positive sum" % (i, nj - 1))
        norm_infl.append([(int(j), efmodel.rnd(w / total)) for j, w in infl])
    order = {r: i for i, r in enumerate(REGIONS)}
    part_tris: Dict[str, List[Tuple[Tuple[int, int, int], int]]] = {p: [] for p in efmodel.EF_PARTS}
    for corners, g in tris:
        acc: Dict[str, float] = {}
        for pi, _ti, _ni in corners:
            for j, w in norm_infl[pi]:
                acc[jr[j]] = acc.get(jr[j], 0.0) + w
        region = max(acc, key=lambda r: (round(acc[r], 9), -order[r]))
        part_tris[_REGION_PART[region]].append((tuple(corners), int(g)))
    tri_groups: Dict[str, List[int]] = {}
    arrays: Dict[str, List[int]] = {}
    anchor = None
    for part in efmodel.EF_PARTS:
        lst = part_tris[part]
        if lst:
            arrays[part] = [int(c) for corners, _g in lst for v in corners for c in v]
            tri_groups[part] = [g for _c, g in lst]
            continue
        if anchor is None:
            uv.append((0.0, 0.0))
            nrm.append((0.0, 1.0, 0.0))
            anchor = (len(uv) - 1, len(nrm) - 1)
        j = efmodel.joint_by_name(efmodel.PART_JOINT[part])
        pos.append(tuple(j["pos"]))
        norm_infl.append([(j["index"], 1.0)])
        p = len(pos) - 1
        arrays[part] = [p, anchor[0], anchor[1]] * 3
        tri_groups[part] = [0]
    weights = [1.0]
    widx = {1.0: 0}
    vcounts: List[int] = []
    vindices: List[int] = []
    for infl in norm_infl:
        vcounts.append(len(infl))
        for j, w in infl:
            if w not in widx:
                widx[w] = len(weights)
                weights.append(w)
            vindices.extend((j, widx[w]))
    flat_pos = [efmodel.rnd(c) for p in pos for c in efmodel.to_blender(p)]
    flat_nrm = [efmodel.rnd(c) for q in nrm for c in efmodel.to_blender(q)]
    flat_uv = [efmodel.rnd(c) for u in uv for c in u[:2]]
    mesh_json = {"vertices": {
        "positions": {"array": flat_pos}, "normals": {"array": flat_nrm}, "uvs": {"array": flat_uv},
        "vcounts": {"array": vcounts}, "weights": {"array": weights}, "vindices": {"array": vindices},
        "parts": {p: {"array": arrays[p]} for p in efmodel.EF_PARTS}}}
    problems = efmodel.check_mesh_json(mesh_json)
    if problems:
        raise BossForgeError("/mesh", "bad_mesh", "the mesh is not a valid Epic Fight mesh: " + "; ".join(problems[:5]))
    used = {pi for corners, _g in tris for pi, _t, _n in corners}
    ys = [pos[i][1] for i in used]
    meta = {"schema": META_SCHEMA, "id": stem, "height": round(max(ys) - min(ys), 5), "units": "blocks", "joint_names": names,
            "groups": list(groups), "tri_groups": tri_groups, "source": source, "generator": "meshsrc/1"}
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    efp = out / (stem + EFMESH_SUFFIX)
    mp = out / (stem + META_SUFFIX)
    efp.write_text(efmodel.dumps(mesh_json) + "\n", encoding="utf-8")
    mp.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return {"efmesh": str(efp), "meshmeta": str(mp)}


# --------------------------------------------------------------------------- shape -> bundle
def bundle_from_shape(n: dict, out_dir, stem: Optional[str] = None) -> dict:
    """Write the box model of a normalized `shape` spec as a bundle (one group per box id) into `out_dir`: the way to
    start a Blender edit of an existing box boss (`bossforge blender import OUT_DIR/<stem>.efmesh.json`), and the
    reference the `mesh` pipeline is tested against. Returns {"efmesh", "meshmeta", "mesh"}: the paths and the `mesh`
    spec block (with `groups` carrying every box's material / colour / accent / region and `texture.size` = the atlas)
    that reproduces the shape's painting; replace the spec's `shape` by it (`spec_from_shape`)."""
    from . import shape
    model = shape.build_model(n)
    box_by_id = {bx["id"]: bx for bx in model.boxes}
    groups = [bx["id"] for bx in model.boxes]
    gindex = {g: i for i, g in enumerate(groups)}
    tri_groups: Dict[str, List[int]] = {p: [] for p in efmodel.EF_PARTS}
    for tri in model.mesh.tris:
        tri_groups[shape.ef_part(box_by_id[tri.part])].append(gindex[tri.part])
    for part in efmodel.EF_PARTS:
        if not tri_groups[part]:
            tri_groups[part] = [0]  # the degenerate anchor triangle of an empty part
    stem = stem or str(n.get("id", "boss"))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    joints = {j["index"]: j["name"] for j in data("rigs")["biped"]["joints"]}
    bone_joint = {bx["id"]: joints[shape.BONES[bx["bone"]]["index"]] for bx in model.boxes}
    meta = {"schema": META_SCHEMA, "id": stem, "height": round(model.height - model.bounds[0][1], 5), "units": "blocks",
            "joint_names": joint_names(), "groups": groups, "tri_groups": tri_groups, "group_bones": bone_joint,
            "source": "shape:%s" % n.get("id", "boss"), "generator": "meshsrc/1"}
    efp = out / (stem + EFMESH_SUFFIX)
    mp = out / (stem + META_SUFFIX)
    efp.write_text(efmodel.dumps(model.mesh_json) + "\n", encoding="utf-8")
    mp.write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    cfg: Dict[str, dict] = {}
    for bx in model.boxes:
        region = model.tpl["part_regions"][bx["id"]]
        entry: Dict[str, Any] = {"region": "private" if region.startswith("box:") else region}
        for k in ("material", "color", "accent"):
            if bx.get(k) is not None:
                entry[k] = bx[k]
        cfg[bx["id"]] = entry
    return {"efmesh": str(efp), "meshmeta": str(mp),
            "mesh": {"file": efp.name, "groups": cfg, "texture": {"size": model.atlas[0]}}}


def spec_from_shape(raw: dict, out_dir, stem: Optional[str] = None) -> dict:
    """The `mesh` twin of a raw `shape` spec: the same spec with `shape` replaced by the bundle written to `out_dir`
    (`mesh.file` relative; `_spec_dir` = `out_dir` so it resolves)."""
    from . import spec as S
    n = S.normalize(raw)
    res = bundle_from_shape(n, out_dir, stem or raw.get("id"))
    twin = {k: copy.deepcopy(v) for k, v in raw.items() if k != "shape"}
    twin["mesh"] = res["mesh"]
    twin["_spec_dir"] = str(Path(out_dir).resolve())
    return twin
