import _path  # noqa
import copy
import tempfile
import unittest
from pathlib import Path

from bossforge_core import efassets, efmesh, pngio, preview, skin, spec
from bossforge_core.efmesh import Mesh, Tri

REF = spec.data("versions")["epicfight"]["assets_ref"]
CACHE = efassets.default_cache_dir()
SCALE = 1  # texture scale used by most tests (64x64 authored -> 64x64): keeps the suite fast


# --------------------------------------------------------------------------- synthetic biped-like mesh
class _Builder:
    """Boxes in the Minecraft cuboid UV layout, built in (r, u, f) = (viewer-right, up, front) pixels.

    World x = fs*r and z = fs*f, so front_sign -1 reproduces the real EF orientation (front = -z, the
    character's right arm at +x) and +1 is the mirrored convention.
    """

    def __init__(self, fs):
        self.fs = fs
        self.pos, self.nrm, self.uvs, self.tris = [], [], [], []

    def _quad(self, part, corners, rect, normal):
        fs = self.fs
        pi = len(self.pos)
        for (r, u, f) in corners:
            self.pos.append((fs * r / 16.0, u / 16.0, fs * f / 16.0))
        nr, nu, nf = normal
        self.nrm.append((fs * nr, nu, fs * nf))
        ni = len(self.nrm) - 1
        x, y, w, h = rect
        ti = len(self.uvs)
        for (uu, vv) in ((0, 0), (1, 0), (1, 1), (0, 1)):
            self.uvs.append(((x + uu * w) / 64.0, (y + vv * h) / 64.0))
        for tri in ((0, 1, 2), (0, 2, 3)):
            self.tris.append(Tri(part, tuple(pi + k for k in tri), tuple(ti + k for k in tri), (ni, ni, ni)))

    def box(self, part, size, center, uv0, grow=0.0):
        w, h, d = size
        cr, cu, cf = center
        hw, hh, hd = w / 2.0 + grow, h / 2.0 + grow, d / 2.0 + grow
        u0, v0 = uv0

        def P(r, u, f):
            return (cr + r, cu + u, cf + f)

        q = self._quad
        q(part, [P(-hw, hh, hd), P(hw, hh, hd), P(hw, -hh, hd), P(-hw, -hh, hd)], (u0 + d, v0 + d, w, h), (0, 0, 1))
        q(part, [P(hw, hh, -hd), P(-hw, hh, -hd), P(-hw, -hh, -hd), P(hw, -hh, -hd)], (u0 + 2 * d + w, v0 + d, w, h), (0, 0, -1))
        q(part, [P(-hw, hh, -hd), P(-hw, hh, hd), P(-hw, -hh, hd), P(-hw, -hh, -hd)], (u0, v0 + d, d, h), (-1, 0, 0))
        q(part, [P(hw, hh, hd), P(hw, hh, -hd), P(hw, -hh, -hd), P(hw, -hh, hd)], (u0 + d + w, v0 + d, d, h), (1, 0, 0))
        q(part, [P(-hw, hh, -hd), P(hw, hh, -hd), P(hw, hh, hd), P(-hw, hh, hd)], (u0 + d, v0, w, d), (0, 1, 0))
        q(part, [P(-hw, -hh, -hd), P(hw, -hh, -hd), P(hw, -hh, hd), P(-hw, -hh, hd)], (u0 + d + w, v0, w, d), (0, -1, 0))


REGIONS = {"head": "head", "hat": "head", "torso": "torso", "rightArm": "arm_r", "leftArm": "arm_l"}


def make_mesh(fs=-1, arms=True, overlay=True):
    b = _Builder(fs)
    b.box("head", (8, 8, 8), (0, 28, 0), (0, 0))
    if overlay:
        b.box("hat", (8, 8, 8), (0, 28, 0), (32, 0), grow=0.5)
    b.box("torso", (8, 12, 4), (0, 18, 0), (16, 16))
    if arms:
        b.box("rightArm", (4, 12, 4), (-6, 18, 0), (40, 16))
        b.box("leftArm", (4, 12, 4), (6, 18, 0), (32, 48))
    regions = {k: v for k, v in REGIONS.items() if overlay or k != "hat"}
    tpl = {"authored_uv": [64, 64], "uv_origin": "top", "front_sign": fs,
           "overlay_parts": ["hat"] if overlay else [], "part_regions": regions}
    return Mesh(b.pos, b.nrm, b.uvs, b.tris, regions), tpl


PALETTE = {"primary": "#9aa5b8", "secondary": "#8c1a20", "accent": "#d4aa32",
           "skin": "#c89a7c", "hair": "#6a4a30", "eyes": "#ff00ff"}


def appearance(**kw):
    ap = {"palette": dict(PALETTE),
          "regions": {"head": {"material": "skin", "color": "skin"},
                      "torso": {"material": "plate_steel", "color": "primary"},
                      "arms": {"material": "cloth", "color": "secondary"}},
          "overlay": [], "overlay_enabled": True, "features": [], "texture": {"scale": SCALE}}
    ap.update(kw)
    return ap


def paint(mesh, tpl, ap, seed=7, **kw):
    canvas, info = skin.paint(mesh, tpl, ap, seed, **kw)
    return canvas, info


def rect_px(rect, scale=SCALE):
    x, y, w, h = rect
    return [(i, j) for j in range(y * scale, (y + h) * scale) for i in range(x * scale, (x + w) * scale)]


# face rectangles in the 64x64 authored layout
HEAD_FACES = [(8, 8, 8, 8), (0, 8, 8, 8), (16, 8, 8, 8), (24, 8, 8, 8), (8, 0, 8, 8), (16, 0, 8, 8)]
HAT_FACES = [(40, 8, 8, 8), (32, 8, 8, 8), (48, 8, 8, 8), (56, 8, 8, 8), (40, 0, 8, 8), (48, 0, 8, 8)]
TORSO_FACES = [(20, 20, 8, 12), (16, 20, 4, 12), (28, 20, 4, 12), (32, 20, 8, 12), (20, 16, 8, 4), (28, 16, 8, 4)]
HEAD_FRONT = (8, 8, 8, 8)
TORSO_FRONT = (20, 20, 8, 12)
TORSO_BACK = (32, 20, 8, 12)
HAT_FRONT = (40, 8, 8, 8)


def changed(a, b, rect=None):
    out = []
    for j in range(a.h):
        for i in range(a.w):
            if a.get(i, j) != b.get(i, j):
                out.append((i, j))
    return out


def inside(points, rect, scale=SCALE):
    x, y, w, h = rect
    return all(x * scale <= i < (x + w) * scale and y * scale <= j < (y + h) * scale for i, j in points)


def mean_rgb(canvas, rect):
    px = [canvas.get(i, j) for i, j in rect_px(rect)]
    return tuple(sum(p[k] for p in px) / len(px) for k in range(3))


def dist(a, b):
    return sum((a[k] - b[k]) ** 2 for k in range(3)) ** 0.5


def is_magenta(px):
    return px[0] > 200 and px[2] > 200 and px[1] < 40


def count_magenta(canvas, x0, x1):
    n = 0
    for j in range(canvas.h):
        for i in range(x0, x1):
            r, g, b, a = canvas.get(i, j)
            if r > 110 and b > 110 and g < 70 and abs(r - b) < 40:
                n += 1
    return n


class SyntheticSkinTests(unittest.TestCase):
    def setUp(self):
        self.mesh, self.tpl = make_mesh()

    def test_texture_dimensions_follow_scale(self):
        for scale in (1, 2, 4):
            c, _ = paint(self.mesh, self.tpl, appearance(texture={"scale": scale}))
            self.assertEqual((c.w, c.h), (64 * scale, 64 * scale))

    def test_texture_too_large_is_an_error(self):
        tpl = dict(self.tpl, authored_uv=[256, 256])
        with self.assertRaises(spec.BossForgeError) as cm:
            skin.paint(self.mesh, tpl, appearance(texture={"scale": 4}), 1)
        self.assertEqual(cm.exception.err.code, "texture_too_large")

    def test_every_base_texel_is_opaque(self):
        c, info = paint(self.mesh, self.tpl, appearance())
        for rect in HEAD_FACES + TORSO_FACES:
            for i, j in rect_px(rect):
                self.assertEqual(c.get(i, j)[3], 255, (rect, i, j))
        self.assertEqual(info["coverage"], 1.0)

    def test_overlay_is_transparent_without_entries(self):
        c, _ = paint(self.mesh, self.tpl, appearance())
        for rect in HAT_FACES:
            for i, j in rect_px(rect):
                self.assertEqual(c.get(i, j)[3], 0, (rect, i, j))

    def test_overlay_entry_is_opaque_only_inside_its_y_range(self):
        ap = appearance(overlay=[{"region": "head", "material": "plate_gold", "color": "accent", "y": [0.8, 1.0]}])
        c, _ = paint(self.mesh, self.tpl, ap)
        x0 = (HAT_FRONT[0] + 4) * SCALE
        rows = [c.get(x0, j)[3] == 255 for j in range(HAT_FRONT[1] * SCALE, (HAT_FRONT[1] + 8) * SCALE)]
        # hat front face spans y 32.5 .. 23.5 px over 8*SCALE rows; the head base box spans 24 .. 32 px
        expected = []
        for r in range(8 * SCALE):
            y_px = 32.5 - (r + 0.5) * 9.0 / (8 * SCALE)
            ly = min(1.0, max(0.0, (y_px - 24.0) / 8.0))
            expected.append(ly >= 0.8)
        self.assertEqual(rows, expected)
        self.assertTrue(any(rows) and not all(rows))
        # the overlay never leaks onto other regions: torso has no overlay layer here and stays steel-coloured
        self.assertLess(dist(mean_rgb(c, TORSO_FRONT), (154, 165, 184)), 90)

    def test_overlay_disabled_bakes_entries_into_base_texels(self):
        entry = {"region": "head", "material": "plate_gold", "color": "accent", "y": [0.8, 1.0]}
        plain, _ = paint(self.mesh, self.tpl, appearance())
        baked, _ = paint(self.mesh, self.tpl, appearance(overlay=[entry], overlay_enabled=False))
        for rect in HAT_FACES:
            for i, j in rect_px(rect):
                self.assertEqual(baked.get(i, j)[3], 0)
        x0 = (HEAD_FRONT[0] + 4) * SCALE
        last = (HEAD_FRONT[1] + 8) * SCALE - 1
        # gold is low in blue, skin is not; the top row is inside the y range, the bottom row is not
        self.assertLess(baked.get(x0, HEAD_FRONT[1] * SCALE)[2], 100)
        self.assertGreater(plain.get(x0, HEAD_FRONT[1] * SCALE)[2], 100)
        self.assertEqual(baked.get(x0, last)[:3], plain.get(x0, last)[:3])

    def test_overlay_entries_ignored_when_template_has_no_overlay_layer(self):
        mesh, tpl = make_mesh(overlay=False)
        entry = {"region": "head", "material": "plate_gold", "color": "accent", "y": [0.0, 1.0]}
        ref, _ = paint(mesh, tpl, appearance())
        c, info = paint(mesh, tpl, appearance(overlay=[entry]))
        self.assertEqual(c.data, ref.data)
        self.assertEqual([w["code"] for w in info["warnings"]], ["overlay_ignored"])

    def test_plate_steel_torso_stays_near_the_primary_colour(self):
        c, _ = paint(self.mesh, self.tpl, appearance())
        self.assertLess(dist(mean_rgb(c, TORSO_FRONT), (154, 165, 184)), 90)

    def test_deterministic_and_seed_sensitive(self):
        ap = appearance(features=[{"type": "wear"}, {"type": "cracks"}, {"type": "runes", "density": 1.0}])
        a, _ = paint(self.mesh, self.tpl, ap, seed=11)
        b, _ = paint(self.mesh, self.tpl, copy.deepcopy(ap), seed=11)
        c, _ = paint(self.mesh, self.tpl, ap, seed=12)
        self.assertEqual(pngio.write_png(None, a), pngio.write_png(None, b))
        self.assertNotEqual(a.data, c.data)

    def test_region_precedence_leaf_over_hands_over_group(self):
        ap = appearance(regions={
            "head": {"material": "skin", "color": "skin"},
            "torso": {"material": "plate_steel", "color": "primary"},
            "arms": {"material": "cloth", "color": "secondary"},
            "arm_l": {"material": "leather", "color": "accent"},
        })
        c, _ = paint(self.mesh, self.tpl, ap)
        right_front = (44, 20, 4, 12)  # rightArm uv (40,16): front face
        left_front = (36, 52, 4, 12)  # leftArm uv (32,48): front face
        self.assertLess(dist(mean_rgb(c, right_front), (140, 26, 32)), dist(mean_rgb(c, right_front), (212, 170, 50)))
        self.assertLess(dist(mean_rgb(c, left_front), (212, 170, 50)), dist(mean_rgb(c, left_front), (140, 26, 32)))
        ap["regions"] = {"head": {"material": "skin", "color": "skin"}, "torso": {"material": "plate_steel", "color": "primary"},
                         "arms": {"material": "cloth", "color": "secondary"},
                         "hands": {"material": "leather", "color": "accent"}}
        c2, _ = paint(self.mesh, self.tpl, ap)
        x = (44 + 2) * SCALE
        low = c2.get(x, (20 + 12) * SCALE - 1)[:3]  # lowest arm row = hand
        high = c2.get(x, 20 * SCALE)[:3]
        self.assertLess(dist(low, (212, 170, 50)), dist(low, (140, 26, 32)))
        self.assertLess(dist(high, (140, 26, 32)), dist(high, (212, 170, 50)))

    def test_arms_in_overlay_entries_reach_both_arms(self):
        # the mesh has no sleeve parts, so the entry has to be baked in with overlay disabled
        entry = {"region": "arms", "material": "plate_gold", "color": "accent", "y": [0.0, 1.0]}
        c, _ = paint(self.mesh, self.tpl, appearance(overlay=[entry], overlay_enabled=False))
        for rect in ((44, 20, 4, 12), (36, 52, 4, 12)):
            self.assertLess(dist(mean_rgb(c, rect), (212, 170, 50)), dist(mean_rgb(c, rect), (140, 26, 32)))

    # -- features
    def test_eyes_only_change_the_head_front_island(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        for style in ("human", "slit", "hollow", "visor"):
            with self.subTest(style=style):
                c, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "eyes", "style": style, "color": "eyes"}]))
                diff = changed(base, c)
                self.assertTrue(diff)
                self.assertTrue(inside(diff, HEAD_FRONT), diff[:5])

    def test_visor_eyes_use_the_eye_colour(self):
        c, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "eyes", "style": "visor", "color": "eyes"}]))
        hits = [(i, j) for i, j in rect_px(HEAD_FRONT) if is_magenta(c.get(i, j))]
        self.assertGreaterEqual(len(hits), 6 * SCALE)

    def test_scar_lands_on_the_characters_side_of_the_face(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        centre = (HEAD_FRONT[0] + 4) * SCALE
        means = {}
        for side in ("left", "right"):
            c, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "scar", "side": side}]))
            diff = changed(base, c)
            self.assertTrue(diff)
            self.assertTrue(inside(diff, HEAD_FRONT))
            means[side] = sum(i for i, _ in diff) / len(diff)
        # the character's left is the viewer's right; texture u grows toward the viewer's right on the front face
        self.assertGreater(means["left"], centre)
        self.assertLess(means["right"], centre)

    def test_brow_and_beard_stay_on_the_head_front(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        c, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "brow"}]))
        diff = changed(base, c)
        self.assertTrue(diff and inside(diff, HEAD_FRONT))
        c, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "beard", "length": "short", "color": "#c8c8d0"}]))
        diff = changed(base, c)
        front = [p for p in diff if HEAD_FRONT[0] * SCALE <= p[0] < 16 * SCALE and HEAD_FRONT[1] * SCALE <= p[1] < 16 * SCALE]
        self.assertTrue(front)
        # the beard covers the lower part of the face only
        self.assertTrue(all(j >= (HEAD_FRONT[1] + 5) * SCALE for _, j in front))

    def test_hair_styles(self):
        base, _ = paint(self.mesh, self.tpl, appearance())

        def styled(style):
            return paint(self.mesh, self.tpl, appearance(features=[{"type": "hair", "style": style, "color": "hair"}]))[0]

        short, long_, hood, bald = styled("short"), styled("long"), styled("hood"), styled("bald")
        self.assertEqual(bald.data, base.data)
        d_short, d_long, d_hood = set(changed(base, short)), set(changed(base, long_)), set(changed(base, hood))
        self.assertLess(d_short, d_long)  # long = short + back and sides
        self.assertTrue(set(rect_px((8, 0, 8, 8))) <= d_short)  # the top of the head is always covered
        back = set(rect_px((24, 8, 8, 8)))
        lower_back = {p for p in back if p[1] >= 14 * SCALE}
        self.assertFalse(lower_back & d_short)
        self.assertTrue(lower_back <= d_long)
        face = {(i, j) for i, j in rect_px(HEAD_FRONT) if 9 * SCALE <= i < 15 * SCALE and j >= 11 * SCALE}
        self.assertFalse(face & d_long)
        self.assertFalse(face & d_hood)
        self.assertTrue(back <= d_hood)  # a hood covers every head texel outside the face opening

    def test_emblem_follows_its_face(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        for face, rect in (("front", TORSO_FRONT), ("back", TORSO_BACK)):
            for shape in ("sun", "cross", "skull", "rune_circle", "star", "crescent"):
                with self.subTest(face=face, shape=shape):
                    f = {"type": "emblem", "shape": shape, "region": "torso", "face": face, "color": "accent", "size": 0.9}
                    c, _ = paint(self.mesh, self.tpl, appearance(features=[f]))
                    diff = changed(base, c)
                    self.assertTrue(diff)
                    self.assertTrue(inside(diff, rect))

    def test_emblem_is_not_stretched_by_the_region_aspect(self):
        # a cross on the 8x12 torso front must be as wide as it is tall in blocks (12-texel-high torso, 8 wide)
        f = {"type": "emblem", "shape": "cross", "region": "torso", "face": "front", "color": "#ff00ff", "size": 0.8}
        base, _ = paint(self.mesh, self.tpl, appearance())
        c, _ = paint(self.mesh, self.tpl, appearance(features=[f]))
        diff = changed(base, c)
        xs = [i for i, _ in diff]
        ys = [j for _, j in diff]
        self.assertAlmostEqual((max(xs) - min(xs) + 1) / (max(ys) - min(ys) + 1), 1.0, delta=0.25)

    def test_trim_hem_collar_and_belt_bands(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        y0, y1 = 20 * SCALE, 32 * SCALE
        for where, check in (("hem", lambda j: j >= y1 - 0.2 * 12 * SCALE),
                             ("collar", lambda j: j < y0 + 0.2 * 12 * SCALE),
                             ("belt", lambda j: y1 - 0.4 * 12 * SCALE <= j <= y1)):
            with self.subTest(where=where):
                f = {"type": "trim", "region": "torso", "where": where, "color": "#ff00ff", "width": 0.1}
                c, _ = paint(self.mesh, self.tpl, appearance(features=[f]))
                diff = changed(base, c)
                self.assertTrue(diff)
                self.assertTrue(all(check(j) for _, j in diff if 20 * SCALE <= j < 32 * SCALE))

    def test_stripes_orientations(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        front = set(rect_px(TORSO_FRONT))
        v, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "stripes", "region": "torso", "orient": "v", "count": 2, "color": "#ff00ff"}]))
        h, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "stripes", "region": "torso", "orient": "h", "count": 2, "color": "#ff00ff"}]))
        dv = set(changed(base, v)) & front
        dh = set(changed(base, h)) & front
        self.assertTrue(2 <= len({i for i, _ in dv}) <= 6)  # two narrow vertical bands ...
        self.assertEqual({j for _, j in dv}, {j for _, j in front})  # ... running the whole height
        self.assertTrue(2 <= len({j for _, j in dh}) <= 6)  # two narrow horizontal bands ...
        self.assertEqual({i for i, _ in dh}, {i for i, _ in front})  # ... running the whole width

    def test_runes_stamp_glyph_pixels(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        c, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "runes", "color": "#ff00ff", "density": 1.0, "regions": ["torso"]}]))
        diff = changed(base, c)
        self.assertGreater(len(diff), 10)
        self.assertTrue(all(c.get(i, j)[:3] == (255, 0, 255) for i, j in diff))
        none, _ = paint(self.mesh, self.tpl, appearance(features=[{"type": "runes", "color": "#ff00ff", "density": 0.0, "regions": ["torso"]}]))
        self.assertEqual(none.data, base.data)

    def test_cracks_wear_plates_change_pixels_without_touching_alpha(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        for f in ({"type": "cracks", "density": 1.0}, {"type": "wear", "amount": 1.0}, {"type": "plates"}):
            with self.subTest(feature=f["type"]):
                c, _ = paint(self.mesh, self.tpl, appearance(features=[f]))
                self.assertTrue(changed(base, c))
                self.assertTrue(all(c.data[k] == base.data[k] for k in range(3, len(c.data), 4)))

    def test_features_naming_missing_regions_are_skipped(self):
        mesh, tpl = make_mesh(arms=False)
        ref, _ = paint(mesh, tpl, appearance())
        feats = [{"type": "emblem", "shape": "sun", "region": "arm_l"},
                 {"type": "trim", "region": "arms", "where": "cuff", "color": "accent"},
                 {"type": "trim", "region": "legs", "where": "hem"},
                 {"type": "plates", "regions": ["legs", "arm_r"]},
                 {"type": "cracks", "regions": ["legs"]},
                 {"type": "runes", "regions": ["arms"], "density": 1.0},
                 {"type": "stripes", "region": "leg_r"}]
        c, _ = paint(mesh, tpl, appearance(features=feats))
        self.assertEqual(c.data, ref.data)

    def test_arms_and_legs_aliases_apply_to_both_limbs(self):
        base, _ = paint(self.mesh, self.tpl, appearance())
        f = {"type": "trim", "region": "arms", "where": "hem", "color": "#ff00ff", "width": 0.25}
        c, _ = paint(self.mesh, self.tpl, appearance(features=[f]))
        diff = changed(base, c)
        right = [p for p in diff if p in set(rect_px((44, 20, 4, 12)))]
        left = [p for p in diff if p in set(rect_px((36, 52, 4, 12)))]
        self.assertTrue(right and left)

    def test_texture_scale_changes_only_resolution_of_the_same_look(self):
        a, _ = paint(self.mesh, self.tpl, appearance(texture={"scale": 1}, features=[{"type": "eyes", "style": "visor", "color": "eyes"}]))
        b, _ = paint(self.mesh, self.tpl, appearance(texture={"scale": 4}, features=[{"type": "eyes", "style": "visor", "color": "eyes"}]))
        ma = sum(1 for i, j in rect_px(HEAD_FRONT, 1) if is_magenta(a.get(i, j)))
        mb = sum(1 for i, j in rect_px(HEAD_FRONT, 4) if is_magenta(b.get(i, j)))
        self.assertGreater(ma, 0)
        self.assertAlmostEqual(mb / (ma * 16.0), 1.0, delta=0.5)


class FrontSignTests(unittest.TestCase):
    """Eyes belong on the face the template calls the front, whichever way the mesh is oriented."""

    def test_eyes_land_on_the_front_island_for_both_conventions(self):
        for fs in (-1, 1):
            with self.subTest(front_sign=fs):
                mesh, tpl = make_mesh(fs=fs)
                right_sign = -fs
                ap_none = appearance()
                ap_eyes = appearance(features=[{"type": "eyes", "style": "visor", "color": "eyes"},
                                               {"type": "scar", "side": "left"}])
                a, _ = skin.paint(mesh, tpl, ap_none, 3, right_sign=right_sign)
                b, _ = skin.paint(mesh, tpl, ap_eyes, 3, right_sign=right_sign)
                diff = changed(a, b)
                self.assertTrue(diff)
                self.assertTrue(inside(diff, HEAD_FRONT))

    def test_preview_shows_the_face_on_the_front_panel_only(self):
        for fs in (-1, 1):
            with self.subTest(front_sign=fs):
                mesh, tpl = make_mesh(fs=fs)
                c, _ = skin.paint(mesh, tpl, appearance(features=[{"type": "eyes", "style": "visor", "color": "eyes"}]), 3, right_sign=-fs)
                img = preview.render_preview(mesh, tpl, c)
                pw = preview.PANEL_W
                self.assertEqual(img.h, preview.PANEL_H)
                gap = 4
                front = count_magenta(img, 0, pw)
                three_q = count_magenta(img, pw + gap, 2 * pw + gap)
                back = count_magenta(img, 2 * (pw + gap), 3 * pw + 2 * gap)
                self.assertGreater(front, 200)
                self.assertGreater(three_q, 50)
                self.assertEqual(back, 0)

    def test_preview_has_three_views_and_the_texture_sheet(self):
        mesh, tpl = make_mesh()
        c, _ = skin.paint(mesh, tpl, appearance(), 3)
        img = preview.render_preview(mesh, tpl, c)
        k = max(1, preview.PANEL_H // c.h)
        self.assertEqual(img.w, 3 * preview.PANEL_W + c.w * k + 3 * 4)
        # each of the three views draws the model: more than background pixels in the middle of the panel
        for n in range(3):
            x = n * (preview.PANEL_W + 4) + preview.PANEL_W // 2
            col = [img.get(x, y)[:3] for y in range(img.h)]
            self.assertTrue(any(p != preview.BG[:3] for p in col))


# --------------------------------------------------------------------------- custom (box-built) shapes
class CustomBuilder:
    """Boxes in model space (y up, front = -z, character's right = +x) with one atlas rectangle per face,
    plus the synthetic template a shape model carries (part_style, box:<id> regions, overlay_to_base)."""

    FACES = ("front", "back", "left", "right", "top", "bottom")

    def __init__(self, density=32, size=None):
        self.density = density
        self.size = size
        self.boxes = []  # (id, min, size, region, style, density multiplier)

    def add(self, bid, mn, size, region, style=None, dens=1.0):
        self.boxes.append((bid, mn, size, region, style, dens))
        return self

    def _pack(self, size):
        faces = {}
        x = y = row = 0
        gut = 2
        for bid, mn, sz, _r, _s, dens in self.boxes:
            d = self.density * dens
            w, h, dp = (max(2, int(round(c * d))) for c in sz)
            dims = {"front": (w, h), "back": (w, h), "left": (dp, h), "right": (dp, h), "top": (w, dp), "bottom": (w, dp)}
            for f in self.FACES:
                fw, fh = dims[f]
                if x + fw > size:
                    x, y, row = 0, y + row + gut, 0
                if y + fh > size:
                    return None
                faces[(bid, f)] = (x, y, fw, fh)
                x += fw + gut
                row = max(row, fh)
        return faces

    def build(self):
        size = self.size
        faces = None
        for cand in ([size] if size else [128, 256, 512]):
            faces = self._pack(cand)
            if faces:
                size = cand
                break
        assert faces, "atlas overflow"
        pos, nrm, uvs, tris = [], [], [], []
        regions, styles = {}, {}
        for bid, mn, sz, region, style, _d in self.boxes:
            regions[bid] = region
            if style:
                styles[bid] = style
            x0, y0, z0 = mn
            x1, y1, z1 = x0 + sz[0], y0 + sz[1], z0 + sz[2]
            quads = {
                "front": ([(x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0)], (0, 0, -1)),
                "back": ([(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)], (0, 0, 1)),
                "left": ([(x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)], (-1, 0, 0)),
                "right": ([(x1, y1, z1), (x1, y1, z0), (x1, y0, z0), (x1, y0, z1)], (1, 0, 0)),
                "top": ([(x1, y1, z1), (x0, y1, z1), (x0, y1, z0), (x1, y1, z0)], (0, 1, 0)),
                "bottom": ([(x1, y0, z0), (x0, y0, z0), (x0, y0, z1), (x1, y0, z1)], (0, -1, 0)),
            }
            for f in self.FACES:
                corners, normal = quads[f]
                rx, ry, rw, rh = faces[(bid, f)]
                pi, ti = len(pos), len(uvs)
                pos.extend(corners)
                nrm.append(normal)
                ni = len(nrm) - 1
                for (u, v) in ((rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)):
                    uvs.append((u / float(size), v / float(size)))
                for tri in ((0, 1, 2), (0, 2, 3)):
                    tris.append(Tri(bid, tuple(pi + k for k in tri), tuple(ti + k for k in tri), (ni, ni, ni)))
        tpl = {"label": "Custom", "humanoid": True, "part_regions": regions, "part_style": styles, "overlay_parts": [],
               "overlay_to_base": True, "authored_uv": [size, size], "uv_origin": "top", "front_sign": -1, "right_sign": 1,
               "axis_mode": "x,z,-y", "unit_scale": 1.0, "index_order": "pos,uv,nrm", "custom": True}
        self.faces = faces
        return Mesh(pos, nrm, uvs, tris, regions), tpl


GOLD = {"material": "plate_gold", "color": "accent", "accent": "accent"}


def custom_body(density=32, head=0.5, wing=False, size=None):
    b = CustomBuilder(density, size)
    b.add("head", (-head / 2, 1.5, -head / 2), (head, head, head), "head")
    b.add("abdomen", (-0.2, 0.8, -0.1), (0.4, 0.35, 0.2), "torso")
    b.add("chest", (-0.3, 1.15, -0.125), (0.6, 0.4, 0.25), "torso")
    b.add("arm_r", (0.3, 0.8, -0.1), (0.2, 0.7, 0.2), "arm_r")
    b.add("pauldron_r", (0.28, 1.45, -0.15), (0.3, 0.12, 0.3), "arm_r", dict(GOLD))
    b.add("horn", (0.1, 1.5 + head, -0.05), (0.08, 0.3, 0.08), "box:horn", dict(GOLD))
    b.add("cape", (-0.25, 0.9, 0.125), (0.5, 0.6, 0.04), "box:cape", {"material": "cloth", "color": "secondary"})
    if wing:
        b.add("wing_r", (0.5, 1.2, 0.2), (1.6, 0.9, 0.05), "box:wing_r", {"material": "leather", "color": "secondary"})
        b.add("wing_l", (-2.1, 1.2, 0.2), (1.6, 0.9, 0.05), "box:wing_l", {"material": "leather", "color": "secondary"})
    return b


def face_pixels(builder, bid, face):
    x, y, w, h = builder.faces[(bid, face)]
    return [(i, j) for j in range(y, y + h) for i in range(x, x + w)]


def components(points):
    """8-connected components of a set of pixels."""
    left = set(points)
    out = []
    while left:
        seed = left.pop()
        stack, comp = [seed], [seed]
        while stack:
            i, j = stack.pop()
            for n in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1), (i + 1, j + 1), (i - 1, j - 1), (i + 1, j - 1), (i - 1, j + 1)):
                if n in left:
                    left.discard(n)
                    stack.append(n)
                    comp.append(n)
        out.append(comp)
    return out


def changed_faces(builder, a, b):
    """Changed texels that belong to a face rectangle (the atlas gutters only carry dilated copies)."""
    inside_faces = {p for (bid, face) in builder.faces for p in face_pixels(builder, bid, face)}
    return {p for p in changed(a, b) if p in inside_faces}


def custom_appearance(**kw):
    ap = appearance(texture={"scale": 4})  # must be ignored for custom shapes
    ap["regions"] = {"head": {"material": "skin", "color": "skin"}, "torso": {"material": "plate_steel", "color": "primary"},
                     "arms": {"material": "plate_steel", "color": "primary"}}
    ap.update(kw)
    return ap


class CustomShapeTests(unittest.TestCase):
    def setUp(self):
        self.b = custom_body()
        self.mesh, self.tpl = self.b.build()

    def test_atlas_size_is_final_and_texture_scale_is_ignored(self):
        c, info = skin.paint(self.mesh, self.tpl, custom_appearance(), 5)
        self.assertEqual((c.w, c.h), tuple(self.tpl["authored_uv"]))
        self.assertEqual(info["coverage"], 1.0)
        self.assertEqual(skin.coverage_of(c, self.mesh, self.tpl), 1.0)
        for (bid, face) in (("head", "front"), ("cape", "back"), ("horn", "top")):
            self.assertTrue(all(c.get(i, j)[3] == 255 for i, j in face_pixels(self.b, bid, face)))

    def test_oversized_atlas_is_an_error(self):
        tpl = dict(self.tpl, authored_uv=[1024, 1024])
        with self.assertRaises(spec.BossForgeError) as cm:
            skin.paint(self.mesh, tpl, custom_appearance(), 1)
        self.assertEqual(cm.exception.err.code, "texture_too_large")

    def test_part_style_wins_over_region_defaults(self):
        c, _ = skin.paint(self.mesh, self.tpl, custom_appearance(), 5)
        gold, steel = (212, 170, 50), (154, 165, 184)

        def mean(pixels):
            px = [c.get(i, j) for i, j in pixels]
            return tuple(sum(p[k] for p in px) / len(px) for k in range(3))

        pauldron = mean(face_pixels(self.b, "pauldron_r", "front"))
        arm = mean(face_pixels(self.b, "arm_r", "front"))
        cape = mean(face_pixels(self.b, "cape", "back"))
        self.assertLess(dist(pauldron, gold), 90)  # own style, although its region is arm_r (steel)
        self.assertLess(dist(arm, steel), 90)
        self.assertLess(dist(cape, (140, 26, 32)), 90)  # cloth in the secondary colour, region box:cape has no default
        self.assertGreater(dist(pauldron, steel), 60)

    def test_overlay_entries_bake_into_base_texels_without_warning(self):
        entry = {"region": "head", "material": "plate_gold", "color": "accent", "y": [0.8, 1.0]}
        plain, _ = skin.paint(self.mesh, self.tpl, custom_appearance(), 5)
        c, info = skin.paint(self.mesh, self.tpl, custom_appearance(overlay=[entry], overlay_enabled=True), 5)
        self.assertEqual(info["warnings"], [])
        diff = changed_faces(self.b, plain, c)
        self.assertTrue(diff)
        head = {p for f in CustomBuilder.FACES for p in face_pixels(self.b, "head", f)}
        self.assertTrue(diff <= head)  # nothing outside the head region (styled boxes are immune)
        x, y, w, h = self.b.faces[("head", "front")]
        top_rows = {j for _, j in diff if x <= _ < x + w and y <= j < y + h}
        self.assertTrue(top_rows and max(top_rows) <= y + int(0.2 * h) + 1)

    def test_ordinary_regions_span_all_their_boxes(self):
        # torso = abdomen + chest: a hem trim sits at the bottom of the abdomen only, a collar at the top of the chest only
        base, _ = skin.paint(self.mesh, self.tpl, custom_appearance(), 5)
        for where, box, other in (("hem", "abdomen", "chest"), ("collar", "chest", "abdomen")):
            with self.subTest(where=where):
                f = {"type": "trim", "region": "torso", "where": where, "color": "#ff00ff", "width": 0.1}
                c, _ = skin.paint(self.mesh, self.tpl, custom_appearance(features=[f]), 5)
                diff = changed_faces(self.b, base, c)
                own = {p for face in CustomBuilder.FACES for p in face_pixels(self.b, box, face)}
                oth = {p for face in CustomBuilder.FACES for p in face_pixels(self.b, other, face)}
                self.assertTrue(diff & own)
                self.assertFalse(diff & oth)

    def test_default_all_regions_features_skip_private_box_regions(self):
        base, _ = skin.paint(self.mesh, self.tpl, custom_appearance(), 5)
        private = {p for bid in ("horn", "cape") for f in CustomBuilder.FACES for p in face_pixels(self.b, bid, f)}
        for f in ({"type": "cracks", "density": 1.0}, {"type": "wear", "amount": 1.0}, {"type": "plates"},
                  {"type": "runes", "density": 1.0}, {"type": "hair", "style": "long", "color": "hair"},
                  {"type": "beard", "length": "long", "color": "hair"}):
            with self.subTest(feature=f["type"]):
                c, _ = skin.paint(self.mesh, self.tpl, custom_appearance(features=[f]), 5)
                diff = changed_faces(self.b, base, c)
                self.assertTrue(diff)
                self.assertFalse(diff & private)

    def test_a_feature_can_target_a_box_region_by_name(self):
        base, _ = skin.paint(self.mesh, self.tpl, custom_appearance(), 5)
        horn = {p for f in CustomBuilder.FACES for p in face_pixels(self.b, "horn", f)}
        for f in ({"type": "cracks", "regions": ["box:horn"], "density": 1.0, "color": "#ff00ff"},
                  {"type": "stripes", "region": "box:horn", "orient": "h", "count": 3, "color": "#ff00ff"},
                  {"type": "trim", "region": "box:horn", "where": "hem", "color": "#ff00ff", "width": 0.2}):
            with self.subTest(feature=f["type"]):
                c, _ = skin.paint(self.mesh, self.tpl, custom_appearance(features=[f]), 5)
                diff = changed_faces(self.b, base, c)
                self.assertTrue(diff)
                self.assertTrue(diff <= horn)

    def test_eyes_sit_on_the_head_front_for_any_head_size(self):
        for head in (0.35, 0.5, 1.1):
            with self.subTest(head=head):
                b = custom_body(head=head)
                mesh, tpl = b.build()
                base, _ = skin.paint(mesh, tpl, custom_appearance(), 5)
                f = {"type": "eyes", "style": "visor", "color": "eyes"}
                c, _ = skin.paint(mesh, tpl, custom_appearance(features=[f]), 5)
                diff = changed_faces(b, base, c)
                front = set(face_pixels(b, "head", "front"))
                self.assertTrue(diff and diff <= front)
                x, y, w, h = b.faces[("head", "front")]
                cy = sum(j for _, j in diff) / len(diff)
                self.assertAlmostEqual((cy + 0.5 - y) / h, 0.56, delta=0.08)
                xs = [i for i, _ in diff]
                self.assertLess((min(xs) - x) / float(w), 0.2)
                self.assertGreater((max(xs) + 1 - x) / float(w), 0.8)

    def test_scar_picks_the_characters_side(self):
        b = self.b
        base, _ = skin.paint(self.mesh, self.tpl, custom_appearance(), 5)
        x, y, w, h = b.faces[("head", "front")]
        for side, left_of_centre in (("left", False), ("right", True)):
            c, _ = skin.paint(self.mesh, self.tpl, custom_appearance(features=[{"type": "scar", "side": side}]), 5)
            diff = changed(base, c)
            self.assertTrue(diff and set(diff) <= set(face_pixels(b, "head", "front")))
            mean_x = (sum(i for i, _ in diff) / len(diff) + 0.5 - x) / w
            self.assertEqual(mean_x < 0.5, left_of_centre)  # the character's left is the viewer's right

    def test_hair_leaves_styled_head_boxes_alone(self):
        # hair paints the head region but leaves a styled helmet box alone
        b = custom_body()
        b.add("helmet", (-0.28, 1.72, -0.28), (0.56, 0.2, 0.56), "head", dict(GOLD))
        mesh, tpl = b.build()
        base, _ = skin.paint(mesh, tpl, custom_appearance(), 5)
        c, _ = skin.paint(mesh, tpl, custom_appearance(features=[{"type": "hair", "style": "long", "color": "hair"}]), 5)
        diff = changed_faces(b, base, c)
        helmet = {p for f in CustomBuilder.FACES for p in face_pixels(b, "helmet", f)}
        self.assertTrue(diff)
        self.assertFalse(diff & helmet)

    def test_runes_glyphs_keep_their_size_in_blocks_across_densities(self):
        heights = {}
        for density in (32, 64):
            b = CustomBuilder(density).add("chest", (-0.5, 1.0, -0.2), (1.0, 1.2, 0.4), "torso")
            mesh, tpl = b.build()
            base, _ = skin.paint(mesh, tpl, custom_appearance(), 5)
            f = {"type": "runes", "color": "#ff00ff", "density": 1.0, "regions": ["torso"]}
            c, _ = skin.paint(mesh, tpl, custom_appearance(features=[f]), 5)
            diff = changed_faces(b, base, c)
            front = set(face_pixels(b, "chest", "front"))
            comps = [comp for comp in components([p for p in diff if p in front]) if len(comp) > 4]
            self.assertTrue(comps)
            hs = sorted(max(j for _, j in comp) - min(j for _, j in comp) + 1 for comp in comps)
            heights[density] = (hs[len(hs) // 2] / float(density))  # median glyph height in blocks
        # a glyph is 5 pixels of ~1/16 block each, whatever the texel density
        for density, blocks in heights.items():
            self.assertAlmostEqual(blocks, 5 / 16.0, delta=0.07, msg=density)

    def test_seams_and_trim_stay_at_least_one_texel_wide_at_high_density(self):
        b = CustomBuilder(64).add("chest", (-0.3, 1.0, -0.15), (0.6, 0.5, 0.3), "torso")
        mesh, tpl = b.build()
        base, _ = skin.paint(mesh, tpl, custom_appearance(), 5)
        f = {"type": "trim", "region": "torso", "where": "hem", "color": "#ff00ff", "width": 0.03}
        c, _ = skin.paint(mesh, tpl, custom_appearance(features=[f]), 5)
        x, y, w, h = b.faces[("chest", "front")]
        rows = {j for i, j in changed(base, c) if x <= i < x + w and y <= j < y + h}
        # 0.03 of a 0.5 block region = 0.015 block = under one texel at 64/block: still painted, never vanishes
        self.assertGreaterEqual(len(rows), 1)

    def test_deterministic(self):
        ap = custom_appearance(features=[{"type": "wear"}, {"type": "cracks"}, {"type": "plates"}])
        a, _ = skin.paint(self.mesh, self.tpl, ap, 9)
        b2, _ = skin.paint(self.mesh, self.tpl, copy.deepcopy(ap), 9)
        self.assertEqual(pngio.write_png(None, a), pngio.write_png(None, b2))

    def test_stock_templates_do_not_get_overlay_to_base_behaviour(self):
        mesh, tpl = make_mesh(overlay=False)
        entry = {"region": "head", "material": "plate_gold", "color": "accent", "y": [0.0, 1.0]}
        _, info = skin.paint(mesh, tpl, appearance(overlay=[entry]), 5)
        self.assertEqual([w["code"] for w in info["warnings"]], ["overlay_ignored"])


def view_panels(img, panel_w, n=4, gap=4):
    return [(i * (panel_w + gap), (i + 1) * panel_w + i * gap) for i in range(n)]


def panel_width_of(mesh, tpl):
    fs, _ = preview.efmesh.frame(mesh, tpl)
    return preview.view_panel_width(mesh, fs, [yaw for _, yaw in preview.CUSTOM_VIEWS])


def drawn_columns(img, x0, x1):
    return [i for i in range(x0, x1) if any(img.get(i, j)[:3] != preview.BG[:3] for j in range(img.h))]


class CustomPreviewTests(unittest.TestCase):
    def test_custom_preview_has_four_views_and_a_texture_sheet(self):
        b = custom_body()
        mesh, tpl = b.build()
        c, _ = skin.paint(mesh, tpl, custom_appearance(), 5)
        img = preview.render_preview(mesh, tpl, c)
        pw = panel_width_of(mesh, tpl)
        k = max(1, preview.PANEL_H // c.h)
        self.assertEqual(pw, preview.PANEL_W)  # a normal-width model keeps the standard panels
        self.assertEqual(img.w, 4 * pw + c.w * k + 4 * 4)
        self.assertEqual(img.h, max(preview.PANEL_H, c.h * k))
        for x0, x1 in view_panels(img, pw):
            self.assertTrue(drawn_columns(img, x0, x1), (x0, x1))

    def test_wide_shapes_are_not_clipped_in_any_view(self):
        b = custom_body(wing=True)
        mesh, tpl = b.build()
        c, _ = skin.paint(mesh, tpl, custom_appearance(), 5)
        img = preview.render_preview(mesh, tpl, c)
        pw = panel_width_of(mesh, tpl)
        self.assertGreater(pw, preview.PANEL_W)  # wings widen the panels
        self.assertLessEqual(pw, preview.MAX_WIDE_PANEL_W)
        for x0, x1 in view_panels(img, pw):
            edge = [img.get(x0, j)[:3] for j in range(img.h)] + [img.get(x1 - 1, j)[:3] for j in range(img.h)]
            self.assertTrue(all(p == preview.BG[:3] for p in edge), "view at x=%d touches the panel edge" % x0)
        cols = drawn_columns(img, 0, pw)
        self.assertGreater(max(cols) - min(cols), 0.8 * pw)  # the front view is width-limited, not tiny

    def test_side_view_is_narrower_than_the_front_view_at_the_same_scale(self):
        b = custom_body()
        mesh, tpl = b.build()
        c, _ = skin.paint(mesh, tpl, custom_appearance(features=[{"type": "eyes", "style": "visor", "color": "eyes"}]), 5)
        img = preview.render_preview(mesh, tpl, c)
        pw = panel_width_of(mesh, tpl)
        panels = view_panels(img, pw)
        self.assertGreater(count_magenta(img, *panels[0]), 20)  # front panel shows the face
        self.assertEqual(count_magenta(img, *panels[3]), 0)  # back panel does not

        def span(n):
            cols = drawn_columns(img, *panels[n])
            return max(cols) - min(cols)

        self.assertLess(span(2), span(0))
        # one shared scale: the model is equally tall in the front and side views
        def height(n):
            x0, x1 = panels[n]
            rows = [j for j in range(img.h) if any(img.get(i, j)[:3] != preview.BG[:3] for i in range(x0, x1))]
            return max(rows) - min(rows)

        self.assertAlmostEqual(height(2) / float(height(0)), 1.0, delta=0.1)


# --------------------------------------------------------------------------- generate() with the real shape builder
def _shape_module():
    try:
        from bossforge_core import shape
        return shape
    except ImportError:
        return None


KING_SHAPE = {"proportions": {"head": 1.3, "shoulders": 1.4},
              "parts": [{"id": "horn", "bone": "head", "min": [0.12, 2.2, -0.05], "size": [0.16, 0.5, 0.16], "taper": [0.15, 0.15],
                         "rotate": [0, 0, -25], "mirror": True, "material": "bone", "color": "#d9c7a3"}]}


@unittest.skipUnless(_shape_module(), "shape module not available")
class GenerateCustomShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = spec.load(_path.EXAMPLE)
        raw["shape"] = copy.deepcopy(KING_SHAPE)
        cls.n = spec.normalize(raw)
        cls.tmp = tempfile.TemporaryDirectory()
        # no EF mesh may be needed: an unusable cache and offline mode must still work
        cls.ctx = spec.make_context(cls.tmp.name, "roundtable", "com.bossforge.roundtable", Path(cls.tmp.name) / "nocache", offline=True)
        cls.files, cls.lang, cls.report = skin.generate_with_report(cls.n, cls.ctx)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_report_and_files_for_a_custom_shape(self):
        r = self.report
        self.assertTrue(r["custom"])
        self.assertEqual(r["template"], "biped")
        self.assertEqual(self.lang, {})
        kinds = {f.kind: f for f in self.files}
        self.assertEqual(kinds["texture"].relpath, "src/main/resources/assets/roundtable/textures/entity/king_arthur.png")
        self.assertEqual(kinds["preview"].relpath, "bossforge/previews/king_arthur_preview.png")
        w, h = pngio.png_size(kinds["texture"].data)
        self.assertEqual(r["atlas"], [w, h])
        self.assertEqual((r["texture"]["width"], r["texture"]["height"]), (w, h))
        self.assertGreaterEqual(r["texture"]["coverage"], 0.90)
        self.assertTrue(all(set(w) == {"path", "code", "message"} for w in r["warnings"]))
        self.assertIsInstance(r["shape_warnings"], list)
        # the texture scale of the appearance is irrelevant for custom shapes: the atlas size is final
        n4 = copy.deepcopy(self.n)
        n4["appearance"]["texture"]["scale"] = 4
        _, _, r4 = skin.generate_with_report(n4, self.ctx)
        self.assertEqual(r4["atlas"], r["atlas"])

    def test_deterministic(self):
        again, _ = skin.generate(self.n, self.ctx)
        self.assertEqual([f.data for f in again], [f.data for f in self.files])

    def test_coverage_of_matches_for_the_written_texture(self):
        model = _shape_module().build_model(self.n, self.ctx)
        tex = pngio.decode_png([f for f in self.files if f.kind == "texture"][0].data)
        self.assertGreaterEqual(skin.coverage_of(tex, model.mesh, model.tpl), 0.90)

    def test_face_is_on_the_front_of_the_head_box_and_the_horns_stay_clean(self):
        model = _shape_module().build_model(self.n, self.ctx)
        ap = copy.deepcopy(self.n["appearance"])
        ap["features"] = []
        base, _ = skin.paint(model.mesh, model.tpl, ap, 5)
        ap["features"] = [{"type": "eyes", "style": "visor", "color": "eyes"}, {"type": "wear", "amount": 1.0}, {"type": "cracks", "density": 1.0}]
        c, _ = skin.paint(model.mesh, model.tpl, ap, 5)
        head_front = None
        horn_texels = set()
        cover = skin._rasterize(model.mesh, model.tpl, c.w, c.h)
        for (x, y), (ti, _a, _b, _c) in cover.items():
            tri = model.mesh.tris[ti]
            if tri.part == "head" and model.mesh.normals[tri.n[0]][2] < -0.9:
                head_front = (head_front or set()) | {(x, y)}
            if tri.part.startswith("horn"):
                horn_texels.add((x, y))
        diff = {p for p in changed(base, c) if p in cover}
        eyes_on_head = diff & head_front
        self.assertTrue(eyes_on_head)
        self.assertFalse(diff & horn_texels)  # horns carry their own style: default-all features leave them alone


# --------------------------------------------------------------------------- real Epic Fight meshes
def _cached(name):
    return (CACHE / "ef" / REF[:8] / (name + ".json")).is_file()


@unittest.skipUnless(_cached("biped") and _cached("iron_golem"), "EF meshes not cached (run fetch-ef-assets)")
class RealMeshSkinTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tpl = spec.data("templates")["biped"]
        path = efassets.mesh_file("biped", CACHE, REF, offline=True)
        cls.mesh = efmesh.load_mesh(path, cls.tpl)

    def test_king_arthur_generates_texture_and_preview(self):
        n = spec.normalize(spec.load(_path.EXAMPLE))
        with tempfile.TemporaryDirectory() as tmp:
            ctx = spec.make_context(tmp, "roundtable", "com.bossforge.roundtable", CACHE, offline=True)
            files, lang, report = skin.generate_with_report(n, ctx)
        self.assertEqual(lang, {})
        kinds = {f.kind: f for f in files}
        self.assertEqual(kinds["texture"].relpath, "src/main/resources/assets/roundtable/textures/entity/king_arthur.png")
        self.assertEqual(kinds["preview"].relpath, "bossforge/previews/king_arthur_preview.png")
        self.assertEqual(pngio.png_size(kinds["texture"].data), (128, 128))
        self.assertGreaterEqual(report["texture"]["coverage"], 0.90)
        self.assertEqual((report["texture"]["width"], report["texture"]["height"]), (128, 128))
        self.assertEqual(report["template"], "biped")
        pw, ph = pngio.png_size(kinds["preview"].data)
        self.assertEqual(ph, preview.PANEL_H)
        self.assertGreater(pw, 3 * preview.PANEL_W)
        again, _ = skin.generate(n, ctx)
        self.assertEqual([f.data for f in again], [f.data for f in files])

    def test_real_mesh_face_features_land_on_the_front(self):
        ap = appearance(regions={"head": {"material": "skin", "color": "skin"}, "torso": {"material": "plate_steel", "color": "primary"},
                                 "arms": {"material": "plate_steel", "color": "primary"}, "legs": {"material": "cloth", "color": "secondary"}})
        a, _ = skin.paint(self.mesh, self.tpl, ap, 5)
        eyes = {"type": "eyes", "style": "visor", "color": "eyes"}
        b, _ = skin.paint(self.mesh, self.tpl, dict(ap, features=[eyes]), 5)
        diff = changed(a, b)
        self.assertTrue(diff)
        self.assertTrue(inside(diff, HEAD_FRONT))
        centre = (HEAD_FRONT[0] + 4) * SCALE
        c, _ = skin.paint(self.mesh, self.tpl, dict(ap, features=[{"type": "scar", "side": "left"}]), 5)
        d = changed(a, c)
        self.assertTrue(d and inside(d, HEAD_FRONT))
        self.assertGreater(sum(i for i, _ in d) / len(d), centre)
        img = preview.render_preview(self.mesh, self.tpl, b)
        pw = preview.PANEL_W
        self.assertGreater(count_magenta(img, 0, pw), 100)
        self.assertEqual(count_magenta(img, 2 * (pw + 4), 3 * pw + 8), 0)

    def test_golem_generates_with_overlay_warning(self):
        n = spec.normalize({
            "schema": spec.SCHEMA, "id": "stone_warden", "mod_id": "roundtable", "display_name": "Stone Warden", "tier": "mini",
            "lore": {"summary": "s", "backstory": "b", "description": "d"}, "size": {"height": 2.6, "width": 1.6},
            "appearance": {"archetype": "golem", "overlay": [{"region": "torso", "material": "stone", "y": [0.0, 1.0]}]}})
        with tempfile.TemporaryDirectory() as tmp:
            ctx = spec.make_context(tmp, "roundtable", "com.bossforge.roundtable", CACHE, offline=True)
            files, _lang, report = skin.generate_with_report(n, ctx)
        self.assertEqual(report["template"], "iron_golem")
        self.assertEqual((report["texture"]["width"], report["texture"]["height"]), (256, 256))
        self.assertGreaterEqual(report["texture"]["coverage"], 0.90)
        self.assertIn("overlay_ignored", [w["code"] for w in report["warnings"]])


if __name__ == "__main__":
    unittest.main()
