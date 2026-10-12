"""Epic Fight animation clips and EF-exact skinning in pure Python (no Blender needed).

Everything here is a port of the Epic Fight 20.14.17 (1.20.1) loader and skinning code, transcribed from the bytecode of
`JsonAssetLoader.getTransformSheet / loadClipForAnimation`, `JointTransform`, `Joint.initOriginTransform`,
`Armature.getPoseTransform` and `MathUtils.lerpQuaternion`:

* An animation file is `{"animation": [{"name": joint, "time": [...], "transform": [[16 floats], ...]}]}`. A matrix is
  the joint's FULL local transform (relative to its parent, Blender bone space, row-major, translation in elements
  3, 7, 11). Times in seconds, negative times are dropped.
* EF stores for each frame `JointTransform.fromMatrix(inverse(L_j) * [CORR *] M)` where `L_j` is the joint's local
  rest transform of the armature (`L_root = CORR * M_root`, from `biped.json` armature), `CORR` = rotation about X by -90
  degrees (Blender Z-up -> Minecraft Y-up) and the bracket applies to the FIRST joint of the file that exists in the
  armature, unless a `Coord` entry came before it (then the Root is already in Minecraft space). The decomposition is
  translation / rotation quaternion / scale; frames are interpolated with lerp, normalized lerp and lerp.
* The pose of joint j is `P_j = P_parent * L_j * T_j` with `T_j = translate(t) * rotate(q) * scale(s)`; the skinning
  matrix is `P_j * inverse(W_j)` with the bind world matrix `W_j = W_parent * L_j`. Mesh positions are converted with
  CORR before skinning: `v' = sum_i w_i (P_i W_i^-1) CORR v` (weights are used as stored, not renormalized).

`blender_deltas` turns the result into rig-independent world-space matrices D_i = CORR^-1 (P_i W_i^-1) CORR (Blender
space) so that Blender can reproduce the pose whatever the bone roll of its armature is: v'_blender = sum_i w_i D_i v.
Matrices are tuples of 16 floats, row-major, to match the JSON.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import efassets
from .spec import BossForgeError, data

Mat = Tuple[float, ...]
Vec = Tuple[float, float, float]

ANIM_MEMBER = "assets/epicfight/animmodels/animations/%s.json"
ANIM_RAW_URL = ("https://raw.githubusercontent.com/Epic-Fight/epicfight/%s/src/main/resources/assets/epicfight/"
                "animmodels/animations/%s.json")
_KEY_RE = re.compile(r"^[a-z0-9_]+(/[a-z0-9_]+)*$")
IDENT: Mat = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


# ------------------------------------------------------------------------------------------- 4x4 matrices
def mat_mul(a: Sequence[float], b: Sequence[float]) -> Mat:
    return tuple(a[r * 4] * b[c] + a[r * 4 + 1] * b[4 + c] + a[r * 4 + 2] * b[8 + c] + a[r * 4 + 3] * b[12 + c]
                 for r in range(4) for c in range(4))


def mat_inv(m: Sequence[float]) -> Mat:
    """General 4x4 inverse (Gauss-Jordan with partial pivoting)."""
    a = [list(m[r * 4:r * 4 + 4]) + [1.0 if r == c else 0.0 for c in range(4)] for r in range(4)]
    for col in range(4):
        piv = max(range(col, 4), key=lambda r: abs(a[r][col]))
        if abs(a[piv][col]) < 1e-14:
            raise BossForgeError("/matrix", "singular_matrix", "cannot invert a singular 4x4 matrix")
        a[col], a[piv] = a[piv], a[col]
        d = a[col][col]
        a[col] = [v / d for v in a[col]]
        for r in range(4):
            if r != col:
                f = a[r][col]
                if f:
                    a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return tuple(a[r][4 + c] for r in range(4) for c in range(4))


def mat_apply(m: Sequence[float], v: Sequence[float]) -> Vec:
    x, y, z = v
    return (m[0] * x + m[1] * y + m[2] * z + m[3], m[4] * x + m[5] * y + m[6] * z + m[7], m[8] * x + m[9] * y + m[10] * z + m[11])


def rot_x(deg: float) -> Mat:
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return (1.0, 0.0, 0.0, 0.0, 0.0, c, -s, 0.0, 0.0, s, c, 0.0, 0.0, 0.0, 0.0, 1.0)


# Blender Z-up -> Minecraft Y-up: JsonAssetLoader.BLENDER_TO_MINECRAFT_COORD = createRotatorDeg(-90, X_AXIS)
CORR: Mat = rot_x(-90.0)
CORR_INV: Mat = mat_inv(CORR)


def conjugate_corr(m: Sequence[float]) -> Mat:
    """CORR^-1 * m * CORR: a Minecraft-space matrix expressed in Blender space."""
    return mat_mul(mat_mul(CORR_INV, m), CORR)


# ------------------------------------------------------------------------------------------- quaternions
Quat = Tuple[float, float, float, float]  # x, y, z, w


def quat_from_matrix(m: Sequence[float]) -> Quat:
    """Rotation quaternion of the upper 3x3 (scale removed by normalizing the columns)."""
    cols = []
    for c in range(3):
        n = math.sqrt(m[c] ** 2 + m[4 + c] ** 2 + m[8 + c] ** 2) or 1.0
        cols.append((m[c] / n, m[4 + c] / n, m[8 + c] / n))
    m00, m10, m20 = cols[0]
    m01, m11, m21 = cols[1]
    m02, m12, m22 = cols[2]
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        q = ((m21 - m12) / s, (m02 - m20) / s, (m10 - m01) / s, 0.25 * s)
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        q = (0.25 * s, (m01 + m10) / s, (m02 + m20) / s, (m21 - m12) / s)
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        q = ((m01 + m10) / s, 0.25 * s, (m12 + m21) / s, (m02 - m20) / s)
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        q = ((m02 + m20) / s, (m12 + m21) / s, 0.25 * s, (m10 - m01) / s)
    return _qnorm(q)


def _qnorm(q: Quat) -> Quat:
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def quat_to_matrix(q: Quat) -> Mat:
    x, y, z, w = _qnorm(q)
    return (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0,
            2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0,
            2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0,
            0.0, 0.0, 0.0, 1.0)


def quat_lerp(a: Quat, b: Quat, t: float) -> Quat:
    """MathUtils.lerpQuaternion: shortest-path normalized lerp."""
    dot = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    k = -t if dot < 0.0 else t
    return _qnorm(tuple((1.0 - t) * x + k * y for x, y in zip(a, b)))  # type: ignore[arg-type]


class JointTransform:
    """translation, rotation (x, y, z, w) and scale of one joint at one time (the EF JointTransform)."""
    __slots__ = ("t", "q", "s")

    def __init__(self, t: Vec = (0.0, 0.0, 0.0), q: Quat = (0.0, 0.0, 0.0, 1.0), s: Vec = (1.0, 1.0, 1.0)):
        self.t, self.q, self.s = t, q, s

    @classmethod
    def from_matrix(cls, m: Sequence[float]) -> "JointTransform":
        s = tuple(math.sqrt(m[c] ** 2 + m[4 + c] ** 2 + m[8 + c] ** 2) for c in range(3))
        return cls((m[3], m[7], m[11]), quat_from_matrix(m), s)  # type: ignore[arg-type]

    def to_matrix(self) -> Mat:
        """translate(t) * rotate(q) * scale(s)."""
        r = quat_to_matrix(self.q)
        sx, sy, sz = self.s
        return (r[0] * sx, r[1] * sy, r[2] * sz, self.t[0], r[4] * sx, r[5] * sy, r[6] * sz, self.t[1],
                r[8] * sx, r[9] * sy, r[10] * sz, self.t[2], 0.0, 0.0, 0.0, 1.0)

    @staticmethod
    def lerp(a: "JointTransform", b: "JointTransform", t: float) -> "JointTransform":
        return JointTransform(tuple(x + (y - x) * t for x, y in zip(a.t, b.t)),  # type: ignore[arg-type]
                              quat_lerp(a.q, b.q, t),
                              tuple(x + (y - x) * t for x, y in zip(a.s, b.s)))  # type: ignore[arg-type]


# ------------------------------------------------------------------------------------------- armature
class Armature:
    """The EF joint hierarchy: names (mesh joint order), parents and local rest matrices from `biped.json`."""

    def __init__(self, names: List[str], parent: Dict[str, Optional[str]], local_json: Dict[str, Mat]):
        self.names = list(names)
        self.index = {n: i for i, n in enumerate(self.names)}
        self.parent = dict(parent)
        self.local_json = dict(local_json)  # as in the file (Blender space, bone matrix relative to the parent bone)
        roots = [n for n in self.names if self.parent.get(n) is None]
        if len(roots) != 1:
            raise BossForgeError("/armature", "bad_armature", "expected exactly one root joint, got %s" % roots)
        self.root = roots[0]
        self.order = self._parents_first()
        # EF Joint.localTransform: the root additionally carries CORR (JsonAssetLoader.getJoint)
        self.local = {n: (mat_mul(CORR, m) if n == self.root else m) for n, m in self.local_json.items()}
        self._bind: Optional[Dict[str, Mat]] = None

    def _parents_first(self) -> List[str]:
        out: List[str] = []
        pending = list(self.names)
        while pending:
            progressed = False
            for n in list(pending):
                p = self.parent.get(n)
                if p is None or p in out:
                    out.append(n)
                    pending.remove(n)
                    progressed = True
            if not progressed:
                raise BossForgeError("/armature", "bad_armature", "joint hierarchy has a cycle")
        return out

    @classmethod
    def from_mesh_json(cls, d: dict) -> "Armature":
        arm = d.get("armature") if isinstance(d, dict) else None
        if not isinstance(arm, dict) or not arm.get("hierarchy"):
            raise BossForgeError("/armature", "no_armature", "the mesh JSON has no armature section")
        parent: Dict[str, Optional[str]] = {}
        local: Dict[str, Mat] = {}

        def walk(node: dict, par: Optional[str]) -> None:
            name = node["name"]
            if len(node["transform"]) != 16:
                raise BossForgeError("/armature", "bad_armature", "joint %s transform is not 16 numbers" % name)
            parent[name] = par
            local[name] = tuple(float(v) for v in node["transform"])
            for ch in node.get("children", []):
                walk(ch, name)

        for top in arm["hierarchy"]:
            walk(top, None)
        names = list(arm.get("joints") or local)
        return cls(names, parent, local)

    def bind_world(self) -> Dict[str, Mat]:
        """W_j = W_parent * L_j in Minecraft space (EF Joint.initOriginTransform; toOrigin = inverse(W_j))."""
        if self._bind is None:
            w: Dict[str, Mat] = {}
            for n in self.order:
                p = self.parent.get(n)
                w[n] = mat_mul(w[p], self.local[n]) if p is not None else self.local[n]
            self._bind = w
        return self._bind

    def bind_world_blender(self) -> Dict[str, Mat]:
        """Rest world matrices in Blender (Z-up storage) space: CORR^-1 * W_j. Bone Y runs along the bone."""
        return {n: mat_mul(CORR_INV, m) for n, m in self.bind_world().items()}


# ------------------------------------------------------------------------------------------- clips
class Clip:
    """One EF animation: per joint a list of (time, JointTransform) keyframes (already relative to the rest pose)."""

    def __init__(self, key: str, tracks: Dict[str, List[Tuple[float, JointTransform]]]):
        self.key = key
        self.tracks = tracks
        self.duration = max((fr[-1][0] for fr in tracks.values() if fr), default=0.0)

    def times(self) -> List[float]:
        return sorted({t for fr in self.tracks.values() for t, _ in fr})

    def sample(self, t: float) -> Dict[str, JointTransform]:
        """Every joint at time `t` (TransformSheet.getInterpolationInfo: binary search, delta clamped to 0..1)."""
        out: Dict[str, JointTransform] = {}
        for name, frames in self.tracks.items():
            if not frames:
                continue
            tt = t + frames[-1][0] if t < 0 else t
            lo, hi = 0, len(frames) - 1
            while hi - lo > 1:
                mid = lo + (hi - lo) // 2
                if frames[mid][0] <= tt and frames[mid + 1][0] > tt:
                    lo, hi = mid, mid + 1
                    break
                if frames[mid][0] > tt:
                    hi = mid
                elif frames[mid + 1][0] <= tt:
                    lo = mid
            t0, t1 = frames[lo][0], frames[hi][0]
            if hi == lo:
                out[name] = frames[lo][1]
                continue
            delta = (tt - t0) / (t1 - t0) if t1 != t0 else 0.0
            out[name] = JointTransform.lerp(frames[lo][1], frames[hi][1], min(1.0, max(0.0, delta)))
        return out


def normalize_key(key: str) -> str:
    """`epicfight:biped/living/walk` -> `biped/living/walk` (an optional `.json` suffix is dropped)."""
    k = str(key).strip()
    if ":" in k:
        ns, k = k.split(":", 1)
        if ns != "epicfight":
            raise BossForgeError("/anim", "bad_animation_key", "only epicfight:* animations are supported, got namespace %r" % ns)
    if k.endswith(".json"):
        k = k[:-5]
    if not _KEY_RE.match(k):
        raise BossForgeError("/anim", "bad_animation_key", "%r is not an animation key like epicfight:biped/living/walk" % key)
    return k


def find_jar(explicit: Optional[str] = None) -> Optional[Path]:
    """The Epic Fight jar: `explicit`, `$EPICFIGHT_JAR`, then launcher mod folders (newest first)."""
    for cand in (explicit, os.environ.get("EPICFIGHT_JAR")):
        if cand:
            p = Path(cand).expanduser()
            if p.is_file():
                return p
    home = Path.home()
    pats = [(home / ".local/share/PrismLauncher/instances", "*/minecraft/mods/epic-fight-*.jar"),
            (home / ".minecraft/mods", "epic-fight-*.jar"), (home / "AppData/Roaming/.minecraft/mods", "epic-fight-*.jar")]
    found: List[Path] = []
    for base, pat in pats:
        if base.is_dir():
            found.extend(base.glob(pat))
    found = [p for p in found if p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


def animation_file(key: str, cache_dir, ef_jar: Optional[str] = None, offline: bool = False) -> Path:
    """Cached path of the animation JSON; read from the jar (no extraction of the 500+ other files) or from GitHub."""
    k = normalize_key(key)
    ref = data("versions")["epicfight"]["assets_ref"]
    path = Path(cache_dir) / "ef" / ref[:8] / "animations" / (k + ".json")
    if path.is_file():
        return path
    jar = find_jar(ef_jar)
    raw: Optional[bytes] = None
    if jar is not None:
        try:
            with zipfile.ZipFile(jar) as z:
                raw = z.read(ANIM_MEMBER % k)
        except KeyError:
            raise BossForgeError("/anim", "animation_not_found", "%s is not in %s (see `bossforge list animations --prefix biped/`)" % (ANIM_MEMBER % k, jar))
        except (OSError, zipfile.BadZipFile) as e:
            raise BossForgeError("/anim", "ef_jar_missing", "cannot read %s: %s" % (jar, e))
    else:
        url = ANIM_RAW_URL % (ref, k)
        if offline:
            raise BossForgeError("/anim", "offline_missing", "animation %s is not cached, no Epic Fight jar found and --offline is set (pass --ef-jar)" % k)
        try:
            raw = efassets.http_get(url)
        except urllib.error.HTTPError as e:
            raise BossForgeError("/anim", "animation_not_found" if e.code == 404 else "download_failed", "GET %s failed: %s" % (url, e))
        except (urllib.error.URLError, OSError) as e:
            raise BossForgeError("/anim", "download_failed", "GET %s failed: %s" % (url, e))
    try:
        d = json.loads(raw.decode("utf-8"))
        ok = isinstance(d.get("animation"), list)
    except (ValueError, AttributeError):
        ok = False
    if not ok:
        raise BossForgeError("/anim", "bad_animation", "%s is not an Epic Fight animation (needs an `animation` array)" % k)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def _frame_transform(entry_t: Any, inv_local: Mat, root_correct: bool, fmt: str) -> JointTransform:
    if fmt == "attributes":
        loc, rot, sca = entry_t["loc"], entry_t["rot"], entry_t["sca"]
        # EF builds the quaternion (-rot[1], -rot[2], -rot[3], rot[0]) and its OpenMatrix4f.fromQuaternion is the transpose
        # of the textbook matrix, so the net rotation is the textbook rotation of (x, y, z, w) = (rot[1], rot[2], rot[3], rot[0])
        # (rot = Blender's w, x, y, z quaternion). Verified against the real EF classes (see tests / blender-pipeline.md).
        return JointTransform((loc[0], loc[1], loc[2]), _qnorm((rot[1], rot[2], rot[3], rot[0])), (sca[0], sca[1], sca[2]))
    m: Mat = tuple(float(v) for v in entry_t)  # type: ignore[assignment]
    if len(m) != 16:
        raise BossForgeError("/anim", "bad_animation", "a joint transform must have 16 numbers")
    if root_correct:
        m = mat_mul(CORR, m)
    m = mat_mul(inv_local, m)
    jt = JointTransform.from_matrix(m)
    jt.q = _qnorm(jt.q)
    return jt


def parse_clip(raw: dict, armature: Armature, key: str = "") -> Clip:
    """JsonAssetLoader.loadClipForAnimation: joints that are not in the armature are skipped, `Coord` clears the
    first-joint flag (the Root of such files is already in Minecraft space)."""
    # raw["format"] is only a hint: EF decides per keyframe whether it is a matrix array or a loc/rot/sca object
    first = True
    tracks: Dict[str, List[Tuple[float, JointTransform]]] = {}
    for entry in raw.get("animation", []):
        name = entry["name"]
        if name == "Coord":
            first = False
            continue
        if name not in armature.index:
            continue
        inv_local = mat_inv(armature.local[name])
        times, tf = entry["time"], entry["transform"]
        if len(times) != len(tf):
            raise BossForgeError("/anim", "bad_animation", "joint %s has %d times but %d transforms" % (name, len(times), len(tf)))
        frames = []
        for t, m in zip(times, tf):
            if t < 0:
                continue
            f = "attributes" if isinstance(m, dict) else "matrix"  # EF switches the format per entry type, whatever `format` says
            frames.append((float(t), _frame_transform(m, inv_local, first, f)))
        tracks[name] = frames
        first = False
    return Clip(key, tracks)


# ------------------------------------------------------------------------------------------- poses and skinning
def pose_matrices(armature: Armature, joint_transforms: Dict[str, JointTransform]) -> Dict[str, Mat]:
    """Armature.getPoseTransform with applyOriginTransform: {joint: P_j * inverse(W_j)} in Minecraft space.
    Joints without a transform keep their rest pose (identity delta)."""
    bind = armature.bind_world()
    pose: Dict[str, Mat] = {}
    out: Dict[str, Mat] = {}
    for n in armature.order:
        p = armature.parent.get(n)
        local = mat_mul(armature.local[n], joint_transforms[n].to_matrix() if n in joint_transforms else IDENT)
        pose[n] = mat_mul(pose[p], local) if p is not None else local
        out[n] = mat_mul(pose[n], mat_inv(bind[n]))
    return out


def blender_deltas(armature: Armature, joint_transforms: Dict[str, JointTransform]) -> Dict[str, Mat]:
    """World-space (Blender, Z-up storage space) deformation matrix of every joint: D_i = CORR^-1 (P_i W_i^-1) CORR.
    A vertex at rest position v moves to sum_i w_i * D_i * v."""
    return {n: conjugate_corr(m) for n, m in pose_matrices(armature, joint_transforms).items()}


def mesh_influences(mesh_json: dict) -> Tuple[List[Vec], List[List[Tuple[int, float]]]]:
    """(positions, per position [(joint index, weight)]) of an EF mesh JSON; positions are the stored Blender-space ones."""
    v = mesh_json["vertices"]
    pos = v["positions"]["array"]
    n = len(pos) // 3
    vcounts, vind, weights = v["vcounts"]["array"], v["vindices"]["array"], v["weights"]["array"]
    out: List[List[Tuple[int, float]]] = []
    k = 0
    for i in range(n):
        infl = []
        for _ in range(int(vcounts[i])):
            infl.append((int(vind[k]), float(weights[int(vind[k + 1])])))
            k += 2
        out.append(infl)
    return [(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]) for i in range(n)], out


def skin_positions(positions: Sequence[Vec], influences: Sequence[Sequence[Tuple[int, float]]], joint_names: Sequence[str],
                   mc_matrices: Dict[str, Mat], in_blender_space: bool = True) -> List[Vec]:
    """EF linear blend skinning: positions are converted with CORR, blended with `mc_matrices` (from `pose_matrices`)
    and converted back, so the result is again in Blender space. weights are NOT renormalized (as in EF's shader)."""
    mats = [mc_matrices[n] for n in joint_names]
    out: List[Vec] = []
    for p, infl in zip(positions, influences):
        v = mat_apply(CORR, p) if in_blender_space else p
        x = y = z = 0.0
        for j, w in infl:
            q = mat_apply(mats[j], v)
            x += w * q[0]
            y += w * q[1]
            z += w * q[2]
        out.append(mat_apply(CORR_INV, (x, y, z)) if in_blender_space else (x, y, z))
    return out


def skin_positions_world(positions: Sequence[Vec], influences: Sequence[Sequence[Tuple[int, float]]], joint_names: Sequence[str],
                         deltas: Dict[str, Mat]) -> List[Vec]:
    """The same blend with Blender-space `blender_deltas`: sum_i w_i * D_i * v (no CORR round trip)."""
    mats = [deltas[n] for n in joint_names]
    out: List[Vec] = []
    for p, infl in zip(positions, influences):
        x = y = z = 0.0
        for j, w in infl:
            q = mat_apply(mats[j], p)
            x += w * q[0]
            y += w * q[1]
            z += w * q[2]
        out.append((x, y, z))
    return out


# ------------------------------------------------------------------------------------------- convenience
def load_armature(cache_dir, ef_jar: Optional[str] = None, offline: bool = False) -> Armature:
    """The stock EF biped armature (from the cached `biped.json`, the jar or GitHub)."""
    ref = data("versions")["epicfight"]["assets_ref"]
    jar = find_jar(ef_jar)
    path = efassets.mesh_file("biped", cache_dir, ref, str(jar) if jar else None, offline)
    return Armature.from_mesh_json(json.loads(path.read_text("utf-8")))


def evaluate(key: str, times: Iterable[float], cache_dir, ef_jar: Optional[str] = None, offline: bool = False) -> Dict[str, Any]:
    """Clip `key` evaluated at `times` (seconds; clamped to the clip): {clip, duration, armature, frames: [{t, deltas}]}.
    `deltas` are the Blender-space matrices from `blender_deltas` (joint -> 16 floats)."""
    arm = load_armature(cache_dir, ef_jar, offline)
    path = animation_file(key, cache_dir, ef_jar, offline)
    clip = parse_clip(json.loads(path.read_text("utf-8")), arm, normalize_key(key))
    frames = []
    for t in times:
        frames.append({"t": float(t), "deltas": blender_deltas(arm, clip.sample(float(t)))})
    return {"clip": clip, "duration": clip.duration, "armature": arm, "frames": frames}
