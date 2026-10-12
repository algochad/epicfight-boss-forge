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
        self.assertEqual(b.atlas, 1024)

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


if __name__ == "__main__":
    unittest.main()
