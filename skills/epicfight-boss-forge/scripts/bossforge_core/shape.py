"""Custom boss shapes: a humanoid built from boxes, skinned rigidly to the stock Epic Fight biped armature.

`resolve()` turns a `shape` spec (silhouette + proportions + attachments + free boxes) into explicit boxes;
`build_model()` turns those boxes into a textured-ready mesh (per-face UV rects packed into one atlas) plus the
Epic Fight mesh JSON (`efmodel`). Every box is bound with weight 1.0 to one armature joint, so all EF biped
animations keep working.

Conventions (model space, blocks): y up, feet at y=0, character's right = +x, front = -z.
Rotation: R = Rz * Ry * Rx (X applied first), standard right-handed matrices, degrees, about the pivot.
A box is built unrotated from `min`/`size`, the top face is scaled by `taper` about the box's vertical centre
axis, then the box is rotated. Faces: front = -z, back = +z, left = -x, right = +x, top = +y, bottom = -y.
UV rect orientation: seen from outside, u grows to the viewer's right and v downward (side faces have +y on the
image top; top faces have -z (front) on the image top, bottom faces have +z on the image top; u grows with +x).
"""
from __future__ import annotations

import copy
import importlib
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from . import efmesh, efmodel
from .colors import is_hex
from .spec import MATERIALS, PALETTE_KEYS, BossForgeError, Err, GenFile, data

Vec = Tuple[float, float, float]

REGIONS = ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r")
FACES = ("front", "back", "left", "right", "top", "bottom")
LAYERS = ("base", "overlay")
MAX_BOXES = 400
LIMIT_X = 4.0
LIMIT_Y = (-1.0, 6.0)
LIMIT_Z = 4.0
DEFAULT_DENSITY = 32.0
DENSITY_RANGE = (8.0, 64.0)
BOX_DENSITY_RANGE = (0.25, 4.0)
TAPER_RANGE = (0.05, 3.0)
MIN_SIZE = 0.01
MAX_ATLAS = 512  # default atlas limit; `texture.atlas` may raise it to a value of ATLAS_SIZES
ATLAS_SIZES = (512, 1024)
GUTTER = 1
MIN_FACE_TEXELS = 2
SLAB_AXES = ("x", "y", "z")
EDGE_TEXELS_RANGE = (1, 8)
REFERENCE_PRESETS = ("relief",)  # attachment presets that need the spec's `reference` views
BOX_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SHAPE_KEYS = ("rig", "silhouette", "proportions", "attachments", "remove", "parts", "texture")
BOX_KEYS = ("id", "bone", "min", "size", "taper", "rotate", "pivot", "mirror", "material", "color", "accent",
            "region", "layer", "skip_faces", "density", "slab", "edge_texels")

PROPORTION_RANGES: Dict[str, Tuple[float, float]] = {
    "head": (0.6, 2.2), "shoulders": (0.7, 2.0), "torso_width": (0.6, 2.0), "torso_depth": (0.6, 2.0),
    "torso_height": (0.8, 1.4), "arm_thickness": (0.5, 2.5), "arm_length": (0.8, 1.4), "fist": (0.6, 3.0),
    "leg_thickness": (0.5, 2.5), "bulk": (0.6, 2.0),
}

# bone -> {"joint": EF joint name, "index": joint index in the armature, "pivot": (x, y, z) bind position}
def _load_bones() -> Dict[str, Dict[str, Any]]:
    rig = data("rigs")["biped"]
    by_name = {j["name"]: j for j in rig["joints"]}
    return {bone: {"joint": joint, "index": by_name[joint]["index"], "pivot": tuple(by_name[joint]["pos"])}
            for bone, joint in rig["bones"].items()}


BONES = _load_bones()
ANATOMY: Dict[str, Any] = dict(data("rigs")["biped"]["anatomy"])

_BASE_PART = {
    "head": "head", "root": "torso", "torso": "torso", "chest": "torso",
    "arm_r": "rightArm", "forearm_r": "rightArm", "hand_r": "rightArm",
    "arm_l": "leftArm", "forearm_l": "leftArm", "hand_l": "leftArm",
    "thigh_r": "rightLeg", "shin_r": "rightLeg", "thigh_l": "leftLeg", "shin_l": "leftLeg",
}
_OVERLAY_PART = {"head": "hat", "torso": "jacket", "rightArm": "rightSleeve", "leftArm": "leftSleeve",
                 "rightLeg": "rightPants", "leftLeg": "leftPants"}
_BONE_REGION = {
    "head": "head", "root": "torso", "torso": "torso", "chest": "torso",
    "arm_r": "arm_r", "forearm_r": "arm_r", "hand_r": "arm_r",
    "arm_l": "arm_l", "forearm_l": "arm_l", "hand_l": "arm_l",
    "thigh_r": "leg_r", "shin_r": "leg_r", "thigh_l": "leg_l", "shin_l": "leg_l",
}
_FACE_SWAP = {"left": "right", "right": "left"}

# four corners (BL, BR, TR, TL as seen from outside; counter-clockwise) of each face as (ix, iy, iz) corner offsets
_FACE_CORNERS = {
    "front": ((1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0)),
    "back": ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),
    "left": ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)),
    "right": ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1)),
    "top": ((0, 1, 1), (1, 1, 1), (1, 1, 0), (0, 1, 0)),
    "bottom": ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),
}
# the two box axes a face spans (u = BL -> BR, v = BL -> TL); `slab` boxes shrink faces along their thin axis
_FACE_AXES = {"front": ("x", "y"), "back": ("x", "y"), "left": ("z", "y"), "right": ("z", "y"),
              "top": ("x", "z"), "bottom": ("x", "z")}


# --------------------------------------------------------------------------- proportions / base body
def default_proportions() -> Dict[str, float]:
    return {k: 1.0 for k in PROPORTION_RANGES}


def _blank(id_: str, bone: str, mn: Sequence[float], size: Sequence[float], layer: str = "base") -> dict:
    mn = [round(float(c), 6) + 0.0 for c in mn]
    size = [round(float(c), 6) + 0.0 for c in size]
    return {
        "id": id_, "bone": bone, "min": mn, "size": size, "taper": [1.0, 1.0], "rotate": [0.0, 0.0, 0.0],
        "pivot": [mn[0] + size[0] / 2.0, mn[1] + size[1] / 2.0, mn[2] + size[2] / 2.0],
        "material": None, "color": None, "accent": None, "region": None, "layer": layer,
        "skip_faces": [], "density": 1.0,
    }


def base_boxes(proportions: Optional[Dict[str, float]] = None) -> List[dict]:
    """The 14 base humanoid boxes (layer base) for the given proportions (missing keys = 1.0).

    At all-1.0 proportions they match the stock EF biped: head 0.5 cube on top (y 1.5-2.0), torso 0.5 x 0.25
    from the hip joint to y 1.5 (pelvis | abdomen | chest split at the waist and chest joints), arms 0.25 x 0.25
    hanging from the shoulder (upper | lower | fist split at the elbow and wrist), legs 0.25 x 0.25 (upper |
    lower split at the knee). Joints never move; boxes grow around them."""
    p = default_proportions()
    p.update({k: float(v) for k, v in (proportions or {}).items() if k in p})
    a = ANATOMY
    bulk = p["bulk"]
    hip, waist, chest_y = a["hip_y"], a["waist_y"], a["chest_y"]
    knee, elbow, wrist = a["knee_y"], a["elbow_y"], a["wrist_y"]
    neck = a["head_top_y"] - a["head_size"]
    half_w = a["torso_half_width"]

    tw = 2.0 * half_w * p["torso_width"] * bulk
    cw = tw * p["shoulders"]
    td = half_w * p["torso_depth"] * bulk  # the stock torso is half as deep as it is wide
    ab_h = (chest_y - waist) * p["torso_height"]
    ch_h = (neck - chest_y) * p["torso_height"]
    ab_top = waist + ab_h
    torso_top = ab_top + ch_h
    e = a["head_size"] * p["head"]

    out = [
        _blank("pelvis", "root", (-tw / 2.0, hip, -td / 2.0), (tw, waist - hip, td)),
        _blank("abdomen", "torso", (-tw / 2.0, waist, -td / 2.0), (tw, ab_h, td)),
        _blank("chest", "chest", (-cw / 2.0, ab_top, -td / 2.0), (cw, ch_h, td)),
        _blank("head", "head", (-e / 2.0, torso_top, -e / 2.0), (e, e, e)),
    ]

    aw = 2.0 * (a["arm_x"] - half_w) * p["arm_thickness"] * bulk
    arm_cx = cw / 2.0 + aw / 2.0
    wrist_y = wrist - (elbow - hip) * (p["arm_length"] - 1.0)  # bottom of the lower arm / top of the fist
    f = p["fist"]
    hand_h = wrist - hip  # the stock hand is a thin slab below the wrist joint
    if f < 1.0:
        fw = aw * f
        fh = hand_h * f
    else:  # an absolute cube of 0.25 * fist blocks, never narrower than the arm; it hangs below the wrist
        fw = max(aw, 0.25 * f)
        fh = min(fw, hand_h + 1.6 * (fw - aw))
    for side, sx in (("r", 1.0), ("l", -1.0)):
        inner = arm_cx - aw / 2.0  # the fist keeps its inner edge flush with the arm and grows outward
        out.append(_blank("arm_%s_upper" % side, "arm_" + side, (sx * arm_cx - aw / 2.0, elbow, -aw / 2.0), (aw, torso_top - elbow, aw)))
        out.append(_blank("arm_%s_lower" % side, "forearm_" + side, (sx * arm_cx - aw / 2.0, wrist_y, -aw / 2.0), (aw, elbow - wrist_y, aw)))
        fx = inner if sx > 0 else -inner - fw
        out.append(_blank("fist_" + side, "hand_" + side, (fx, wrist_y - fh, -fw / 2.0), (fw, fh, fw)))

    lw = 2.0 * a["leg_x"] * p["leg_thickness"] * bulk
    leg_cx = max(a["leg_x"], lw / 2.0)
    for side, sx in (("r", 1.0), ("l", -1.0)):
        out.append(_blank("leg_%s_upper" % side, "thigh_" + side, (sx * leg_cx - lw / 2.0, knee, -lw / 2.0), (lw, hip - knee, lw)))
        out.append(_blank("leg_%s_lower" % side, "shin_" + side, (sx * leg_cx - lw / 2.0, 0.0, -lw / 2.0), (lw, knee, lw)))
    return out


# --------------------------------------------------------------------------- box helpers
def ef_part(box: dict) -> str:
    """EF part name a box is written to (layer + bone)."""
    base = _BASE_PART[box["bone"]]
    return base if box.get("layer", "overlay") == "base" else _OVERLAY_PART[base]


def default_region(box: dict) -> str:
    return _BONE_REGION[box["bone"]]


def _swap_lr(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    if name.endswith("_r"):
        return name[:-2] + "_l"
    if name.endswith("_l"):
        return name[:-2] + "_r"
    return name


def mirror_box(b: dict) -> dict:
    """The x-mirrored copy of an explicit box (see the contract)."""
    nb = copy.deepcopy(b)
    if b["id"].endswith("_r") or b["id"].endswith("_l"):
        nb["id"] = _swap_lr(b["id"])
    else:
        nb["id"] = b["id"] + "_m"
    nb["bone"] = _swap_lr(b["bone"])
    nb["min"] = [0.0 - (b["min"][0] + b["size"][0]), b["min"][1], b["min"][2]]
    nb["pivot"] = [0.0 - b["pivot"][0], b["pivot"][1], b["pivot"][2]]
    nb["rotate"] = [b["rotate"][0], 0.0 - b["rotate"][1], 0.0 - b["rotate"][2]]
    nb["skip_faces"] = [_FACE_SWAP.get(f, f) for f in b["skip_faces"]]
    nb["region"] = _swap_lr(b["region"])
    return nb


def _rot_matrix(rx: float, ry: float, rz: float):
    ax, ay, az = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx, cy, sy, cz, sz = math.cos(ax), math.sin(ax), math.cos(ay), math.sin(ay), math.cos(az), math.sin(az)
    # R = Rz * Ry * Rx
    return (
        (cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx),
        (sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx),
        (-sy, cy * sx, cy * cx),
    )


def box_corners(box: dict) -> List[Vec]:
    """The 8 final corners of a box (taper applied, then rotated about the pivot); index = ix + 2*iy + 4*iz
    where i* = 0 selects the low and 1 the high side of the unrotated box."""
    mn, sz = box["min"], box["size"]
    tx, tz = box["taper"]
    cx, cz = mn[0] + sz[0] / 2.0, mn[2] + sz[2] / 2.0
    pv = box["pivot"]
    m = _rot_matrix(*box["rotate"])
    out: List[Vec] = []
    for iz in (0, 1):
        for iy in (0, 1):
            for ix in (0, 1):
                x, y, z = mn[0] + ix * sz[0], mn[1] + iy * sz[1], mn[2] + iz * sz[2]
                if iy:
                    x = cx + (x - cx) * tx
                    z = cz + (z - cz) * tz
                dx, dy, dz = x - pv[0], y - pv[1], z - pv[2]
                out.append((pv[0] + m[0][0] * dx + m[0][1] * dy + m[0][2] * dz,
                            pv[1] + m[1][0] * dx + m[1][1] * dy + m[1][2] * dz,
                            pv[2] + m[2][0] * dx + m[2][1] * dy + m[2][2] * dz))
    return out


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _vec(v, n: int) -> bool:
    return isinstance(v, (list, tuple)) and len(v) == n and all(_is_num(c) for c in v)


def _color_ok(v) -> bool:
    return isinstance(v, str) and (is_hex(v) or v in PALETTE_KEYS)


def _norm_box(raw, base: str, errs: List[Err], default_layer: str, strict: bool,
              field_paths: bool) -> Optional[Tuple[dict, bool]]:
    """Validate + normalize one raw box dict -> (explicit box, mirror flag); None when it has errors."""
    start = len(errs)

    def bad(field: str, code: str, msg: str) -> None:
        errs.append(Err(base + "/" + field if field_paths else base, code, ("box %r: " % (raw.get("id"),) if not field_paths else "") + msg))

    if not isinstance(raw, dict):
        errs.append(Err(base, "bad_type", "a box must be an object"))
        return None
    if strict:
        for k in raw:
            if k not in BOX_KEYS:
                bad(str(k), "unknown_key", "unknown box key %r; allowed: %s" % (k, ", ".join(BOX_KEYS)))
    bid = raw.get("id")
    if not isinstance(bid, str) or not BOX_ID_RE.match(bid):
        bad("id", "bad_id", "box id %r must match %s" % (bid, BOX_ID_RE.pattern))
    bone = raw.get("bone")
    if bone not in BONES:
        bad("bone", "bad_bone", "bone %r is not allowed; allowed bones: %s" % (bone, ", ".join(BONES)))
    mn, size = raw.get("min"), raw.get("size")
    if not _vec(mn, 3):
        bad("min", "bad_min", "min must be [x, y, z] numbers, got %r" % (mn,))
    if not _vec(size, 3) or any(c < MIN_SIZE for c in size):
        bad("size", "bad_size", "size must be [w, h, d] numbers >= %s, got %r" % (MIN_SIZE, size))
    taper = raw.get("taper")
    if taper is None:
        taper = [1.0, 1.0]
    elif not _vec(taper, 2) or any(c < TAPER_RANGE[0] or c > TAPER_RANGE[1] for c in taper):
        bad("taper", "bad_taper", "taper must be [sx, sz] within %s..%s, got %r" % (TAPER_RANGE[0], TAPER_RANGE[1], taper))
    rotate = raw.get("rotate")
    if rotate is None:
        rotate = [0.0, 0.0, 0.0]
    elif not _vec(rotate, 3):
        bad("rotate", "bad_rotate", "rotate must be [rx, ry, rz] degrees, got %r" % (rotate,))
    pivot = raw.get("pivot")
    if pivot is not None and not _vec(pivot, 3):
        bad("pivot", "bad_rotate", "pivot must be [x, y, z], got %r" % (pivot,))
    material = raw.get("material")
    if material is not None and material not in MATERIALS:
        bad("material", "bad_material", "material %r is not allowed; allowed: %s" % (material, ", ".join(MATERIALS)))
    for key in ("color", "accent"):
        val = raw.get(key)
        if val is not None and not _color_ok(val):
            bad(key, "bad_color", "%s %r must be a palette key (%s) or #rrggbb" % (key, val, ", ".join(PALETTE_KEYS)))
    region = raw.get("region")
    if region is not None and region not in REGIONS:
        bad("region", "bad_region", "region %r is not allowed; allowed: %s (or omit it)" % (region, ", ".join(REGIONS)))
    layer = raw.get("layer", default_layer)
    if layer not in LAYERS:
        bad("layer", "bad_layer", "layer %r must be base or overlay" % (layer,))
    skip = raw.get("skip_faces", [])
    if not isinstance(skip, (list, tuple)) or any(f not in FACES for f in skip):
        bad("skip_faces", "bad_face", "skip_faces must be a subset of %s, got %r" % (", ".join(FACES), skip))
    density = raw.get("density", 1.0)
    if not _is_num(density) or density < BOX_DENSITY_RANGE[0] or density > BOX_DENSITY_RANGE[1]:
        bad("density", "out_of_range", "density %r is outside %s..%s" % (density, BOX_DENSITY_RANGE[0], BOX_DENSITY_RANGE[1]))
    slab = raw.get("slab")
    if slab is not None and slab not in SLAB_AXES:
        bad("slab", "bad_slab", "slab %r must be one of %s (the thin axis of the box)" % (slab, ", ".join(SLAB_AXES)))
    edge = raw.get("edge_texels")
    if edge is not None and (not isinstance(edge, int) or isinstance(edge, bool)
                             or edge < EDGE_TEXELS_RANGE[0] or edge > EDGE_TEXELS_RANGE[1]):
        bad("edge_texels", "out_of_range", "edge_texels %r must be an integer within %d..%d" % (edge, EDGE_TEXELS_RANGE[0], EDGE_TEXELS_RANGE[1]))
    elif edge is not None and slab is None:
        bad("edge_texels", "bad_slab", "edge_texels needs slab (the thin axis x, y or z)")
    mirror = raw.get("mirror", False)
    if not isinstance(mirror, bool):
        bad("mirror", "bad_type", "mirror must be true or false")
    if len(errs) > start:
        return None
    mn_f = [float(c) for c in mn]
    sz_f = [float(c) for c in size]
    box = {
        "id": bid, "bone": bone, "min": mn_f, "size": sz_f,
        "taper": [float(c) for c in taper], "rotate": [float(c) for c in rotate],
        "pivot": [float(c) for c in pivot] if pivot is not None else [mn_f[i] + sz_f[i] / 2.0 for i in range(3)],
        "material": material, "color": raw.get("color"), "accent": raw.get("accent"), "region": region,
        "layer": layer, "skip_faces": list(dict.fromkeys(skip)), "density": float(density),
    }
    if slab is not None:
        box["slab"] = slab
        box["edge_texels"] = int(edge) if edge is not None else 1
    return box, mirror


# --------------------------------------------------------------------------- resolution
def _presets():
    return importlib.import_module(__package__ + ".presets")


def _check_proportions(raw, errs: List[Err]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if raw is None:
        return out
    if not isinstance(raw, dict):
        errs.append(Err("/proportions", "bad_type", "proportions must be an object"))
        return out
    for k, v in raw.items():
        path = "/proportions/%s" % k
        if k not in PROPORTION_RANGES:
            errs.append(Err(path, "unknown_proportion", "unknown proportion %r; allowed: %s" % (k, ", ".join(PROPORTION_RANGES))))
            continue
        lo, hi = PROPORTION_RANGES[k]
        if not _is_num(v):
            errs.append(Err(path, "bad_type", "expected a number, got %r" % (v,)))
        elif v < lo or v > hi:
            errs.append(Err(path, "out_of_range", "%r is outside the allowed range %s..%s" % (v, lo, hi)))
        else:
            out[k] = float(v)
    return out


def _box_out_of_bounds(box: dict) -> Optional[Vec]:
    for c in box_corners(box):
        if (abs(c[0]) > LIMIT_X + 1e-9 or abs(c[2]) > LIMIT_Z + 1e-9
                or c[1] < LIMIT_Y[0] - 1e-9 or c[1] > LIMIT_Y[1] + 1e-9):
            return c
    return None


def _resolve(shape, views: Optional[dict] = None, notes: Optional[dict] = None,
             owners: Optional[List[str]] = None) -> Tuple[List[dict], List[str], Dict[str, float], List[Err]]:
    """(explicit boxes, per-box origin pointers, effective proportions, all errors). `views` are the reference views
    for `relief` attachments; `notes` collects {"warnings": [Err], "relief": [dict]}; `owners` receives the owner of
    every box ("base", the attachment id or "parts")."""
    errs: List[Err] = []
    boxes: List[dict] = []
    origins: List[str] = []
    props = default_proportions()
    if not isinstance(shape, dict):
        errs.append(Err("", "bad_type", "shape must be an object"))
        return boxes, origins, props, errs
    for k in shape:
        if k not in SHAPE_KEYS:
            errs.append(Err("/" + str(k), "unknown_key", "unknown shape key %r; allowed: %s" % (k, ", ".join(SHAPE_KEYS))))
    if shape.get("rig", "biped") != "biped":
        errs.append(Err("/rig", "bad_rig", "rig %r is not supported; only 'biped' is" % (shape.get("rig"),)))
    tex = shape.get("texture")
    if tex is not None:
        if not isinstance(tex, dict):
            errs.append(Err("/texture", "bad_type", "texture must be an object"))
        else:
            for k in tex:
                if k not in ("density", "atlas"):
                    errs.append(Err("/texture/" + str(k), "unknown_key", "unknown texture key %r; allowed: density, atlas" % (k,)))
            d = tex.get("density", DEFAULT_DENSITY)
            if not _is_num(d):
                errs.append(Err("/texture/density", "bad_type", "density must be a number, got %r" % (d,)))
            elif d < DENSITY_RANGE[0] or d > DENSITY_RANGE[1]:
                errs.append(Err("/texture/density", "out_of_range", "%r is outside the allowed range %s..%s texels per block" % (d, DENSITY_RANGE[0], DENSITY_RANGE[1])))
            atlas = tex.get("atlas", MAX_ATLAS)
            if not _is_num(atlas):
                errs.append(Err("/texture/atlas", "bad_type", "atlas must be 512 or 1024, got %r" % (atlas,)))
            elif atlas not in ATLAS_SIZES:
                errs.append(Err("/texture/atlas", "out_of_range", "atlas %r must be one of %s texels" % (atlas, ", ".join(str(a) for a in ATLAS_SIZES))))

    # silhouette
    sil = None  # type: Optional[dict]
    name = shape.get("silhouette")
    if name is not None:
        presets = _presets()
        names = list(presets.list_silhouettes())
        if not isinstance(name, str) or name not in names:
            errs.append(Err("/silhouette", "unknown_silhouette", "unknown silhouette %r; allowed: %s" % (name, ", ".join(names))))
        else:
            sil = presets.silhouette(name)

    # proportions: silhouette (clamped) then shape overrides (validated)
    if sil is not None:
        for k, v in (sil.get("proportions") or {}).items():
            if k in PROPORTION_RANGES and _is_num(v):
                lo, hi = PROPORTION_RANGES[k]
                props[k] = min(hi, max(lo, float(v)))
    props.update(_check_proportions(shape.get("proportions"), errs))

    # attachments
    slots: List[Tuple[dict, str]] = []
    sil_atts = [copy.deepcopy(a) for a in (sil.get("attachments") or [])] if sil is not None else []
    remove = shape.get("remove", [])
    remove_ids = set()
    if not isinstance(remove, list):
        errs.append(Err("/remove", "bad_type", "remove must be a list of silhouette attachment ids"))
    else:
        known = [a.get("id") for a in sil_atts]
        for j, rid in enumerate(remove):
            if rid not in known:
                errs.append(Err("/remove/%d" % j, "unknown_remove", "%r is not an attachment of the silhouette; silhouette attachments: %s" % (rid, ", ".join(map(str, known)) or "(none)")))
            else:
                remove_ids.add(rid)
    for a in sil_atts:
        if a.get("id") not in remove_ids:
            slots.append((a, "/silhouette"))
    user_atts = shape.get("attachments", [])
    if not isinstance(user_atts, list):
        errs.append(Err("/attachments", "bad_type", "attachments must be a list"))
        user_atts = []
    seen_att = set()
    valid_user: List[Tuple[dict, str]] = []
    for i, att in enumerate(user_atts):
        path = "/attachments/%d" % i
        if not isinstance(att, dict):
            errs.append(Err(path, "bad_type", "an attachment must be an object"))
            continue
        before = len(errs)
        aid = att.get("id")
        if not isinstance(aid, str) or not BOX_ID_RE.match(aid):
            errs.append(Err(path + "/id", "bad_id", "attachment id %r must match %s" % (aid, BOX_ID_RE.pattern)))
        elif aid in seen_att:
            errs.append(Err(path + "/id", "duplicate_attachment_id", "attachment id %r is used twice" % (aid,)))
        else:
            seen_att.add(aid)
        presets = _presets()
        preset_names = list(presets.list_presets())
        if att.get("preset") not in preset_names:
            errs.append(Err(path + "/preset", "unknown_preset", "unknown preset %r; allowed: %s" % (att.get("preset"), ", ".join(preset_names))))
        else:
            for e in presets.validate_attachment(copy.deepcopy(att)):
                errs.append(Err(path + e.path, e.code, e.message))
        if len(errs) == before:
            valid_user.append((att, path))
    for att, path in valid_user:
        for idx, (cur, _origin) in enumerate(slots):
            if cur.get("id") == att["id"]:
                slots[idx] = (att, path)
                break
        else:
            slots.append((att, path))

    # boxes: base body, attachments, free parts
    raw_boxes: List[Tuple[dict, str]] = [(b, "/proportions") for b in base_boxes(props)]
    raw_owner: List[str] = ["base"] * len(raw_boxes)
    anatomy = copy.deepcopy(ANATOMY)
    for att, origin in slots:
        extra = {"views": views, "notes": notes} if att.get("preset") in REFERENCE_PRESETS else {}
        warned = len(notes.get("warnings", ())) if notes is not None else 0
        try:
            produced = _presets().expand(copy.deepcopy(att), dict(props), copy.deepcopy(anatomy), **extra)
        except BossForgeError as e:
            errs.append(Err(origin + e.err.path, e.err.code, e.err.message))
            continue
        if notes is not None:  # warnings of a preset point at the attachment
            lst = notes.get("warnings", [])
            for wi in range(warned, len(lst)):
                lst[wi] = Err(origin + lst[wi].path, lst[wi].code, lst[wi].message)
        for rb in produced:
            nb = _norm_box(rb, origin, errs, "overlay", False, False)
            if nb is not None:
                raw_boxes.append((nb[0], origin))
                raw_owner.append(str(att.get("id", att.get("preset"))))
                if nb[1]:
                    raw_boxes.append((mirror_box(nb[0]), origin))
                    raw_owner.append(str(att.get("id", att.get("preset"))))
    parts = shape.get("parts", [])
    if not isinstance(parts, list):
        errs.append(Err("/parts", "bad_type", "parts must be a list of boxes"))
        parts = []
    for i, rb in enumerate(parts):
        nb = _norm_box(rb, "/parts/%d" % i, errs, "overlay", True, True)
        if nb is not None:
            raw_boxes.append((nb[0], "/parts/%d" % i))
            raw_owner.append("parts")
            if nb[1]:
                raw_boxes.append((mirror_box(nb[0]), "/parts/%d" % i))
                raw_owner.append("parts")

    ids = set()
    for (b, origin), owner in zip(raw_boxes, raw_owner):
        if b["id"] in ids:
            errs.append(Err(origin + ("/id" if origin.startswith("/parts/") else ""), "duplicate_box_id", "box id %r is used more than once" % (b["id"],)))
            continue
        ids.add(b["id"])
        boxes.append(b)
        origins.append(origin)
        if owners is not None:
            owners.append(owner)
    if len(boxes) > MAX_BOXES:
        errs.append(Err("", "too_many_boxes", "%d boxes after expansion; the limit is %d" % (len(boxes), MAX_BOXES)))
    for b, origin in zip(boxes, origins):
        c = _box_out_of_bounds(b)
        if c is not None:
            errs.append(Err(origin, "out_of_bounds", "box %r has a vertex at (%.2f, %.2f, %.2f); limits are |x|<=%s, y in [%s, %s], |z|<=%s" % (b["id"], c[0], c[1], c[2], LIMIT_X, LIMIT_Y[0], LIMIT_Y[1], LIMIT_Z)))
    return boxes, origins, props, errs


def _dedupe(errs: List[Err]) -> List[Err]:
    seen = set()
    out = []
    for e in errs:
        key = (e.path, e.code)
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _load_views(spec: Optional[dict], shape: Any) -> dict:
    """The reference views of `spec` when `shape` has a reference-driven attachment (relief), else {}.
    Raises BossForgeError (reference_*) when the images cannot be loaded."""
    atts = shape.get("attachments") if isinstance(shape, dict) else None
    if not isinstance(spec, dict) or not isinstance(atts, list):
        return {}
    if not any(isinstance(a, dict) and a.get("preset") in REFERENCE_PRESETS for a in atts):
        return {}
    from . import reference
    return reference.views_for(spec)


def validate_shape(shape: dict, spec: Optional[dict] = None) -> List[Err]:
    """All problems of a `shape` spec; paths are relative to the shape (e.g. /parts/0/size). `spec` (the whole
    boss spec, raw or normalized) supplies the `reference` views for `relief` attachments."""
    try:
        views = _load_views(spec, shape)
    except BossForgeError as e:
        return [Err("", e.err.code, e.err.message)]
    return _dedupe(_resolve(shape, views)[3])


def _resolve_checked(shape: dict, views: Optional[dict], notes: Optional[dict]) -> Tuple[List[dict], dict, List[str]]:
    owners: List[str] = []
    boxes, _origins, props, errs = _resolve(shape, views, notes, owners)
    errs = _dedupe(errs)
    if errs:
        exc = BossForgeError(errs[0].path, errs[0].code, errs[0].message)
        exc.errors = errs  # type: ignore[attr-defined]
        raise exc
    return boxes, props, owners


def resolve(shape: dict, views: Optional[dict] = None, notes: Optional[dict] = None) -> Tuple[List[dict], dict]:
    """(explicit boxes with unique ids, effective proportions). Raises BossForgeError (first problem; all of
    them are on `.errors`). `views` ({name: reference.View}) feed `relief` attachments, `notes` collects their
    warnings and measurements ({"warnings": [Err], "relief": [dict]})."""
    boxes, props, _owners = _resolve_checked(shape, views, notes)
    return boxes, props


# --------------------------------------------------------------------------- atlas
def _shelf_pack(order: List[int], sizes: List[Tuple[int, int]], side: int) -> Optional[List[Tuple[int, int]]]:
    pos: List[Tuple[int, int]] = [(0, 0)] * len(sizes)
    x = y = row_h = 0
    for i in order:
        w, h = sizes[i]
        if w > side:
            return None
        if x + w > side:
            y += row_h + GUTTER
            x = 0
            row_h = 0
        if y + h > side:
            return None
        pos[i] = (x, y)
        x += w + GUTTER
        row_h = max(row_h, h)
    return pos


def pack_rects(sizes: List[Tuple[int, int]], max_atlas: int = MAX_ATLAS) -> Optional[Tuple[int, List[Tuple[int, int]]]]:
    """Smallest square power-of-two atlas (<= max_atlas) holding every (w, h) rect with a 1-texel gutter;
    returns (side, [(x, y)]) or None. Deterministic shelf packing, tallest first."""
    order = sorted(range(len(sizes)), key=lambda i: (-sizes[i][1], -sizes[i][0], i))
    area = sum((w + GUTTER) * (h + GUTTER) for w, h in sizes)
    side = 16
    while (side + GUTTER) * (side + GUTTER) < area and side < max_atlas:
        side *= 2
    while side <= max_atlas:
        pos = _shelf_pack(order, sizes, side)
        if pos is not None:
            return side, pos
        side *= 2
    return None


def _dist(a: Vec, b: Vec) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _normal(bl: Vec, br: Vec, tl: Vec) -> Vec:
    ux, uy, uz = br[0] - bl[0], br[1] - bl[1], br[2] - bl[2]
    vx, vy, vz = tl[0] - bl[0], tl[1] - bl[1], tl[2] - bl[2]
    nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
    ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    return (nx / ln, ny / ln, nz / ln)


# --------------------------------------------------------------------------- model
@dataclass
class ShapeModel:
    boxes: List[dict]
    proportions: dict
    mesh: efmesh.Mesh
    tpl: dict
    atlas: Tuple[int, int]
    mesh_json: dict
    height: float
    bounds: Tuple[Vec, Vec]
    warnings: List[Err]
    box_counts: Dict[str, int] = field(default_factory=dict)  # boxes per owner: "base", attachment ids, "parts"
    relief: List[dict] = field(default_factory=list)  # measurements of every `relief` attachment
    atlas_fill: float = 0.0  # fraction of the atlas covered by face rects


def build_model(n: dict, ctx=None) -> ShapeModel:
    """Build the mesh, template and EF JSON for a normalized boss spec `n` (uses n["shape"], n["id"], n["mod_id"]).
    A spec with a `mesh` block (a Blender bundle) is built by `meshsrc.build_model`, which returns the same kind of model."""
    if isinstance(n.get("mesh"), dict):
        from . import meshsrc
        return meshsrc.build_model(n, ctx)
    shape = n.get("shape")
    if not isinstance(shape, dict):
        raise BossForgeError("/shape", "required", "the spec has no shape object")
    views = _load_views(n, shape)
    notes: Dict[str, list] = {}
    boxes, props, owners = _resolve_checked(shape, views, notes)
    tex = shape.get("texture") or {}
    density0 = float(tex.get("density", DEFAULT_DENSITY))
    max_atlas = int(tex.get("atlas", MAX_ATLAS))

    # geometry
    positions: List[Vec] = []
    joint_of_position: List[int] = []
    box_pos0: List[int] = []
    faces = []  # (box index, face name, (BL, BR, TR, TL) position indices, width blocks, height blocks)
    for bi, b in enumerate(boxes):
        corners = [(efmodel.rnd(c[0]), efmodel.rnd(c[1]), efmodel.rnd(c[2])) for c in box_corners(b)]
        base = len(positions)
        box_pos0.append(base)
        positions.extend(corners)
        joint_of_position.extend([BONES[b["bone"]]["index"]] * 8)
        for face in FACES:
            if face in b["skip_faces"]:
                continue
            idx = [base + ix + 2 * iy + 4 * iz for ix, iy, iz in _FACE_CORNERS[face]]
            bl, br, tr, tl = (positions[i] for i in idx)
            width = (_dist(bl, br) + _dist(tl, tr)) / 2.0
            height = (_dist(bl, tl) + _dist(br, tr)) / 2.0
            faces.append((bi, face, idx, width, height))

    # atlas
    warnings: List[Err] = list(notes.get("warnings", []))
    density = density0
    while True:
        sizes = []
        for bi, face, _idx, width, height in faces:
            b = boxes[bi]
            d = density * b["density"]
            w, h = max(MIN_FACE_TEXELS, int(width * d + 0.5)), max(MIN_FACE_TEXELS, int(height * d + 0.5))
            if "slab" in b:  # thin slab: faces along the thin axis are edge strips of `edge_texels`
                au, av = _FACE_AXES[face]
                if au == b["slab"]:
                    w = b["edge_texels"]
                if av == b["slab"]:
                    h = b["edge_texels"]
            sizes.append((w, h))
        packed = pack_rects(sizes, max_atlas)
        if packed is not None:
            break
        if density <= DENSITY_RANGE[0]:
            raise BossForgeError("/shape/texture/density", "texture_too_large",
                                 "the %d boxes do not fit a %dx%d atlas even at %s texels per block; remove boxes or shrink them%s"
                                 % (len(boxes), max_atlas, max_atlas, DENSITY_RANGE[0], "" if max_atlas >= ATLAS_SIZES[-1] else " (or set shape.texture.atlas to 1024)"))
        density = max(DENSITY_RANGE[0], density / 2.0)
    side, rect_pos = packed
    if density != density0:
        warnings.append(Err("/texture/density", "density_reduced", "texture density reduced from %g to %g texels per block to fit a %dx%d atlas%s"
                            % (density0, density, side, side, "" if max_atlas >= ATLAS_SIZES[-1] else " (shape.texture.atlas 1024 allows a larger atlas)")))

    # uvs, normals, triangles
    uvs: List[Tuple[float, float]] = []
    normals: List[Vec] = []
    normal_index: Dict[Vec, int] = {}
    tris: List[efmesh.Tri] = []
    part_tris: Dict[str, List[efmodel.Tri3]] = {}
    for (bi, _face, idx, _w, _h), (w, h), (x, y) in zip(faces, sizes, rect_pos):
        b = boxes[bi]
        bl, br, tl = positions[idx[0]], positions[idx[1]], positions[idx[3]]
        nrm = _normal(bl, br, tl)
        key = (efmodel.rnd(nrm[0]), efmodel.rnd(nrm[1]), efmodel.rnd(nrm[2]))
        if key not in normal_index:
            normal_index[key] = len(normals)
            normals.append(key)
        ni = normal_index[key]
        u0 = len(uvs)
        for px, py in ((x, y + h), (x + w, y + h), (x + w, y), (x, y)):  # BL, BR, TR, TL
            uvs.append((efmodel.rnd(px / side), efmodel.rnd(py / side)))
        for a, c, d in ((0, 1, 2), (0, 2, 3)):
            pi = (idx[a], idx[c], idx[d])
            ti = (u0 + a, u0 + c, u0 + d)
            tris.append(efmesh.Tri(b["id"], pi, ti, (ni, ni, ni)))
            part_tris.setdefault(ef_part(b), []).append(tuple((pi[k], ti[k], ni) for k in range(3)))  # type: ignore[arg-type]

    # template
    part_regions: Dict[str, str] = {}
    part_style: Dict[str, dict] = {}
    for b in boxes:
        if b["region"] is not None:
            part_regions[b["id"]] = b["region"]
        elif b["layer"] == "base":
            part_regions[b["id"]] = default_region(b)
        else:
            part_regions[b["id"]] = "box:" + b["id"]
        if b["material"] is not None or b["color"] is not None or b["accent"] is not None:
            part_style[b["id"]] = {"material": b["material"], "color": b["color"], "accent": b["accent"]}
    xs = [p[0] for p in positions]
    ys = [p[1] for p in positions]
    zs = [p[2] for p in positions]
    bounds = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
    height = bounds[1][1]
    model_id = "%s:entity/%s" % (n.get("mod_id", "mod"), n.get("id", "boss"))
    tpl = {
        "label": "Custom (EF biped rig)", "humanoid": True, "mesh_name": None, "model_id": model_id,
        "armature_id": "epicfight:entity/biped", "renderer": "player",
        "part_regions": dict(part_regions), "part_style": part_style, "overlay_parts": [],
        "overlay_to_base": True, "authored_uv": [side, side], "uv_origin": "top", "front_sign": -1,
        "right_sign": 1, "axis_mode": "x,z,-y", "unit_scale": 1.0, "index_order": "pos,uv,nrm",
        "model_height": height, "weapon_capable": True, "custom": True,
    }
    mesh = efmesh.Mesh(positions, normals, uvs, tris, part_regions)
    mesh_json = efmodel.write_mesh_json(positions, normals, uvs, part_tris, joint_of_position)
    counts: Dict[str, int] = {}
    for owner in owners:
        counts[owner] = counts.get(owner, 0) + 1
    fill = sum(w * h for w, h in sizes) / float(side * side)
    return ShapeModel(boxes=boxes, proportions=props, mesh=mesh, tpl=tpl, atlas=(side, side), mesh_json=mesh_json,
                      height=height, bounds=bounds, warnings=warnings, box_counts=counts,
                      relief=list(notes.get("relief", [])), atlas_fill=round(fill, 4))


def generate(n: dict, ctx) -> Tuple[List[GenFile], Dict[str, str]]:
    """The custom EF mesh JSON for the boss: assets/<mod>/animmodels/entity/<id>.json."""
    model = build_model(n, ctx)
    problems = efmodel.check_mesh_json(model.mesh_json)
    if problems:
        raise BossForgeError("/shape", "bad_mesh", "generated mesh is invalid: " + "; ".join(problems[:5]))
    rel = "src/main/resources/assets/%s/animmodels/entity/%s.json" % (n["mod_id"], n["id"])
    return [GenFile(rel, efmodel.dumps(model.mesh_json).encode("utf-8"), "json")], {}
