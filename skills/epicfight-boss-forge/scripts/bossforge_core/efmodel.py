"""Epic Fight mesh JSON writer/validator for custom (box-built) humanoid meshes.

Layout follows EF's `JsonAssetLoader` (verified against the 20.14.17 jar): one JSON object
`{"vertices": {positions, normals, uvs, vcounts, weights, vindices, parts}}`, every member `{"array": [...]}`
except `parts` (`{"<part>": {"array": [ints]}}`). Positions/normals are Blender Z-up (the game rotates them by
-90 degrees about X), so our y-up model space (x, y, z) is stored as (x, -z, y). Each consecutive int triple in a
part array is one vertex `(position index, uv index, normal index)`; three vertices make one triangle.
`HumanoidMesh` looks up exactly the 12 names in `EF_PARTS`, so a custom mesh must contain all of them, non-empty.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

from .spec import BossForgeError, data

Vec = Tuple[float, float, float]
Vertex = Tuple[int, int, int]  # (position index, uv index, normal index)
Tri3 = Tuple[Vertex, Vertex, Vertex]

DECIMALS = 5
EF_PARTS = ("head", "hat", "torso", "jacket", "rightArm", "leftArm", "rightLeg", "leftLeg",
            "rightSleeve", "leftSleeve", "rightPants", "leftPants")
# joint that anchors an otherwise empty part's degenerate triangle
PART_JOINT = {
    "head": "Head", "hat": "Head",
    "torso": "Chest", "jacket": "Chest",
    "rightArm": "Arm_R", "rightSleeve": "Arm_R",
    "leftArm": "Arm_L", "leftSleeve": "Arm_L",
    "rightLeg": "Thigh_R", "rightPants": "Thigh_R",
    "leftLeg": "Thigh_L", "leftPants": "Thigh_L",
}
MEMBERS = ("positions", "normals", "uvs", "vcounts", "weights", "vindices")
WINDING_MIN_AGREE = 0.95


def _rig() -> dict:
    return data("rigs")["biped"]


def joint_count() -> int:
    return len(_rig()["joints"])


def joint_by_name(name: str) -> dict:
    for j in _rig()["joints"]:
        if j["name"] == name:
            return j
    raise BossForgeError("/rig", "unknown_joint", "no joint %r in the biped armature" % (name,))


def rnd(x: float) -> float:
    """Round to DECIMALS places; `+ 0.0` turns -0.0 into 0.0 so output never contains '-0.0'."""
    return round(float(x), DECIMALS) + 0.0


def to_blender(v: Sequence[float]) -> Vec:
    """y-up model space -> EF/Blender Z-up storage space."""
    return (v[0], -v[2], v[1])


def from_blender(v: Sequence[float]) -> Vec:
    return (v[0], v[2], -v[1])


def dumps(obj) -> str:
    """Compact deterministic serialization (insertion order kept)."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True)


def write_mesh_json(positions: Sequence[Sequence[float]], normals: Sequence[Sequence[float]],
                    uvs: Sequence[Sequence[float]], part_tris: Dict[str, Sequence[Tri3]],
                    joint_of_position: Sequence[int]) -> dict:
    """Build the EF mesh JSON dict.

    positions/normals are y-up model space; uvs are (u, v) with v measured from the top row; `part_tris` maps an
    EF part name to triangles, each three `(position, uv, normal)` index triples; `joint_of_position[i]` is the
    armature joint index that position i is rigidly bound to (weight 1.0). Parts that are missing or empty get a
    degenerate triangle at their anchor joint so every one of the 12 EF parts has a non-empty array.
    """
    if len(joint_of_position) != len(positions):
        raise BossForgeError("/mesh", "bad_mesh", "joint_of_position has %d entries for %d positions" % (len(joint_of_position), len(positions)))
    for name in part_tris:
        if name not in EF_PARTS:
            raise BossForgeError("/mesh/parts/" + str(name), "bad_part", "unknown EF part %r (allowed: %s)" % (name, ", ".join(EF_PARTS)))
    pos = [tuple(p) for p in positions]
    nrm = [tuple(n) for n in normals]
    uv = [tuple(u) for u in uvs]
    joints = [int(j) for j in joint_of_position]
    nj = joint_count()
    for j in joints:
        if j < 0 or j >= nj:
            raise BossForgeError("/mesh", "bad_joint", "joint index %d outside 0..%d" % (j, nj - 1))
    parts: Dict[str, List[int]] = {}
    degenerate_uv = None  # type: Optional[int]
    degenerate_n = None  # type: Optional[int]
    for name in EF_PARTS:
        tris = part_tris.get(name) or []
        arr: List[int] = []
        for tri in tris:
            for v in tri:
                arr.extend((int(v[0]), int(v[1]), int(v[2])))
        if not arr:
            if degenerate_uv is None:
                uv.append((0.0, 0.0))
                degenerate_uv = len(uv) - 1
                nrm.append((0.0, 1.0, 0.0))
                degenerate_n = len(nrm) - 1
            j = joint_by_name(PART_JOINT[name])
            pos.append(tuple(j["pos"]))
            joints.append(j["index"])
            p = len(pos) - 1
            arr = [p, degenerate_uv, degenerate_n] * 3
        parts[name] = arr
    flat_pos: List[float] = []
    for p in pos:
        flat_pos.extend(rnd(c) for c in to_blender(p))
    flat_nrm: List[float] = []
    for n in nrm:
        flat_nrm.extend(rnd(c) for c in to_blender(n))
    flat_uv: List[float] = []
    for u in uv:
        flat_uv.extend((rnd(u[0]), rnd(u[1])))
    vindices: List[int] = []
    for j in joints:
        vindices.extend((j, 0))
    return {"vertices": {
        "positions": {"array": flat_pos},
        "normals": {"array": flat_nrm},
        "uvs": {"array": flat_uv},
        "vcounts": {"array": [1] * len(pos)},
        "weights": {"array": [1.0]},
        "vindices": {"array": vindices},
        "parts": {name: {"array": parts[name]} for name in EF_PARTS},
    }}


def _array(v: dict, key: str, errs: List[str]) -> Optional[list]:
    obj = v.get(key)
    if not isinstance(obj, dict) or not isinstance(obj.get("array"), list):
        errs.append("vertices.%s must be an object with an 'array' list" % key)
        return None
    return obj["array"]


def _numbers(arr: list) -> bool:
    return all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in arr)


def check_mesh_json(d: dict) -> List[str]:
    """Structural validation of an EF mesh JSON dict. Returns problems (empty list = valid)."""
    errs: List[str] = []
    if not isinstance(d, dict) or not isinstance(d.get("vertices"), dict):
        return ["top level must be an object with a 'vertices' object"]
    v = d["vertices"]
    arrays = {k: _array(v, k, errs) for k in MEMBERS}
    parts = v.get("parts")
    if not isinstance(parts, dict):
        errs.append("vertices.parts must be an object")
        parts = {}
    if errs:
        return errs
    pos, nrm, uv = arrays["positions"], arrays["normals"], arrays["uvs"]
    vcounts, weights, vindices = arrays["vcounts"], arrays["weights"], arrays["vindices"]
    for k in ("positions", "normals", "uvs", "weights"):
        if not _numbers(arrays[k]):
            errs.append("vertices.%s must contain only finite numbers" % k)
    for k in ("vcounts", "vindices"):
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in arrays[k]):
            errs.append("vertices.%s must contain only integers" % k)
    if errs:
        return errs
    if len(pos) % 3:
        errs.append("positions length %d is not a multiple of 3" % len(pos))
    if len(nrm) % 3:
        errs.append("normals length %d is not a multiple of 3" % len(nrm))
    if len(uv) % 2:
        errs.append("uvs length %d is not a multiple of 2" % len(uv))
    if errs:
        return errs
    npos, nnrm, nuv = len(pos) // 3, len(nrm) // 3, len(uv) // 2
    if len(vcounts) != npos:
        errs.append("vcounts has %d entries for %d positions" % (len(vcounts), npos))
    elif sum(vcounts) * 2 != len(vindices):
        errs.append("vindices length %d does not match sum(vcounts)*2 = %d" % (len(vindices), sum(vcounts) * 2))
    else:
        nj = joint_count()
        k = 0
        for i, c in enumerate(vcounts):
            if c < 1:
                errs.append("position %d has no joint influence" % i)
                k += 2 * c
                continue
            total = 0.0
            for _ in range(c):
                ji, wi = vindices[k], vindices[k + 1]
                k += 2
                if ji < 0 or ji >= nj:
                    errs.append("position %d uses joint %d outside 0..%d" % (i, ji, nj - 1))
                elif wi < 0 or wi >= len(weights):
                    errs.append("position %d uses weight index %d outside 0..%d" % (i, wi, len(weights) - 1))
                else:
                    total += weights[wi]
            if abs(total - 1.0) > 1e-3:
                errs.append("position %d weights sum to %.4f, expected 1.0" % (i, total))
            if len(errs) > 20:
                errs.append("(further influence errors suppressed)")
                break
    missing = [p for p in EF_PARTS if p not in parts]
    if missing:
        errs.append("missing EF parts: %s" % ", ".join(missing))
    extra = [p for p in parts if p not in EF_PARTS]
    if extra:
        errs.append("unknown EF parts: %s" % ", ".join(extra))
    if errs:
        return errs
    agree = 0
    tested = 0
    bad_normals = 0
    for name in EF_PARTS:
        obj = parts[name]
        arr = obj.get("array") if isinstance(obj, dict) else None
        if not isinstance(arr, list) or not arr:
            errs.append("part %s must have a non-empty 'array'" % name)
            continue
        if len(arr) % 9:
            errs.append("part %s array length %d is not a multiple of 9" % (name, len(arr)))
            continue
        if not all(isinstance(x, int) and not isinstance(x, bool) for x in arr):
            errs.append("part %s array must contain only integers" % name)
            continue
        ok = True
        for i in range(0, len(arr), 3):
            p, t, n = arr[i], arr[i + 1], arr[i + 2]
            if not (0 <= p < npos and 0 <= t < nuv and 0 <= n < nnrm):
                errs.append("part %s vertex %d has index (%d, %d, %d) out of range (positions %d, uvs %d, normals %d)" % (name, i // 3, p, t, n, npos, nuv, nnrm))
                ok = False
                break
        if not ok:
            continue
        for i in range(0, len(arr), 9):
            pi = [arr[i + 3 * k] for k in range(3)]
            ni = [arr[i + 3 * k + 2] for k in range(3)]
            a, b, c = (from_blender(pos[3 * q:3 * q + 3]) for q in pi)
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            gx, gy, gz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            if gx * gx + gy * gy + gz * gz < 1e-14:
                continue  # degenerate (anchor) triangle
            sx = sy = sz = 0.0
            for q in ni:
                nx, ny, nz = from_blender(nrm[3 * q:3 * q + 3])
                ln = math.sqrt(nx * nx + ny * ny + nz * nz)
                if abs(ln - 1.0) > 0.01:
                    bad_normals += 1
                sx, sy, sz = sx + nx, sy + ny, sz + nz
            tested += 1
            if gx * sx + gy * sy + gz * sz > 0.0:
                agree += 1
    if bad_normals:
        errs.append("%d triangle vertices use a normal that is not unit length" % bad_normals)
    if tested and agree / tested < WINDING_MIN_AGREE:
        errs.append("triangle winding agrees with the normals for only %.1f%% of %d triangles (need >= %d%%)" % (100.0 * agree / tested, tested, int(WINDING_MIN_AGREE * 100)))
    return errs
