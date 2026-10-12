"""Reference views: orthographic concept-art views of the boss (front / back / right / left) with a foreground mask and
an exact pixel <-> model projection. See references/reference-fit.md.

Coordinates: image pixels are continuous, pixel (i, j) covers [i, i+1) x [j, j+1) (centre i+0.5), origin top-left, y down.
Model space is the generated mesh BEFORE the entity scale: y up, feet at y = 0, the character faces -z, its right is +x.

    front: px = cx - s*x   back: px = cx + s*x   right: px = cx - s*z   left: px = cx + s*z   all: py = gy - s*y
"""
from __future__ import annotations

import json
import math
from array import array
from itertools import accumulate
from operator import add
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import refmask
from .pngio import Canvas, png_size, read_png
from .spec import BossForgeError, Err

VIEW_NAMES = ("front", "back", "right", "left")
MASK_MODES = ("auto", "color", "image", "polygon")
# name -> (direction toward the camera, in-plane model axis, sign of d(px)/d(axis))
_VIEW_DEF = {
    "front": ((0.0, 0.0, -1.0), 0, -1.0),
    "back": ((0.0, 0.0, 1.0), 0, 1.0),
    "right": ((1.0, 0.0, 0.0), 2, -1.0),
    "left": ((-1.0, 0.0, 0.0), 2, 1.0),
}
PX_PER_BLOCK_RANGE = (4.0, 4096.0)
ERODE_RANGE = (0, 12)
TOLERANCE_RANGE = (0.0, 255.0)
VIEW_KEYS = ("image", "crop", "mask", "ground_y", "center_x", "px_per_block", "top_y", "top_model_y")
MASK_KEYS = ("mode", "bg", "tolerance", "image", "include", "exclude", "keep", "erode", "outline", "seal", "min_island")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ---------------------------------------------------------------------------------------------- View
class View:
    """One reference image with calibration and mask (see module docstring)."""

    def __init__(self, name: str, width: int, height: int, rgba: bytearray, mask: bytearray,
                 crop: Tuple[int, int, int, int], px_per_block: float, ground_y: float, center_x: float):
        if name not in _VIEW_DEF:
            raise BossForgeError("/reference/views/" + str(name), "reference_unknown_view",
                                 "view %r is not one of %s" % (name, ", ".join(VIEW_NAMES)))
        self.name = name
        self.width = int(width)
        self.height = int(height)
        self.rgba = rgba
        self.mask = mask
        self.crop = tuple(int(v) for v in crop)  # type: ignore[assignment]
        self.px_per_block = float(px_per_block)
        self.ground_y = float(ground_y)
        self.center_x = float(center_x)
        toward, axis, sign = _VIEW_DEF[name]
        self.toward = toward
        self._axis = axis  # model axis index that runs across the picture (0 = x, 2 = z)
        self._sign = sign  # d(px)/d(axis) / px_per_block
        self._sat: Optional[Tuple[int, int, int, int, int, List[array]]] = None

    @classmethod
    def synthetic(cls, name: str, rgba, mask, width: int, height: int, px_per_block: float, ground_y: float,
                  center_x: float, crop: Optional[Tuple[int, int, int, int]] = None) -> "View":
        """A View from in-memory buffers (tests). `mask` bytes: nonzero = foreground; stored as 0/255."""
        m = bytearray(mask)
        if any(v not in (0, 255) for v in set(m)):
            m = bytearray(bytes(m).translate(bytes([0] + [255] * 255)))
        return cls(name, width, height, bytearray(rgba), m, crop if crop is not None else (0, 0, width, height),
                   px_per_block, ground_y, center_x)

    # ---- projection
    def project(self, x: float, y: float, z: float) -> Tuple[float, float, float]:
        """(px, py, depth): image position of the model point and its distance along the direction to the camera."""
        u = z if self._axis == 2 else x
        tx, ty, tz = self.toward
        return (self.center_x + self._sign * self.px_per_block * u, self.ground_y - self.px_per_block * y,
                tx * x + ty * y + tz * z)

    def unproject(self, px: float, py: float) -> Tuple[float, float]:
        """In-plane model coordinates of an image position: (x, y) for front/back, (z, y) for right/left."""
        return (self._sign * (px - self.center_x) / self.px_per_block, (self.ground_y - py) / self.px_per_block)

    def facing(self, nx: float, ny: float, nz: float) -> float:
        tx, ty, tz = self.toward
        return nx * tx + ny * ty + nz * tz

    # ---- pixel access
    def mask_at(self, px: float, py: float) -> bool:
        x, y = math.floor(px), math.floor(py)
        return 0 <= x < self.width and 0 <= y < self.height and self.mask[y * self.width + x] != 0

    def color_at(self, px: float, py: float) -> Tuple[int, int, int]:
        x = min(max(int(math.floor(px)), 0), self.width - 1)
        y = min(max(int(math.floor(py)), 0), self.height - 1)
        i = (y * self.width + x) * 4
        d = self.rgba
        return (d[i], d[i + 1], d[i + 2])

    # ---- area-weighted statistics (summed-area tables, bilinear at fractional edges = exact box integrals)
    def mask_box(self) -> Optional[Tuple[int, int, int, int]]:
        """Half-open pixel bounds of the foreground, None when the mask is empty."""
        return refmask.bbox(self.mask, self.width, self.height)

    def _tables(self):
        if self._sat is None:
            box = self.mask_box() or (0, 0, 0, 0)
            x0, y0, x1, y1 = box
            bw, bh = x1 - x0, y1 - y0
            sats = [array("q", bytes(8 * (bw + 1))) for _ in range(4)]
            w = self.width
            z = bytes(256)
            t01 = bytes([0] + [1] * 255)
            prev = [array("q", bytes(8 * (bw + 1))) for _ in range(4)]
            for yy in range(y0, y1):
                m01 = bytes(self.mask[yy * w + x0:yy * w + x1]).translate(t01)
                base = (yy * w + x0) * 4
                rows = [m01]
                for c in range(3):
                    ch = self.rgba[base + c:base + 4 * bw:4]
                    rows.append(bytes([a * m for a, m in zip(ch, m01)]))
                for k in range(4):
                    cur = array("q", [0])
                    cur.extend(map(add, prev[k][1:], accumulate(rows[k])))
                    sats[k].extend(cur)
                    prev[k] = cur
            self._sat = (x0, y0, bw, bh, bw + 1, sats)
        return self._sat

    def _integral(self, px: float, py: float) -> List[float]:
        x0, y0, bw, bh, W, sats = self._tables()
        x = min(max(px - x0, 0.0), float(bw))
        y = min(max(py - y0, 0.0), float(bh))
        if bw == 0 or bh == 0:
            return [0.0, 0.0, 0.0, 0.0]
        i = min(int(x), bw - 1)
        j = min(int(y), bh - 1)
        a, b = x - i, y - j
        o = j * W + i
        out = []
        for s in sats:
            out.append(s[o] * (1 - a) * (1 - b) + s[o + 1] * a * (1 - b) + s[o + W] * (1 - a) * b + s[o + W + 1] * a * b)
        return out

    def _box_sums(self, px0: float, py0: float, px1: float, py1: float) -> List[float]:
        if px1 < px0:
            px0, px1 = px1, px0
        if py1 < py0:
            py0, py1 = py1, py0
        a, b, c, d = self._integral(px1, py1), self._integral(px0, py1), self._integral(px1, py0), self._integral(px0, py0)
        return [a[k] - b[k] - c[k] + d[k] for k in range(4)]

    def mask_fraction(self, px0: float, py0: float, px1: float, py1: float) -> float:
        """Fraction (0..1) of the image-pixel rectangle covered by foreground (area-weighted; outside the image = 0)."""
        area = abs((px1 - px0) * (py1 - py0))
        if area <= 1e-12:
            return 1.0 if self.mask_at(px0, py0) else 0.0
        return min(1.0, max(0.0, self._box_sums(px0, py0, px1, py1)[0] / area))

    def mask_cells(self, u0: float, v0: float, u1: float, v1: float) -> float:
        """Fraction of foreground inside the in-plane model rectangle (u across, v up; see `unproject`)."""
        s = self.px_per_block
        pa = self.center_x + self._sign * s * u0
        pb = self.center_x + self._sign * s * u1
        return self.mask_fraction(min(pa, pb), self.ground_y - s * max(v0, v1), max(pa, pb), self.ground_y - s * min(v0, v1))

    def average(self, px0: float, py0: float, px1: float, py1: float, fg_only: bool = True) -> Optional[Tuple[int, int, int]]:
        """Area-weighted mean colour of the pixel rectangle (foreground pixels only unless fg_only=False);
        None when it contains no foreground (fg_only) / lies outside the image."""
        if px1 < px0:
            px0, px1 = px1, px0
        if py1 < py0:
            py0, py1 = py1, py0
        if px1 - px0 < 1e-9:
            px0, px1 = math.floor(px0), math.floor(px0) + 1.0
        if py1 - py0 < 1e-9:
            py0, py1 = math.floor(py0), math.floor(py0) + 1.0
        if fg_only:
            m, r, g, b = self._box_sums(px0, py0, px1, py1)
            if m < 1e-6:
                return None
            return (min(255, int(r / m + 0.5)), min(255, int(g / m + 0.5)), min(255, int(b / m + 0.5)))
        w, h = self.width, self.height
        xa, xb = max(0, int(math.floor(px0))), min(w, int(math.ceil(px1)))
        ya, yb = max(0, int(math.floor(py0))), min(h, int(math.ceil(py1)))
        tot = sr = sg = sb = 0.0
        for y in range(ya, yb):
            wy = min(py1, y + 1) - max(py0, y)
            for x in range(xa, xb):
                wt = wy * (min(px1, x + 1) - max(px0, x))
                i = (y * w + x) * 4
                tot += wt
                sr += wt * self.rgba[i]
                sg += wt * self.rgba[i + 1]
                sb += wt * self.rgba[i + 2]
        if tot < 1e-9:
            return None
        return (int(sr / tot + 0.5), int(sg / tot + 0.5), int(sb / tot + 0.5))

    # ---- helpers for fit / presets
    def model_extent(self) -> Optional[Dict[str, float]]:
        """Foreground extent in model blocks: {u_min, u_max, y_min, y_max} (u = x for front/back, z for right/left)."""
        box = self.mask_box()
        if box is None:
            return None
        ua, va = self.unproject(box[0], box[1])
        ub, vb = self.unproject(box[2], box[3])
        return {"u_min": min(ua, ub), "u_max": max(ua, ub), "y_min": min(va, vb), "y_max": max(va, vb)}


# ---------------------------------------------------------------------------------------------- validation
def _poly_errs(errs: List[Err], path: str, polys: Any) -> None:
    if not isinstance(polys, list) or not polys:
        errs.append(Err(path, "reference_bad_mask", "expected a non-empty list of polygons [[[x,y],[x,y],[x,y],...],...]"))
        return
    for i, poly in enumerate(polys):
        ok = isinstance(poly, list) and len(poly) >= 3 and all(
            isinstance(p, (list, tuple)) and len(p) == 2 and _num(p[0]) and _num(p[1]) for p in poly)
        if not ok:
            errs.append(Err("%s/%d" % (path, i), "reference_bad_mask", "a polygon is a list of >= 3 [x, y] pixel points"))


def _hex_rgb(s: Any) -> Optional[Tuple[int, int, int]]:
    if not isinstance(s, str) or not s.startswith("#"):
        return None
    h = s[1:]
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    except ValueError:
        return None


def validate_mask(errs: List[Err], path: str, m: Any) -> None:
    if m is None:
        return
    if not isinstance(m, dict):
        errs.append(Err(path, "reference_bad_mask", "mask must be an object"))
        return
    for k in m:
        if k not in MASK_KEYS:
            errs.append(Err(path + "/" + k, "reference_bad_mask", "unknown mask key %r; allowed: %s" % (k, ", ".join(MASK_KEYS))))
    mode = m.get("mode", "auto")
    if mode not in MASK_MODES:
        errs.append(Err(path + "/mode", "reference_bad_mask", "mode %r is not one of %s" % (mode, ", ".join(MASK_MODES))))
        return
    if mode == "color":
        bg = m.get("bg")
        if not isinstance(bg, list) or not 1 <= len(bg) <= 8 or any(_hex_rgb(c) is None for c in bg):
            errs.append(Err(path + "/bg", "reference_bad_mask", "mode color needs bg: a list of 1-8 hex colours like \"#8a8a8a\""))
    if mode == "image" and not (isinstance(m.get("image"), str) and m["image"]):
        errs.append(Err(path + "/image", "reference_bad_mask", "mode image needs image: a mask PNG path (white/opaque = character)"))
    if mode == "polygon" and not m.get("include"):
        errs.append(Err(path + "/include", "reference_bad_mask", "mode polygon needs include: the character outline polygon(s)"))
    if "tolerance" in m and not (_num(m["tolerance"]) and TOLERANCE_RANGE[0] <= m["tolerance"] <= TOLERANCE_RANGE[1]):
        errs.append(Err(path + "/tolerance", "reference_bad_mask", "tolerance must be a number in %g..%g" % TOLERANCE_RANGE))
    if "erode" in m and not (isinstance(m["erode"], int) and not isinstance(m["erode"], bool) and ERODE_RANGE[0] <= m["erode"] <= ERODE_RANGE[1]):
        errs.append(Err(path + "/erode", "reference_bad_mask", "erode must be an integer in %d..%d" % ERODE_RANGE))
    if "outline" in m and not (isinstance(m["outline"], int) and not isinstance(m["outline"], bool) and 0 <= m["outline"] <= 8):
        errs.append(Err(path + "/outline", "reference_bad_mask", "outline (rings of dark outline/shadow pixels peeled off the silhouette) must be an integer in 0..8"))
    if "seal" in m and not (isinstance(m["seal"], int) and not isinstance(m["seal"], bool) and 0 <= m["seal"] <= 3):
        errs.append(Err(path + "/seal", "reference_bad_mask", "seal (px of outline gap the background flood may not cross; 0 for outline-less art) must be an integer in 0..3"))
    if "min_island" in m and not (_num(m["min_island"]) and 0 <= m["min_island"] <= 1):
        errs.append(Err(path + "/min_island", "reference_bad_mask", "min_island (kept component size as a fraction of the largest) must be in 0..1"))
    for k in ("include", "exclude"):
        if k in m:
            _poly_errs(errs, path + "/" + k, m[k])
    if "keep" in m and not (isinstance(m["keep"], list) and m["keep"] and all(
            isinstance(p, (list, tuple)) and len(p) == 2 and _num(p[0]) and _num(p[1]) for p in m["keep"])):
        errs.append(Err(path + "/keep", "reference_bad_mask", "keep must be a list of [x, y] image points; only the foreground components containing one of them are kept"))


def validate_view(errs: List[Err], path: str, name: str, v: Any) -> None:
    """Static validation of one `reference.views.<name>` object (no file access)."""
    if name not in VIEW_NAMES:
        errs.append(Err(path, "reference_unknown_view", "view %r is not one of %s" % (name, ", ".join(VIEW_NAMES))))
        return
    if not isinstance(v, dict):
        errs.append(Err(path, "reference_bad_image", "a view must be an object {image, crop, mask, ground_y, center_x, px_per_block}"))
        return
    for k in v:
        if k not in VIEW_KEYS:
            errs.append(Err(path + "/" + k, "unknown_value", "unknown view key %r; allowed: %s" % (k, ", ".join(VIEW_KEYS))))
    if not (isinstance(v.get("image"), str) and v["image"]):
        errs.append(Err(path + "/image", "reference_bad_image", "image must be a PNG path (relative to the spec file or absolute); convert other formats with `magick in.webp out.png`"))
    crop = v.get("crop")
    if crop is not None:
        if not (isinstance(crop, list) and len(crop) == 4 and all(_num(c) for c in crop)):
            errs.append(Err(path + "/crop", "reference_bad_crop", "crop must be [x0, y0, x1, y1] in image pixels"))
        elif not (crop[0] < crop[2] and crop[1] < crop[3] and crop[0] >= 0 and crop[1] >= 0):
            errs.append(Err(path + "/crop", "reference_bad_crop", "crop needs 0 <= x0 < x1 and 0 <= y0 < y1"))
    for k in ("ground_y", "center_x"):
        if not _num(v.get(k)):
            errs.append(Err(path + "/" + k, "reference_bad_calibration", "%s is required (image pixels, a number)" % k))
    has_s, has_top = "px_per_block" in v, ("top_y" in v or "top_model_y" in v)
    if has_s == has_top and not (has_s and has_top):
        errs.append(Err(path, "reference_bad_calibration", "give px_per_block, or both top_y and top_model_y (not both ways, not neither)"))
    elif has_s and has_top:
        errs.append(Err(path, "reference_bad_calibration", "give px_per_block or top_y + top_model_y, not both"))
    elif has_s:
        if not (_num(v["px_per_block"]) and PX_PER_BLOCK_RANGE[0] <= v["px_per_block"] <= PX_PER_BLOCK_RANGE[1]):
            errs.append(Err(path + "/px_per_block", "reference_bad_calibration", "px_per_block must be a number in %g..%g" % PX_PER_BLOCK_RANGE))
    else:
        if not (_num(v.get("top_y")) and _num(v.get("top_model_y")) and v["top_model_y"] > 0):
            errs.append(Err(path, "reference_bad_calibration", "top_y (image row of the top of the character) and top_model_y (> 0, its height in model blocks) must both be numbers"))
        elif _num(v.get("ground_y")) and v["top_y"] >= v["ground_y"]:
            errs.append(Err(path + "/top_y", "reference_bad_calibration", "top_y must be above ground_y (smaller row number)"))
    validate_mask(errs, path + "/mask", v.get("mask"))


def validate_reference(ref: Any, base_dir=None) -> Tuple[List[Err], List[Err]]:
    """(errors, warnings) of the `views` part of a spec's `reference` block. Image files that cannot be found are only
    warnings here (they are hard errors when views are loaded)."""
    errs: List[Err] = []
    warns: List[Err] = []
    if not isinstance(ref, dict):
        return [Err("/reference", "reference_bad_image", "reference must be an object {views: {...}, bake: {...}}")], warns
    views = ref.get("views")
    if "views" in ref:
        if not isinstance(views, dict) or not views:
            errs.append(Err("/reference/views", "reference_bad_image", "views must be a non-empty object keyed by front/back/right/left"))
        else:
            for name, v in views.items():
                validate_view(errs, "/reference/views/" + str(name), str(name), v)
                if base_dir is not None and isinstance(v, dict):
                    sizes: Dict[str, Tuple[int, int]] = {}
                    for key, val in _image_refs(v):
                        p = _resolve(base_dir, val)
                        if not p.is_file():
                            warns.append(Err("/reference/views/%s%s" % (name, key), "reference_image_missing", "file not found: %s" % p))
                            continue
                        try:
                            sizes[key] = png_size(p)
                        except (ValueError, OSError):
                            errs.append(Err("/reference/views/%s%s" % (name, key), "reference_bad_image",
                                            "%s is not a PNG; convert it first, e.g. `magick %s out.png`" % (p, p.name)))
                    size = sizes.get("/image")
                    crop = v.get("crop")
                    if size and isinstance(crop, list) and len(crop) == 4 and all(_num(c) for c in crop) and (
                            crop[2] > size[0] or crop[3] > size[1]):
                        errs.append(Err("/reference/views/%s/crop" % name, "reference_bad_crop", "crop %s is outside the %dx%d image" % (crop, size[0], size[1])))
                    if size and sizes.get("/mask/image") not in (None, size):
                        errs.append(Err("/reference/views/%s/mask/image" % name, "reference_bad_mask", "mask image is %dx%d but the view image is %dx%d" % (sizes["/mask/image"] + size)))
    for k in ref:
        if k not in ("views", "bake"):
            errs.append(Err("/reference/" + str(k), "unknown_value", "unknown reference key %r; allowed: views, bake" % k))
    return errs, warns


def _image_refs(v: dict):
    if isinstance(v.get("image"), str):
        yield "/image", v["image"]
    m = v.get("mask")
    if isinstance(m, dict) and m.get("mode") == "image" and isinstance(m.get("image"), str):
        yield "/mask/image", m["image"]


# ---------------------------------------------------------------------------------------------- loading
_IMAGE_CACHE: Dict[Tuple[str, int, int], Canvas] = {}
_VIEWS_CACHE: Dict[str, Dict[str, "View"]] = {}


def _resolve(base_dir, rel: str) -> Path:
    p = Path(rel)
    if p.is_absolute():
        return p
    return Path(base_dir if base_dir is not None else ".") / p


def _load_image(path: Path, err_path: str) -> Canvas:
    try:
        st = path.stat()
    except OSError:
        raise BossForgeError(err_path, "reference_bad_image", "image file not found: %s" % path)
    key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
    c = _IMAGE_CACHE.get(key)
    if c is None:
        try:
            c = read_png(path)
        except (ValueError, OSError) as e:
            raise BossForgeError(err_path, "reference_bad_image",
                                 "%s is not a readable PNG (%s); convert it first, e.g. `magick %s out.png`" % (path, e, path.name))
        if len(_IMAGE_CACHE) > 6:
            _IMAGE_CACHE.clear()
        _IMAGE_CACHE[key] = c
    return c


def _calibration(v: dict, path: str, w: int, h: int) -> Tuple[float, float, float]:
    gy, cx = v["ground_y"], v["center_x"]
    if "px_per_block" in v:
        s = float(v["px_per_block"])
    else:
        s = (float(gy) - float(v["top_y"])) / float(v["top_model_y"])
    if not (PX_PER_BLOCK_RANGE[0] <= s <= PX_PER_BLOCK_RANGE[1]):
        raise BossForgeError(path, "reference_bad_calibration", "px_per_block %.3f is outside %g..%g" % (s, PX_PER_BLOCK_RANGE[0], PX_PER_BLOCK_RANGE[1]))
    if not (0 <= gy <= h * 2 and -w <= cx <= w * 2):
        raise BossForgeError(path, "reference_bad_calibration", "ground_y %s / center_x %s are far outside the %dx%d image" % (gy, cx, w, h))
    return s, float(gy), float(cx)


def build_mask(canvas: Canvas, crop: Tuple[int, int, int, int], m: Optional[dict], base_dir, path: str) -> bytearray:
    """The 0/255 foreground mask of the full image for the mask spec `m` (None = auto)."""
    w, h = canvas.w, canvas.h
    m = m or {}
    mode = m.get("mode", "auto")
    erode = int(m.get("erode", 0 if mode == "polygon" else 1))
    min_island = float(m.get("min_island", 0.03))
    seal = int(m.get("seal", 1))
    if mode == "auto":
        from . import automask
        fg = automask.auto_mask(canvas.data, w, h, crop, float(m.get("tolerance", automask.AUTO_MARGIN)),
                                min_island=min_island, seal=seal)
    elif mode == "color":
        from . import automask
        bgs = [_hex_rgb(c) for c in m.get("bg", [])]
        fg = automask.color_mask(canvas.data, w, h, crop, [b for b in bgs if b is not None], float(m.get("tolerance", 28)),
                                 min_island=min_island, seal=seal)
    elif mode == "image":
        mc = _load_image(_resolve(base_dir, m["image"]), path + "/image")
        if (mc.w, mc.h) != (w, h):
            raise BossForgeError(path + "/image", "reference_bad_mask", "mask image is %dx%d but the view image is %dx%d" % (mc.w, mc.h, w, h))
        a = mc.data[3::4]
        if a.count(255) != len(a):
            fg = bytearray(a.translate(bytes([0] * 128 + [1] * 128)))
        else:
            fg = bytearray(mc.data[0::4].translate(bytes([0] * 128 + [1] * 128)))
    else:  # polygon
        fg = refmask.fill_polygons(m["include"], w, h)
    refmask.restrict_rect(fg, w, h, crop)
    if mode != "polygon" and m.get("include"):
        fg = refmask.land(fg, refmask.fill_polygons(m["include"], w, h))
    if m.get("exclude"):
        fg = refmask.land(fg, refmask.invert(refmask.fill_polygons(m["exclude"], w, h)))
    if m.get("keep"):
        comps = refmask.Components(fg, w, h)
        wanted = set()
        for k, pt in enumerate(m["keep"]):
            x, y = int(math.floor(pt[0])), int(math.floor(pt[1]))
            hit = [c for c in range(len(comps)) if any(ry == y and x0 <= x < x1 for ry, x0, x1 in comps.runs[c])] \
                if 0 <= x < w and 0 <= y < h else []
            if not hit:
                raise BossForgeError("%s/keep/%d" % (path, k), "reference_bad_mask",
                                     "keep point (%s, %s) is not on the foreground of the mask (mode %s)" % (pt[0], pt[1], mode))
            wanted.update(hit)
        fg = comps.paint(sorted(wanted))
    outline = int(m.get("outline", 0))
    if outline:
        from . import automask
        fg = refmask.peel(fg, automask.dark_mask(canvas.data, w, h), w, h, outline)
    if erode:
        fg = refmask.erode(fg, w, h, erode)
    if not any(fg):
        raise BossForgeError(path, "reference_bad_mask", "the mask is empty (mode %s): adjust bg/tolerance/include or use another mode" % mode)
    return refmask.finish(fg)


def load_views(reference: dict, base_dir) -> Dict[str, View]:
    """Load every view of a spec's `reference` block. Raises BossForgeError (reference_bad_image / _bad_crop /
    _bad_calibration / _bad_mask / _unknown_view)."""
    if not isinstance(reference, dict) or not isinstance(reference.get("views"), dict) or not reference["views"]:
        raise BossForgeError("/reference/views", "reference_bad_image", "reference.views must be a non-empty object")
    out: Dict[str, View] = {}
    for name, v in reference["views"].items():
        path = "/reference/views/%s" % name
        errs: List[Err] = []
        validate_view(errs, path, str(name), v)
        if errs:
            raise BossForgeError(errs[0].path, errs[0].code, errs[0].message)
        canvas = _load_image(_resolve(base_dir, v["image"]), path + "/image")
        w, h = canvas.w, canvas.h
        crop = v.get("crop") or [0, 0, w, h]
        x0, y0, x1, y1 = (int(round(c)) for c in crop)
        if not (0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h):
            raise BossForgeError(path + "/crop", "reference_bad_crop", "crop %s is outside the %dx%d image" % ([x0, y0, x1, y1], w, h))
        s, gy, cx = _calibration(v, path, w, h)
        mask = build_mask(canvas, (x0, y0, x1, y1), v.get("mask"), base_dir, path + "/mask")
        out[name] = View(name, w, h, canvas.data, mask, (x0, y0, x1, y1), s, gy, cx)
    return out


def views_for(n: dict) -> Dict[str, View]:
    """The views of a (normalised) spec: {} without a `reference.views` block. Honours n["_views"] (ready-made View
    objects, for tests) and caches by the reference JSON, the spec directory and the files' mtimes."""
    if n.get("_views"):
        return n["_views"]
    ref = n.get("reference")
    if not isinstance(ref, dict) or not ref.get("views"):
        return {}
    base = n.get("_spec_dir") or "."
    stamps: List[Any] = []
    for v in ref["views"].values():
        for _k, rel in _image_refs(v if isinstance(v, dict) else {}):
            try:
                st = _resolve(base, rel).stat()
                stamps.append((st.st_mtime_ns, st.st_size))
            except OSError:
                stamps.append(None)
    key = json.dumps([ref["views"], str(base), stamps], sort_keys=True, default=str)
    got = _VIEWS_CACHE.get(key)
    if got is None:
        got = load_views(ref, base)
        if len(_VIEWS_CACHE) > 4:
            _VIEWS_CACHE.clear()
        _VIEWS_CACHE[key] = got
    return got
