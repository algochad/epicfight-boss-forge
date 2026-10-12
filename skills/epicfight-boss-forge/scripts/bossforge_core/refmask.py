"""Mask image processing for reference views (stdlib only, row-wise / big-integer tricks, no per-pixel Python loops
where avoidable). A mask is a bytearray of width*height bytes; 1 = foreground, 0 = background (255 only at the very end,
see `finish`)."""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

_RUN = re.compile(rb"\x01+")
Poly = Sequence[Sequence[float]]


# ---------------------------------------------------------------------------------------------- polygons
def fill_polygons(polys: Sequence[Poly], w: int, h: int) -> bytearray:
    """Union of the polygons rasterised with the even-odd rule at pixel centres (pixel (x, y) is inside when the point
    (x + 0.5, y + 0.5) is inside)."""
    out = bytearray(w * h)
    for poly in polys:
        pts = [(float(p[0]), float(p[1])) for p in poly]
        n = len(pts)
        if n < 3:
            continue
        ys = [p[1] for p in pts]
        ya = max(0, int(min(ys) - 0.5) - 1)
        yb = min(h - 1, int(max(ys) + 0.5) + 1)
        edges = []
        for i in range(n):
            (x0, y0), (x1, y1) = pts[i], pts[(i + 1) % n]
            if y0 != y1:
                edges.append((x0, y0, x1, y1) if y0 < y1 else (x1, y1, x0, y0))
        for y in range(ya, yb + 1):
            cy = y + 0.5
            xs = sorted(x0 + (cy - y0) * (x1 - x0) / (y1 - y0) for x0, y0, x1, y1 in edges if y0 <= cy < y1)
            for k in range(0, len(xs) - 1, 2):
                a = max(0, int(-(-(xs[k] - 0.5) // 1)))  # first pixel centre >= xs[k]
                b = min(w, int(-(-(xs[k + 1] - 0.5) // 1)))  # first pixel centre >= xs[k+1] (exclusive)
                if b > a:
                    out[y * w + a:y * w + b] = b"\x01" * (b - a)
    return out


# ---------------------------------------------------------------------------------------------- morphology
class _Grid:
    """Constant big-integer masks for a w x h byte-per-pixel image."""
    _cache: Dict[Tuple[int, int], "_Grid"] = {}

    def __init__(self, w: int, h: int):
        self.w, self.h, self.n = w, h, w * h
        self.one = int.from_bytes(b"\x01" * (w * h), "little")
        self.not_left = int.from_bytes((b"\x00" + b"\x01" * (w - 1)) * h, "little")  # x != 0
        self.not_right = int.from_bytes((b"\x01" * (w - 1) + b"\x00") * h, "little")  # x != w - 1

    @classmethod
    def get(cls, w: int, h: int) -> "_Grid":
        g = cls._cache.get((w, h))
        if g is None:
            if len(cls._cache) > 4:
                cls._cache.clear()
            g = cls._cache[(w, h)] = _Grid(w, h)
        return g


def _to_int(m: bytearray) -> int:
    return int.from_bytes(m, "little")


def _to_mask(v: int, n: int) -> bytearray:
    return bytearray(v.to_bytes(n, "little"))


def _dilate_int(g: _Grid, m: int) -> int:
    hz = m | ((m << 8) & g.not_left) | ((m >> 8) & g.not_right)
    return (hz | (hz << (8 * g.w)) | (hz >> (8 * g.w))) & g.one


def _erode_int(g: _Grid, m: int) -> int:
    """3x3 erosion; pixels outside the image count as background."""
    hz = m & ((m << 8) & g.not_left) & ((m >> 8) & g.not_right)
    return hz & ((hz << (8 * g.w)) & g.one) & (hz >> (8 * g.w))


def dilate(mask: bytearray, w: int, h: int, r: int = 1) -> bytearray:
    if r <= 0:
        return bytearray(mask)
    g = _Grid.get(w, h)
    m = _to_int(mask)
    for _ in range(r):
        m = _dilate_int(g, m)
    return _to_mask(m, w * h)


def erode(mask: bytearray, w: int, h: int, r: int = 1) -> bytearray:
    if r <= 0:
        return bytearray(mask)
    g = _Grid.get(w, h)
    m = _to_int(mask)
    for _ in range(r):
        m = _erode_int(g, m)
    return _to_mask(m, w * h)


def peel(mask: bytearray, allowed: bytearray, w: int, h: int, layers: int) -> bytearray:
    """Remove up to `layers` rings of foreground edge pixels, but only pixels that are also set in `allowed`
    (0/1 mask); used to strip the dark outline of cartoon art off a silhouette without eroding the art itself."""
    g = _Grid.get(w, h)
    m = _to_int(mask)
    a = _to_int(allowed)
    for _ in range(layers):
        edge = m & (g.one ^ _erode_int(g, m))
        m &= g.one ^ (edge & a)
    return _to_mask(m, w * h)


def invert(mask: bytearray) -> bytearray:
    n = len(mask)
    return _to_mask(_to_int(mask) ^ int.from_bytes(b"\x01" * n, "little"), n)


def land(a: bytearray, b: bytearray) -> bytearray:
    """Element-wise AND of two 0/1 masks."""
    return _to_mask(_to_int(a) & _to_int(b), len(a))


def count(mask: bytearray) -> int:
    return len(mask) - mask.count(0)


def bbox(mask: bytearray, w: int, h: int) -> Optional[Tuple[int, int, int, int]]:
    """(x0, y0, x1, y1) half-open pixel bounds of the foreground, None when empty."""
    zero = bytes(w)
    ys = [y for y in range(h) if mask[y * w:(y + 1) * w] != zero]
    if not ys:
        return None
    x0, x1 = w, 0
    for y in ys:
        row = bytes(mask[y * w:(y + 1) * w])
        lo = len(row) - len(row.lstrip(b"\x00"))
        hi = len(row.rstrip(b"\x00"))
        x0, x1 = min(x0, lo), max(x1, hi)
    return (x0, ys[0], x1, ys[-1] + 1)


# ---------------------------------------------------------------------------------------------- components
class Components:
    """Connected components of a mask: `runs[c]` = list of (y, x0, x1); `area[c]`, `box[c]` = (x0, y0, x1, y1),
    `border[c]` True when the component touches the image border."""

    def __init__(self, mask: bytearray, w: int, h: int, conn8: bool = True):
        self.w, self.h = w, h
        runs: List[Tuple[int, int, int]] = []
        parent: List[int] = []

        def find(a: int) -> int:
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        slack = 1 if conn8 else 0
        prev: List[int] = []
        for y in range(h):
            row = bytes(mask[y * w:(y + 1) * w])
            cur: List[int] = []
            j = 0
            for m in _RUN.finditer(row):
                x0, x1 = m.start(), m.end()
                rid = len(runs)
                runs.append((y, x0, x1))
                parent.append(rid)
                cur.append(rid)
                while j < len(prev) and runs[prev[j]][2] + slack <= x0:
                    j += 1
                k = j
                while k < len(prev) and runs[prev[k]][1] - slack < x1:
                    ra, rb = find(rid), find(prev[k])
                    if ra != rb:
                        parent[rb] = ra
                    k += 1
            prev = cur
        groups: Dict[int, List[Tuple[int, int, int]]] = {}
        for rid, r in enumerate(runs):
            groups.setdefault(find(rid), []).append(r)
        self.runs = [groups[k] for k in sorted(groups, key=lambda g: groups[g][0][0] * w + groups[g][0][1])]
        self.area = [sum(x1 - x0 for _y, x0, x1 in rs) for rs in self.runs]
        self.box = [(min(r[1] for r in rs), rs[0][0], max(r[2] for r in rs), rs[-1][0] + 1) for rs in self.runs]
        self.border = [any(r[0] in (0, h - 1) or r[1] == 0 or r[2] == w for r in rs) for rs in self.runs]

    def __len__(self) -> int:
        return len(self.runs)

    def paint(self, ids: Sequence[int], out: Optional[bytearray] = None) -> bytearray:
        w = self.w
        if out is None:
            out = bytearray(w * self.h)
        for c in ids:
            for y, x0, x1 in self.runs[c]:
                out[y * w + x0:y * w + x1] = b"\x01" * (x1 - x0)
        return out


def keep_components(mask: bytearray, w: int, h: int, min_frac: float) -> bytearray:
    """Keep the largest 8-connected component plus every component of at least `min_frac` of its area."""
    comps = Components(mask, w, h)
    if not len(comps):
        return bytearray(w * h)
    big = max(comps.area)
    return comps.paint([c for c in range(len(comps)) if comps.area[c] >= max(1, big * min_frac)])


def restrict_rect(mask: bytearray, w: int, h: int, rect: Tuple[int, int, int, int]) -> None:
    """Zero everything outside the half-open pixel rect (in place)."""
    x0, y0, x1, y1 = rect
    zero = bytes(w)
    for y in range(h):
        if y < y0 or y >= y1:
            mask[y * w:(y + 1) * w] = zero
        else:
            if x0 > 0:
                mask[y * w:y * w + x0] = bytes(x0)
            if x1 < w:
                mask[y * w + x1:(y + 1) * w] = bytes(w - x1)


def finish(mask: bytearray) -> bytearray:
    """0/1 -> 0/255."""
    return bytearray(mask.translate(bytes([0, 255] + [255] * 254)))
