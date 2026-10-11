"""Procedural boss skin: paints a texture onto the real Epic Fight mesh UV layout (pure Python).

Pipeline (see references/skin-forge.md): rasterize every mesh triangle into texture texels (later triangles
overwrite earlier ones), build a surface context per texel (position, normal, face, region-local coordinates),
shade base texels from the region material, overlay texels from the matching `overlay` entry, apply the
appearance features in order, dilate one texel into unpainted gaps, and write the PNG.
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

from . import colors, efmesh, features
from .features import PaintState, RegionSel, expand_regions
from .materials import Texel, shade_texel
from .pngio import Canvas, write_png
from .spec import BossForgeError, Err, GenFile
from .preview import render_preview

MAX_TEXTURE_EDGE = 512
HANDS_MAX_LY = features.HANDS_MAX_LY
FEET_MAX_LY = features.FEET_MAX_LY
DEFAULT_ENTRY = {"material": "cloth", "color": "primary"}
_EPS = 1e-6
_NEIGHBORS = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, -1), (-1, 1), (1, 1))


# --------------------------------------------------------------------------- rasterization
STRETCH_LIMIT = 8.0  # UV area / surface area (both in authored texels) above which a triangle is a mapping glitch


def _paint_order(mesh, tpl: dict) -> List[int]:
    """Triangle indices in painting order. Later wins, so triangles whose UV footprint is wildly larger than
    their surface (stray triangles in the Epic Fight golem arms) go first and never override real surfaces."""
    aw, ah = tpl["authored_uv"]
    stray = []  # type: List[int]
    normal = []  # type: List[int]
    pos, uvs = mesh.positions, mesh.uvs
    for ti, tri in enumerate(mesh.tris):
        a, b, c = pos[tri.p[0]], pos[tri.p[1]], pos[tri.p[2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        surface = 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz) * 256.0
        (u0, v0), (u1, v1), (u2, v2) = [(uvs[i][0] * aw, uvs[i][1] * ah) for i in tri.t]
        uv_area = abs((u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0)) * 0.5
        if uv_area > STRETCH_LIMIT * max(surface, 1e-6):
            stray.append(ti)
        else:
            normal.append(ti)
    return stray + normal


def _rasterize(mesh, tpl: dict, w: int, h: int) -> Dict[Tuple[int, int], Tuple[int, float, float, float]]:
    """texel (x, y) -> (triangle index, barycentric weights); later triangles overwrite earlier ones."""
    origin = tpl.get("uv_origin", "top")
    cover = {}  # type: Dict[Tuple[int, int], Tuple[int, float, float, float]]
    uvs = mesh.uvs
    for ti in _paint_order(mesh, tpl):
        tri = mesh.tris[ti]
        pts = [efmesh.uv_to_pixel(uvs[i][0], uvs[i][1], w, h, origin) for i in tri.t]
        (x0, y0), (x1, y1), (x2, y2) = pts
        det = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
        if abs(det) < 1e-12:
            continue
        minx = max(0, int(math.floor(min(x0, x1, x2))))
        maxx = min(w - 1, int(math.ceil(max(x0, x1, x2))))
        miny = max(0, int(math.floor(min(y0, y1, y2))))
        maxy = min(h - 1, int(math.ceil(max(y0, y1, y2))))
        for py in range(miny, maxy + 1):
            cy = py + 0.5
            for px in range(minx, maxx + 1):
                cx = px + 0.5
                l0 = ((y1 - y2) * (cx - x2) + (x2 - x1) * (cy - y2)) / det
                l1 = ((y2 - y0) * (cx - x2) + (x0 - x2) * (cy - y2)) / det
                l2 = 1.0 - l0 - l1
                if l0 >= -_EPS and l1 >= -_EPS and l2 >= -_EPS:
                    cover[(px, py)] = (ti, l0, l1, l2)
    return cover


def _region_boxes(mesh, overlay_parts) -> Dict[Optional[str], Tuple[List[float], List[float]]]:
    """Per-region bounding box of the region's base (non-overlay) parts."""
    boxes = {}  # type: Dict[Optional[str], Tuple[List[float], List[float]]]
    for tri in mesh.tris:
        if tri.part in overlay_parts:
            continue
        region = mesh.region_of(tri.part)
        lo, hi = boxes.setdefault(region, ([1e18, 1e18, 1e18], [-1e18, -1e18, -1e18]))
        for i in tri.p:
            p = mesh.positions[i]
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]
    return boxes


def _clamp01(v: float) -> float:
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _norm01(v: float, lo: float, hi: float) -> float:
    ext = hi - lo
    if ext < 1e-9:
        return 0.5
    return _clamp01((v - lo) / ext)


def _classify(n, front_sign: int, right_sign: int) -> str:
    dz = n[2] * front_sign
    if dz > 0.7:
        return "front"
    if dz < -0.7:
        return "back"
    if n[1] > 0.7:
        return "top"
    if n[1] < -0.7:
        return "bottom"
    dr = n[0] * right_sign
    if dr > 0.7:
        return "right"
    if dr < -0.7:
        return "left"
    return "side"


def _build_texels(mesh, tpl: dict, cover, overlay_parts, front_sign: int, right_sign: int, tb: float) -> List[Texel]:
    boxes = _region_boxes(mesh, overlay_parts)
    pos = mesh.positions
    nrm = mesh.normals
    out = []  # type: List[Texel]
    for (x, y) in sorted(cover, key=lambda k: (k[1], k[0])):
        ti, l0, l1, l2 = cover[(x, y)]
        tri = mesh.tris[ti]
        pa, pb, pc = pos[tri.p[0]], pos[tri.p[1]], pos[tri.p[2]]
        t = Texel(x, y)
        px = pa[0] * l0 + pb[0] * l1 + pc[0] * l2
        py = pa[1] * l0 + pb[1] * l1 + pc[1] * l2
        pz = pa[2] * l0 + pb[2] * l1 + pc[2] * l2
        t.pos = (px, py, pz)
        t.p = (px * 16.0, py * 16.0, pz * 16.0)
        na, nb, nc = nrm[tri.n[0]], nrm[tri.n[1]], nrm[tri.n[2]]
        nx = na[0] * l0 + nb[0] * l1 + nc[0] * l2
        ny = na[1] * l0 + nb[1] * l1 + nc[1] * l2
        nz = na[2] * l0 + nb[2] * l1 + nc[2] * l2
        nl = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nl < 1e-9:
            n = efmesh.tri_normal(pa, pb, pc)
        else:
            n = (nx / nl, ny / nl, nz / nl)
        t.n = n
        t.part = tri.part
        region = mesh.region_of(tri.part)
        t.region = region
        t.layer = "overlay" if tri.part in overlay_parts else "base"
        t.tb = tb
        box = boxes.get(region)
        if box is None:
            lo, hi = [px - 0.5, py - 0.5, pz - 0.5], [px + 0.5, py + 0.5, pz + 0.5]
        else:
            lo, hi = box
        ax = _norm01(px, lo[0], hi[0])
        az = _norm01(pz, lo[2], hi[2])
        t.ly = _norm01(py, lo[1], hi[1])
        t.lx = ax if front_sign > 0 else 1.0 - ax
        t.lz = az if front_sign > 0 else 1.0 - az
        face = _classify(n, front_sign, right_sign)
        t.face = face
        if face == "back":
            t.fu = 1.0 - t.lx
            t.vr = -float(front_sign)
        elif face in ("front", "top", "bottom"):
            t.fu = t.lx
            t.vr = float(front_sign)
        else:
            t.fu = t.lz
        t.fv = 1.0 - t.ly
        horiz = hi[2] - lo[2] if face in ("left", "right", "side") else hi[0] - lo[0]
        t.eh = max(horiz, 1e-4)
        t.ev = max(hi[1] - lo[1], 1e-4)
        out.append(t)
    return out


# --------------------------------------------------------------------------- painting
def _entry_for(regions: dict, region: Optional[str], ly: float) -> dict:
    if region in ("arm_l", "arm_r"):
        if region in regions:
            return regions[region]
        if ly < HANDS_MAX_LY and "hands" in regions:
            return regions["hands"]
        if "arms" in regions:
            return regions["arms"]
    elif region in ("leg_l", "leg_r"):
        if region in regions:
            return regions[region]
        if ly < FEET_MAX_LY and "feet" in regions:
            return regions["feet"]
        if "legs" in regions:
            return regions["legs"]
    elif region in regions:
        return regions[region]
    return regions.get("torso") or DEFAULT_ENTRY


class _Overlay:
    def __init__(self, entry: dict):
        self.sel = RegionSel([entry.get("region")]) if entry.get("region") else RegionSel([])
        y = entry.get("y") or [0.0, 1.0]
        self.lo = float(y[0])
        self.hi = float(y[1])
        self.entry = entry

    def matches(self, t: Texel) -> bool:
        return self.sel.match(t) and self.lo <= t.ly <= self.hi


def _resolve(palette: dict, ref, default_key: str):
    try:
        return colors.resolve(ref if ref is not None else default_key, palette)
    except ValueError:
        return colors.resolve(default_key, palette)


def _shade_entry(entry: dict, t: Texel, palette: dict, seed: int):
    c = _resolve(palette, entry.get("color"), "primary")
    a = _resolve(palette, entry.get("accent"), "accent")
    return shade_texel(entry.get("material", "cloth"), t, c, a, seed)


def paint(mesh, tpl: dict, ap: dict, seed: int, front_sign: Optional[int] = None, right_sign: Optional[int] = None):
    """Paint a texture for ``mesh`` / template ``tpl`` from a normalized appearance dict.

    Returns ``(canvas, info)``; info = {coverage, base_texels, overlay_texels, overlay_opaque, warnings}.
    """
    scale = int((ap.get("texture") or {}).get("scale", 2))
    aw, ah = tpl["authored_uv"]
    w, h = aw * scale, ah * scale
    if max(w, h) > MAX_TEXTURE_EDGE:
        raise BossForgeError("/appearance/texture/scale", "texture_too_large",
                             "texture %dx%d exceeds %d; lower texture.scale" % (w, h, MAX_TEXTURE_EDGE))
    fs, rs = efmesh.frame(mesh, tpl)
    if front_sign is not None:
        fs = front_sign
    if right_sign is not None:
        rs = right_sign
    tb = 1.0 / (16.0 * scale)
    overlay_parts = set(tpl.get("overlay_parts") or [])
    palette = dict(ap.get("palette") or {})
    for key in ("primary", "secondary", "accent", "skin", "hair", "eyes"):
        palette.setdefault(key, "#808080")
    regions = ap.get("regions") or {}
    warnings = []  # type: List[dict]

    cover = _rasterize(mesh, tpl, w, h)
    texels = _build_texels(mesh, tpl, cover, overlay_parts, fs, rs, tb)

    overlay_entries = [e for e in (ap.get("overlay") or []) if isinstance(e, dict)]
    # `arms` / `legs` overlay regions expand to both limbs
    expanded = []  # type: List[_Overlay]
    for e in overlay_entries:
        for r in expand_regions([e.get("region")]):
            ee = dict(e)
            ee["region"] = r
            expanded.append(_Overlay(ee))
    has_overlay_layer = bool(overlay_parts)
    if overlay_entries and not has_overlay_layer:
        warnings.append(Err("/appearance/overlay", "overlay_ignored",
                            "template has no overlay layer; overlay entries are ignored").to_dict())
        expanded = []
    overlay_enabled = bool(ap.get("overlay_enabled", True))

    for t in texels:
        if t.layer == "base":
            t.rgb = _shade_entry(_entry_for(regions, t.region, t.ly), t, palette, seed)
            if not overlay_enabled:
                for o in expanded:
                    if o.matches(t):
                        t.rgb = _shade_entry(o.entry, t, palette, seed)
        elif overlay_enabled:
            hit = None
            for o in expanded:
                if o.matches(t):
                    hit = o
            if hit is not None:
                t.rgb = _shade_entry(hit.entry, t, palette, seed)

    st = PaintState(texels, palette, seed, tb)
    for f in ap.get("features") or []:
        if isinstance(f, dict):
            features.apply_feature(st, f)

    canvas = Canvas(w, h)
    base_map = {}  # type: Dict[Tuple[int, int], Tuple[int, int, int]]
    base_total = 0
    base_painted = 0
    overlay_total = 0
    overlay_opaque = 0
    for t in texels:
        if t.layer == "base":
            base_total += 1
            if t.rgb is not None:
                base_painted += 1
                base_map[(t.x, t.y)] = t.rgb
                canvas.set(t.x, t.y, (t.rgb[0], t.rgb[1], t.rgb[2], 255))
        else:
            overlay_total += 1
            if t.rgb is not None:
                overlay_opaque += 1
                canvas.set(t.x, t.y, (t.rgb[0], t.rgb[1], t.rgb[2], 255))
    # one dilation pass from painted base texels into texels no triangle covers (avoids edge bleed)
    for y in range(h):
        for x in range(w):
            if (x, y) in cover:
                continue
            for dx, dy in _NEIGHBORS:
                c = base_map.get((x + dx, y + dy))
                if c is not None:
                    canvas.set(x, y, (c[0], c[1], c[2], 255))
                    break
    info = {
        "coverage": round(base_painted / base_total, 4) if base_total else 0.0,
        "base_texels": base_total,
        "overlay_texels": overlay_total,
        "overlay_opaque": overlay_opaque,
        "warnings": warnings,
    }
    return canvas, info


def coverage_of(canvas: Canvas, mesh, tpl: dict) -> float:
    """Fraction of base-layer UV texels that are opaque in ``canvas`` (QA re-check of a written texture)."""
    overlay_parts = set(tpl.get("overlay_parts") or [])
    cover = _rasterize(mesh, tpl, canvas.w, canvas.h)
    total = 0
    opaque = 0
    for (x, y), (ti, _l0, _l1, _l2) in cover.items():
        if mesh.tris[ti].part in overlay_parts:
            continue
        total += 1
        if canvas.get(x, y)[3] > 0:
            opaque += 1
    return opaque / total if total else 0.0


# --------------------------------------------------------------------------- generator entry points
def generate_with_report(n: dict, ctx):
    """(files, lang, report) for a normalized spec."""
    d = n.get("derived") or {}
    template = d.get("template")
    if not template:
        raise BossForgeError("/body/template", "no_template", "no body template could be resolved from size/body/weapon")
    mesh, tpl = efmesh.load_for_template(ctx, template)
    ap = n["appearance"]
    canvas, info = paint(mesh, tpl, ap, int(n["seed"]))
    preview = render_preview(mesh, tpl, canvas)
    tex_bytes = write_png(None, canvas)
    prev_bytes = write_png(None, preview)
    tex_rel = "src/main/resources/assets/%s/textures/entity/%s.png" % (n["mod_id"], n["id"])
    prev_rel = "bossforge/previews/%s_preview.png" % n["id"]
    files = [GenFile(tex_rel, tex_bytes, "texture"), GenFile(prev_rel, prev_bytes, "preview")]
    warnings = list(d.get("warnings") or []) + list(info["warnings"])
    report = {
        "texture": {"path": tex_rel, "width": canvas.w, "height": canvas.h, "coverage": info["coverage"]},
        "preview": prev_rel,
        "template": template,
        "scale": d.get("scale"),
        "hitbox": d.get("hitbox"),
        "warnings": warnings,
    }
    return files, {}, report


def generate(n: dict, ctx):
    """(files, lang) for a normalized spec; the skin adds no lang keys."""
    files, lang, _report = generate_with_report(n, ctx)
    return files, lang
