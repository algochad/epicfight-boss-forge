import _path  # noqa
import copy
import json
import math
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from bossforge_core import automask, refmask, reference, spec
from bossforge_core.pngio import Canvas, decode_png, read_png, write_png
from bossforge_core.reference import View
from bossforge_core.spec import BossForgeError


# ------------------------------------------------------------------ tiny PNG encoder (all colour types / depths / filters / Adam7)
def _lcg(seed):
    s = seed
    while True:
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        yield s >> 8


def _pack(samples, depth):
    if depth == 8:
        return bytes(samples)
    if depth == 16:
        return b"".join(struct.pack(">H", v) for v in samples)
    out = bytearray()
    per = 8 // depth
    for i in range(0, len(samples), per):
        b = 0
        chunk = samples[i:i + per]
        for k in range(per):
            b = (b << depth) | (chunk[k] if k < len(chunk) else 0)
        out.append(b)
    return bytes(out)


def _filter(ft, row, prev, bpp):
    out = bytearray()
    for i, x in enumerate(row):
        a = row[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ft == 0:
            p = 0
        elif ft == 1:
            p = a
        elif ft == 2:
            p = b
        elif ft == 3:
            p = (a + b) // 2
        else:
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
            p = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        out.append((x - p) & 255)
    return bytes([ft]) + bytes(out)


def _chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


def encode(w, h, ctype, depth, samples, interlace=False, palette=None, trns=None):
    """samples: per pixel a tuple of channel values (already in the file's bit depth)."""
    nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bits = nch * depth
    bpp = max(1, bits // 8)
    passes = ([(0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2)] if interlace
              else [(0, 0, 1, 1)])
    raw = bytearray()
    ftn = 0
    for x0, y0, dx, dy in passes:
        pw = (w - x0 + dx - 1) // dx
        ph = (h - y0 + dy - 1) // dy
        if pw <= 0 or ph <= 0:
            continue
        prev = bytes((pw * bits + 7) // 8)
        for j in range(ph):
            flat = []
            for i in range(pw):
                flat.extend(samples[(y0 + j * dy) * w + x0 + i * dx])
            row = _pack(flat, depth)
            raw += _filter(ftn % 5, row, prev, bpp)
            ftn += 1
            prev = row
    body = _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, 1 if interlace else 0))
    if palette is not None:
        body += _chunk(b"PLTE", b"".join(bytes(p) for p in palette))
    if trns is not None:
        body += _chunk(b"tRNS", trns)
    return b"\x89PNG\r\n\x1a\n" + body + _chunk(b"IDAT", zlib.compress(bytes(raw))) + _chunk(b"IEND", b"")


class PngReaderTests(unittest.TestCase):
    W, H = 13, 11

    def check(self, ctype, depth, interlace, trns=None, palette=None):
        g = _lcg(ctype * 100 + depth)
        nch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
        top = (1 << depth) - 1
        n_pal = len(palette) if palette else 0
        samples = [tuple((next(g) % (n_pal if ctype == 3 else top + 1)) for _ in range(nch)) for _ in range(self.W * self.H)]
        data = encode(self.W, self.H, ctype, depth, samples, interlace, palette, trns)
        c = read_png(data)
        self.assertEqual((c.w, c.h), (self.W, self.H))
        for i, s in enumerate(samples):
            if depth == 16:
                v = tuple(x >> 8 for x in s)
            elif ctype == 3:
                v = s
            else:
                v = tuple(x * 255 // top for x in s)
            if ctype == 0:
                exp = (v[0], v[0], v[0], 255)
            elif ctype == 2:
                exp = (v[0], v[1], v[2], 255)
            elif ctype == 3:
                exp = tuple(palette[s[0]]) + ((trns[s[0]] if trns and s[0] < len(trns) else 255),)
            elif ctype == 4:
                exp = (v[0], v[0], v[0], v[1])
            else:
                exp = tuple(v)
            x, y = i % self.W, i // self.W
            self.assertEqual(c.get(x, y), exp, "type %d depth %d interlace %s pixel %d,%d" % (ctype, depth, interlace, x, y))
        return c

    def test_all_colour_types_depths_filters_and_adam7(self):
        palette = [(10, 20, 30), (200, 100, 50), (0, 255, 0), (255, 0, 255), (9, 9, 9), (77, 88, 99), (1, 2, 3), (250, 251, 252)]
        for ctype, depths in ((0, (1, 2, 4, 8, 16)), (2, (8, 16)), (3, (1, 2, 4, 8)), (4, (8, 16)), (6, (8, 16))):
            for depth in depths:
                for interlace in (False, True):
                    pal = palette[:min(len(palette), 1 << depth)] if ctype == 3 else None
                    self.check(ctype, depth, interlace, palette=pal)

    def test_palette_transparency_and_colour_key(self):
        pal = [(10, 20, 30), (200, 100, 50), (0, 255, 0), (255, 0, 255)]
        c = self.check(3, 8, False, trns=bytes([0, 128]), palette=pal)
        self.assertTrue(any(c.get(x, y)[3] == 0 for y in range(self.H) for x in range(self.W)))
        # colour-key transparency of a grey 8-bit image: pixels equal to the key get alpha 0
        samples = [(0,), (50,), (50,), (200,)]
        c = read_png(encode(2, 2, 0, 8, samples, trns=struct.pack(">H", 50)))
        self.assertEqual([c.get(x, y)[3] for y in range(2) for x in range(2)], [255, 0, 0, 255])
        samples = [(1, 2, 3), (4, 5, 6), (1, 2, 3), (7, 8, 9)]
        c = read_png(encode(2, 2, 2, 8, samples, trns=struct.pack(">HHH", 1, 2, 3)))
        self.assertEqual([c.get(x, y)[3] for y in range(2) for x in range(2)], [0, 255, 0, 255])

    def test_decode_png_is_the_same_reader_and_writer_roundtrip(self):
        c = Canvas(7, 5)
        for y in range(5):
            for x in range(7):
                c.set(x, y, (x * 30, y * 40, (x + y) * 10, 255 - x))
        data = write_png(None, c)
        self.assertEqual(decode_png(data).data, c.data)
        self.assertEqual(read_png(data).data, c.data)

    def test_bad_png_raises_value_error(self):
        for bad in (b"RIFF....WEBPVP8 ", b"\x89PNG\r\n\x1a\n", b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 7, 6, 0, 0, 0))):
            with self.assertRaises(ValueError):
                read_png(bad)


# ------------------------------------------------------------------ views
def synthetic_view(name="front", w=64, h=48, s=16.0, gy=40.0, cx=32.0, rect=(20, 10, 44, 40)):
    rgba = bytearray(w * h * 4)
    mask = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            i = y * w + x
            inside = rect[0] <= x < rect[2] and rect[1] <= y < rect[3]
            rgba[i * 4:i * 4 + 4] = bytes((200, 40, 40, 255) if (inside and x < 32) else ((40, 40, 200, 255) if inside else (90, 90, 90, 255)))
            mask[i] = 255 if inside else 0
    return View.synthetic(name, rgba, mask, w, h, s, gy, cx)


class ProjectionTests(unittest.TestCase):
    PTS = [(0.0, 0.0, 0.0), (0.5, 1.2, -0.3), (-0.7, 2.0, 0.4), (1.1, 0.3, 0.9)]

    def test_formulas_for_all_four_views(self):
        s, cx, gy = 50.0, 120.0, 400.0
        for name in reference.VIEW_NAMES:
            v = View.synthetic(name, bytes(4), bytes(1), 1, 1, s, gy, cx)
            for x, y, z in self.PTS:
                px, py, depth = v.project(x, y, z)
                self.assertAlmostEqual(py, gy - s * y, 9)
                exp = {"front": cx - s * x, "back": cx + s * x, "right": cx - s * z, "left": cx + s * z}[name]
                self.assertAlmostEqual(px, exp, 9, name)
                toward = {"front": (0, 0, -1), "back": (0, 0, 1), "right": (1, 0, 0), "left": (-1, 0, 0)}[name]
                self.assertEqual(v.toward, tuple(float(t) for t in toward))
                self.assertAlmostEqual(depth, x * toward[0] + y * toward[1] + z * toward[2], 9)
                u, vv = v.unproject(px, py)
                self.assertAlmostEqual(u, z if name in ("right", "left") else x, 9)
                self.assertAlmostEqual(vv, y, 9)
                self.assertEqual(v.facing(*toward), 1.0)
                self.assertEqual(v.facing(*[-t for t in toward]), -1.0)

    def test_front_view_shows_the_characters_right_on_the_image_left_and_right_view_faces_image_right(self):
        s, cx = 100.0, 500.0
        front = View.synthetic("front", bytes(4), bytes(1), 1, 1, s, 100.0, cx)
        self.assertLess(front.project(0.3, 0, 0)[0], cx)  # +x = the character's right appears on the image left
        back = View.synthetic("back", bytes(4), bytes(1), 1, 1, s, 100.0, cx)
        self.assertGreater(back.project(0.3, 0, 0)[0], cx)
        right = View.synthetic("right", bytes(4), bytes(1), 1, 1, s, 100.0, cx)
        self.assertGreater(right.project(0, 0, -0.3)[0], cx)  # the character faces -z: its front points to the image right
        left = View.synthetic("left", bytes(4), bytes(1), 1, 1, s, 100.0, cx)
        self.assertLess(left.project(0, 0, -0.3)[0], cx)


class ViewQueryTests(unittest.TestCase):
    def test_mask_and_color_access(self):
        v = synthetic_view()
        self.assertTrue(v.mask_at(20.0, 10.0))
        self.assertTrue(v.mask_at(43.99, 39.99))
        self.assertFalse(v.mask_at(44.0, 20.0))
        self.assertFalse(v.mask_at(-1, 5))
        self.assertFalse(v.mask_at(1000, 5))
        self.assertEqual(v.color_at(25.5, 20.5), (200, 40, 40))
        self.assertEqual(v.color_at(40.5, 20.5), (40, 40, 200))
        self.assertEqual(v.mask_box(), (20, 10, 44, 40))

    def test_area_weighted_fractions_are_exact(self):
        v = synthetic_view()
        self.assertAlmostEqual(v.mask_fraction(22, 12, 30, 20), 1.0)
        self.assertAlmostEqual(v.mask_fraction(0, 0, 10, 10), 0.0)
        self.assertAlmostEqual(v.mask_fraction(10, 10, 30, 20), 0.5)  # half of it is left of x=20
        self.assertAlmostEqual(v.mask_fraction(19.5, 10, 20.5, 11), 0.5)  # a half pixel column
        self.assertAlmostEqual(v.mask_fraction(19.5, 9.5, 20.5, 10.5), 0.25)
        # model space: s = 16, cx = 32, gy = 40 -> the rectangle spans x 20..44 -> u (front) = (cx - px)/s = -0.75 .. 0.75, y 0..1.875
        self.assertAlmostEqual(v.mask_cells(-0.5, 0.0, 0.5, 1.0), 1.0)
        self.assertAlmostEqual(v.mask_cells(-1.5, 0.0, -0.75, 1.0), 0.0)
        self.assertAlmostEqual(v.mask_cells(-1.5, 0.0, 0.0, 1.0), 0.5)
        self.assertAlmostEqual(v.mask_cells(0.0, 1.0, 1.5, 3.0), 0.5 * 0.875 / 2.0, 6)

    def test_average_colour_is_area_weighted_over_foreground_only(self):
        v = synthetic_view()
        self.assertEqual(v.average(22, 12, 30, 20), (200, 40, 40))
        self.assertEqual(v.average(34, 12, 42, 20), (40, 40, 200))
        mixed = v.average(28, 12, 36, 20)  # half red (x<32), half blue
        self.assertEqual(mixed, (120, 40, 120))
        self.assertEqual(v.average(10, 12, 15, 20), None)  # all background
        # foreground only: the rect straddles the mask edge, background (grey) must not leak in
        self.assertEqual(v.average(16, 12, 24, 20), (200, 40, 40))
        grey = v.average(16, 12, 24, 20, fg_only=False)
        self.assertNotEqual(grey, (200, 40, 40))
        self.assertEqual(v.average(21.0, 15.0, 21.0, 15.0), (200, 40, 40))  # degenerate rect = the pixel

    def test_model_extent(self):
        v = synthetic_view()
        e = v.model_extent()
        self.assertAlmostEqual(e["u_min"], -0.75)
        self.assertAlmostEqual(e["u_max"], 0.75)
        self.assertAlmostEqual(e["y_min"], 0.0)
        self.assertAlmostEqual(e["y_max"], 1.875)


# ------------------------------------------------------------------ masks, loading, validation
def write_scene(path, w=120, h=90, fig=(40, 20, 80, 75), seed=5, outline=True):
    """Grey brick-like background with noise, a blue-grey figure (optionally with a black outline)."""
    g = _lcg(seed)
    c = Canvas(w, h)
    for y in range(h):
        for x in range(w):
            base = 105 + (6 if (x // 20 + y // 10) % 2 else 0) - (25 if y % 10 == 0 else 0)
            n = next(g) % 5
            c.set(x, y, (base + n, base + n, base + n + 1, 255))
    x0, y0, x1, y1 = fig
    for y in range(y0, y1):
        for x in range(x0, x1):
            edge = outline and (x in (x0, x1 - 1) or y in (y0, y1 - 1))
            c.set(x, y, (0, 0, 0, 255) if edge else (110 + (x % 7), 140 + (y % 5), 190, 255))
    write_png(path, c)
    return c


def view_doc(img, **over):
    v = {"image": img, "ground_y": 75, "center_x": 60, "px_per_block": 30.0}
    v.update(over)
    return {"views": {"front": v}}


class LoadViewsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        write_scene(self.dir / "scene.png")

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, **over):
        return reference.load_views(view_doc("scene.png", **over), self.dir)["front"]

    def code(self, **over):
        with self.assertRaises(BossForgeError) as cm:
            self.load(**over)
        return cm.exception.err.code

    def test_auto_mask_finds_the_figure_on_a_textured_wall(self):
        v = self.load(mask={"mode": "auto", "erode": 0})
        self.assertEqual(v.mask_box(), (40, 20, 80, 75))
        self.assertEqual(sum(1 for b in v.mask if b), 40 * 55)

    def test_default_erode_is_one_pixel(self):
        v = self.load()
        self.assertEqual(v.mask_box(), (41, 21, 79, 74))
        v2 = self.load(mask={"erode": 3})
        self.assertEqual(v2.mask_box(), (43, 23, 77, 72))

    def test_outline_peels_the_dark_edge(self):
        v = self.load(mask={"erode": 0, "outline": 1})
        self.assertEqual(v.mask_box(), (41, 21, 79, 74))

    def test_auto_handles_flat_gradient_and_chroma_backgrounds(self):
        for kind in ("white", "gradient", "green"):
            g = _lcg(3)
            c = Canvas(120, 90)
            for y in range(90):
                for x in range(120):
                    n = next(g) % 4
                    t = y / 89.0
                    col = {"white": (250 - n, 250 - n, 252), "gradient": (int(40 + 50 * t) + n, int(60 + 50 * t) + n, int(90 + 50 * t) + n),
                           "green": (20 + n, 200 - n, 30)}[kind]
                    c.set(x, y, col + (255,))
            for y in range(20, 75):
                for x in range(40, 80):
                    c.set(x, y, (180 + (x % 5), 90 + (y % 4), 70, 255))
            write_png(self.dir / (kind + ".png"), c)
            v = reference.load_views(view_doc(kind + ".png", mask={"erode": 0}), self.dir)["front"]
            self.assertEqual(v.mask_box(), (40, 20, 80, 75), kind)
            self.assertEqual(sum(1 for b in v.mask if b), 40 * 55, kind)

    def test_crop_restricts_and_other_figures_can_be_picked_with_keep(self):
        write_scene(self.dir / "two.png", fig=(40, 20, 80, 75))
        c = read_png(self.dir / "two.png")
        for y in range(20, 75):
            for x in range(90, 110):
                c.set(x, y, (200, 60, 60, 255))
        write_png(self.dir / "two.png", c)
        both = reference.load_views(view_doc("two.png", mask={"erode": 0}), self.dir)["front"]
        self.assertEqual(both.mask_box(), (40, 20, 110, 75))
        only = reference.load_views(view_doc("two.png", mask={"erode": 0, "keep": [[60, 40]]}), self.dir)["front"]
        self.assertEqual(only.mask_box(), (40, 20, 80, 75))
        cropped = reference.load_views(view_doc("two.png", crop=[0, 0, 85, 90], mask={"erode": 0}), self.dir)["front"]
        self.assertEqual(cropped.mask_box(), (40, 20, 80, 75))
        with self.assertRaises(BossForgeError) as cm:
            reference.load_views(view_doc("two.png", mask={"keep": [[5, 5]]}), self.dir)
        self.assertEqual(cm.exception.err.code, "reference_bad_mask")

    def test_polygon_mode_include_and_exclude(self):
        tri = [[[10, 10], [50, 10], [10, 50]]]
        v = self.load(mask={"mode": "polygon", "include": tri})
        n = sum(1 for b in v.mask if b)
        self.assertAlmostEqual(n, 40 * 40 / 2.0, delta=45)
        self.assertTrue(v.mask_at(15, 15))
        self.assertFalse(v.mask_at(45, 45))
        sq = [[[40, 20], [80, 20], [80, 75], [40, 75]]]
        v = self.load(mask={"mode": "polygon", "include": sq, "exclude": [[[50, 30], [60, 30], [60, 40], [50, 40]]]})
        self.assertEqual(sum(1 for b in v.mask if b), 40 * 55 - 100)
        # include restricts an automatic mask: only the left half of the figure survives
        v = self.load(mask={"erode": 0, "include": [[[0, 0], [60, 0], [60, 90], [0, 90]]]})
        self.assertEqual(v.mask_box(), (40, 20, 60, 75))

    def test_color_mode(self):
        v = self.load(mask={"mode": "color", "bg": ["#6a6a6a", "#505050"], "tolerance": 30, "erode": 0})
        self.assertEqual(v.mask_box(), (40, 20, 80, 75))

    def test_image_mode(self):
        m = Canvas(120, 90)
        m.fill((0, 0, 0, 255))
        for y in range(30, 60):
            for x in range(50, 70):
                m.set(x, y, (255, 255, 255, 255))
        write_png(self.dir / "mask.png", m)
        v = self.load(mask={"mode": "image", "image": "mask.png", "erode": 0})
        self.assertEqual(v.mask_box(), (50, 30, 70, 60))
        m2 = Canvas(100, 90)
        write_png(self.dir / "small.png", m2)
        self.assertEqual(self.code(mask={"mode": "image", "image": "small.png"}), "reference_bad_mask")

    def test_alpha_channel_is_used_for_transparent_images(self):
        c = Canvas(50, 40)
        for y in range(10, 30):
            for x in range(10, 40):
                c.set(x, y, (10, 200, 10, 255))
        write_png(self.dir / "cut.png", c)
        v = reference.load_views(view_doc("cut.png", mask={"erode": 0}), self.dir)["front"]
        self.assertEqual(v.mask_box(), (10, 10, 40, 30))

    def test_calibration_forms(self):
        doc = view_doc("scene.png")
        del doc["views"]["front"]["px_per_block"]
        doc["views"]["front"].update(top_y=30.0, top_model_y=1.5)
        v = reference.load_views(doc, self.dir)["front"]
        self.assertAlmostEqual(v.px_per_block, (75.0 - 30.0) / 1.5)

    def test_error_codes(self):
        self.assertEqual(self.code(crop=[10, 10, 5, 50]), "reference_bad_crop")
        self.assertEqual(self.code(crop=[0, 0, 500, 50]), "reference_bad_crop")
        self.assertEqual(self.code(px_per_block=1.0), "reference_bad_calibration")
        self.assertEqual(self.code(ground_y="low"), "reference_bad_calibration")
        self.assertEqual(self.code(mask={"mode": "magic"}), "reference_bad_mask")
        self.assertEqual(self.code(mask={"mode": "color"}), "reference_bad_mask")
        self.assertEqual(self.code(mask={"include": [[[1, 1], [2, 2]]]}), "reference_bad_mask")
        self.assertEqual(self.code(mask={"erode": 99}), "reference_bad_mask")
        self.assertEqual(self.code(mask={"mode": "polygon", "include": [[[0, 0], [0.2, 0], [0.2, 0.2]]]}), "reference_bad_mask")  # empty: no pixel centre inside
        self.assertEqual(self.code(image="missing.png"), "reference_bad_image")
        with self.assertRaises(BossForgeError) as cm:
            reference.load_views({"views": {"top": {"image": "scene.png", "ground_y": 1, "center_x": 1, "px_per_block": 10}}}, self.dir)
        self.assertEqual(cm.exception.err.code, "reference_unknown_view")
        (self.dir / "fake.png").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        with self.assertRaises(BossForgeError) as cm:
            reference.load_views(view_doc("fake.png"), self.dir)
        self.assertEqual(cm.exception.err.code, "reference_bad_image")
        self.assertIn("magick", cm.exception.err.message)

    def test_views_for_uses_spec_dir_and_caches(self):
        n = {"reference": view_doc("scene.png", mask={"erode": 0}), "_spec_dir": str(self.dir)}
        a = reference.views_for(n)
        self.assertIs(reference.views_for(n), a)
        self.assertEqual(reference.views_for({}), {})
        v = synthetic_view()
        self.assertEqual(reference.views_for({"_views": {"front": v}}), {"front": v})


class SpecIntegrationTests(unittest.TestCase):
    def base(self):
        return json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))

    def codes(self, s):
        return [e.code for e in spec.validate(s)]

    def test_valid_reference_block_has_no_errors_and_missing_file_is_only_a_warning(self):
        s = self.base()
        s["reference"] = view_doc("nowhere.png", mask={"mode": "auto"})
        n, errs, warns = spec.check(s)
        self.assertEqual([e for e in errs if e.path.startswith("/reference")], [])
        self.assertEqual(n["reference"]["views"]["front"]["px_per_block"], 30.0)
        s["_spec_dir"] = "/nonexistent-dir"
        _n, errs, warns = spec.check(s)
        self.assertIn("reference_image_missing", [w.code for w in warns])

    def test_views_block_errors_are_reported(self):
        bad = {"views": {"front": {"image": "a.png", "crop": [5, 5, 1, 1], "ground_y": 10, "center_x": 5},
                         "top": {"image": "b.png"}, "back": {"image": "c.png", "ground_y": 1, "center_x": 1, "px_per_block": 2.0,
                                                            "mask": {"mode": "x", "erode": -1}}}}
        s = self.base()
        s["reference"] = bad
        codes = self.codes(s)
        for c in ("reference_bad_crop", "reference_bad_calibration", "reference_unknown_view", "reference_bad_mask"):
            self.assertIn(c, codes)
        s["reference"] = {"views": {}}
        self.assertIn("reference_bad_image", self.codes(s))
        s["reference"] = "nope"
        self.assertIn("reference_bad_image", self.codes(s))

    def test_validate_reads_png_headers_for_format_and_crop(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_scene(Path(tmp) / "scene.png")
            (Path(tmp) / "fake.png").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
            s = self.base()
            s["_spec_dir"] = tmp
            s["reference"] = {"views": {"front": dict(view_doc("scene.png")["views"]["front"], crop=[0, 0, 500, 50]),
                                        "back": dict(view_doc("fake.png")["views"]["front"])}}
            errs = {(e.path, e.code) for e in spec.validate(s)}
            self.assertIn(("/reference/views/front/crop", "reference_bad_crop"), errs)
            self.assertIn(("/reference/views/back/image", "reference_bad_image"), errs)
            s["reference"]["views"]["front"]["crop"] = [0, 0, 100, 50]
            s["reference"]["views"]["back"] = {"image": "scene.png", "ground_y": 75, "center_x": 60, "px_per_block": 30.0,
                                               "mask": {"mode": "image", "image": "scene.png"}}
            self.assertEqual([e for e in spec.validate(s) if e.path.startswith("/reference")], [])

    def test_spec_without_reference_is_unchanged(self):
        s = self.base()
        n, errs, warns = spec.check(s)
        self.assertEqual(errs, [])
        self.assertNotIn("_spec_dir", n)

    def test_spec_load_sets_spec_dir_only_with_a_reference_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            s = self.base()
            p = Path(tmp) / "boss.json"
            p.write_text(json.dumps(s), encoding="utf-8")
            self.assertNotIn("_spec_dir", spec.load(p))
            s["reference"] = view_doc("x.png")
            p.write_text(json.dumps(s), encoding="utf-8")
            self.assertEqual(spec.load(p)["_spec_dir"], str(Path(tmp).resolve()))


class MorphologyTests(unittest.TestCase):
    def test_erode_dilate_components_and_polygons(self):
        w, h = 20, 12
        m = bytearray(w * h)
        for y in range(3, 9):
            m[y * w + 4:y * w + 12] = b"\x01" * 8
        m[0:3] = b"\x01\x01\x01"
        self.assertEqual(refmask.count(refmask.erode(m, w, h, 1)), 6 * 4)
        self.assertEqual(refmask.bbox(refmask.dilate(m, w, h, 2), w, h), (0, 0, 14, 11))
        comps = refmask.Components(m, w, h)
        self.assertEqual(sorted(comps.area), [3, 48])
        self.assertEqual(refmask.count(refmask.keep_components(m, w, h, 0.5)), 48)
        sq = refmask.fill_polygons([[[2, 2], [6, 2], [6, 5], [2, 5]]], w, h)
        self.assertEqual(refmask.count(sq), 4 * 3)
        ones = refmask.invert(bytearray(w * h))
        self.assertEqual(refmask.peel(m, ones, w, h, 1), refmask.erode(m, w, h, 1))
        self.assertEqual(refmask.peel(m, bytearray(w * h), w, h, 3), m)


if __name__ == "__main__":
    unittest.main()
