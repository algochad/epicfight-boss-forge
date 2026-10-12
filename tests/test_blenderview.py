"""`bossforge blender view`: argument parsing, pose frames, EF animation maths (efanim), compositing / montage layout
(stdlib, always run) and the headless Blender renders, pose conversion proof and reference alignment (skipped without
Blender / the Epic Fight jar)."""
import _path  # noqa
import ast
import json
import math
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from bossforge_core import blender, blenderview as bv, cli, efanim, efmodel, reference, shape
from bossforge_core import spec as S
from bossforge_core.pngio import Canvas, read_png, write_png
from bossforge_core.spec import BossForgeError

from _path import SCRIPTS, SHIPPED_EXAMPLE

BLENDER = blender.find_blender()
needs_blender = unittest.skipUnless(BLENDER, "Blender not found")
EF_JAR = efanim.find_jar()
CACHE = Path(os.path.expanduser("~/.cache/epicfight-boss-forge"))
WALK_CACHED = next(iter(CACHE.glob("ef/*/animations/biped/living/walk.json")), None)
needs_walk = unittest.skipUnless(EF_JAR or WALK_CACHED, "no Epic Fight jar / cached walk animation")


def small_armature():
    """A 3 joint chain in Blender space: Root (rest at z=1), Torso 1 above, Head 1 above that (bone Y along world Z)."""
    up = (1.0, 0.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)  # Y axis -> +Z
    child = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    return efanim.Armature(["Root", "Torso", "Head"], {"Root": None, "Torso": "Root", "Head": "Torso"},
                           {"Root": up, "Torso": child, "Head": child})


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class ArgumentTests(unittest.TestCase):
    def test_pose_forms(self):
        self.assertEqual(bv.parse_pose("arm_r=60,arm_l=-30,thigh_r=40"),
                         [("Arm_R", "X", 60.0), ("Arm_L", "X", -30.0), ("Thigh_R", "X", 40.0)])
        self.assertEqual(bv.parse_pose("head=z:40, THIGH_L = y:-15.5"), [("Head", "Z", 40.0), ("Thigh_L", "Y", -15.5)])
        self.assertEqual(bv.parse_pose(""), [])
        self.assertEqual(bv.parse_pose(None), [])

    def test_pose_names_resolve_to_ef_joints(self):
        for name, joint in (("forearm_r", "Hand_R"), ("shin_l", "Leg_L"), ("hand_l", "Tool_L"), ("torso", "Torso"), ("Knee_R", "Knee_R")):
            self.assertEqual(bv.parse_pose("%s=10" % name)[0][0], joint)

    def test_pose_errors(self):
        for bad in ("arm_r", "arm_r=fast", "wing=10", "arm_r=w:10", "arm_r=1000"):
            with self.assertRaises(BossForgeError, msg=bad) as cm:
                bv.parse_pose(bad)
            self.assertIn(cm.exception.err.code, ("bad_pose", "unknown_bone"))

    def test_times_colors_lists(self):
        self.assertEqual(bv.parse_times("0, 0.25,1"), [0.0, 0.25, 1.0])
        self.assertEqual(bv.parse_times(None), [0.0])
        for bad in ("a", "-1", ",".join(["0"] * 13)):
            with self.assertRaises(BossForgeError):
                bv.parse_times(bad)
        self.assertEqual(bv.parse_color("#1f2430"), (0x1f, 0x24, 0x30))
        self.assertEqual(bv.parse_color("fff"), (255, 255, 255))
        with self.assertRaises(BossForgeError):
            bv.parse_color("#12345")
        self.assertEqual(bv.parse_list("front,back,front", bv.VIEW_NAMES, "/views", ()), ["front", "back"])
        with self.assertRaises(BossForgeError):
            bv.parse_list("front,sideways", bv.VIEW_NAMES, "/views", ())

    def test_describe_pose(self):
        self.assertEqual(bv.describe_pose([]), "rest")
        self.assertEqual(bv.describe_pose([("Arm_R", "X", 60.0)]), "arm_r x60")

    def test_view_names_match_blender_module(self):
        """bfk/view.py cannot be imported without bpy: read its VIEW_ANGLES literal."""
        tree = ast.parse((SCRIPTS / "blender" / "bfk" / "view.py").read_text("utf-8"))
        names = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "VIEW_ANGLES" for t in node.targets):
                names = {k.value for k in node.value.keys}
        self.assertEqual(names, set(bv.VIEW_NAMES))

    def test_cli_wiring(self):
        args = cli.build_parser().parse_args(["blender", "view", "boss.json", "--out", "o", "--pose", "arm_r=60", "--pose-sheet",
                                              "--anim", "epicfight:biped/living/walk", "--at", "0,0.2", "--engine", "eevee", "--size", "320",
                                              "--reference-views", "--bg", "#102030"])
        self.assertIs(args.fn, bv.run_view)
        o = bv.options_from_args(args)
        self.assertEqual(o["pose_ops"], [("Arm_R", "X", 60.0)])
        self.assertEqual(o["times"], [0.0, 0.2])
        self.assertTrue(o["pose_sheet"] and o["reference_views"])
        self.assertEqual((o["engine"], o["size"], o["bg"]), ("eevee", 320, "#102030"))
        self.assertEqual(o["views"], list(bv.DEFAULT_VIEWS))

    def test_missing_source_is_a_clean_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(BossForgeError) as cm:
                bv.run_pipeline(os.path.join(d, "nope.json"), d, {}, d)
            self.assertEqual(cm.exception.err.code, "file_not_found")

    def test_options_validation(self):
        for bad in ({"views": ["sideways"]}, {"engine": "cycles"}, {"size": 8}, {"bg": "red"}):
            with self.assertRaises(BossForgeError):
                bv.normalize_options(bad)


class PlanTests(unittest.TestCase):
    def test_pose_sheet_frames(self):
        fr = bv.plan_frames(["front", "side"], [], True, ["front", "3q", "side"], 640, 360, [], "")
        self.assertEqual([f["id"] for f in fr], ["main"] + ["ps_" + p[0] for p in bv.POSE_SHEET])
        self.assertEqual(fr[0]["views"], ["front", "side"])
        self.assertEqual(fr[0]["size"], 640)
        self.assertTrue(all(f["size"] == 360 and f["views"] == ["front", "3q", "side"] for f in fr[1:]))
        ids = {p[0] for p in bv.POSE_SHEET}
        self.assertEqual(ids, {"neutral", "arms_fwd", "arms_back", "walk", "head_turn", "torso_bend"})
        walk = next(p for p in bv.POSE_SHEET if p[0] == "walk")[2]
        self.assertEqual(walk, (("Thigh_R", "X", 35.0), ("Thigh_L", "X", -35.0)))

    def test_custom_pose_and_animation_frames(self):
        ops = [("Arm_R", "X", 60.0)]
        anim = [{"t": 0.0, "deltas": {"Root": efanim.IDENT}}, {"t": 0.2, "deltas": {"Root": efanim.IDENT}}]
        fr = bv.plan_frames(["front"], ops, True, ["front"], 640, 360, anim, "walk", ["ref_front"])
        self.assertEqual(fr[0]["ops"], [["Arm_R", "X", 60.0]])
        self.assertEqual(fr[0]["views"], ["front", "ref_front"])
        self.assertEqual(fr[0]["label"], "arm_r x60")
        self.assertEqual([f["id"] for f in fr if f["group"] == "pose_sheet"][-1], "ps_custom")
        a = [f for f in fr if f["group"] == "anim"]
        self.assertEqual([f["label"] for f in a], ["walk t=0.00", "walk t=0.20"])
        self.assertEqual(len(a[0]["deltas"]["Root"]), 16)
        json.dumps(fr)  # must travel as JSON


class DrawingTests(unittest.TestCase):
    def test_font_covers_label_characters(self):
        for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.,:=+-/()_%|<>#? ":
            rows = bv._GLYPH_ROWS[ch]
            self.assertEqual(len(rows), bv.GLYPH_H, ch)
            self.assertTrue(all(len(r) == bv.GLYPH_W for r in rows), ch)

    def test_text_draws_inside_its_plate(self):
        c = bv.solid(80, 24, (0, 0, 0))
        bv.draw_text(c, 4, 4, "arm_r x60", 1, fg=(255, 255, 255), plate=None)
        w, h = bv.text_size("arm_r x60", 1)
        lit = [(x, y) for y in range(c.h) for x in range(c.w) if c.get(x, y)[0] == 255]
        self.assertGreater(len(lit), 30)
        self.assertTrue(all(4 <= x < 4 + w and 4 <= y < 4 + h for x, y in lit))

    def test_over_composites_straight_alpha(self):
        dst = bv.solid(4, 1, (10, 20, 30))
        src = Canvas(4, 1)
        src.set(1, 0, (255, 0, 0, 255))
        src.set(2, 0, (255, 0, 0, 128))
        bv.over(dst, src)
        self.assertEqual(dst.get(0, 0), (10, 20, 30, 255))
        self.assertEqual(dst.get(1, 0), (255, 0, 0, 255))
        r, g, b, a = dst.get(2, 0)
        self.assertTrue(abs(r - 133) <= 1 and abs(g - 10) <= 1 and abs(b - 15) <= 1 and a == 255)

    def test_lines_are_clipped_and_blended(self):
        c = bv.solid(32, 32, (0, 0, 0))
        bv.draw_line(c, -50, 10, 80, 10, (200, 200, 200), 0.5)  # horizontal, longer than the canvas
        self.assertEqual(c.get(0, 10)[:3], (100, 100, 100))
        self.assertEqual(c.get(31, 10)[:3], (100, 100, 100))
        self.assertEqual(c.get(5, 11)[:3], (0, 0, 0))
        bv.draw_line(c, 7, -9, 7, 90, (200, 200, 200), 1.0)  # vertical
        self.assertEqual(c.get(7, 0)[:3], (200, 200, 200))
        self.assertEqual(c.get(7, 31)[:3], (200, 200, 200))
        bv.draw_line(c, 0, 0, 31, 31, (255, 0, 0), 1.0)  # diagonal
        self.assertEqual(c.get(16, 16)[:3], (255, 0, 0))
        self.assertIsNone(bv._clip_line(-10, -10, -5, -5, 32, 32))

    def test_montage_layout(self):
        cells = [bv.solid(40, 30, (i * 40, 0, 0)) for i in range(5)]
        m = bv.montage(cells, 3, gap=6)
        self.assertEqual((m.w, m.h), (3 * 40 + 4 * 6, 2 * 30 + 3 * 6))
        self.assertEqual(m.get(6 + 5, 6 + 5)[:3], (0, 0, 0))
        self.assertEqual(m.get(6 + 40 + 6 + 5, 6 + 5)[:3], (40, 0, 0))
        self.assertEqual(m.get(6 + 5, 6 + 30 + 6 + 5)[:3], (120, 0, 0))
        self.assertEqual(m.get(6 + 40 + 6 + 40 + 6 + 5, 6 + 30 + 6 + 5)[:3], (12, 14, 20))  # no 6th cell: background
        titled = bv.montage(cells, 2, title="X")
        self.assertEqual(titled.h, 3 * 30 + 4 * 6 + 22)
        self.assertEqual(bv.montage(cells, 99).w, 5 * 40 + 6 * 6)

    def test_compose_view_cache_and_label(self):
        cam = bv.Cam(front_cam_meta(64, 4.0))
        raw = Canvas(64, 64)
        for y in range(20, 50):
            for x in range(20, 40):
                raw.set(x, y, (0, 255, 0, 255))
        cache = {}
        a = bv.compose_view(raw, cam, (30, 30, 40), "front", True, cache)
        b = bv.compose_view(raw, cam, (30, 30, 40), "front", True, cache)
        self.assertEqual(len(cache), 1)
        self.assertEqual(bytes(a.data), bytes(b.data))
        self.assertEqual(a.get(30, 40)[:3], (0, 255, 0))
        self.assertEqual(a.get(60, 3)[:3], (30, 30, 40))


def front_cam_meta(size, ortho_scale, persp=False):
    """What bfk.view.camera_meta reports for the front camera at (0, 40, 1) looking along -Y (image right = -X)."""
    # columns of matrix_world are the camera axes: right (-X), up (+Z), back (+Y); translation (0, 40, 1)
    m = [[-1.0, 0.0, 0.0, 0.0], [0.0, 0.0, 1.0, 40.0], [0.0, 1.0, 0.0, 1.0], [0.0, 0.0, 0.0, 1.0]]
    return {"view": "front", "type": "PERSP" if persp else "ORTHO", "matrix_world": m, "ortho_scale": ortho_scale, "lens": 50.0,
            "sensor_width": 36.0, "width": size, "height": size}


class ProjectionTests(unittest.TestCase):
    def test_ortho_front(self):
        cam = bv.Cam(front_cam_meta(100, 4.0))
        self.assertAlmostEqual(cam.px_per_block, 25.0)
        self.assertEqual(tuple(round(v, 6) for v in cam.project((0.0, 0.0, 1.0))), (50.0, 50.0))  # target = image centre
        x, y = cam.project((1.0, 5.0, 2.0))  # +X is image LEFT for a camera looking at the face
        self.assertAlmostEqual(x, 25.0)
        self.assertAlmostEqual(y, 25.0)  # one block up = 25 px up
        x, y = cam.project((-2.0, -9.0, 0.0))
        self.assertAlmostEqual(x, 100.0)
        self.assertAlmostEqual(y, 75.0)

    def test_perspective_shrinks_with_distance(self):
        cam = bv.Cam(front_cam_meta(100, 4.0, persp=True))
        near = cam.project((1.0, 30.0, 1.0))[0]
        far = cam.project((1.0, -30.0, 1.0))[0]
        self.assertLess(abs(far - 50.0), abs(near - 50.0))
        self.assertAlmostEqual(cam.project((0.0, 0.0, 1.0))[0], 50.0)

    def test_reference_cameras_follow_the_calibration(self):
        w, h = 200, 300
        v = reference.View.synthetic("front", bytearray(w * h * 4), bytearray(w * h), w, h, 100.0, 280.0, 90.0, (10, 20, 190, 290))
        c = bv.reference_cameras({"front": v}, ["front"])[0]
        self.assertEqual((c["width"], c["height"]), (180, 270))
        self.assertAlmostEqual(c["ortho_scale"], 1.8)  # 180 px / 100 px per block
        # crop centre (100, 155) -> model x = -(100 - 90)/100, y = (280 - 155)/100
        self.assertAlmostEqual(c["target"][0], -0.1)
        self.assertAlmostEqual(c["target"][2], 1.25)
        self.assertEqual((c["azimuth"], c["elevation"]), (0.0, 0.0))
        side = reference.View.synthetic("right", bytearray(w * h * 4), bytearray(w * h), w, h, 100.0, 280.0, 90.0, (0, 0, 200, 300))
        c = bv.reference_cameras({"right": side}, ["right"])[0]
        self.assertEqual(c["azimuth"], -90.0)
        # right view: px = cx - s*z  ->  z = -(px - cx)/s and storage y = -z
        self.assertAlmostEqual(c["target"][1], (100 - 90) / 100.0)

    def test_silhouette_metrics(self):
        a = bytes([255, 255, 0, 0, 255, 0])
        m = bytes([255, 0, 255, 0, 255, 0])
        r = bv.silhouette_metrics(a, m)
        self.assertEqual((r["render_px"], r["reference_px"], r["render_only_px"], r["reference_only_px"]), (3, 3, 1, 1))
        self.assertAlmostEqual(r["iou"], 0.5)


class EfAnimTests(unittest.TestCase):
    def test_matrix_helpers(self):
        m = efanim.mat_mul(efanim.rot_x(30), (1.0, 0, 0, 2.0, 0, 1.0, 0, -1.0, 0, 0, 1.0, 5.0, 0, 0, 0, 1.0))
        self.assertTrue(close(efanim.mat_mul(m, efanim.mat_inv(m)), efanim.IDENT, 1e-12))
        self.assertTrue(close(efanim.mat_mul(efanim.CORR, efanim.CORR_INV), efanim.IDENT, 1e-12))
        # CORR is the Blender (Z up, front +Y) -> Minecraft (Y up, front -Z) change of axes: (x, y, z) -> (x, z, -y)
        self.assertTrue(close(efanim.mat_apply(efanim.CORR, (1.0, 2.0, 3.0)), (1.0, 3.0, -2.0), 1e-12))
        self.assertTrue(close(efmodel.from_blender((1.0, 2.0, 3.0)), efanim.mat_apply(efanim.CORR, (1.0, 2.0, 3.0)), 1e-12))
        self.assertTrue(close(efanim.mat_apply(efanim.CORR_INV, (0.4, 1.2, -0.7)), efmodel.to_blender((0.4, 1.2, -0.7)), 1e-12))

    def test_quaternions(self):
        q = efanim.quat_from_matrix(efanim.rot_x(90))
        self.assertTrue(close(q, (math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)), 1e-12))
        for deg in (0, 17, 90, 179, -120):
            for rot in (efanim.rot_x(deg), efanim.mat_mul(efanim.rot_x(deg), efanim.CORR)):
                self.assertTrue(close(efanim.quat_to_matrix(efanim.quat_from_matrix(rot)), rot, 1e-9), deg)
        a, b = (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 1.0, 0.0)
        mid = efanim.quat_lerp(a, b, 0.5)
        self.assertTrue(close(mid, (0.0, 0.0, math.sqrt(0.5), math.sqrt(0.5)), 1e-12))
        # shortest path: q and -q are the same rotation
        self.assertTrue(close(efanim.quat_lerp(a, tuple(-c for c in a), 0.3), a, 1e-12))

    def test_joint_transform_roundtrip_and_scale(self):
        jt = efanim.JointTransform((1.0, 2.0, 3.0), efanim.quat_from_matrix(efanim.rot_x(40)), (1.0, 2.0, 1.0))
        back = efanim.JointTransform.from_matrix(jt.to_matrix())
        self.assertTrue(close(back.t + back.s, jt.t + jt.s, 1e-9))
        self.assertTrue(close(back.q, jt.q, 1e-9))

    def test_armature_rest_pose_is_identity(self):
        arm = small_armature()
        self.assertEqual(arm.root, "Root")
        self.assertEqual(arm.order, ["Root", "Torso", "Head"])
        for n, m in efanim.pose_matrices(arm, {}).items():
            self.assertTrue(close(m, efanim.IDENT, 1e-9), n)
        w = arm.bind_world_blender()
        self.assertTrue(close((w["Head"][3], w["Head"][7], w["Head"][11]), (0.0, 0.0, 3.0), 1e-9))  # 1 + 1 + 1 up
        self.assertTrue(close((w["Torso"][3], w["Torso"][7], w["Torso"][11]), (0.0, 0.0, 2.0), 1e-9))

    def test_clip_parse_pose_and_skinning(self):
        """A file that rotates the Torso 90 degrees about world X moves the Head vertices around the Torso joint."""
        arm = small_armature()
        wt = arm.bind_world_blender()["Torso"]  # Blender-space rest matrix of the Torso joint
        rot = efanim.mat_mul(efanim.mat_mul(efanim.mat_mul(efanim.mat_mul(  # animated world = Rx(90) about the joint head * rest
            (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1), (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 2.0, 0, 0, 0, 1)),
            efanim.rot_x(90)), (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, -2.0, 0, 0, 0, 1)), wt)
        # local json matrix of the Torso = inverse(parent animated world) * its animated world; the Root is not animated
        root_world = arm.bind_world_blender()["Root"]
        torso_local = efanim.mat_mul(efanim.mat_inv(root_world), rot)
        root_local = arm.local_json["Root"]
        raw = {"animation": [
            {"name": "Root", "time": [0.0, 1.0], "transform": [list(root_local), list(root_local)]},
            {"name": "Torso", "time": [0.0, 1.0], "transform": [list(arm.local_json["Torso"]), list(torso_local)]},
            {"name": "Coordless", "time": [0.0], "transform": [list(efanim.IDENT)]}]}
        clip = efanim.parse_clip(raw, arm, "test")
        self.assertEqual(set(clip.tracks), {"Root", "Torso"})  # unknown joints are skipped
        self.assertAlmostEqual(clip.duration, 1.0)
        # rest at t = 0, rotated at t = 1, halfway interpolates the rotation
        self.assertTrue(all(close(m, efanim.IDENT, 1e-6) for m in efanim.pose_matrices(arm, clip.sample(0.0)).values()))
        d1 = efanim.blender_deltas(arm, clip.sample(1.0))
        head_rest = (0.0, 0.0, 3.0)
        got = efanim.mat_apply(d1["Head"], head_rest)
        # Head vertex 1 above the torso joint (z = 2): rotating 90 degrees about X sends +Z to -Y
        self.assertTrue(close(got, (0.0, -1.0, 2.0), 1e-6), got)
        half = efanim.blender_deltas(arm, clip.sample(0.5))
        got = efanim.mat_apply(half["Head"], head_rest)
        self.assertTrue(close(got, (0.0, -math.sin(math.radians(45)), 2.0 + math.cos(math.radians(45))), 1e-6), got)
        # the Minecraft-space path with the CORR round trip gives the same vertices as the Blender-space deltas
        mc = efanim.pose_matrices(arm, clip.sample(1.0))
        pos = [(0.0, 0.0, 3.0), (0.2, 0.1, 0.5)]
        infl = [[(2, 1.0)], [(0, 0.25), (1, 0.75)]]
        a = efanim.skin_positions(pos, infl, arm.names, mc)
        b = efanim.skin_positions_world(pos, infl, arm.names, d1)
        for p, q in zip(a, b):
            self.assertTrue(close(p, q, 1e-9))

    def test_first_joint_rule_and_coord(self):
        """The CORR is applied to the first joint of the file that exists in the armature; a `Coord` entry clears that."""
        arm = small_armature()
        rl = arm.local_json["Root"]
        coordless = {"animation": [{"name": "Root", "time": [0.0], "transform": [list(rl)]}]}
        with_coord = {"animation": [{"name": "Coord", "time": [0.0], "transform": [list(efanim.IDENT)]},
                                    {"name": "Root", "time": [0.0], "transform": [list(rl)]}]}
        t_plain = efanim.parse_clip(coordless, arm).tracks["Root"][0][1]
        self.assertTrue(close(t_plain.to_matrix(), efanim.IDENT, 1e-9))
        t_coord = efanim.parse_clip(with_coord, arm).tracks["Root"][0][1]  # no CORR: L^-1 * M != identity
        self.assertFalse(close(t_coord.to_matrix(), efanim.IDENT, 1e-3))

    def test_sampling_follows_ef_search(self):
        jt = lambda x: efanim.JointTransform((x, 0.0, 0.0))
        clip = efanim.Clip("c", {"J": [(0.0, jt(0.0)), (1.0, jt(10.0)), (3.0, jt(30.0))]})
        for t, want in ((-0.5, 0.0 + 0.0), (0.0, 0.0), (0.5, 5.0), (1.0, 10.0), (2.0, 20.0), (3.0, 30.0), (9.0, 30.0)):
            if t >= 0:
                self.assertAlmostEqual(clip.sample(t)["J"].t[0], want, msg=str(t))

    def test_attribute_format_rotation_convention(self):
        """`rot` = Blender (w, x, y, z): EF negates the vector part and uses the transposed quaternion matrix, net = textbook rotation."""
        w, x = math.cos(math.radians(20)), math.sin(math.radians(20))  # 40 degrees about X
        arm = small_armature()
        raw = {"format": "attributes", "animation": [{"name": "Torso", "time": [0.0], "transform": [{"loc": [0, 0, 0], "rot": [w, x, 0, 0], "sca": [1, 1, 1]}]}]}
        t = efanim.parse_clip(raw, arm).tracks["Torso"][0][1]
        self.assertTrue(close(t.to_matrix(), efanim.rot_x(40), 1e-9))

    def test_keys(self):
        self.assertEqual(efanim.normalize_key("epicfight:biped/living/walk"), "biped/living/walk")
        self.assertEqual(efanim.normalize_key("biped/living/walk.json"), "biped/living/walk")
        for bad in ("../x", "epicfight:Biped/Walk", "other:biped/walk", "", "a//b"):
            with self.assertRaises(BossForgeError):
                efanim.normalize_key(bad)

    def test_animation_read_from_jar_and_cached(self):
        with tempfile.TemporaryDirectory() as d:
            jar = Path(d) / "epic-fight-test.jar"
            body = json.dumps({"animation": [{"name": "Root", "time": [0.0], "transform": [list(efanim.IDENT)]}]})
            with zipfile.ZipFile(jar, "w") as z:
                z.writestr("assets/epicfight/animmodels/animations/biped/living/walk.json", body)
            cache = Path(d) / "cache"
            p = efanim.animation_file("epicfight:biped/living/walk", cache, str(jar), offline=True)
            self.assertTrue(p.is_file())
            self.assertEqual(json.loads(p.read_text())["animation"][0]["name"], "Root")
            p2 = efanim.animation_file("biped/living/walk", cache, None, offline=True)  # now served from the cache
            self.assertEqual(p, p2)
            with self.assertRaises(BossForgeError) as cm:
                efanim.animation_file("biped/living/nope", cache, str(jar), offline=True)
            self.assertEqual(cm.exception.err.code, "animation_not_found")

    def test_used_positions_matches_importer_rule(self):
        mesh = {"vertices": {"parts": {"a": {"array": [0, 0, 0, 1, 0, 0, 2, 0, 0, 5, 0, 0, 5, 0, 0, 5, 0, 0]},
                                       "b": {"array": [3, 0, 0, 4, 0, 0, 0, 0, 0]}}}}
        self.assertEqual(bv.used_positions(mesh), [0, 1, 2, 3, 4])  # the (5, 5, 5) anchor triangle is degenerate


def _java_check():
    sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
    try:
        import efanim_java_check as chk
    except ImportError:  # pragma: no cover
        return None
    return chk if (EF_JAR and chk.java_commands() and chk.find_libs()) else None


JAVA_CHECK = _java_check()


@unittest.skipUnless(JAVA_CHECK, "needs a JDK, the Epic Fight jar and the gson/joml/guava/DFU/log4j/fastutil jars (~/.gradle cache)")
class RealEpicFightTests(unittest.TestCase):
    def test_port_matches_the_real_ef_classes(self):
        """efanim.pose_matrices vs EF's own JsonAssetLoader / TransformSheet / JointTransform / Joint code (tests/tools/efanim_java)."""
        res = JAVA_CHECK.run(EF_JAR, JAVA_CHECK.find_libs(), 10, 11)
        self.assertGreaterEqual(res["frames"], 40)
        self.assertLess(res["max_error"], 2e-3, res["worst"])


class RawLoadTests(unittest.TestCase):
    def test_load_raw_prefers_rgba_and_falls_back_to_png(self):
        with tempfile.TemporaryDirectory() as d:
            c = Canvas(3, 2)
            c.set(1, 1, (9, 8, 7, 255))
            png = Path(d) / "a.png"
            write_png(png, c)
            cam = {"width": 3, "height": 2}
            from_png = bv.load_raw({"file": str(png), "camera": cam})
            self.assertEqual(from_png.get(1, 1), (9, 8, 7, 255))
            rgba = Path(d) / "a.rgba"
            c2 = Canvas(3, 2)
            c2.set(0, 0, (1, 2, 3, 255))
            rgba.write_bytes(bytes(c2.data))
            self.assertEqual(bv.load_raw({"file": str(png), "rgba": str(rgba), "camera": cam}).get(0, 0), (1, 2, 3, 255))
            rgba.write_bytes(b"short")  # wrong size: ignored
            self.assertEqual(bv.load_raw({"file": str(png), "rgba": str(rgba), "camera": cam}).get(1, 1), (9, 8, 7, 255))


@needs_blender
class BlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        raw = S.load(SHIPPED_EXAMPLE)
        n, errs, _w = S.check(raw)
        assert not errs, errs
        model = shape.build_model(n)
        cls.bundle = root / "model" / "king_arthur.efmesh.json"
        cls.bundle.parent.mkdir()
        cls.bundle.write_text(efmodel.dumps(model.mesh_json))
        cls.cache = root / "cache"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def render(self, name, **opts):
        out = Path(self.tmp.name) / name
        return bv.render(self.bundle, None, out, cache_dir=self.cache, offline=True, **opts), out

    def test_render_views_montage_and_labels(self):
        res, out = self.render("views", views=["front", "side", "top", "closeup_head"], size=160, grid=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual(set(res["views"]), {"front", "side", "top", "closeup_head"})
        for p in res["views"].values():
            im = read_png(p)
            self.assertEqual((im.w, im.h), (160, 160))
            self.assertEqual(im.get(1, 150)[3], 255)
        self.assertTrue(Path(res["montage"]).is_file())
        self.assertTrue((out / "view.json").is_file())
        raw = read_png(out / "raw" / "main__front.png")
        self.assertEqual(raw.get(0, 0)[3], 0)  # the raw render is transparent around the model
        self.assertGreater(sum(1 for a in raw.data[3::4] if a), 3000)
        self.assertEqual(res["engine"].split("_")[-1], "WORKBENCH")
        self.assertEqual(res["rig"]["missing"], [])
        # framing: the character fits the picture and 1 block = ppb pixels in the ortho front camera
        cam = res["cameras"]["front"]
        self.assertTrue(cam["ortho"])
        bb = res["frames"][0]["bbox"]
        self.assertLess(bb["max"][2] - 0.0, cam["ortho_scale"])

    def test_pose_moves_the_arm(self):
        rest, _ = self.render("rest", views=["side"], size=160)
        posed, _ = self.render("posed", views=["side"], size=160, pose="arm_r=70,arm_l=70")
        a = read_png(rest["views"]["side"])
        b = read_png(posed["views"]["side"])
        diff = sum(1 for i in range(0, len(a.data), 4) if a.data[i:i + 3] != b.data[i:i + 3])
        self.assertGreater(diff, 1500)
        bb_rest, bb_posed = rest["frames"][0]["bbox"], posed["frames"][0]["bbox"]
        self.assertGreater(bb_posed["max"][1], bb_rest["max"][1] + 0.3)  # arms swung forward (+Y)

    def test_pose_sheet_and_bad_bone(self):
        res, out = self.render("sheet", views=["front"], size=96, sheet_size=96, pose_sheet=True, sheet_views=["front", "side"])
        self.assertTrue(res["ok"], res)
        sheet = read_png(res["pose_sheet"])
        self.assertGreaterEqual(sheet.w, 2 * 96)
        self.assertGreaterEqual(sheet.h, len(bv.POSE_SHEET) * 96)
        ids = [f["id"] for f in res["frames"]]
        self.assertEqual(ids, ["main"] + ["ps_" + p[0] for p in bv.POSE_SHEET])
        with self.assertRaises(BossForgeError):
            bv.render(self.bundle, None, Path(self.tmp.name) / "bad", pose="wing_r=10")

    def test_world_rotation_equals_deltas_equals_python_skinning(self):
        """`pose=arm_r=55` (rotation about the joint in world axes), the equivalent world-space delta matrices and a pure-Python
        linear blend skinning of the same JSON deform the mesh identically."""
        h = efmodel.to_blender(next(j["pos"] for j in S.data("rigs")["biped"]["joints"] if j["name"] == "Arm_R"))  # joint head, storage space
        t_in = (1, 0, 0, h[0], 0, 1, 0, h[1], 0, 0, 1, h[2], 0, 0, 0, 1)
        t_out = (1, 0, 0, -h[0], 0, 1, 0, -h[1], 0, 0, 1, -h[2], 0, 0, 0, 1)
        d = efanim.mat_mul(efanim.mat_mul(t_in, efanim.rot_x(55)), t_out)
        arm_chain = ("Arm_R", "Hand_R", "Tool_R", "Elbow_R")  # Shoulder_R is the parent: unaffected
        deltas = {n: list(d if n in arm_chain else efanim.IDENT) for n in bv.joint_names()}
        frames = [{"id": "ops", "label": "ops", "views": [], "size": 64, "ops": [["Arm_R", "X", 55.0]], "dump_positions": True},
                  {"id": "deltas", "label": "deltas", "views": [], "size": 64, "deltas": deltas, "dump_positions": True}]
        args = {"source": str(self.bundle), "texture": None, "out": str(Path(self.tmp.name) / "ops"), "frames": frames, "engine": "workbench", "size": 64}
        res = blender.run_task("view", args, timeout=300)
        self.assertTrue(res["ok"], res)
        by = {f["id"]: f["positions"] for f in res["frames"]}
        mesh = json.loads(self.bundle.read_text())
        pos, infl = efanim.mesh_influences(mesh)
        used = bv.used_positions(mesh)
        want = efanim.skin_positions_world(pos, infl, bv.joint_names(), {k: tuple(v) for k, v in deltas.items()})
        moved = 0.0
        for k, i in enumerate(used):
            self.assertLess(math.dist(by["ops"][k], by["deltas"][k]), 1e-5)
            self.assertLess(math.dist(by["ops"][k], want[i]), 1e-5)
            moved = max(moved, math.dist(by["ops"][k], pos[i]))
        self.assertGreater(moved, 0.3)  # the arm really moved

    @needs_walk
    def test_animation_matches_ef_skinning(self):
        """The proof: Blender's deformation through the converted EF joint matrices equals EF's linear blend skinning of the
        same JSON (pure Python port of the 20.14.17 code, itself checked against the real EF classes)."""
        res, out = self.render("anim", views=["front"], size=96, sheet_size=96, anim="epicfight:biped/living/walk",
                               times=[0.0, 0.1167, 0.25, 0.4], verify_anim=True, ef_jar=str(EF_JAR) if EF_JAR else None)
        self.assertTrue(res["ok"], res.get("errors"))
        v = res["verification"]
        self.assertLess(v["max_error"], 1e-3)
        self.assertEqual(len(v["frames"]), 4)
        self.assertGreater(max(f["max_vertex_displacement"] for f in v["frames"]), 0.1)  # the poses really move the mesh
        self.assertTrue(Path(res["anim_sheet"]).is_file())

    def test_reference_views_are_pixel_aligned(self):
        """Blender silhouettes through `reference_cameras` equal an independent rasterization with `View.project`."""
        mesh = json.loads(self.bundle.read_text())
        pos, _infl = efanim.mesh_influences(mesh)
        tris = []
        for arr in mesh["vertices"]["parts"].values():
            a = arr["array"]
            for k in range(len(a) // 9):
                t = [a[9 * k + 3 * c] for c in range(3)]
                if len(set(t)) == 3:
                    tris.append(t)
        W, H = 300, 330
        views = {}
        for name, (ppb, gy, cx, crop) in {"front": (110.0, 300.0, 150.0, (0, 0, W, H)), "right": (110.0, 295.0, 170.0, (10, 5, 280, 320))}.items():
            v0 = reference.View.synthetic(name, bytearray(W * H * 4), bytearray(W * H), W, H, ppb, gy, cx, crop)
            pts = [v0.project(*efmodel.from_blender(p))[:2] for p in pos]
            mask = bytearray(W * H)
            for t in tris:
                (x0, y0), (x1, y1), (x2, y2) = [pts[i] for i in t]
                den = (y1 - y2) * (x0 - x2) + (x2 - x1) * (y0 - y2)
                if abs(den) < 1e-12:
                    continue
                for y in range(max(0, int(min(y0, y1, y2))), min(H, int(max(y0, y1, y2)) + 1)):
                    for x in range(max(0, int(min(x0, x1, x2))), min(W, int(max(x0, x1, x2)) + 1)):
                        px, py = x + 0.5, y + 0.5
                        a = ((y1 - y2) * (px - x2) + (x2 - x1) * (py - y2)) / den
                        b = ((y2 - y0) * (px - x2) + (x0 - x2) * (py - y2)) / den
                        if a >= -1e-9 and b >= -1e-9 and 1 - a - b >= -1e-9:
                            mask[y * W + x] = 255
            views[name] = reference.View.synthetic(name, bytearray(b"\x80\x80\x80\xff" * (W * H)), mask, W, H, ppb, gy, cx, crop)
        cams = bv.reference_cameras(views, list(views))
        args = {"source": str(self.bundle), "texture": None, "out": str(Path(self.tmp.name) / "align"), "engine": "workbench", "light": "flat",
                "size": 128, "cameras": cams, "frames": [{"id": "main", "label": "x", "views": [c["name"] for c in cams], "size": 128, "ops": []}]}
        res = blender.run_task("view", args, timeout=300)
        self.assertTrue(res["ok"], res)
        for name, v in views.items():
            raw = bv.load_raw(res["frames"][0]["images"]["ref_" + name])
            met = bv.silhouette_metrics(bytes(raw.data[3::4]), bv._mask_crop(v))
            self.assertGreater(met["iou"], 0.995, (name, met))


def joint_names():
    return bv.joint_names()


if __name__ == "__main__":
    unittest.main()
