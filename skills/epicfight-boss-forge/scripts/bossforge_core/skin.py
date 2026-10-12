"""Procedural boss skin: paints a texture onto an Epic Fight mesh UV layout (pure Python).

The mesh is either a stock EF mesh (biped / iron golem, fixed 64x64 / 128x128 layout times `texture.scale`)
or a custom box-built model from `shape.build_model` (`tpl["custom"]`; its atlas size is final).

Pipeline (see references/skin-forge.md): rasterize every mesh triangle into texture texels (later triangles
overwrite earlier ones), build a surface context per texel (position, normal, face, region-local coordinates),
shade base texels from the box's own style (custom shapes) or the region material, overlay texels from the
matching `overlay` entry, apply the appearance features in order, dilate one texel into unpainted gaps, and
write the PNG.

Custom templates (`tpl["custom"]`) additionally carry `part_style` (box id -> explicit material/color/accent,
wins over region defaults), `part_regions` values of the form `box:<id>` (a private region whose bounding box is
that single box), and `overlay_to_base` (appearance `overlay` entries are baked into base texels).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import colors, efmesh, features
from .features import PaintState, RegionSel, expand_regions
from .materials import Texel, shade_texel
from .pngio import Canvas, write_png
from .spec import BossForgeError, Err, GenFile
from .preview import render_closeup, render_preview

MAX_TEXTURE_EDGE = 512
MAX_CUSTOM_EDGE = 1024  # custom shapes: `shape.texture.atlas` may be 1024 (relief parts need the room)
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
    if tpl.get("custom"):  # generated atlases have no stray triangles and a per-box texel density
        return list(range(len(mesh.tris)))
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


def _tri_texel_sizes(mesh, w: int, h: int) -> List[float]:
    """Texel edge length in blocks for every triangle (sqrt of surface area / UV area in texels)."""
    pos, uvs = mesh.positions, mesh.uvs
    out = []  # type: List[Optional[float]]
    good = []  # type: List[float]
    for tri in mesh.tris:
        a, b, c = pos[tri.p[0]], pos[tri.p[1]], pos[tri.p[2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        cx, cy, cz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        surface = 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)
        (u0, v0), (u1, v1), (u2, v2) = [(uvs[i][0] * w, uvs[i][1] * h) for i in tri.t]
        uv_area = abs((u1 - u0) * (v2 - v0) - (u2 - u0) * (v1 - v0)) * 0.5
        if surface > 1e-9 and uv_area > 1e-6:
            v = math.sqrt(surface / uv_area)
            out.append(v)
            good.append(v)
        else:
            out.append(None)
    good.sort()
    fallback = good[len(good) // 2] if good else 1.0 / 32.0
    return [fallback if v is None else v for v in out]


def _pattern_step(tb: float) -> int:
    """Texels per 1/32 block (>= 1): weave patterns span this many texels so they keep their size in blocks."""
    return max(1, int(round(1.0 / (32.0 * tb))))


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


def _build_texels(mesh, tpl: dict, cover, overlay_parts, front_sign: int, right_sign: int, tb: float,
                  tri_tb: Optional[List[float]] = None) -> List[Texel]:
    boxes = _region_boxes(mesh, overlay_parts)
    styles = tpl.get("part_style") or {}
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
        t.styled = tri.part in styles
        t.tb = tb if tri_tb is None else tri_tb[ti]
        t.m = _pattern_step(t.tb)
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
        t.u = pz * front_sign if face in ("left", "right", "side") else px * t.vr
        t.v = py
        horiz = hi[2] - lo[2] if face in ("left", "right", "side") else hi[0] - lo[0]
        t.eh = max(horiz, 1e-4)
        t.ev = max(hi[1] - lo[1], 1e-4)
        out.append(t)
    # position inside the atlas rectangle of the (part, face) a texel belongs to: materials such as silk_feather shade
    # along and across a box face whatever its rotation
    rects = {}  # type: Dict[Tuple[str, str], List[int]]
    for t in out:
        r = rects.setdefault((t.part, t.face), [t.x, t.x, t.y, t.y])
        r[0], r[1], r[2], r[3] = min(r[0], t.x), max(r[1], t.x), min(r[2], t.y), max(r[3], t.y)
    for t in out:
        x0, x1, y0, y1 = rects[(t.part, t.face)]
        cols, rows = x1 - x0 + 1, y1 - y0 + 1
        t.ru = (t.x - x0 + 0.5) / cols
        t.rv = (t.y - y0 + 0.5) / rows
        t.rnu = 1.0 / cols
        t.rnv = 1.0 / rows
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


def _entry_with_style(base: dict, style: Optional[dict]) -> dict:
    """A box's own material/color/accent win over the region default entry."""
    if not style:
        return base
    merged = dict(base)
    for k in ("material", "color", "accent"):
        if style.get(k) is not None:
            merged[k] = style[k]
    return merged


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
    t.base_rgb, t.accent_rgb = c, a
    return shade_texel(entry.get("material", "cloth"), t, c, a, seed)


def paint(mesh, tpl: dict, ap: dict, seed: int, front_sign: Optional[int] = None, right_sign: Optional[int] = None, ref_bake=None):
    """Paint a texture for ``mesh`` / template ``tpl`` from a normalized appearance dict.

    Returns ``(canvas, info)``; info = {coverage, base_texels, overlay_texels, overlay_opaque, warnings[, bake]}.
    ``ref_bake`` is an optional ``refbake.BakePlan``: reference pixels are baked into the texels before (`order: first`) or after
    (`order: last`) the appearance features, and its statistics land in ``info["bake"]``.
    """
    custom = bool(tpl.get("custom"))
    aw, ah = tpl["authored_uv"]
    if custom:  # the atlas size is final; texel density is whatever the shape builder chose
        w, h = int(aw), int(ah)
        too_large = ("/shape/texture/density", "lower shape.texture.density")
    else:
        scale = int((ap.get("texture") or {}).get("scale", 2))
        w, h = aw * scale, ah * scale
        too_large = ("/appearance/texture/scale", "lower texture.scale")
    limit = MAX_CUSTOM_EDGE if custom else MAX_TEXTURE_EDGE
    if max(w, h) > limit:
        raise BossForgeError(too_large[0], "texture_too_large",
                             "texture %dx%d exceeds %d; %s" % (w, h, limit, too_large[1]))
    fs, rs = efmesh.frame(mesh, tpl)
    if tpl.get("right_sign") is not None:
        rs = int(tpl["right_sign"])
    if front_sign is not None:
        fs = front_sign
    if right_sign is not None:
        rs = right_sign
    overlay_parts = set(tpl.get("overlay_parts") or [])
    styles = tpl.get("part_style") or {}
    palette = dict(ap.get("palette") or {})
    for key in ("primary", "secondary", "accent", "skin", "hair", "eyes"):
        palette.setdefault(key, "#808080")
    regions = ap.get("regions") or {}
    warnings = []  # type: List[dict]

    cover = _rasterize(mesh, tpl, w, h)
    if custom:
        tb = 1.0 / 32.0
        texels = _build_texels(mesh, tpl, cover, overlay_parts, fs, rs, tb, _tri_texel_sizes(mesh, w, h))
    else:
        tb = 1.0 / (16.0 * scale)
        texels = _build_texels(mesh, tpl, cover, overlay_parts, fs, rs, tb)

    overlay_entries = [e for e in (ap.get("overlay") or []) if isinstance(e, dict)]
    # `arms` / `legs` overlay regions expand to both limbs
    expanded = []  # type: List[_Overlay]
    for e in overlay_entries:
        for r in expand_regions([e.get("region")]):
            ee = dict(e)
            ee["region"] = r
            expanded.append(_Overlay(ee))
    overlay_to_base = bool(tpl.get("overlay_to_base"))
    has_overlay_layer = bool(overlay_parts) or overlay_to_base
    if overlay_entries and not has_overlay_layer:
        warnings.append(Err("/appearance/overlay", "overlay_ignored",
                            "template has no overlay layer; overlay entries are ignored").to_dict())
        expanded = []
    overlay_enabled = bool(ap.get("overlay_enabled", True))
    bake = overlay_to_base or not overlay_enabled  # overlay entries paint base texels instead of overlay islands

    for t in texels:
        if t.layer == "base":
            style = styles.get(t.part)
            t.rgb = _shade_entry(_entry_with_style(_entry_for(regions, t.region, t.ly), style), t, palette, seed)
            if bake and not style:
                for o in expanded:
                    if o.matches(t):
                        t.rgb = _shade_entry(o.entry, t, palette, seed)
                        t.covered = True
        elif overlay_enabled:
            hit = None
            for o in expanded:
                if o.matches(t):
                    hit = o
            if hit is not None:
                t.rgb = _shade_entry(hit.entry, t, palette, seed)

    st = PaintState(texels, palette, seed, tb)
    bake_stats = None
    bake_args = (texels, cover, mesh, (w, h), tpl.get("uv_origin", "top"), fs, rs)
    if ref_bake is not None and ref_bake.cfg["order"] == "first":
        bake_stats = ref_bake.apply(*bake_args)
    for f in ap.get("features") or []:
        if isinstance(f, dict):
            features.apply_feature(st, f)
    if ref_bake is not None and ref_bake.cfg["order"] == "last":
        bake_stats = ref_bake.apply(*bake_args)

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
    if bake_stats is not None:
        warnings.extend(bake_stats.pop("warnings"))
    info = {
        "coverage": round(base_painted / base_total, 4) if base_total else 0.0,
        "base_texels": base_total,
        "overlay_texels": overlay_total,
        "overlay_opaque": overlay_opaque,
        "warnings": warnings,
    }
    if bake_stats is not None:
        info["bake"] = bake_stats
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
def _err_dict(e) -> dict:
    return e.to_dict() if hasattr(e, "to_dict") else dict(e)


def generate_with_report(n: dict, ctx):
    """(files, lang, report) for a normalized spec. Custom shapes (`derived.custom`) paint the box-built model."""
    d = n.get("derived") or {}
    template = d.get("template")
    custom = bool(d.get("custom"))
    shape_warnings = []  # type: List[dict]
    if custom:
        from . import shape  # lazy: stock bosses never need the shape builder
        model = shape.build_model(n, ctx)
        mesh, tpl = model.mesh, model.tpl
        shape_warnings = [_err_dict(w) for w in (model.warnings or [])]
        template = template or "biped"
    else:
        if not template:
            raise BossForgeError("/body/template", "no_template", "no body template could be resolved from size/body/weapon")
        mesh, tpl = efmesh.load_for_template(ctx, template)
    ap = n["appearance"]
    bake = None
    if n.get("reference"):
        from . import refbake  # lazy: bosses without a reference never load it
        bake = refbake.plan_for(n)
    canvas, info = paint(mesh, tpl, ap, int(n["seed"]), ref_bake=bake)
    preview = render_preview(mesh, tpl, canvas)
    tex_bytes = write_png(None, canvas)
    prev_bytes = write_png(None, preview)
    tex_rel = "src/main/resources/assets/%s/textures/entity/%s.png" % (n["mod_id"], n["id"])
    prev_rel = "bossforge/previews/%s_preview.png" % n["id"]
    files = [GenFile(tex_rel, tex_bytes, "texture"), GenFile(prev_rel, prev_bytes, "preview")]
    closeup_rel = None
    if custom:  # wide wings / capes shrink the whole-body views: add a body + face closeup to read faces and ornaments
        closeup = render_closeup(mesh, tpl, canvas)
        if closeup is not None:
            closeup_rel = "bossforge/previews/%s_closeup.png" % n["id"]
            files.append(GenFile(closeup_rel, write_png(None, closeup), "preview"))
    warnings = [_err_dict(w) for w in (d.get("warnings") or [])] + shape_warnings + list(info["warnings"])
    report = {
        "texture": {"path": tex_rel, "width": canvas.w, "height": canvas.h, "coverage": info["coverage"]},
        "preview": prev_rel,
        "template": template,
        "scale": d.get("scale"),
        "hitbox": d.get("hitbox"),
        "warnings": warnings,
    }
    if custom:
        report["custom"] = True
        report["atlas"] = [canvas.w, canvas.h]
        report["shape_warnings"] = shape_warnings
        if closeup_rel is not None:
            report["closeup"] = closeup_rel
    if bake is not None:  # reference bake: statistics plus [reference | model | difference] images per baked view
        cmp_files, cmp_metrics = bake.compare(mesh, canvas, n["id"])
        files.extend(cmp_files)
        report["bake"] = dict(info["bake"], compare={v: dict(m, image=str(Path(ctx.project_root) / m["image"])) for v, m in cmp_metrics.items()})
    return files, {}, report


def generate(n: dict, ctx):
    """(files, lang) for a normalized spec; the skin adds no lang keys."""
    files, lang, _report = generate_with_report(n, ctx)
    return files, lang
