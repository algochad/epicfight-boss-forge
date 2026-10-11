"""Attachment presets and whole-body silhouettes for custom boss shapes.

A preset turns a small parameter set (`{"id": "horns", "preset": "horns", "style": "ram", "scale": 1.2}`) into
explicit box dicts (see the shape contract) positioned on the PROPORTIONED body, so horns sit on the scaled head,
pauldrons on the scaled shoulders and capes behind the scaled back. `silhouettes.json` bundles proportions and
attachments into named whole-body looks.

Body model (every number is read from `shape.base_boxes(proportions)`, the single source of truth):
    head   : edge `he` = 0.5 * proportions.head, base `head_bot` = torso top, top `head_top` = head_bot + he,
             centre z `hcz` (front face z = hcz - he/2; the character faces -z, its right is +x)
    torso  : chest half width `cw` = 0.25 * torso_width * shoulders * bulk, half depth `cd` = 0.125 * torso_depth *
             bulk, `chest_bot` (chest joint) .. `chest_top` (= neck = head_bot); abdomen/pelvis half width `aw`/`pw`
    arms   : centre x `arm_cx` = cw + arm_t/2, thickness `arm_t` = 0.25 * arm_thickness * bulk, top `arm_top` =
             chest_top, `elbow`, `wrist` (bottom of the lower arm), fist `fist_w` x `fist_h` at `fist_cx`, bottom `fist_bot`
    legs   : centre x `leg_cx`, thickness `leg_t`, `hip` (top of the thighs), `knee`
Presets size their parts in blocks times `scale` (about their anchor, the point where they touch the body) and,
for head-mounted pieces, times the head unit `hu` = he / 0.5 (horns use hu**0.75) so a big head gets big horns.
Clearance of capes, skirts, wings and banners is computed against the real base boxes (`Body.back_z`,
`Body.front_z`), so they stay outside the body for thick limbs, wide chests and big fists too.

Positions are written for the character's RIGHT side (+x) with `mirror: true`; ids of mirrored boxes end in `_r`
(the copy becomes `_l`). Box ids are local; `expand` prefixes them with the attachment id. Curved shapes
(horns, tails, wing struts, tusks) are chains of tapered boxes built by `_Ctx.path`: segment i runs from point
i to point i+1, its top face is scaled to the width at point i+1 (taper) and its axis is aligned with the segment
by the Euler angles `_orient` (R = Rz*Ry*Rx, rx = asin(dz), rz = atan2(-dx, dy)).
"""
from __future__ import annotations

import copy
import importlib
import json
import math
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import DATA_DIR
from . import spec as _spec
from .colors import is_hex

Vec = Tuple[float, float, float]

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
SCALE_RANGE = (0.5, 2.5)
MAX_BOXES = 40  # per attachment, after mirror expansion
_SILHOUETTE_BY_ARCHETYPE = {
    "knight": "knight", "undead": "skeletal_king", "beast": "beast", "demon": "fiend",
    "elemental": "colossus", "golem": "colossus", "wraith": "wraith", "cultist": "caster",
}


# --------------------------------------------------------------------------- vector helpers
def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)


def _len(a):
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _norm(a):
    n = _len(a)
    if n < 1e-9:
        return (0.0, 1.0, 0.0)
    return (a[0] / n, a[1] / n, a[2] / n)


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _r(v: float) -> float:
    return round(float(v), 4) + 0.0


def _orient(d) -> List[float]:
    """Euler angles [rx, 0, rz] (degrees, R = Rz*Ry*Rx) that turn the +y axis of a box onto direction d."""
    x, y, z = _norm(d)
    return [math.degrees(math.asin(_clamp(z, -1.0, 1.0))), 0.0, math.degrees(math.atan2(-x, y))]


# --------------------------------------------------------------------------- body model
class Body:
    """Anchor points and clearances of the proportioned base humanoid (see the module docstring)."""

    def __init__(self, boxes: List[dict]):
        by = {b["id"]: b for b in boxes}
        self.boxes = [b for b in boxes if b["id"] != "head" and b.get("layer", "base") == "base"]
        head, chest, abd, pel = by["head"], by["chest"], by["abdomen"], by["pelvis"]
        self.he = head["size"][0]
        self.head_bot = head["min"][1]
        self.head_top = head["min"][1] + head["size"][1]
        self.hcz = head["min"][2] + head["size"][2] / 2.0
        self.cw = chest["size"][0] / 2.0
        self.cd = chest["size"][2] / 2.0
        self.cz = chest["min"][2] + chest["size"][2] / 2.0
        self.chest_bot = chest["min"][1]
        self.chest_top = chest["min"][1] + chest["size"][1]
        self.aw = abd["size"][0] / 2.0
        self.ad = abd["size"][2] / 2.0
        self.abd_bot = abd["min"][1]
        self.pw = pel["size"][0] / 2.0
        arm_u, arm_l, fist = by["arm_r_upper"], by["arm_r_lower"], by["fist_r"]
        self.arm_t = arm_u["size"][0]
        self.arm_cx = arm_u["min"][0] + arm_u["size"][0] / 2.0
        self.arm_top = arm_u["min"][1] + arm_u["size"][1]
        self.elbow = arm_u["min"][1]
        self.wrist = arm_l["min"][1]
        self.fist_w = fist["size"][0]
        self.fist_h = fist["size"][1]
        self.fist_cx = fist["min"][0] + fist["size"][0] / 2.0
        self.fist_bot = fist["min"][1]
        leg_u = by["leg_r_upper"]
        self.leg_t = leg_u["size"][0]
        self.leg_cx = leg_u["min"][0] + leg_u["size"][0] / 2.0
        self.hip = leg_u["min"][1] + leg_u["size"][1]
        self.knee = leg_u["min"][1]

    def _near(self, y0: float, y1: float, x0: float, x1: float, arms: bool):
        for b in self.boxes:
            if not arms and (b["id"].startswith("arm_") or b["id"].startswith("fist_")):
                continue
            if (b["min"][1] < y1 and b["min"][1] + b["size"][1] > y0
                    and b["min"][0] < x1 and b["min"][0] + b["size"][0] > x0):
                yield b

    def back_z(self, y0: float, y1: float, x0: float = -9.0, x1: float = 9.0, arms: bool = True) -> float:
        """Largest z (towards the back) of the base boxes overlapping the y and x ranges (head excluded)."""
        vals = [b["min"][2] + b["size"][2] for b in self._near(y0, y1, x0, x1, arms)]
        return max(vals) if vals else self.cz + self.cd

    def front_z(self, y0: float, y1: float, x0: float = -9.0, x1: float = 9.0, arms: bool = True) -> float:
        """Smallest z (towards the front) of the base boxes overlapping the y and x ranges (head excluded)."""
        vals = [b["min"][2] for b in self._near(y0, y1, x0, x1, arms)]
        return min(vals) if vals else self.cz - self.cd


def _shape_mod():
    return importlib.import_module(__package__ + ".shape")


def body_from(proportions: Optional[dict]) -> Body:
    return Body(_shape_mod().base_boxes(proportions or {}))


# --------------------------------------------------------------------------- parameter schema
def _enum(default, allowed):
    return {"type": "enum", "default": default, "allowed": list(allowed)}


def _float(default, lo, hi):
    return {"type": "float", "default": float(default), "range": [float(lo), float(hi)]}


def _int(default, lo, hi):
    return {"type": "int", "default": int(default), "range": [int(lo), int(hi)]}


def _bool(default):
    return {"type": "bool", "default": bool(default)}


COMMON_PARAMS = ("id", "scale", "color", "accent", "material")
PRESETS: Dict[str, dict] = {}
_FUNCS: Dict[str, Callable] = {}
_ROLES: Dict[str, Dict[str, Tuple[str, str, str]]] = {}
# (material, color, accent) triples; `main` is overridable by the attachment's material/color/accent
_GLOBAL_ROLES = {
    "glow": ("plate_gold", "eyes", "eyes"),
    "dark": ("leather", "#1a1418", "#1a1418"),
    "wood": ("leather", "#3a2616", "#3a2616"),
    "bone": ("bone", "skin", "accent"),
    "trim": ("plate_gold", "accent", "accent"),
    "steel": ("plate_steel", "primary", "accent"),
    "cloth": ("cloth", "secondary", "accent"),
}


def _preset(name: str, description: str, params: Dict[str, dict], main: Tuple[str, str, str],
            trim: Optional[Tuple[str, str, str]] = None):
    def deco(fn):
        ordered = dict(params)
        ordered["id"] = {"type": "id", "default": name, "pattern": ID_RE.pattern}
        ordered["scale"] = _float(1.0, *SCALE_RANGE)
        ordered["color"] = {"type": "color", "default": main[1]}
        ordered["accent"] = {"type": "color", "default": main[2]}
        ordered["material"] = {"type": "enum", "default": main[0], "allowed": list(_spec.MATERIALS)}
        PRESETS[name] = {"description": description, "params": ordered}
        roles = {"main": main}
        if trim is not None:
            roles["trim"] = trim
        _ROLES[name] = roles
        _FUNCS[name] = fn
        return fn
    return deco


class _Ctx:
    """Everything a preset function needs: body, scale, resolved parameters and the box builders."""

    def __init__(self, name: str, att: dict, body: Body):
        self.name = name
        self.b = body
        schema = PRESETS[name]["params"]
        self.p = {k: att.get(k, v["default"]) for k, v in schema.items() if k not in COMMON_PARAMS}
        self.s = float(att.get("scale", 1.0))
        self.hu = body.he / 0.5
        self.over = {k: att.get(k) for k in ("material", "color", "accent")}
        self.roles = dict(_GLOBAL_ROLES)
        self.roles.update(_ROLES[name])

    def style(self, role: str) -> Tuple[str, str, str]:
        m, col, acc = self.roles[role]
        if role == "main":
            m = self.over["material"] or m
            col = self.over["color"] or col
            acc = self.over["accent"] or acc
        elif role == "trim" and self.over["accent"]:
            col = acc = self.over["accent"]
        return m, col, acc

    def box(self, lid: str, bone: str, cen, size, role: str = "main", taper=None, rotate=None, pivot=None,
            mirror: bool = False) -> dict:
        w, h, d = (max(0.01, float(v)) for v in size)
        out: Dict[str, Any] = {
            "id": lid, "bone": bone,
            "min": [_r(cen[0] - w / 2.0), _r(cen[1] - h / 2.0), _r(cen[2] - d / 2.0)],
            "size": [_r(w), _r(h), _r(d)],
        }
        if taper is not None and (abs(taper[0] - 1.0) > 1e-6 or abs(taper[1] - 1.0) > 1e-6):
            out["taper"] = [_r(_clamp(taper[0], 0.05, 3.0)), _r(_clamp(taper[1], 0.05, 3.0))]
        if rotate is not None and any(abs(a) > 1e-6 for a in rotate):
            out["rotate"] = [_r(a) for a in rotate]
        if pivot is not None:
            out["pivot"] = [_r(v) for v in pivot]
        if mirror:
            out["mirror"] = True
        out["material"], out["color"], out["accent"] = self.style(role)
        return out

    def path(self, prefix: str, bone: str, pts: Sequence[Vec], widths: Sequence[float],
             depths: Optional[Sequence[float]] = None, role: str = "main", suffix: str = "",
             mirror: bool = False, overlap: float = 0.08) -> List[dict]:
        """One tapered box per consecutive point pair (ids `prefix` + index + `suffix`); `widths[i]` and
        `depths[i]` are the thickness at point i (width = local x, depth = local z of the box)."""
        out: List[dict] = []
        depths = depths if depths is not None else widths
        last = len(pts) - 2
        for i in range(len(pts) - 1):
            v = _sub(pts[i + 1], pts[i])
            ln = _len(v)
            if ln < 1e-6:
                continue
            d = (v[0] / ln, v[1] / ln, v[2] / ln)
            ext = ln * (1.0 + overlap) if i < last else ln
            cen = _add(pts[i], _mul(d, ext / 2.0))
            w0, w1 = max(widths[i], 0.01), max(widths[i + 1], 0.01)
            d0, d1 = max(depths[i], 0.01), max(depths[i + 1], 0.01)
            out.append(self.box("%s%d%s" % (prefix, i, suffix), bone, cen, (w0, ext, d0), role=role,
                                taper=(w1 / w0, d1 / d0), rotate=_orient(d), mirror=mirror))
        return out

    def walk(self, p0: Vec, dirs: Sequence[Vec], lengths: Sequence[float]) -> List[Vec]:
        pts = [p0]
        for d, ln in zip(dirs, lengths):
            pts.append(_add(pts[-1], _mul(_norm(d), ln)))
        return pts

    def horn(self, prefix: str, bone: str, p0: Vec, dirs, lengths, w0: float, tapers, role: str = "main",
             suffix: str = "_r", mirror: bool = True, depth: Optional[float] = None) -> List[dict]:
        """A curved, tapering chain of boxes: segment directions `dirs`, lengths `lengths`, base width `w0`,
        per-segment taper factors `tapers`."""
        pts = self.walk(p0, dirs, lengths)
        widths = [w0]
        for t in tapers:
            widths.append(widths[-1] * t)
        depths = None if depth is None else [depth * wi / w0 for wi in widths]
        return self.path(prefix, bone, pts, widths, depths, role=role, suffix=suffix, mirror=mirror)


# --------------------------------------------------------------------------- head presets
@_preset("crown", "Crown or circlet around the top of the head.",
         {"style": _enum("band", ("band", "spiked", "tall", "broken")), "gem": _bool(True)},
         main=("plate_gold", "accent", "eyes"))
def _crown(c: _Ctx) -> List[dict]:
    b, s, hu = c.b, c.s, c.hu
    k = hu * s
    hw = b.he / 2.0
    top, cz = b.head_top, b.hcz
    st = c.p["style"]
    R = max(hw + 0.03, (hw + 0.035 * hu) * s)  # outer half size of the band
    hb = 0.15 * k
    t = 0.05 * k
    yc = top - 0.1 * hb
    ytop = yc + hb / 2.0
    zf, zb = cz - R + t / 2, cz + R - t / 2
    out: List[dict] = [c.box("front", "head", (0, yc, zf), (2 * R, hb, t))]
    if st == "broken":
        out.append(c.box("back", "head", (-0.45 * R, yc, zb), (1.1 * R, hb, t)))
        out.append(c.box("side_l", "head", (-(R - t / 2), yc, cz - 0.1 * R), (t, hb, 1.5 * R)))
    else:
        out.append(c.box("back", "head", (0, yc, zb), (2 * R, hb, t)))
        out.append(c.box("side_r", "head", (R - t / 2, yc, cz), (t, hb, 2 * R - 2 * t), mirror=True))

    def spike(lid, x, z, h, w, tilt=0.0, mirror=False):
        return c.box(lid, "head", (x, ytop + h / 2 - 0.01, z), (w, h, w), taper=(0.12, 0.12), rotate=[0, 0, tilt],
                     mirror=mirror)

    if st == "spiked":
        out += [spike("sp_f", 0, zf, 0.3 * k, 0.1 * k),
                spike("sp_f2_r", 0.62 * R, zf, 0.22 * k, 0.09 * k, mirror=True),
                spike("sp_s_r", R - t / 2, cz, 0.22 * k, 0.09 * k, mirror=True),
                spike("sp_b", 0, zb, 0.22 * k, 0.09 * k),
                spike("sp_b2_r", 0.62 * R, zb, 0.17 * k, 0.08 * k, mirror=True)]
    elif st == "tall":
        out += [spike("pil_r", 0.88 * R, zf, 0.4 * k, 0.11 * k, mirror=True),
                spike("pil_b_r", 0.88 * R, zb, 0.34 * k, 0.11 * k, mirror=True),
                spike("mid_f", 0, zf, 0.27 * k, 0.1 * k),
                spike("mid_b", 0, zb, 0.24 * k, 0.1 * k),
                spike("mid_s_r", R - t / 2, cz, 0.24 * k, 0.1 * k, mirror=True)]
    elif st == "broken":
        out += [c.box("snap", "head", (0, ytop + 0.04 * k, zf), (0.1 * k, 0.08 * k, 0.1 * k), rotate=[0, 0, 10]),
                spike("sp_r", 0.62 * R, zf, 0.25 * k, 0.09 * k, tilt=-12),
                spike("sp_l", -0.62 * R, zf, 0.22 * k, 0.09 * k, tilt=10),
                spike("sp_bl", -0.4 * R, zb, 0.15 * k, 0.08 * k, tilt=14)]
    if c.p["gem"]:
        out.append(c.box("gem", "head", (0, yc, cz - R - 0.012 * k), (0.1 * k, 0.1 * k, 0.03 * k), role="glow",
                         rotate=[0, 0, 45]))
    return out


@_preset("horns", "Pair of horns, antlers or a ram's curl growing from the head.",
         {"style": _enum("curved", ("curved", "straight", "ram", "antlers", "demon", "bull")),
          "length": _float(1.0, 0.5, 2.0)},
         main=("bone", "skin", "accent"))
def _horns(c: _Ctx) -> List[dict]:
    b = c.b
    k = c.s * c.hu ** 0.75
    ln = c.p["length"] * k
    hw = b.he / 2.0
    top, cz = b.head_top, b.hcz
    st = c.p["style"]
    if st == "curved":
        return c.horn("h", "head", (0.6 * hw, top - 0.03, cz),
                      [(0.65, 0.76, -0.05), (0.45, 0.9, -0.05), (0.0, 1, 0), (-0.5, 0.87, 0)],
                      [0.19 * ln, 0.18 * ln, 0.17 * ln, 0.14 * ln], 0.15 * k, [0.85, 0.85, 0.8, 0.2])
    if st == "straight":
        return c.horn("h", "head", (0.45 * hw, top - 0.03, cz), [(0.3, 0.95, -0.05), (0.25, 0.97, -0.05)],
                      [0.32 * ln, 0.32 * ln], 0.16 * k, [0.8, 0.12])
    if st == "ram":
        return c.horn("h", "head", (0.8 * hw, top - 0.2 * b.he, cz + 0.05),
                      [(0.8, 0.5, 0.3), (0.9, -0.1, 0.25), (0.55, -0.75, 0.0), (-0.1, -0.9, -0.2), (-0.7, -0.5, -0.3)],
                      [0.17 * ln, 0.17 * ln, 0.17 * ln, 0.15 * ln, 0.13 * ln], 0.2 * k, [0.88, 0.88, 0.86, 0.8, 0.25])
    if st == "demon":
        return c.horn("h", "head", (0.5 * hw, top - 0.06, cz - 0.2 * hw),
                      [(0.35, 0.75, 0.25), (0.25, 0.55, 0.65), (0.15, 0.25, 0.9), (0.05, -0.25, 0.95)],
                      [0.22 * ln, 0.22 * ln, 0.2 * ln, 0.16 * ln], 0.16 * k, [0.85, 0.8, 0.7, 0.2])
    if st == "bull":
        return c.horn("h", "head", (0.95 * hw, top - 0.25 * b.he, cz),
                      [(1, 0.05, -0.05), (0.9, 0.25, -0.15), (0.5, 0.8, -0.25), (0.25, 0.97, -0.1)],
                      [0.17 * ln, 0.16 * ln, 0.16 * ln, 0.15 * ln], 0.2 * k, [0.88, 0.86, 0.8, 0.25])
    # antlers: a main beam with forward tines
    p0 = (0.4 * hw, top - 0.02, cz + 0.05)
    dirs = [(0.3, 1, 0.1), (0.3, 1, 0.1), (0.5, 0.9, 0.15), (0.2, 1, 0.1)]
    lens = [0.18 * ln, 0.18 * ln, 0.16 * ln, 0.16 * ln]
    pts = c.walk(p0, dirs, lens)
    out = c.horn("beam", "head", p0, dirs, lens, 0.1 * k, [0.88, 0.88, 0.85, 0.3])
    for i, (node, dv) in enumerate(((pts[1], (0.6, 0.8, -0.5)), (pts[2], (0.5, 0.9, -0.4)), (pts[3], (0.7, 0.7, -0.2)))):
        out += c.path("tine%d_" % i, "head", c.walk(node, [dv], [0.17 * ln]), [0.055 * k, 0.012 * k], suffix="_r", mirror=True)
    return out


@_preset("helm", "Helmet over the head: bucket, nasal visor, horned, plumed or great helm.",
         {"style": _enum("bucket", ("bucket", "visor", "horned", "plumed", "great"))},
         main=("plate_steel", "primary", "accent"), trim=("cloth", "secondary", "accent"))
def _helm(c: _Ctx) -> List[dict]:
    b, s, hu = c.b, c.s, c.hu
    st = c.p["style"]
    hw, he = b.he / 2.0, b.he
    top, bot, cz = b.head_top, b.head_bot, b.hcz
    m = (0.065 if st == "great" else 0.035) * hu * s
    X = hw + m
    out: List[dict] = []
    y_hi, y_lo = top - 0.42 * he, top - 0.64 * he  # eye slit band
    if st in ("bucket", "horned", "great"):
        ytop = top + (0.09 if st == "great" else 0.05) * hu
        ybot = bot - (0.06 if st == "great" else 0.02) * hu
        wall = 0.05 * hu
        ymid = (y_hi + y_lo) / 2
        out.append(c.box("top", "head", (0, (y_hi + ytop) / 2, cz), (2 * X, ytop - y_hi, 2 * X),
                         taper=None if st == "great" else (0.9, 0.9)))
        out.append(c.box("jaw", "head", (0, (ybot + y_lo) / 2, cz), (2 * X, y_lo - ybot, 2 * X)))
        out.append(c.box("back", "head", (0, ymid, cz + X - wall / 2), (2 * X, y_hi - y_lo, wall)))
        out.append(c.box("side_r", "head", (X - wall / 2, ymid, cz), (wall, y_hi - y_lo, 2 * X - 2 * wall), mirror=True))
        if st == "great":
            out.append(c.box("bar", "head", (0, ymid, cz - X + 0.02), (0.07 * hu, y_hi - y_lo, 0.04 * hu), role="trim"))
            out.append(c.box("crest", "head", (0, ytop + 0.02 * hu, cz), (0.07 * hu, 0.05 * hu, 1.8 * X), role="trim"))
            out.append(c.box("rim_r", "head", (X + 0.005, (ytop + ybot) / 2 - 0.02, cz), (0.02 * hu, 0.04 * hu, 2 * X), role="trim", mirror=True))
        if st == "horned":
            k = s * hu ** 0.75
            out += c.horn("horn", "head", (X - 0.01, y_hi + 0.05, cz), [(1.0, 0.35, 0.05), (0.7, 0.7, 0.0)],
                          [0.2 * k, 0.2 * k], 0.12 * k, [0.8, 0.2], role="trim")
        return out
    # nasal helm: dome + cheek/back plates, the face stays visible
    ytop = top + 0.05 * hu
    y_brow = top - 0.28 * he
    wall = 0.045 * hu
    out.append(c.box("dome", "head", (0, (ytop + y_brow) / 2, cz), (2 * X, ytop - y_brow, 2 * X), taper=(0.92, 0.92)))
    out.append(c.box("back", "head", (0, (y_brow + bot) / 2, cz + X - wall / 2), (2 * X, y_brow - bot + 0.02, wall)))
    out.append(c.box("cheek_r", "head", (X - wall / 2, (y_brow + bot + 0.1 * he) / 2, cz + 0.05 * he),
                     (wall, y_brow - bot - 0.1 * he, 1.7 * hw), mirror=True))
    out.append(c.box("nasal", "head", (0, (y_brow + top - 0.62 * he) / 2, cz - X + 0.015),
                     (0.06 * hu, y_brow - (top - 0.62 * he), 0.04 * hu)))
    out.append(c.box("brim", "head", (0, y_brow + 0.02, cz - X + 0.015), (2 * X, 0.05 * hu, 0.04 * hu), role="trim"))
    if st == "plumed":
        u = hu * s
        pts = [(0, ytop - 0.01, cz - 0.7 * X), (0, ytop + 0.14 * u, cz - 0.2 * X), (0, ytop + 0.18 * u, cz + 0.4 * X),
               (0, ytop + 0.06 * u, cz + X + 0.12 * u), (0, ytop - 0.2 * u, cz + X + 0.2 * u)]
        out += c.path("plume", "head", pts, [0.1 * hu, 0.14 * hu, 0.15 * hu, 0.13 * hu, 0.06 * hu], [0.1 * hu] * 5, role="trim")
    return out


@_preset("hood", "Cloth hood around the head with an optional shoulder cowl.",
         {"style": _enum("soft", ("soft", "pointed")), "cowl": _bool(True)},
         main=("cloth", "primary", "accent"))
def _hood(c: _Ctx) -> List[dict]:
    b, s, hu = c.b, c.s, c.hu
    hw, he = b.he / 2.0, b.he
    top, bot, cz = b.head_top, b.head_bot, b.hcz
    m = 0.045 * hu * s
    X = hw + m
    yb = bot - 0.05 * hu
    out: List[dict] = [
        c.box("top", "head", (0, top + 0.035 * hu, cz + 0.03 * hu), (2 * X, 0.07 * hu, 2 * X + 0.06 * hu)),
        c.box("back", "head", (0, (yb + top) / 2, cz + X + 0.02 * hu), (2 * X, top - yb, 0.07 * hu)),
        c.box("side_r", "head", (hw + m / 2, (yb + top) / 2, cz + 0.07 * hu), (m, top - yb, 1.84 * hw + m + 0.06 * hu), mirror=True),
        c.box("brow", "head", (0, top - 0.12 * he, cz - hw - 0.03 * hu), (2 * X, 0.24 * he, 0.075 * hu)),
    ]
    if c.p["style"] == "pointed":
        u = s * hu
        pts = [(0, top + 0.03 * hu, cz + hw * 0.55), (0, top + 0.24 * u, cz + hw + 0.1 * u), (0, top + 0.2 * u, cz + hw + 0.46 * u)]
        out += c.path("peak", "head", pts, [0.2 * u, 0.12 * u, 0.02 * u], [0.2 * u, 0.12 * u, 0.02 * u])
    if c.p["cowl"]:
        ct = b.chest_top
        wcow = b.cw + 0.07 * s
        dcow = max(b.cd, b.arm_t / 2.0) + 0.06 * s
        out.append(c.box("cowl", "chest", (0, ct - 0.115 * s, b.cz), (2 * wcow, 0.29 * s, 2 * dcow), taper=(0.78, 0.8)))
        out.append(c.box("drape", "chest", (0, ct - 0.34 * s, b.cz + b.cd + 0.05 * s), (1.5 * b.cw, 0.5 * s, 0.05)))
    return out


@_preset("mane", "Spiky ridge or shaggy mane over the head and the upper back.",
         {"style": _enum("spiky", ("spiky", "lion"))},
         main=("fur", "hair", "accent"))
def _mane(c: _Ctx) -> List[dict]:
    b, s, hu = c.b, c.s, c.hu
    k = s * hu ** 0.8
    hw = b.he / 2.0
    top, bot, cz = b.head_top, b.head_bot, b.hcz
    out: List[dict] = []
    if c.p["style"] == "spiky":
        for i, (zz, ln, d) in enumerate(((-0.25, 0.26, (0, 1, 0.5)), (0.1, 0.3, (0, 1, 0.8)), (0.45, 0.28, (0, 0.8, 1.0)))):
            pts = c.walk((0, top - 0.02, cz + zz * hw * 2), [d], [ln * k])
            out += c.path("hs%d_" % i, "head", pts, [0.15 * k, 0.015 * k])
        back = b.back_z(b.chest_bot, b.chest_top, -0.1, 0.1, arms=False)
        step = min(0.17, (b.chest_top - b.chest_bot - 0.08) / 3.5)
        for i in range(4):
            y = b.chest_top - 0.06 - i * step
            pts = c.walk((0, y, back - 0.02), [(0, 0.5, 1.0)], [(0.34 - 0.04 * i) * k])
            out += c.path("bs%d_" % i, "chest", pts, [(0.18 - 0.016 * i) * k, 0.015 * k])
        return out
    t = 0.15 * k  # lion: a thick ring of fur around the head that falls onto the shoulders
    X = hw + t
    ytop = top + 0.03 * hu
    ybot = bot - 0.12 * hu
    out.append(c.box("top", "head", (0, ytop + 0.02 * hu, cz + hw * 0.5), (2 * X, 0.07 * hu, hw + X + 0.0), taper=(0.9, 1.0)))
    out.append(c.box("back", "head", (0, (ytop + ybot) / 2, cz + hw + t / 2), (2 * X, ytop - ybot, t), taper=(1.15, 1.0)))
    out.append(c.box("side_r", "head", (hw + t / 2, (ytop + ybot) / 2, cz + 0.3 * hw), (t, ytop - ybot, 1.5 * hw + 0.5 * t), mirror=True))
    out.append(c.box("fall", "chest", (0, b.chest_top - 0.13 * s, b.cz + b.cd * 0.6),
                     (2 * (b.cw + 0.09 * s), 0.3 * s, 2 * max(b.cd, b.arm_t / 2) + 0.18 * s), taper=(0.85, 0.85)))
    # fur spikes radiating from the ring: upper corners, cheeks, lower corners, centre of the back
    for i, (px, py, pz, d, ln) in enumerate(((0.8 * X, ytop, cz + hw * 0.2, (0.6, 1.0, 0.1), 0.2),
                                             (X, (ytop + ybot) / 2, cz + hw * 0.5, (1.0, 0.1, 0.2), 0.2),
                                             (0.85 * X, ybot + 0.03, cz + hw * 0.4, (0.5, -1.0, 0.35), 0.24))):
        pts = c.walk((px, py, pz), [d], [ln * k])
        out += c.path("fur%d_" % i, "head", pts, [0.15 * k, 0.025 * k], suffix="_r", mirror=True)
    out += c.path("furc_", "head", c.walk((0, ybot + 0.03, cz + hw + t * 0.6), [(0, -1, 0.5)], [0.28 * k]), [0.24 * k, 0.03 * k])
    out += c.path("furt_", "head", c.walk((0, ytop, cz + hw + t * 0.5), [(0, 0.8, 0.6)], [0.2 * k]), [0.2 * k, 0.03 * k])
    return out


@_preset("tusks", "Tusks rising from or hanging below the mouth.",
         {"style": _enum("boar", ("boar", "curved", "walrus"))},
         main=("bone", "skin", "accent"))
def _tusks(c: _Ctx) -> List[dict]:
    b = c.b
    k = c.s * c.hu ** 0.8
    front = b.hcz - b.he / 2.0
    y = b.head_bot + 0.14 * b.he
    x = 0.3 * b.he
    st = c.p["style"]
    if st == "boar":
        return c.horn("t", "head", (x, y, front + 0.02), [(0.25, 0.8, -0.6), (0.1, 1, -0.2)], [0.17 * k, 0.16 * k], 0.12 * k, [0.8, 0.2])
    if st == "curved":
        return c.horn("t", "head", (x, y, front + 0.02), [(0.1, 0.3, -1), (0.1, 0.8, -0.6), (-0.1, 1, 0.0)],
                      [0.15 * k, 0.15 * k, 0.14 * k], 0.13 * k, [0.85, 0.8, 0.2])
    return c.horn("t", "head", (x * 1.1, y + 0.07 * b.he, front + 0.03), [(0.05, -1, -0.1), (0.0, -1, -0.05)],
                  [0.3 * k, 0.28 * k], 0.13 * k, [0.8, 0.25])


@_preset("fangs", "Fangs or mandibles at the mouth.",
         {"style": _enum("fangs", ("fangs", "mandibles", "saber"))},
         main=("bone", "skin", "accent"))
def _fangs(c: _Ctx) -> List[dict]:
    b = c.b
    k = c.s * c.hu ** 0.8
    he = b.he
    front = b.hcz - he / 2.0
    y = b.head_bot + 0.17 * he
    st = c.p["style"]
    if st == "fangs":
        out: List[dict] = []
        for i, (x, ln) in enumerate(((0.2 * he, 0.2), (0.34 * he, 0.14))):
            out += c.path("f%d_" % i, "head", [(x, y + 0.03, front + 0.01), (x, y - ln * k, front - 0.02 * k)],
                          [0.075 * k, 0.012 * k], suffix="_r", mirror=True)
        return out
    if st == "saber":
        return c.horn("f", "head", (0.3 * he, y + 0.05 * he, front + 0.01), [(0.05, -1, -0.15), (0, -1, -0.1)],
                      [0.26 * k, 0.24 * k], 0.09 * k, [0.8, 0.2])
    return c.horn("m", "head", (0.32 * he, y - 0.04 * he, front + 0.02),
                  [(0.5, -0.3, -0.8), (0.0, -0.4, -0.9), (-0.7, -0.2, -0.7)],
                  [0.19 * k, 0.2 * k, 0.17 * k], 0.12 * k, [0.8, 0.75, 0.2])


@_preset("jaw", "Heavy underbite jaw with tusks for brutes, or a gaping maw.",
         {"style": _enum("underbite", ("underbite", "maw"))},
         main=("skin", "skin", "accent"))
def _jaw(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    k = s * c.hu
    he = b.he
    front = b.hcz - he / 2.0
    bot = b.head_bot
    if c.p["style"] == "underbite":
        h = 0.26 * he * s
        zf = front - 0.1 * he * s
        out = [c.box("jaw", "head", (0, bot + h / 2 - 0.06 * he, zf + 0.14 * he), (0.82 * he, h, 0.46 * he * s)),
               c.box("lip", "head", (0, bot + h - 0.06 * he, zf + 0.03 * he), (0.7 * he, 0.05 * k, 0.06 * k), role="bone")]
        out += c.horn("tusk", "head", (0.3 * he, bot + h - 0.07 * he, zf + 0.05 * he), [(0.2, 1, -0.2), (0.25, 1, -0.1)],
                      [0.3 * he * s, 0.25 * he * s], 0.22 * he * s, [0.8, 0.2], role="bone")
        return out
    h = 0.3 * he * s
    ytop = bot - 0.1 * he
    zc = front + 0.2 * he * s
    out = [c.box("jaw", "head", (0, ytop - h / 2 + 0.0, zc), (0.86 * he, h, 0.62 * he * s)),
           c.box("mouth", "head", (0, ytop + 0.005, zc), (0.7 * he, 0.02, 0.5 * he * s), role="dark")]
    for i in range(4):
        x = (-0.27 + 0.18 * i) * he
        out += c.path("lt%d_" % i, "head", [(x, ytop, front + 0.02 * he), (x, ytop + 0.14 * k, front)], [0.07 * k, 0.012 * k], role="bone")
    return out


@_preset("halo", "Floating ring, orbs or sunburst above the head.",
         {"style": _enum("ring", ("ring", "orbs", "sunburst"))},
         main=("plate_gold", "eyes", "eyes"))
def _halo(c: _Ctx) -> List[dict]:
    b, s, hu = c.b, c.s, c.hu
    st = c.p["style"]
    R = (0.5 * b.he + 0.1) * s
    y = b.head_top + 0.2 * hu * s
    cz = b.hcz
    out: List[dict] = []
    if st == "orbs":
        n = 6
        for i in range(n):
            a = 2 * math.pi * i / n + math.pi / 6
            e = 0.11 * s
            out.append(c.box("o%d" % i, "head", (R * math.cos(a), y + 0.03 * s * math.cos(2 * a), cz + R * math.sin(a)), (e, e, e), rotate=[0, 45, 0]))
        return out
    n = 8
    ln = 2 * R * math.tan(math.pi / n) * 1.08
    for i in range(n):
        a = 2 * math.pi * i / n + math.pi / n
        out.append(c.box("r%d" % i, "head", (R * math.cos(a), y, cz + R * math.sin(a)), (ln, 0.05 * s, 0.08 * s),
                         rotate=[0, -(math.degrees(a) + 90.0), 0]))
    if st == "sunburst":
        for i in range(n):
            a = 2 * math.pi * i / n + math.pi / n
            pts = [(R * math.cos(a), y, cz + R * math.sin(a)), ((R + 0.22 * s) * math.cos(a), y, cz + (R + 0.22 * s) * math.sin(a))]
            out += c.path("ray%d_" % i, "head", pts, [0.07 * s, 0.01 * s], [0.05 * s, 0.01 * s], role="trim")
    return out


# --------------------------------------------------------------------------- torso / arm presets
@_preset("pauldrons", "Shoulder armor: round, spiked, layered plates, skulls or one huge asymmetric pauldron.",
         {"style": _enum("round", ("round", "spiked", "layered", "skull", "asymmetric")),
          "side": _enum("right", ("right", "left"))},
         main=("plate_steel", "primary", "accent"))
def _pauldrons(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    pw0 = max(0.3, b.arm_t * 1.3 + 0.1)

    def pad(sx: float, scl: float, kind: str, mirror: bool) -> List[dict]:
        u = s * scl
        pw = pw0 * u
        sfx = "_r" if sx > 0 else "_l"
        bone = "arm_r" if sx > 0 else "arm_l"
        cx0 = b.arm_cx + 0.06 * u
        cx = sx * (cx0 + _clamp(b.he / 2.0 + 0.005 - (cx0 - pw / 2.0), 0.0, 0.12 * u))  # keep the dome off a wide head
        y0 = b.arm_top - 0.02
        z0 = b.cz
        res: List[dict] = []

        def bx(lid, cen, size, **kw):
            return c.box(lid + sfx, bone, cen, size, mirror=mirror, **kw)

        if kind == "layered":
            for i in range(3):
                wi = pw * (0.8 + 0.1 * i)
                res.append(bx("pl%d" % i, (cx + sx * 0.02 * u * i, y0 + 0.07 * u - i * 0.07 * u, z0), (wi, 0.085 * u, wi * 0.92),
                              rotate=[0, 0, -sx * (6 + 4 * i)]))
            return res
        res.append(bx("cap0", (cx, y0 + 0.05 * u, z0), (pw, 0.17 * u, pw * 0.92), taper=(0.8, 0.8)))
        res.append(bx("cap1", (cx, y0 + 0.16 * u, z0), (pw * 0.72, 0.09 * u, pw * 0.66), taper=(0.62, 0.62)))
        res.append(bx("flange", (cx + sx * pw * 0.42, y0 - 0.08 * u, z0), (0.05 * u, 0.2 * u, pw * 0.78)))
        if kind == "spiked":
            for i, (dv, ln) in enumerate((((0, 1, 0), 0.32), ((sx * 0.7, 0.7, -0.3), 0.28), ((sx * 0.7, 0.7, 0.3), 0.28))):
                pts = c.walk((cx + sx * dv[0] * 0.06, y0 + 0.2 * u, z0 + dv[2] * 0.1), [dv], [ln * u])
                res += c.path("sp%d_" % i, bone, pts, [0.12 * u, 0.012 * u], suffix=sfx, mirror=mirror)
        elif kind == "skull":
            zf = z0 - pw * 0.4 - 0.04
            ks = 0.19 * u
            yk = y0 + 0.2 * u
            res.append(bx("skull", (cx, yk, zf), (ks, ks, ks * 0.85), role="bone"))
            res.append(bx("skjaw", (cx, yk - ks * 0.62, zf + 0.01), (ks * 0.62, ks * 0.3, ks * 0.6), role="bone"))
            res.append(c.box("eye_a" + sfx, bone, (cx + ks * 0.24, yk + 0.01, zf - ks * 0.43), (ks * 0.24, ks * 0.22, 0.012), role="dark", mirror=mirror))
            res.append(c.box("eye_b" + sfx, bone, (cx - ks * 0.24, yk + 0.01, zf - ks * 0.43), (ks * 0.24, ks * 0.22, 0.012), role="dark", mirror=mirror))
        elif kind == "huge":
            res.append(bx("rim", (cx + sx * pw * 0.45, y0 - 0.04 * u, z0), (0.05 * u, 0.2 * u, pw * 0.8), role="trim"))
            for i, (dv, ln) in enumerate((((0, 1, 0), 0.3), ((sx * 0.7, 0.7, -0.3), 0.26), ((sx * 0.7, 0.7, 0.3), 0.26))):
                pts = c.walk((cx + sx * dv[0] * 0.06, y0 + 0.2 * u, z0 + dv[2] * 0.1), [dv], [ln * u])
                res += c.path("sp%d_" % i, bone, pts, [0.12 * u, 0.012 * u], suffix=sfx, mirror=mirror)
        return res

    if st == "asymmetric":
        big_sx = 1.0 if c.p["side"] == "right" else -1.0
        out = pad(big_sx, 1.6, "huge", False) + pad(big_sx, 1.5, "layered", False)
        return out + pad(-big_sx, 0.8, "round", False)
    return pad(1.0, 1.0, st, True)


@_preset("cape", "Cloak hanging behind the body in rigid segments (short, long or tattered).",
         {"style": _enum("long", ("short", "long", "tattered"))},
         main=("cloth", "secondary", "accent"))
def _cape(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    th = 0.05 * math.sqrt(s)
    gap = 0.035
    ytop = b.chest_top - 0.02
    rows = {"short": [b.chest_bot, b.abd_bot + 0.02], "long": [b.chest_bot, b.hip, 0.3],
            "tattered": [b.chest_bot, b.hip, 0.5]}[st]
    ys = [ytop] + rows
    ys[-1] = max(0.08, ytop - (ytop - ys[-1]) * s)  # `scale` lengthens the cloak from the neck down
    bones = ["chest", "torso", "root"]
    flare = 0.2 * s

    def half(y: float) -> float:
        return b.cw + 0.03 + flare * (ytop - y)

    zs = []
    for i, y in enumerate(ys):
        lo = ys[i + 1] if i + 1 < len(ys) else y
        hi = ys[i - 1] if i > 0 else y
        hw = half(min(lo, y))
        zs.append(b.back_z(lo, hi, -hw, hw) + gap + th / 2.0 + 0.1 * s * (ytop - y) ** 1.3)
    pts = [(0.0, y, z) for y, z in zip(ys, zs)]
    widths = [2 * half(y) for y in ys]
    n_main = len(pts) - 2 if st == "tattered" else len(pts) - 1
    out: List[dict] = []
    for i in range(n_main):
        out += c.path("seg%d_" % i, bones[i], pts[i:i + 2], widths[i:i + 2], [th, th], overlap=0.03)
    if st == "tattered":
        base, wl = pts[n_main], widths[n_main]
        for i, (dx, ln, ang) in enumerate(((-0.33, 0.55, 5), (0.0, 0.72, -3), (0.33, 0.48, -6))):
            y1 = base[1] - ln * s
            tip = (dx * wl + math.sin(math.radians(ang)) * ln * s * 0.6, y1, base[2] + 0.12 * ln * s)
            out += c.path("strip%d_" % i, "root", [(dx * wl, base[1], base[2]), tip], [wl * 0.36, wl * 0.2], [th, th], overlap=0)
    return out


@_preset("skirt", "Tabard, robe or kilt hanging from the waist on the thigh bones, so the legs stay free.",
         {"style": _enum("robe", ("tabard", "robe", "kilt")), "trim": _bool(True)},
         main=("cloth", "secondary", "accent"))
def _skirt(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    ytop = b.abd_bot + 0.04
    hem = max(0.03, ytop - (ytop - {"kilt": 0.5, "tabard": 0.26, "robe": 0.06}[st]) * s)
    ys = [ytop] + ([b.knee] if hem < b.knee - 0.05 else []) + [hem]
    gap, th = 0.03, 0.04
    flare = {"kilt": 0.0, "tabard": 0.08, "robe": 0.2}[st] * s
    x0 = max(b.leg_cx + b.leg_t / 2.0, b.pw) + 0.045
    f0 = b.front_z(hem, ytop, -x0, x0, arms=False) - gap - th / 2.0
    b0 = b.back_z(hem, ytop, -x0, x0, arms=False) + gap + th / 2.0
    xs = [x0 + flare * (ytop - y) for y in ys]
    fs = [f0 - 0.5 * flare * (ytop - y) for y in ys]
    bs = [b0 + 0.5 * flare * (ytop - y) for y in ys]
    bones = ["thigh_r" if ys[i + 1] >= b.knee - 1e-6 else "shin_r" for i in range(len(ys) - 1)]  # a segment below the knee follows the shin
    inner = 0.005
    out: List[dict] = []

    def panel(prefix: str, centers, widths, depths, zs):
        pts = [(centers[i], ys[i], zs[i]) for i in range(len(ys))]
        for i in range(len(ys) - 1):
            out.extend(c.path("%s%d_" % (prefix, i), bones[i], pts[i:i + 2], widths[i:i + 2], depths[i:i + 2], suffix="_r", mirror=True, overlap=0.03))
        if c.p["trim"]:
            q0, q1 = pts[-2], pts[-1]
            tt = 0.06 / max(_len(_sub(q1, q0)), 1e-6)
            out.extend(c.path("%st_" % prefix, bones[-1], [_lerp(q1, q0, tt), q1], [widths[-1] + 0.014] * 2, [depths[-1] + 0.014] * 2,
                              role="trim", suffix="_r", mirror=True, overlap=0))

    reach = [x * (0.62 if st == "tabard" else 1.0) for x in xs]
    wid = [r - inner for r in reach]
    cen = [inner + w / 2.0 for w in wid]
    panel("fr", cen, wid, [th] * len(ys), fs)
    panel("bk", cen, wid, [th] * len(ys), bs)
    if st != "tabard":
        panel("sd", [x + th / 2.0 for x in xs], [th] * len(ys), [bs[i] - fs[i] for i in range(len(ys))], [(fs[i] + bs[i]) / 2.0 for i in range(len(ys))])
    out.append(c.box("band", "root", (0, (ytop + b.hip) / 2 + 0.0275, b.cz), (2 * (x0 - 0.01), ytop - b.hip + 0.045, 2 * (max(b.ad, b.leg_t / 2.0) + 0.035)), role="trim"))
    return out


@_preset("tail", "Tail trailing behind the pelvis, tilting down and back.",
         {"style": _enum("lizard", ("lizard", "spiked", "whip", "club")), "length": _float(1.0, 0.5, 1.6)},
         main=("scales", "primary", "accent"))
def _tail(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    ln = c.p["length"] * s
    z0 = b.cz + max(b.ad, b.leg_t / 2.0) - 0.04
    y0 = b.hip + 0.06
    w0 = 0.6 * 2 * b.pw * s
    pitches = {"lizard": [-6, -16, -14, -4, 12], "spiked": [-10, -14, -8, 4], "whip": [-8, -16, -24, -30, -20, 0],
               "club": [-8, -14, -18, -16]}[st]
    seg = {"lizard": 0.3, "spiked": 0.3, "whip": 0.22, "club": 0.3}[st] * ln
    taper = {"lizard": 0.8, "spiked": 0.8, "whip": 0.78, "club": 0.92}[st]
    pts = [(0.0, y0, z0)]
    for p in pitches:
        a = math.radians(p)
        pts.append(_add(pts[-1], (0.0, math.sin(a) * seg, math.cos(a) * seg)))
    base = w0 * (0.6 if st == "whip" else 1.0)
    widths = [base * taper ** i for i in range(len(pts))]
    if st == "spiked":
        widths[-1] *= 0.3
    out = c.path("t", "root", pts, widths, [wi * 0.85 for wi in widths])
    if st == "spiked":
        for i in range(len(pts) - 1):
            d = _norm(_sub(pts[i + 1], pts[i]))
            mid = _lerp(pts[i], pts[i + 1], 0.5)
            up = _norm((0.0, d[2], -d[1]))
            sp = c.walk(_add(mid, _mul(up, widths[i] * 0.3)), [_norm(_add(up, (0.0, 0.0, 0.9)))], [(0.19 - 0.02 * i) * s])
            out += c.path("sp%d_" % i, "root", sp, [widths[i] * 0.5, 0.012 * s])
    elif st == "club":
        d = _norm(_sub(pts[-1], pts[-2]))
        cen = _add(pts[-1], _mul(d, 0.17 * s))
        cs = 0.38 * s
        out.append(c.box("club", "root", cen, (cs, cs, cs), role="steel"))
        for i, dv in enumerate(((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1))):
            out += c.path("cs%d_" % i, "root", c.walk(_add(cen, _mul(dv, cs * 0.4)), [dv], [0.19 * s]), [0.12 * s, 0.015 * s], role="steel")
    return out


# --------------------------------------------------------------------------- wings, orbs, extra limbs
@_preset("wings", "Pair of wings on the upper back; `spread` (10-80 degrees) tilts the main strut away from vertical.",
         {"style": _enum("bat", ("bat", "feathered", "angel", "skeletal", "flame")), "spread": _float(45.0, 10.0, 80.0)},
         main=("leather", "secondary", "accent"))
def _wings(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    sp = float(c.p["spread"])
    back = b.back_z(b.chest_bot, b.chest_top, -0.15, 0.15, arms=False)
    root: Vec = (0.3 * b.cw + 0.03, b.chest_top - 0.2, back + 0.04)

    def at(p: Vec, a_deg: float, ln: float) -> Vec:
        a = math.radians(a_deg)
        return _add(p, _mul(_norm((math.sin(a), math.cos(a), 0.22)), ln))  # angle from vertical, swept back

    elbow = at(root, sp, 0.6 * s)
    wrist = at(elbow, sp + 25.0, 0.85 * s)
    R = "_r"
    out: List[dict] = []
    if st == "flame":
        for i, (a, ln) in enumerate(((-5, 0.8), (20, 1.0), (45, 1.1), (70, 1.05), (95, 0.9), (120, 0.7))):
            p1 = at(root, sp + a, ln * 0.55 * s)
            p2 = at(p1, sp + a + 14.0, ln * 0.45 * s)
            tongue = c.path("fl%d_" % i, "chest", [root, p1, p2], [0.17 * s, 0.11 * s, 0.015 * s], [0.04 * s] * 3, suffix=R, mirror=True)
            if i % 2:
                for bx in tongue:
                    bx["color"] = bx["accent"] = c.style("trim")[1]
            out += tongue
        return out
    arm_role = "bone" if st in ("bat", "skeletal") else "trim"
    out += c.path("arm_", "chest", [root, elbow, wrist], [0.11 * s, 0.09 * s, 0.07 * s], [0.07 * s] * 3, role=arm_role, suffix=R, mirror=True, overlap=0.1)
    base_a = sp + 25.0
    if st in ("bat", "skeletal"):
        fingers = [(base_a + 40, 1.0), (base_a + 75, 1.1), (base_a + 108, 1.0), (base_a + 138, 0.8)]
        if st == "skeletal":
            fingers = [(base_a + 25, 0.95), (base_a + 55, 1.1), (base_a + 85, 1.1), (base_a + 115, 1.0), (base_a + 145, 0.8)]
        for i, (a, ln) in enumerate(fingers):
            out += c.path("fg%d_" % i, "chest", [wrist, at(wrist, a, ln * s)], [0.065 * s, 0.025 * s], [0.045 * s] * 2, role="bone", suffix=R, mirror=True, overlap=0)
        if st == "bat":
            edges = [(base_a, 0.85 * s)] + [(a, ln * s) for a, ln in fingers]
            for i in range(len(edges) - 1):
                (a0, l0), (a1, l1) = edges[i], edges[i + 1]
                lm = min(l0, l1) * 0.93
                chord = 2.0 * lm * math.sin(math.radians(abs(a1 - a0)) / 2.0)
                out += c.path("m%d_" % i, "chest", [at(wrist, (a0 + a1) / 2.0, lm), wrist], [max(chord, 0.05), 0.02 * s], [0.02 * s] * 2, suffix=R, mirror=True, overlap=0)
        else:
            out += c.path("claw_", "chest", [wrist, at(wrist, sp + 5.0, 0.22 * s)], [0.08 * s, 0.01 * s], role="bone", suffix=R, mirror=True, overlap=0)
        return out
    rows = []  # (name, origin, angles, length, width)
    if st == "angel":
        rows = [("cov", elbow, [sp + 50 + 28 * j for j in range(4)], 0.45 * s, 0.19 * s),
                ("sec", elbow, [sp + 40 + 24 * j for j in range(4)], 0.85 * s, 0.19 * s),
                ("pri", wrist, [base_a + 8 + 20 * j for j in range(6)], 1.15 * s, 0.19 * s)]
    else:
        rows = [("sec", elbow, [sp + 45 + 30 * j for j in range(4)], 0.75 * s, 0.16 * s),
                ("pri", wrist, [base_a + 8 + 22 * j for j in range(6)], 1.1 * s, 0.16 * s)]
    for name, origin, angles, ln, w in rows:
        for j, a in enumerate(angles):
            fe = c.path("%s%d_" % (name, j), "chest", [origin, at(origin, a, ln * (1.0 - 0.04 * j))], [w, w * 0.45], [0.035 * s] * 2, suffix=R, mirror=True, overlap=0)
            if st == "angel":
                for bx in fe:
                    bx["material"], bx["color"] = "cloth", "#efe9dc"
            out += fe
    return out


@_preset("orbs", "Floating orbs, shards or blades orbiting the torso (static pose).",
         {"style": _enum("orbs", ("orbs", "shards", "blades")), "count": _int(4, 2, 8), "radius": _float(1.0, 0.6, 2.0)},
         main=("plate_gold", "eyes", "eyes"))
def _orbs(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    n = c.p["count"]
    R = (b.cw + 0.5) * c.p["radius"] * s
    y0 = (b.chest_bot + b.chest_top) / 2.0 + 0.1
    out: List[dict] = []
    for i in range(n):
        a = math.pi / 2 + (i + 0.5) * 2 * math.pi / n  # symmetric about x = 0 for every count
        x, z = R * math.cos(a), b.cz + R * math.sin(a)
        y = y0 + 0.25 * s * math.cos(2 * a)
        if st == "orbs":
            e = 0.15 * s
            out.append(c.box("o%d" % i, "chest", (x, y, z), (e, e, e), rotate=[45, 0, 0] if min(i, n - 1 - i) % 2 else [0, 45, 0]))
        elif st == "shards":
            e = 0.13 * s
            out.append(c.box("u%d" % i, "chest", (x, y + 0.1 * s, z), (e, 0.2 * s, e), taper=(0.1, 0.1)))
            out.append(c.box("d%d" % i, "chest", (x, y - 0.07 * s, z), (0.04 * s, 0.14 * s, 0.04 * s), taper=(3.0, 3.0)))
        else:
            out.append(c.box("b%d" % i, "chest", (x, y, z), (0.08 * s, 0.7 * s, 0.03 * s), taper=(0.1, 1.0),
                             rotate=[0, -math.degrees(a) - 90.0, 180.0]))
    return out


@_preset("extra_arms", "Static pair(s) of extra arms on the chest (rigid, no animation).",
         {"pose": _enum("forward", ("forward", "splayed", "raised")), "count": _int(1, 1, 2)},
         main=("skin", "skin", "accent"))
def _extra_arms(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    pose = c.p["pose"]
    t = 0.6 * b.arm_t * s
    front = b.cz - b.cd
    ys = [b.chest_top - 0.27, b.chest_bot - 0.12]
    poses = {"forward": [[(0.65, -0.15, -0.75), (0.1, -0.1, -1.0)], [(0.9, -0.3, -0.35), (0.45, -0.3, -0.85)]],
             "splayed": [[(0.95, 0.1, -0.3), (0.7, -0.5, -0.5)], [(0.9, -0.35, -0.25), (0.55, -0.8, -0.2)]],
             "raised": [[(0.55, 0.65, -0.5), (0.25, -0.3, -1.0)], [(0.8, 0.25, -0.5), (0.3, -0.6, -0.8)]]}[pose]
    out: List[dict] = []
    for j in range(c.p["count"]):
        d1, d2 = poses[j]
        pts = c.walk((0.45 * b.cw, ys[j], front + 0.04), [d1, d2], [0.34 * s, 0.34 * s])
        out += c.path("a%d_" % j, "chest", pts, [t, t * 0.9, t * 0.8], suffix="_r", mirror=True, overlap=0.12)
        fs = 0.2 * s
        dn = _norm(d2)
        out.append(c.box("f%d_r" % j, "chest", _add(pts[2], _mul(dn, fs * 0.35)), (fs, fs, fs), rotate=_orient(dn), mirror=True))
    return out


@_preset("claws", "Claws, talons or blades on both fists.",
         {"style": _enum("claws", ("claws", "talons", "blades")), "count": _int(3, 2, 5)},
         main=("bone", "skin", "accent"))
def _claws(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    k = s * max(0.8, b.fist_w / 0.25) ** 0.6
    n = c.p["count"]
    st = c.p["style"]
    out: List[dict] = []
    for i in range(n):
        x = b.fist_cx + (i - (n - 1) / 2.0) * (b.fist_w * 0.8 / max(n - 1, 1))
        p0 = (x, b.fist_bot + 0.04, b.cz - b.fist_w * 0.38)
        if st == "claws":
            pts, wd = c.walk(p0, [(0, -0.85, -0.5)], [0.25 * k]), [0.06 * k, 0.01 * k]
        elif st == "talons":
            pts, wd = c.walk(p0, [(0, -0.8, -0.6), (0, -0.95, 0.0)], [0.16 * k, 0.15 * k]), [0.065 * k, 0.045 * k, 0.01 * k]
        else:
            pts, wd = c.walk(p0, [(0, -0.9, -0.4)], [0.55 * k]), [0.09 * k, 0.03 * k]
        out += c.path("c%d_" % i, "hand_r", pts, wd, [0.035 * k] * len(wd) if st == "blades" else wd, suffix="_r", mirror=True)
    return out


@_preset("ribcage", "Exposed ribs over the front of the chest.",
         {"count": _int(4, 3, 6)},
         main=("bone", "skin", "accent"))
def _ribcage(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    n = c.p["count"]
    front = b.cz - b.cd
    y_hi, y_lo = b.chest_top - 0.1, b.chest_bot + 0.03
    cav = c.over["accent"] or "#1c1418"
    cavity = c.box("cavity", "chest", (0, (y_hi + y_lo) / 2.0, front - 0.01), (1.76 * b.cw, y_hi - y_lo + 0.06, 0.02))
    cavity["material"], cavity["color"], cavity["accent"] = "leather", cav, cav
    out = [cavity, c.box("sternum", "chest", (0, (y_hi + y_lo) / 2.0, front - 0.035 * s), (0.05 * s, y_hi - y_lo + 0.04, 0.05 * s))]
    for i in range(n):
        y = y_hi - (y_hi - y_lo) * i / max(n - 1, 1)
        shrink = 0.06 * abs(i - (n - 1) / 2.0) / max((n - 1) / 2.0, 1.0)
        out.append(c.box("rib%d" % i, "chest", (0, y, front - 0.03 * s), (2 * b.cw * (0.88 - shrink), 0.05 * s, 0.05 * s)))
    return out


@_preset("core", "Glowing gem or reactor set into the chest.",
         {"style": _enum("gem", ("gem", "reactor", "crystal"))},
         main=("plate_steel", "secondary", "accent"))
def _core(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    front = b.cz - b.cd
    y = b.chest_bot + 0.62 * (b.chest_top - b.chest_bot)
    st = c.p["style"]
    if st == "gem":
        e = 0.17 * s
        return [c.box("socket", "chest", (0, y, front - 0.02), (0.3 * s, 0.3 * s, 0.04 * s)),
                c.box("gem", "chest", (0, y, front - 0.04 - e * 0.45), (e, e, e * 0.9), role="glow", rotate=[0, 0, 45])]
    if st == "reactor":
        out = [c.box("plate", "chest", (0, y, front - 0.02), (0.36 * s, 0.36 * s, 0.04 * s))]
        for i, (dx, dy, w, h) in enumerate(((0, 0.15, 0.4, 0.05), (0, -0.15, 0.4, 0.05), (0.17, 0, 0.05, 0.3), (-0.17, 0, 0.05, 0.3))):
            out.append(c.box("fr%d" % i, "chest", (dx * s, y + dy * s, front - 0.055 * s), (w * s, h * s, 0.04 * s), role="trim"))
        out.append(c.box("glow", "chest", (0, y, front - 0.06 * s), (0.22 * s, 0.22 * s, 0.05 * s), role="glow"))
        return out
    out = [c.box("socket", "chest", (0, y, front - 0.02), (0.28 * s, 0.28 * s, 0.04 * s))]
    for i, (dx, ln, wd, ox) in enumerate(((0.0, 0.3, 0.12, 0.0), (0.35, 0.22, 0.09, 0.07), (-0.35, 0.22, 0.09, -0.07))):
        pts = c.walk((ox * s, y - 0.03 * s, front - 0.04), [_norm((dx, 0.9, -0.6))], [ln * s])
        out += c.path("cr%d_" % i, "chest", pts, [wd * s, 0.03 * s], role="glow")
    return out


@_preset("spine_spikes", "Row of spikes, plates or blades down the back.",
         {"style": _enum("spikes", ("spikes", "plates", "blades")), "count": _int(6, 3, 9)},
         main=("bone", "skin", "accent"))
def _spine(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    n = c.p["count"]
    st = c.p["style"]
    y_hi, y_lo = b.head_bot - 0.04, b.abd_bot + 0.04
    out: List[dict] = []
    for i in range(n):
        t = i / max(n - 1, 1)
        y = y_hi + (y_lo - y_hi) * t
        bone = "chest" if y >= b.chest_bot else "torso"
        back = b.back_z(y - 0.05, y + 0.05, -0.1, 0.1, arms=False)
        f = 0.75 + 0.5 * math.sin(math.pi * min(1.0, t * 0.8 + 0.1))
        if st == "plates":
            h = 0.38 * f * s
            out.append(c.box("p%d" % i, bone, (0, y + h * 0.2, back + 0.07 * s), (0.06 * s, h, 0.28 * s), taper=(1.0, 0.2), rotate=[20, 0, 0]))
        else:
            ln = (0.32 if st == "blades" else 0.22) * f * s
            w = (0.07 if st == "blades" else 0.11) * f * s
            out += c.path("sp%d_" % i, bone, c.walk((0, y, back - 0.01), [(0, 0.45, 1.0)], [ln]), [w, 0.012 * s],
                          [w * 0.6 if st == "blades" else w, 0.012 * s])
    return out


# --------------------------------------------------------------------------- armor presets
@_preset("shield", "Shield strapped to a forearm: round, kite or tower.",
         {"style": _enum("round", ("round", "kite", "tower")), "side": _enum("left", ("left", "right"))},
         main=("plate_steel", "primary", "accent"))
def _shield(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    sx = -1.0 if c.p["side"] == "left" else 1.0
    bone = "forearm_l" if sx < 0 else "forearm_r"
    sfx = "_l" if sx < 0 else "_r"
    th = 0.05
    cx = sx * (b.arm_cx + 0.03 * s)
    yc = (b.elbow + b.wrist) / 2.0
    if st == "round":
        hx, y0, y1 = 0.32 * s, yc - 0.32 * s, yc + 0.32 * s
    elif st == "kite":
        hx, y0, y1 = 0.28 * s, yc - 0.5 * s, yc + 0.35 * s
    else:
        h = 1.15 * s
        yt = min(b.elbow - 0.05 * s, b.head_bot - 0.03 - h / 2.0)  # tower shields stop below the chin
        hx, y0, y1 = 0.3 * s, yt - h / 2.0, yt + h / 2.0
    zc = b.front_z(y0, y1, cx - hx, cx + hx) - 0.02 - th / 2.0  # in front of whatever the shield overlaps
    zt = zc - th / 2 - 0.007  # trim sits just in front of the face
    out: List[dict] = []
    if st == "round":
        R = hx
        out += [c.box("h" + sfx, bone, (cx, yc, zc), (2 * R, 1.4 * R, th)),
                c.box("v" + sfx, bone, (cx, yc, zc), (1.4 * R, 2 * R, th)),
                c.box("rim" + sfx, bone, (cx, yc, zt), (2 * R * 1.02, 0.045 * s, 0.015), role="trim"),
                c.box("boss" + sfx, bone, (cx, yc, zc - th / 2 - 0.035 * s), (0.18 * s, 0.18 * s, 0.07 * s), role="trim", taper=(0.7, 0.7), rotate=[0, 0, 45])]
    elif st == "kite":
        w = 2 * hx
        out += c.path("kite_", bone, [(cx, y1, zc), (cx, yc, zc), (cx, y0, zc)], [w, w, 0.06 * s], [th] * 3, suffix=sfx)
        out += [c.box("band_v" + sfx, bone, (cx, yc - 0.05 * s, zt), (0.07 * s, 0.8 * s, 0.015), role="trim"),
                c.box("band_h" + sfx, bone, (cx, yc + 0.12 * s, zt), (w * 0.85, 0.07 * s, 0.015), role="trim")]
    else:
        w = 2 * hx
        out += [c.box("slab" + sfx, bone, (cx, yt, zc), (w, h, th)),
                c.box("rim_t" + sfx, bone, (cx, y1 - 0.02, zt), (w, 0.06 * s, 0.015), role="trim"),
                c.box("rim_b" + sfx, bone, (cx, y0 + 0.02, zt), (w, 0.06 * s, 0.015), role="trim"),
                c.box("boss" + sfx, bone, (cx, yt + 0.15 * s, zc - th / 2 - 0.03 * s), (0.2 * s, 0.2 * s, 0.06 * s), role="trim", taper=(0.7, 0.7))]
    return out


@_preset("gauntlets", "Oversized armored forearms and fists.",
         {"style": _enum("plate", ("plate", "spiked", "knuckle"))},
         main=("plate_steel", "primary", "accent"))
def _gauntlets(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    t = (b.arm_t + 0.07) * s
    yl, yh = b.wrist, b.elbow - 0.02
    fw = (b.fist_w + 0.07) * s
    fh = b.fist_h + 0.05 * s
    fcy = b.fist_bot + b.fist_h / 2.0
    fx = b.fist_cx - b.fist_w / 2.0 + fw / 2.0  # the guard keeps the fist's inner edge and grows outwards
    out: List[dict] = [
        c.box("cuff_r", "forearm_r", (b.arm_cx, (yl + yh) / 2, b.cz), (t, yh - yl, t), taper=(1.12, 1.12), mirror=True),
        c.box("band_r", "forearm_r", (b.arm_cx, yh - 0.02, b.cz), (t * 1.18, 0.05 * s, t * 1.18), role="trim", mirror=True),
        c.box("fist_r", "hand_r", (fx, fcy, b.cz), (fw, fh, fw), taper=(0.96, 0.96), mirror=True),
    ]
    if st == "spiked":
        pts = c.walk((b.arm_cx + 0.02, b.elbow - 0.05, b.cz + t / 2.0 - 0.02), [(0.35, 0.3, 1.0)], [0.22 * s])
        out += c.path("elb_", "forearm_r", pts, [0.1 * s, 0.012 * s], suffix="_r", mirror=True)
        for i in range(3):
            pts = c.walk((fx + (i - 1) * fw * 0.3, fcy + fh / 2 - 0.01, b.cz - fw * 0.25), [(0, 1, -0.4)], [0.13 * s])
            out += c.path("ks%d_" % i, "hand_r", pts, [0.06 * s, 0.01 * s], suffix="_r", mirror=True)
    elif st == "knuckle":
        out.append(c.box("kbar_r", "hand_r", (fx, fcy + fh * 0.1, b.cz - fw / 2 - 0.02 * s), (fw * 1.02, 0.07 * s, 0.045 * s), role="trim", mirror=True))
        for i in range(3):
            pts = c.walk((fx + (i - 1) * fw * 0.32, fcy + fh * 0.1, b.cz - fw / 2 - 0.03 * s), [(0, 0, -1)], [0.12 * s])
            out += c.path("kn%d_" % i, "hand_r", pts, [0.06 * s, 0.01 * s], suffix="_r", mirror=True)
    return out


@_preset("greaves", "Shin armor with knee plates; optionally spiked or with heavy boots.",
         {"style": _enum("plate", ("plate", "spiked", "sabaton"))},
         main=("plate_steel", "primary", "accent"))
def _greaves(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    t = (b.leg_t + 0.06) * s
    y0, y1 = (0.12 if st == "sabaton" else 0.06), b.knee - 0.01
    kz = b.cz - t / 2.0
    out: List[dict] = [
        c.box("shin_r", "shin_r", (b.leg_cx, (y0 + y1) / 2, b.cz), (t, y1 - y0, t), taper=(1.1, 1.1), mirror=True),
        c.box("knee_r", "shin_r", (b.leg_cx, b.knee + 0.01, kz - 0.015), (b.leg_t * 0.85 * s, 0.13 * s, 0.07 * s), role="trim", mirror=True),
    ]
    if st == "spiked":
        out += c.path("ks_", "shin_r", c.walk((b.leg_cx, b.knee + 0.02, kz - 0.04), [(0.15, 0.35, -1.0)], [0.22 * s]), [0.11 * s, 0.012 * s], suffix="_r", mirror=True)
        out += c.path("sp_", "shin_r", c.walk((b.leg_cx, 0.14, b.cz + t / 2 - 0.02), [(0.0, 0.5, 1.0)], [0.16 * s]), [0.08 * s, 0.01 * s], suffix="_r", mirror=True)
    elif st == "sabaton":
        bw = (b.leg_t + 0.08) * s
        out.append(c.box("boot_r", "shin_r", (b.leg_cx, 0.06, b.cz - 0.06 * s), (bw, 0.12, b.leg_t + 0.18 * s), mirror=True))
        out.append(c.box("toe_r", "shin_r", (b.leg_cx, 0.045, b.cz - b.leg_t / 2 - 0.12 * s), (bw * 0.8, 0.09, 0.08 * s), taper=(0.7, 1.0), role="trim", mirror=True))
    return out


@_preset("belt", "Belt with a buckle, hanging plates or a sash.",
         {"style": _enum("plain", ("plain", "plated", "sash"))},
         main=("leather", "secondary", "accent"))
def _belt(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    y = b.abd_bot + 0.045
    hz = b.ad + 0.03
    out: List[dict] = [c.box("belt", "torso", (0, y, b.cz), (2 * (b.aw + 0.03), 0.1 * s, 2 * hz)),
                       c.box("buckle", "torso", (0, y, b.cz - hz - 0.01), (0.12 * s, 0.1 * s, 0.03), role="trim")]
    if st == "plated":
        xo = b.leg_cx + b.leg_t / 2.0
        zf = b.front_z(b.hip - 0.3, b.hip + 0.06, 0.0, xo + 0.03, arms=False) - 0.05
        for i, (x0, x1, ln) in enumerate(((0.01, xo * 0.72, 0.26), (xo * 0.72 + 0.01, xo + 0.03, 0.22))):
            xc = (x0 + x1) / 2
            out.append(c.box("tas%d_r" % i, "thigh_r", (xc, b.hip - ln * s / 2 + 0.02, zf), (x1 - x0 - 0.005, ln * s, 0.035),
                             role="steel", taper=(0.9, 1.0), rotate=[-4, 0, 0], pivot=(xc, b.hip + 0.02, zf), mirror=True))
    elif st == "sash":
        zs = b.cz - hz - 0.01
        ze = min(zs, b.front_z(y - 0.4, y, -b.aw, 0.0, arms=False) - 0.05)
        out += c.path("tail1_", "root", [(-b.aw * 0.55, y - 0.04, zs), (-b.aw * 0.65, y - 0.35 * s, ze)], [0.12 * s, 0.1 * s], [0.03, 0.03], role="cloth", overlap=0)
        out += c.path("tail2_", "root", [(-b.aw * 0.35, y - 0.04, zs - 0.005), (-b.aw * 0.3, y - 0.27 * s, ze - 0.02)], [0.1 * s, 0.08 * s], [0.03, 0.03], role="cloth", overlap=0)
    return out


@_preset("banner", "Banner on a pole carried on the back.",
         {"style": _enum("standard", ("standard", "pennant", "sashimono"))},
         main=("cloth", "primary", "accent"), trim=("plate_gold", "accent", "accent"))
def _banner(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    back = b.back_z(b.hip, b.chest_top, -0.1, 0.1, arms=False)
    zp = max(back, b.hcz + b.he / 2.0) + 0.05  # the pole stands behind the head
    ytop = b.head_top + 0.25 * s
    ybot = b.hip + 0.08
    out: List[dict] = [c.box("pole", "chest", (0, (ytop + ybot) / 2, zp), (0.05, ytop - ybot, 0.05), role="wood")]
    for i, ys in enumerate((b.chest_top - 0.1, ybot + 0.1)):  # struts tie the pole to the back
        zb = b.back_z(ys - 0.03, ys + 0.03, -0.1, 0.1, arms=False)
        out.append(c.box("strut%d" % i, "chest" if ys >= b.chest_bot else "torso", (0, ys, (zb + zp) / 2), (0.04, 0.04, zp - zb + 0.02), role="wood"))
    if st == "standard":
        wd = 0.5 * s
        out.append(c.box("bar", "chest", (0, ytop - 0.02, zp), (0.6 * s, 0.045, 0.045), role="trim"))
        pts = [(0, ytop - 0.05, zp + 0.045), (0, ytop - 0.05 - 0.55 * s, zp + 0.045 + 0.04 * s), (0, ytop - 0.05 - 0.85 * s, zp + 0.045 + 0.07 * s)]
        out += c.path("cloth", "chest", pts, [wd, wd, wd * 0.15], [0.03] * 3, overlap=0.0)
    elif st == "pennant":
        out += c.path("flag", "chest", [(0.025, ytop - 0.03 - 0.18 * s, zp), (0.025 + 0.8 * s, ytop - 0.07 - 0.04 * s, zp + 0.06)], [0.36 * s, 0.03 * s], [0.03, 0.03], overlap=0)
    else:
        w, h = 0.55 * s, 1.0 * s
        zb = zp + 0.05
        yc = ytop - h / 2 - 0.02
        out += [c.box("cloth", "chest", (0, yc, zb), (w, h, 0.03)),
                c.box("fr_t", "chest", (0, yc + h / 2, zb), (w + 0.06, 0.05 * s, 0.045), role="trim"),
                c.box("fr_b", "chest", (0, yc - h / 2, zb), (w + 0.06, 0.05 * s, 0.045), role="trim"),
                c.box("fr_s_r", "chest", (w / 2 + 0.015, yc, zb), (0.045, h, 0.045), role="trim", mirror=True)]
    return out


# --------------------------------------------------------------------------- public API
def list_presets() -> List[str]:
    return list(PRESETS)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _color_ok(v) -> bool:
    return isinstance(v, str) and (is_hex(v) or v in _spec.PALETTE_KEYS)


def validate_attachment(att: Any) -> List[Any]:
    """All problems of one attachment dict; paths are relative to it (`/style`).

    Codes: unknown_preset, unknown_value (unknown parameter or enum value), out_of_range, bad_color,
    bad_material, bad_type."""
    Err = _spec.Err
    if not isinstance(att, dict):
        return [Err("", "bad_type", "an attachment must be an object")]
    name = att.get("preset")
    if not isinstance(name, str) or name not in PRESETS:
        return [Err("/preset", "unknown_preset", "unknown preset %r; allowed: %s" % (name, ", ".join(PRESETS)))]
    errs: List[Any] = []
    params = PRESETS[name]["params"]
    for key, val in att.items():
        path = "/" + str(key)
        if key == "preset":
            continue
        if key not in params:
            errs.append(Err(path, "unknown_value", "unknown parameter %r for preset %r; allowed: %s" % (key, name, ", ".join(params))))
            continue
        p = params[key]
        typ = p["type"]
        if typ == "id":
            if not isinstance(val, str) or not ID_RE.match(val):
                errs.append(Err(path, "bad_type", "id %r must match %s" % (val, ID_RE.pattern)))
        elif key == "material":
            if not isinstance(val, str) or val not in _spec.MATERIALS:
                errs.append(Err(path, "bad_material", "material %r is not allowed; allowed: %s" % (val, ", ".join(_spec.MATERIALS))))
        elif typ == "enum":
            if not isinstance(val, str):
                errs.append(Err(path, "bad_type", "%s must be one of %s" % (key, ", ".join(p["allowed"]))))
            elif val not in p["allowed"]:
                errs.append(Err(path, "unknown_value", "%s %r is not allowed; allowed: %s" % (key, val, ", ".join(p["allowed"]))))
        elif typ == "color":
            if not isinstance(val, str):
                errs.append(Err(path, "bad_type", "%s must be a palette key or #rrggbb string" % key))
            elif not _color_ok(val):
                errs.append(Err(path, "bad_color", "%s %r must be a palette key (%s) or #rrggbb" % (key, val, ", ".join(_spec.PALETTE_KEYS))))
        elif typ == "bool":
            if not isinstance(val, bool):
                errs.append(Err(path, "bad_type", "%s must be true or false" % key))
        else:  # float / int
            ok = _is_num(val) if typ == "float" else (isinstance(val, int) and not isinstance(val, bool))
            if not ok:
                errs.append(Err(path, "bad_type", "%s must be %s, got %r" % (key, "a number" if typ == "float" else "an integer", val)))
            elif val < p["range"][0] or val > p["range"][1]:
                errs.append(Err(path, "out_of_range", "%s %r is outside the allowed range %s..%s" % (key, val, p["range"][0], p["range"][1])))
    return errs


def expand(att: dict, proportions: Optional[dict] = None, anatomy: Optional[dict] = None) -> List[dict]:
    """Final box dicts of one attachment on the body with the given proportions (ids prefixed by the attachment id).

    Raises BossForgeError for an invalid attachment. `anatomy` (rigs.json) is accepted for the shape API; the
    positions come from the proportioned base boxes."""
    errs = validate_attachment(att)
    if errs:
        raise _spec.BossForgeError(errs[0].path, errs[0].code, errs[0].message)
    name = att["preset"]
    prefix = att.get("id", name)
    boxes = _FUNCS[name](_Ctx(name, att, body_from(proportions)))
    out: List[dict] = []
    seen = set()
    for bx in boxes:
        bx = dict(bx)
        bx["id"] = "%s_%s" % (prefix, bx["id"])
        if bx["id"] in seen:
            raise _spec.BossForgeError("/id", "duplicate_box_id", "preset %r produced box id %r twice" % (name, bx["id"]))
        seen.add(bx["id"])
        out.append(bx)
    return out


# --------------------------------------------------------------------------- silhouettes
_SILHOUETTES: Dict[str, dict] = {}


def _load_silhouettes() -> Dict[str, dict]:
    if not _SILHOUETTES:
        _SILHOUETTES.update(json.loads((DATA_DIR / "silhouettes.json").read_text(encoding="utf-8")))
    return _SILHOUETTES


def list_silhouettes() -> List[str]:
    return list(_load_silhouettes())


def silhouette(name: str) -> dict:
    sil = _load_silhouettes()
    if name not in sil:
        raise _spec.BossForgeError("/silhouette", "unknown_silhouette",
                                   "unknown silhouette %r; allowed: %s" % (name, ", ".join(sil)))
    return copy.deepcopy(sil[name])


def default_silhouette(archetype: str) -> str:
    return _SILHOUETTE_BY_ARCHETYPE.get(archetype, "plain")
