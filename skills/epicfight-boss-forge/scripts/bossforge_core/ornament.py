"""Pixel-art ornament engine behind the `filigree` feature (pure Python, deterministic, no noise).

Ornaments are drawn on a *panel*: the cell grid of one face (front, back, ...) of a connected cluster of texels.
A face of a box is its own rectangle in the texture atlas, and a garment is usually several boxes (the two
halves of a skirt panel, an upper and a lower segment, chest + abdomen + pelvis), so a pattern cannot run on the
atlas rectangles directly. `build_panels` lays the rectangles of a cluster out on one virtual texel grid from
their world coordinates (``Texel.u`` viewer-right, ``Texel.v`` height), keeps every rectangle's own texel grid
exact (a stroke is never a texel wider or thinner than its neighbours), merges them into square *cells* of
`cell` texels and records which cells exist. Patterns then work on cells:

* ``border``  a repeating strip along the outline of the cell set (any irregular outline, with corner pieces);
* ``band``    the same strip across a horizontal band of the cell set;
* ``panel``   one centred motif (optionally on a filled, edged rectangle, like a tabard ornament).

Strips and motifs are 1-cell strokes with 1-cell gaps, mirror-symmetric about the panel centre, so the
ornament reads as crisp pixel art at any texture density (the cell is a whole number of texels).
"""
from __future__ import annotations

import math
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .materials import Texel

Cell = Tuple[int, int]

PATTERNS = ("meander", "key", "lozenge", "cross_band", "wing", "line", "diamond_chain", "crest", "collar_wing", "lace")
WHERES = ("border", "panel", "band")
EDGES = ("top", "bottom", "left", "right")
ANCHORS = ("center", "top", "bottom")
SIZES = ("auto", "small", "large")
AUTO_CELL_BLOCKS = 0.03  # default ornament cell: about 1/32 block (1 texel at 32-48 per block, 2 at 64)
SEAM_TEXELS = 2  # empty lattice columns / rows up to this wide between the rectangles of a cluster are squeezed out
LARGE_MIN_CELLS = 24  # `size: auto` picks the 5-row strips when the panel is at least this many cells in both directions

# Strip tiles: rows outer edge -> inner, '#' = painted. The tile repeats every len(row) cells along the edge and is
# mirrored about the panel centre. Two heights per pattern: 3 rows (small) and 5 rows (large).
STRIPS = {
    "meander": {  # squared Greek-key hooks
        3: ("###.", "#.#.", "#..."),
        5: ("#.####", "#.#...", "#.#.##", "#.#.#.", "#...#."),
    },
    "key": {  # stepped zig-zag: a line that bounces between the outer and inner row
        3: ("#...", ".#.#", "..#."),
        5: ("#.......", ".#.....#", "..#...#.", "...#.#..", "....#..."),
    },
    "lozenge": {  # chain of small diamonds (the large one has a dot in each)
        3: (".#..", "#.#.", ".#.."),
        5: ("..#...", ".#.#..", "#.#.#.", ".#.#..", "..#..."),
    },
    "cross_band": {  # plus signs
        3: (".#..", "###.", ".#.."),
        5: ("..#...", "..#...", "#####.", "..#...", "..#..."),
    },
    "wing": {  # layered feather chevrons
        3: ("#....#", ".#..#.", "..##.."),
        5: ("#......#", ".#....#.", "#.#..#.#", ".#.##.#.", "..#..#.."),
    },
    "line": {  # a plain line (a double line when large): cuff edges, outlines
        3: ("#",),
        5: ("#", ".", "#"),
    },
    "diamond_chain": {  # diamonds sharing their tips
        3: (".#.#", "#...", ".#.#"),
        5: ("..#.", ".#.#", "#...", ".#.#", "..#."),
    },
    "crest": {  # battlement / ladder band
        3: ("#.#.", "####", "#.#."),
        5: ("#.#.#.", "######", "#.#.#.", "######", "#.#.#."),
    },
    "collar_wing": {  # as `wing`: layered feather chevrons
        3: ("#....#", ".#..#.", "..##.."),
        5: ("#......#", ".#....#.", "#.#..#.#", ".#.##.#.", "..#..#.."),
    },
    "lace": {  # scalloped edge: outer row first, so on a hem the arches hang at the bottom over a solid line
        3: (".##.", "#..#", "####"),
        5: (".##.", "#..#", "#..#", "####", ".#.#"),
    },
}


def strip_rows(pattern: str, size: str, w: int, h: int) -> Tuple[str, ...]:
    variants = STRIPS[pattern]
    if size == "small":
        return variants[3]
    if size == "large":
        return variants[5]
    return variants[5] if min(w, h) >= LARGE_MIN_CELLS else variants[3]


# --------------------------------------------------------------------------- panel layout
class Panel:
    """Cell grid of one face of a texel cluster. ``cells`` maps a cell to the texels it covers."""

    def __init__(self, w: int, h: int, m: int, cells: Dict[Cell, List[Texel]], occ: Set[Cell]):
        self.w = w  # cells
        self.h = h
        self.m = m  # texels per cell
        self.cells = cells
        self.occ = occ

    def texels(self) -> List[Texel]:
        out = []  # type: List[Texel]
        for c in sorted(self.cells):
            out.extend(self.cells[c])
        return out


def _squeeze(values: Set[int], limit: int) -> Dict[int, int]:
    """old index -> new index, dropping runs of at most `limit` unused indices between used ones."""
    mapping = {}  # type: Dict[int, int]
    shift = 0
    prev = None  # type: Optional[int]
    for v in sorted(values):
        if prev is not None and 0 < v - prev - 1 <= limit:
            shift += v - prev - 1
        mapping[v] = v - shift
        prev = v
    return mapping


def auto_cell(pitch: float) -> int:
    """Texels per ornament cell for a texel size in blocks: at least 1, about AUTO_CELL_BLOCKS."""
    return max(1, int(round(AUTO_CELL_BLOCKS / max(pitch, 1e-6))))


def _median(vals: Sequence[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2]


class _Rect:
    __slots__ = ("texels", "cx", "cy", "umin", "umax", "vmin", "vmax", "ref_cx", "ref_cy")

    def __init__(self, ts: List[Texel]):
        self.texels = ts
        minx = min(t.x for t in ts)
        maxx = max(t.x for t in ts)
        miny = min(t.y for t in ts)
        maxy = max(t.y for t in ts)
        left = [t.u for t in ts if t.x == minx]
        right = [t.u for t in ts if t.x == maxx]
        top = [t.v for t in ts if t.y == miny]
        bot = [t.v for t in ts if t.y == maxy]
        su = sum(right) / len(right) >= sum(left) / len(left)  # u grows with the atlas x
        sv = sum(top) / len(top) >= sum(bot) / len(bot)  # v falls with the atlas y (the usual orientation)
        self.cx = {}  # type: Dict[int, int]
        self.cy = {}  # type: Dict[int, int]
        for i, t in enumerate(ts):
            self.cx[i] = (t.x - minx) if su else (maxx - t.x)
            self.cy[i] = (t.y - miny) if sv else (maxy - t.y)
        us = [t.u for t in ts]
        vs = [t.v for t in ts]
        self.umin, self.umax, self.vmin, self.vmax = min(us), max(us), min(vs), max(vs)
        iu = min(range(len(ts)), key=lambda i: (ts[i].u, self.cx[i], i))
        iv = min(range(len(ts)), key=lambda i: (-ts[i].v, self.cy[i], i))
        self.ref_cx = self.cx[iu]
        self.ref_cy = self.cy[iv]


def _clusters(rects: List[_Rect], tol: float) -> List[List[_Rect]]:
    """Rectangles whose (u, v) extents touch (within `tol` blocks) form one panel."""
    n = len(rects)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            a, b = rects[i], rects[j]
            if a.umin - tol <= b.umax and b.umin - tol <= a.umax and a.vmin - tol <= b.vmax and b.vmin - tol <= a.vmax:
                parent[find(i)] = find(j)
    groups = {}  # type: Dict[int, List[_Rect]]
    for i in range(n):
        groups.setdefault(find(i), []).append(rects[i])
    out = list(groups.values())
    out.sort(key=lambda g: (round(min(r.umin for r in g), 6), -round(max(r.vmax for r in g), 6)))
    return out


def build_panels(texels: Sequence[Texel], cell: int = 0, gap_texels: float = 2.5) -> List[Panel]:
    """Lay the texels (all of ONE face class) out on cell grids, one `Panel` per connected cluster.

    ``cell`` = texels per ornament cell (0 = automatic from the texel size)."""
    if not texels:
        return []
    by_rect = {}  # type: Dict[Tuple[str, str], List[Texel]]
    for t in texels:
        by_rect.setdefault((t.part, t.face), []).append(t)
    rects = [_Rect(by_rect[k]) for k in sorted(by_rect)]
    pitch = _median([t.tb for t in texels])
    m = cell if cell > 0 else auto_cell(pitch)
    out = []  # type: List[Panel]
    for group in _clusters(rects, gap_texels * pitch):
        u0 = min(r.umin for r in group)
        v1 = max(r.vmax for r in group)
        placed = []  # type: List[Tuple[Texel, int, int]]
        for r in group:
            bx = int(round((r.umin - u0) / pitch)) - r.ref_cx
            by = int(round((v1 - r.vmax) / pitch)) - r.ref_cy
            for i, t in enumerate(r.texels):
                placed.append((t, bx + r.cx[i], by + r.cy[i]))
        gx0 = min(p[1] for p in placed)
        gy0 = min(p[2] for p in placed)
        # rectangles of one garment are a hair apart (two skirt halves): squeeze such seams out of the lattice so a
        # centred motif is not split by a column of nothing
        mapx = _squeeze({p[1] - gx0 for p in placed}, SEAM_TEXELS)
        mapy = _squeeze({p[2] - gy0 for p in placed}, SEAM_TEXELS)
        placed = [(t, mapx[gx - gx0], mapy[gy - gy0]) for t, gx, gy in placed]
        grid = {(gx, gy) for _t, gx, gy in placed}
        # close one-texel holes between rectangles so they never read as an outline
        for (gx, gy) in sorted(grid):
            if (gx + 2, gy) in grid and (gx + 1, gy) not in grid:
                grid.add((gx + 1, gy))
            if (gx, gy + 2) in grid and (gx, gy + 1) not in grid:
                grid.add((gx, gy + 1))
        width = max(g[0] for g in grid) + 1
        height = max(g[1] for g in grid) + 1
        wc, hc = width // m, height // m
        if wc < 1 or hc < 1:
            continue
        mx, my = (width - wc * m) // 2, (height - hc * m) // 2
        occ = set()  # type: Set[Cell]
        for (gx, gy) in grid:
            ci, cj = (gx - mx) // m, (gy - my) // m
            if 0 <= ci < wc and 0 <= cj < hc:
                occ.add((ci, cj))
        cells = {}  # type: Dict[Cell, List[Texel]]
        for t, gx, gy in placed:
            ci, cj = (gx - mx) // m, (gy - my) // m
            if 0 <= ci < wc and 0 <= cj < hc:
                cells.setdefault((ci, cj), []).append(t)
        out.append(Panel(wc, hc, m, cells, occ))
    return out


# --------------------------------------------------------------------------- edge distances
def edge_depths(occ: Set[Cell]):
    """(left, right, up, down): for every cell, how many occupied cells lie between it and the nearest gap (or the
    grid edge) in that direction; a cell on the outline has depth 0."""
    left, right, up, down = {}, {}, {}, {}  # type: Dict[Cell, int]
    rows = {}  # type: Dict[int, List[int]]
    cols = {}  # type: Dict[int, List[int]]
    for (i, j) in occ:
        rows.setdefault(j, []).append(i)
        cols.setdefault(i, []).append(j)

    def runs(vals: List[int]):
        vals.sort()
        start = prev = vals[0]
        for v in vals[1:]:
            if v != prev + 1:
                yield start, prev
                start = v
            prev = v
        yield start, prev

    for j, xs in rows.items():
        for a, b in runs(xs):
            for x in range(a, b + 1):
                left[(x, j)] = x - a
                right[(x, j)] = b - x
    for i, ys in cols.items():
        for a, b in runs(ys):
            for y in range(a, b + 1):
                up[(i, y)] = y - a
                down[(i, y)] = b - y
    return left, right, up, down


_INF = 10 ** 6


def border_cells(occ: Set[Cell], w: int, h: int, rows: Sequence[str], inset: int = 0,
                 edges: Iterable[str] = EDGES, mirror: bool = True) -> Set[Cell]:
    """Cells of a strip tiled along the outline of `occ` (outer row first), mirror-symmetric about the centre."""
    edges = set(edges)
    bw = len(rows)
    period = len(rows[0])
    dl, dr, du, dd = edge_depths(occ)
    cc = (w - 1) / 2.0
    cr = (h - 1) / 2.0
    out = set()  # type: Set[Cell]
    for c in occ:
        i, j = c
        dh = min(dl[c] if "left" in edges else _INF, dr[c] if "right" in edges else _INF)
        dv = min(du[c] if "top" in edges else _INF, dd[c] if "bottom" in edges else _INF)
        in_h = inset <= dh < inset + bw
        in_v = inset <= dv < inset + bw
        if in_v and dh >= inset + bw:  # along a top / bottom edge
            col = int(abs(i - cc)) % period if mirror else i % period
            if rows[dv - inset][col] == "#":
                out.add(c)
        elif in_h and dv >= inset + bw:  # along a left / right edge
            col = int(abs(j - cr)) % period if mirror else j % period
            if rows[dh - inset][col] == "#":
                out.add(c)
        elif in_h and in_v:  # corner: a small hollow square (with a dot when it is large enough)
            a, b = dv - inset, dh - inset
            ring = a == 0 or b == 0 or a == bw - 1 or b == bw - 1
            if ring or (bw >= 5 and a == bw // 2 and b == bw // 2):
                out.add(c)
    return out


def band_rows(panel: Panel, lo: float, hi: float) -> Tuple[int, int]:
    """Inclusive cell-row range whose centres lie in [lo, hi] (fractions of the panel height, 0 = bottom)."""
    h = panel.h
    sel = [j for j in range(h) if lo <= 1.0 - (j + 0.5) / h <= hi]
    if not sel:
        j = min(h - 1, max(0, int(round((1.0 - 0.5 * (lo + hi)) * h - 0.5))))
        return j, j
    return sel[0], sel[-1]


def band_cells(occ: Set[Cell], w: int, h: int, rows: Sequence[str], row_lo: int, row_hi: int,
               inset: int = 0, mirror: bool = True) -> Set[Cell]:
    """Cells of a strip laid across the horizontal band [row_lo, row_hi] (centred in the band)."""
    bw = len(rows)
    period = len(rows[0])
    offset = ((row_hi - row_lo + 1) - bw) // 2
    dl, dr, _du, _dd = edge_depths(occ)
    cc = (w - 1) / 2.0
    out = set()  # type: Set[Cell]
    for c in occ:
        i, j = c
        if not row_lo <= j <= row_hi:
            continue
        r = j - row_lo - offset
        if not 0 <= r < bw:
            continue
        if min(dl[c], dr[c]) < inset:
            continue
        col = int(abs(i - cc)) % period if mirror else i % period
        if rows[r][col] == "#":
            out.add(c)
    return out


# --------------------------------------------------------------------------- motifs (panel mode)
def _full(mw: int, mh: int) -> Set[Cell]:
    return {(i, j) for i in range(mw) for j in range(mh)}


def _motif_meander(mw: int, mh: int, size: str) -> Set[Cell]:
    rows = strip_rows("meander", size, mw, mh)
    if min(mw, mh) < 2 * len(rows) + 1:
        rows = STRIPS["meander"][3]
    out = border_cells(_full(mw, mh), mw, mh, rows)
    if mw >= 7 and mh >= 7:  # a centre mark inside the frame
        out.update({(i, j) for i in {(mw - 1) // 2, mw // 2} for j in {(mh - 1) // 2, mh // 2}})
    return out


def _motif_key(mw: int, mh: int, size: str) -> Set[Cell]:
    """Concentric stepped frames with a notch at the top centre of every inner one."""
    out = set()  # type: Set[Cell]
    rx = (mw - 1) / 2.0
    for i in range(mw):
        for j in range(mh):
            d = min(i, mw - 1 - i, j, mh - 1 - j)
            if d % 2 or d > (min(mw, mh) - 1) // 2:
                continue
            if d > 0 and j == d and abs(i - rx) < 0.6:
                continue
            out.add((i, j))
    return out


def _diamond(mw: int, mh: int, scale: float) -> Set[Cell]:
    """One-cell outline of a diamond filling `scale` of the box: every row and every column gets the cell where the
    outline crosses it, so the line is connected (8-neighbours) for any aspect ratio."""
    cx, cy = (mw - 1) / 2.0, (mh - 1) / 2.0
    rx, ry = max(cx * scale, 0.5), max(cy * scale, 0.5)
    out = set()  # type: Set[Cell]
    for j in range(mh):
        dy = abs(j - cy)
        if dy <= ry + 1e-9:
            ir = int(math.floor(cx + rx * (1.0 - dy / ry) + 0.5))  # round half up; the left cell mirrors the right one
            for i in (ir, mw - 1 - ir):
                if 0 <= i < mw:
                    out.add((i, j))
    for i in range(mw):
        dx = abs(i - cx)
        if dx <= rx + 1e-9:
            jb = int(math.floor(cy + ry * (1.0 - dx / rx) + 0.5))
            for j in (jb, mh - 1 - jb):
                if 0 <= j < mh:
                    out.add((i, j))
    return out


def _motif_lozenge(mw: int, mh: int, size: str) -> Set[Cell]:
    """A diamond outline; a nested one when the box is large, otherwise a plus in the middle."""
    out = _diamond(mw, mh, 1.0)
    xs = {(mw - 1) // 2, mw // 2}
    ys = {(mh - 1) // 2, mh // 2}
    if mw >= 13 and mh >= 13:
        out |= _diamond(mw, mh, 0.5)
    if mw >= 5 and mh >= 5:
        out.update({(i, j) for i in xs for j in ys})
        if mw >= 9 and mh >= 9:
            out.update({(i, j) for i in xs for j in (min(ys) - 1, max(ys) + 1)})
            out.update({(i, j) for j in ys for i in (min(xs) - 1, max(xs) + 1)})
    return out


def _motif_cross(mw: int, mh: int, size: str) -> Set[Cell]:
    xs = {(mw - 1) // 2, mw // 2}
    ys = {(mh - 1) // 2, mh // 2}
    out = set()  # type: Set[Cell]
    for i in range(mw):
        for j in range(mh):
            if i in xs or j in ys:
                out.add((i, j))
    rx, ry = (mw - 1) / 2.0, (mh - 1) / 2.0
    q = max(2, int(round(min(rx, ry) * 0.6)))
    if min(mw, mh) >= 7:
        ir, jb = int(math.floor(rx + q + 0.5)), int(math.floor(ry + q + 0.5))  # mirrored, not re-rounded
        for i in (ir, mw - 1 - ir):
            for j in (jb, mh - 1 - jb):
                out.add((i, j))
        for i in (0, mw - 1):  # end caps
            out.update({(i, j) for j in (min(ys) - 1, max(ys) + 1) if 0 <= j < mh})
        for j in (0, mh - 1):
            out.update({(i, j) for i in (min(xs) - 1, max(xs) + 1) if 0 <= i < mw})
    return out


def _motif_wing(mw: int, mh: int, size: str) -> Set[Cell]:
    """A short stem whose foot sends three feather lines to both sides: a steep one, a middle one and a flat one."""
    out = set()  # type: Set[Cell]
    xs = {(mw - 1) // 2, mw // 2}
    half_w = mw - 1 - max(xs)
    y0 = mh - 1
    for j in range(y0 - max(2, mh // 3) + 1, y0 + 1):
        out.update({(i, j) for i in xs})
    slopes = (1.0, 0.5, 0.0) if mh >= 6 else (0.5, 0.0)
    for n, slope in enumerate(slopes):
        for t in range(1, half_w + 1):
            j = y0 - n - int(round(slope * (t - 1)))
            if j < 0:
                break
            if n == len(slopes) - 1 and t == half_w and half_w > 3:
                break  # the flat feather is the shortest
            out.update({(max(xs) + t, j), (min(xs) - t, j)})
    return out


def _motif_line(mw: int, mh: int, size: str) -> Set[Cell]:
    """A one-cell rectangle outline."""
    return {(i, j) for i in range(mw) for j in range(mh) if i in (0, mw - 1) or j in (0, mh - 1)}


def _motif_diamond_chain(mw: int, mh: int, size: str) -> Set[Cell]:
    """Diamonds as wide as the box, stacked tip to tip down its height."""
    dh = max(5, min(mw, mh))
    if dh % 2 == 0:
        dh -= 1
    step = dh - 1
    n = max(1, (mh - 1) // step)
    top = (mh - (n * step + 1)) // 2
    diamond = _diamond(mw, dh, 1.0)
    return {(i, top + k * step + j) for k in range(n) for (i, j) in diamond if top + k * step + j < mh}


def _motif_crest(mw: int, mh: int, size: str) -> Set[Cell]:
    """A bodice crest: two vertical bars that flare at the top, carry a small tab pointing inward twice, and close in a
    stepped cup that narrows to one block at the bottom."""
    out = set()  # type: Set[Cell]
    half = (mw - 1) / 2.0
    t = max(1, int(round(mw / 6.0)))
    tab = max(1, int(round(t * 0.6)))
    steps = 3 if mh >= 8 else 2
    body = mh - steps
    tab_rows = {int(round(body * 0.35)), int(round(body * 0.7))} if body >= 5 else set()
    for j in range(mh):
        for i in range(mw):
            d = abs(i - half)
            if j >= body:
                k = j - body
                if steps == 3 and k == 0:
                    hit = half - t - 1 < d <= half
                elif k == steps - 2:
                    hit = d <= half - 1
                else:
                    hit = d <= max(0.5, half / 3.0)
            elif j == 0:
                hit = half - t - 1 < d <= half
            elif j in tab_rows:
                hit = half - t - tab < d <= half
            else:
                hit = half - t < d <= half
            if hit:
                out.add((i, j))
    return out


def _motif_collar_wing(mw: int, mh: int, size: str) -> Set[Cell]:
    """Two wing pieces either side of an empty middle (for the neck and a cross): layered feather lines that step down
    toward the middle, the lower ones starting further in."""
    gap = max(3, mw // 3)
    if (mw - gap) % 2:
        gap += 1
    side = (mw - gap) // 2
    out = set()  # type: Set[Cell]
    for n in range(max(1, min(3, (mh + 1) // 3))):
        for x in range(n, side):
            j = 3 * n + (x - n) // 2
            if j < mh:
                out.update({(x, j), (mw - 1 - x, j)})
    return out


def _motif_lace(mw: int, mh: int, size: str) -> Set[Cell]:
    """A one-cell frame filled with a mesh of chevrons (diagonals mirrored about the vertical centre line)."""
    out = _motif_line(mw, mh, size)
    for i in range(1, mw - 1):
        d = int(abs(i - (mw - 1) / 2.0))  # cells from the centre line, the same for a cell and its mirror image
        for j in range(1, mh - 1):
            if (d + j) % 4 == 0 or (d - j) % 4 == 0:
                out.add((i, j))
    return out


MOTIFS = {
    "meander": _motif_meander,
    "key": _motif_key,
    "lozenge": _motif_lozenge,
    "cross_band": _motif_cross,
    "wing": _motif_wing,
    "line": _motif_line,
    "diamond_chain": _motif_diamond_chain,
    "crest": _motif_crest,
    "collar_wing": _motif_collar_wing,
    "lace": _motif_lace,
}


def _rect_cells(i0: int, j0: int, pw: int, ph: int) -> Set[Cell]:
    return {(i, j) for i in range(i0, i0 + pw) for j in range(j0, j0 + ph)}


def panel_rect(w: int, h: int, cx: float, cy: float, wfrac: float, hfrac: float) -> Tuple[int, int, int, int]:
    """(i0, j0, pw, ph) of a centred rectangle in a w x h cell grid; the width keeps the grid's parity so a
    centred panel stays mirror-symmetric."""
    pw = max(1, min(w, int(round(wfrac * w))))
    ph = max(1, min(h, int(round(hfrac * h))))
    if abs(cx - 0.5) < 1e-9 and (w - pw) % 2 and pw > 1:
        pw -= 1
    i0 = max(0, min(w - pw, int(round(cx * w - pw / 2.0))))
    j0 = max(0, min(h - ph, int(round(cy * h - ph / 2.0))))
    return i0, j0, pw, ph


def panel_layers(panel: Panel, pattern: str, cx: float, cy: float, wfrac: float, hfrac: float,
                 fill: bool, edge: bool, inset: int, motif_height: float, anchor: str, size: str,
                 edges: Iterable[str] = EDGES) -> Tuple[Set[Cell], Set[Cell], Set[Cell]]:
    """(fill cells, edge cells, motif cells) of a centred panel motif."""
    i0, j0, pw, ph = panel_rect(panel.w, panel.h, cx, cy, wfrac, hfrac)
    rect = _rect_cells(i0, j0, pw, ph) & panel.occ
    edges = set(edges)
    edge_cells = set()  # type: Set[Cell]
    if edge:
        for (i, j) in rect:
            if (("left" in edges and i == i0) or ("right" in edges and i == i0 + pw - 1)
                    or ("top" in edges and j == j0) or ("bottom" in edges and j == j0 + ph - 1)):
                edge_cells.add((i, j))
    ring = 1 if edge else 0
    pad = max(inset, ring)
    iw, ih = pw - 2 * pad, ph - 2 * pad
    motif = set()  # type: Set[Cell]
    if iw >= 3 and ih >= 3:
        mh = max(3, min(ih, int(round(motif_height * ih))))
        mj0 = j0 + pad + {"top": 0, "bottom": ih - mh}.get(anchor, (ih - mh) // 2)
        for (a, b) in MOTIFS[pattern](iw, mh, size):
            c = (i0 + pad + a, mj0 + b)
            if c in panel.occ:
                motif.add(c)
    return (rect if fill else set()), edge_cells, motif
