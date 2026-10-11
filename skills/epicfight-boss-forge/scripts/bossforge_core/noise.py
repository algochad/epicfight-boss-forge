"""Deterministic value noise, fbm and worley noise (pure Python)."""
from __future__ import annotations

import math
from typing import Tuple


def hash01(ix: int, iy: int, iz: int, seed: int = 0) -> float:
    h = (ix * 73856093 ^ iy * 19349663 ^ iz * 83492791 ^ (seed * 2654435761)) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 1274126177) & 0xFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFF) / 65535.0


def vnoise(x: float, y: float, z: float, seed: int = 0) -> float:
    ix, iy, iz = math.floor(x), math.floor(y), math.floor(z)
    fx, fy, fz = x - ix, y - iy, z - iz
    fx = fx * fx * (3 - 2 * fx)
    fy = fy * fy * (3 - 2 * fy)
    fz = fz * fz * (3 - 2 * fz)

    def c(a, b, d):
        return hash01(ix + a, iy + b, iz + d, seed)

    c00 = c(0, 0, 0) * (1 - fx) + c(1, 0, 0) * fx
    c10 = c(0, 1, 0) * (1 - fx) + c(1, 1, 0) * fx
    c01 = c(0, 0, 1) * (1 - fx) + c(1, 0, 1) * fx
    c11 = c(0, 1, 1) * (1 - fx) + c(1, 1, 1) * fx
    return (c00 * (1 - fy) + c10 * fy) * (1 - fz) + (c01 * (1 - fy) + c11 * fy) * fz


def fbm(x: float, y: float, z: float, octaves: int = 3, seed: int = 0) -> float:
    tot = 0.0
    amp = 1.0
    norm = 0.0
    for o in range(octaves):
        f = 2 ** o
        tot += amp * vnoise(x * f, y * f, z * f, seed + o * 17)
        norm += amp
        amp *= 0.5
    return tot / norm


def worley(x: float, y: float, z: float, seed: int = 0) -> Tuple[float, float]:
    ix, iy, iz = math.floor(x), math.floor(y), math.floor(z)
    f1 = f2 = 1e9
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for dz in (-1, 0, 1):
                cx, cy, cz = ix + dx, iy + dy, iz + dz
                px = cx + hash01(cx, cy, cz, seed)
                py = cy + hash01(cx, cy, cz, seed + 1)
                pz = cz + hash01(cx, cy, cz, seed + 2)
                d = math.sqrt((px - x) ** 2 + (py - y) ** 2 + (pz - z) ** 2)
                if d < f1:
                    f2 = f1
                    f1 = d
                elif d < f2:
                    f2 = d
    return f1, f2
