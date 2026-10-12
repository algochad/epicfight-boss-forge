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
Parts with a fixed look inside a preset (ribbon loops, pins, vials, the skirt's hem layer) use extra roles
(`_preset(..., extra=...)`); colour parameters of type `colors` are lists (pouch vials). Colours that are blended
(seraph feather gradient) blend only `#rrggbb` values: presets know no palette, a palette key snaps to the nearest stop.
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
from .colors import hex_to_rgb, is_hex, mix, rgb_to_hex

Vec = Tuple[float, float, float]

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
SCALE_RANGE = (0.5, 2.5)
MAX_BOXES = 120  # per attachment, after mirror expansion
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


def _colors(default, max_len: int):
    return {"type": "colors", "default": list(default), "max": int(max_len)}


COMMON_PARAMS = ("id", "scale", "color", "accent", "material")
PRESETS: Dict[str, dict] = {}
_FUNCS: Dict[str, Callable] = {}
_ROLES: Dict[str, Dict[str, Tuple[Optional[str], str, str]]] = {}
_FOLLOW: Dict[str, Tuple[str, ...]] = {}
# (material, color, accent) triples; `main` is overridable by the attachment's material/color/accent. A role whose
# material is None uses the main material (with its override); a role named in a preset's `follow` keeps its own
# material until the attachment sets `material`, which then reaches that role too.
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
            trim: Optional[Tuple[str, str, str]] = None, extra: Optional[Dict[str, Tuple[Optional[str], str, str]]] = None,
            follow: Sequence[str] = ()):
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
        roles.update(extra or {})  # fixed-look parts of the preset (not recolored by color/accent)
        _ROLES[name] = roles
        _FOLLOW[name] = tuple(follow)
        _FUNCS[name] = fn
        return fn
    return deco


class _Ctx:
    """Everything a preset function needs: body, scale, resolved parameters and the box builders."""

    def __init__(self, name: str, att: dict, body: Body, views: Optional[dict] = None, notes: Optional[dict] = None):
        self.att_id = att.get("id", name)
        self.views = views  # reference views {name: View} (only the `relief` preset reads them)
        self.notes = notes  # optional out-channel {"warnings": [Err], "relief": [dict]}
        self.name = name
        self.b = body
        schema = PRESETS[name]["params"]
        self.given = set(att)  # parameters the attachment really sets (defaults are not "given")
        self.p = {k: att.get(k, v["default"]) for k, v in schema.items() if k not in COMMON_PARAMS}
        self.s = float(att.get("scale", 1.0))
        self.hu = body.he / 0.5
        self.over = {k: att.get(k) for k in ("material", "color", "accent")}
        self.roles = dict(_GLOBAL_ROLES)
        self.roles.update(_ROLES[name])
        self.follow = _FOLLOW.get(name, ())

    def style(self, role: str) -> Tuple[str, str, str]:
        m, col, acc = self.roles[role]
        if role == "main":
            m = self.over["material"] or m
            col = self.over["color"] or col
            acc = self.over["accent"] or acc
        else:
            if m is None:
                m = self.over["material"] or self.roles["main"][0]
            elif role in self.follow and self.over["material"]:
                m = self.over["material"]
            if role == "trim" and self.over["accent"]:
                col = acc = self.over["accent"]
        return m, col, acc

    def box(self, lid: str, bone: str, cen, size, role: str = "main", taper=None, rotate=None, pivot=None,
            mirror: bool = False, density: Optional[float] = None) -> dict:
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
        if density is not None and abs(density - 1.0) > 1e-6:
            out["density"] = _r(density)
        out["material"], out["color"], out["accent"] = self.style(role)
        return out

    def path(self, prefix: str, bone: str, pts: Sequence[Vec], widths: Sequence[float],
             depths: Optional[Sequence[float]] = None, role: str = "main", suffix: str = "",
             mirror: bool = False, overlap: float = 0.08, density: Optional[float] = None) -> List[dict]:
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
                                taper=(w1 / w0, d1 / d0), rotate=_orient(d), mirror=mirror, density=density))
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


@_preset("skirt", "Tabard, robe or kilt hanging from the waist on the thigh bones, so the legs stay free. `slit` cuts two slits "
                  "into the front, one on each side of a centre front panel (ids `fp*`, a proud cream tabard panel), from the "
                  "hem trim up to the thigh so the legs show in them; `panel` adds the centre panel without slits; `hem` adds "
                  "a second, lighter and longer layer (colour `hem_color`, material `lace`, own thin trim) under "
                  "the hem trim. `arm_clear` keeps the skirt (and its belt band) inside the inner edge of the arms down to "
                  "the hands, so the sleeves, cuffs and hands stay visible and nothing clips through a swinging arm; it "
                  "flares outwards only below the hands.",
         {"style": _enum("robe", ("tabard", "robe", "kilt")), "trim": _bool(True), "slit": _bool(False), "panel": _bool(False),
          "hem": _bool(False), "hem_color": {"type": "color", "default": "#e8eefa"}, "arm_clear": _bool(False)},
         main=("cloth", "secondary", "accent"),
         extra={"under": ("lace", "#e8eefa", "#e8eefa"), "tabard": (None, "#f2ead3", "#d4aa32")},
         follow=("trim", "under"))
def _skirt(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    slit = bool(c.p["slit"])
    has_fp = bool(c.p["panel"]) or slit
    arm_clear = bool(c.p["arm_clear"])
    ytop = b.abd_bot + 0.04
    hem = max(0.03, ytop - (ytop - {"kilt": 0.5, "tabard": 0.26, "robe": 0.06}[st]) * s)
    ys = [ytop] + ([b.knee] if hem < b.knee - 0.05 else []) + [hem]
    gap, th = 0.03, 0.04
    fac = 0.62 if st == "tabard" else 1.0
    flare = {"kilt": 0.0, "tabard": 0.08, "robe": 0.2}[st] * s
    x0 = max(b.leg_cx + b.leg_t / 2.0, b.pw) + 0.045
    f0 = b.front_z(hem, ytop, -x0, x0, arms=False) - gap - th / 2.0
    b0 = b.back_z(hem, ytop, -x0, x0, arms=False) + gap + th / 2.0
    # arm_clear: above the hands the skirt stays inside the arms' inner edge (the chest edge, minus 1 cm); it flares from
    # the hands' height (a little above the lowest point of the fists) down to the hem
    x_arm = b.arm_cx - b.arm_t / 2.0 - 0.01
    yc = min(ytop, b.fist_bot - 0.08)
    if arm_clear and hem + 0.05 < yc < ytop - 0.03 and all(abs(y - yc) > 0.03 for y in ys):
        ys = sorted(ys + [yc], reverse=True)

    def xr(y: float) -> float:
        """Half width of the front/back panels at height y, before the tabard factor."""
        r = x0 + flare * (ytop - y)
        if not arm_clear:
            return r
        lim = x_arm / fac
        if y >= yc - 1e-9 or hem >= yc - 0.05:
            return min(r, lim)
        r_c = min(x0 + flare * (ytop - yc), lim)
        r_h = x0 + flare * (ytop - hem)
        return r_c + (r_h - r_c) * (yc - y) / (yc - hem)

    def fz(y: float) -> float:
        return f0 - 0.5 * flare * (ytop - y)

    def bz(y: float) -> float:
        return b0 + 0.5 * flare * (ytop - y)

    y_s: Optional[float] = None  # the slits run from the hem trim up to this height
    if slit:
        y_s = hem + max(0.06, min(0.6 * (ytop - hem), ytop - hem - 0.08))
        if y_s < ytop - 0.02:
            if arm_clear and yc in ys[1:-1] and abs(y_s - yc) < 0.15:
                y_s = yc  # the slits open where the skirt starts to flare
            y_s = next((y for y in ys[1:-1] if abs(y - y_s) < 0.05), y_s)  # reuse a joint when it is close
            if y_s not in ys:
                ys = sorted(ys + [y_s], reverse=True)
        else:
            y_s = None
    xs = [xr(y) for y in ys]
    fs = [fz(y) for y in ys]
    bs = [bz(y) for y in ys]
    n = len(ys) - 1
    bones = ["thigh_r" if ys[i + 1] >= b.knee - 1e-6 else "shin_r" for i in range(n)]  # a segment below the knee follows the shin
    inner = 0.0  # mirrored halves meet at x = 0 (a gap would show as a dark line)
    reach = [x * fac for x in xs]
    # centre panel half width and the slit's outer edge (x of the right side; the left is mirrored)
    ph = [0.40 * r for r in reach]
    xo = [0.40 * r + max(0.05, 0.2 * r) for r in reach]
    seams = [(xo[i], xo[i + 1]) if y_s is not None and ys[i] <= y_s + 1e-6 else (inner, inner) for i in range(n)]
    out: List[dict] = []

    def panel(prefix: str, zs, seam_of):
        for i in range(n):
            xa, xb = seam_of[i]
            wt, wb = reach[i] - xa, reach[i + 1] - xb
            pts = [(xa + wt / 2.0, ys[i], zs[i]), (xb + wb / 2.0, ys[i + 1], zs[i + 1])]
            out.extend(c.path("%s%d_" % (prefix, i), bones[i], pts, [wt, wb], [th, th], suffix="_r", mirror=True, overlap=0.03))
        if c.p["trim"]:  # the trim runs across the whole width, also under the slits: the panels meet at the hem
            q0 = (inner + (reach[-2] - inner) / 2.0, ys[-2], zs[-2])
            q1 = (inner + (reach[-1] - inner) / 2.0, ys[-1], zs[-1])
            tt = 0.06 / max(_len(_sub(q1, q0)), 1e-6)
            out.extend(c.path("%st_" % prefix, bones[-1], [_lerp(q1, q0, tt), q1], [reach[-1] - inner + 0.014] * 2, [th + 0.014] * 2,
                              role="trim", suffix="_r", mirror=True, overlap=0))

    def pt_side(y: float) -> Vec:
        return (xr(y) + th / 2.0, y, (fz(y) + bz(y)) / 2.0)

    panel("fr", fs, seams)
    panel("bk", bs, [(inner, inner)] * n)
    if has_fp:  # centre front panel, 1 cm proud of the front; it stops above the hem trim so the trim stays continuous
        for i in range(n):
            top = (inner + (ph[i] - inner) / 2.0, ys[i], fs[i])
            bot = (inner + (ph[i + 1] - inner) / 2.0, ys[i + 1], fs[i + 1])
            w0, w1 = ph[i] - inner, ph[i + 1] - inner
            if i == n - 1 and c.p["trim"]:
                tt = 0.06 / max(_len(_sub(bot, top)), 1e-6)
                bot = _lerp(bot, top, tt)
                w1 = w1 + (w0 - w1) * tt
            out.extend(c.path("fp%d_" % i, bones[i], [top, bot], [w0, w1], [th + 0.02] * 2, role="tabard", suffix="_r", mirror=True, overlap=0))
    if st != "tabard":
        sd_ys = list(ys)
        if arm_clear:
            # side panels only where the skirt stands clear of the thighs (and so of the arms): from the height where its
            # half width reaches the unconstrained top width, down to the hem
            if xr(ytop) >= x0:
                y_sd: Optional[float] = ytop
            elif xr(hem) < x0:
                y_sd = None
            else:
                lo, hi = hem, ytop  # xr(lo) >= x0 > xr(hi): the skirt is wider lower down
                for _ in range(40):
                    mid = (lo + hi) / 2.0
                    lo, hi = (lo, mid) if xr(mid) < x0 else (mid, hi)
                y_sd = lo
            if y_sd is not None:
                y_sd = min(y_sd, yc)  # never in the hands' zone, even on a body whose arms hang far from the thighs
            sd_ys = [] if y_sd is None or y_sd - hem < 0.06 else [y_sd] + [y for y in ys if y < y_sd - 1e-6]
        for i in range(len(sd_ys) - 1):
            q0, q1 = pt_side(sd_ys[i]), pt_side(sd_ys[i + 1])
            mid_y = (sd_ys[i] + sd_ys[i + 1]) / 2.0
            k = max(j for j in range(n) if ys[j] >= mid_y - 1e-9)  # the front panel segment this piece belongs to
            out.extend(c.path("sd%d_" % k, bones[k], [q0, q1], [th, th], [bz(sd_ys[i]) - fz(sd_ys[i]), bz(sd_ys[i + 1]) - fz(sd_ys[i + 1])],
                              suffix="_r", mirror=True, overlap=0.03))
        if c.p["trim"] and len(sd_ys) >= 2:
            q0, q1 = pt_side(sd_ys[-2]), pt_side(sd_ys[-1])
            tt = 0.06 / max(_len(_sub(q1, q0)), 1e-6)
            out.extend(c.path("sdt_", bones[-1], [_lerp(q1, q0, tt), q1], [th + 0.014] * 2, [bz(sd_ys[-1]) - fz(sd_ys[-1]) + 0.014] * 2,
                              role="trim", suffix="_r", mirror=True, overlap=0))
    if c.p["hem"]:
        # a lighter, longer layer under the trim: it continues the last segment's line downwards and sticks out of it by
        # 6 mm on every side, so the two layers never share a face; its own thin trim closes the lower edge
        ya = hem + 0.015
        yb = max(0.02, hem - min(0.2, max(0.09, 0.16 * (ytop - hem) + 0.04)))
        if ya - yb >= 0.05:
            ext = 0.012

            def row(y: float):
                return (y, xr(y) * fac + ext / 2.0, fz(y), bz(y))

            rows = [row(ya), row(yb)]
            th_e = 0.035 if c.p["trim"] else 0.0
            edge = row(yb + th_e)
            made: List[dict] = []
            for pre, zi in (("hf", 2), ("hb", 3)):
                pts = [(inner + (r[1] - inner) / 2.0, r[0], r[zi]) for r in rows]
                made += c.path(pre, bones[-1], pts, [r[1] - inner for r in rows], [th + ext] * 2, role="under", suffix="_r", mirror=True, overlap=0)
            if st != "tabard" and (not arm_clear or sd_ys):
                pts = [(xr(r[0]) + th / 2.0, r[0], (r[2] + r[3]) / 2.0) for r in rows]
                made += c.path("hs", bones[-1], pts, [th + ext] * 2, [r[3] - r[2] + ext for r in rows], role="under", suffix="_r", mirror=True, overlap=0)
            for bx in made:
                bx["color"] = bx["accent"] = c.p["hem_color"]
            out += made
            if th_e:
                for pre, zi in (("hft", 2), ("hbt", 3)):
                    pts = [(inner + (r[1] - inner) / 2.0, r[0], r[zi]) for r in (edge, rows[1])]
                    out += c.path(pre, bones[-1], pts, [r[1] - inner + 0.006 for r in (edge, rows[1])], [th + ext + 0.006] * 2, role="trim",
                                  suffix="_r", mirror=True, overlap=0)
                if st != "tabard" and (not arm_clear or sd_ys):
                    pts = [(xr(r[0]) + th / 2.0, r[0], (r[2] + r[3]) / 2.0) for r in (edge, rows[1])]
                    out += c.path("hst", bones[-1], pts, [th + ext + 0.006] * 2, [r[3] - r[2] + ext + 0.006 for r in (edge, rows[1])],
                                  role="trim", suffix="_r", mirror=True, overlap=0)
    band_x = min(x0 - 0.01, x_arm) if arm_clear else x0 - 0.01
    out.append(c.box("band", "root", (0, (ytop + b.hip) / 2 + 0.0275, b.cz), (2 * band_x, ytop - b.hip + 0.045, 2 * (max(b.ad, b.leg_t / 2.0) + 0.035)), role="trim"))
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


# --------------------------------------------------------------------------- seraph wings (dense feathered sail)
_SERAPH_ROWS = 9   # feather rows from the front of the wing to the back; the middle row is the longest
_SERAPH_PER_ROW = 5
_SERAPH_STAIRS = 7  # stacked blocks of the leading edge
# (t along the strut 0..1, how far below the wing root the tips of the longest row hang, blocks at scale 1.0)
_SERAPH_TIPS = ((0.0, 0.30), (0.2, 0.48), (0.5, 0.70), (0.78, 0.74), (1.0, 0.54))
_SERAPH_TIP_DEFAULT = "#aa98f2"


def _table(rows: Sequence[Tuple[float, float]], t: float) -> float:
    """Piecewise-linear lookup of `t` in sorted (t, value) rows (clamped at both ends)."""
    if t <= rows[0][0]:
        return rows[0][1]
    for (t0, v0), (t1, v1) in zip(rows, rows[1:]):
        if t <= t1:
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return rows[-1][1]


def _mix_ref(a: str, b: str, t: float) -> str:
    """Blend two colour references. #rrggbb blends; palette keys cannot (presets know no palette), they snap."""
    if is_hex(a) and is_hex(b):
        return rgb_to_hex(mix(hex_to_rgb(a), hex_to_rgb(b), t))
    return a if t < 0.5 else b


def _gradient(stops: Sequence[str], u: float) -> str:
    """Colour at `u` (0..1) along the evenly spaced `stops`."""
    n = len(stops) - 1
    x = _clamp(u, 0.0, 1.0) * n
    i = min(int(x), n - 1)
    return _mix_ref(stops[i], stops[i + 1], x - i)


def _seraph(c: _Ctx, back: float) -> List[dict]:
    """Raised angel wings as ONE dense sail: a thick stair-stepped leading edge rising up and out from the shoulder
    blades (angle `spread` from vertical), and 9 layered rows of broad overlapping feathers fanning out from it (rows
    offset by half a feather so no gap stays open; inner feathers hang straight and short, outer ones are longer and
    splay outwards; the lower edge is jagged). The row index drives a 3-stop gradient: the outer rows (seen from the
    front and from the back) take `color`, the middle rows `accent`, the longest middle row `tip`; rows are ordered so
    that the visible band below each row is a little longer and a little more tinted than the one above."""
    b, s = c.b, c.s
    sq = math.sqrt(s)
    sp = float(c.p["spread"])
    white = c.over["color"] or "#f4f6ff"
    lilac = c.over["accent"] or "#ddd3ff"
    tip = c.p["tip"]
    if "tip" not in c.given and is_hex(lilac):
        tip = _mix_ref(lilac, "#8a70e8", 0.55)  # a deeper violet of the accent
    stops = [white, lilac, tip]
    mat = c.over["material"]  # an override reaches every box; by default the feathers use their own vane material
    hw = b.he / 2.0
    xr, yr = max(0.3 * b.cw + 0.03, 0.12), b.head_bot - 0.18 * s
    zc = back + 0.09   # the wing plane starts just behind the back and sweeps backwards with the distance
    kz = 0.3
    # strut: from the shoulder blade out to the side of the head (so it never cuts through the skull), then up and out
    a1, a2 = math.radians(sp), math.radians(0.85 * sp)
    q1 = (hw + 0.13 * s + 0.01, b.head_bot - 0.14 * s)  # the blocks up to here stay below the head
    q2 = (q1[0] + 0.52 * s * math.sin(a1), q1[1] + 0.52 * s * math.cos(a1))
    q3 = (q2[0] + 0.62 * s * math.sin(a2), q2[1] + 0.62 * s * math.cos(a2))
    poly = [(xr, yr), q1, q2, q3]
    lens = [math.hypot(poly[i + 1][0] - poly[i][0], poly[i + 1][1] - poly[i][1]) for i in range(3)]
    total = sum(lens)
    splay = min(0.55 * sp, 26.0)  # how far the outermost feathers point outwards (degrees from vertical)

    def strut(t: float) -> Vec:
        d = _clamp(t, 0.0, 1.0) * total
        i = 0
        while i < 2 and d > lens[i]:
            d -= lens[i]
            i += 1
        u = d / lens[i]
        x = poly[i][0] + (poly[i + 1][0] - poly[i][0]) * u
        y = poly[i][1] + (poly[i + 1][1] - poly[i][1]) * u
        return (x, y, zc + kz * (x - xr))

    out: List[dict] = []
    for j in range(_SERAPH_STAIRS):
        u = j / (_SERAPH_STAIRS - 1.0)
        w = (0.24 - 0.04 * u) * s
        out.append(c.box("st%d_r" % j, "chest", strut(u), (w, w, 0.14 * sq * (1.0 - 0.25 * u)), mirror=True, density=0.7))
    mid = (_SERAPH_ROWS - 1) / 2.0
    dz, th = 0.016 * sq, 0.024 * sq
    for k in range(_SERAPH_ROWS):
        u_row = 1.0 - abs(k - mid) / mid
        keep = 0.40 + 0.60 * u_row  # share of the full length: the outer rows are short, the middle one longest
        for j in range(_SERAPH_PER_ROW):
            t = 0.05 + 0.95 * (j + 0.5 * (k % 2)) / (_SERAPH_PER_ROW - 0.5)
            ax, ay, az = strut(t)
            az += (k - mid) * dz
            ang = math.radians(2.0 + splay * t ** 1.1 + 1.6 * math.sin(1.7 * j + 2.3 * k + 0.5))
            tip_y = yr - _table(_SERAPH_TIPS, t) * s
            end_y = max(0.03, ay - (ay - tip_y) * keep + 0.05 * s * math.sin(2.9 * j + 1.3 * k + 1.0))
            ln = max(0.12 * s, (ay - end_y) / math.cos(ang))
            d = _norm((math.sin(ang), -math.cos(ang), kz * math.sin(ang)))
            a = (ax, ay, az)
            out += c.path("f%d_%d_" % (k, j), "chest", [a, _add(a, _mul(d, ln))], [0.32 * s, 0.19 * s], [th, th],
                          suffix="_r", mirror=True, overlap=0.0, density=0.6)
            # neighbouring feathers differ slightly in tone so the single feathers stay readable inside the sail
            out[-1]["color"] = out[-1]["accent"] = _gradient(stops, u_row + 0.06 * (((j + k) % 2) - 0.5) * (1.0 if u_row < 1.0 else 0.5))
    for bx in out:
        stem = bx["id"].startswith("st")
        bx["material"] = mat or ("silk" if stem else "silk_feather")
        if stem:
            bx["color"] = bx["accent"] = white
    return out


# --------------------------------------------------------------------------- wings, orbs, extra limbs
@_preset("wings", "Pair of wings on the upper back; `spread` (10-80 degrees) tilts the main strut away from vertical. "
                  "`seraph` = raised angel wings as one dense fan-shaped sail (stair-stepped leading edge at `spread` degrees from "
                  "vertical, 9 layered feather rows): `color` white, `accent` lavender, `tip` violet; use spread ~30.",
         {"style": _enum("bat", ("bat", "feathered", "angel", "seraph", "skeletal", "flame")), "spread": _float(45.0, 10.0, 80.0),
          "tip": {"type": "color", "default": _SERAPH_TIP_DEFAULT}},
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
    if st == "seraph":
        return _seraph(c, back)
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
                    bx["material"], bx["color"] = "cloth", c.over["color"] or "#efe9dc"
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


@_preset("greaves", "Shin armor with knee plates; optionally spiked or with heavy boots. `cuff` adds a coloured band around the top "
                    "of the shin (for `sabaton` it replaces the knee plate: smooth boots with a cuff), `sole` a light sole under "
                    "the `sabaton` boot. A `material` override reaches every box (try `silk` or `leather` for soft boots).",
         {"style": _enum("plate", ("plate", "spiked", "sabaton")), "cuff": _bool(False),
          "cuff_color": {"type": "color", "default": "secondary"}, "sole": _bool(False)},
         main=("plate_steel", "primary", "accent"),
         extra={"cuffband": (None, "secondary", "secondary"), "sole": (None, "#e6ebf5", "#e6ebf5")},
         follow=("trim",))
def _greaves(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    t = (b.leg_t + 0.06) * s
    y0, y1 = (0.12 if st == "sabaton" else 0.06), b.knee - 0.01
    kz = b.cz - t / 2.0
    cuff = bool(c.p["cuff"])
    out: List[dict] = [
        c.box("shin_r", "shin_r", (b.leg_cx, (y0 + y1) / 2, b.cz), (t, y1 - y0, t), taper=(1.1, 1.1), mirror=True),
    ]
    if not (cuff and st == "sabaton"):
        out.append(c.box("knee_r", "shin_r", (b.leg_cx, b.knee + 0.01, kz - 0.015), (b.leg_t * 0.85 * s, 0.13 * s, 0.07 * s), role="trim", mirror=True))
    if cuff:
        ch = 0.075 * s
        band = c.box("cuff_r", "shin_r", (b.leg_cx, y1 - ch / 2.0 + 0.005, b.cz), (t * 1.1 + 0.02, ch, t * 1.1 + 0.02), role="cuffband", mirror=True)
        band["color"] = band["accent"] = c.p["cuff_color"]
        out.append(band)
    if st == "spiked":
        out += c.path("ks_", "shin_r", c.walk((b.leg_cx, b.knee + 0.02, kz - 0.04), [(0.15, 0.35, -1.0)], [0.22 * s]), [0.11 * s, 0.012 * s], suffix="_r", mirror=True)
        out += c.path("sp_", "shin_r", c.walk((b.leg_cx, 0.14, b.cz + t / 2 - 0.02), [(0.0, 0.5, 1.0)], [0.16 * s]), [0.08 * s, 0.01 * s], suffix="_r", mirror=True)
    elif st == "sabaton":
        bw = (b.leg_t + 0.08) * s
        out.append(c.box("boot_r", "shin_r", (b.leg_cx, 0.06, b.cz - 0.06 * s), (bw, 0.12, b.leg_t + 0.18 * s), mirror=True))
        out.append(c.box("toe_r", "shin_r", (b.leg_cx, 0.045, b.cz - b.leg_t / 2 - 0.12 * s), (bw * 0.8, 0.09, 0.08 * s), taper=(0.7, 1.0), role="trim", mirror=True))
        if c.p["sole"]:
            out.append(c.box("sole_r", "shin_r", (b.leg_cx, 0.02, b.cz - 0.065 * s), (bw + 0.014, 0.04, b.leg_t + 0.19 * s + 0.014),
                             role="sole", mirror=True))
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


# --------------------------------------------------------------------------- hair, ribbons, pouch
def _hair_bun(c: _Ctx, prefix: str, x: float, z: float, y0: float, r: float, mirror: bool) -> List[dict]:
    """A bun of four stacked boxes (lower, darker band, upper, cap): `r` is its diameter, `y0` its base."""
    sfx = "_r" if mirror else ""
    out: List[dict] = []
    y = y0
    for tag, role, wr, hr, ry in (("a", "main", 1.0, 0.30, 0.0), ("b", "band", 1.14, 0.12, 0.0),
                                  ("c", "main", 0.8, 0.34, 45.0), ("d", "main", 0.66, 0.16, 0.0)):  # 45 degrees keeps it symmetric
        out.append(c.box("%s%s%s" % (prefix, tag, sfx), "head", (x, y + hr * r / 2.0, z), (wr * r, hr * r, wr * r), role=role,
                         rotate=[0.0, ry, 0.0], mirror=mirror))
        y += hr * r
    return out


@_preset("hairdo", "Hair volume on the head in the hair colour: a cap over the top and back (the face stays open), a bun, "
                   "twin buns, ponytail or braid, bangs and side locks framing the face down to the chest, optional white-blue "
                   "ribbon loops at the back of the head and teal/gold leaf pins at the front sides.",
         {"style": _enum("bun", ("bun", "twin_buns", "ponytail", "braid")), "bangs": _bool(True), "locks": _bool(True),
          "ribbon_loops": _bool(False), "pins": _bool(False)},
         main=("hair_soft", "hair", "#f4f8ff"), trim=("silk", "#f4f8ff", "#9cc2ff"),
         extra={"band": ("leather", "hair", "hair"), "blue": ("silk", "#9cc2ff", "#9cc2ff"),
                "teal": ("plate_gold", "#35c9b8", "#35c9b8"), "gold": ("plate_gold", "#e6c050", "#e6c050")})
def _hairdo(c: _Ctx) -> List[dict]:
    b, s, hu = c.b, c.s, c.hu
    he, hw = b.he, b.he / 2.0
    yt, yb, cz = b.head_top, b.head_bot, b.hcz
    zf, zb = cz - hw, cz + hw
    t = 0.03 * hu ** 0.7
    st = c.p["style"]
    y_cap = yb + 0.40 * he  # the cap's back covers the upper 60 % of the head; a narrower curtain continues below
    out: List[dict] = [
        c.box("top", "head", (0, yt + (t - 0.004) / 2.0, (zf + zb + t) / 2.0), (he + 2 * t, t + 0.004, he + t)),
        c.box("back", "head", (0, (yt + t + y_cap) / 2.0, zb + t / 2.0), (he + 2 * t, yt + t - y_cap, t + 0.004)),
        c.box("side_r", "head", (hw + t / 2.0 - 0.002, yt + t / 2.0 - 0.22 * he, (zf + 0.25 * he + zb + t) / 2.0),
              (t + 0.004, 0.44 * he + t, zb + t - zf - 0.25 * he), mirror=True),
    ]
    # hair gathered behind the head: a narrow tapering curtain down to the shoulder blades, never wider than the head
    cu_pts = [(0.0, y_cap + 0.04 * he, zb + t + 0.012), (0.0, yb - 0.04 * he, zb + t + 0.02), (0.0, yb - 0.30 * he * s, zb + t - 0.012)]
    out += c.path("cu", "head", cu_pts, [0.8 * he, 0.68 * he, 0.42 * he], [0.05 * hu ** 0.7, 0.05 * hu ** 0.7, 0.04 * hu ** 0.7], overlap=0.05)
    if c.p["bangs"]:  # centre-parted fringe over the forehead; it ends well above the brows
        bt = 0.045 * hu ** 0.7
        y_hi, y_lo = yt + t, yt - 0.22 * he
        out.append(c.box("bang_r", "head", (0.26 * he, (y_hi + y_lo) / 2.0, zf - bt / 2.0 + 0.005), (0.52 * he, y_hi - y_lo, bt),
                         rotate=[0.0, 0.0, 7.0], mirror=True))
    if c.p["locks"]:  # thin locks beside the face, past the chin down to the chest
        ks = s ** 0.6
        wl, dl = 0.11 * he * ks, 0.07 * hu ** 0.7 * ks
        x = hw + wl / 2.0 - 0.008
        top, bottom = yt - 0.06 * he, yb - 0.28 * he * s
        ys = [top + (bottom - top) * f for f in (0.0, 0.45, 0.78, 1.0)]
        xs = [x, x + 0.004, x - 0.004, x - 0.016]
        pts = []
        for px, py in zip(xs, ys):
            z = zf - 0.006 + dl / 2.0
            if py < yb:  # below the head: stay in front of the chest and the arms
                z = min(z, b.front_z(py - 0.06, py + 0.06, 0.0, px + wl, arms=True) - dl / 2.0 - 0.012)
            pts.append((px, py, z))
        out += c.path("lk", "head", pts, [wl, wl, wl * 0.85, wl * 0.5], [dl, dl, dl * 0.85, dl * 0.6], suffix="_r", mirror=True, overlap=0.05)
    ybase = yt + t - 0.004
    if st == "bun":
        out += _hair_bun(c, "bn", 0.0, cz + 0.2 * he, ybase, 0.56 * he * s, False)
    elif st == "twin_buns":
        out += _hair_bun(c, "bn", 0.3 * he, cz + 0.2 * he, ybase, 0.34 * he * s, True)
    else:  # ponytail / braid: hangs from a tie at the back of the head
        k = he * s
        n = 4 if st == "ponytail" else 6
        seg = 0.5 * k if st == "ponytail" else 0.33 * k
        pts = c.walk((0.0, yt - 0.1 * he, zb + t + 0.02 * k), [(0.0, -1.0, 0.35 + 0.1 * (i % 3))for i in range(n)], [seg] * n)
        if st == "ponytail":
            out += c.path("pt", "head", pts, [0.2 * k, 0.26 * k, 0.22 * k, 0.14 * k, 0.06 * k], [0.16 * k, 0.2 * k, 0.17 * k, 0.11 * k, 0.05 * k])
        else:
            for i in range(n):
                w = (0.17 if i % 2 == 0 else 0.14) * k
                out += c.path("br%d_" % i, "head", pts[i:i + 2], [w, w * 0.92], [w * 0.85] * 2, role="main" if i % 2 == 0 else "band", overlap=0.1)
        out.append(c.box("tie", "head", pts[0], (0.24 * k, 0.07 * k, 0.2 * k), role="band"))
    if c.p["ribbon_loops"]:  # two ribbon loops hanging behind the cap
        lw, lh, bar = 0.32 * he, 0.78 * he, max(0.03, 0.07 * he)
        dep = 0.035 * hu ** 0.7
        zl = zb + t + dep / 2.0 + 0.002
        xc, ytop = 0.3 * he, yt - 0.05 * he
        yc = ytop - lh / 2.0
        piv = (xc, yc, zl)
        for tag, role, bx, by, bw, bh in (("o", "trim", xc + lw / 2.0 - bar / 2.0, yc, bar, lh), ("i", "blue", xc - lw / 2.0 + bar / 2.0, yc, bar, lh),
                                          ("t", "trim", xc, ytop - bar / 2.0, lw, bar), ("b", "trim", xc, ytop - lh + bar / 2.0, lw, bar)):
            out.append(c.box("loop_%s_r" % tag, "head", (bx, by, zl), (bw, bh, dep), role=role, pivot=piv, rotate=[0.0, 0.0, 8.0], mirror=True))
    if c.p["pins"]:  # leaf pins on the bangs' outer edge, in front of them
        zp = zf - 0.06 * hu ** 0.7
        yp = yt - 0.1 * he
        out += [c.box("leaf_a_r", "head", (0.40 * he, yp, zp), (0.14 * he, 0.09 * he, 0.05 * he), role="teal", rotate=[0.0, 0.0, 35.0], mirror=True),
                c.box("leaf_b_r", "head", (0.30 * he, yp + 0.02 * he, zp - 0.004), (0.12 * he, 0.08 * he, 0.05 * he), role="teal", rotate=[0.0, 0.0, -35.0], mirror=True),
                c.box("pin_r", "head", (0.36 * he, yp - 0.06 * he, zp - 0.008), (0.07 * he, 0.07 * he, 0.05 * he), role="gold", rotate=[0.0, 0.0, 20.0], mirror=True)]
    return out


_RIBBON_FLOOR = 0.03  # plus half the ribbon width: lowest height of a ribbon's centre line


def _ribbon_pts(p0: Vec, segs: Sequence[Tuple[float, float, float]], ln: float, floor: float) -> List[Vec]:
    """Points of a drifting ribbon: every seg is (length, outward angle from vertical in degrees, drift towards the
    back); what would sink below `floor` trails along it instead."""
    pts = [p0]
    for length, ang, drift in segs:
        a = math.radians(ang)
        q = _add(pts[-1], _mul(_norm((math.sin(a), -math.cos(a), drift)), length * ln))
        if q[1] < floor:
            q = (q[0], floor, q[2] + 0.8 * (floor - q[1]))
        pts.append(q)
    return pts


@_preset("ribbons", "Long thin ribbons drifting from the wrists (pairs, down past the hem), the hem sides or the back of the "
                    "head: rigid angled strips in 2-3 segments that follow their bone. `length` stretches them; what would "
                    "sink below the floor trails on it.",
         {"style": _enum("wrist", ("wrist", "hem", "back", "all")), "length": _float(1.0, 0.5, 2.0)},
         main=("silk", "#f4f8ff", "#bcd6ff"), trim=("silk", "#bcd6ff", "#bcd6ff"))
def _ribbons(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    st = c.p["style"]
    ln = c.p["length"] * s
    w0 = 0.085 * math.sqrt(s)
    out: List[dict] = []

    def ribbon(prefix: str, bone: str, p0: Vec, segs, role: str) -> None:
        pts = _ribbon_pts(p0, segs, ln, _RIBBON_FLOOR + w0 / 2.0)
        out.extend(c.path(prefix, bone, pts, [w0 * (1.0 - 0.1 * i) for i in range(len(pts))], [0.02] * len(pts),
                          role=role, suffix="_r", mirror=True, overlap=0.08))

    if st in ("wrist", "all"):
        x, y = b.arm_cx + b.arm_t / 2.0, b.wrist - 0.04
        ribbon("wa", "hand_r", (x + 0.02, y, 0.0), ((0.30, 10.0, 0.08), (0.27, 28.0, 0.14), (0.25, 40.0, 0.2)), "main")
        ribbon("wb", "hand_r", (x - 0.2 * b.arm_t, y, b.arm_t / 2.0 + 0.02), ((0.28, 4.0, 0.22), (0.26, 18.0, 0.3), (0.24, 34.0, 0.36)), "trim")
    if st in ("hem", "all"):
        x = b.leg_cx + b.leg_t / 2.0 + 0.07
        ribbon("ha", "shin_r", (x, b.knee, -0.06), ((0.18, 6.0, -0.02), (0.16, 16.0, 0.0)), "main")
        ribbon("hb", "shin_r", (x, b.knee, 0.07), ((0.18, 4.0, 0.12), (0.16, 12.0, 0.2)), "trim")
    if st in ("back", "all"):
        ribbon("bk", "head", (0.23 * b.he, b.head_bot + 0.2 * b.he, b.hcz + b.he / 2.0 + 0.07),
               ((0.2, 4.0, 0.1), (0.18, 10.0, 0.14), (0.16, 3.0, 0.06)), "main")
    return out


@_preset("pouch", "Belt pouch on one hip with small glass vials standing in it, hung from an optional thin belt. `vials` 0-4, "
                  "`colors` lists the vial colours (cycled), `side` is the character's own side. `arm_clear` squeezes the "
                  "pouch (and the belt) between the centre line and the inner edge of the arm, so it never hides the hand "
                  "or the sleeve.",
         {"side": _enum("right", ("right", "left")), "vials": _int(3, 0, 4),
          "colors": _colors(("#e04ad8", "#58e07a", "#4ad8f0", "#f0c040"), 4), "belt": _bool(True), "arm_clear": _bool(False)},
         main=("leather", "#6b4a2f", "#d4aa32"),
         extra={"flap": ("leather", "#4a3220", "#4a3220"), "cork": ("leather", "#8a6a42", "#8a6a42")})
def _pouch(c: _Ctx) -> List[dict]:
    b, s = c.b, c.s
    sx = 1.0 if c.p["side"] == "right" else -1.0
    clear = bool(c.p["arm_clear"])
    w, h, d = 0.15 * s, 0.18 * s, 0.09 * s
    y_belt = b.abd_bot + 0.045
    top = y_belt - 0.02
    x_out = b.arm_cx - b.arm_t / 2.0 - 0.012  # the arm's inner edge, minus 1.2 cm
    if clear:
        w = min(w, x_out - 0.4 * x_out)
        xc = sx * (x_out - w / 2.0)
    else:
        xc = sx * (0.55 * b.aw + w / 2.0)
    zf = b.front_z(top - h, top + 0.05, xc - w / 2.0, xc + w / 2.0, arms=True) - d / 2.0 - 0.008
    if clear:
        zf -= 0.03  # stands in front of the skirt panels
    out: List[dict] = []
    if c.p["belt"]:
        bw = min(b.aw + 0.045, x_out) if clear else b.aw + 0.045
        out.append(c.box("belt", "torso", (0, y_belt, b.cz), (2 * bw, 0.05 * s, 2 * (b.ad + 0.045)), role="flap"))
    out.append(c.box("body", "torso", (xc, top - h / 2.0, zf), (w, h, d)))
    out.append(c.box("flap", "torso", (xc, top - 0.035 * s + 0.005, zf - 0.004), (w + 0.012, 0.075 * s, d + 0.014), role="flap"))
    out.append(c.box("buckle", "torso", (xc, top - 0.07 * s, zf - d / 2.0 - 0.012), (0.04 * s, 0.04 * s, 0.016), role="trim"))
    n = c.p["vials"]
    cols = c.p["colors"]
    for i in range(n):
        vh = (0.09, 0.075, 0.1, 0.082)[i] * s
        pitch = (0.72 if clear else 0.8) * w / max(n - 1, 1)
        vw = min(0.038 * s, 0.9 * pitch) if clear and n > 1 else 0.038 * s
        vx = xc + (i - (n - 1) / 2.0) * pitch if n > 1 else xc
        vz = zf + (0.012 if i % 2 else -0.008) * s
        vy = top + 0.005 + vh / 2.0
        tilt = (5.0, -4.0, 6.0, -5.0)[i]
        glass = c.box("vial%d" % i, "torso", (vx, vy, vz), (vw, vh, vw), role="main", rotate=[0.0, 0.0, tilt],
                      pivot=(vx, top + 0.005, vz))
        glass["material"], glass["color"], glass["accent"] = "plate_gold", cols[i % len(cols)], cols[i % len(cols)]
        out.append(glass)
        out.append(c.box("cork%d" % i, "torso", (vx - math.sin(math.radians(tilt)) * (vh + 0.011 * s), top + 0.005 + (vh + 0.011 * s) * math.cos(math.radians(tilt)), vz),
                         (min(0.03 * s, vw * 0.85), 0.022 * s, min(0.03 * s, vw * 0.85)), role="cork", rotate=[0.0, 0.0, tilt],
                         pivot=(vx - math.sin(math.radians(tilt)) * (vh + 0.011 * s), top + 0.005 + (vh + 0.011 * s) * math.cos(math.radians(tilt)), vz)))
    return out

# --------------------------------------------------------------------------- relief (boxes from reference silhouettes)
RELIEF_BONES = tuple(_spec.data("rigs")["biped"]["bones"])
RELIEF_VIEWS = ("front", "back", "right", "left")


@_preset("relief", "Boxes cut out of the reference silhouettes (needs the spec's `reference` block): `extrude` = one view + pixel "
         "`rect` meshed into slab boxes (greedy rectangles on a grid of `cell` blocks, `thickness` deep at depth `z`, `mirror` meshes the "
         "character's right half and mirrors it, `layers` stacks slabs `layer_step` apart); `hull` = `views` {front|back: rect, right|left: rect}, "
         "the product of the front x intervals and the side z intervals per band of `cell` blocks. `max_boxes` coarsens `cell` until it fits "
         "(warning relief_coarsened). `align` shifts the extrude grid by fractions of a cell to the best-fitting phase. Boxes follow `bone`; "
         "`rotate`/`pivot` turn the whole part. Slabs carry `edge_texels` thin edge faces and "
         "skip faces hidden by neighbouring boxes. `ref_color` colours each box with its reference average. No `scale`: size comes from the art.",
         {"mode": _enum("extrude", ("extrude", "hull")),
          "view": _enum("front", RELIEF_VIEWS),
          "rect": {"type": "rect", "default": None,
                   "doc": "extrude: [x0, y0, x1, y1] pixel rectangle (ORIGINAL image pixels) of the part in the view; required"},
          "views": {"type": "views", "default": None,
                    "doc": "hull: {\"front\"|\"back\": rect, \"right\"|\"left\": rect}, exactly one of each pair; required"},
          "bone": _enum("chest", RELIEF_BONES),
          "cell": _float(0.08, 0.02, 1.0),
          "thickness": _float(0.06, 0.01, 2.0),
          "z": _float(0.0, -4.0, 4.0),
          "layers": _int(1, 1, 6),
          "layer_step": _float(0.0, 0.0, 1.0),
          "max_boxes": _int(120, 1, 386),
          "mirror": _bool(False),
          "align": _bool(True),
          "edge_texels": _int(1, 0, 8),
          "ref_color": _bool(True),
          "rotate": {"type": "vec3", "default": [0.0, 0.0, 0.0], "doc": "[rx, ry, rz] degrees for the whole part (about `pivot`)"},
          "pivot": {"type": "vec3", "default": None, "doc": "[x, y, z] model coordinates; default: centre of the part's bounding box"}},
         main=("cloth", "primary", "accent"))
def _relief(c: _Ctx) -> List[dict]:
    from . import relief
    return relief.build(c.p, c.views, c.style("main"), c.att_id, c.notes)


del PRESETS["relief"]["params"]["scale"]  # the size comes from the reference calibration, not from a multiplier


# --------------------------------------------------------------------------- public API
def list_presets() -> List[str]:
    return list(PRESETS)


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _color_ok(v) -> bool:
    return isinstance(v, str) and (is_hex(v) or v in _spec.PALETTE_KEYS)


def _rect_errors(path: str, key: str, val: Any) -> List[Any]:
    Err = _spec.Err
    if not isinstance(val, (list, tuple)) or len(val) != 4 or not all(_is_num(c) for c in val):
        return [Err(path, "bad_type", "%s must be [x0, y0, x1, y1] image pixels, got %r" % (key, val))]
    if not (val[0] < val[2] and val[1] < val[3]):
        return [Err(path, "out_of_range", "%s %r must have x0 < x1 and y0 < y1" % (key, list(val)))]
    return []


def _validate_relief_value(path: str, key: str, typ: str, val: Any) -> List[Any]:
    """Value checks of the structured relief parameters: rect [x0,y0,x1,y1], views {view: rect}, vec3 [a,b,c]."""
    Err = _spec.Err
    if typ == "rect":
        return _rect_errors(path, key, val)
    if typ == "vec3":
        if not isinstance(val, (list, tuple)) or len(val) != 3 or not all(_is_num(c) for c in val):
            return [Err(path, "bad_type", "%s must be [x, y, z] numbers, got %r" % (key, val))]
        return []
    if not isinstance(val, dict):
        return [Err(path, "bad_type", "%s must be an object {view: [x0, y0, x1, y1]}" % key)]
    errs: List[Any] = []
    for name, rect in val.items():
        if name not in RELIEF_VIEWS:
            errs.append(Err("%s/%s" % (path, name), "unknown_value", "view %r is not allowed; allowed: %s" % (name, ", ".join(RELIEF_VIEWS))))
        else:
            errs.extend(_rect_errors("%s/%s" % (path, name), "%s.%s" % (key, name), rect))
    return errs


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
        elif typ == "colors":
            if not isinstance(val, list):
                errs.append(Err(path, "bad_type", "%s must be a list of palette keys or #rrggbb strings" % key))
            elif not 1 <= len(val) <= p["max"]:
                errs.append(Err(path, "out_of_range", "%s has %d entries; it needs 1..%d" % (key, len(val), p["max"])))
            else:
                for i, v in enumerate(val):
                    if not isinstance(v, str):
                        errs.append(Err("%s/%d" % (path, i), "bad_type", "%s entries must be palette key or #rrggbb strings" % key))
                    elif not _color_ok(v):
                        errs.append(Err("%s/%d" % (path, i), "bad_color", "%s entry %r must be a palette key (%s) or #rrggbb" % (key, v, ", ".join(_spec.PALETTE_KEYS))))
        elif typ == "bool":
            if not isinstance(val, bool):
                errs.append(Err(path, "bad_type", "%s must be true or false" % key))
        elif typ in ("rect", "views", "vec3"):
            if val is None:
                continue
            errs.extend(_validate_relief_value(path, key, typ, val))
        else:  # float / int
            ok = _is_num(val) if typ == "float" else (isinstance(val, int) and not isinstance(val, bool))
            if not ok:
                errs.append(Err(path, "bad_type", "%s must be %s, got %r" % (key, "a number" if typ == "float" else "an integer", val)))
            elif val < p["range"][0] or val > p["range"][1]:
                errs.append(Err(path, "out_of_range", "%s %r is outside the allowed range %s..%s" % (key, val, p["range"][0], p["range"][1])))
    return errs


def expand(att: dict, proportions: Optional[dict] = None, anatomy: Optional[dict] = None,
           views: Optional[dict] = None, notes: Optional[dict] = None) -> List[dict]:
    """Final box dicts of one attachment on the body with the given proportions (ids prefixed by the attachment id).

    Raises BossForgeError for an invalid attachment. `anatomy` (rigs.json) is accepted for the shape API; the
    positions come from the proportioned base boxes. `views` ({name: reference.View}) feed the `relief` preset;
    `notes` ({"warnings": [], "relief": []}) collects what a preset wants to report."""
    errs = validate_attachment(att)
    if errs:
        raise _spec.BossForgeError(errs[0].path, errs[0].code, errs[0].message)
    name = att["preset"]
    prefix = att.get("id", name)
    boxes = _FUNCS[name](_Ctx(name, att, body_from(proportions), views, notes))
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
