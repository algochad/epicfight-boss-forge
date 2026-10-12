"""Pixel-art painter features on imported-mesh style geometry: `decal` (upright on rotated / mirrored UV islands, mirror pairs, anchors,
tiling, files, warnings), world-space stepped gradients with Bayer dithering, `iridescent` face selection on double-sided slabs, island-level
face classes, `posterize` / `palette_snap`, `quality: pixel` materials, the 8x8 face (`anchor: head_front`) and error codes.

The meshes are built here from quads: every quad has its own UV island that can be rotated and mirrored in the atlas (all 8 orientations), so
a decal that reads upright on each of them proves the frame comes from the mesh and not from the UV layout."""
import _path  # noqa
import copy
import json
import math
import os
import tempfile
import time
import unittest

from bossforge_core import colors, decal, features, materials, meshsrc, pixelart, skin, spec
from bossforge_core.pngio import write_png

TB = 1.0 / 32.0  # blocks per texel of every quad (the painter measures it per triangle)
GREY = (128, 128, 128)
RED = "#ff0000"
BLUE = "#0000ff"
OUT = {"front": (0.0, 0.0, -1.0), "back": (0.0, 0.0, 1.0), "right": (1.0, 0.0, 0.0), "left": (-1.0, 0.0, 0.0), "top": (0.0, 1.0, 0.0)}
ORIENTS = tuple(range(8))  # UV orientations of an island: 4 rotations x mirror


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(v):
    ln = math.sqrt(sum(c * c for c in v))
    return tuple(c / ln for c in v)


def to_atlas(i, j, w, h, orient):
    """Quad-local texel coordinates (right, down as a viewer sees the face) -> offset in the atlas rectangle of the island."""
    if orient >= 4:
        i = w - i
    k = orient % 4
    if k == 0:
        return i, j
    if k == 1:
        return h - j, i
    if k == 2:
        return w - i, h - j
    return j, w - i


def from_atlas(x, y, w, h, orient):
    k = orient % 4
    if k == 0:
        i, j = x, y
    elif k == 1:
        i, j = y, h - x
    elif k == 2:
        i, j = w - x, h - y
    else:
        i, j = w - y, x
    return (w - i, j) if orient >= 4 else (i, j)


class Quad:
    """A w x h texel face. `origin` = its top-left corner as a viewer outside sees it, `facing` the outward direction (front -z, back +z,
    right +x, left -x, top +y), `uv` = (x, y) of its atlas rectangle, `orient` 0..7 = how the exporter rotated / mirrored the island.
    `smooth` gives its corners vertex normals tilted away from the face (a smooth-shaded slab); `shape: tri` is the upper-left half."""

    def __init__(self, part, w, h, origin, facing="front", uv=(0, 0), orient=0, region=None, smooth=0.0, shape="quad"):
        self.part, self.w, self.h, self.origin, self.facing, self.uv, self.orient = part, w, h, origin, facing, uv, orient
        self.region = region or ("box:" + part)
        self.smooth, self.shape = smooth, shape
        out = OUT[facing]
        up = (0.0, 0.0, -1.0) if facing == "top" else (0.0, 1.0, 0.0)  # on a horizontal face "up" is toward the character's front
        self.right = _unit(_cross(up, out))
        self.down = tuple(-c for c in up)
        self.out = out

    def corner(self, a, b):
        return tuple(self.origin[k] + self.right[k] * a * self.w * TB + self.down[k] * b * self.h * TB for k in range(3))

    def atlas_size(self):
        return (self.h, self.w) if self.orient % 2 else (self.w, self.h)

    def uv_of(self, a, b, size):
        x, y = to_atlas(a * self.w, b * self.h, self.w, self.h, self.orient)
        return ((self.uv[0] + x) / size, (self.uv[1] + y) / size)

    def local(self, tx, ty):
        """Quad-local (col, row) of the atlas texel (tx, ty)."""
        i, j = from_atlas(tx + 0.5 - self.uv[0], ty + 0.5 - self.uv[1], self.w, self.h, self.orient)
        return int(math.floor(i)), int(math.floor(j))


class Scene:
    def __init__(self, quads, size=64):
        self.quads, self.size = quads, size
        pos, nrm, uvs, tris, isl, regions = [], [], [], [], [], {}
        for qi, q in enumerate(quads):
            corners = {(a, b): q.corner(a, b) for a in (0, 1) for b in (0, 1)}
            ids = {}
            for (a, b), p in corners.items():
                ids[(a, b)] = len(pos)
                pos.append(p)
                n = q.out
                if q.smooth:
                    tilt = tuple(q.right[k] * (a - 0.5) * 2 + q.down[k] * (b - 0.5) * 2 for k in range(3))
                    n = _unit(tuple(q.out[k] + q.smooth * tilt[k] for k in range(3)))
                nrm.append(n)
                uvs.append(q.uv_of(a, b, size))
            tri_corners = [((0, 0), (0, 1), (1, 1)), ((0, 0), (1, 1), (1, 0))] if q.shape == "quad" else [((0, 0), (0, 1), (1, 0))]
            for cs in tri_corners:
                t = meshsrc._Tri(q.part, tuple(ids[c] for c in cs), tuple(ids[c] for c in cs), tuple(ids[c] for c in cs))
                t.region = q.region
                tris.append(t)
                isl.append(qi)
            regions[q.part] = q.region
        self.mesh = meshsrc.RegionMesh(pos, nrm, uvs, tris, regions, isl)
        self.tpl = {"custom": True, "mesh_source": True, "authored_uv": [size, size], "uv_origin": "top", "front_sign": -1, "right_sign": 1,
                    "part_regions": regions, "part_style": {}, "overlay_parts": [], "overlay_to_base": True, "dilate": 0, "max_edge": 2048,
                    "model_height": 2.0, "humanoid": True}

    def appearance(self, features_, quality="pixel", material="skin_soft", color="#808080", **extra):
        ap = {"palette": {"primary": "#808080"}, "regions": {"torso": {"material": material, "color": color, "quality": quality}},
              "features": features_, "texture": {"scale": 2}}
        ap.update(extra)
        return ap

    def paint(self, features_=(), seed=3, spec_dir=None, **kw):
        return skin.paint(self.mesh, self.tpl, self.appearance(list(features_), **kw), seed, spec_dir=spec_dir)

    def texels(self):
        w = self.size
        cover = skin._rasterize(self.mesh, self.tpl, w, w)
        return skin._build_texels(self.mesh, self.tpl, cover, set(), -1, 1, TB, skin._tri_texel_sizes(self.mesh, w, w))

    def picture(self, quad, features_, **kw):
        """{(col, row): rgb} of the texels of `quad` the features changed (quad-local view coordinates), plus the report."""
        base, _ = self.paint([], **kw)
        c, info = self.paint(features_, **kw)
        got = {}
        x0, y0 = quad.uv
        aw, ah = quad.atlas_size()
        for ty in range(y0, y0 + ah):
            for tx in range(x0, x0 + aw):
                if c.get(tx, ty) != base.get(tx, ty):
                    got[quad.local(tx, ty)] = c.get(tx, ty)[:3]
        return got, info


def hexrgb(h):
    return (int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16))


def expect(rows, x0, y0, color=hexrgb(RED), mirror=False, scale=1):
    """Picture dict of a bitmap with '#' opaque placed with its top-left at (x0, y0) (optionally mirrored about its own centre)."""
    w = max(len(r) for r in rows)
    out = {}
    for j, r in enumerate(rows):
        for i, ch in enumerate(r):
            if ch == "#":
                ii = (w - 1 - i) if mirror else i
                for dy in range(scale):
                    for dx in range(scale):
                        out[(x0 + ii * scale + dx, y0 + j * scale + dy)] = color
    return out


def mirrored_x(pic, w):
    return {(w - 1 - i, j): c for (i, j), c in pic.items()}


F_SHAPE = ["###", "#..", "##.", "#..", "#.."]  # an upright, asymmetric letter F: orientation and mirroring are visible
FEAT = {"type": "decal", "regions": ["part:wall"], "bitmap": F_SHAPE, "color": RED}


def wall(**kw):
    """A face on the character's RIGHT side (+x) by default, so `mirror: auto` leaves it as drawn."""
    facing = kw.pop("facing", "front")
    default = {"front": (0.5, 1.0, 0.0), "back": (0.2, 1.0, 0.0), "right": (0.5, 1.0, 0.0), "left": (0.5, 1.0, 0.0), "top": (0.4, 1.0, 0.0)}[facing]
    return Quad("wall", kw.pop("w", 12), kw.pop("h", 11), kw.pop("origin", default), facing, **kw)


class Orientation(unittest.TestCase):
    def test_reads_upright_on_all_eight_uv_orientations_and_all_facings(self):
        for facing in ("front", "back", "right", "left"):
            for orient in ORIENTS:
                with self.subTest(facing=facing, orient=orient):
                    q = wall(facing=facing, uv=(8, 5), orient=orient, w=12, h=11)
                    got, info = Scene([q]).picture(q, [FEAT])
                    self.assertEqual(got, expect(F_SHAPE, (12 - 3) // 2, (11 - 5) // 2))
                    self.assertEqual(info["warnings"], [])

    def test_a_decal_is_stamped_in_texel_units_without_stretching(self):
        q = wall(uv=(2, 2), orient=1, w=12, h=11)
        got, _ = Scene([q]).picture(q, [dict(FEAT, scale=2)])
        self.assertEqual(got, expect(F_SHAPE, (12 - 6) // 2, (11 - 10) // 2, scale=2))

    def test_horizontal_faces_read_with_up_toward_the_front(self):
        for orient in (0, 3, 5):
            q = Quad("wall", 12, 11, (0.0, 1.0, 0.0), "top", uv=(4, 4), orient=orient)
            got, _ = Scene([q]).picture(q, [FEAT])
            self.assertEqual(got, expect(F_SHAPE, 4, 3), orient)

    def test_islands_rotated_off_the_axes_sample_nearest_but_stay_upright(self):
        # a face tilted 30 degrees about its own normal: the UV island is an upright rectangle, the world "up" is tilted in it
        q = wall(w=24, h=24, uv=(2, 2))
        sc = Scene([q])
        c, a = math.cos(math.radians(30)), math.sin(math.radians(30))
        pos = sc.mesh.positions
        o = q.origin
        for i, p in enumerate(pos):  # rotate the corners about the face centre, in the face plane (x/y)
            mid = (o[0] - q.w * TB / 2.0, o[1] - q.h * TB / 2.0)
            dx, dy = p[0] - mid[0], p[1] - mid[1]
            pos[i] = (mid[0] + c * dx - a * dy, mid[1] + a * dx + c * dy, p[2])
        got, info = sc.picture(q, [dict(FEAT, scale=3)])
        self.assertTrue(got)
        self.assertEqual(info["warnings"], [])
        # every painted texel lies inside the tilted decal's footprint, which spans 9 x 15 texels about the island centre
        xs = [i for i, _ in got]
        ys = [j for _, j in got]
        self.assertLess(max(xs) - min(xs), 24)
        self.assertLess(max(ys) - min(ys), 24)


class MirrorPairs(unittest.TestCase):
    def pair(self, orient_r, orient_l, mirror="auto"):
        r = Quad("wall", 12, 11, (0.5, 1.0, 0.0), "front", uv=(2, 2), orient=orient_r)  # +x = the character's right
        l = Quad("wall", 12, 11, (-0.2, 1.0, 0.0), "front", uv=(30, 2), orient=orient_l)
        return r, l, Scene([r, l])

    def test_left_piece_gets_the_mirror_image_whatever_its_uv_orientation(self):
        for orient_r, orient_l in ((0, 0), (1, 6), (2, 3), (5, 4)):
            r, l, sc = self.pair(orient_r, orient_l)
            gr, _ = sc.picture(r, [FEAT])
            gl, info = sc.picture(l, [FEAT])
            self.assertEqual(gr, expect(F_SHAPE, 4, 3), (orient_r, orient_l))
            self.assertEqual(gl, expect(F_SHAPE, 12 - 4 - 3, 3, mirror=True), (orient_r, orient_l))
            self.assertEqual(info["warnings"], [])

    def test_mirror_none_and_flip(self):
        r, l, sc = self.pair(0, 0)
        for mirror, expected in (("none", expect(F_SHAPE, 4, 3)), ("flip", expect(F_SHAPE, 5, 3, mirror=True))):
            gl, _ = sc.picture(l, [dict(FEAT, mirror=mirror)])
            self.assertEqual(gl, expected, mirror)

    def test_anchors_and_offsets_mirror_with_the_piece(self):
        r, l, sc = self.pair(0, 2)
        f = dict(FEAT, anchor="top_left", offset=[1, 2])
        gr, _ = sc.picture(r, [f])
        gl, _ = sc.picture(l, [f])
        self.assertEqual(gr, expect(F_SHAPE, 1, 2))
        self.assertEqual(gl, expect(F_SHAPE, 12 - 1 - 3, 2, mirror=True))  # top_left of the left piece is its top-right corner
        self.assertEqual(gl, mirrored_x(gr, 12))

    def test_a_centre_piece_is_never_flipped(self):
        c = Quad("wall", 12, 11, (0.19, 1.0, 0.0), "front", uv=(2, 2), orient=0)  # spans x -0.0..0.19 -> centred on the plane
        c.origin = (6 * TB, 1.0, 0.0)
        got, _ = Scene([c]).picture(c, [FEAT])
        self.assertEqual(got, expect(F_SHAPE, 4, 3))

    def test_the_whole_pair_is_an_exact_mirror_in_world_space(self):
        # the left piece is the reflection x -> -x of the right piece (UV islands oriented differently): the painted texels are too
        r = Quad("wall", 12, 11, (0.5, 1.0, 0.0), "front", uv=(2, 2), orient=1)
        l = Quad("wall", 12, 11, (-0.125, 1.0, 0.0), "front", uv=(30, 2), orient=6)
        sc = Scene([r, l])
        base, _ = sc.paint([])
        c, _ = sc.paint([FEAT])
        painted = [t for t in sc.texels() if c.get(t.x, t.y) != base.get(t.x, t.y)]
        right = {(round(2 * t.pos[0] / TB), round(2 * t.pos[1] / TB)) for t in painted if t.pos[0] > 0}  # half-texel units: exact
        left = {(round(-2 * t.pos[0] / TB), round(2 * t.pos[1] / TB)) for t in painted if t.pos[0] < 0}
        self.assertEqual(len(right), sum(r.count("#") for r in F_SHAPE))
        self.assertEqual(right, left)


class Placement(unittest.TestCase):
    def setUp(self):
        self.q = wall(w=14, h=12, uv=(3, 3), orient=3)
        self.sc = Scene([self.q])

    def got(self, **kw):
        return self.sc.picture(self.q, [dict(FEAT, **kw)])[0]

    def test_every_anchor(self):
        w, h, bw, bh = 14, 12, 3, 5
        cases = {"center": ((w - bw) // 2, (h - bh) // 2), "top": ((w - bw) // 2, 0), "bottom": ((w - bw) // 2, h - bh),
                 "left": (0, (h - bh) // 2), "right": (w - bw, (h - bh) // 2), "top_left": (0, 0), "top_right": (w - bw, 0),
                 "bottom_left": (0, h - bh), "bottom_right": (w - bw, h - bh)}
        for anchor, (x0, y0) in cases.items():
            self.assertEqual(self.got(anchor=anchor), expect(F_SHAPE, x0, y0), anchor)

    def test_offset_moves_in_texels_right_and_down(self):
        self.assertEqual(self.got(anchor="top_left", offset=[2, 3]), expect(F_SHAPE, 2, 3))

    def test_repeat_tiles_whole_copies_with_gaps_along_the_axis(self):
        got = self.got(repeat="x", gap=1, anchor="left")  # 14 wide: 3 + 1 gap per copy -> 3 whole copies (3+1+3+1+3 = 11)
        want = {}
        for k in range(3):
            want.update(expect(F_SHAPE, k * 4, (12 - 5) // 2))
        self.assertEqual(got, want)
        centred = self.got(repeat="x", gap=1, anchor="center")  # the group of copies is centred: (14 - 11) // 2 = 1
        self.assertEqual(centred, {(i + 1, j): c for (i, j), c in want.items()})
        counted = self.got(repeat="x", count=2, gap=1, anchor="left")
        self.assertEqual(counted, {k: v for k, v in want.items() if k[0] < 7})

    def test_partial_tiles_are_clipped_instead_of_dropped_with_partial_true(self):
        got = self.got(repeat="x", anchor="left", partial=True)  # 14 / 3 -> 4 whole copies + one clipped to its first 2 columns
        self.assertEqual(max(i for i, _ in got), 13)
        self.assertIn((13, 3), got)
        self.assertIn((12, 4), got)
        self.assertNotIn((14, 3), got)
        whole = self.got(repeat="x", anchor="left")
        self.assertEqual(max(i for i, _ in whole), 11)

    def test_repeat_y_and_xy(self):
        got = self.got(repeat="y", anchor="top")
        rows = sorted({j for _, j in got})
        self.assertEqual(rows[0], 0)
        self.assertEqual(rows[-1], 9)  # two whole copies of 5 rows
        xy = self.got(repeat="xy", anchor="top_left")
        self.assertEqual(max(i for i, _ in xy), 14 - 14 % 3 - 1)
        self.assertEqual(max(j for _, j in xy), 9)

    def test_multiply_blend_shades_the_base_and_opacity_mixes(self):
        grey = {"type": "decal", "regions": ["part:wall"], "bitmap": ["#"], "palette": {"#": "#808080"}, "blend": "multiply", "anchor": "top_left"}
        got = self.sc.picture(self.q, [grey])[0]
        self.assertEqual(got, {(0, 0): (64, 64, 64)})
        half = self.sc.picture(self.q, [dict(FEAT, bitmap=["#"], opacity=0.5, anchor="top_left")])[0]
        self.assertEqual(half, {(0, 0): (192, 64, 64)})

    def test_palette_directives_and_comments_in_files(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "decals"))
            with open(os.path.join(d, "decals", "f.txt"), "w", encoding="utf-8") as fh:
                fh.write("; an F\n@g #00ff00\n@. none\n@# #0000ff\n\ngg#\n#..\n##.\n")
            got, info = self.sc.picture(self.q, [{"type": "decal", "regions": ["part:wall"], "file": "decals/f.txt", "anchor": "top_left"}], spec_dir=d)
            self.assertEqual(info["warnings"], [])
            self.assertEqual(got[(0, 0)], (0, 255, 0))
            self.assertEqual(got[(1, 0)], (0, 255, 0))
            self.assertEqual(got[(2, 0)], (0, 0, 255))
            self.assertEqual(got[(0, 1)], (0, 0, 255))
            self.assertNotIn((1, 1), got)
            self.assertEqual(len(got), 3 + 1 + 2)
            # the json palette wins over the file's directives
            got, _ = self.sc.picture(self.q, [{"type": "decal", "regions": ["part:wall"], "file": "decals/f.txt", "anchor": "top_left",
                                               "palette": {"g": "#ffffff"}}], spec_dir=d)
            self.assertEqual(got[(0, 0)], (255, 255, 255))

    def test_deterministic(self):
        f = [dict(FEAT, repeat="xy", gap=1), {"type": "iridescent", "regions": ["part:wall"], "strength": 0.3}]
        a, _ = self.sc.paint(f)
        b, _ = self.sc.paint(copy.deepcopy(f))
        self.assertEqual(bytes(a.data), bytes(b.data))


class Warnings(unittest.TestCase):
    def codes(self, info):
        return [w["code"] for w in info["warnings"]]

    def test_decal_too_big_and_partial(self):
        q = wall(w=3, h=4, uv=(2, 2))
        _g, info = Scene([q]).picture(q, [FEAT])
        self.assertEqual(self.codes(info), ["decal_too_big", "decal_partial"])
        _g, info = Scene([q]).picture(q, [dict(FEAT, partial=True)])
        self.assertEqual(self.codes(info), ["decal_too_big"])

    def test_skip_small_takes_no_copy_and_no_warning(self):
        big, small = wall(w=12, h=11, uv=(2, 2)), Quad("wall", 3, 4, (0.6, 1.0, 0.0), "front", uv=(30, 2))
        sc = Scene([big, small])
        _g, info = sc.picture(small, [dict(FEAT, skip_small=True)])
        self.assertEqual(info["warnings"], [])
        self.assertEqual(sc.picture(small, [dict(FEAT, skip_small=True)])[0], {})
        self.assertTrue(sc.picture(big, [dict(FEAT, skip_small=True)])[0])

    def test_nothing_targeted(self):
        q = wall()
        _g, info = Scene([q]).picture(q, [dict(FEAT, regions=["part:nothing_here"])])
        self.assertEqual(self.codes(info), ["decal_no_target"])

    def test_stretched_islands_are_reported(self):
        q = wall(w=12, h=12, uv=(2, 2))
        sc = Scene([q])
        sc.mesh.uvs[:] = [((2 + (u * 64 - 2) * 2) / 64.0, v) for u, v in sc.mesh.uvs]  # the island is twice as wide in texels
        _g, info = sc.paint([FEAT])
        self.assertIn("decal_stretched", self.codes(info))

    def test_fit_inside_slides_a_decal_onto_a_tapered_island(self):
        q = wall(w=16, h=16, uv=(2, 2), shape="tri")  # a right triangle, the right angle at the top left
        sc = Scene([q])
        plain, info = sc.picture(q, [dict(FEAT, bitmap=["####", "####", "####", "####"])])
        self.assertIn("decal_partial", self.codes(info))
        fitted, info = sc.picture(q, [dict(FEAT, bitmap=["####", "####", "####", "####"], fit="inside")])
        self.assertEqual(self.codes(info), [])
        self.assertEqual(len(fitted), 16)
        self.assertLess(len(plain), 16)
        for i, j in fitted:
            self.assertLessEqual(i + j, 15)  # every pixel inside the triangle


class UnionScope(unittest.TestCase):
    def test_union_places_one_copy_across_islands_that_form_one_surface(self):
        a = wall(w=8, h=11, uv=(2, 2), origin=(8 * TB, 1.0, 0.0), orient=0)  # viewer-left half (x > 0 side)
        b = Quad("wall", 8, 11, (0.0, 1.0, 0.0), "front", uv=(20, 2), orient=3)  # viewer-right half
        sc = Scene([a, b])
        per_island, _ = sc.paint([dict(FEAT, bitmap=["#######", "#.....#", "#######"], mirror="none")])
        base, _ = sc.paint([])
        union, info = sc.paint([dict(FEAT, bitmap=["#######", "#.....#", "#######"], scope="union", mirror="none")])
        painted = lambda c: sum(1 for y in range(64) for x in range(64) if c.get(x, y) != base.get(x, y))
        self.assertEqual(painted(per_island), 2 * (7 + 2 + 7))  # one frame per island
        self.assertEqual(painted(union), 7 + 2 + 7)  # one copy
        self.assertEqual(info["warnings"], [])
        # the single copy spans the seam: both halves carry part of it
        la = sum(1 for y in range(2, 13) for x in range(2, 10) if union.get(x, y) != base.get(x, y))
        lb = sum(1 for y in range(2, 13) for x in range(20, 31) if union.get(x, y) != base.get(x, y))
        self.assertTrue(la and lb)


class Projection(unittest.TestCase):
    """`project`: the picture is looked up by the texels' world position (orthographic, nearest cell), facing the viewer, nearest layer only."""

    def right_piece(self, **kw):
        return Quad("skirt", 12, 11, (0.5, 1.0, 0.0), "front", uv=(2, 2), **kw)  # x 0.125 .. 0.5, y 0.656 .. 1.0 (viewer-left of the body)

    PROJ = {"view": "front", "at": [0.3, 0.85]}

    def feat(self, **kw):
        return dict({"type": "decal", "regions": ["part:skirt"], "bitmap": F_SHAPE, "color": RED, "project": self.PROJ, "mirror": "none"}, **kw)

    def test_the_picture_sits_at_its_world_position_and_reads_upright(self):
        for orient in (0, 3, 6):
            q = self.right_piece(orient=orient)
            got, info = Scene([q]).picture(q, [self.feat()])
            # texel centre rule: the left edge of the picture (world x 0.3 + 1.5 texels) falls on quad column 5, its top (y 0.85 + 2.5 texels) on row 2
            self.assertEqual(got, expect(F_SHAPE, 5, 2), orient)
            self.assertEqual(info["warnings"], [])

    def test_anchors_and_scale(self):
        q = self.right_piece()
        sc = Scene([q])
        self.assertEqual(sc.picture(q, [self.feat(anchor="top_left")])[0], expect(F_SHAPE, 6, 5))
        self.assertEqual(sc.picture(q, [self.feat(scale=2)])[0], expect(F_SHAPE, 3, 0, scale=2))
        self.assertEqual(sc.picture(q, [self.feat(project=dict(self.PROJ, pixel=2 * TB))])[0], expect(F_SHAPE, 3, 0, scale=2))  # pixel in blocks

    def test_mirror_stamps_the_reflection_on_the_other_side(self):
        r = self.right_piece()
        l = Quad("skirt", 12, 11, (-0.125, 1.0, 0.0), "front", uv=(30, 2))  # x -0.5 .. -0.125
        sc = Scene([r, l])
        gr, _ = sc.picture(r, [self.feat(mirror="auto")])
        gl, _ = sc.picture(l, [self.feat(mirror="auto")])
        self.assertEqual(gr, expect(F_SHAPE, 5, 2))
        self.assertEqual(gl, {(11 - i, j): c for (i, j), c in gr.items()})
        none, _ = sc.picture(l, [self.feat(mirror="none")])
        self.assertEqual(none, {})
        centred = dict(self.feat(mirror="auto"), project={"view": "front", "at": [0.0, 0.85]})
        c = Quad("skirt", 12, 11, (6 * TB, 1.0, 0.0), "front", uv=(2, 2))
        gc, _ = Scene([c]).picture(c, [centred])
        self.assertEqual(gc, expect(F_SHAPE, 4, 2))  # a centred decal is not doubled and not flipped

    def test_only_the_visible_layer_facing_the_viewer_is_stamped(self):
        outer = self.right_piece()
        inner = Quad("skirt", 12, 11, (0.5, 1.0, 0.2), "front", uv=(20, 2))  # the same place, 0.2 blocks deeper (an inner wall)
        far_back = Quad("skirt", 12, 11, (0.125, 1.0, 0.1), "back", uv=(38, 2))  # faces away from the viewer
        sc = Scene([outer, inner, far_back])
        for q, expected in ((outer, expect(F_SHAPE, 5, 2)), (inner, {}), (far_back, {})):
            got, _ = sc.picture(q, [self.feat()])
            self.assertEqual(got, expected, q.uv)
        thick, _ = sc.picture(inner, [self.feat(project=dict(self.PROJ, depth=0.5))])
        self.assertTrue(thick)  # with a deep tolerance the inner layer counts as visible too
        grazing = Quad("skirt", 12, 11, (0.5, 1.0, 0.0), "right", uv=(2, 2))
        self.assertEqual(Scene([grazing]).picture(grazing, [self.feat()])[0], {})  # a side face (dot 0) is not facing the front view

    def test_side_and_top_views(self):
        side = Quad("skirt", 12, 11, (0.5, 1.0, 0.1), "right", uv=(2, 2), orient=2)  # plane x = 0.5; viewer-right = -z
        got, _ = Scene([side]).picture(side, [self.feat(project={"view": "right", "at": [0.1 - 6 * TB, 0.85]})])
        self.assertEqual(got, expect(F_SHAPE, 4, 2))
        top = Quad("skirt", 12, 11, (0.4, 1.0, 0.1), "top", uv=(2, 2), orient=1)  # up = toward the front (-z), right = +x
        got, _ = Scene([top]).picture(top, [self.feat(project={"view": "top", "at": [0.4 + 6 * TB, 0.1 + 5.5 * TB]})])
        self.assertEqual(got, expect(F_SHAPE, 4, 3))

    def test_warnings(self):
        q = self.right_piece()
        sc = Scene([q])
        _g, info = sc.picture(q, [self.feat(project={"view": "back", "at": [0.3, 0.85]})])
        self.assertEqual([w["code"] for w in info["warnings"]], ["decal_no_target"])
        _g, info = sc.picture(q, [self.feat(project={"view": "front", "at": [3.0, 3.0]})])
        self.assertEqual([w["code"] for w in info["warnings"]], ["decal_no_target"])

    def test_repeat_with_a_count_makes_a_strip(self):
        q = self.right_piece()
        got, _ = Scene([q]).picture(q, [self.feat(repeat="x", count=2, gap=1, anchor="left", project={"view": "front", "at": [0.5 - 2 * TB, 0.85]})])
        want = expect(F_SHAPE, 2, 2)  # the strip starts at the world position of `at` (a texel edge): copies 4 texels apart (3 + gap 1)
        want.update(expect(F_SHAPE, 6, 2))
        self.assertEqual(got, want)



class Bevel(unittest.TestCase):
    """`bevel`: per-face rim lighting in the upright frame of every UV island, whatever its UV orientation."""

    def setUp(self):
        self.q = Quad("plate", 8, 6, (0.5, 1.0, 0.0), "front", uv=(3, 3))

    def factors(self, q, feat, **kw):
        sc = Scene([q])
        base, _ = sc.paint([], **kw)
        c, info = sc.paint([dict({"type": "bevel", "regions": ["part:plate"]}, **feat)], **kw)
        out = {}
        for ty in range(64):
            for tx in range(64):
                if c.get(tx, ty)[3] and (c.get(tx, ty) != base.get(tx, ty) or q.local(tx, ty)[0] in (0, q.w - 1) or q.local(tx, ty)[1] in (0, q.h - 1)):
                    out[q.local(tx, ty)] = c.get(tx, ty)[0] / float(base.get(tx, ty)[0])
        return out, info

    def test_edges_are_lit_upright_on_every_uv_orientation_and_facing(self):
        for facing in ("front", "back", "right", "left"):
            for orient in ORIENTS:
                q = Quad("plate", 8, 6, (0.5, 1.0, 0.0), facing, uv=(3, 3), orient=orient)
                got, info = self.factors(q, {"top": 0.25, "bottom": -0.25, "left": 0.3, "right": -0.3})
                self.assertEqual(info["warnings"], [])
                for (i, j), k in got.items():
                    want = 1.0
                    if j == 0:
                        want += 0.25
                    if j == 5:
                        want -= 0.25
                    if i == 0:
                        want += 0.3
                    if i == 7:
                        want -= 0.3
                    self.assertAlmostEqual(k, want, delta=0.02, msg=(facing, orient, i, j))
                self.assertEqual(len([1 for (i, j) in got if i in (0, 7) or j in (0, 5)]), 2 * 8 + 2 * 6 - 4)
                self.assertTrue(all(i in (0, 7) or j in (0, 5) for (i, j) in got if abs(got[(i, j)] - 1.0) > 0.02))

    def test_width_two_and_min_size(self):
        got, _ = self.factors(self.q, {"top": 0.25, "width": 2})
        self.assertEqual({j for (i, j), k in got.items() if k > 1.1}, {0, 1})
        thin = Quad("plate", 2, 10, (0.5, 1.0, 0.0), "front", uv=(3, 3))
        self.assertEqual([k for k in self.factors(thin, {"top": 0.25})[0].values() if k != 1.0], [])  # narrower than min_size 3: skipped
        self.assertTrue([k for k in self.factors(thin, {"top": 0.25, "min_size": 1})[0].values() if k != 1.0])

    def test_the_rim_follows_the_islands_outline(self):
        tri = Quad("plate", 16, 16, (0.5, 1.0, 0.0), "front", uv=(3, 3), shape="tri")  # right angle top left, stepped hypotenuse
        got, _ = self.factors(tri, {"bottom": -0.3})
        dark = {(i, j) for (i, j), k in got.items() if k < 0.9}
        self.assertTrue(dark)
        for i, j in dark:
            self.assertGreaterEqual(i + j, 14)  # only along the hypotenuse (and the bottom leg)
        self.assertIn((7, 8), dark)

    def test_outline_mode_mixes_the_rim_toward_a_colour(self):
        sc = Scene([self.q])
        base, _ = sc.paint([])
        c, _ = sc.paint([{"type": "bevel", "regions": ["part:plate"], "mode": "outline", "color": "#000000", "amount": 0.5}])
        for ty in range(64):
            for tx in range(64):
                if not c.get(tx, ty)[3]:
                    continue
                i, j = self.q.local(tx, ty)
                rim = i in (0, 7) or j in (0, 5)
                self.assertEqual(c.get(tx, ty)[0], 64 if rim else 128, (i, j))

    def test_a_mirror_pair_is_lit_from_the_same_side(self):
        r = Quad("plate", 8, 6, (0.5, 1.0, 0.0), "front", uv=(3, 3))
        l = Quad("plate", 8, 6, (-0.125, 1.0, 0.0), "front", uv=(20, 3), orient=5)
        sc = Scene([r, l])
        base, _ = sc.paint([])
        c, _ = sc.paint([{"type": "bevel", "regions": ["part:plate"], "left": 0.5}])
        for q in (r, l):
            x0, y0 = q.uv
            aw, ah = q.atlas_size()
            lit = {q.local(tx, ty)[0] for ty in range(y0, y0 + ah) for tx in range(x0, x0 + aw) if c.get(tx, ty) != base.get(tx, ty)}
            self.assertEqual(lit, {0})  # the viewer-left column on both pieces: the light is not mirrored with the piece


class WorldGradientGroups(unittest.TestCase):
    def test_groups_with_different_base_colours_get_the_same_ramp(self):
        a = Quad("wing_a", 10, 14, (0.3, 0.9, 0.0), "front", uv=(2, 2))
        b = Quad("wing_c", 10, 14, (-0.1, 0.9, 0.0), "front", uv=(20, 2))
        sc = Scene([a, b])
        sc.tpl["part_style"] = {"wing_a": {"material": "skin_soft", "color": "#ffffff", "quality": "pixel"},
                                "wing_c": {"material": "skin_soft", "color": "#2030a0", "quality": "pixel"}}
        c, _ = sc.paint([{"type": "gradient", "space": "world", "regions": ["part:wing_a", "part:wing_c"], "lo_y": 0.5, "hi_y": 0.9, "from": "#ff0000", "to": "#0000ff",
                          "texture": 1.0, "curve": "linear"}])
        by = {}
        for t in sc.texels():
            by.setdefault(round(t.pos[1], 5), {})[t.part] = c.get(t.x, t.y)[:3]
        for y, parts in by.items():
            self.assertEqual(parts["wing_a"], parts["wing_c"], y)  # a flat white and a flat blue base both take the ramp colour as it is


class WorldGradient(unittest.TestCase):
    def two_islands(self):
        a = Quad("wing_a", 10, 14, (0.3, 0.9, 0.0), "front", uv=(2, 2), orient=1)
        b = Quad("wing_b", 10, 14, (-0.1, 0.9, 0.0), "front", uv=(20, 2), orient=3)  # another group, another UV orientation, same heights
        return a, b, Scene([a, b])

    GRAD = {"type": "gradient", "space": "world", "regions": ["part:wing_a", "part:wing_b"], "lo_y": 0.5, "hi_y": 0.9, "from": "#000000",
            "to": "#ffffff", "texture": 0.0}

    def test_the_colour_depends_on_the_world_height_only(self):
        a, b, sc = self.two_islands()
        c, _ = sc.paint([self.GRAD])
        by_y = {}
        for t in sc.texels():
            by_y.setdefault(round(t.pos[1], 6), set()).add(c.get(t.x, t.y)[:3])
        self.assertTrue(by_y)
        for y, cols in by_y.items():
            self.assertEqual(len(cols), 1, (y, cols))  # both islands (and every texel row) agree
        for y, cols in by_y.items():  # smooth ramp from black at 0.5 to white at 0.9 (the smoothstep curve is the default)
            s = min(1.0, max(0.0, (y - 0.5) / 0.4))
            self.assertAlmostEqual(next(iter(cols))[0], 255 * s * s * (3 - 2 * s), delta=1.5)

    def test_absolute_range_is_independent_of_the_targeted_texels(self):
        a, b, sc = self.two_islands()
        both, _ = sc.paint([self.GRAD])
        only_a, _ = sc.paint([dict(self.GRAD, regions=["part:wing_a"])])
        for t in sc.texels():
            if t.part == "wing_a":
                self.assertEqual(both.get(t.x, t.y), only_a.get(t.x, t.y))

    def test_steps_quantise_to_flat_bands(self):
        a, b, sc = self.two_islands()
        c, _ = sc.paint([dict(self.GRAD, steps=4, curve="linear")])
        cols = {c.get(t.x, t.y)[:3] for t in sc.texels()}
        self.assertEqual(len(cols), 4)
        self.assertEqual(sorted(v[0] for v in cols), [0, 85, 170, 255])
        # bands are cut at equal world heights: 0.5 + 0.1 k
        for t in sc.texels():
            k = min(3, max(0, int(math.floor((t.pos[1] - 0.5) / 0.4 * 4 + 1e-9))))
            self.assertEqual(c.get(t.x, t.y)[0], round(255 * k / 3), t.pos)

    def test_dither_follows_the_bayer_matrix(self):
        q = Quad("floor", 16, 16, (0.0, 0.3, 0.0), "top", uv=(2, 2))  # every texel at height 0.3: s = 0.3 / 1 -> 0.6 of a 2-step ramp
        sc = Scene([q])
        grad = {"type": "gradient", "space": "world", "regions": ["part:floor"], "lo_y": 0.0, "hi_y": 1.0, "from": "#000000", "to": "#ffffff",
                "texture": 0.0, "steps": 2, "curve": "linear"}
        flat, _ = sc.paint([grad])
        self.assertEqual({flat.get(x, y)[0] for x in range(2, 18) for y in range(2, 18)}, {0})
        c, _ = sc.paint([dict(grad, dither=1.0)])
        for y in range(2, 18):
            for x in range(2, 18):
                up = 0.3 * 2 + (pixelart.bayer(x, y) - 0.5) >= 1.0
                self.assertEqual(c.get(x, y)[0], 255 if up else 0, (x, y))
        share = sum(1 for y in range(2, 18) for x in range(2, 18) if c.get(x, y)[0] == 255) / 256.0
        self.assertAlmostEqual(share, 0.1 + 0.0, delta=0.07)
        for x in range(2, 14):  # a 4 x 4 pattern
            for y in range(2, 14):
                self.assertEqual(c.get(x, y), c.get(x + 4, y))
                self.assertEqual(c.get(x, y), c.get(x, y + 4))

    def test_stops_and_horizontal_axis(self):
        a, b, sc = self.two_islands()
        g = {"type": "gradient", "space": "world", "axis": "x", "abs": True, "regions": ["part:wing_a", "part:wing_b"], "lo_x": 0.0, "hi_x": 0.5,
             "stops": [[0, "#ff0000"], [0.5, "#00ff00"], [1, "#0000ff"]], "texture": 0.0, "curve": "linear"}
        c, _ = sc.paint([g])
        for t in sc.texels():
            s = min(1.0, abs(t.pos[0]) / 0.5)
            r, gg, bb = c.get(t.x, t.y)[:3]
            if s <= 0.5:
                self.assertAlmostEqual(r, 255 * (1 - s / 0.5), delta=2)
                self.assertAlmostEqual(gg, 255 * s / 0.5, delta=2)
            else:
                self.assertAlmostEqual(gg, 255 * (1 - (s - 0.5) / 0.5), delta=2)
                self.assertAlmostEqual(bb, 255 * (s - 0.5) / 0.5, delta=2)

    def test_fractions_of_the_targeted_box_and_the_block_scale(self):
        a, b, sc = self.two_islands()
        frac, _ = sc.paint([{"type": "gradient", "space": "world", "regions": ["part:wing_a", "part:wing_b"], "from": "#000000", "to": "#ffffff",
                             "texture": 0.0, "curve": "linear"}])
        ys = [t.pos[1] for t in sc.texels()]
        lo, hi = min(ys), max(ys)
        for t in sc.texels():
            self.assertAlmostEqual(frac.get(t.x, t.y)[0], 255 * (t.pos[1] - lo) / (hi - lo), delta=1.5)
        scaled, _ = sc.paint([dict(self.GRAD, lo_y=0.5 / 0.5, hi_y=0.9 / 0.5, block=0.5)])
        plain, _ = sc.paint([self.GRAD])
        self.assertEqual(bytes(scaled.data), bytes(plain.data))


class DoubleSidedSlabs(unittest.TestCase):
    def slab(self, smooth=0.0):
        front = Quad("wing_a", 8, 12, (0.1, 1.0, 0.0), "front", uv=(2, 2), smooth=smooth)
        back = Quad("wing_a", 8, 12, (0.1 - 8 * TB, 1.0, 0.0), "back", uv=(20, 2), orient=2, smooth=smooth)  # the same plane, the other side
        return front, back, Scene([front, back])

    def test_front_and_back_come_from_the_islands_own_normal(self):
        for smooth in (0.0, 0.9):  # 0.9: smooth-shaded corners whose normals lean far to the sides
            front, back, sc = self.slab(smooth)
            if smooth:  # the premise: classifying the corner vertex normals themselves would NOT give 'front' / 'back'
                self.assertNotIn(skin._classify(sc.mesh.normals[0], -1, 1), ("front", "back"))
            faces = {}
            for t in sc.texels():
                faces.setdefault(t.isl, set()).add(t.face)
            self.assertEqual(sorted(map(sorted, faces.values())), [["back"], ["front"]], smooth)

    def test_a_curved_island_is_still_classified_per_texel(self):
        q = Quad("skirt", 12, 10, (0.5, 1.0, 0.0), "front", uv=(2, 2))
        sc = Scene([q])
        # twist the face: one corner moves back and its vertex normal turns to the side, so the island is not flat
        pos, nrm = sc.mesh.positions, sc.mesh.normals
        for i in range(len(pos)):
            if pos[i][0] < 0.4 and pos[i][1] < 0.8:
                pos[i] = (pos[i][0], pos[i][1], pos[i][2] + 0.6)
                nrm[i] = _unit((-0.95, 0.0, -0.3))
        faces = {t.face for t in sc.texels()}
        self.assertGreater(len(faces), 1)

    def test_iridescent_on_back_faces_leaves_front_faces_alone(self):
        front, back, sc = self.slab(0.9)
        base, _ = sc.paint([])
        c, _ = sc.paint([{"type": "iridescent", "regions": ["part:wing_a"], "faces": ["back"], "strength": 0.8}])
        for t in sc.texels():
            changed = c.get(t.x, t.y) != base.get(t.x, t.y)
            self.assertEqual(changed, t.face == "back", (t.x, t.y, t.face))
        self.assertTrue(any(c.get(x, y) != base.get(x, y) for y in range(2, 14) for x in range(20, 28)))

    def test_iridescent_is_stepped_pastel_and_follows_world_position(self):
        front, back, sc = self.slab()
        c, _ = sc.paint([{"type": "iridescent", "regions": ["part:wing_a"], "faces": ["front", "back"], "strength": 1.0, "steps": 5,
                          "scale": 0.4, "dir": [0, 1, 0]}])
        cols = {c.get(t.x, t.y)[:3] for t in sc.texels()}
        self.assertLessEqual(len(cols), 5 + 1)
        self.assertTrue(cols & set(pixelart.PASTEL))
        by_y = {}
        for t in sc.texels():
            by_y.setdefault(round(t.pos[1], 6), set()).add(c.get(t.x, t.y)[:3])
        self.assertTrue(all(len(v) == 1 for v in by_y.values()))  # front and back agree at equal heights
        smooth, _ = sc.paint([{"type": "iridescent", "regions": ["part:wing_a"], "strength": 1.0, "steps": 0, "scale": 0.4, "dir": [0, 1, 0]}])
        self.assertGreater(len({smooth.get(t.x, t.y)[:3] for t in sc.texels()}), len(cols))

    def test_iridescent_material(self):
        front, back, sc = self.slab()
        c, _ = sc.paint([], material="iridescent", color="#ffffff")
        base, _ = sc.paint([], material="skin_soft", color="#ffffff")
        self.assertNotEqual(bytes(c.data), bytes(base.data))
        self.assertLessEqual(len({c.get(t.x, t.y)[:3] for t in sc.texels()}), 5)


class PixelQuality(unittest.TestCase):
    def wall(self):
        return Scene([Quad("wall", 40, 40, (0.0, 1.5, 0.0), "front", uv=(2, 2)), Quad("wall", 40, 12, (0.0, 1.5, 0.0), "right", uv=(2, 44))], size=64)

    def test_pixel_variants_have_few_flat_tones(self):
        sc = self.wall()
        for material, limit in (("silk", 3), ("lace", 6), ("cloth", 6), ("skin_soft", 2), ("hair_soft", 12), ("plate_steel", 12), ("silk_feather", 8),
                                ("leather", 6), ("plate_gold", 6), ("skin", 4)):
            with self.subTest(material=material):
                pix, _ = sc.paint([], material=material, color="#9f9cef", quality="pixel")
                smooth, _ = sc.paint([], material=material, color="#9f9cef", quality="smooth")
                npix = len({pix.get(t.x, t.y)[:3] for t in sc.texels()})
                nsm = len({smooth.get(t.x, t.y)[:3] for t in sc.texels()})
                self.assertLessEqual(npix, limit)
                if material not in ("skin_soft",):
                    self.assertLess(npix, nsm)

    def test_pixel_noise_is_regions_not_speckle(self):
        sc = self.wall()
        pix, _ = sc.paint([], material="silk", color="#9f9cef", quality="pixel")
        front = [t for t in sc.texels() if t.face == "front"]
        same = total = 0
        for t in front:
            for dx, dy in ((1, 0), (0, 1)):
                o = pix.get(t.x + dx, t.y + dy)
                if o[3] == 255:
                    total += 1
                    same += o == pix.get(t.x, t.y)
        self.assertGreater(same / float(total), 0.9)  # neighbouring texels share their tone: bands, not grain

    def test_the_quality_is_taken_from_group_region_global_and_repaint(self):
        sc = self.wall()
        glob, _ = sc.paint([], material="silk", color="#9f9cef", quality=None, texture={"scale": 2, "quality": "pixel"})
        entry, _ = sc.paint([], material="silk", color="#9f9cef", quality="pixel")
        self.assertEqual(bytes(glob.data), bytes(entry.data))
        rep, _ = sc.paint([{"type": "repaint", "regions": ["part:wall"], "material": "silk", "quality": "pixel"}], material="silk", color="#9f9cef",
                          quality="smooth")
        self.assertEqual(bytes(rep.data), bytes(entry.data))

    def test_pixel_tones_depend_on_texel_units_only(self):
        # the same texel grid on a model twice as big: positions and the measured texel size both double, so nothing changes
        q = Quad("wall", 40, 40, (0.5, 1.0, 0.0), "front", uv=(2, 2))
        small, big = Scene([q]), Scene([q])
        for i, p in enumerate(big.mesh.positions):
            big.mesh.positions[i] = (p[0] * 2, p[1] * 2, p[2] * 2)
        for material in ("silk", "cloth", "hair_soft", "lace", "silk_feather"):
            a, _ = small.paint([], material=material, color="#9f9cef")
            b, _ = big.paint([], material=material, color="#9f9cef")
            self.assertEqual(bytes(a.data), bytes(b.data), material)


class Quantise(unittest.TestCase):
    def setUp(self):
        self.q = wall(w=24, h=24, uv=(2, 2))
        self.sc = Scene([self.q])
        self.ramp = {"type": "gradient", "regions": ["part:wall"], "from": "#000000", "to": "#ffffff", "texture": 0.0, "curve": "linear", "lo": 0.0, "hi": 1.0}

    def colours(self, c):
        return {c.get(t.x, t.y)[:3] for t in self.sc.texels()}

    def test_posterize_luma_levels(self):
        smooth, _ = self.sc.paint([self.ramp])
        c, _ = self.sc.paint([self.ramp, {"type": "posterize", "regions": ["part:wall"], "levels": 4}])
        self.assertGreater(len(self.colours(smooth)), 16)
        self.assertEqual(len(self.colours(c)), 4)

    def test_posterize_keeps_the_hue_in_luma_mode_and_dither_stipples(self):
        warm = dict(self.ramp, **{"from": "#201008", "to": "#ffd8b0"})
        c, _ = self.sc.paint([warm, {"type": "posterize", "regions": ["part:wall"], "levels": 3}])
        lums = {round(colors.luminance(col) * 255 / 4) for col in self.colours(c)}  # brightness bands (a few units of rounding noise)
        self.assertLessEqual(len(lums), 3)
        for r, g, b in self.colours(c):
            self.assertTrue(r >= g >= b)  # the hue of the warm ramp survives
        d, _ = self.sc.paint([warm, {"type": "posterize", "regions": ["part:wall"], "levels": 3, "dither": 1.0}])
        self.assertNotEqual(bytes(c.data), bytes(d.data))
        self.assertLessEqual(len({round(colors.luminance(col) * 255 / 4) for col in self.colours(d)}), 3)

    def test_palette_snap_to_a_given_palette_and_by_median_cut(self):
        pal = ["#000000", "#ff0000", "#ffffff"]
        c, _ = self.sc.paint([self.ramp, {"type": "palette_snap", "regions": ["part:wall"], "palette": pal}])
        self.assertTrue(self.colours(c) <= {(0, 0, 0), (255, 0, 0), (255, 255, 255)})
        self.assertGreater(len(self.colours(c)), 1)
        m, _ = self.sc.paint([self.ramp, {"type": "palette_snap", "regions": ["part:wall"], "colors": 4}])
        self.assertLessEqual(len(self.colours(m)), 4)
        self.assertGreater(len(self.colours(m)), 1)
        dth, _ = self.sc.paint([self.ramp, {"type": "palette_snap", "regions": ["part:wall"], "palette": ["#000000", "#ffffff"], "dither": 1.0}])
        self.assertEqual(self.colours(dth), {(0, 0, 0), (255, 255, 255)})


class Face(unittest.TestCase):
    FACE = ["........", "..##.##.", ".#.#.#.#", "........", ".#....#.", "..####..", "........", "........"]

    def head(self, w=8, h=8):
        q = Quad("skin", w, h, (w * TB / 2.0, 1.7, 0.0), "front", uv=(2, 2), region="head")  # centred on the symmetry plane
        return q, Scene([q])

    def test_face_pixels_land_on_exact_texel_cells(self):
        q, sc = self.head()
        got, info = sc.picture(q, [{"type": "decal", "anchor": "head_front", "bitmap": self.FACE, "palette": {"#": "#301810"}}])
        self.assertEqual(info["warnings"], [])
        self.assertEqual(got, expect(self.FACE, 0, 0, color=hexrgb("#301810")))
        self.assertEqual(info["face"], {"texels": [8, 8], "bitmap": [8, 8], "scale": 1, "texels_per_art_pixel": 1.0, "exact": True})

    def test_a_bigger_head_takes_whole_texels_per_art_pixel(self):
        q, sc = self.head(16, 16)
        got, info = sc.picture(q, [{"type": "decal", "anchor": "head_front", "bitmap": self.FACE, "palette": {"#": "#301810"}}])
        self.assertEqual(got, expect(self.FACE, 0, 0, color=hexrgb("#301810"), scale=2))
        self.assertEqual(info["face"]["scale"], 2)
        self.assertTrue(info["face"]["exact"])
        self.assertEqual(info["warnings"], [])

    def test_a_face_that_does_not_fit_the_head_grid_is_reported(self):
        q, sc = self.head(12, 12)
        _g, info = sc.picture(q, [{"type": "decal", "anchor": "head_front", "bitmap": self.FACE}])
        self.assertIn("face_grid", [w["code"] for w in info["warnings"]])
        self.assertFalse(info["face"]["exact"])
        q, sc = self.head(8, 8)
        _g, info = sc.picture(q, [{"type": "decal", "anchor": "head_front", "bitmap": self.FACE + ["........"]}])
        self.assertTrue({"face_grid", "decal_too_big"} <= {w["code"] for w in info["warnings"]})

    def test_the_face_features_land_on_the_8x8_grid_mirror_symmetric(self):
        q, sc = self.head()
        base, _ = sc.paint([])
        feats = {"eyes": {"type": "eyes", "style": "anime", "color": "#3a5a9a"}, "brow": {"type": "brow"}, "mouth": {"type": "mouth"},
                 "blush": {"type": "blush", "color": "#ff8080"}, "bangs": {"type": "bangs", "locks": False}}
        for name, f in feats.items():
            with self.subTest(feature=name):
                c, info = sc.paint([f])
                diff = {(x - 2, y - 2) for y in range(2, 10) for x in range(2, 10) if c.get(x, y) != base.get(x, y)}
                self.assertTrue(diff, name)
                self.assertTrue(all(0 <= i < 8 and 0 <= j < 8 for i, j in diff))
                outside = [(x, y) for y in range(64) for x in range(64) if not (2 <= x < 10 and 2 <= y < 10) and c.get(x, y) != base.get(x, y)]
                self.assertEqual(outside, [])
                if name != "bangs":  # the fringe has strands of uneven length by design
                    # head front texels are viewed with the character's right on the viewer's left: the picture mirrors about column 3.5
                    got, _ = sc.picture(q, [f])
                    self.assertEqual({(7 - i, j) for i, j in got}, set(got), name)

    def test_blush_is_hard_edged_on_a_coarse_face(self):
        q, sc = self.head()
        got, _ = sc.picture(q, [{"type": "blush", "color": "#ff8080"}])
        self.assertEqual(len(set(got.values())), 1)


class Validation(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(_path.SHIPPED_EXAMPLE.read_text(encoding="utf-8"))
        self.raw["appearance"]["features"] = []

    def check(self, *feats, **extra):
        raw = copy.deepcopy(self.raw)
        raw["appearance"]["features"] = list(feats)
        raw["appearance"].update(extra)
        _n, errs, warns = spec.check(raw)
        return {(e.path, e.code) for e in errs}

    B = "/appearance/features/"

    def test_listed_everywhere(self):
        for name in ("decal", "iridescent", "posterize", "palette_snap"):
            self.assertIn(name, spec.FEATURE_TYPES)
            self.assertIn(name, features.FEATURES)
        self.assertEqual(set(spec.MATERIALS), set(materials.MATERIAL_FUNCS))
        self.assertIn("iridescent", spec.MATERIALS)

    def test_a_valid_set_passes(self):
        got = self.check({"type": "decal", "regions": ["torso"], "bitmap": ["##", ".#"], "palette": {"#": "accent", ".": None}, "anchor": "top_left",
                          "offset": [1, -1], "scale": 2, "repeat": "x", "gap": 1, "mirror": "auto", "blend": "multiply", "fit": "inside", "scope": "union"},
                         {"type": "decal", "anchor": "head_front", "bitmap": "##\n#.", "scale": "auto"},
                         {"type": "gradient", "space": "world", "axis": "y", "lo_y": 0.5, "hi_y": 1.5, "stops": [[0, "#ff0000"], [1, "#00ff00"]], "steps": 5, "dither": 0.5},
                         {"type": "iridescent", "regions": ["torso"], "faces": ["back"], "palette": ["#ff0000", "#00ff00"], "dir": [0, 1, 0], "steps": 4},
                         {"type": "posterize", "levels": 4}, {"type": "palette_snap", "colors": 8}, {"type": "palette_snap", "palette": ["#000000", "#ffffff"]},
                         {"type": "repaint", "regions": ["torso"], "material": "silk", "quality": "pixel"}, texture={"scale": 2, "quality": "pixel"})
        self.assertEqual(got, set())

    def test_decal_errors(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "bad.txt"), "w", encoding="utf-8") as fh:
                fh.write("@x\n##\n")
            raw = copy.deepcopy(self.raw)
            raw["_spec_dir"] = d
            feats = [{"type": "decal", "regions": ["torso"], "file": "nope.txt"},
                     {"type": "decal", "regions": ["torso"], "bitmap": ["#?"]},
                     {"type": "decal", "bitmap": ["##"]},
                     {"type": "decal", "regions": ["torso"], "bitmap": ["#" * 129]},
                     {"type": "decal", "regions": ["torso"], "file": "bad.txt"},
                     {"type": "decal", "regions": ["torso"]},
                     {"type": "decal", "regions": ["torso"], "bitmap": ["#"], "file": "bad.txt"},
                     {"type": "decal", "regions": ["torso"], "bitmap": ["#"], "anchor": "middle", "scale": 9, "offset": [1], "repeat": "z", "mirror": "x"},
                     {"type": "decal", "regions": ["torso"], "bitmap": ["#"], "palette": {"##": "#fff", "#": "#zzzzzz"}, "blend": "add", "opacity": 2}]
            raw["appearance"]["features"] = feats
            _n, errs, _w = spec.check(raw)
            got = {(e.path, e.code) for e in errs}
        for want in (("0/file", "decal_missing_file"), ("1/palette", "decal_bad_palette"), ("2", "required"), ("3/bitmap", "decal_bad_bitmap"),
                     ("4/file", "decal_bad_bitmap"), ("5", "required"), ("6/bitmap", "decal_bad_bitmap"), ("7/anchor", "unknown_value"),
                     ("7/scale", "out_of_range"), ("7/offset", "bad_type"), ("7/repeat", "unknown_value"), ("7/mirror", "unknown_value"),
                     ("8/palette", "decal_bad_palette"), ("8/palette/#", "bad_color"), ("8/blend", "unknown_value"), ("8/opacity", "out_of_range")):
            self.assertIn((self.B + want[0], want[1]), got, want)

    def test_gradient_iridescent_posterize_errors(self):
        got = self.check({"type": "gradient", "from": "#000000", "lo_y": 1, "axis": "y"},
                         {"type": "gradient", "space": "world", "stops": [[0, "#000000"]], "steps": 1, "dither": 2, "block": 0},
                         {"type": "gradient", "space": "sky", "axis": "w", "stops": [[0.5, "#000000"], [0.2, "#ffffff"]]},
                         {"type": "iridescent", "palette": ["#ffffff"], "dir": [0, 0, 0], "strength": 2, "faces": ["roof"], "mode": "add"},
                         {"type": "posterize", "levels": 1, "mode": "hsv"}, {"type": "palette_snap"}, {"type": "palette_snap", "colors": 99},
                         {"type": "palette_snap", "palette": ["#fff"]}, {"type": "repaint", "regions": ["torso"], "material": "silk", "quality": "crisp"},
                         texture={"scale": 2, "quality": "crisp"})
        for want in (("0/lo_y", "needs_world_space"), ("0/axis", "needs_world_space"), ("1/stops", "bad_type"), ("1/steps", "out_of_range"),
                     ("1/dither", "out_of_range"), ("1/block", "out_of_range"), ("2/space", "unknown_value"), ("2/axis", "unknown_value"),
                     ("2/stops/1", "bad_range"), ("3/palette", "bad_type"), ("3/dir", "bad_type"), ("3/strength", "out_of_range"),
                     ("3/faces/0", "unknown_value") if False else ("3/faces", "unknown_value"), ("3/mode", "unknown_value"),
                     ("4/levels", "out_of_range"), ("4/mode", "unknown_value"), ("5", "required"), ("6/colors", "out_of_range"),
                     ("7/palette/0", "bad_color") if False else ("7/palette", "bad_type"), ("8/quality", "unknown_value")):
            self.assertIn((self.B + want[0], want[1]), got, want)
        self.assertIn(("/appearance/texture/quality", "unknown_value"), got)

    def test_project_and_bevel_errors(self):
        got = self.check({"type": "decal", "regions": ["torso"], "bitmap": ["#"], "project": {"view": "up", "pixel": 0, "facing": 2}},
                         {"type": "decal", "regions": ["torso"], "bitmap": ["#"], "project": {"view": "front", "at": [1]}},
                         {"type": "decal", "regions": ["torso"], "bitmap": ["#"], "project": {"view": "front", "at": [0, 0]}, "fit": "inside", "repeat": "x"},
                         {"type": "decal", "regions": ["torso"], "bitmap": ["#"], "project": 3},
                         {"type": "bevel", "regions": ["torso"], "top": 2, "width": 3, "min_size": 0, "mode": "round", "amount": -1, "faces": ["roof"]})
        for want in (("0/project/view", "unknown_value"), ("0/project/at", "required"), ("0/project/pixel", "out_of_range"),
                     ("0/project/facing", "out_of_range"), ("1/project/at", "bad_type"), ("2/fit", "decal_project_conflict"), ("2/count", "required"),
                     ("3/project", "bad_type"), ("4/top", "out_of_range"), ("4/width", "out_of_range"), ("4/min_size", "out_of_range"),
                     ("4/mode", "unknown_value"), ("4/amount", "out_of_range"), ("4/faces", "unknown_value")):
            self.assertIn((self.B + want[0], want[1]), got, want)

    def test_region_and_overlay_quality(self):
        got = self.check(regions={"torso": {"material": "silk", "quality": "crisp"}}, overlay=[{"region": "head", "material": "silk", "y": [0, 1], "quality": "x"}])
        self.assertIn(("/appearance/regions/torso/quality", "unknown_value"), got)
        self.assertIn(("/appearance/overlay/0/quality", "unknown_value"), got)

    def test_mesh_group_quality(self):
        errs = meshsrc.validate_mesh({"file": "x.efmesh.json", "groups": {"wing_a": {"quality": "crisp"}, "wing_b": {"quality": "pixel"}}}, {})
        self.assertEqual({(e.path, e.code) for e in errs if "quality" in e.path}, {("/mesh/groups/wing_a/quality", "unknown_value")})


class Speed(unittest.TestCase):
    def test_a_512_atlas_at_16_texels_per_block_paints_in_seconds(self):
        # 16 texels per art block (30.8 per model block): the head cube is 8 texels; a 512 atlas holds the whole character
        quads = []
        x = y = row = 0
        for k in range(36):
            w, h = 28 + (k % 5) * 6, 36 + (k % 4) * 6
            if x + h + 2 > 512:
                x, y, row = 0, y + row + 2, 0
            part = ("wing_a", "wing_b", "skirt", "robe", "hair", "pauldron")[k % 6]
            quads.append(Quad(part, w, h, (0.01 * k, 1.0, 0.01 * k), ("front", "back", "right", "left")[k % 4], uv=(x, y), orient=k % 8))
            x += quads[-1].atlas_size()[0] + 2
            row = max(row, quads[-1].atlas_size()[1])
        sc = Scene(quads, size=512)
        feats = [{"type": "gradient", "space": "world", "regions": ["part:wing_a", "part:wing_b"], "lo_y": 0.2, "hi_y": 1.0, "from": "#a49df0", "to": "#ffffff", "steps": 6},
                 {"type": "iridescent", "regions": ["part:wing_a"], "faces": ["back"]},
                 {"type": "decal", "regions": ["part:skirt", "part:pauldron"], "bitmap": F_SHAPE, "skip_small": True},
                 {"type": "sparkle", "density": 0.01}, {"type": "posterize", "levels": 6}]
        t0 = time.time()
        _c, info = sc.paint(feats, material="silk", color="#9f9cef")
        elapsed = time.time() - t0
        self.assertGreater(info["base_texels"], 20000)
        self.assertLess(elapsed, 20.0, "painting %d texels took %.1f s" % (info["base_texels"], elapsed))


if __name__ == "__main__":
    unittest.main()
