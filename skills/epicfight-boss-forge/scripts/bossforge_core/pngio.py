"""Minimal RGBA canvas, PNG writer and a general PNG reader (`read_png`; stdlib only)."""
from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import List, Optional, Sequence, Tuple, Union

RGBA = Tuple[int, int, int, int]


class Canvas:
    __slots__ = ("w", "h", "data")

    def __init__(self, w: int, h: int, data: Optional[bytearray] = None):
        self.w = int(w)
        self.h = int(h)
        self.data = data if data is not None else bytearray(self.w * self.h * 4)

    def set(self, x: int, y: int, rgba: Sequence[int]) -> None:
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 4
            self.data[i] = rgba[0]
            self.data[i + 1] = rgba[1]
            self.data[i + 2] = rgba[2]
            self.data[i + 3] = rgba[3] if len(rgba) > 3 else 255

    def get(self, x: int, y: int) -> RGBA:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return (0, 0, 0, 0)
        i = (y * self.w + x) * 4
        d = self.data
        return (d[i], d[i + 1], d[i + 2], d[i + 3])

    def fill(self, rgba: Sequence[int]) -> None:
        px = bytes((rgba[0], rgba[1], rgba[2], rgba[3] if len(rgba) > 3 else 255))
        self.data[:] = px * (self.w * self.h)

    def blit(self, src: "Canvas", dx: int, dy: int) -> None:
        """Copy src over self (opaque copy of src pixels with alpha > 0 composited over)."""
        for y in range(src.h):
            ty = y + dy
            if ty < 0 or ty >= self.h:
                continue
            for x in range(src.w):
                tx = x + dx
                if tx < 0 or tx >= self.w:
                    continue
                si = (y * src.w + x) * 4
                a = src.data[si + 3]
                if a == 0:
                    continue
                ti = (ty * self.w + tx) * 4
                if a == 255:
                    self.data[ti:ti + 4] = src.data[si:si + 4]
                else:
                    ba = self.data[ti + 3]
                    oa = a + ba * (255 - a) / 255.0
                    for c in range(3):
                        self.data[ti + c] = int(round(
                            (src.data[si + c] * a + self.data[ti + c] * ba * (255 - a) / 255.0) / oa))
                    self.data[ti + 3] = int(round(oa))

    def copy(self) -> "Canvas":
        return Canvas(self.w, self.h, bytearray(self.data))

    def scaled(self, k: int) -> "Canvas":
        k = int(k)
        out = Canvas(self.w * k, self.h * k)
        for y in range(self.h):
            row = bytearray()
            for x in range(self.w):
                i = (y * self.w + x) * 4
                row += bytes(self.data[i:i + 4]) * k
            rowb = bytes(row)
            for ky in range(k):
                o = ((y * k + ky) * out.w) * 4
                out.data[o:o + len(rowb)] = rowb
        return out

    def downsampled(self, k: int) -> "Canvas":
        k = int(k)
        ow, oh = self.w // k, self.h // k
        out = Canvas(ow, oh)
        for y in range(oh):
            for x in range(ow):
                sr = sg = sb = sa = 0
                for ky in range(k):
                    base = ((y * k + ky) * self.w + x * k) * 4
                    for kx in range(k):
                        i = base + kx * 4
                        a = self.data[i + 3]
                        sr += self.data[i] * a
                        sg += self.data[i + 1] * a
                        sb += self.data[i + 2] * a
                        sa += a
                o = (y * ow + x) * 4
                if sa > 0:
                    out.data[o] = int(round(sr / sa))
                    out.data[o + 1] = int(round(sg / sa))
                    out.data[o + 2] = int(round(sb / sa))
                    out.data[o + 3] = int(round(sa / (k * k)))
        return out


def hconcat(canvases: List[Canvas], gap: int = 4, bg: Sequence[int] = (0, 0, 0, 0)) -> Canvas:
    if not canvases:
        return Canvas(1, 1)
    h = max(c.h for c in canvases)
    w = sum(c.w for c in canvases) + gap * (len(canvases) - 1)
    out = Canvas(w, h)
    out.fill(bg if len(bg) > 3 else tuple(bg) + (255,))
    x = 0
    for c in canvases:
        # opaque copy (preview panels are opaque); transparent pixels keep bg
        out.blit(c, x, (h - c.h) // 2)
        x += c.w + gap
    return out


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)


def encode_png(canvas: Canvas) -> bytes:
    w, h = canvas.w, canvas.h
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += canvas.data[y * stride:(y + 1) * stride]
    return (b"\x89PNG\r\n\x1a\n"
            + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + _chunk(b"IEND", b""))


def write_png(path: Union[str, Path, None], canvas: Canvas) -> bytes:
    data = encode_png(canvas)
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return data


def png_size(src: Union[bytes, str, Path]) -> Tuple[int, int]:
    data = src if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    w, h = struct.unpack(">II", data[16:24])
    return w, h


_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_VALID_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
MAX_DECODE_PIXELS = 100_000_000


def _unfilter(raw: bytes, pos: int, rows: int, stride: int, bpp: int) -> Tuple[List[bytearray], int]:
    """Reverse the PNG scanline filters of `rows` rows (each 1 filter byte + `stride` bytes) starting at raw[pos]."""
    out: List[bytearray] = []
    prev = bytearray(stride)
    for _ in range(rows):
        if pos + 1 + stride > len(raw):
            raise ValueError("truncated PNG image data")
        ft = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        if ft == 0:
            pass
        elif ft == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 255
        elif ft == 2:
            line = bytearray([(a + b) & 255 for a, b in zip(line, prev)])
        elif ft == 3:
            for i in range(min(bpp, stride)):
                line[i] = (line[i] + (prev[i] >> 1)) & 255
            for i in range(bpp, stride):
                line[i] = (line[i] + ((line[i - bpp] + prev[i]) >> 1)) & 255
        elif ft == 4:
            for i in range(min(bpp, stride)):
                line[i] = (line[i] + prev[i]) & 255
            for i in range(bpp, stride):
                a = line[i - bpp]
                b = prev[i]
                c = prev[i - bpp]
                p = a + b - c
                pa = p - a if p >= a else a - p
                pb = p - b if p >= b else b - p
                pc = p - c if p >= c else c - p
                if pa <= pb and pa <= pc:
                    pr = a
                elif pb <= pc:
                    pr = b
                else:
                    pr = c
                line[i] = (line[i] + pr) & 255
        else:
            raise ValueError("bad PNG filter type %d" % ft)
        out.append(line)
        prev = line
    return out, pos


_UNPACK = {d: [tuple((b >> (8 - d * (k + 1))) & ((1 << d) - 1) for k in range(8 // d)) for b in range(256)] for d in (1, 2, 4)}


def _samples(line: bytearray, w: int, ctype: int, depth: int):
    """Sample values of one scanline as a sequence of ints (len w * channels): bytes for 8 bit, 16-bit words, unpacked bits."""
    n = w * _CHANNELS[ctype]
    if depth == 8:
        return line
    if depth == 16:
        return struct.unpack(">%dH" % n, bytes(line[:2 * n]))
    tab = _UNPACK[depth]
    out: List[int] = []
    for b in line:
        out.extend(tab[b])
    return out[:n]


def _rgba_row(line: bytearray, w: int, ctype: int, depth: int, plte, trns) -> bytearray:
    """One scanline -> RGBA8 row (w*4 bytes)."""
    out = bytearray(w * 4)
    out[3::4] = b"\xff" * w
    keyed = None  # per-pixel alpha overrides from a colour-key tRNS chunk
    if trns is not None and ctype in (0, 2):
        samples = _samples(line, w, ctype, depth)
        if ctype == 0:
            key = trns[0]
            keyed = [i for i in range(w) if samples[i] == key]
        else:
            key = (trns[0], trns[1], trns[2])
            keyed = [i for i in range(w) if (samples[3 * i], samples[3 * i + 1], samples[3 * i + 2]) == key]
    if ctype == 3:
        samples = line if depth == 8 else bytes(_samples(line, w, 3, depth))
        samples = bytes(samples[:w])
        pr, pg, pb, pa = plte
        out[0::4] = samples.translate(pr)
        out[1::4] = samples.translate(pg)
        out[2::4] = samples.translate(pb)
        if trns is not None:
            out[3::4] = samples.translate(pa)
        return out
    if depth == 16:
        line = bytearray(line[0::2])  # high bytes
    elif depth < 8:
        mx = (1 << depth) - 1
        samples = bytes([v * 255 // mx for v in _samples(line, w, ctype, depth)])
        line = bytearray(samples)
    if ctype == 6:
        out[:] = line[:w * 4]
    elif ctype == 2:
        out[0::4] = line[0:3 * w:3]
        out[1::4] = line[1:3 * w:3]
        out[2::4] = line[2:3 * w:3]
    elif ctype == 0:
        g = line[:w]
        out[0::4] = g
        out[1::4] = g
        out[2::4] = g
    elif ctype == 4:
        g = line[0:2 * w:2]
        out[0::4] = g
        out[1::4] = g
        out[2::4] = g
        out[3::4] = line[1:2 * w:2]
    if keyed:
        for i in keyed:
            out[4 * i + 3] = 0
    return out


def read_png(src: Union[bytes, bytearray, str, Path]) -> Canvas:
    """Decode any non-animated PNG (colour types 0/2/3/4/6, bit depths 1-16, all filters, Adam7, tRNS) to an 8-bit
    RGBA Canvas (16-bit samples keep their high byte). Raises ValueError on anything that is not a decodable PNG."""
    data = src if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes()
    if data[:8] != _PNG_SIG:
        raise ValueError("not a PNG file (bad signature)")
    pos = 8
    ihdr = None
    idat = []
    plte_raw = b""
    trns_raw = b""
    while pos + 8 <= len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = bytes(data[pos + 4:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if len(body) != n:
            raise ValueError("truncated PNG chunk %r" % tag)
        if tag == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", bytes(body[:13]))
        elif tag == b"PLTE":
            plte_raw = bytes(body)
        elif tag == b"tRNS":
            trns_raw = bytes(body)
        elif tag == b"IDAT":
            idat.append(bytes(body))
        elif tag == b"IEND":
            break
        pos += 12 + n
    if ihdr is None:
        raise ValueError("PNG has no IHDR chunk")
    w, h, depth, ctype, comp, filt, interlace = ihdr
    if ctype not in _CHANNELS or depth not in _VALID_DEPTHS[ctype]:
        raise ValueError("unsupported PNG colour type %d / bit depth %d" % (ctype, depth))
    if comp != 0 or filt != 0 or interlace not in (0, 1):
        raise ValueError("unsupported PNG compression/filter/interlace method")
    if w <= 0 or h <= 0 or w * h > MAX_DECODE_PIXELS:
        raise ValueError("PNG size %dx%d is out of range" % (w, h))
    if not idat:
        raise ValueError("PNG has no IDAT chunk")
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error as e:
        raise ValueError("corrupt PNG image data: %s" % e)
    plte = None
    trns = None
    if ctype == 3:
        if not plte_raw or len(plte_raw) % 3:
            raise ValueError("palette PNG without a valid PLTE chunk")
        np_ = len(plte_raw) // 3
        pal = [plte_raw[3 * i:3 * i + 3] for i in range(np_)] + [b"\x00\x00\x00"] * (256 - np_)
        alpha = list(trns_raw[:256]) + [255] * (256 - min(len(trns_raw), 256))
        plte = tuple(bytes(p[c] for p in pal[:256]) for c in range(3)) + (bytes(alpha),)
        if trns_raw:
            trns = True
    elif ctype in (0, 2) and trns_raw:
        need = 2 if ctype == 0 else 6
        if len(trns_raw) >= need:
            trns = struct.unpack(">%dH" % (need // 2), trns_raw[:need])
            if depth < 16:
                trns = tuple(v & ((1 << depth) - 1) if depth < 8 else v & 255 for v in trns)
    bits = _CHANNELS[ctype] * depth
    bpp = max(1, bits // 8)
    out = bytearray(w * h * 4)
    if interlace == 0:
        stride = (w * bits + 7) // 8
        rows, _ = _unfilter(raw, 0, h, stride, bpp)
        o = 0
        for line in rows:
            out[o:o + w * 4] = _rgba_row(line, w, ctype, depth, plte, trns)
            o += w * 4
    else:
        pos = 0
        for x0, y0, dx, dy in _ADAM7:
            pw = (w - x0 + dx - 1) // dx
            ph = (h - y0 + dy - 1) // dy
            if pw <= 0 or ph <= 0:
                continue
            stride = (pw * bits + 7) // 8
            rows, pos = _unfilter(raw, pos, ph, stride, bpp)
            for r, line in enumerate(rows):
                row = _rgba_row(line, pw, ctype, depth, plte, trns)
                base = (y0 + r * dy) * w * 4
                for k in range(4):
                    out[base + x0 * 4 + k:base + w * 4:dx * 4] = row[k::4]
    return Canvas(w, h, out)


def decode_png(src: Union[bytes, str, Path]) -> Canvas:
    """Decode a PNG to an RGBA Canvas (alias of read_png, kept for the writers' round-trip tests)."""
    return read_png(src)
