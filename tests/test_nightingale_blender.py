"""Tests of the Blender modelling kit (`scripts/blender/bfk/modeling.py`) and the Nightingale example build.

The kit's geometry, UV packing and weight maths are pure Python and always run; the Blender build + export test runs only
when a Blender executable exists (`bossforge_core.blender.find_blender`)."""
import json
import math
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import _path  # noqa: F401  (puts scripts/ on sys.path)
from _path import SKILL

sys.path.insert(0, str(SKILL / "scripts" / "blender"))
from bfk import modeling as K  # noqa: E402

EXAMPLE = SKILL / "assets" / "examples" / "nightingale-blender"


def signed_volume(built):
    v = 0.0
    for f in built.faces:
        p = [built.positions[i] for i in f]
        for k in range(1, len(p) - 1):
            v += K.vdot(p[0], K.vcross(p[k], p[k + 1])) / 6.0
    return v


class WeightMaths(unittest.TestCase):
    def test_smoothstep_endpoints_and_direction(self):
        self.assertEqual(K.smoothstep(0.0, 1.0, -1.0), 0.0)
        self.assertEqual(K.smoothstep(0.0, 1.0, 2.0), 1.0)
        self.assertAlmostEqual(K.smoothstep(0.0, 1.0, 0.5), 0.5)
        self.assertEqual(K.smoothstep(1.0, 0.0, 1.0), 0.0)   # descending edges
        self.assertEqual(K.smoothstep(1.0, 0.0, 0.0), 1.0)
        xs = [i / 20.0 for i in range(21)]
        ys = [K.smoothstep(0.0, 1.0, x) for x in xs]
        self.assertEqual(ys, sorted(ys))

    def test_normalize_prunes_and_sums_to_one(self):
        w = K.normalize_weights({"A": 0.5, "B": 0.3, "C": 0.15, "D": 0.05, "E": 0.0})
        self.assertEqual(sorted(w), ["A", "B", "C"])
        self.assertAlmostEqual(sum(w.values()), 1.0, places=12)
        self.assertEqual(K.normalize_weights({"A": 2.0}), {"A": 1.0})
        self.assertEqual(K.normalize_weights({"A": 1.0, "B": 0.001}), {"A": 1.0})  # below min_weight
        with self.assertRaises(ValueError):
            K.normalize_weights({"A": 0.0})

    def test_normalize_is_deterministic_on_ties(self):
        a = K.normalize_weights({"B": 1.0, "A": 1.0, "C": 1.0, "D": 1.0})
        b = K.normalize_weights({"D": 1.0, "C": 1.0, "B": 1.0, "A": 1.0})
        self.assertEqual(a, b)
        self.assertEqual(sorted(a), ["A", "B", "C"])

    def test_blend_weights_preserves_total(self):
        a, b = {"Chest": 1.0}, {"Arm_R": 0.6, "Hand_R": 0.4}
        for t in (0.0, 0.25, 0.5, 1.0):
            w = K.blend_weights(a, b, t)
            self.assertAlmostEqual(sum(w.values()), 1.0, places=12)
        self.assertEqual(K.blend_weights(a, b, 0.0), {"Chest": 1.0})

    def test_rule_chain_stops_and_symmetry(self):
        rule = K.rule_chain(2, [(1.5, "Chest"), (1.4, "Arm_R"), (1.1, "Arm_R"), (1.0, "Hand_R")])  # descending
        self.assertEqual(rule((0, 0, 1.6)), {"Chest": 1.0})
        self.assertEqual(rule((0, 0, 1.25)), {"Arm_R": 1.0})
        self.assertEqual(rule((0, 0, 0.5)), {"Hand_R": 1.0})
        mid = rule((0, 0, 1.45))
        self.assertAlmostEqual(mid["Chest"], 0.5, places=6)
        self.assertAlmostEqual(sum(mid.values()), 1.0, places=12)
        asc = K.rule_chain(2, [(0.0, "Leg_R"), (0.3, "Leg_R"), (0.5, "Thigh_R")])
        self.assertEqual(asc((0, 0, 0.1)), {"Leg_R": 1.0})
        self.assertEqual(asc((0, 0, 0.9)), {"Thigh_R": 1.0})
        self.assertTrue(0.0 < asc((0, 0, 0.4))["Thigh_R"] < 1.0)

    def test_rule_mix_left_right(self):
        rule = K.rule_mix("Thigh_L", "Thigh_R", lambda p: K.smoothstep(-0.05, 0.05, p[0]))
        self.assertEqual(rule((-0.2, 0, 0)), {"Thigh_L": 1.0})
        self.assertEqual(rule((0.2, 0, 0)), {"Thigh_R": 1.0})
        c = rule((0.0, 0, 0))
        self.assertAlmostEqual(c["Thigh_L"], 0.5)
        self.assertAlmostEqual(c["Thigh_R"], 0.5)

    def test_swap_joint(self):
        self.assertEqual(K.swap_joint("Thigh_R"), "Thigh_L")
        self.assertEqual(K.swap_joint("Hand_L"), "Hand_R")
        self.assertEqual(K.swap_joint("Chest"), "Chest")


class AtlasPacking(unittest.TestCase):
    def _check(self, sizes, pos, atlas, margin):
        for (w, h), (x, y) in zip(sizes, pos):
            self.assertGreaterEqual(x, margin)
            self.assertGreaterEqual(y, margin)
            self.assertLessEqual(x + w + margin, atlas)
            self.assertLessEqual(y + h + margin, atlas)
        for i in range(len(sizes)):
            for j in range(i + 1, len(sizes)):
                (w1, h1), (x1, y1), (w2, h2), (x2, y2) = sizes[i], pos[i], sizes[j], pos[j]
                gap_x = max(x1 - (x2 + w2), x2 - (x1 + w1))
                gap_y = max(y1 - (y2 + h2), y2 - (y1 + h1))
                self.assertGreaterEqual(max(gap_x, gap_y), margin, "rects %d and %d closer than the margin" % (i, j))

    def test_no_overlap_inside_atlas_with_margin(self):
        rng = random.Random(3)
        sizes = [(rng.randint(1, 60), rng.randint(1, 60)) for _ in range(120)]
        pos = K.pack_rects(sizes, 512, margin=4)
        self.assertIsNotNone(pos)
        self._check(sizes, pos, 512, 4)

    def test_deterministic_and_order_independent_of_input_hash(self):
        sizes = [(10, 20), (10, 20), (30, 5), (7, 7)]
        self.assertEqual(K.pack_rects(sizes, 128, 2), K.pack_rects(list(sizes), 128, 2))

    def test_too_big_returns_none(self):
        self.assertIsNone(K.pack_rects([(200, 10)], 128, 2))
        self.assertIsNone(K.pack_rects([(100, 100)] * 4, 128, 2))

    def test_fit_scale_shrinks_until_it_fits(self):
        dims = [(0.5, 0.5, 400.0)] * 12            # 200 x 200 texels each at full density: far too big for 256
        f = K.fit_scale(dims, 256, 2)
        self.assertLess(f, 1.0)
        sizes = [K.island_texels(w, h, d * f) for w, h, d in dims]
        self.assertIsNotNone(K.pack_rects(sizes, 256, 2))
        self.assertEqual(K.fit_scale([(0.1, 0.1, 10.0)], 256, 2), 1.0)
        with self.assertRaises(ValueError):
            K.fit_scale([(5.0, 5.0, 1000.0)], 64, 2)

    def test_island_texels_whole_and_at_least_one(self):
        self.assertEqual(K.island_texels(0.5, 0.25, 64.0), (32, 16))
        self.assertEqual(K.island_texels(0.001, 0.001, 10.0), (1, 1))


class GeometryAndUv(unittest.TestCase):
    def model(self):
        return K.Model(["skin", "cloth"], atlas=256, margin=2, density={"skin": 64.0, "cloth": 32.0})

    def test_block_is_closed_outward_and_has_expected_faces(self):
        m = self.model()
        K.block(m, (0, 0, 0), (1, 2, 3), "skin", "Head")
        b = m.build()
        self.assertAlmostEqual(signed_volume(b), 6.0, places=9)
        self.assertEqual(len(b.faces), 6)
        self.assertEqual(b.triangles(), 12)

    def test_bevelled_block_volume_and_orientation(self):
        m = self.model()
        K.block(m, (0, 0, 0), (1, 1, 1), "skin", "Head", bevel=0.1)
        b = m.build()
        self.assertTrue(0.9 < signed_volume(b) < 1.0)
        self.assertTrue(all(len(f) >= 3 for f in b.faces))

    def test_sweep_and_loft_are_outward(self):
        m = self.model()
        K.sweep(m, [(0, 0, 0), (0, 0.1, -0.5), (0, 0.3, -1.0)], [0.2, 0.2, 0.05], 0.05, "cloth", "Chest")
        K.stacked_rects(m, [(1.0, 0, 0, 0.3, 0.2), (0.5, 0, 0, 0.4, 0.3), (0.0, 0, 0, 0.5, 0.3)], "cloth", "Root")
        b = m.build()
        self.assertGreater(signed_volume(b), 0.0)

    def test_cell_outlines_polygons_and_holes(self):
        self.assertEqual(K.cell_outlines({(0, 0)}), [[(0, 0), (1, 0), (1, 1), (0, 1)]])
        ell = K.cell_outlines({(0, 0), (1, 0), (0, 1)})                     # an L: one polygon, 6 corners (collinear points dropped)
        self.assertEqual(len(ell), 1)
        self.assertEqual(len(ell[0]), 6)
        self.assertEqual(len(K.cell_outlines({(0, 0), (1, 1)})), 2)         # cells that only touch at a corner are two pieces
        with self.assertRaises(ValueError):                                 # a ring of cells round a hole
            K.cell_outlines({(i, j) for i in range(3) for j in range(3)} - {(1, 1)})

    def test_cell_plate_is_a_closed_outward_slab_centred_on_its_plane(self):
        m = self.model()
        cells = {(0, 0), (1, 0), (2, 0), (0, 1), (2, 1)}                    # a U of 5 cells
        K.cell_plate(m, cells, "cloth", "Head", origin=(0.0, 0.0, 0.0), es=(0.1, 0.0, 0.0), ez=(0.0, 0.0, 0.1), thickness=0.02)
        b = m.build()
        self.assertAlmostEqual(signed_volume(b), 5 * 0.1 * 0.1 * 0.02, places=9)
        ys = [p[1] for p in b.positions]
        self.assertAlmostEqual(min(ys), -0.01, places=9)
        self.assertAlmostEqual(max(ys), 0.01, places=9)

    def test_uniform_texel_density_per_group(self):
        m = self.model()
        K.block(m, (0, 0, 0), (0.5, 0.25, 0.75), "skin", "Head")
        K.stacked_rects(m, [(1.0, 1, 0, 0.2, 0.1), (0.0, 1, 0, 0.3, 0.1)], "cloth", "Root")
        b = m.build()
        self.assertEqual(b.scale, 1.0)
        a = b.atlas
        for f, uv, g in zip(b.faces, b.uvs, b.groups):
            dens = (64.0, 32.0)[g]
            for i in range(len(f)):
                j = (i + 1) % len(f)
                e3 = K.vlen(K.vsub(b.positions[f[i]], b.positions[f[j]]))
                e2 = math.hypot((uv[i][0] - uv[j][0]) * a, (uv[i][1] - uv[j][1]) * a)
                if e3 > 0.12:  # long edges: texel snapping error is below 1 texel
                    self.assertAlmostEqual(e2 / e3, dens, delta=dens * 0.1 + 1.0 / e3 * 0.5)

    def test_uvs_inside_unit_square_without_overlap(self):
        m = self.model()
        for k in range(6):
            K.block(m, (k, 0, 0), (k + 0.4, 0.3, 0.5), "skin", "Head")
        b = m.build()
        covered = {}
        a = b.atlas
        for uv in b.uvs:
            for u, v in uv:
                self.assertTrue(-1e-9 <= u <= 1.0 + 1e-9 and -1e-9 <= v <= 1.0 + 1e-9)
        # each face lies on its own texels: sample face centres and make sure no texel belongs to two faces
        for fi, uv in enumerate(b.uvs):
            cu = sum(p[0] for p in uv) / len(uv)
            cv = sum(p[1] for p in uv) / len(uv)
            key = (int(cu * a), int(cv * a))
            self.assertNotIn(key, covered)
            covered[key] = fi

    def test_images_are_upright_for_vertical_faces(self):
        """Image-up (v increasing) must be world-up for a front face, i.e. the texture is not rotated or mirrored."""
        m = self.model()
        K.block(m, (-0.5, -0.5, 0.0), (0.5, 0.5, 1.0), "skin", "Head")
        b = m.build()
        for f, uv in zip(b.faces, b.uvs):
            p = [b.positions[i] for i in f]
            n = K.vnorm(K.newell(p))
            if abs(n[1]) > 0.9 and n[1] > 0:  # the +Y (front) face
                # u must grow towards the viewer's right = -x; v grows with z
                du = sum((uv[i][0] - uv[0][0]) * (p[i][0] - p[0][0]) for i in range(len(f)))
                dv = sum((uv[i][1] - uv[0][1]) * (p[i][2] - p[0][2]) for i in range(len(f)))
                self.assertLess(du, 0.0)
                self.assertGreater(dv, 0.0)
                return
        self.fail("no front face found")

    def test_mirror_swaps_joints_and_keeps_winding_outward(self):
        m = self.model()
        mk = m.mark()
        K.block(m, (0.2, -0.1, 0.0), (0.4, 0.1, 0.5), "skin", K.rule_blend(2, 0.5, 0.0, "Arm_R", "Hand_R"))
        m.mirror(mk)
        b = m.build()
        self.assertEqual(len(b.positions), 16)
        right = [p for p in b.positions[:8]]
        left = b.positions[8:]
        for p, q in zip(right, left):
            self.assertEqual((-p[0], p[1], p[2]), q)
        self.assertTrue(all(set(w) <= {"Arm_R", "Hand_R"} for w in b.weights[:8]))
        self.assertTrue(all(set(w) <= {"Arm_L", "Hand_L"} for w in b.weights[8:]))
        self.assertAlmostEqual(signed_volume(b), 2 * 0.2 * 0.2 * 0.5, places=9)

    def test_every_vertex_weight_sums_to_one(self):
        m = self.model()
        K.stacked_rects(m, [(1.0, 0, 0, 0.3, 0.2), (0.0, 0, 0, 0.3, 0.2)], "cloth",
                        K.rule_chain(2, [(1.0, "Torso"), (0.5, "Chest"), (0.0, "Head")]))
        b = m.build()
        for w in b.weights:
            self.assertAlmostEqual(sum(w.values()), 1.0, places=12)

    def test_unknown_group_is_an_error(self):
        m = self.model()
        with self.assertRaises(KeyError):
            K.block(m, (0, 0, 0), (1, 1, 1), "nope", "Head")

    def test_joint_positions_follow_rigs_json(self):
        j = K.joint_positions()
        self.assertEqual(len(j), 20)
        self.assertAlmostEqual(j["Thigh_R"][0], 0.125)
        self.assertAlmostEqual(j["Head"][2], 1.514, places=3)  # storage z = rigs.json y


class Triangulation(unittest.TestCase):
    def test_ear_clip_concave_c_shape(self):
        # a C-shaped (concave) polygon with collinear runs, like the cap of a skirt wall
        pts = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (3.0, 0.0, 0.0), (3.0, 1.0, 0.0), (3.0, 3.0, 0.0), (2.0, 3.0, 0.0),
               (1.0, 3.0, 0.0), (0.0, 3.0, 0.0), (0.0, 2.0, 0.0), (2.0, 2.0, 0.0), (2.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
        area = abs(K.newell(pts)[2]) / 2.0
        tris = K.ear_clip(pts)
        self.assertGreaterEqual(len(tris), 3)
        total = 0.0
        for a, b, c in tris:
            n = K.vcross(K.vsub(pts[b], pts[a]), K.vsub(pts[c], pts[a]))
            self.assertGreater(abs(n[2]), 1e-9, "zero-area triangle")
            self.assertEqual(n[2] > 0, K.newell(pts)[2] > 0, "triangle winding differs from the polygon's")
            total += abs(n[2]) / 2.0
        self.assertAlmostEqual(total, area, places=9)

    def test_loft_cap_with_subdivided_face_has_no_zero_area_triangles(self):
        m = K.Model(["a"], atlas=128, margin=2, density={"a": 32.0})
        ring = lambda z: [(0.1, 0.05, z), (0.03, 0.05, z), (-0.03, 0.05, z), (-0.1, 0.05, z), (-0.1, -0.05, z), (-0.03, -0.05, z), (0.03, -0.05, z), (0.1, -0.05, z)]
        K.loft(m, [ring(1.0), ring(0.5), ring(0.0)], "a", "Root")
        b = m.build()
        for f, uv in zip(b.faces, b.uvs):
            self.assertLessEqual(len(f), 4, "n-gons must be triangulated by the kit")
            for k in range(1, len(f) - 1):
                p = [b.positions[i] for i in (f[0], f[k], f[k + 1])]
                self.assertGreater(K.vlen(K.vcross(K.vsub(p[1], p[0]), K.vsub(p[2], p[0]))), 1e-9)
        self.assertAlmostEqual(signed_volume(b), 0.2 * 0.1 * 1.0, places=9)


class NightingaleModel(unittest.TestCase):
    """The pure-Python build of the shipped example (no Blender): budgets, skinning, UVs, and that the committed bundle is the build."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(EXAMPLE))
        import build_nightingale as bn
        import nt_checks
        cls.bn, cls.checks = bn, nt_checks
        cls.model, cls.params = bn.build_model({})
        cls.built = cls.model.build()
        cls.spec = json.loads((EXAMPLE / "boss.json").read_text())
        cls.mesh = json.loads((EXAMPLE / "models" / "nightingale.efmesh.json").read_text())
        cls.meta = json.loads((EXAMPLE / "models" / "nightingale.meshmeta.json").read_text())

    def test_budgets(self):
        b = self.built
        self.assertLessEqual(b.triangles(), 40000)
        self.assertEqual(b.scale, 1.0, "the UV atlas overflowed: islands were shrunk")
        self.assertEqual(b.atlas, 512)

    def test_no_degenerate_triangles_and_uvs_in_range(self):
        b = self.built
        a = b.atlas
        for f, uv in zip(b.faces, b.uvs):
            self.assertLessEqual(len(f), 4)
            for u, v in uv:
                self.assertTrue(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0)
            for k in range(1, len(f) - 1):
                p = [b.positions[i] for i in (f[0], f[k], f[k + 1])]
                self.assertGreater(K.vlen(K.vcross(K.vsub(p[1], p[0]), K.vsub(p[2], p[0]))), 1e-9)
                q = (uv[0], uv[k], uv[k + 1])
                area = abs((q[1][0] - q[0][0]) * (q[2][1] - q[0][1]) - (q[2][0] - q[0][0]) * (q[1][1] - q[0][1])) / 2.0 * a * a
                self.assertGreater(area, 0.01)

    def test_every_vertex_weighted_to_total_one_with_at_most_three_joints(self):
        w = self.checks.weights_total(self.built)
        self.assertAlmostEqual(w["min_total"], 1.0, places=9)
        self.assertAlmostEqual(w["max_total"], 1.0, places=9)
        self.assertLessEqual(w["max_influences"], 3)
        joints = set(K.joint_positions())
        self.assertTrue(set(w["joints"]) <= joints)

    def test_groups_match_the_spec(self):
        used = {self.model.groups[g] for g in self.built.groups}
        self.assertEqual(used, set(self.spec["mesh"]["groups"]))
        self.assertEqual(set(self.meta["groups"]), used)
        self.assertEqual(self.spec["mesh"]["texture"]["size"], self.built.atlas)

    def test_committed_bundle_is_exactly_this_build(self):
        pos = self.mesh["vertices"]["positions"]["array"]
        self.assertGreaterEqual(len(pos) // 3, len(self.built.positions))
        for i, p in enumerate(self.built.positions):
            for k in range(3):
                self.assertAlmostEqual(p[k], pos[3 * i + k], delta=2e-5, msg="vertex %d differs: rebuild the bundle" % i)  # Blender stores float32
        tris = sum(len(v["array"]) // 9 for v in self.mesh["vertices"]["parts"].values())
        self.assertEqual(tris - 6, self.built.triangles())  # 6 empty EF parts keep one degenerate anchor triangle each

    def test_committed_bundle_weights_and_ef_validity(self):
        from bossforge_core import efmodel
        self.assertEqual(efmodel.check_mesh_json(self.mesh), [])
        r = self.checks.bundle_weight_check(self.mesh)
        self.assertEqual(r["without_weights"], 0)
        self.assertAlmostEqual(r["min_total"], 1.0, places=3)
        self.assertAlmostEqual(r["max_total"], 1.0, places=3)
        self.assertLessEqual(self.meta["height"] and r["max_influences"], 3)
        self.assertTrue((EXAMPLE / "texture" / "nightingale.png").is_file())

    def test_committed_build_report_has_no_poke_through_for_legs(self):
        rep = json.loads((EXAMPLE / "models" / "build_report.json").read_text())
        self.assertTrue(rep["ok"])
        for pose, v in rep["poke_through"].items():
            self.assertEqual(v["legs"]["inside"], 0, pose)

    def test_spec_validates(self):
        import contextlib
        import io
        from bossforge_core import cli
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(["validate", str(EXAMPLE / "boss.json")])
        res = json.loads(buf.getvalue())
        self.assertEqual(code, 0, res)
        self.assertEqual(res["errors"], [])

    def test_param_overrides_change_the_model(self):
        m2, _p = self.bn.build_model({"parts": ["nt_extras"], "skirt_segments": 2})
        self.assertLess(m2.build().triangles(), self.built.triangles())


class NightingaleHeadItems(unittest.TestCase):
    """Hair, bun, laurel with its pins, ribbon loops, long ribbons and hem tabs (reduced model: body + `nt_hair` + `nt_ribbons`): where the
    reference puts them, on the art pixel grid, skinned to Head / Chest / Arm / Root / Thigh."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(EXAMPLE))
        import build_nightingale as bn
        import nt_common
        import nt_hair
        cls.nc, cls.nh = nt_common, nt_hair
        cls.model, cls.params = bn.build_model({"parts": ["nt_hair", "nt_ribbons"]})
        cls.built = cls.model.build()
        cls.hx, cls.y0, cls.y1, cls.z0, cls.z1 = nt_common.HEAD_BOX
        cls.yc = (cls.y0 + cls.y1) / 2.0
        cls.px = nt_common.PIXEL

    def verts(self, group):
        """Vertex indices of the faces of one group."""
        gi = self.model.groups.index(group)
        return sorted({v for f, g in zip(self.built.faces, self.built.groups) if g == gi for v in f})

    def pos(self, group):
        return [self.built.positions[i] for i in self.verts(group)]

    def test_head_is_a_true_cube_of_eight_pixels(self):
        px = self.px
        self.assertAlmostEqual(2 * self.hx, 8 * px, places=9)
        self.assertAlmostEqual(self.y1 - self.y0, 8 * px, places=9)
        self.assertAlmostEqual(self.z1 - self.z0, 8 * px, places=9)

    def test_bun_top_and_dome_follow_the_reference(self):
        hair = self.pos("hair")
        top = max(p[2] for p in hair)
        self.assertAlmostEqual(top, self.z0 + 14 * self.px, places=9)               # RefStage2: bun top 1.90 blocks
        self.assertAlmostEqual(top, self.nh.bun_top_z(), places=9)
        self.assertAlmostEqual(max(abs(p[0]) for p in hair), 5 * self.px, places=9)   # hair is 10 px wide (1 px beyond the cube)
        self.assertAlmostEqual(min(p[1] for p in hair), self.yc - 6 * self.px, places=9)
        steps = sorted({round((p[2] - self.z0) / self.px, 6) for p in hair if p[2] > self.z0 + 8 * self.px})
        self.assertEqual(steps, [11.0, 12.0, 13.0, 14.0])                          # dome top + three bun tiers, whole pixels

    def test_hair_never_enters_the_head_cube(self):
        eps = 1e-6
        for x, y, z in self.pos("hair"):
            inside = abs(x) < self.hx - eps and self.y0 + eps < y < self.y1 - eps and self.z0 + eps < z < self.z1 - eps
            self.assertFalse(inside, (x, y, z))

    def test_hair_is_on_the_pixel_grid(self):
        px, zb = self.px, self.nh._curtain_bottom()
        for x, y, z in self.pos("hair"):
            for v, off in ((x, 0.0), (y, self.yc), (z, self.z0)):
                q = (v - off) / px
                if abs(q - zb) < 1e-6:        # the curtain ends exactly on the collar of the robe
                    continue
                self.assertAlmostEqual(q * 2.0, round(q * 2.0), places=6)       # whole or half pixels

    def test_laurel_sits_outside_the_hair_and_pins_reach_z12(self):
        px, eps = self.px, 1e-6
        lau = self.pos("laurel")
        self.assertTrue(lau)
        self.assertAlmostEqual(max(p[2] for p in lau), self.z0 + 12 * px, places=9)
        self.assertAlmostEqual(min(p[2] for p in lau), self.z0 + 6 * px, places=9)
        for x, y, z in lau:
            self.assertTrue(abs(x) >= 5 * px - eps or y <= self.yc - 6 * px + eps, (x, y, z))   # never inside the 10 px hair shell
        rounded = {tuple(round(c, 6) for c in p) for p in lau}
        self.assertEqual(rounded, {(-x, y, z) for x, y, z in rounded})                  # symmetric
        centre_gap = [p for p in lau if abs(p[0]) < 1 * px - eps and p[1] < self.yc]
        self.assertEqual(centre_gap, [])                                               # 2 px gap at the back centre

    def test_loops_are_one_connected_c_beside_the_head(self):
        cells = self.nh.loop_cells({})
        self.assertEqual(len(K.cell_outlines(cells)), 1)                               # one 4-connected plate per side
        loop = [p for p in self.pos("ribbon") if p[2] > 1.45]                          # above the neck: only the loops
        self.assertTrue(loop)
        self.assertAlmostEqual(max(abs(p[0]) for p in loop), 8 * self.px, delta=0.5 * self.px)   # outer edge |x| 7.9 px in the reference
        for _x, y, _z in loop:                                                          # a flat plate in a plane behind the head
            self.assertAlmostEqual(y, self.yc + self.nh.LOOP_Y * self.px, delta=0.26 * self.px)

    def test_ribbons_hang_from_hip_and_wrist_and_reach_the_ground(self):
        rib = self.pos("ribbon")
        body = [p for p in rib if p[2] < 1.3]                                           # the loops are above the neck, everything else is below
        self.assertLess(min(p[2] for p in body), 0.16)                                  # long ribbons end near the ground (reference 0.155)
        self.assertLess(max(p[2] for p in body), 0.96)                                  # ... and start at the belt / hip side, not at the head
        self.assertLess(max(abs(p[0]) for p in body), 0.6)
        self.assertGreater(max(abs(p[0]) for p in body), 0.5)                           # swing out to |x| 0.53 (reference 0.49-0.57)
        rounded = {tuple(round(c, 5) for c in p) for p in rib}
        self.assertEqual(rounded, {(-x, y, z) for x, y, z in rounded})                  # right / left mirror

    def test_loop_and_ribbon_skinning(self):
        b = self.built
        for i in self.verts("ribbon"):
            x, y, z = b.positions[i]
            w = b.weights[i]
            self.assertAlmostEqual(sum(w.values()), 1.0, places=9)
            self.assertLessEqual(len(w), 3)
            if z > 1.45:
                self.assertGreaterEqual(w.get("Head", 0.0), 0.99, (x, y, z))           # the loops follow the head
            if z < 0.3:
                self.assertTrue(set(w) <= {"Root", "Thigh_R", "Thigh_L"}, (z, w))      # the lower swing follows hips and thighs
        for i in self.verts("hair") + self.verts("laurel"):
            self.assertGreaterEqual(b.weights[i].get("Head", 0.0) + b.weights[i].get("Chest", 0.0), 0.99)

    def test_short_ribbon_is_attached_to_the_wrist(self):
        b = self.built
        tool = [(b.positions[i], b.weights[i]) for i in self.verts("ribbon") if {"Tool_R", "Tool_L"} & set(b.weights[i])]
        self.assertTrue(tool)
        for p, w in tool:
            self.assertTrue(0.45 < p[2] < 0.9 and abs(p[0]) > 0.3, p)                   # only the short ribbon (and its prongs), outside the fist
            self.assertTrue(set(w) <= {"Tool_R", "Tool_L", "Hand_R", "Hand_L"}, w)      # it moves with the fist: no stretching, no poke-through
            self.assertGreaterEqual(sum(v for k, v in w.items() if k.startswith("Tool")), 0.6)


class NightingaleBodyPixelGrid(unittest.TestCase):
    """Body, pauldrons, belt, pouches and boots of the Nightingale (reduced model: body + `nt_extras`) on the art pixel grid: 1 texel =
    1 art pixel = 0.0325 blocks, the head cube is 8 px, texel density is one parameter, every body group is uniformly dense."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(EXAMPLE))
        import build_nightingale as bn
        import nt_common
        cls.bn, cls.nc = bn, nt_common
        cls.model, cls.params = bn.build_model({"parts": ["nt_extras"]})
        cls.built = cls.model.build()

    def faces_of(self, group):
        gi = self.model.groups.index(group)
        return [f for f, g in zip(self.built.faces, self.built.groups) if g == gi]

    def test_pixel_constants(self):
        nc = self.nc
        self.assertAlmostEqual(nc.px(8), 0.26)            # the head cube is 8 art pixels = 0.26 blocks
        self.assertAlmostEqual(nc.BLOCK, 0.52)             # 16 art pixels
        self.assertAlmostEqual(nc.snap(0.1), 0.0975)       # 3 px
        self.assertAlmostEqual(nc.snap(0.1, 2.0), 0.13)    # nearest multiple of 2 px
        self.assertEqual(self.params["texel_density"], 16.0)
        self.assertEqual(self.params["atlas"], 512)

    def test_body_groups_have_one_texel_per_art_pixel(self):
        base = self.params["texel_density"] / self.nc.BLOCK   # 30.77 texels per model block
        rep = self.built.density_report
        for g in ("skin", "robe", "skirt", "tabard", "trim", "boots", "leather", "pauldron", "lace", "rune"):
            self.assertAlmostEqual(rep[g]["area"] / base, 1.0, delta=0.12, msg=g)
        for g in ("vial_g", "vial_v", "vial_r"):   # 32-texel detail group
            self.assertAlmostEqual(rep[g]["area"] / base, 2.0, delta=0.3, msg=g)
        self.assertGreaterEqual(min(v["area"] for v in rep.values()), 8.0)   # the painter's mesh_low_density threshold
        self.assertEqual(self.built.scale, 1.0)

    def test_texel_density_parameter_scales_every_group(self):
        m8, p8 = self.bn.build_model({"parts": ["nt_extras"], "texel_density": 8.0, "atlas": 256})
        b8 = m8.build()
        self.assertEqual(b8.scale, 1.0)
        for g in ("skin", "robe", "pauldron", "boots"):
            self.assertAlmostEqual(b8.density_report[g]["area"] / self.built.density_report[g]["area"], 0.5, delta=0.1, msg=g)

    def test_blocks_sit_on_the_quarter_pixel_grid(self):
        step = self.nc.PIXEL / 4.0
        for g in ("pauldron", "rune", "leather"):
            for f in self.faces_of(g):
                for i in f:
                    for c in self.built.positions[i]:
                        self.assertAlmostEqual(c / step, round(c / step), delta=1e-3, msg="%s vertex %s" % (g, self.built.positions[i]))

    def test_pauldrons_are_two_stepped_plates_per_shoulder(self):
        faces = self.faces_of("pauldron")
        self.assertEqual(len(faces), 2 * 2 * 6)
        pos = self.built.positions
        right = [f for f in faces if all(pos[i][0] > 0 for i in f)]
        heights = sorted({round(pos[i][2], 4) for f in right for i in f})
        self.assertEqual(len(heights), 4)   # 2 plates stepping 3 px down: tops and bottoms (4 distinct heights)
        self.assertAlmostEqual(heights[-1], self.nc.px(46), places=4)   # top: 0.04 over the shoulder line (landmarks 1.496)
        self.assertAlmostEqual(heights[0], self.nc.px(39), places=4)    # bottom (landmarks 1.267)
        xs = sorted({round(pos[i][0], 4) for f in right for i in f})
        self.assertEqual(len(xs), 4)        # each plate steps 1 px further out: x 4 -> 9 and 5 -> 10 px
        self.assertAlmostEqual(xs[0], self.nc.px(4), places=4)
        self.assertAlmostEqual(xs[-1], self.nc.px(10), places=4)

    def test_front_and_back_tabard_and_hip_pouch_on_the_right(self):
        ys = [K.centroid([self.built.positions[i] for i in f])[1] for f in self.faces_of("tabard")]
        self.assertTrue(any(y > 0.15 for y in ys) and any(y < -0.08 for y in ys), "tabard front and back panel")
        pouch = [K.centroid([self.built.positions[i] for i in f]) for f in self.faces_of("vial_g") if self.built.positions[f[0]][1] > 0.2]
        self.assertTrue(pouch and all(c[0] > 0 for c in pouch), "the hip pouch sits on the character's right (+x)")

    def test_belt_buckle_and_chest_ornaments(self):
        pos = self.built.positions
        orn = self.faces_of("ornament")
        self.assertEqual(len(orn), 3 * 6)   # two chest plates (mirrored) and one belt buckle
        buckle = [f for f in orn if all(p[2] < 1.1 for p in (pos[i] for i in f))]
        self.assertEqual(len(buckle), 6)
        self.assertTrue(all(pos[i][0] < 0 for f in buckle for i in f), "the buckle is left of the centre (the right one is behind the pouch)")
        belt = [pos[i][2] for f in self.faces_of("leather") for i in f if abs(pos[i][1] - 0.065) < 0.15 and abs(pos[i][0]) < 0.17 and pos[i][1] < 0.2]
        self.assertAlmostEqual(max(belt), self.nc.px(33), places=4)   # belt top (landmarks 1.02-1.065)

    def test_sleeves_cuffs_and_hands(self):
        pos = self.built.positions
        sleeve = [pos[i] for f in self.faces_of("robe") for i in f if pos[i][0] > 0.2 and pos[i][2] < 1.4]
        self.assertTrue(sleeve)
        self.assertAlmostEqual(max(p[0] for p in sleeve), self.nc.px(7.75 + 1.5 + 2.5), places=4)   # lean out 1.5 px from shoulder to wrist
        cuff = [pos[i] for f in self.faces_of("trim") for i in f if pos[i][0] > 0.15 and 0.8 < pos[i][2] < 1.05]
        width = max(p[0] for p in cuff) - min(p[0] for p in cuff)
        self.assertAlmostEqual(width, self.nc.px(6), places=4)        # 6 px cuff flares 0.5 px each side of the 5 px sleeve
        hands = [pos[i] for f in self.faces_of("skin") for i in f if pos[i][0] > 0.3 and pos[i][2] < 0.95]
        self.assertAlmostEqual(max(p[2] for p in hands), self.nc.px(28), places=4)
        self.assertAlmostEqual(min(p[2] for p in hands), self.nc.px(22), places=4)

    def test_three_back_vials_purple_violet_cyan_left_to_right_from_behind(self):
        order = {}
        for g in ("vial_r", "vial_v", "vial_g"):
            xs = [K.centroid([self.built.positions[i] for i in f]) for f in self.faces_of(g)]
            back = [c[0] for c in xs if c[1] < -0.1]
            self.assertTrue(back, g)
            order[g] = sum(back) / len(back)
        # back view: +x is on the image right
        self.assertLess(order["vial_r"], order["vial_v"])
        self.assertLess(order["vial_v"], order["vial_g"])

    def test_boots_have_a_sole_cuffs_and_rune_plates_on_the_back(self):
        for g in ("lace", "rune", "boots", "trim"):
            self.assertTrue(self.faces_of(g), g)
        self.assertEqual(len(self.faces_of("rune")), 2 * 6)
        for f in self.faces_of("rune"):
            for i in f:
                p = self.built.positions[i]
                self.assertLessEqual(p[1], 1e-9)           # behind the boot shaft
                self.assertTrue(0.15 < p[2] < 0.3)        # on the upper back of the shaft (z 5.5-8.5 px)
        self.assertAlmostEqual(min(self.built.positions[i][2] for f in self.faces_of("boots") for i in f), 0.0, places=6)

    def test_lace_frill_hangs_3_px_at_the_back_and_its_lower_edge_follows_the_shin(self):
        low = min(self.built.positions[i][2] for f in self.faces_of("lace") for i in f)
        self.assertAlmostEqual(low, self.nc.px(11), places=6)          # 3 px below the hem at the back centre (landmarks: hem z 0.36-0.42)
        top = max(self.built.positions[i][2] for f in self.faces_of("lace") for i in f)
        self.assertAlmostEqual(top, self.nc.px(14), places=6)
        shin = [self.built.weights[i] for f in self.faces_of("lace") for i in f if self.built.positions[i][2] < 0.36]
        self.assertTrue(shin)
        for w in shin:
            self.assertGreaterEqual(w.get("Leg_R", 0.0) + w.get("Leg_L", 0.0), 0.99)   # below the knee the lace moves with the shin


class NightingaleTexture(unittest.TestCase):
    """The painted texture of the example: committed bundle + `boss.json` + the hand-authored `decals/*.txt` through the pure-Python
    painter (no Blender). Checks structure (every UV texel painted, determinism, the committed PNG is this paint), the pixel-art
    character (small palette, flat runs) and palette sanity per group; never single pixels."""

    @classmethod
    def setUpClass(cls):
        from bossforge_core import pngio, shape, skin, spec
        cls.skin, cls.pngio = skin, pngio
        cls.raw = spec.load(EXAMPLE / "boss.json")
        cls.n, cls.errs, cls.warns = spec.check(cls.raw)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ctx = spec.make_context(cls.tmp.name, "roundtable", "com.bossforge.roundtable", Path(cls.tmp.name) / "cache", offline=True)
        files, _lang, cls.report = skin.generate_with_report(cls.n, cls.ctx)
        cls.png = next(f.data for f in files if f.kind == "texture")
        cls.canvas = pngio.decode_png(cls.png)
        cls.model = shape.build_model(cls.n, cls.ctx)
        cls.cover = skin._rasterize(cls.model.mesh, cls.model.tpl, cls.canvas.w, cls.canvas.h)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def texels(self, group):
        """[(x, y, rgb, normal z, world y)] of the covered texels of one group (painter space: y up, the front is -z)."""
        out = []
        mesh = self.model.mesh
        for (x, y), (ti, l0, l1, l2) in self.cover.items():
            tri = mesh.tris[ti]
            if tri.part != group:
                continue
            nz = sum(mesh.normals[tri.n[k]][2] * w for k, w in enumerate((l0, l1, l2)))
            py = sum(mesh.positions[tri.p[k]][1] * w for k, w in enumerate((l0, l1, l2)))
            out.append((x, y, self.canvas.get(x, y)[:3], nz, py))
        return out

    @staticmethod
    def mean(cols):
        return tuple(sum(c[i] for c in cols) / float(len(cols)) for i in range(3))

    def test_spec_validates_with_decals_and_every_group_is_styled(self):
        self.assertEqual(self.errs, [])
        self.assertFalse([w for w in self.warns if w.code.startswith("decal_")], self.warns)
        groups = {self.model.mesh.tris[ti].part for ti, *_ in self.cover.values()}
        self.assertEqual(groups, set(self.raw["mesh"]["groups"]))
        for g, style in self.raw["mesh"]["groups"].items():
            self.assertTrue(style.get("material") and style.get("color"), g)

    def test_atlas_size_and_every_uv_texel_is_painted(self):
        self.assertEqual((self.canvas.w, self.canvas.h), (self.raw["mesh"]["texture"]["size"],) * 2)
        self.assertEqual(self.report["texture"]["coverage"], 1.0)
        self.assertEqual(self.report["mesh"]["texture"]["uv_overlap"], 0.0)
        self.assertEqual(self.report["mesh"]["texture"]["uv_outside_triangles"], 0)
        self.assertTrue(all(self.canvas.get(x, y)[3] == 255 for (x, y) in self.cover))
        for g, info in self.report["mesh"]["groups"].items():
            self.assertGreaterEqual(info["texels_per_block"], 28.0, g)   # 1 texel per art pixel (30.8) or denser

    def test_painting_is_deterministic_and_the_committed_texture_is_this_paint(self):
        files, _lang, _rep = self.skin.generate_with_report(self.n, self.ctx)
        self.assertEqual(next(f.data for f in files if f.kind == "texture"), self.png)
        committed = (EXAMPLE / "texture" / "nightingale.png").read_bytes()
        self.assertEqual(committed, self.png, "texture/nightingale.png is stale: `bossforge skin` the example and copy the PNG")

    def test_it_is_pixel_art_a_small_palette_of_flat_runs(self):
        colours = {self.canvas.get(x, y)[:3] for (x, y) in self.cover}
        self.assertLess(len(colours), 1800, "pixel art: a limited palette, no smooth noise")
        same = pairs = 0
        for (x, y) in self.cover:
            if (x + 1, y) in self.cover:
                pairs += 1
                same += self.canvas.get(x, y)[:3] == self.canvas.get(x + 1, y)[:3]
        self.assertGreater(same / float(pairs), 0.45, "chunky flat runs expected (smooth noise gives ~0)")

    def test_group_colours_are_plausible(self):
        def lum(c):
            return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
        m = {g: self.mean([t[2] for t in self.texels(g)]) for g in self.raw["mesh"]["groups"]}
        self.assertTrue(m["skin"][0] > m["skin"][1] > m["skin"][2] and lum(m["skin"]) > 150, m["skin"])      # warm light skin
        self.assertTrue(m["hair"][0] > m["hair"][1] > m["hair"][2] and lum(m["hair"]) < 120, m["hair"])      # brown hair
        self.assertTrue(m["leather"][0] > m["leather"][2] and lum(m["leather"]) < 130, m["leather"])
        self.assertTrue(m["trim"][2] > m["trim"][0] + 25, m["trim"])                                         # blue trim
        for g in ("robe", "skirt", "tabard", "boots", "pauldron"):
            self.assertTrue(m[g][2] >= m[g][0] and 110 < lum(m[g]) < 225, (g, m[g]))                        # grey-blue cloth and boots
        self.assertGreater(lum(m["ribbon"]), 200)
        self.assertGreater(m["rune"][2], 150)                                                                # glowing cyan runes
        self.assertGreater(m["rune"][1], m["rune"][0] + 40)
        self.assertGreater(lum(m["glyph"]), 170)

    def test_wings_front_steps_from_white_to_periwinkle_and_back_is_iridescent(self):
        front, back = [], []
        for g in ("wing_a", "wing_b", "wing_c", "wing_d"):
            for t in self.texels(g):
                (front if t[3] < -0.5 else back if t[3] > 0.5 else []).append(t)
        self.assertGreater(len(front), 400)
        self.assertGreater(len(back), 400)
        ys = sorted(t[4] for t in front)
        lo, hi = ys[len(ys) // 3], ys[2 * len(ys) // 3]
        top = [t[2] for t in front if t[4] >= hi]
        low = [t[2] for t in front if t[4] <= lo]
        sat = lambda c: max(c) - min(c)
        self.assertLess(sat(self.mean(top)), sat(self.mean(low)) + 1e-9)       # the upper sail is white, the lower sail tinted
        self.assertGreater(min(self.mean(top)), 215)                           # white leading rows
        self.assertGreater(self.mean(low)[2], self.mean(low)[0] + 12)          # lavender / periwinkle toward the tips
        import colorsys
        hues = set()
        for t in back:
            h, s, v = colorsys.rgb_to_hsv(*(c / 255.0 for c in t[2]))
            if s > 0.04 and v > 0.8:
                hues.add(int(h * 12) % 12)
        self.assertGreaterEqual(len(hues), 5, "pearly pastel sheen: pink, peach, yellow, mint, lilac")
        self.assertGreater(min(self.mean([t[2] for t in back])), 200)          # pastel on white, never dark

    def test_face_is_the_8x8_art_face_on_the_head_front(self):
        from bossforge_core import decal
        rows, pal = decal.load_file("decals/face.txt", str(EXAMPLE))
        self.assertEqual((len(rows), len(rows[0])), (8, 8))
        white = tuple(int(pal["w"][i:i + 2], 16) for i in (1, 3, 5))
        iris = tuple(int(pal["i"][i:i + 2], 16) for i in (1, 3, 5))
        skin = [t for t in self.texels("skin") if t[3] < -0.7 and t[4] > 1.45]
        self.assertEqual(sum(t[2] == white for t in skin), 2, "two eye whites")
        self.assertEqual(sum(t[2] == iris for t in skin), 2, "two irises")

    def test_every_decal_file_exists_parses_and_is_used(self):
        from bossforge_core import decal
        used = {f["file"] for f in self.raw["appearance"]["features"] if f.get("type") == "decal" and f.get("file")}
        on_disk = {"decals/" + p.name for p in (EXAMPLE / "decals").glob("*.txt")}
        self.assertEqual(used, on_disk)
        for f in sorted(used):
            rows, _pal = decal.load_file(f, str(EXAMPLE))
            self.assertTrue(rows and len(rows[0]) <= 16 and len(rows) <= 16, f)


class PrismPrimitive(unittest.TestCase):
    """`K.prism`: a thin slab whose two big faces are the polygon itself (own UV islands), used by the wing planks and the glyph tile."""

    def build(self, outline, depth):
        m = K.Model(["a"], atlas=256, margin=2, density={"a": 32.0})
        K.prism(m, outline, depth, "a", "Chest")
        return m.build()

    def test_closed_outward_concave_outline_and_volume(self):
        # an L-shaped (concave) plank outline in the xz plane, 0.1 thick along -y
        outline = [(0, 0, 0), (0.2, 0, 0), (0.2, 0, 0.1), (0.1, 0, 0.1), (0.1, 0, 0.4), (0, 0, 0.4)]
        b = self.build(outline, (0.0, -0.1, 0.0))
        area = 0.2 * 0.1 + 0.1 * 0.3
        self.assertAlmostEqual(abs(signed_volume(b)), area * 0.1, places=9)
        self.assertGreater(signed_volume(b), 0.0, "faces must wind outward")
        for f in b.faces:
            self.assertLessEqual(len(f), 4, "caps are triangulated by the kit")

    def test_front_and_back_faces_have_islands_of_their_own(self):
        outline = [(0, 0, 0), (0.1, 0, 0), (0.1, 0, 0.3), (0, 0, 0.3)]
        b = self.build(outline, (0.0, -0.06, 0.0))
        front, back, wall = [], [], []
        for f, uv in zip(b.faces, b.uvs):
            p = [b.positions[i] for i in f]
            n = K.vnorm(K.newell(p))
            (front if n[1] > 0.9 else back if n[1] < -0.9 else wall).append(uv)
        self.assertTrue(front and back and wall)

        def box(uvs):
            us = [u for uv in uvs for u, _v in uv]
            vs = [v for uv in uvs for _u, v in uv]
            return min(us), max(us), min(vs), max(vs)
        fb, bb, wb = box(front), box(back), box(wall)
        for a, c in ((fb, bb), (fb, wb), (bb, wb)):
            self.assertTrue(a[1] <= c[0] or c[1] <= a[0] or a[3] <= c[2] or c[3] <= a[2], "islands overlap")
        # the plank's long axis (z) runs down the island: the front island is taller than wide
        self.assertGreater(fb[3] - fb[2], fb[1] - fb[0])


class NightingaleWingsHarness(unittest.TestCase):
    """Wings (plank feathers on the art pixel grid), back harness and floating glyph tile (reduced model: body + `nt_wings` + `nt_harness`):
    size and shape against the reference landmarks (loose bounds), tiers and depth, no holes, front / back islands, skinning, what stays
    clear of the body and the arms. Never exact numbers of the hand-fitted curves."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(EXAMPLE))
        import build_nightingale as bn
        import nt_common
        import nt_harness
        import nt_wings
        cls.bn, cls.nc, cls.nh, cls.nw = bn, nt_common, nt_harness, nt_wings
        cls.model, cls.params = bn.build_model({"parts": ["nt_wings", "nt_harness"]})
        cls.built = cls.model.build()
        cls.px = nt_common.PIXEL
        cls.WING = ("wing_a", "wing_b", "wing_c", "wing_d")

    def verts(self, groups):
        gi = {self.model.groups.index(g) for g in groups}
        return sorted({v for f, g in zip(self.built.faces, self.built.groups) if g in gi for v in f})

    def pos(self, groups):
        return [self.built.positions[i] for i in self.verts(groups)]

    def tris(self, groups):
        gi = {self.model.groups.index(g) for g in groups}
        return sum(len(f) - 2 for f, g in zip(self.built.faces, self.built.groups) if g in gi)

    def body_back(self):
        return min(self.built.positions[i][1] for f, g in zip(self.built.faces, self.built.groups) if self.model.groups[g] in ("robe", "pauldron", "trim", "leather")
                   for i in f if abs(self.built.positions[i][0]) <= 0.5 and 1.15 <= self.built.positions[i][2] <= 1.45)

    # ----------------------------------------------------------------------------------------------------------- the layout (pure data)
    def test_planks_are_flat_tipped_parallelograms_on_the_pixel_grid(self):
        planks = self.nw.layout()
        self.assertGreater(len(planks), 20)
        self.assertEqual({p["row"] for p in planks}, set(self.nw.ROWS))
        for p in planks:
            zs = sorted({q[1] for q in p["poly"]})
            ztip, ztop = zs[0], zs[-1]
            self.assertAlmostEqual(ztip, round(ztip), places=9, msg=p)                    # tip and top on whole pixels
            self.assertAlmostEqual(ztop, round(ztop), places=9, msg=p)
            self.assertGreaterEqual(ztop - ztip, 2.0 - 1e-9, p)
            for z in (ztip, ztop):
                us = sorted(q[0] for q in p["poly"] if abs(q[1] - z) < 1e-9)
                self.assertGreaterEqual(len(us), 2, "flat tip / top edge")
                for u in (us[0], us[-1]):
                    self.assertAlmostEqual(u * 2.0, round(u * 2.0), places=9, msg=p)      # corners on half pixels
            self.assertTrue(0.0 <= p["lean"] <= self.nw.LEAN_MAX + 1e-9 or p["row"] == "d", p)

    def test_every_plank_stays_inside_the_silhouette(self):
        for p in self.nw.layout():
            for u, z in p["poly"]:
                # within one pixel of the silhouette (corners are rounded to half pixels sideways)
                self.assertTrue(any(self.nw.inside(u + d, z, tol=0.5) for d in (-0.5, 0.0, 0.5)), (p["row"], p["j"], u, z))

    def test_sail_has_no_holes(self):
        """Flood the empty cells of the planks' front footprint (half-pixel grid) from outside: whatever is not reached is a hole."""
        step = 0.5
        nu, nz = int(40 / step), int(66 / step)
        filled = [[False] * nu for _ in range(nz)]
        for p in self.nw.layout():
            poly = p["poly"]
            us, zs = [q[0] for q in poly], [q[1] for q in poly]
            for j in range(int(min(zs) / step), int(max(zs) / step) + 1):
                z = (j + 0.5) * step
                xs = []
                for k in range(len(poly)):
                    (u0, z0), (u1, z1) = poly[k], poly[(k + 1) % len(poly)]
                    if (z0 <= z < z1) or (z1 <= z < z0):
                        xs.append(u0 + (z - z0) * (u1 - u0) / (z1 - z0))
                xs.sort()
                for a, b in zip(xs[0::2], xs[1::2]):
                    for i in range(int(a / step), int(b / step) + 1):
                        if 0 <= i < nu and 0 <= j < nz and a <= (i + 0.5) * step <= b:
                            filled[j][i] = True
        seen = [[False] * nu for _ in range(nz)]
        stack = [(j, i) for j in range(nz) for i in range(nu) if (i in (0, nu - 1) or j in (0, nz - 1)) and not filled[j][i]]
        for j, i in stack:
            seen[j][i] = True
        while stack:
            j, i = stack.pop()
            for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                a, b = j + dj, i + di
                if 0 <= a < nz and 0 <= b < nu and not filled[a][b] and not seen[a][b]:
                    seen[a][b] = True
                    stack.append((a, b))
        holes = sum(1 for j in range(nz) for i in range(nu) if not filled[j][i] and not seen[j][i])
        area = sum(1 for j in range(nz) for i in range(nu) if filled[j][i])
        self.assertGreater(area, 1000)
        self.assertLess(holes / float(area), 0.01, "see-through holes in the wing sail")

    def test_arm_clearance_pocket_is_optional(self):
        with_pocket = len(self.nw.layout())
        without = len(self.nw.layout({"wing_arm_clear": False}))
        self.assertGreaterEqual(without, with_pocket)
        for p in self.nw.layout():
            for u, z in p["poly"]:
                self.assertFalse(u < self.nw.ARM_CLEAR[0] - 1e-9 and z < self.nw.ARM_CLEAR[1] - 1e-9, "plank inside the arm pocket")

    # ----------------------------------------------------------------------------------------------------------- size and shape
    def test_wings_have_the_reference_size_and_reach(self):
        p = self.pos(self.WING)
        span = max(x for x, _y, _z in p) - min(x for x, _y, _z in p)
        head_top = self.nc.HEAD_BOX[4]
        self.assertGreater(span, 1.9)                              # sheet: 2.15 blocks tip to tip (1.17 x the body height)
        self.assertLess(span, 2.4)
        self.assertGreater(max(z for _x, _y, z in p), head_top + 0.1)   # the leading edge towers over the head cube
        self.assertLess(max(z for _x, _y, z in p), self.nh_bun_top() + 0.08)
        self.assertTrue(0.4 < min(z for _x, _y, z in p) < 0.8, "long feathers hang to thigh / knee height")

    def nh_bun_top(self):
        import nt_hair
        return nt_hair.bun_top_z()

    def test_four_wing_groups_two_symmetric_wings_and_a_budget(self):
        for g in self.WING:
            self.assertGreater(self.tris([g]), 100, g)
        self.assertLessEqual(self.tris(self.WING), 15000)           # contract: wings may use up to ~15k triangles
        self.assertLessEqual(self.built.triangles(), 40000)
        pts = {(round(x, 5), round(y, 5), round(z, 5)) for x, y, z in self.pos(self.WING)}
        for x, y, z in pts:
            self.assertIn((round(-x, 5), y, z), pts, "wings are mirror images")

    def test_tiers_lie_one_behind_the_other_front_tier_first(self):
        by = {}
        for g in self.WING:
            by[g] = max(y for _x, y, _z in self.pos([g]))
        # wing_a is the front-most tier, then b, c, d (each tier is at least as far back as the previous one)
        self.assertGreaterEqual(by["wing_a"], by["wing_b"])
        self.assertGreaterEqual(by["wing_b"], by["wing_c"])
        self.assertGreaterEqual(by["wing_c"], by["wing_d"])

    def test_planks_are_thin_slabs_with_front_and_back_islands(self):
        n_front = n_back = 0
        for f, g in zip(self.built.faces, self.built.groups):
            if self.model.groups[g] not in self.WING:
                continue
            nrm = K.vnorm(K.newell([self.built.positions[i] for i in f]))
            n_front += nrm[1] > 0.9
            n_back += nrm[1] < -0.9
        self.assertEqual(n_front, n_back)
        self.assertGreater(n_front, 40)
        ys = self.pos(self.WING)
        deep = max(y for _x, y, _z in ys) - min(y for _x, y, _z in ys)
        self.assertLess(deep, 0.25, "the wing is a shallow fan, not a volume")

    # ----------------------------------------------------------------------------------------------------------- body and arms
    def test_wings_and_harness_stay_behind_the_body(self):
        back = self.body_back()
        for p in self.pos(self.WING + ("harness", "harness_gold")):
            self.assertLess(p[1], back + 1e-9, p)
        gx, gy = self.pos(["glyph"]), self.pos(["glyph"])
        self.assertLess(max(p[1] for p in gy), self.nc.HEAD_BOX[2], "the glyph floats behind the head cube")

    def test_wings_follow_the_chest_only_and_the_glyph_the_head(self):
        for i in self.verts(self.WING + ("harness", "harness_gold")):
            self.assertEqual(self.built.weights[i], {"Chest": 1.0})
        for i in self.verts(["glyph"]):
            self.assertEqual(self.built.weights[i], {"Head": 1.0})
        m2, _p = self.bn.build_model({"parts": ["nt_wings"], "wing_weight": "shoulder"})
        b2 = m2.build()
        arm = {j for w in b2.weights for j in w if j.startswith("Arm_")}
        self.assertTrue(arm, "wing_weight=shoulder blends the roots into the arms")

    def arm_box(self):
        pts = [self.built.positions[i] for f, t in zip(self.built.faces, self.built.tags) if t == "arm" and self.built.positions[f[0]][0] > 0 for i in f]
        return [min(p[k] for p in pts) for k in range(3)], [max(p[k] for p in pts) for k in range(3)]

    def swung_inside(self, groups, degrees):
        """Samples (vertices + face centres, character's right side) of `groups` inside the right arm's bounding box swung back by `degrees`
        about the shoulder joint (conservative: the box is bigger than the sleeve)."""
        lo, hi = self.arm_box()
        piv = self.nc.J["Arm_R"]
        inv = K.euler_matrix(degrees, 0.0, 0.0)
        gi = {self.model.groups.index(g) for g in groups}
        n = 0
        for f, g in zip(self.built.faces, self.built.groups):
            if g not in gi:
                continue
            pts = [self.built.positions[i] for i in f]
            for p in pts + [K.centroid(pts)]:
                if p[0] <= 0:
                    continue
                d = K.vsub(p, piv)
                q = (piv[0] + K.vdot(inv[0], d), piv[1] + K.vdot(inv[1], d), piv[2] + K.vdot(inv[2], d))
                n += all(lo[k] + 1e-6 < q[k] < hi[k] - 1e-6 for k in range(3))
        return n

    def test_wings_clear_the_arms_at_rest_and_swung_back_30_degrees(self):
        for deg in (0, 10, 20, 30):
            self.assertEqual(self.swung_inside(self.WING, deg), 0, "wing planks inside the arm swung back %d degrees" % deg)
        for deg in (0, 10):
            self.assertEqual(self.swung_inside(("harness", "harness_gold"), deg), 0, "harness inside the arm swung back %d degrees" % deg)

    def test_without_the_arm_pocket_the_wings_reach_into_the_swing(self):
        m2, _p = self.bn.build_model({"parts": ["nt_wings"], "wing_arm_clear": False})
        b2 = m2.build()
        self.assertGreater(b2.triangles(), 0)
        self.assertGreater(self.tris(self.WING), 0)

    # ----------------------------------------------------------------------------------------------------------- harness and glyph
    def test_harness_sits_on_the_shoulder_blades_and_reaches_the_leading_edges(self):
        for g in ("harness", "harness_gold"):
            self.assertGreater(self.tris([g]), 100, g)
        p = self.pos(("harness", "harness_gold"))
        self.assertLessEqual(max(abs(x) for x, _y, _z in p), 0.63)     # landmarks: |x| <= 0.575 (small ring ~0.6) ...
        self.assertGreater(max(abs(x) for x, _y, _z in p), 0.5)        # ... reaching the leading edge column
        self.assertGreater(min(z for _x, _y, z in p), 1.0)
        self.assertLess(max(z for _x, _y, z in p), 1.8)                 # yoke y 1.17-1.38, column top 1.72
        core = [q for q in self.pos(("harness",)) if abs(q[0]) < 0.06]
        self.assertTrue(core, "a central yoke plate")
        gold = self.pos(("harness_gold",))
        self.assertTrue(any(abs(x) < 0.12 for x, _y, _z in gold), "gold uprights / clasps beside the yoke")
        self.assertTrue(any(x > 0 for x, _y, _z in p) and any(x < 0 for x, _y, _z in p))

    def test_harness_is_clear_of_the_arms_and_the_ribbon_loops_at_rest(self):
        lo, hi = self.arm_box()
        for p in self.pos(("harness", "harness_gold")):
            self.assertFalse(abs(p[0]) > lo[0] and abs(p[0]) < hi[0] and lo[1] + 1e-6 < p[1] < hi[1] - 1e-6 and lo[2] < p[2] < hi[2], p)

    def test_glyph_is_a_thin_square_tile_behind_left_of_the_head(self):
        lo, hi = self.nh.glyph_box()
        px = self.px
        self.assertAlmostEqual(hi[0] - lo[0], 7 * px, places=9)
        self.assertAlmostEqual(hi[2] - lo[2], 7 * px, places=9)
        self.assertAlmostEqual(hi[1] - lo[1], 1 * px, places=9)
        self.assertLess(hi[0], -0.1)                                  # the character's left: x -0.38 .. -0.16 in the sheet
        self.assertLessEqual(hi[2], self.nh_bun_top() + 0.05)           # landmarks: the tile top is 0.01 above the bun top
        self.assertGreater(hi[2], self.nh_bun_top() - 0.1)
        self.assertGreater(lo[2], self.nc.HEAD_BOX[4] - 0.1)
        p = self.pos(["glyph"])
        self.assertEqual(len(p), 8)
        faces = [f for f, g in zip(self.built.faces, self.built.groups) if self.model.groups[g] == "glyph"]
        big = [f for f in faces if abs(K.vnorm(K.newell([self.built.positions[i] for i in f]))[1]) > 0.9]
        self.assertEqual(len(big), 2)

    # ----------------------------------------------------------------------------------------------------------- UV and parameters
    def test_uniform_texel_density_on_the_new_groups(self):
        base = self.params["texel_density"] / self.nc.BLOCK
        for g in self.WING + ("harness", "harness_gold", "glyph"):
            area = self.built.density_report[g]["area"]
            self.assertAlmostEqual(area / base, 1.0, delta=0.08, msg=g)

    def test_no_sub_texel_slivers_and_uvs_inside_the_atlas(self):
        a = self.built.atlas
        gi = {self.model.groups.index(g) for g in self.WING + ("harness", "harness_gold", "glyph")}
        for f, uv, g in zip(self.built.faces, self.built.uvs, self.built.groups):
            if g not in gi:
                continue
            self.assertLessEqual(len(f), 4)
            for u, v in uv:
                self.assertTrue(0.0 <= u <= 1.0 and 0.0 <= v <= 1.0)
            for k in range(1, len(f) - 1):
                q = (uv[0], uv[k], uv[k + 1])
                area = abs((q[1][0] - q[0][0]) * (q[2][1] - q[0][1]) - (q[2][0] - q[0][0]) * (q[1][1] - q[0][1])) / 2.0 * a * a
                self.assertGreater(area, 0.01)

    def test_parameters_switch_parts_off(self):
        def groups(over):
            m, _p = self.bn.build_model(dict({"parts": ["nt_wings", "nt_harness"]}, **over))
            return {m.groups[g] for g in m.build().groups}
        self.assertFalse(set(groups({"wings": False})) & set(self.WING + ("harness", "harness_gold")))
        g = groups({"harness": False})
        self.assertTrue(set(self.WING) <= g and "harness" not in g and "harness_gold" not in g and "glyph" in g)
        g = groups({"glyph": False})
        self.assertTrue("glyph" not in g and "harness" in g)
        m, _p = self.bn.build_model({"parts": ["nt_wings"], "wing_density": 2.0})
        dens = m.build().density_report["wing_a"]["area"]
        self.assertGreater(dens, 1.8 * self.params["texel_density"] / self.nc.BLOCK)


try:
    from bossforge_core import blender as _blender
    BLENDER = _blender.find_blender(None)
except Exception:  # pragma: no cover - blender module missing in odd environments
    BLENDER = None


@unittest.skipUnless(BLENDER, "no Blender executable")
class NightingaleBuild(unittest.TestCase):
    """Builds a reduced Nightingale (body + extras) in headless Blender, exports the EF bundle and validates it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nightingale-test-"))
        args = {"out": str(cls.tmp), "id": "ngtest", "checks": True, "params": {"parts": ["nt_extras"], "atlas": 512}}
        script = EXAMPLE / "build_nightingale.py"
        res = _blender.run(script, args, timeout=300)
        cls.res = res

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_build_ok_and_budget(self):
        self.assertTrue(self.res["ok"], self.res)
        self.assertLessEqual(self.res["triangles"], 40000)
        self.assertEqual(self.res["texel_scale"], 1.0)
        self.assertEqual(self.res["texel_density"], 16.0)
        self.assertAlmostEqual(self.res["texels_per_model_block"], 30.769, places=2)
        self.assertEqual(self.res["weights"]["unweighted"], 0)
        self.assertLessEqual(self.res["weights"]["max_influences"], 3)
        self.assertAlmostEqual(self.res["weights"]["min_total"], 1.0, places=4)

    def test_bundle_is_a_valid_ef_mesh(self):
        from bossforge_core import efmodel
        mesh = json.loads((self.tmp / "ngtest.efmesh.json").read_text())
        meta = json.loads((self.tmp / "ngtest.meshmeta.json").read_text())
        self.assertEqual(efmodel.check_mesh_json(mesh), [])
        v = mesh["vertices"]
        self.assertEqual(len(v["vcounts"]["array"]), len(v["positions"]["array"]) // 3)
        self.assertTrue(all(c >= 1 for c in v["vcounts"]["array"]), "a vertex without weights")
        self.assertEqual(meta["schema"], "bossforge-mesh/1")
        for g in ("skin", "robe", "skirt", "boots"):
            self.assertIn(g, meta["groups"])
        us = v["uvs"]["array"]
        self.assertTrue(all(-1e-6 <= x <= 1 + 1e-6 for x in us))
        self.assertGreater(meta["height"], 1.5)
        self.assertEqual(sorted(mesh["vertices"]["parts"]), sorted(efmodel.EF_PARTS))

    def test_saved_blend_exists(self):
        self.assertTrue((self.tmp / "ngtest.blend").is_file())

    def test_skinning_totals_and_leg_poke_through(self):
        self.assertAlmostEqual(self.res["bundle_weights"]["min_total"], 1.0, places=3)
        self.assertEqual(self.res["bundle_weights"]["without_weights"], 0)
        for pose, v in self.res["poke_through"].items():
            self.assertEqual(v["legs"]["inside"], 0, "cloth inside a leg at pose %s" % pose)
        self.assertIn("legs_split_60", self.res["poke_through"])


@unittest.skipUnless(BLENDER, "no Blender executable")
class NightingaleWingsHarnessBuild(unittest.TestCase):
    """Body + wings + harness + glyph through headless Blender: EF bundle valid, every weight total 1, the new groups exported, no
    wing / harness feather inside an arm or leg at rest, a thin plank keeps its two big faces after the export."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nightingale-wings-test-"))
        args = {"out": str(cls.tmp), "id": "ngwings", "checks": True, "params": {"parts": ["nt_wings", "nt_harness"], "atlas": 512}}
        cls.res = _blender.run(EXAMPLE / "build_nightingale.py", args, timeout=300)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_bundle_is_valid_and_exports_the_new_groups(self):
        from bossforge_core import efmodel
        self.assertTrue(self.res["ok"], self.res)
        mesh = json.loads((self.tmp / "ngwings.efmesh.json").read_text())
        meta = json.loads((self.tmp / "ngwings.meshmeta.json").read_text())
        self.assertEqual(efmodel.check_mesh_json(mesh), [])
        for g in ("wing_a", "wing_b", "wing_c", "wing_d", "harness", "harness_gold", "glyph"):
            self.assertIn(g, meta["groups"])
        self.assertEqual(self.res["texel_scale"], 1.0)
        self.assertLessEqual(self.res["triangles"], 40000)

    def test_every_weight_total_is_one_with_at_most_three_influences(self):
        self.assertEqual(self.res["weights"]["unweighted"], 0)
        self.assertLessEqual(self.res["weights"]["max_influences"], 3)
        bw = self.res["bundle_weights"]
        self.assertEqual(bw["without_weights"], 0)
        self.assertAlmostEqual(bw["min_total"], 1.0, places=6)
        self.assertAlmostEqual(bw["max_total"], 1.0, places=6)

    def test_nothing_of_the_back_rig_pokes_through_the_body_at_rest(self):
        for pose in ("rest", "right_leg_back_60", "head_turn_40"):
            r = self.res["poke_through"][pose]
            self.assertEqual(r["arms"]["inside"], 0, pose)
            self.assertEqual(r["legs"]["inside"], 0, pose)


if __name__ == "__main__":
    unittest.main()
