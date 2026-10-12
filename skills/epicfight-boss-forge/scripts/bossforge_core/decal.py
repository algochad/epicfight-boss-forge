"""`decal` feature: hand-drawn ASCII bitmaps stamped onto UV islands in texel units (pure Python, deterministic).

A decal is a small picture (rows of characters mapped through a palette) that is placed on the targeted texels without
stretching: one bitmap pixel covers `scale` x `scale` texels. The picture reads UPRIGHT on every surface:

* Per UV island (one face of one part) the painter fits the island's local frame from the mesh: the world position as an
  affine function of the texel (x, y), the island's outward normal, `up` = world +Y projected on the surface (on horizontal
  surfaces: the character's front) and `right` = up x normal (what a viewer looking at the surface from outside calls
  right). Both are mapped back into texel space, so islands the exporter rotated by 90 / 180 / 270 degrees or mirrored in UV
  space (a flipped UV island) still show the picture upright. Frames within 12 degrees of the texel axes snap to them
  (exact pixels); islands rotated further are sampled nearest-texel.
* The bitmap is authored for the piece on the character's RIGHT side (+x for `right_sign` 1, viewer-left in the front view).
  `mirror: auto` stamps the mirror image on pieces of the LEFT side (the whole composition, anchors and offsets included, is
  reflected, so a left/right pair is an exact mirror pair), centre pieces (|x| < CENTER_TOL) are never flipped.
* `anchor` places the bitmap in the island's texel bounding box (or in the union of the targeted islands with
  `scope: union`), `offset` moves it in texels (right, down), `repeat` tiles it (strips), `fit: inside` slides it to the
  nearest spot where every opaque pixel lands on the island (tapered islands).

Bitmap files (`decals/<name>.txt`, relative to the spec): `;` at column 0 is a comment, `@<char> <colour>` maps a character
(`#rrggbb`, a palette key, or `none`), every other non-blank line is a row; rows are right-padded with transparent pixels.
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import colors
from .pixelart import bayer  # noqa: F401  (re-exported for callers that dither decals)

RGB = Tuple[int, int, int]

ANCHORS = ("center", "top", "bottom", "left", "right", "top_left", "top_right", "bottom_left", "bottom_right", "head_front")
MIRRORS = ("auto", "none", "flip")
ALIGNS = ("upright", "island")
BLENDS = ("opaque", "multiply")
SCOPES = ("island", "union")
REPEATS = ("x", "y", "xy")
FITS = ("none", "inside")
VIEWS = ("front", "back", "left", "right", "top", "bottom")
PROJECT_FACING = 0.3  # `project`: minimum dot(normal, toward the viewer) of a texel that takes the picture
PROJECT_DEPTH = 0.05  # `project`: blocks behind the nearest texel of a projected cell that still count as the same visible layer
SCALE_RANGE = (1, 8)
OFFSET_RANGE = (-512, 512)
GAP_RANGE = (0, 64)
COUNT_RANGE = (1, 64)
MAX_EDGE = 128  # bitmap width / height limit, in pixels
CENTER_TOL = 0.03  # blocks: a piece closer than this to the symmetry plane is a centre piece (never mirrored)
SNAP_COS = math.cos(math.radians(12.0))  # `align: upright`: frames this close to the texel axes snap to them (exact pixels)
ISLAND_SNAP_COS = -1.0  # `align: island`: every frame snaps to its nearest texel axes (the picture tilts with a tilted island)
FIT_RADIUS = 24  # texels `fit: inside` may slide the decal
STRETCH_LIMIT = 1.25  # texel aspect (blocks per texel along the decal's x / y) beyond which `decal_stretched` is reported
CREAM = (243, 236, 210)
TRANSPARENT = (".", " ")
PALETTE_FALLBACK = "primary"


class DecalError(ValueError):
    """A bitmap the painter cannot read; `code` is the error code `validate` reports."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- bitmap text
def _is_none(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip().lower() in ("", "none", "null", "transparent"))


def parse_text(text: str, source: str = "bitmap") -> Tuple[List[str], Dict[str, Optional[str]]]:
    """(rows, directives) of a decal file / multi-line string. Directives map a character to a colour reference (None =
    transparent). Raises DecalError (`decal_bad_bitmap`)."""
    rows = []  # type: List[str]
    directives = {}  # type: Dict[str, Optional[str]]
    for n, raw in enumerate(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"), 1):
        if raw.startswith(";"):
            continue
        if raw.startswith("@"):
            parts = raw[1:].split()
            if len(parts) != 2 or len(parts[0]) != 1:
                raise DecalError("decal_bad_bitmap", "%s line %d: a palette directive is `@<char> <colour>` (colour = #rrggbb, a palette key or none), got %r" % (source, n, raw))
            directives[parts[0]] = None if _is_none(parts[1]) else parts[1]
            continue
        line = raw.rstrip()
        if not line.strip():
            continue
        rows.append(line)
    return rows, directives


def load_file(file: str, spec_dir: Optional[str]) -> Tuple[List[str], Dict[str, Optional[str]]]:
    """Read `file` (relative to the spec's directory, or the working directory without one). Raises DecalError
    (`decal_missing_file`, `decal_bad_bitmap`)."""
    p = Path(file)
    if not p.is_absolute():
        p = Path(spec_dir or ".") / p
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise DecalError("decal_missing_file", "cannot read decal file %s: %s" % (p, e))
    return parse_text(text, p.name)


def source_of(f: dict, spec_dir: Optional[str]) -> Tuple[List[str], Dict[str, Optional[str]]]:
    """(rows, file directives) of a decal feature: `bitmap` (list of rows or one multi-line string) or `file`."""
    if f.get("bitmap") is not None:
        raw = f["bitmap"]
        if isinstance(raw, str):
            return parse_text(raw, "bitmap")
        if not isinstance(raw, list) or not raw or not all(isinstance(r, str) for r in raw):
            raise DecalError("decal_bad_bitmap", "bitmap must be a non-empty list of row strings (or one string with line breaks)")
        return [r.rstrip() for r in raw if r.strip()], {}
    if isinstance(f.get("file"), str) and f["file"]:
        return load_file(f["file"], spec_dir)
    raise DecalError("required", "a decal needs `bitmap` (rows) or `file` (decals/<name>.txt)")


def _palette_map(f: dict, directives: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    pal = {"#": f.get("color") if f.get("color") is not None else None}  # type: Dict[str, Optional[str]]
    pal["."] = None
    pal[" "] = None
    pal.update(directives)
    given = f.get("palette")
    if isinstance(given, dict):
        for k, v in given.items():
            pal[k] = None if _is_none(v) else v
    return pal


def check_bitmap(f: dict, spec_dir: Optional[str]) -> List[Tuple[str, str, str]]:
    """Problems of a decal feature's bitmap as (subpath, code, message); subpath is relative to the feature ('' = the feature)."""
    try:
        rows, directives = source_of(f, spec_dir)
    except DecalError as e:
        return [("/file" if f.get("file") is not None and e.code != "required" else "", e.code, e.message)]
    out = []  # type: List[Tuple[str, str, str]]
    if not rows:
        return [("/bitmap" if "bitmap" in f else "/file", "decal_bad_bitmap", "the bitmap has no rows")]
    w, h = max(len(r) for r in rows), len(rows)
    if w > MAX_EDGE or h > MAX_EDGE:
        out.append(("/bitmap" if "bitmap" in f else "/file", "decal_bad_bitmap", "the bitmap is %dx%d; the limit is %d x %d pixels" % (w, h, MAX_EDGE, MAX_EDGE)))
    pal = _palette_map(f, directives)
    seen = []  # type: List[str]
    for r in rows:
        for ch in r:
            if ch not in seen:
                seen.append(ch)
    unknown = [ch for ch in seen if ch not in pal]
    if unknown:
        out.append(("/palette", "decal_bad_palette", "no colour for character(s) %s: add `@<char> <colour>` lines to the file or a `palette` entry"
                    % " ".join(repr(c) for c in unknown)))
    return out


def build_bitmap(st, f: dict) -> Optional[List[List[Optional[RGB]]]]:
    """Rows of RGB-or-None for a decal feature (colours resolved through the painter palette); None when it cannot be read
    (the validator reports why)."""
    try:
        rows, directives = source_of(f, getattr(st, "spec_dir", None))
    except DecalError:
        return None
    if not rows:
        return None
    pal = _palette_map(f, directives)
    cache = {}  # type: Dict[str, Optional[RGB]]
    for ch, ref in pal.items():
        if ch == "#" and ref is None and "#" not in directives and not (isinstance(f.get("palette"), dict) and "#" in f["palette"]):
            cache[ch] = CREAM
        elif ref is None:
            cache[ch] = None
        else:
            cache[ch] = st.color(ref, PALETTE_FALLBACK)
    w = max(len(r) for r in rows)
    out = []  # type: List[List[Optional[RGB]]]
    for r in rows:
        out.append([cache.get(ch) for ch in r.ljust(w)])
    return out


# --------------------------------------------------------------------------- island frames
def _dot(a, b) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _unit(v):
    ln = math.sqrt(_dot(v, v))
    return (v[0] / ln, v[1] / ln, v[2] / ln) if ln > 1e-12 else None


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _solve2(a, b, w):
    """(alpha, beta) minimising |alpha a + beta b - w|: the texel-space direction of the world vector w."""
    gaa, gab, gbb = _dot(a, a), _dot(a, b), _dot(b, b)
    ra, rb = _dot(a, w), _dot(b, w)
    det = gaa * gbb - gab * gab
    if det <= 1e-18:
        return None
    return ((gbb * ra - gab * rb) / det, (gaa * rb - gab * ra) / det)


def _normalized2(v) -> Optional[Tuple[float, float]]:
    ln = math.hypot(v[0], v[1])
    return (v[0] / ln, v[1] / ln) if ln > 1e-12 else None


def surface_axes(a, b, n, front_sign: int, snap_cos: float = SNAP_COS):
    """(right, down, lattice, aspect): the texel-space unit vectors (x right, y down) along which a viewer looking at the
    surface from outside sees right and down, whether they are the texel axes (`lattice`), and the texel aspect (world length
    of one texel along right / along down). `a`, `b` = world displacement per texel in x / y, `n` = outward normal."""
    up_w = (0.0, 1.0, 0.0)
    k = _dot(up_w, n)
    up_w = (up_w[0] - n[0] * k, up_w[1] - n[1] * k, up_w[2] - n[2] * k)
    up = _unit(up_w) if math.sqrt(_dot(up_w, up_w)) > 0.3 else None
    if up is None:  # a (near) horizontal surface: up is toward the front of the character
        fw = (0.0, 0.0, float(front_sign))
        k = _dot(fw, n)
        up = _unit((fw[0] - n[0] * k, fw[1] - n[1] * k, fw[2] - n[2] * k))
    if up is None:
        return (1.0, 0.0), (0.0, 1.0), True, 1.0
    right_w = _unit(_cross(up, n))
    if right_w is None:
        return (1.0, 0.0), (0.0, 1.0), True, 1.0
    r2 = _solve2(a, b, right_w)
    u2 = _solve2(a, b, up)
    r = _normalized2(r2) if r2 else None
    d = _normalized2((-u2[0], -u2[1])) if u2 else None
    if r is None or d is None:
        return (1.0, 0.0), (0.0, 1.0), True, 1.0
    lattice = False
    best = max(((1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)), key=lambda ax: r[0] * ax[0] + r[1] * ax[1])
    if r[0] * best[0] + r[1] * best[1] >= snap_cos:
        r = best
        lattice = True
    rot = (-r[1], r[0])
    d = rot if (d[0] * rot[0] + d[1] * rot[1]) >= 0.0 else (-rot[0], -rot[1])
    lr = math.sqrt(_dot((r[0] * a[0] + r[1] * b[0], r[0] * a[1] + r[1] * b[1], r[0] * a[2] + r[1] * b[2]),
                        (r[0] * a[0] + r[1] * b[0], r[0] * a[1] + r[1] * b[1], r[0] * a[2] + r[1] * b[2])))
    ld = math.sqrt(_dot((d[0] * a[0] + d[1] * b[0], d[0] * a[1] + d[1] * b[1], d[0] * a[2] + d[1] * b[2]),
                        (d[0] * a[0] + d[1] * b[0], d[0] * a[1] + d[1] * b[1], d[0] * a[2] + d[1] * b[2])))
    aspect = lr / ld if ld > 1e-12 else 1.0
    return r, d, lattice, aspect


def fit_tangents(texels):
    """(mean normal, a, b) of a group of texels: the least-squares affine map world = p0 + a x + b y over the texel centres, or
    None when the texels do not span a plane (a line, a single texel)."""
    m = len(texels)
    if m < 3:
        return None
    mx = my = 0.0
    mp = [0.0, 0.0, 0.0]
    sn = [0.0, 0.0, 0.0]
    for t in texels:
        mx += t.x + 0.5
        my += t.y + 0.5
        p = t.pos
        mp[0] += p[0]
        mp[1] += p[1]
        mp[2] += p[2]
        n = t.n
        sn[0] += n[0]
        sn[1] += n[1]
        sn[2] += n[2]
    mx /= m
    my /= m
    mp = [v / m for v in mp]
    sxx = sxy = syy = 0.0
    sxp = [0.0, 0.0, 0.0]
    syp = [0.0, 0.0, 0.0]
    for t in texels:
        dx = t.x + 0.5 - mx
        dy = t.y + 0.5 - my
        sxx += dx * dx
        sxy += dx * dy
        syy += dy * dy
        p = t.pos
        for k in range(3):
            dp = p[k] - mp[k]
            sxp[k] += dx * dp
            syp[k] += dy * dp
    det = sxx * syy - sxy * sxy
    if det <= 1e-9 * max(sxx * syy, 1e-9):
        return None
    a = tuple((syy * sxp[k] - sxy * syp[k]) / det for k in range(3))
    b = tuple((sxx * syp[k] - sxy * sxp[k]) / det for k in range(3))
    n = _unit(sn)
    if n is None:
        n = _unit(_cross(a, b))
        if n is None:
            return None
    return n, a, b


class Frame:
    """The texels of one island (or of a union) in the decal's upright frame.

    `pts` = [(texel, s, t)] with s running right and t running down as a viewer sees the surface from outside, origin at the
    top-left corner of the bounding box (a texel centre of a `lattice` frame is at half-integers); `w`, `h` = bbox size in
    texels. A frame built with `flip` is already reflected (s -> w - s), so everything placed on it mirrors with it."""
    __slots__ = ("pts", "w", "h", "lattice", "flip", "aspect", "cover", "texels")

    def __init__(self, pts, w, h, lattice, flip, aspect, texels):
        self.pts = pts
        self.w = w
        self.h = h
        self.lattice = lattice
        self.flip = flip
        self.aspect = aspect
        self.texels = texels
        self.cover = None  # type: Optional[Dict[int, int]]  # lattice frames: row -> bit mask of covered columns


def mean_x(texels) -> float:
    return sum(t.pos[0] for t in texels) / max(1, len(texels))


def wants_flip(mode: str, texels, right_sign: int) -> bool:
    if mode == "flip":
        return True
    if mode == "none":
        return False
    x = mean_x(texels) * (1 if right_sign >= 0 else -1)
    return x < -CENTER_TOL


def build_frame(texels, front_sign: int, right_sign: int, mirror: str = "auto", union: bool = False, align: str = "upright") -> Optional[Frame]:
    """Upright frame of a group of texels (an island, or `union` = several islands projected through world space)."""
    got = fit_tangents(texels)
    if got is None:
        return None
    n, a, b = got
    flip = wants_flip(mirror, texels, right_sign)
    if union:
        return _union_frame(texels, n, front_sign, flip)
    r, d, lattice, aspect = surface_axes(a, b, n, front_sign, ISLAND_SNAP_COS if align == "island" else SNAP_COS)
    raw = []
    smin = tmin = 1e18
    smax = tmax = -1e18
    for t in texels:
        x, y = t.x + 0.5, t.y + 0.5
        s = x * r[0] + y * r[1]
        v = x * d[0] + y * d[1]
        raw.append((t, s, v))
        smin = min(smin, s)
        smax = max(smax, s)
        tmin = min(tmin, v)
        tmax = max(tmax, v)
    w = int(math.floor(smax - smin + 1.0 + 1e-6))
    h = int(math.floor(tmax - tmin + 1.0 + 1e-6))
    pts = []
    for t, s, v in raw:
        s = s - smin + 0.5
        v = v - tmin + 0.5
        if lattice:
            s, v = round(s - 0.5) + 0.5, round(v - 0.5) + 0.5
        if flip:
            s = w - s
        pts.append((t, s, v))
    return _with_cover(Frame(pts, w, h, lattice, flip, aspect, texels))


def _union_frame(texels, n, front_sign: int, flip: bool) -> Optional[Frame]:
    up = (0.0, 1.0, 0.0)
    k = _dot(up, n)
    upv = (up[0] - n[0] * k, up[1] - n[1] * k, up[2] - n[2] * k)
    ul = math.sqrt(_dot(upv, upv))
    if ul < 0.3:
        fw = (0.0, 0.0, float(front_sign))
        k = _dot(fw, n)
        upv = (fw[0] - n[0] * k, fw[1] - n[1] * k, fw[2] - n[2] * k)
    up_u = _unit(upv)
    if up_u is None:
        return None
    right = _unit(_cross(up_u, n))
    if right is None:
        return None
    sizes = sorted(t.tb for t in texels)
    unit = sizes[len(sizes) // 2]
    raw = []
    smin = tmin = 1e18
    smax = tmax = -1e18
    for t in texels:
        p = t.pos
        s = _dot(p, right) / unit
        v = -_dot(p, up_u) / unit
        raw.append((t, s, v))
        smin = min(smin, s)
        smax = max(smax, s)
        tmin = min(tmin, v)
        tmax = max(tmax, v)
    w = int(math.floor(smax - smin + 1.0 + 1e-6))
    h = int(math.floor(tmax - tmin + 1.0 + 1e-6))
    pts = []
    for t, s, v in raw:
        s = s - smin + 0.5
        v = v - tmin + 0.5
        pts.append((t, w - s if flip else s, v))
    return Frame(pts, w, h, False, flip, 1.0, texels)


def _with_cover(fr: Frame) -> Frame:
    cover = {}  # type: Dict[int, int]
    for _t, s, v in fr.pts:
        j = int(v)
        cover[j] = cover.get(j, 0) | (1 << int(s))
    fr.cover = cover
    return fr


# --------------------------------------------------------------------------- bitmap placement
def tiled(bm: List[List[Optional[RGB]]], nx: int, ny: int, gap_x: int, gap_y: int) -> List[List[Optional[RGB]]]:
    """The bitmap repeated nx x ny times with transparent gaps between the copies."""
    w = len(bm[0])
    out = []  # type: List[List[Optional[RGB]]]
    for j in range(ny):
        for row in bm:
            line = []  # type: List[Optional[RGB]]
            for i in range(nx):
                if i:
                    line.extend([None] * gap_x)
                line.extend(row)
            out.append(line)
        if j < ny - 1:
            out.extend([[None] * (nx * w + (nx - 1) * gap_x) for _ in range(gap_y)])
    return out


def _span(frame_len: int, size: int, gap: int, whole: bool) -> int:
    """How many copies of `size` (spaced by `gap`) the length takes: whole copies only, or one more partial copy."""
    if size <= 0:
        return 1
    fit = (frame_len + gap) // (size + gap)
    if not whole and (frame_len + gap) % (size + gap) > 0:
        fit += 1
    return max(1, fit)


def place(fr: Frame, bw: int, bh: int, anchor: str, dx: int, dy: int) -> Tuple[int, int]:
    """Top-left texel (x0, y0) of a bw x bh decal in the frame for `anchor` plus offset (floor-centred: an odd difference puts
    the extra texel on the right / bottom, and a mirrored frame reflects the result, so pairs stay exact mirrors)."""
    if anchor == "head_front":
        anchor = "top"
    if "left" in anchor:
        x0 = 0
    elif "right" in anchor:
        x0 = fr.w - bw
    else:
        x0 = (fr.w - bw) // 2
    if "top" in anchor:
        y0 = 0
    elif "bottom" in anchor:
        y0 = fr.h - bh
    else:
        y0 = (fr.h - bh) // 2
    return x0 + dx, y0 + dy


def _popcount(v: int) -> int:
    return bin(v).count("1")


def _row_masks(bm, scale: int) -> List[int]:
    """Bit mask over texel columns of every texel row of the scaled bitmap (opaque pixels only)."""
    out = []  # type: List[int]
    for row in bm:
        m = 0
        for i, c in enumerate(row):
            if c is not None:
                m |= ((1 << scale) - 1) << (i * scale)
        out.extend([m] * scale)
    return out


def hits(cover: Dict[int, int], masks: Sequence[int], x0: int, y0: int) -> int:
    """Opaque texels of the placed decal that land on island texels (lattice frames)."""
    tot = 0
    for j, m in enumerate(masks):
        row = cover.get(y0 + j)
        if not row or not m:
            continue
        tot += _popcount((m << x0 if x0 >= 0 else m >> -x0) & row)
    return tot


def fit_inside(fr: Frame, masks: Sequence[int], x0: int, y0: int) -> Tuple[int, int, bool]:
    """Nearest (x0, y0) to the start position with every opaque texel on the island, searched in rings of growing Chebyshev
    distance up to FIT_RADIUS; (x0, y0, False) when there is none."""
    total = sum(_popcount(m) for m in masks)
    cover = fr.cover or {}
    if hits(cover, masks, x0, y0) == total:
        return x0, y0, True
    for r in range(1, FIT_RADIUS + 1):
        best = None
        for oy in range(-r, r + 1):
            for ox in range(-r, r + 1):
                if max(abs(ox), abs(oy)) != r:
                    continue
                if hits(cover, masks, x0 + ox, y0 + oy) == total:
                    cand = (ox * ox + oy * oy, oy, ox)
                    if best is None or cand < best:
                        best = cand
        if best is not None:
            return x0 + best[2], y0 + best[1], True
    return x0, y0, False


def stamp(st, fr: Frame, bm, scale: int, x0: int, y0: int, blend: str, opacity: float) -> Tuple[int, int]:
    """Paint the bitmap onto the frame's texels. Returns (painted texels, texels of opaque pixels that fell off the island)."""
    bh, bw = len(bm), len(bm[0])
    painted = 0
    opaque = sum(1 for row in bm for c in row if c is not None) * scale * scale
    inv = 1.0 / scale
    multiply = blend == "multiply"
    for t, s, v in fr.pts:
        i = int(math.floor((s - x0) * inv + 1e-9))
        j = int(math.floor((v - y0) * inv + 1e-9))
        if i < 0 or j < 0 or i >= bw or j >= bh:
            continue
        c = bm[j][i]
        if c is None or t.rgb is None:
            continue
        base = t.rgb
        if multiply:
            c = (base[0] * c[0] // 255, base[1] * c[1] // 255, base[2] * c[2] // 255)
        if opacity < 1.0:
            c = colors.mix(base, c, opacity)
        t.rgb = c
        painted += 1
    return painted, max(0, opaque - painted)


def _auto_scale(fr_w: int, fr_h: int, bw: int, bh: int) -> int:
    return max(1, min(fr_w // max(bw, 1), fr_h // max(bh, 1)))


MIN_ISLAND_TEXELS = 12  # smaller blobs (slivers, hidden rims) take no decal


def islands_of(texels) -> List[List]:
    """The targeted texels grouped by UV island (`Texel.isl`), each island split into its 8-connected blobs in the atlas, so
    two faces an exporter chained through a shared UV corner are still two islands. Ordered by their top-left texel."""
    by_isl = {}  # type: Dict[int, List]
    for t in texels:
        by_isl.setdefault(t.isl, []).append(t)
    out = []  # type: List[List]
    for k in sorted(by_isl):
        cells = {(t.x, t.y): t for t in by_isl[k]}
        seen = set()
        for key in sorted(cells, key=lambda p: (p[1], p[0])):
            if key in seen:
                continue
            seen.add(key)
            stack = [key]
            blob = []
            while stack:
                cx, cy = stack.pop()
                blob.append(cells[(cx, cy)])
                for ddx in (-1, 0, 1):
                    for ddy in (-1, 0, 1):
                        q = (cx + ddx, cy + ddy)
                        if q in cells and q not in seen:
                            seen.add(q)
                            stack.append(q)
            if len(blob) >= MIN_ISLAND_TEXELS:
                blob.sort(key=lambda t: (t.y, t.x))
                out.append(blob)
    return out


# --------------------------------------------------------------------------- the feature
def _num(v, default, lo, hi):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return min(hi, max(lo, v))


def view_basis(view: str, front_sign: int, right_sign: int):
    """(toward the viewer, picture right, picture up) world unit vectors of a view of the model (`project`): `front` is seen from the
    character's front, `right` from the character's right side; up is +Y, on top / bottom views it points to the character's front."""
    f, rs = float(front_sign), float(right_sign)
    toward = {"front": (0.0, 0.0, f), "back": (0.0, 0.0, -f), "right": (rs, 0.0, 0.0), "left": (-rs, 0.0, 0.0),
              "top": (0.0, 1.0, 0.0), "bottom": (0.0, -1.0, 0.0)}[view]
    up = (0.0, 0.0, f) if view in ("top", "bottom") else (0.0, 1.0, 0.0)
    return toward, _unit(_cross(up, toward)), up


def _project_point(view: str, at) -> Tuple[float, float, float]:
    """World point of `at`: [x, y] for front / back, [z, y] for left / right, [x, z] for top / bottom."""
    if view in ("front", "back"):
        return (float(at[0]), float(at[1]), 0.0)
    if view in ("left", "right"):
        return (0.0, float(at[1]), float(at[0]))
    return (float(at[0]), 0.0, float(at[1]))


def _apply_projected(st, f: dict, bm, texels: List, proj: dict) -> None:
    """`project`: look every targeted texel that faces the viewer up in the bitmap by its world position (orthographic projection along
    the view, nearest cell, no resampling). The bitmap's `anchor` sits at world position `at`; one pixel is `pixel` blocks (auto =
    the median texel size of the targeted texels times `scale`). Only the nearest layer of each projected cell (within `depth`
    blocks) is stamped, so inner walls and the far side of a cone stay clean. `mirror: auto` also stamps the reflection x -> -x of
    the whole decal (a centred decal is not doubled)."""
    view = proj.get("view", "front")
    toward, right, up = view_basis(view, st.front_sign, st.right_sign)
    facing = _num(proj.get("facing"), PROJECT_FACING, -1.0, 1.0)
    depth_tol = _num(proj.get("depth"), PROJECT_DEPTH, 0.0, 4.0)
    at = proj.get("at")
    anchor = f.get("anchor", "center")
    mirror = f.get("mirror", "auto")
    blend = f.get("blend", "opaque")
    opacity = _num(f.get("opacity"), 1.0, 0.0, 1.0)
    pic = bm
    rep = f.get("repeat")
    if rep:
        gap = int(_num(f.get("gap"), 0, GAP_RANGE[0], GAP_RANGE[1]))
        count = int(_num(f.get("count"), 1, COUNT_RANGE[0], COUNT_RANGE[1]))
        pic = tiled(bm, count if "x" in rep else 1, count if "y" in rep else 1, gap, gap)
    if mirror == "flip":
        pic = [list(reversed(row)) for row in pic]
    bw, bh = len(pic[0]), len(pic)
    pixel = proj.get("pixel", "auto")
    sizes = sorted(t.tb for t in texels)
    if pixel == "auto" or isinstance(pixel, bool) or not isinstance(pixel, (int, float)):
        pixel = sizes[len(sizes) // 2] * _num(f.get("scale"), 1, SCALE_RANGE[0], SCALE_RANGE[1])
    pixel = float(pixel)
    p = _project_point(view, at)
    u_at = _dot(p, right)
    v_at = _dot(p, up)
    u_left = u_at - (0.0 if "left" in anchor else bw * pixel if "right" in anchor else bw * pixel / 2.0)
    v_top = v_at + (0.0 if "top" in anchor else bh * pixel if "bottom" in anchor else bh * pixel / 2.0)
    # variants: the decal as placed, and its reflection x -> -x (texel positions and normals reflected instead of the picture)
    centred = view in ("front", "back", "top", "bottom") and abs(p[0]) < CENTER_TOL
    variants = [1.0] if (mirror in ("none", "flip") or centred) else [1.0, -1.0]
    cell = pixel * 0.5
    hits = {}  # type: Dict[int, int]
    facing_texels = 0
    for k, sx in enumerate(variants):
        cand = []  # (texel, u, v, depth)
        nearest = {}  # type: Dict[Tuple[int, int], float]
        for t in texels:
            n = t.n
            if sx * n[0] * toward[0] + n[1] * toward[1] + n[2] * toward[2] < facing:
                continue
            q = (sx * t.pos[0], t.pos[1], t.pos[2])
            u, v, d = _dot(q, right), _dot(q, up), _dot(q, toward)
            key = (int(math.floor(u / cell)), int(math.floor(v / cell)))
            if d > nearest.get(key, -1e18):
                nearest[key] = d
            cand.append((t, u, v, d, key))
        for t, u, v, d, key in cand:
            if d < nearest[key] - depth_tol:
                continue
            facing_texels += 1
            i = int(math.floor((u - u_left) / pixel + 1e-7))  # a texel centre exactly on a cell edge belongs to the cell after it
            j = int(math.floor((v_top - v) / pixel + 1e-7))
            if i < 0 or j < 0 or i >= bw or j >= bh or id(t) in hits:
                continue
            c = pic[j][i]
            if c is None or t.rgb is None:
                continue
            base = t.rgb
            if blend == "multiply":
                c = (base[0] * c[0] // 255, base[1] * c[1] // 255, base[2] * c[2] // 255)
            if opacity < 1.0:
                c = colors.mix(base, c, opacity)
            t.rgb = c
            hits[id(t)] = k
    if not facing_texels:
        st.warn("decal_no_target", "no targeted texel faces the %s view (dot(normal, toward the viewer) >= %.2f)" % (view, facing))
    elif not hits:
        st.warn("decal_no_target", "the projected decal at %s covers no targeted texel (check `at`, `view` and `pixel`)" % (list(at),))


def apply(st, f: dict, texels: List) -> None:
    """Stamp the decal `f` on `texels` (already selected by region and face): one frame per UV island (`scope: island`) or one
    for all of them (`scope: union`); see the module docstring for the placement rules. With `project` the picture is projected
    through world space instead (`_apply_projected`)."""
    bm = build_bitmap(st, f)
    if bm is None:
        return
    if not texels:
        st.warn("decal_no_target", "the decal's region / faces match no painted texel")
        return
    if isinstance(f.get("project"), dict):
        _apply_projected(st, f, bm, texels, f["project"])
        return
    anchor = f.get("anchor", "center")
    mirror = f.get("mirror", "auto")
    blend = f.get("blend", "opaque")
    opacity = _num(f.get("opacity"), 1.0, 0.0, 1.0)
    off = f.get("offset") or [0, 0]
    dx, dy = int(round(off[0])), int(round(off[1]))
    scale_raw = f.get("scale", "auto" if anchor == "head_front" else 1)
    union = f.get("scope", "island") == "union"
    fit = f.get("fit", "none") == "inside"
    partial = bool(f.get("partial", False))
    skip_small = bool(f.get("skip_small", False))
    rep = f.get("repeat")
    gap = int(_num(f.get("gap"), 0, GAP_RANGE[0], GAP_RANGE[1]))
    count = f.get("count")
    groups = [list(texels)] if union else islands_of(texels)  # type: List[List]
    too_big = []  # type: List[str]
    stretched = []  # type: List[float]
    lost_total = 0
    lost_islands = 0
    placed = 0
    skipped = 0
    bw0, bh0 = len(bm[0]), len(bm)
    for group in groups:
        fr = build_frame(group, st.front_sign, st.right_sign, mirror, union, f.get("align", "upright"))
        if fr is None:
            continue
        scale = _auto_scale(fr.w, fr.h, bw0, bh0) if scale_raw == "auto" else int(scale_raw)
        if skip_small and (bw0 * scale > fr.w or bh0 * scale > fr.h):
            skipped += 1  # an island smaller than the bitmap (a sleeve next to a torso) takes no copy and no warning
            continue
        if anchor == "head_front":
            exact = fr.w == bw0 * scale and fr.h == bh0 * scale
            st.info["face"] = {"texels": [fr.w, fr.h], "bitmap": [bw0, bh0], "scale": scale, "texels_per_art_pixel": round(fr.w / float(bw0), 3),
                               "exact": exact}
            if not exact:
                st.warn("face_grid", "the head front is %dx%d texels but the face bitmap is %dx%d pixels at scale %d: the art pixels do not land on whole "
                        "texels (use a head of %dx%d texels per face, or a bitmap with %dx%d pixels)" % (fr.w, fr.h, bw0, bh0, scale, bw0 * scale, bh0 * scale, fr.w // scale, fr.h // scale))
        pic = bm
        if rep:
            gap_px = (gap + scale - 1) // scale  # the gap is given in texels, tiles sit on the bitmap pixel grid
            nx = ny = 1
            if "x" in rep:
                nx = int(count) if count is not None else _span(fr.w, bw0 * scale, gap_px * scale, not partial)
            if "y" in rep:
                ny = int(count) if count is not None else _span(fr.h, bh0 * scale, gap_px * scale, not partial)
            pic = tiled(bm, nx, ny, gap_px, gap_px)
        pw, ph = len(pic[0]) * scale, len(pic) * scale
        if pw > fr.w or ph > fr.h:
            too_big.append("%dx%d texels in %dx%d" % (pw, ph, fr.w, fr.h))
        if len(group) >= 48 and (fr.aspect < 1.0 / STRETCH_LIMIT or fr.aspect > STRETCH_LIMIT):
            stretched.append(fr.aspect)
        x0, y0 = place(fr, pw, ph, anchor, dx, dy)
        if fit and fr.lattice:
            x0, y0, _ok = fit_inside(fr, _row_masks(pic, scale), x0, y0)
        painted, lost = stamp(st, fr, pic, scale, x0, y0, blend, opacity)
        placed += 1
        if not fr.lattice and lost <= 0.15 * (painted + lost):
            lost = 0  # a rotated island samples the bitmap nearest-texel: a few texels more or less are aliasing, not clipping
        if lost and not partial:
            lost_total += lost
            lost_islands += 1
    if too_big and not rep:
        st.warn("decal_too_big", "the bitmap is larger than the island it is stamped on (%s; %d of %d islands): the overflow is clipped"
                % (too_big[0], len(too_big), len(groups)))
    if lost_total:
        st.warn("decal_partial", "%d opaque texels of the decal fall outside the island (%d islands): move it with `offset` / `anchor`, use `fit: inside` "
                "or a smaller bitmap" % (lost_total, lost_islands))
    if stretched:
        st.warn("decal_stretched", "the UV island's texels are %.2f:1 stretched (width:height), so the decal looks stretched there" % stretched[0])
    if not placed and not skipped:
        st.warn("decal_no_target", "no targeted UV island has enough texels to place the decal")
