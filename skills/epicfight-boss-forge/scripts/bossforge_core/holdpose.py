"""Where Epic Fight puts a held item, and the item-model `display` transform that lines a sprite up with the hand.

Epic Fight (20.14.17) draws a held item with the vanilla item renderer, but replaces the vanilla hand placement:

    point on screen = EntityMatrix * pose[Tool_R] * T(0, 0, -0.13) * Rx(-90) * D * T(-0.5, -0.5, -0.5) * sprite point

* `EntityMatrix` is the entity placement and its `attributes.scale` (the item scales with the wielder),
* `pose[Tool_R]` is the animated transform of the Tool_R joint (the animation of the moment: idle, hold_spear...),
* `T(0,0,-0.13) Rx(-90)` is EF's fixed correction for Tool_R (RenderItemBase.GLOBAL_MAINHAND_ITEM_TRANSFORMS),
* `D` is the model's `thirdperson_righthand` display transform (translate, then rotate X*Y*Z, then scale), the only
  piece an item model controls, and the sprite is the unit quad of `item/generated` centred on (0.5, 0.5, 0.5).

`assets/data/hold-poses.json` stores the product `pose[Tool_R] * correction` (the *item frame*) for the animations an
item is held in, so a `D` can be solved offline: it puts a chosen sprite point (the grip) on the hand and turns the
sprite axis to a chosen direction. Everything is plain stdlib math; no Epic Fight classes are needed.

Body space: x = the wielder's right, y = up, z = backwards (the wielder faces -z), units = blocks at entity scale 1.
"""
from __future__ import annotations

import json
import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import sprites
from .spec import BossForgeError, data

Vec = Tuple[float, float, float]
Mat = Tuple[Vec, Vec, Vec]  # three rows

POSE = "hold_spear"  # the animation EF plays for a held spear/staff in the two-hand style (mobs and players)

# `hold` knobs (weapon object): name -> (low, high); `facing` and `offset` are checked separately
HOLD_LIMITS = {"lean": (-45.0, 60.0), "side": (-45.0, 45.0), "grip": (0.1, 0.8)}
HOLD_FACING = ("front", "side")
HOLD_OFFSET_LIMIT = 1.0
DEFAULT_LEAN = 10.0
MAX_SCALE = 4.0  # Minecraft clamps display scale to +-4
MAX_TRANSLATION = 80.0  # ... and translation to +-80 sixteenths


# --------------------------------------------------------------------------------------------- tiny linear algebra
def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a: Sequence[float]) -> Vec:
    n = math.sqrt(_dot(a, a))
    return (a[0] / n, a[1] / n, a[2] / n)


def _mul(a: Mat, b: Mat) -> Mat:
    cols = list(zip(*b))
    return tuple(tuple(_dot(r, c) for c in cols) for r in a)  # type: ignore[return-value]


def _apply(m: Mat, v: Sequence[float]) -> Vec:
    return (_dot(m[0], v), _dot(m[1], v), _dot(m[2], v))


def _transpose(m: Mat) -> Mat:
    return tuple(zip(*m))  # type: ignore[return-value]


def _from_columns(c0: Sequence[float], c1: Sequence[float], c2: Sequence[float]) -> Mat:
    return ((c0[0], c1[0], c2[0]), (c0[1], c1[1], c2[1]), (c0[2], c1[2], c2[2]))


def _orthonormal(m: Mat) -> Mat:
    """Nearest rotation (Gram-Schmidt on the columns); the stored frames carry ~1e-3 of numeric skew."""
    c0, c1, c2 = _transpose(m)
    x = _unit(c0)
    y = _unit(tuple(c1[i] - _dot(c1, x) * x[i] for i in range(3)))
    return _from_columns(x, y, _cross(x, y))


def _inverse(m: Mat) -> Mat:
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return (((e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det),
            ((f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det),
            ((d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det))


def _nearest_rotation(m: Mat) -> Mat:
    """Polar decomposition by Newton iteration: the rotation closest to `m` in the Frobenius norm."""
    r = m
    for _ in range(40):
        inv_t = _transpose(_inverse(r))
        r = tuple(tuple(0.5 * (r[i][j] + inv_t[i][j]) for j in range(3)) for i in range(3))  # type: ignore[assignment]
    return r  # type: ignore[return-value]


def rot_xyz(rx: float, ry: float, rz: float) -> Mat:
    """Minecraft item rotation (degrees): the matrix Rx * Ry * Rz, i.e. a vector is turned about Z first, then Y, then X."""
    ax, ay, az = (math.radians(v) for v in (rx, ry, rz))
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    rxm: Mat = ((1.0, 0.0, 0.0), (0.0, cx, -sx), (0.0, sx, cx))
    rym: Mat = ((cy, 0.0, sy), (0.0, 1.0, 0.0), (-sy, 0.0, cy))
    rzm: Mat = ((cz, -sz, 0.0), (sz, cz, 0.0), (0.0, 0.0, 1.0))
    return _mul(_mul(rxm, rym), rzm)


def euler_xyz(m: Mat) -> Tuple[float, float, float]:
    """Inverse of `rot_xyz`: degrees (rx, ry, rz) with `rot_xyz(rx, ry, rz) == m`."""
    ry = math.asin(max(-1.0, min(1.0, m[0][2])))
    if abs(m[0][2]) < 0.999999:
        rx = math.atan2(-m[1][2], m[2][2])
        rz = math.atan2(-m[0][1], m[0][0])
    else:  # gimbal lock: all the remaining turn goes into Z
        rx, rz = 0.0, math.atan2(m[1][0], m[1][1])
    return math.degrees(rx), math.degrees(ry), math.degrees(rz)


# --------------------------------------------------------------------------------------------- hand frames
def reference_height() -> float:
    return float(data("hold-poses")["reference_height"])


def pose_names() -> List[str]:
    return sorted(data("hold-poses")["poses"])


def hand_frame(pose: str = POSE) -> Tuple[Vec, Mat]:
    """(origin, rotation) of the item frame for an animation: columns of the rotation are the item model's x, y, z axes."""
    poses = data("hold-poses")["poses"]
    if pose not in poses:
        raise BossForgeError("/hold-poses", "unknown_value", "no hand frame for %r; known: %s" % (pose, ", ".join(sorted(poses))))
    p = poses[pose]
    o = p["origin"]
    return (float(o[0]), float(o[1]), float(o[2])), _orthonormal(_from_columns(*p["axes"]))


# --------------------------------------------------------------------------------------------- the display transform
def display_matrix(rotation: Sequence[float], translation: Sequence[float], scale: Sequence[float]) -> Tuple[Mat, Vec, Vec]:
    """(rotation matrix, translation in blocks, scale) of a right-hand display entry (translation in 1/16 block)."""
    return rot_xyz(*rotation), (translation[0] / 16.0, translation[1] / 16.0, translation[2] / 16.0), (scale[0], scale[1], scale[2])


def place(display: dict, point: Sequence[float], pose: str = POSE) -> Vec:
    """Body-space position (blocks, entity scale 1) of an item-model point (unit square, y up; z = 0.5 is the sprite plane)
    when the `thirdperson_righthand` display entry `display` is held in the hand frame of `pose`."""
    origin, rf = hand_frame(pose)
    rd, t, s = display_matrix(display["rotation"], display["translation"], display["scale"])
    p = (s[0] * (point[0] - 0.5), s[1] * (point[1] - 0.5), 0.0)  # the sprite plane z = 0.5 is the pivot plane
    local = _apply(rd, p)
    local = (local[0] + t[0], local[1] + t[1], local[2] + t[2])
    w = _apply(rf, local)
    return (origin[0] + w[0], origin[1] + w[1], origin[2] + w[2])


def direction(display: dict, a: Sequence[float], b: Sequence[float], pose: str = POSE) -> Vec:
    """Unit body-space direction from item point `a` to item point `b` for a display entry held in `pose`."""
    pa, pb = place(display, a, pose), place(display, b, pose)
    return _unit((pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]))


def normal(display: dict, pose: str = POSE) -> Vec:
    """Unit body-space direction of the sprite's +z (the face that reads un-mirrored from that side)."""
    _, rf = hand_frame(pose)
    rd, _, _ = display_matrix(display["rotation"], display["translation"], display["scale"])
    return _unit(_apply(rf, _apply(rd, (0.0, 0.0, 1.0))))


def _target_axis(lean: float, side: float) -> Vec:
    """Up, leaned `lean` degrees forward (-z) and `side` degrees outward (+x)."""
    l, s = math.radians(lean), math.radians(side)
    return _unit((math.sin(s) * math.cos(l), math.cos(s) * math.cos(l), -math.sin(l)))


def _plane_normal(axis: Vec, facing: str) -> Vec:
    want = (0.0, 0.0, -1.0) if facing == "front" else (1.0, 0.0, 0.0)
    d = _dot(want, axis)
    return _unit(tuple(want[i] - d * axis[i] for i in range(3)))


def _num(v: float, digits: int) -> float:
    return round(v, digits) + 0.0  # + 0.0 turns -0.0 into 0.0


def check_hold(hold: object, path: str = "/hold") -> List[Tuple[str, str, str]]:
    """Problems of a weapon `hold` object as (path, code, message); empty when it is valid."""
    if hold is None:
        return []
    if not isinstance(hold, dict):
        return [(path, "bad_type", "hold must be an object like {lean, side, grip, facing, offset}")]
    errs: List[Tuple[str, str, str]] = []
    for k, v in hold.items():
        if k in HOLD_LIMITS:
            lo, hi = HOLD_LIMITS[k]
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                errs.append((path + "/" + k, "bad_type", "expected a number, got %r" % (v,)))
            elif not lo <= v <= hi:
                errs.append((path + "/" + k, "out_of_range", "%r is outside the allowed range %s..%s" % (v, lo, hi)))
        elif k == "facing":
            if v not in HOLD_FACING:
                errs.append((path + "/facing", "unknown_value", "%r is not allowed; allowed values: %s" % (v, ", ".join(HOLD_FACING))))
        elif k == "offset":
            ok = isinstance(v, list) and len(v) == 3 and all(
                isinstance(c, (int, float)) and not isinstance(c, bool) and abs(c) <= HOLD_OFFSET_LIMIT for c in v)
            if not ok:
                errs.append((path + "/offset", "bad_type", "offset must be [x, y, z] numbers within +-%s blocks" % HOLD_OFFSET_LIMIT))
        else:
            errs.append((path + "/" + k, "unknown_key", "unknown hold key %r; allowed: %s" % (
                k, ", ".join(sorted(list(HOLD_LIMITS) + ["facing", "offset"])))))
    return errs


def staff_display(appearance: Optional[dict], visual_scale: float = 1.0, hold: Optional[dict] = None,
                  pose: str = POSE) -> Dict[str, List[float]]:
    """`thirdperson_righthand` display entry that holds a staff upright in the hand.

    The staff axis (butt -> tip, from `sprites.staff_geometry`) is turned to point up, leaned `hold.lean` degrees forward
    (default 10) and `hold.side` degrees outward (default 0); the sprite plane faces the front (`hold.facing: front`,
    default) or the wielder's right side. The hand grips the axis at the fraction `hold.grip` from the foot (default
    `sprites.STAFF_GRIP`), shifted by `hold.offset` blocks in the hand frame. The staff is `visual_scale` times the stock
    biped height long (1.8 blocks at entity scale 1; Epic Fight multiplies by the wielder's scale, so it stays in
    proportion to the wielder). The result is exact for the `pose` animation (default hold_spear) and degrades smoothly in
    other poses (see `references/weapon-forge.md`).
    """
    h = dict(hold or {})
    problems = check_hold(h)
    if problems:
        raise BossForgeError(problems[0][0], problems[0][1], problems[0][2])
    geo = sprites.staff_geometry(appearance)
    butt, tip = geo["butt"], geo["tip"]
    a_s = (geo["axis"][0], geo["axis"][1], 0.0)
    b_s = _cross((0.0, 0.0, 1.0), a_s)
    axis = _target_axis(float(h.get("lean", DEFAULT_LEAN)), float(h.get("side", 0.0)))
    n = _plane_normal(axis, h.get("facing", "front"))
    rtot = _mul(_from_columns(axis, _cross(n, axis), n), _transpose(_from_columns(a_s, b_s, (0.0, 0.0, 1.0))))
    _, rf = hand_frame(pose)
    rd = _mul(_transpose(rf), rtot)
    rot = euler_xyz(rd)
    rot = tuple(_num(v, 3) for v in rot)
    rd = rot_xyz(*rot)  # solve the translation with the rounded angles that actually get written
    scale_xy = float(visual_scale) * reference_height() / float(geo["length"])
    if scale_xy > MAX_SCALE:
        raise BossForgeError("/visual_scale", "out_of_range", "a staff this long needs display scale %.2f > %s" % (scale_xy, MAX_SCALE))
    scale_xy = _num(scale_xy, 4)
    g = float(h.get("grip", sprites.STAFF_GRIP))
    grip = (butt[0] + g * (tip[0] - butt[0]) - 0.5, butt[1] + g * (tip[1] - butt[1]) - 0.5, 0.0)
    off = h.get("offset") or (0.0, 0.0, 0.0)
    turned = _apply(rd, (scale_xy * grip[0], scale_xy * grip[1], 0.0))
    t16 = tuple(16.0 * (float(off[i]) - turned[i]) for i in range(3))
    if max(abs(v) for v in t16) > MAX_TRANSLATION:
        raise BossForgeError("/hold/offset", "out_of_range", "display translation %s exceeds +-%s" % (t16, MAX_TRANSLATION))
    return {"rotation": [_num(v, 3) for v in rot], "translation": [_num(v, 4) for v in t16], "scale": [scale_xy, scale_xy, 1.0]}


# --------------------------------------------------------------------------------------------- maintainer: derive the frames
# Animation key behind every stored pose (None = the armature's rest pose, the frame EF's vanilla-mode hand correction copies)
POSE_ANIMATIONS = {"bind": None, "idle": "biped/living/idle", "walk": "biped/living/walk", "hold_spear": "biped/living/hold_spear",
                   "walk_spear": "biped/living/walk_spear", "run_spear": "biped/living/run_spear"}
# RenderItemBase.GLOBAL_MAINHAND_ITEM_TRANSFORMS["Tool_R"] = translate(0, 0, -0.13) * rotateDeg(-90, X), row-major 4x4
_TOOL_R_CORRECTION = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, -1.0, 0.0, -0.13, 0.0, 0.0, 0.0, 1.0)


def derive_frames(cache_dir=None, ef_jar: Optional[str] = None, offline: bool = False, samples: int = 64) -> Dict[str, dict]:
    """Recompute the `poses` of `assets/data/hold-poses.json` from Epic Fight's own files (maintainer tool).

    For every pose: the item frame `P_Tool_R * correction` sampled `samples` times over the animation loop (Epic Fight's
    own keyframe interpolation, `efanim`), averaged, the rotation made orthonormal. Needs the Epic Fight jar or its
    cached assets (see `efanim.find_jar`); a changed Epic Fight version means rerunning this and checking the tests."""
    from . import efanim, efassets
    cache = cache_dir or efassets.default_cache_dir()
    arm = efanim.load_armature(cache, ef_jar, offline)
    bind = arm.bind_world()
    out: Dict[str, dict] = {}
    for pose, key in POSE_ANIMATIONS.items():
        if key is None:
            clip, times = None, [0.0]
        else:
            clip = efanim.parse_clip(json.loads(efanim.animation_file(key, cache, ef_jar, offline).read_text("utf-8")), arm, key)
            times = [clip.duration * k / samples for k in range(samples)]
        frames = []
        for t in times:
            skin = efanim.pose_matrices(arm, clip.sample(t) if clip else {})["Tool_R"]  # P_j * inverse(W_j)
            frames.append(efanim.mat_mul(efanim.mat_mul(skin, bind["Tool_R"]), _TOOL_R_CORRECTION))
        mean = [sum(f[k] for f in frames) / len(frames) for k in range(16)]
        rot = _nearest_rotation(((mean[0], mean[1], mean[2]), (mean[4], mean[5], mean[6]), (mean[8], mean[9], mean[10])))
        worst = 0.0
        for f in frames:
            fr = _nearest_rotation(((f[0], f[1], f[2]), (f[4], f[5], f[6]), (f[8], f[9], f[10])))
            tr = sum(rot[i][j] * fr[i][j] for i in range(3) for j in range(3))
            worst = max(worst, math.degrees(math.acos(max(-1.0, min(1.0, (tr - 1.0) / 2.0)))))
        out[pose] = {"origin": [round(mean[3], 5), round(mean[7], 5), round(mean[11], 5)],
                     "axes": [[round(rot[r][c], 5) for r in range(3)] for c in range(3)],
                     "loop_seconds": round(clip.duration if clip else 0.0, 4),
                     "max_rotation_deviation_deg": round(worst, 2)}
    return out
