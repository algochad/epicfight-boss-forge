"""Foreground masks for reference images: `auto` (background colour model from the crop border + flood fill from the
border) and `color` (explicit background colours). Stdlib only; colours are classified through a 6-bit-per-channel
look-up table so a megapixel image costs ~0.5 s."""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

from . import refmask

Rect = Tuple[int, int, int, int]
Color = Tuple[int, int, int]

AUTO_MARGIN = 6.0  # extra RGB distance added to every background colour ball (the `tolerance` of mode auto)
RING = 3  # border ring thickness (px) sampled for the background model
MAX_SAMPLES = 6000
K_CLUSTERS = 8
CHAIN_DISTANCE = 42.0  # a cluster joins the background when it is this close (RGB) to an accepted one
MIN_CLUSTER_MASS = 0.02
SEAL = 1  # default `seal` of the mask spec: the background flood may not cross outline gaps up to 2 x SEAL px wide


def _quant_keys(data: bytearray, w: int, h: int) -> List[int]:
    """6-bit-per-channel key of every pixel (index y*w+x)."""
    t = bytes(v >> 2 for v in range(256))
    r = data[0::4].translate(t)
    g = data[1::4].translate(t)
    b = data[2::4].translate(t)
    return [(x << 12) | (y << 6) | z for x, y, z in zip(r, g, b)]


def _key_color(k: int) -> Color:
    return (((k >> 12) & 63) * 4 + 2, ((k >> 6) & 63) * 4 + 2, (k & 63) * 4 + 2)


def _ring_samples(data: bytearray, w: int, crop: Rect, t: int = RING) -> List[Color]:
    x0, y0, x1, y1 = crop
    pts: List[Color] = []
    step = max(1, ((x1 - x0) * 2 + (y1 - y0) * 2) * t // MAX_SAMPLES)
    cells = set()
    for k in range(min(t, (x1 - x0 + 1) // 2, (y1 - y0 + 1) // 2)):
        for x in range(x0, x1, step):
            cells.add((x, y0 + k))
            cells.add((x, y1 - 1 - k))
        for y in range(y0, y1, step):
            cells.add((x0 + k, y))
            cells.add((x1 - 1 - k, y))
    for x, y in sorted(cells):
        i = (y * w + x) * 4
        pts.append((data[i], data[i + 1], data[i + 2]))
    return pts


def _kmeans(pts: List[Color], k: int, iters: int = 10) -> List[Tuple[Color, int, float]]:
    """Deterministic k-means in RGB. Returns [(centre, count, rms radius)] sorted by count (largest first)."""
    order = sorted(range(len(pts)), key=lambda i: (pts[i][0] * 299 + pts[i][1] * 587 + pts[i][2] * 114, i))
    k = max(1, min(k, len(pts)))
    cents = [pts[order[(2 * j + 1) * len(pts) // (2 * k)]] for j in range(k)]
    cents = [tuple(float(c) for c in p) for p in cents]
    assign = [0] * len(pts)
    for _ in range(iters):
        sums = [[0.0, 0.0, 0.0, 0] for _ in range(k)]
        changed = False
        for i, (r, g, b) in enumerate(pts):
            best, bd = 0, 1e18
            for j, (cr, cg, cb) in enumerate(cents):
                d = (r - cr) ** 2 + (g - cg) ** 2 + (b - cb) ** 2
                if d < bd:
                    best, bd = j, d
            if assign[i] != best:
                assign[i] = best
                changed = True
            s = sums[best]
            s[0] += r
            s[1] += g
            s[2] += b
            s[3] += 1
        for j, s in enumerate(sums):
            if s[3]:
                cents[j] = (s[0] / s[3], s[1] / s[3], s[2] / s[3])
        if not changed:
            break
    out = []
    for j in range(k):
        mem = [pts[i] for i in range(len(pts)) if assign[i] == j]
        if not mem:
            continue
        c = cents[j]
        rms = math.sqrt(sum((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2 + (p[2] - c[2]) ** 2 for p in mem) / len(mem))
        out.append(((c[0], c[1], c[2]), len(mem), rms))
    out.sort(key=lambda t: (-t[1], t[0]))
    return out


def background_model(pts: List[Color], margin: float) -> List[Tuple[Color, float]]:
    """[(centre, radius)] colour balls of the background: the biggest border colour cluster plus every sizeable cluster
    chained to it within CHAIN_DISTANCE (so shaded / brick-textured walls are covered, white wing tips and dark
    outlines touching the border are not)."""
    cl = _kmeans(pts, K_CLUSTERS)
    total = float(sum(c[1] for c in cl))
    accepted = [cl[0]]
    rest = [c for c in cl[1:] if c[1] / total >= MIN_CLUSTER_MASS]
    grew = True
    while grew:
        grew = False
        for c in list(rest):
            if any(math.dist(c[0], a[0]) <= CHAIN_DISTANCE for a in accepted):
                accepted.append(c)
                rest.remove(c)
                grew = True
    return [(c, max(2.5 * rms, 5.0) + margin) for c, _n, rms in accepted]


def _classify(data: bytearray, w: int, h: int, balls: Sequence[Tuple[Color, float]], slack: float = 0.0) -> bytearray:
    """1 where the pixel colour lies in one of the colour balls (background-like)."""
    keys = _quant_keys(data, w, h)
    lut: Dict[int, int] = {}
    for k in set(keys):
        c = _key_color(k)
        lut[k] = 1 if any(math.dist(c, ctr) <= rad + slack for ctr, rad in balls) else 0
    return bytearray(map(lut.__getitem__, keys))


def _alpha_mask(data: bytearray, crop: Rect, w: int, h: int) -> Optional[bytearray]:
    """Foreground from the alpha channel when the crop has a transparent background."""
    a = data[3::4]
    x0, y0, x1, y1 = crop
    transparent = 0
    for y in range(y0, y1):
        transparent += (len(a[y * w + x0:y * w + x1]) - a[y * w + x0:y * w + x1].count(255))
    area = (x1 - x0) * (y1 - y0)
    if transparent < 0.02 * area or transparent > 0.98 * area:
        return None
    return bytearray(bytes(a).translate(bytes([0] * 128 + [1] * 128)))


def _flood_foreground(bglike: bytearray, w: int, h: int, crop: Rect, min_island: float, seal: int) -> bytearray:
    """Foreground = everything that is not background-like pixels connected to the crop border. The background must reach
    the border through pixels at least `seal` px away from non-background ones, so a gap of up to 2 x `seal` px in the
    character's outline does not let it flood the figure. Background-coloured pixels enclosed by the outline (grey
    boots, see-through slits too thin to separate) stay foreground. Islands smaller than `min_island` x the largest
    component are dropped."""
    x0, y0, x1, y1 = crop
    cw, ch = x1 - x0, y1 - y0
    p = 2 * seal  # the world outside the crop counts as background: pad with background
    pw, ph = cw + 2 * p, ch + 2 * p
    padded = bytearray(b"\x01" * (pw * p))
    side = b"\x01" * p
    for y in range(y0, y1):
        padded += side + bglike[y * w + x0:y * w + x1] + side
    padded += b"\x01" * (pw * p)
    free = refmask.erode(padded, pw, ph, seal) if seal else padded
    if seal:  # the erosion eats the padding itself; put it back so border-connected components are found
        free[0:p * pw] = b"\x01" * (p * pw)
        free[(ph - p) * pw:] = b"\x01" * (p * pw)
        for y in range(p, ph - p):
            free[y * pw:y * pw + p] = side
            free[(y + 1) * pw - p:(y + 1) * pw] = side
    comps = refmask.Components(free, pw, ph, conn8=False)
    ext = comps.paint([c for c in range(len(comps)) if comps.border[c]])
    if seal:
        ext = refmask.dilate(ext, pw, ph, seal)
    fg = refmask.invert(refmask.land(ext, padded))
    sub = bytearray()
    for y in range(p, p + ch):
        sub += fg[y * pw + p:y * pw + p + cw]
    sub = refmask.keep_components(sub, cw, ch, min_island)
    out = bytearray(w * h)
    for y in range(ch):
        out[(y0 + y) * w + x0:(y0 + y) * w + x1] = sub[y * cw:(y + 1) * cw]
    return out


def auto_mask(data: bytearray, w: int, h: int, crop: Rect, tolerance: float = AUTO_MARGIN,
              min_island: float = 0.03, seal: int = SEAL) -> bytearray:
    """0/1 foreground mask of the crop: alpha channel when the image has a transparent background, otherwise the
    background colour model of the crop border + flood fill from the border."""
    alpha = _alpha_mask(data, crop, w, h)
    if alpha is not None:
        refmask.restrict_rect(alpha, w, h, crop)
        return refmask.keep_components(alpha, w, h, min_island)
    balls = background_model(_ring_samples(data, w, crop), tolerance)
    return _flood_foreground(_classify(data, w, h, balls, 3.5), w, h, crop, min_island, seal)


def color_mask(data: bytearray, w: int, h: int, crop: Rect, bgs: Sequence[Color], tolerance: float,
               min_island: float = 0.03, seal: int = SEAL) -> bytearray:
    """0/1 foreground mask: pixels farther than `tolerance` (RGB distance) from every colour in `bgs`, background
    connected to the crop border is removed; background-coloured pixels sealed inside the figure stay foreground."""
    balls = [(tuple(float(v) for v in c), float(tolerance)) for c in bgs]
    return _flood_foreground(_classify(data, w, h, balls), w, h, crop, min_island, seal)


OUTLINE_DARK = 85  # a pixel is outline / shadow when R, G and B are all below this


def dark_mask(data: bytearray, w: int, h: int, limit: int = OUTLINE_DARK) -> bytearray:
    """1 where all of R, G, B are below `limit` (the black outline / drop shadow of cartoon art)."""
    t = bytes([1] * limit + [0] * (256 - limit))
    n = w * h
    v = (int.from_bytes(data[0::4].translate(t), "little") & int.from_bytes(data[1::4].translate(t), "little")
         & int.from_bytes(data[2::4].translate(t), "little"))
    return bytearray(v.to_bytes(n, "little"))
