"""Software rasterizer that renders the real Epic Fight mesh with the generated texture (review artifact)."""
from __future__ import annotations

import math
from typing import Optional

from . import efmesh
from .pngio import Canvas, hconcat

PANEL_W = 320
PANEL_H = 480
BG = (36, 40, 50, 255)
ALPHA_MIN = 26
PITCH_DEG = 14.0
VIEWS = (("front", 0.0), ("three_quarter", 35.0), ("back", 180.0))
CUSTOM_VIEWS = (("front", 0.0), ("three_quarter", 35.0), ("side", 90.0), ("back", 180.0))  # wings / tails need a side view
FIT = 0.9  # fraction of the panel the model may fill
MAX_WIDE_PANEL_W = 480  # custom shapes wider than tall get wider view panels, up to this
_L = (-0.3, 0.6, 0.74)
_LL = math.sqrt(sum(c * c for c in _L))
LIGHT = (_L[0] / _LL, _L[1] / _LL, _L[2] / _LL)


def _rotate(v, flip: bool, cy: float, sy: float, cp: float, sp: float):
    x, y, z = v
    if flip:  # bring the character's front to +z (toward the viewer)
        x, z = -x, -z
    x2 = x * cy + z * sy
    z2 = -x * sy + z * cy
    return (x2, y * cp - z2 * sp, y * sp + z2 * cp)


def _projected_extents(mesh, flip: bool, yaw_deg: float):
    """(xmin, xmax, ymin, ymax) of the mesh in screen space (model units) for one view."""
    yaw = math.radians(yaw_deg)
    pitch = math.radians(PITCH_DEG)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    pts = [_rotate(p, flip, cy, sy, cp, sp) for p in mesh.positions]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), max(xs), min(ys), max(ys)


def fit_scale(mesh, front_sign: int, yaws, panel_w: int = PANEL_W, panel_h: int = PANEL_H) -> float:
    """Pixels per model unit so that the given views fit both the panel height and width."""
    flip = front_sign < 0
    best = 1e18
    for yaw in yaws:
        x0, x1, y0, y1 = _projected_extents(mesh, flip, yaw)
        best = min(best, FIT * panel_h / max(y1 - y0, 1e-6), FIT * panel_w / max(x1 - x0, 1e-6))
    return best


def view_panel_width(mesh, front_sign: int, yaws) -> int:
    """Width of every view panel of a custom shape: PANEL_W, widened (up to MAX_WIDE_PANEL_W) when the model is
    wider than the panel allows at its full-height scale (wings, big pauldrons), so faces stay readable."""
    flip = front_sign < 0
    ext = [_projected_extents(mesh, flip, yaw) for yaw in yaws]
    width = max(e[1] - e[0] for e in ext)
    full_height = fit_scale(mesh, front_sign, yaws, panel_w=10 ** 6)
    need = int(math.ceil(width * full_height / FIT))
    return max(PANEL_W, min(MAX_WIDE_PANEL_W, need))


def render_view(mesh, tpl: dict, tex: Canvas, yaw_deg: float, front_sign: int,
                panel_w: int = PANEL_W, panel_h: int = PANEL_H, scale: Optional[float] = None) -> Canvas:
    """One orthographic view. yaw 0 = looking at the character's front.

    ``scale`` (pixels per model unit) switches to fit-to-bounds framing: the view is centred on its own
    projected extents (stock meshes keep the original height-fitted, model-centred framing)."""
    out = Canvas(panel_w, panel_h)
    out.fill(BG)
    od = out.data
    zbuf = [-1e18] * (panel_w * panel_h)
    flip = front_sign < 0
    yaw = math.radians(yaw_deg)
    pitch = math.radians(PITCH_DEG)
    cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
    if scale is None:
        (xmin, ymin, zmin), (xmax, ymax, zmax) = mesh.bounds()
        height = max(ymax - ymin, 1e-6)
        scale = 0.9 * panel_h / height
        cen = _rotate(((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0), flip, cy, sy, cp, sp)
    else:
        x0, x1, y0, y1 = _projected_extents(mesh, flip, yaw_deg)
        cen = ((x0 + x1) / 2.0, (y0 + y1) / 2.0, 0.0)
    pts = []
    for p in mesh.positions:
        x, y, z = _rotate(p, flip, cy, sy, cp, sp)
        pts.append((panel_w / 2.0 + (x - cen[0]) * scale, panel_h / 2.0 - (y - cen[1]) * scale, z))
    nrm = [_rotate(n, flip, cy, sy, cp, sp) for n in mesh.normals]
    tw, th = tex.w, tex.h
    td = tex.data
    top_origin = tpl.get("uv_origin", "top") == "top"
    uvs = mesh.uvs
    for tri in mesh.tris:
        ia, ib, ic = tri.p
        ax, ay, az = pts[ia]
        bx, by, bz = pts[ib]
        cx, cy_, cz = pts[ic]
        n0, n1, n2 = nrm[tri.n[0]], nrm[tri.n[1]], nrm[tri.n[2]]
        nx = n0[0] + n1[0] + n2[0]
        ny = n0[1] + n1[1] + n2[1]
        nz = n0[2] + n1[2] + n2[2]
        nl = math.sqrt(nx * nx + ny * ny + nz * nz)
        if nl < 1e-9:
            continue
        nx, ny, nz = nx / nl, ny / nl, nz / nl
        if nz <= 0.0:
            continue  # backface
        det = (by - cy_) * (ax - cx) + (cx - bx) * (ay - cy_)
        if abs(det) < 1e-9:
            continue
        light = 0.55 + 0.45 * max(0.0, nx * LIGHT[0] + ny * LIGHT[1] + nz * LIGHT[2])
        a0 = (by - cy_) / det
        b0 = (cx - bx) / det
        c0 = -a0 * cx - b0 * cy_
        a1 = (cy_ - ay) / det
        b1 = (ax - cx) / det
        c1 = -a1 * cx - b1 * cy_
        a2 = -a0 - a1
        b2 = -b0 - b1
        c2 = 1.0 - c0 - c1
        u0, v0 = uvs[tri.t[0]]
        u1, v1 = uvs[tri.t[1]]
        u2, v2 = uvs[tri.t[2]]
        if not top_origin:
            v0, v1, v2 = 1.0 - v0, 1.0 - v1, 1.0 - v2
        miny = max(0, int(math.floor(min(ay, by, cy_) - 0.5)))
        maxy = min(panel_h - 1, int(math.ceil(max(ay, by, cy_) + 0.5)))
        for py in range(miny, maxy + 1):
            yy = py + 0.5
            lo = -1e18
            hi = 1e18
            ok = True
            for a, b, c in ((a0, b0, c0), (a1, b1, c1), (a2, b2, c2)):
                v = b * yy + c
                if a > 1e-12:
                    lo = max(lo, -v / a)
                elif a < -1e-12:
                    hi = min(hi, -v / a)
                elif v < -1e-9:
                    ok = False
                    break
            if not ok or lo > hi:
                continue
            x0 = max(0, int(math.ceil(lo - 0.5 - 1e-9)))
            x1 = min(panel_w - 1, int(math.floor(hi - 0.5 + 1e-9)))
            for px in range(x0, x1 + 1):
                xx = px + 0.5
                l0 = a0 * xx + b0 * yy + c0
                l1 = a1 * xx + b1 * yy + c1
                l2 = 1.0 - l0 - l1
                z = l0 * az + l1 * bz + l2 * cz
                zi = py * panel_w + px
                if z <= zbuf[zi]:
                    continue
                u = l0 * u0 + l1 * u1 + l2 * u2
                v = l0 * v0 + l1 * v1 + l2 * v2
                tx = int(u * tw)
                ty = int(v * th)
                if tx < 0:
                    tx = 0
                elif tx >= tw:
                    tx = tw - 1
                if ty < 0:
                    ty = 0
                elif ty >= th:
                    ty = th - 1
                ti = (ty * tw + tx) * 4
                if td[ti + 3] < ALPHA_MIN:
                    continue
                zbuf[zi] = z
                oi = zi * 4
                r = int(td[ti] * light + 0.5)
                g = int(td[ti + 1] * light + 0.5)
                b = int(td[ti + 2] * light + 0.5)
                od[oi] = r if r < 255 else 255
                od[oi + 1] = g if g < 255 else 255
                od[oi + 2] = b if b < 255 else 255
                od[oi + 3] = 255
    return out


def texture_panel(tex: Canvas, panel_h: int = PANEL_H) -> Canvas:
    k = max(1, panel_h // max(tex.h, 1))
    big = tex.scaled(k)
    out = Canvas(big.w, big.h)
    out.fill((60, 64, 76, 255))
    out.blit(big, 0, 0)
    return out


def render_preview(mesh, tpl: dict, tex: Canvas) -> Canvas:
    """Front, three-quarter and back panels plus the texture sheet, side by side.

    Custom shapes get a side panel as well (wings, tails) and one scale that fits every view in both height and
    width, so wide models (wings, big pauldrons) are never clipped and the panels stay comparable; panels widen
    up to MAX_WIDE_PANEL_W for wide models (see view_panel_width)."""
    front_sign, _ = efmesh.frame(mesh, tpl)
    if tpl.get("custom"):
        yaws = [yaw for _, yaw in CUSTOM_VIEWS]
        pw = view_panel_width(mesh, front_sign, yaws)
        scale = fit_scale(mesh, front_sign, yaws, panel_w=pw)  # one scale for all views, so panels stay comparable
        panels = [render_view(mesh, tpl, tex, yaw, front_sign, panel_w=pw, scale=scale) for yaw in yaws]
    else:
        panels = [render_view(mesh, tpl, tex, yaw, front_sign) for _, yaw in VIEWS]
    panels.append(texture_panel(tex))
    return hconcat(panels, gap=4, bg=BG)
