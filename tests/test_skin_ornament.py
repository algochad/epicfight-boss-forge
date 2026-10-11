"""Skin painter: filigree ornaments, gradients, the anime face (eyes, mouth, blush, bangs), glowing emblems and
`box:<glob>` targeting. Synthetic custom-shape meshes (see test_skin.CustomBuilder) keep these fast."""
import _path  # noqa
import copy
import json
import subprocess
import sys
import unittest

from bossforge_core import colors, features, ornament, preview, shape, skin, spec
from bossforge_core.materials import MATERIAL_FUNCS, Texel, shade_texel
from test_skin import CustomBuilder, appearance, changed_faces, custom_appearance, custom_body, dist, face_pixels

MAGENTA = (255, 0, 255)
MAG_HEX = "#ff00ff"
CREAM_HEX = "#f2ead3"


def paint(builder_mesh, features_, seed=5, **kw):
    mesh, tpl = builder_mesh
    c, _info = skin.paint(mesh, tpl, custom_appearance(features=features_, **kw), seed)
    return c


def base_of(builder_mesh, seed=5, **kw):
    return paint(builder_mesh, [], seed, **kw)


def faces_of(builder, bids, faces):
    return {p for bid in bids for f in faces for p in face_pixels(builder, bid, f)}


def painted(canvas_a, canvas_b, builder):
    """Texels inside face rectangles whose colour differs between the two canvases."""
    return changed_faces(builder, canvas_a, canvas_b)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads(_path.SHIPPED_EXAMPLE.read_text(encoding="utf-8"))
        self.raw["appearance"]["features"] = []

    def check(self, *feats):
        raw = copy.deepcopy(self.raw)
        raw["appearance"]["features"] = list(feats)
        _n, errs, warns = spec.check(raw)
        return errs, warns

    def codes(self, errs):
        return {(e.path, e.code) for e in errs}

    def test_new_features_are_listed(self):
        for name in ("filigree", "gradient", "mouth", "blush", "bangs"):
            self.assertIn(name, spec.FEATURE_TYPES)
            self.assertIn(name, features.FEATURES)
        out = subprocess.run([sys.executable, str(_path.SCRIPTS / "bossforge.py"), "list", "features"],
                             capture_output=True, text=True, check=True).stdout
        listing = json.loads(out)
        self.assertTrue({"filigree", "gradient", "mouth", "blush", "bangs"} <= set(listing["features"]))
        self.assertIn("anime", listing["enums"]["eyes"]["style"])
        self.assertIn("winged_cross", listing["enums"]["emblem"]["shape"])
        self.assertIn("meander", listing["enums"]["filigree"]["pattern"])

    def test_spec_and_painter_agree_on_patterns_materials_and_features(self):
        self.assertEqual(tuple(spec.FEATURE_ENUMS["filigree"]["pattern"]), ornament.PATTERNS)
        self.assertEqual(set(spec.MATERIALS), set(MATERIAL_FUNCS))
        self.assertEqual(set(spec.FEATURE_TYPES), set(features.FEATURES))
        for name in ("repaint", "sparkle", "latin_cross", "silk_feather", "lace", "diamond_chain", "crest", "collar_wing", "line"):
            self.assertTrue(name in spec.FEATURE_TYPES or name in spec.MATERIALS or name in ornament.PATTERNS
                            or name in spec.FEATURE_ENUMS["emblem"]["shape"], name)

    def test_repaint_needs_a_material_and_a_target(self):
        errs, _ = self.check({"type": "repaint", "regions": ["box:horn"]},
                             {"type": "repaint", "material": "granite", "regions": ["torso"]},
                             {"type": "repaint", "material": "silk"},
                             {"type": "repaint", "material": "lace", "regions": ["part:fist_*", "torso"]})
        got = self.codes(errs)
        base = "/appearance/features/"
        self.assertIn((base + "0/material", "unknown_material"), got)
        self.assertIn((base + "1/material", "unknown_material"), got)
        self.assertIn((base + "2", "required"), got)
        self.assertFalse([e for e in errs if e.path.startswith(base + "3")])

    def test_part_targets_validate_and_unknown_parts_warn(self):
        n, _e, _w = spec.check(copy.deepcopy(self.raw))
        ids = [b["id"] for b in shape.build_model(n).boxes]
        target = [i for i in ids if i.startswith("arm_")][0]
        errs, warns = self.check({"type": "repaint", "material": "silk", "regions": ["part:" + target]},
                                 {"type": "repaint", "material": "silk", "regions": ["part:no_such_*"]},
                                 {"type": "trim", "regions": ["part:" + target[:4] + "*"], "at": 0.3, "color": "accent"})
        self.assertEqual(errs, [])
        self.assertEqual([w.path for w in warns if w.code == "unknown_box"], ["/appearance/features/1/regions"])
        errs, _ = self.check({"type": "trim", "at": 1.5}, {"type": "sparkle", "density": 0.9, "strength": -1})
        self.assertEqual(self.codes(errs), {("/appearance/features/0/at", "out_of_range"),
                                            ("/appearance/features/1/density", "out_of_range"),
                                            ("/appearance/features/1/strength", "out_of_range")})

    def test_a_valid_set_of_new_features_has_no_errors(self):
        feats = [
            {"type": "filigree", "region": "torso", "pattern": "wing", "where": "panel", "fill": CREAM_HEX, "outline": "secondary",
             "color": "accent", "faces": ["front", "back"], "sides": ["top", "left"], "cell": 2, "width": 0.4},
            {"type": "gradient", "regions": ["legs"], "from": "#3f68a8", "lo": 0.0, "hi": 0.3},
            {"type": "eyes", "style": "anime", "color": "#5a3a22"},
            {"type": "mouth", "style": "smile"}, {"type": "blush", "amount": 0.4}, {"type": "bangs", "part": "left"},
            {"type": "emblem", "shape": "winged_cross", "glow": True, "wing_color": "#e4eeff", "halo": 4},
            {"type": "hair", "style": "long", "material": "hair_soft"},
        ]
        errs, _warns = self.check(*feats)
        self.assertEqual([e.to_dict() for e in errs], [])

    def test_unknown_values_are_reported_with_their_path(self):
        errs, _ = self.check({"type": "filigree", "pattern": "spiral"},
                             {"type": "filigree", "where": "everywhere"},
                             {"type": "filigree", "size": "huge", "anchor": "left"},
                             {"type": "eyes", "style": "round"},
                             {"type": "emblem", "shape": "heart"},
                             {"type": "mouth", "style": "grin"},
                             {"type": "bangs", "part": "middle"},
                             {"type": "hair", "material": "plate_steel"})
        got = self.codes(errs)
        base = "/appearance/features/"
        for i, key in enumerate(("pattern", "where", "size", "style", "shape", "style", "part", "material")):
            if i == 2:
                self.assertIn((base + "2/size", "unknown_value"), got)
                self.assertIn((base + "2/anchor", "unknown_value"), got)
            else:
                self.assertIn(("%s%d/%s" % (base, i, key), "unknown_value"), got)
        msg = [e.message for e in errs if e.path.endswith("/0/pattern")][0]
        self.assertIn("meander", msg)  # the allowed values are listed

    def test_numbers_faces_sides_and_colors_are_checked(self):
        errs, _ = self.check({"type": "filigree", "cell": 99, "width": 5, "texture": "lots"},
                             {"type": "filigree", "faces": ["top"]},
                             {"type": "filigree", "faces": "front", "sides": ["middle"]},
                             {"type": "gradient", "from": "#12", "lo": -1},
                             {"type": "filigree", "fill": "nope", "outline": 3},
                             {"type": "bangs", "depth": 0.9})
        got = self.codes(errs)
        base = "/appearance/features/"
        self.assertIn((base + "0/cell", "out_of_range"), got)
        self.assertIn((base + "0/width", "out_of_range"), got)
        self.assertIn((base + "0/texture", "bad_type"), got)
        self.assertIn((base + "1/faces", "unknown_value"), got)  # a filigree needs a vertical face
        self.assertIn((base + "2/faces", "bad_type"), got)
        self.assertIn((base + "2/sides", "unknown_value"), got)
        self.assertIn((base + "3/from", "bad_color"), got)
        self.assertIn((base + "3/lo", "out_of_range"), got)
        self.assertIn((base + "4/fill", "bad_color"), got)
        self.assertIn((base + "4/outline", "bad_color"), got)
        self.assertIn((base + "5/depth", "out_of_range"), got)

    def test_box_regions_validate_and_unknown_boxes_warn(self):
        n, _e, _w = spec.check(copy.deepcopy(self.raw))
        ids = [b["id"] for b in shape.build_model(n).boxes]
        target = [i for i in ids if i not in ("head", "chest", "abdomen", "pelvis")][0]
        errs, warns = self.check({"type": "filigree", "region": "box:" + target},
                                 {"type": "gradient", "regions": ["box:" + target[:3] + "*", "torso"], "from": "#112233"})
        self.assertEqual([e.to_dict() for e in errs], [])
        self.assertEqual([w for w in warns if w.code == "unknown_box"], [])
        errs, warns = self.check({"type": "stripes", "region": "box:no_such_box_*"})
        self.assertEqual(errs, [])
        self.assertEqual([w.path for w in warns if w.code == "unknown_box"], ["/appearance/features/0/region"])
        errs, _ = self.check({"type": "filigree", "region": "boxes:x"}, {"type": "filigree", "regions": ["sleeve"]})
        self.assertEqual(self.codes(errs), {("/appearance/features/0/region", "unknown_region"),
                                            ("/appearance/features/1/regions", "unknown_region")})


class OrnamentEngineTests(unittest.TestCase):
    def test_every_strip_is_rectangular_and_every_pattern_draws_something(self):
        w, h = 22, 18
        occ = {(i, j) for i in range(w) for j in range(h)}
        for pattern, variants in ornament.STRIPS.items():
            for rows_n, rows in variants.items():
                with self.subTest(pattern=pattern, rows=rows_n):
                    self.assertLessEqual(len(rows), rows_n)
                    self.assertEqual(len({len(r) for r in rows}), 1)
                    self.assertTrue(all(set(r) <= {"#", "."} for r in rows))
                    cells = ornament.border_cells(occ, w, h, rows)
                    self.assertTrue(cells)
                    # mirror-symmetric about the vertical and the horizontal centre line
                    self.assertEqual(cells, {(w - 1 - i, j) for i, j in cells})
                    self.assertEqual(cells, {(i, h - 1 - j) for i, j in cells})
                    # nothing beyond the band depth
                    self.assertTrue(all(min(i, w - 1 - i, j, h - 1 - j) < len(rows) for i, j in cells))

    def test_motifs_are_mirror_symmetric_and_stay_inside_their_box(self):
        for name, fn in ornament.MOTIFS.items():
            for mw, mh in ((9, 9), (12, 8), (7, 7), (15, 11)):
                with self.subTest(motif=name, size=(mw, mh)):
                    cells = fn(mw, mh, "auto")
                    self.assertTrue(cells)
                    self.assertTrue(all(0 <= i < mw and 0 <= j < mh for i, j in cells))
                    self.assertEqual(cells, {(mw - 1 - i, j) for i, j in cells})

    def test_auto_cell_keeps_the_ornament_size_in_blocks(self):
        self.assertEqual(ornament.auto_cell(1.0 / 16), 1)
        self.assertEqual(ornament.auto_cell(1.0 / 32), 1)
        self.assertEqual(ornament.auto_cell(1.0 / 48), 1)
        self.assertEqual(ornament.auto_cell(1.0 / 64), 2)
        self.assertEqual(ornament.auto_cell(1.0 / 128), 4)

    def test_outline_follows_an_irregular_shape_and_edges_can_be_limited(self):
        # an L-shaped cell set: the notch gets its own outline
        occ = {(i, j) for i in range(12) for j in range(12) if not (i >= 6 and j < 6)}
        rows = ornament.STRIPS["key"][3]
        every = ornament.border_cells(occ, 12, 12, rows)
        self.assertTrue(any(i >= 6 and j in (5, 6) for i, j in every))  # along the notch edge
        top_only = ornament.border_cells({(i, j) for i in range(12) for j in range(12)}, 12, 12, rows, edges=["top"])
        self.assertTrue(top_only and all(j < 3 for _i, j in top_only))

    def test_every_pattern_draws_in_every_mode(self):
        w, h = 20, 16
        occ = {(i, j) for i in range(w) for j in range(h)}
        panel = ornament.Panel(w, h, 1, {}, occ)
        for pattern in ornament.PATTERNS:
            with self.subTest(pattern=pattern):
                rows = ornament.strip_rows(pattern, "auto", w, h)
                self.assertTrue(ornament.border_cells(occ, w, h, rows))
                r0, r1 = ornament.band_rows(panel, 0.2, 0.5)
                self.assertTrue(ornament.band_cells(occ, w, h, rows, r0, r1))
                fill, outline, motif = ornament.panel_layers(panel, pattern, 0.5, 0.5, 0.8, 0.8, True, True, 0, 1.0, "center", "auto")
                self.assertTrue(fill and outline and motif)
                self.assertTrue(outline <= fill and motif <= fill)

    def test_squeeze_removes_only_narrow_seams(self):
        self.assertEqual(ornament._squeeze({0, 1, 4, 5}, 2), {0: 0, 1: 1, 4: 2, 5: 3})
        self.assertEqual(ornament._squeeze({0, 1, 6, 7}, 2), {0: 0, 1: 1, 6: 6, 7: 7})


class FiligreeTests(unittest.TestCase):
    def setUp(self):
        self.b = custom_body(density=32, wing=True)
        self.model = self.b.build()
        self.base = base_of(self.model)

    def test_ornament_only_touches_the_targeted_faces(self):
        for faces in (["front"], ["left", "right"]):
            for where in ("border", "band", "panel"):
                with self.subTest(faces=faces, where=where):
                    f = {"type": "filigree", "region": "torso", "faces": faces, "where": where, "pattern": "key",
                         "color": MAG_HEX, "lo": 0.2, "hi": 0.8, "width": 0.8, "height": 0.8}
                    diff = painted(self.base, paint(self.model, [f]), self.b)
                    self.assertTrue(diff)
                    self.assertTrue(diff <= faces_of(self.b, ("abdomen", "chest"), faces))

    def test_box_glob_targets_only_the_matching_boxes(self):
        wings = faces_of(self.b, ("wing_r", "wing_l"), CustomBuilder.FACES)
        for region in ("box:wing_*", "box:wing_?"):
            f = {"type": "filigree", "region": region, "face": "back", "color": MAG_HEX, "size": "small"}
            diff = painted(self.base, paint(self.model, [f]), self.b)
            self.assertTrue(diff and diff <= wings)
            self.assertTrue(diff & faces_of(self.b, ("wing_r",), ["back"]))
            self.assertTrue(diff & faces_of(self.b, ("wing_l",), ["back"]))
        f = {"type": "filigree", "region": "box:wing_r", "face": "back", "color": MAG_HEX}
        diff = painted(self.base, paint(self.model, [f]), self.b)
        self.assertTrue(diff and diff <= faces_of(self.b, ("wing_r",), ["back"]))
        f = {"type": "filigree", "region": "box:nothing_*", "color": MAG_HEX}
        self.assertEqual(painted(self.base, paint(self.model, [f]), self.b), set())

    def test_strokes_are_flat_colour_and_deterministic(self):
        f = {"type": "filigree", "region": "torso", "pattern": "meander", "color": MAG_HEX}
        a, b = paint(self.model, [f], seed=5), paint(self.model, [f], seed=5)
        self.assertEqual(bytes(a.data), bytes(b.data))
        other = paint(self.model, [f], seed=9)
        diff = painted(self.base, a, self.b)
        self.assertTrue(diff)
        self.assertEqual({a.get(i, j)[:3] for i, j in diff}, {MAGENTA})  # no noise, no shading
        # the ornament does not depend on the seed (only the cloth under it does)
        self.assertEqual(painted(base_of(self.model, seed=9), other, self.b), diff)

    def test_panel_fill_outline_and_motif_use_their_own_colours(self):
        f = {"type": "filigree", "region": "torso", "where": "panel", "pattern": "lozenge", "width": 0.7, "height": 0.8,
             "fill": "#00ff00", "outline": "#0000ff", "color": MAG_HEX, "texture": 0.0, "size": "small"}
        c = paint(self.model, [f])
        cols = {c.get(i, j)[:3] for i, j in painted(self.base, c, self.b)}
        self.assertEqual(cols, {(0, 255, 0), (0, 0, 255), MAGENTA})
        # without fill and outline only the motif is drawn
        f2 = dict(f)
        del f2["fill"], f2["outline"]
        c2 = paint(self.model, [f2])
        self.assertEqual({c2.get(i, j)[:3] for i, j in painted(self.base, c2, self.b)}, {MAGENTA})

    def test_a_centred_panel_is_mirror_symmetric(self):
        f = {"type": "filigree", "region": "torso", "face": "front", "where": "panel", "pattern": "lozenge", "color": MAG_HEX,
             "width": 0.8, "height": 0.8, "cell": 1}
        diff = painted(self.base, paint(self.model, [f]), self.b)
        for bid in ("chest", "abdomen"):
            x, y, w, h = self.b.faces[(bid, "front")]
            mask = {(i - x, j - y) for i, j in diff if x <= i < x + w and y <= j < y + h}
            if mask:
                self.assertEqual(mask, {(w - 1 - i, j) for i, j in mask}, bid)

    def test_two_boxes_a_hair_apart_form_one_symmetric_panel(self):
        b = CustomBuilder(density=32)
        style = {"material": "cloth", "color": "primary"}
        b.add("torso", (-0.2, 0.8, -0.1), (0.4, 0.6, 0.2), "torso")
        b.add("half_r", (0.005, 0.1, -0.2), (0.3, 0.7, 0.04), "box:half_r", dict(style))
        b.add("half_l", (-0.305, 0.1, -0.2), (0.3, 0.7, 0.04), "box:half_l", dict(style))
        mesh_tpl = b.build()
        base = base_of(mesh_tpl)
        f = {"type": "filigree", "regions": ["box:half_*"], "face": "front", "where": "panel", "pattern": "lozenge",
             "color": MAG_HEX, "width": 0.9, "height": 0.9, "cell": 1}
        diff = painted(base, paint(mesh_tpl, [f]), b)
        rects = {}
        for bid in ("half_r", "half_l"):
            x, y, w, h = b.faces[(bid, "front")]
            rects[bid] = ({(i - x, j - y) for i, j in diff if x <= i < x + w and y <= j < y + h}, w)
        self.assertTrue(rects["half_r"][0] and rects["half_l"][0])
        w = rects["half_r"][1]
        # the two halves are mirror images of each other
        self.assertEqual(rects["half_r"][0], {(w - 1 - i, j) for i, j in rects["half_l"][0]})
        # and the motif is not split by the seam: it reaches the inner edge of both halves
        self.assertEqual(max(i for i, _j in rects["half_r"][0]), w - 1)
        self.assertEqual(min(i for i, _j in rects["half_l"][0]), 0)

    def test_the_same_look_at_other_densities(self):
        for density in (32, 48, 64):
            with self.subTest(density=density):
                b = custom_body(density=density, size=512)
                mesh_tpl = b.build()
                f = {"type": "filigree", "region": "torso", "pattern": "key", "color": MAG_HEX, "sides": ["bottom"]}
                diff = painted(base_of(mesh_tpl), paint(mesh_tpl, [f]), b)
                x, y, w, h = b.faces[("abdomen", "front")]
                rows = sorted({j for i, j in diff if x <= i < x + w and y <= j < y + h})
                self.assertTrue(rows)
                depth_blocks = (rows[-1] - rows[0] + 1) / float(density)
                self.assertLess(abs(depth_blocks - 0.09), 0.05)  # about three cells (3 x 0.03 block), whatever the density

    def test_stock_mesh_regions_work_too(self):
        from test_skin import HAT_FACES, HEAD_FACES, TORSO_FACES, TORSO_FRONT, make_mesh, rect_px
        mesh, tpl = make_mesh()
        base, _ = skin.paint(mesh, tpl, appearance(), 3)
        f = {"type": "filigree", "region": "torso", "where": "border", "pattern": "lozenge", "color": MAG_HEX}
        c, _ = skin.paint(mesh, tpl, appearance(features=[f]), 3)
        faces = {p for rect in HEAD_FACES + HAT_FACES + TORSO_FACES for p in rect_px(rect)}
        diff = {(i, j) for (i, j) in faces if c.get(i, j) != base.get(i, j)}  # (the gutters only carry dilated copies)
        self.assertTrue(diff)
        self.assertTrue(diff <= set(rect_px(TORSO_FRONT)))


class GradientTests(unittest.TestCase):
    def setUp(self):
        self.b = custom_body(density=32)
        self.model = self.b.build()
        self.base = base_of(self.model)

    def test_blend_goes_from_the_colour_at_the_bottom_to_the_base_at_the_top(self):
        f = {"type": "gradient", "region": "arm_r", "from": "#ff0000", "lo": 0.0, "hi": 0.5}
        c = paint(self.model, [f])
        x, y, w, h = self.b.faces[("arm_r", "front")]
        rows = list(range(y, y + h))

        def row_mean(canvas, j):
            px = [canvas.get(i, j) for i in range(x, x + w)]
            return tuple(sum(p[k] for p in px) / len(px) for k in range(3))

        bottom, top = row_mean(c, rows[-1]), row_mean(c, rows[0])
        self.assertLess(dist(bottom, (255, 0, 0)), 90)
        self.assertGreater(dist(bottom, row_mean(self.base, rows[-1])), 60)
        # above `hi` the base is untouched
        for j in rows[: len(rows) // 2 - 3]:
            self.assertTrue(all(c.get(i, j) == self.base.get(i, j) for i in range(x, x + w)))
        self.assertLess(dist(top, row_mean(self.base, rows[0])), 1)

    def test_the_base_texture_variation_survives(self):
        f = {"type": "gradient", "region": "arm_r", "from": "#808080", "to": "#808080", "texture": 1.0}
        c = paint(self.model, [f])
        x, y, w, h = self.b.faces[("arm_r", "front")]
        lums = {colors.luminance(c.get(i, j)[:3]) for j in range(y, y + h) for i in range(x, x + w)}
        self.assertGreater(len(lums), 5)  # steel noise is carried over, not a flat fill
        flat = paint(self.model, [dict(f, texture=0.0)])
        self.assertEqual({flat.get(i, j)[:3] for j in range(y, y + h) for i in range(x, x + w)}, {(128, 128, 128)})

    def test_only_targeted_texels_change_and_faces_can_be_chosen(self):
        f = {"type": "gradient", "regions": ["arm_r"], "faces": ["front"], "from": MAG_HEX}
        diff = painted(self.base, paint(self.model, [f]), self.b)
        self.assertTrue(diff and diff <= faces_of(self.b, ("arm_r", "pauldron_r"), ["front"]))  # the pauldron box is region arm_r too
        # no region: every ordinary body region, never the private boxes
        f = {"type": "gradient", "from": MAG_HEX, "hi": 1.0}
        diff = painted(self.base, paint(self.model, [f]), self.b)
        private = faces_of(self.b, ("horn", "cape"), CustomBuilder.FACES)
        self.assertTrue(diff and not diff & private)

    def test_without_any_colour_nothing_happens(self):
        self.assertEqual(painted(self.base, paint(self.model, [{"type": "gradient", "region": "torso"}]), self.b), set())


class FaceTests(unittest.TestCase):
    def setUp(self):
        self.b = custom_body(density=48, size=512)
        self.model = self.b.build()
        self.base = base_of(self.model)
        self.front = set(face_pixels(self.b, "head", "front"))
        self.x, self.y, self.w, self.h = self.b.faces[("head", "front")]

    def fu_fv(self, px):
        return (px[0] - self.x + 0.5) / self.w, (px[1] - self.y + 0.5) / self.h

    def test_face_features_stay_on_the_head_front(self):
        for f in ({"type": "eyes", "style": "anime", "color": MAG_HEX}, {"type": "mouth"}, {"type": "blush"},
                  {"type": "bangs", "color": MAG_HEX}):
            with self.subTest(feature=f["type"]):
                diff = painted(self.base, paint(self.model, [f]), self.b)
                self.assertTrue(diff)
                self.assertTrue(diff <= self.front)

    def test_anime_eyes_are_a_symmetric_pair_with_a_lash_a_highlight_and_no_wide_sclera(self):
        c = paint(self.model, [{"type": "eyes", "style": "anime", "color": MAG_HEX}])
        diff = painted(self.base, c, self.b)
        mask = {(i - self.x, j - self.y) for i, j in diff}
        self.assertEqual({(i, j) for i, j in mask if i < self.w // 2}, {(self.w - 1 - i, j) for i, j in mask if i >= self.w // 2})
        cols = [c.get(i, j)[:3] for i, j in diff]
        self.assertTrue(any(col == features.HIGHLIGHT for col in cols))
        self.assertTrue(any(col == features.LASH for col in cols))
        iris = [col for col in cols if col[0] > 60 and col[2] > 60 and col[1] < col[0] * 0.8]
        white = [col for col in cols if col == features.SCLERA]
        self.assertGreater(len(iris), len(white))  # the iris dominates the eye
        rows = sorted({j for _i, j in mask})
        self.assertLessEqual(rows[-1] - rows[0] + 1, 8)  # a few rows, not a mask
        _fu, fv = self.fu_fv((self.x, self.y + rows[0]))
        self.assertAlmostEqual(fv, 0.5, delta=0.1)

    def test_mouth_is_small_and_low_and_blush_sits_under_the_eyes(self):
        diff = painted(self.base, paint(self.model, [{"type": "mouth", "nose": False}]), self.b)
        fus = [self.fu_fv(p)[0] for p in diff]
        fvs = [self.fu_fv(p)[1] for p in diff]
        self.assertTrue(0.35 < min(fus) and max(fus) < 0.65)
        self.assertTrue(0.7 < min(fvs) and max(fvs) < 0.9)
        diff = painted(self.base, paint(self.model, [{"type": "blush"}]), self.b)
        pts = [self.fu_fv(p) for p in diff]
        self.assertTrue(all(0.55 < fv < 0.9 for _fu, fv in pts))
        self.assertTrue(any(fu < 0.4 for fu, _ in pts) and any(fu > 0.6 for fu, _ in pts))
        self.assertFalse(any(0.4 < fu < 0.6 for fu, _ in pts))  # the cheeks are apart

    def test_bangs_cover_the_forehead_and_the_temples_but_never_the_eyes(self):
        c = paint(self.model, [{"type": "bangs", "color": MAG_HEX, "material": "hair_soft"}])
        diff = painted(self.base, c, self.b)
        pts = [self.fu_fv(p) for p in diff]
        self.assertTrue(any(fv < 0.2 and 0.4 < fu < 0.6 for fu, fv in pts))  # fringe over the middle
        self.assertTrue(any(fu < 0.1 and fv > 0.6 for fu, fv in pts))  # temple locks reach down
        self.assertTrue(any(fu > 0.9 and fv > 0.6 for fu, fv in pts))
        eye_zone = [(fu, fv) for fu, fv in pts if 0.14 < fu < 0.86 and fv > 0.44]
        self.assertEqual(eye_zone, [])  # the face below the fringe is free
        for part in ("left", "right", "none"):
            d = painted(self.base, paint(self.model, [{"type": "bangs", "part": part}]), self.b)
            self.assertTrue(d)
        self.assertEqual(painted(self.base, paint(self.model, [{"type": "bangs", "locks": False, "depth": 0.2}]), self.b) &
                         {p for p in self.front if self.fu_fv(p)[1] > 0.45}, set())

    def test_hair_soft_has_no_grey_flecks_where_hair_does(self):
        t = Texel(0, 0)
        greyed = {"hair": 0, "hair_soft": 0}
        for n in range(2000):
            t.p = (n * 0.37, n * 0.11, n * 0.23)
            for name in greyed:
                rgb = shade_texel(name, t, (100, 70, 45), (0, 0, 0), 3)
                greyed[name] += int(max(rgb) - min(rgb) < 30)  # a brown strand never gets this colourless
        self.assertGreater(greyed["hair"], 0)
        self.assertEqual(greyed["hair_soft"], 0)

    def test_soft_materials_are_smoother_than_their_originals(self):
        def spread(material):
            ap = custom_appearance(regions={"head": {"material": material, "color": "skin"},
                                            "torso": {"material": material, "color": "primary"},
                                            "arms": {"material": material, "color": "primary"}})
            c, _ = skin.paint(*self.model, ap, 5)
            x, y, w, h = self.b.faces[("chest", "front")]
            lums = [colors.luminance(c.get(i, j)[:3]) for j in range(y, y + h) for i in range(x, x + w)]
            mean = sum(lums) / len(lums)
            return (sum((v - mean) ** 2 for v in lums) / len(lums)) ** 0.5

        self.assertLess(spread("silk"), spread("cloth") * 0.5)
        self.assertLess(spread("skin_soft"), spread("skin") * 0.5)


class EmblemTests(unittest.TestCase):
    def setUp(self):
        self.b = custom_body(density=48, size=512)
        self.model = self.b.build()
        self.base = base_of(self.model)
        self.front = faces_of(self.b, ("abdomen", "chest"), ["front"])

    def emblem(self, **kw):
        f = {"type": "emblem", "shape": "cross", "region": "torso", "face": "front", "color": "#40e0ff", "size": 0.7, "cy": 0.75}
        f.update(kw)
        return paint(self.model, [f])

    def test_glow_adds_a_halo_and_lightens_the_cross(self):
        plain = self.emblem()
        glow = self.emblem(glow=True)
        d_plain = painted(self.base, plain, self.b)
        d_glow = painted(self.base, glow, self.b)
        self.assertTrue(d_plain < d_glow)  # the halo is extra texels around the cross
        self.assertTrue(d_glow <= self.front)

        def lum(canvas, pts):
            return sum(colors.luminance(canvas.get(*p)[:3]) for p in pts) / len(pts)

        self.assertGreater(lum(glow, d_plain), lum(plain, d_plain) + 0.08)  # white-hot body
        self.assertGreater(len(d_glow - d_plain), len(d_plain) // 2)
        self.assertEqual(painted(self.base, self.emblem(glow=False), self.b), d_plain)
        self.assertLess(len(d_glow), len(painted(self.base, self.emblem(glow=True, halo=5), self.b)))

    def test_winged_cross_paints_the_cross_and_two_wings(self):
        c = self.emblem(shape="winged_cross", size=0.95, wing_color=MAG_HEX)
        x, y, w, h = self.b.faces[("chest", "front")]
        diff = {(i - x, j - y) for i, j in painted(self.base, c, self.b) if x <= i < x + w and y <= j < y + h}
        wings = {p for p in diff if c.get(p[0] + x, p[1] + y)[:3] == MAGENTA}
        cross = diff - wings
        self.assertTrue(wings and cross)
        cx = (min(p[0] for p in cross) + max(p[0] for p in cross)) / 2.0
        left = [p for p in wings if p[0] < cx]
        right = [p for p in wings if p[0] > cx]
        self.assertTrue(left and right)
        self.assertEqual({(w - 1 - i, j) for i, j in left}, set(right))  # mirror-symmetric wings


class CloseupTests(unittest.TestCase):
    def test_closeup_frames_the_body_and_shows_the_face_large(self):
        b = custom_body(density=48, size=512, wing=True)
        mesh, tpl = b.build()
        c = paint((mesh, tpl), [{"type": "eyes", "style": "visor", "color": MAG_HEX}])
        img = preview.render_closeup(mesh, tpl, c)
        pw = preview.CLOSEUP_W
        self.assertEqual((img.w, img.h), (3 * pw + 2 * 4, preview.CLOSEUP_BODY_H))

        def magenta(x0, x1):
            return sum(1 for j in range(img.h) for i in range(x0, x1)
                       if img.get(i, j)[0] > 110 and img.get(i, j)[2] > 110 and img.get(i, j)[1] < 70)

        front, three_q, face = (0, pw), (pw + 4, 2 * pw + 4), (2 * (pw + 4), 3 * pw + 8)
        self.assertGreater(magenta(*front), 50)
        self.assertGreater(magenta(*three_q), 20)
        self.assertGreater(magenta(*face), 2 * magenta(*front))  # the face panel is much closer than the body panel
        rows = [j for j in range(img.h) if any(img.get(i, j)[:3] != preview.BG[:3] for i in range(*front))]
        self.assertGreater(max(rows) - min(rows), 0.7 * preview.CLOSEUP_BODY_H)  # the body fills the panel, wings or not
        # the standard preview shows the same eyes much smaller (wings shrink the whole-body views)
        full = preview.render_preview(mesh, tpl, c)
        pw_full = preview.view_panel_width(mesh, preview.efmesh.frame(mesh, tpl)[0], [yaw for _, yaw in preview.CUSTOM_VIEWS])
        full_magenta = sum(1 for j in range(full.h) for i in range(pw_full)
                           if full.get(i, j)[0] > 110 and full.get(i, j)[2] > 110 and full.get(i, j)[1] < 70)
        self.assertGreater(magenta(*front), 2 * full_magenta)

    def test_no_closeup_without_a_head(self):
        mesh, tpl = make_stock_without_head()
        c, _ = skin.paint(mesh, tpl, appearance(), 3)
        self.assertIsNone(preview.render_closeup(mesh, tpl, c))


def make_stock_without_head():
    from test_skin import make_mesh
    mesh, tpl = make_mesh(overlay=False)
    mesh.tris = [t for t in mesh.tris if t.part != "head"]
    return mesh, tpl


class RepaintAndTargetTests(unittest.TestCase):
    def setUp(self):
        self.b = custom_body(density=32, wing=True)
        self.model = self.b.build()
        self.base = base_of(self.model)

    def test_repaint_changes_only_the_target_and_keeps_the_boxes_own_colour(self):
        horn = faces_of(self.b, ("horn",), CustomBuilder.FACES)
        c = paint(self.model, [{"type": "repaint", "region": "box:horn", "material": "silk"}])
        diff = painted(self.base, c, self.b)
        self.assertTrue(diff and diff <= horn)
        mean = tuple(sum(c.get(i, j)[k] for i, j in horn) / len(horn) for k in range(3))
        self.assertLess(dist(mean, (212, 170, 50)), 60)  # still the gold of the box's style, now as smooth silk
        c2 = paint(self.model, [{"type": "repaint", "region": "box:horn", "material": "silk", "color": MAG_HEX}])
        mean2 = tuple(sum(c2.get(i, j)[k] for i, j in horn) / len(horn) for k in range(3))
        self.assertLess(dist(mean2, MAGENTA), 40)
        # no target, no change; an unknown material changes nothing either
        self.assertEqual(painted(self.base, paint(self.model, [{"type": "repaint", "material": "silk"}]), self.b), set())
        self.assertEqual(painted(self.base, paint(self.model, [{"type": "repaint", "region": "box:horn", "material": "x"}]), self.b), set())

    def test_part_targets_select_a_single_body_box_whatever_its_region(self):
        for sel, bid in (("part:arm_r", "arm_r"), ("part:p*", "pauldron_r"), ("part:wing_?", None)):
            f = {"type": "repaint", "regions": [sel], "material": "silk", "color": MAG_HEX}
            diff = painted(self.base, paint(self.model, [f]), self.b)
            self.assertTrue(diff, sel)
            expect = faces_of(self.b, (bid,), CustomBuilder.FACES) if bid else faces_of(self.b, ("wing_r", "wing_l"), CustomBuilder.FACES)
            self.assertTrue(diff <= expect, sel)

    def test_trim_can_start_above_the_bottom_and_takes_several_regions(self):
        f = {"type": "trim", "regions": ["arms"], "where": "cuff", "at": 0.5, "width": 0.1, "color": MAG_HEX}
        c = paint(self.model, [f])
        diff = painted(self.base, c, self.b)
        x, y, w, h = self.b.faces[("arm_r", "front")]
        rows = sorted({j for i, j in diff if x <= i < x + w and y <= j < y + h})
        self.assertTrue(rows)
        # the arm_r region spans the arm box and its pauldron: y 0.8 .. 1.57; the band is the 0.5-0.6 fraction of that
        lo, hi = 0.8 + 0.5 * 0.77, 0.8 + 0.6 * 0.77
        for j in rows:
            wy = 1.5 - (j - y + 0.5) / float(h) * 0.7
            self.assertTrue(lo - 0.03 <= wy <= hi + 0.03, (j, wy))
        plain = paint(self.model, [dict(f, at=0.0)])
        self.assertNotEqual(bytes(plain.data), bytes(c.data))


class SparkleTests(unittest.TestCase):
    def setUp(self):
        self.b = custom_body(density=32)
        self.model = self.b.build()
        self.base = base_of(self.model)

    def test_density_deterministic_and_confined(self):
        f = {"type": "sparkle", "region": "torso", "faces": ["front"], "density": 0.1, "color": MAG_HEX, "strength": 1.0}
        c = paint(self.model, [f])
        diff = painted(self.base, c, self.b)
        front = faces_of(self.b, ("abdomen", "chest"), ["front"])
        self.assertTrue(diff <= front)
        self.assertAlmostEqual(len(diff) / float(len(front)), 0.1, delta=0.05)
        self.assertEqual({c.get(i, j)[:3] for i, j in diff}, {MAGENTA})
        self.assertEqual(bytes(c.data), bytes(paint(self.model, [f]).data))
        other = paint(self.model, [f], seed=6)
        self.assertNotEqual(painted(base_of(self.model, seed=6), other, self.b), diff)  # the sparkle pattern follows the boss seed
        self.assertEqual(painted(self.base, paint(self.model, [dict(f, density=0.0)]), self.b), set())


class MaterialShapeTests(unittest.TestCase):
    def texel(self, **kw):
        t = Texel(kw.pop("x", 0), kw.pop("y", 0))
        for k, v in kw.items():
            setattr(t, k, v)
        return t

    def test_silk_feather_has_a_pale_rib_soft_edges_and_rare_sparkles(self):
        c = (150, 120, 220)
        rib = shade_texel("silk_feather", self.texel(ru=0.5, rv=0.5, rnu=0.1, rnv=0.05, x=3, y=3), c, c, 1)
        mid = shade_texel("silk_feather", self.texel(ru=0.3, rv=0.5, rnu=0.1, rnv=0.05, x=4, y=3), c, c, 1)
        edge = shade_texel("silk_feather", self.texel(ru=0.02, rv=0.5, rnu=0.1, rnv=0.05, x=5, y=3), c, c, 1)
        lum = colors.luminance
        self.assertGreater(lum(rib), lum(mid) + 0.03)
        self.assertLess(lum(edge), lum(mid) - 0.02)
        white = (250, 250, 255)
        rib_w = shade_texel("silk_feather", self.texel(ru=0.5, rv=0.5, rnu=0.1, rnv=0.05, x=3, y=3), white, white, 1)
        mid_w = shade_texel("silk_feather", self.texel(ru=0.3, rv=0.5, rnu=0.1, rnv=0.05, x=4, y=3), white, white, 1)
        self.assertLess(lum(rib_w), lum(mid_w) + 1e-9)  # on white feathers the rib is a touch darker, never invisible-bright
        sparkles = 0
        for x in range(200):
            for y in range(100):
                px = shade_texel("silk_feather", self.texel(ru=0.3, rv=0.5, rnu=0.1, rnv=0.05, x=x, y=y), c, c, 1)
                sparkles += int(min(px) > 200)
        self.assertTrue(20 < sparkles < 160, sparkles)  # about 0.4% of 20000 texels
        again = shade_texel("silk_feather", self.texel(ru=0.5, rv=0.5, rnu=0.1, rnv=0.05, x=3, y=3), c, c, 1)
        self.assertEqual(again, rib)

    def test_silk_feather_follows_the_face_rectangle_of_a_box(self):
        b = custom_body(density=32, wing=True)
        model = b.build()
        base = base_of(model)
        c = paint(model, [{"type": "repaint", "region": "box:wing_r", "faces": ["back"], "material": "silk_feather", "color": "#8060c0"}])
        x, y, w, h = b.faces[("wing_r", "back")]
        cols = [sum(colors.luminance(c.get(i, j)[:3]) for j in range(y, y + h)) / h for i in range(x, x + w)]
        mid = [i for i, v in enumerate(cols) if v == max(cols)]
        self.assertTrue(abs(mid[0] - (w - 1) / 2.0) <= 1.0, (mid, w))  # the rib sits in the middle of the face
        self.assertLess(cols[0], cols[w // 4])  # and the edge is shaded
        self.assertEqual(painted(base, c, b) - faces_of(b, ("wing_r",), ["back"]), set())

    def test_hair_soft_has_strands_a_gloss_band_and_a_darker_underside(self):
        def avg(ly, face="front"):
            vals = []
            for n in range(300):
                t = self.texel(u=n * 0.0125, v=0.5, tb=0.02, ly=ly, face=face)
                vals.append(colors.luminance(shade_texel("hair_soft", t, (110, 70, 45), (0, 0, 0), 4)))
            return sum(vals) / len(vals)

        self.assertGreater(avg(0.8), avg(0.5) + 0.02)
        self.assertLess(avg(0.05), avg(0.5) - 0.02)
        self.assertLess(avg(0.5, "bottom"), avg(0.5))
        # tone changes from strand to strand along a row, but drifts slowly down a strand
        row = [colors.luminance(shade_texel("hair_soft", self.texel(u=n * 0.032, v=0.5, tb=0.02, ly=0.5), (110, 70, 45), (0, 0, 0), 4)) for n in range(60)]
        col = [colors.luminance(shade_texel("hair_soft", self.texel(u=0.4, v=0.5 + n * 0.01, tb=0.02, ly=0.5), (110, 70, 45), (0, 0, 0), 4)) for n in range(60)]
        spread = lambda vs: max(vs) - min(vs)
        self.assertGreater(spread(row), 2 * spread(col))

    def test_lace_has_no_checker_grain_unlike_cloth(self):
        def jitter(material):
            tot = 0.0
            prev = None
            for x in range(80):
                t = self.texel(x=x, y=7, m=1, p=(x * 0.5, 3.0, 1.0))
                v = colors.luminance(shade_texel(material, t, (230, 235, 250), (0, 0, 0), 2))
                if prev is not None:
                    tot += abs(v - prev)
                prev = v
            return tot / 79

        self.assertLess(jitter("lace"), jitter("cloth") * 0.6)
        self.assertLess(jitter("silk"), jitter("cloth") * 0.6)


class LatinCrossTests(unittest.TestCase):
    def test_latin_cross_is_the_winged_cross_without_wings(self):
        b = custom_body(density=48, size=512)
        model = b.build()
        base = base_of(model)
        f = {"type": "emblem", "region": "torso", "face": "front", "color": "#40e0ff", "size": 0.9, "cy": 0.75, "wing_color": MAG_HEX}
        winged = paint(model, [dict(f, shape="winged_cross")])
        latin = paint(model, [dict(f, shape="latin_cross")])
        d_w, d_l = painted(base, winged, b), painted(base, latin, b)
        wings = {p for p in d_w if winged.get(*p)[:3] == MAGENTA}
        self.assertTrue(wings)
        self.assertEqual(d_w - wings, d_l)
        self.assertFalse({p for p in d_l if latin.get(*p)[:3] == MAGENTA})


class RegionSelTests(unittest.TestCase):
    def test_box_globs_match_box_regions_only(self):
        sel = features.RegionSel(["box:wing_*", "torso"])
        for region, expect in (("box:wing_r", True), ("box:wing_l", True), ("box:cape", False), ("torso", True),
                               ("head", False), ("wing_r", False)):
            t = Texel(0, 0)
            t.region = region
            self.assertEqual(sel.match(t), expect, region)
        exact = features.RegionSel(["box:cape"])
        t = Texel(0, 0)
        t.region = "box:cape2"
        self.assertFalse(exact.match(t))


if __name__ == "__main__":
    unittest.main()
