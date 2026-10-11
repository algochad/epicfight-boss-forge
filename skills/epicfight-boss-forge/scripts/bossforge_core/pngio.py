"""Minimal RGBA canvas and PNG writer (stdlib only)."""
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


def decode_png(src: Union[bytes, str, Path]) -> Canvas:
    """Decode PNGs produced by write_png (8-bit RGBA, filter 0 or any standard filter)."""
    data = src if isinstance(src, (bytes, bytearray)) else Path(src).read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG")
    pos = 8
    idat = b""
    w = h = 0
    while pos < len(data):
        n = struct.unpack(">I", data[pos:pos + 4])[0]
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype != 6:
                raise ValueError("only 8-bit RGBA PNGs are supported")
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray(h * stride)
    prev = bytearray(stride)
    p = 0
    for y in range(h):
        ft = raw[p]
        line = bytearray(raw[p + 1:p + 1 + stride])
        p += 1 + stride
        for i in range(stride):
            a = line[i - 4] if i >= 4 else 0
            b = prev[i]
            c = prev[i - 4] if i >= 4 else 0
            if ft == 1:
                line[i] = (line[i] + a) & 255
            elif ft == 2:
                line[i] = (line[i] + b) & 255
            elif ft == 3:
                line[i] = (line[i] + ((a + b) >> 1)) & 255
            elif ft == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 255
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return Canvas(w, h, out)
