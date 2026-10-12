import _path  # noqa
import copy
import importlib
import itertools
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path

import bossforge_core
from bossforge_core import efassets, efmesh, efmodel, shape
from bossforge_core.spec import BossForgeError, Err, data

try:  # the real ShapeKit, if it exists in this checkout (used only by RealPresetsTests)
    REAL_PRESETS = importlib.import_module("bossforge_core.presets")
except ImportError:
    REAL_PRESETS = None

ANAT = data("rigs")["biped"]["anatomy"]
JOINTS = data("rigs")["biped"]["joints"]
EPS = 1e-6
TEXEL_TOL = 0.01  # uvs are rounded to 5 decimals, so rect edges are within ~0.001 texel of the grid


# --------------------------------------------------------------------------- a minimal ShapeKit stand-in
def _stub_presets():
    m = types.ModuleType("bossforge_core.presets")

    def head_box(props):
        return [b for b in shape.base_boxes(props) if b["id"] == "head"][0]

    def expand(att, props, anatomy):
        s = float(att.get("scale", 1.0))
        h = head_box(props)
        if att["preset"] == "horns":
            top = h["min"][1] + h["size"][1]
            return [{"id": att["id"] + "_r", "bone": "head", "min": [h["size"][0] * 0.2, top, -0.05 * s],
                     "size": [0.1 * s, 0.4 * s, 0.1 * s], "taper": [0.2, 0.2], "rotate": [0, 0, -20],
                     "mirror": True, "material": "bone", "color": att.get("color", "accent")}]
        if att["preset"] == "spikes":
            return [{"id": att["id"] + "_%d" % i, "bone": "chest", "min": [-0.05, 1.3 + 0.1 * i, 0.15],
                     "size": [0.1, 0.1, 0.2 * s]} for i in range(2)]
        raise BossForgeError("/preset", "unknown_preset", "no such preset")

    def validate_attachment(att):
        errs = []
        sc = att.get("scale", 1.0)
        if not isinstance(sc, (int, float)) or sc < 0.5 or sc > 2.5:
            errs.append(Err("/scale", "out_of_range", "scale must be within 0.5..2.5"))
        return errs

    silhouettes = {
        "plain": {"description": "plain", "archetype": "knight", "proportions": {}, "attachments": []},
        "brute": {"description": "brute", "archetype": "golem",
                  "proportions": {"shoulders": 1.4, "bulk": 1.2, "arm_thickness": 9.0, "head": 0.8},
                  "attachments": [{"id": "horns", "preset": "horns", "scale": 1.0},
                                  {"id": "crest", "preset": "spikes"}]},
    }
    m.PRESETS = {"horns": {"description": "horns", "params": {}}, "spikes": {"description": "spikes", "params": {}}}
    m.expand = expand
    m.validate_attachment = validate_attachment
    m.list_presets = lambda: ["horns", "spikes"]
    m.list_silhouettes = lambda: ["brute", "plain"]
    m.silhouette = lambda name: copy.deepcopy(silhouettes[name])
    m.default_silhouette = lambda archetype: "plain"
    return m


class StubPresets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._saved_mod = sys.modules.get("bossforge_core.presets")
        cls._saved_attr = getattr(bossforge_core, "presets", None)
        stub = _stub_presets()
        sys.modules["bossforge_core.presets"] = stub
        bossforge_core.presets = stub

    @classmethod
    def tearDownClass(cls):
        if cls._saved_mod is None:
            sys.modules.pop("bossforge_core.presets", None)
        else:
            sys.modules["bossforge_core.presets"] = cls._saved_mod
        if cls._saved_attr is None:
            if hasattr(bossforge_core, "presets"):
                del bossforge_core.presets
        else:
            bossforge_core.presets = cls._saved_attr


# --------------------------------------------------------------------------- helpers
def box(**kw):
    b = {"id": "b", "bone": "head", "min": [0.0, 2.0, 0.0], "size": [0.2, 0.2, 0.2]}
    b.update(kw)
    return b


def spec_of(shape_dict, id_="test_boss"):
    return {"id": id_, "mod_id": "roundtable", "shape": shape_dict}


def by_id(boxes):
    return {b["id"]: b for b in boxes}


def extents(corners):
    return [max(c[i] for c in corners) - min(c[i] for c in corners) for i in range(3)]


def codes(errs):
    return {(e.path, e.code) for e in errs}


def face_rects(model):
    """Pixel rect (x0, y0, x1, y1) per quad face, from the mesh (triangles come in pairs per face)."""
    W, H = model.atlas
    t = model.mesh.tris
    out = []
    for i in range(0, len(t), 2):
        a, b = t[i], t[i + 1]
        assert a.part == b.part
        uvs = [model.mesh.uvs[k] for k in set(a.t) | set(b.t)]
        xs = [u[0] * W for u in uvs]
        ys = [u[1] * H for u in uvs]
        out.append((a.part, min(xs), min(ys), max(xs), max(ys)))
    return out


def overlaps(a, b, gutter=1.0):
    g = gutter - TEXEL_TOL
    return a[1] < b[3] + g and b[1] < a[3] + g and a[2] < b[4] + g and b[2] < a[4] + g


def agree_fraction(positions, normals, tris):
    agree = tested = 0
    for t in tris:
        a, b, c = (positions[i] for i in t.p)
        g = efmesh.tri_normal(a, b, c)
        if abs(g[0]) + abs(g[1]) + abs(g[2]) < 1e-9:
            continue
        s = [sum(normals[i][k] for i in t.n) for k in range(3)]
        tested += 1
        agree += g[0] * s[0] + g[1] * s[1] + g[2] * s[2] > 0
    return agree / tested


# --------------------------------------------------------------------------- base body
class BaseBodyTests(unittest.TestCase):
    def setUp(self):
        self.boxes = by_id(shape.base_boxes())

    def test_fourteen_base_boxes_on_the_base_layer(self):
        self.assertEqual(set(self.boxes), {
            "pelvis", "abdomen", "chest", "head", "arm_r_upper", "arm_l_upper", "arm_r_lower", "arm_l_lower",
            "fist_r", "fist_l", "leg_r_upper", "leg_l_upper", "leg_r_lower", "leg_l_lower"})
        self.assertEqual({b["layer"] for b in self.boxes.values()}, {"base"})

    def test_default_dimensions_follow_rig_anatomy(self):
        b = self.boxes
        neck = ANAT["head_top_y"] - ANAT["head_size"]
        self.assertEqual(b["head"]["size"], [0.5, 0.5, 0.5])
        self.assertAlmostEqual(b["head"]["min"][1], neck)
        self.assertAlmostEqual(b["head"]["min"][1] + 0.5, ANAT["head_top_y"])
        # torso 0.5 wide x 0.25 deep, hip joint to neck, split at the waist and chest joints
        self.assertAlmostEqual(b["pelvis"]["min"][1], ANAT["hip_y"])
        self.assertAlmostEqual(b["abdomen"]["min"][1], ANAT["waist_y"])
        self.assertAlmostEqual(b["chest"]["min"][1], ANAT["chest_y"])
        self.assertAlmostEqual(b["chest"]["min"][1] + b["chest"]["size"][1], neck)
        for n in ("pelvis", "abdomen", "chest"):
            self.assertAlmostEqual(b[n]["size"][0], 2 * ANAT["torso_half_width"])
            self.assertAlmostEqual(b[n]["size"][2], 0.25)
        # arms 0.25 x 0.25 centred on arm_x, upper|lower split at the elbow, fist at the wrist
        for side, sx in (("r", 1), ("l", -1)):
            up, lo, fist = b["arm_%s_upper" % side], b["arm_%s_lower" % side], b["fist_" + side]
            self.assertAlmostEqual(up["min"][0] + up["size"][0] / 2, sx * ANAT["arm_x"])
            self.assertEqual(up["size"][0], 0.25)
            self.assertAlmostEqual(up["min"][1], ANAT["elbow_y"])
            self.assertAlmostEqual(up["min"][1] + up["size"][1], neck)
            self.assertAlmostEqual(lo["min"][1], ANAT["wrist_y"])
            self.assertAlmostEqual(lo["min"][1] + lo["size"][1], ANAT["elbow_y"])
            self.assertAlmostEqual(fist["min"][1] + fist["size"][1], ANAT["wrist_y"])
            self.assertAlmostEqual(fist["min"][1], ANAT["hip_y"], delta=0.02)
        # legs 0.25 x 0.25 at +-leg_x, split at the knee, feet on y=0
        for side, sx in (("r", 1), ("l", -1)):
            up, lo = b["leg_%s_upper" % side], b["leg_%s_lower" % side]
            self.assertAlmostEqual(up["min"][0] + up["size"][0] / 2, sx * ANAT["leg_x"])
            self.assertEqual(up["size"][2], 0.25)
            self.assertAlmostEqual(up["min"][1], ANAT["knee_y"])
            self.assertAlmostEqual(up["min"][1] + up["size"][1], ANAT["hip_y"])
            self.assertEqual(lo["min"][1], 0.0)
            self.assertAlmostEqual(lo["min"][1] + lo["size"][1], ANAT["knee_y"])

    def test_bones_follow_the_joint_each_box_hangs_from(self):
        b = self.boxes
        self.assertEqual({k: b[k]["bone"] for k in ("pelvis", "abdomen", "chest", "head")},
                         {"pelvis": "root", "abdomen": "torso", "chest": "chest", "head": "head"})
        self.assertEqual((b["arm_r_upper"]["bone"], b["arm_r_lower"]["bone"], b["fist_r"]["bone"]), ("arm_r", "forearm_r", "hand_r"))
        self.assertEqual((b["arm_l_upper"]["bone"], b["arm_l_lower"]["bone"], b["fist_l"]["bone"]), ("arm_l", "forearm_l", "hand_l"))
        self.assertEqual((b["leg_r_upper"]["bone"], b["leg_r_lower"]["bone"]), ("thigh_r", "shin_r"))
        self.assertEqual((b["leg_l_upper"]["bone"], b["leg_l_lower"]["bone"]), ("thigh_l", "shin_l"))

    def test_bones_table_matches_rigs_json(self):
        rig = data("rigs")["biped"]
        self.assertEqual(set(shape.BONES), set(rig["bones"]))
        for bone, info in shape.BONES.items():
            j = JOINTS[info["index"]]
            self.assertEqual(j["name"], rig["bones"][bone])
            self.assertEqual(info["joint"], j["name"])
            self.assertEqual(list(info["pivot"]), j["pos"])

    def test_default_silhouette_matches_the_stock_biped(self):
        cache = efassets.default_cache_dir()
        ref = data("versions")["epicfight"]["assets_ref"]
        path = cache / "ef" / ref[:8] / "biped.json"
        if not path.is_file():
            self.skipTest("EF biped mesh not cached")
        tpl = data("templates")["biped"]
        stock = efmesh.load_mesh(path, tpl)
        mine = {}
        for b in shape.base_boxes():
            region = shape.default_region(b)
            lo, hi = mine.setdefault(region, ([9.0] * 3, [-9.0] * 3))
            for c in shape.box_corners(b):
                for i in range(3):
                    lo[i], hi[i] = min(lo[i], c[i]), max(hi[i], c[i])
        for part, region in (("head", "head"), ("torso", "torso"), ("rightArm", "arm_r"), ("leftArm", "arm_l"),
                             ("rightLeg", "leg_r"), ("leftLeg", "leg_l")):
            lo, hi = stock.bounds([part])
            for i in range(3):
                self.assertAlmostEqual(mine[region][0][i], lo[i], delta=0.02, msg="%s min %d" % (part, i))
                self.assertAlmostEqual(mine[region][1][i], hi[i], delta=0.02, msg="%s max %d" % (part, i))


class ProportionTests(StubPresets):
    def test_head_scale_grows_the_head_and_the_total_height(self):
        base = shape.build_model(spec_of({}))
        big = shape.build_model(spec_of({"proportions": {"head": 2.0}}))
        self.assertAlmostEqual(base.height, 2.0)
        self.assertAlmostEqual(by_id(big.boxes)["head"]["size"][0], 1.0)
        self.assertAlmostEqual(by_id(big.boxes)["head"]["min"][1], by_id(base.boxes)["head"]["min"][1])
        self.assertAlmostEqual(big.height, 2.5)

    def test_torso_arm_and_leg_proportions_scale_the_expected_boxes(self):
        d = by_id(shape.base_boxes())
        # shoulders widen the chest only and push the arms outward
        s = by_id(shape.base_boxes({"shoulders": 1.5}))
        self.assertAlmostEqual(s["chest"]["size"][0], 0.75)
        self.assertAlmostEqual(s["abdomen"]["size"][0], 0.5)
        self.assertAlmostEqual(s["arm_r_upper"]["min"][0], 0.375)
        self.assertAlmostEqual(s["arm_l_upper"]["min"][0] + 0.25, -0.375)
        # torso_width scales pelvis, abdomen and chest; torso_depth scales their depth
        w = by_id(shape.base_boxes({"torso_width": 1.4, "torso_depth": 2.0}))
        for n in ("pelvis", "abdomen", "chest"):
            self.assertAlmostEqual(w[n]["size"][0], 0.7)
            self.assertAlmostEqual(w[n]["size"][2], 0.5)
        # torso_height stretches abdomen + chest and lifts the head
        t = by_id(shape.base_boxes({"torso_height": 1.4}))
        self.assertAlmostEqual(t["abdomen"]["size"][1], 0.3 * 1.4, delta=2e-3)
        self.assertGreater(t["head"]["min"][1], d["head"]["min"][1] + 0.25)
        self.assertAlmostEqual(t["arm_r_upper"]["min"][1] + t["arm_r_upper"]["size"][1], t["head"]["min"][1])
        # arm_thickness and arm_length
        a = by_id(shape.base_boxes({"arm_thickness": 2.0, "arm_length": 1.4}))
        self.assertAlmostEqual(a["arm_r_lower"]["size"][0], 0.5)
        self.assertAlmostEqual(a["arm_r_lower"]["size"][2], 0.5)
        self.assertLess(a["fist_r"]["min"][1], d["fist_r"]["min"][1] - 0.1)
        # fist: grows (outward, inner edge flush with the arm) with the multiplier
        f = by_id(shape.base_boxes({"fist": 2.0}))
        self.assertAlmostEqual(f["fist_r"]["size"][0], 0.5)
        self.assertAlmostEqual(f["fist_r"]["min"][0], d["fist_r"]["min"][0])
        self.assertAlmostEqual(f["fist_l"]["min"][0] + 0.5, d["fist_l"]["min"][0] + 0.25)
        self.assertGreater(f["fist_r"]["size"][1], d["fist_r"]["size"][1])
        # leg_thickness keeps the legs apart and the height unchanged
        l = by_id(shape.base_boxes({"leg_thickness": 2.5}))
        self.assertAlmostEqual(l["leg_r_upper"]["size"][0], 0.625)
        self.assertGreaterEqual(l["leg_r_upper"]["min"][0], -EPS)
        self.assertLessEqual(l["leg_l_upper"]["min"][0] + l["leg_l_upper"]["size"][0], EPS)
        # bulk multiplies widths and depths of torso, arms and legs but not the head
        bk = by_id(shape.base_boxes({"bulk": 1.5}))
        self.assertAlmostEqual(bk["chest"]["size"][0], 0.75)
        self.assertAlmostEqual(bk["arm_r_upper"]["size"][0], 0.375)
        self.assertAlmostEqual(bk["leg_r_lower"]["size"][2], 0.375)
        self.assertEqual(bk["head"]["size"], d["head"]["size"])

    def test_out_of_range_and_unknown_proportions_are_reported_with_pointers(self):
        for key, (lo, hi) in shape.PROPORTION_RANGES.items():
            for v in (lo - 0.01, hi + 0.01):
                errs = shape.validate_shape({"proportions": {key: v}})
                self.assertIn(("/proportions/" + key, "out_of_range"), codes(errs))
            self.assertEqual(shape.validate_shape({"proportions": {key: lo}}), [])
            self.assertEqual(shape.validate_shape({"proportions": {key: hi}}), [])
        self.assertIn(("/proportions/horns", "unknown_proportion"), codes(shape.validate_shape({"proportions": {"horns": 1.0}})))
        self.assertIn(("/proportions/head", "bad_type"), codes(shape.validate_shape({"proportions": {"head": "big"}})))
        with self.assertRaises(BossForgeError) as cm:
            shape.build_model(spec_of({"proportions": {"head": 5}}))
        self.assertEqual((cm.exception.err.path, cm.exception.err.code), ("/proportions/head", "out_of_range"))

    def test_effective_proportions_are_complete(self):
        boxes, props = shape.resolve({"proportions": {"head": 1.3}})
        self.assertEqual(set(props), set(shape.PROPORTION_RANGES))
        self.assertEqual(props["head"], 1.3)
        self.assertEqual(props["bulk"], 1.0)
        self.assertEqual(shape.default_proportions(), {k: 1.0 for k in shape.PROPORTION_RANGES})


# --------------------------------------------------------------------------- mirror / taper / rotate
class MirrorTests(StubPresets):
    def test_mirror_semantics(self):
        src = box(id="horn_r", bone="forearm_r", min=[0.3, 1.0, -0.1], size=[0.1, 0.4, 0.2], taper=[0.5, 0.25],
                  rotate=[10, 20, 30], pivot=[0.3, 1.0, 0.0], mirror=True, skip_faces=["left", "top"],
                  region="arm_r", material="bone", color="accent", accent="#102030")
        boxes = by_id(shape.resolve({"parts": [src]})[0])
        r, l = boxes["horn_r"], boxes["horn_l"]
        self.assertNotIn("mirror", r)
        self.assertNotIn("mirror", l)
        self.assertEqual((l["bone"], l["region"]), ("forearm_l", "arm_l"))
        self.assertAlmostEqual(l["min"][0], -(0.3 + 0.1))
        self.assertEqual(l["min"][1:], r["min"][1:])
        self.assertEqual(l["size"], r["size"])
        self.assertEqual(l["taper"], r["taper"])
        self.assertEqual(l["rotate"], [10.0, -20.0, -30.0])
        self.assertEqual(l["pivot"], [-0.3, 1.0, 0.0])
        self.assertEqual(set(l["skip_faces"]), {"right", "top"})
        self.assertEqual((l["material"], l["color"], l["accent"], l["layer"]), ("bone", "accent", "#102030", "overlay"))

    def test_mirror_ids(self):
        boxes = by_id(shape.resolve({"parts": [box(id="wing_l", mirror=True), box(id="crest", mirror=True, min=[0.5, 2, 0]),
                                                box(id="tusk_r", mirror=True, min=[-1, 2, 0])]})[0])
        self.assertIn("wing_r", boxes)
        self.assertIn("crest_m", boxes)
        self.assertIn("tusk_l", boxes)

    def test_mirrored_geometry_is_the_x_reflection(self):
        src = box(id="horn_r", bone="head", min=[0.15, 2.0, -0.05], size=[0.1, 0.5, 0.2], taper=[0.3, 0.6],
                  rotate=[20, 35, -40], mirror=True)
        boxes = by_id(shape.resolve({"parts": [src]})[0])
        a = [(-c[0], c[1], c[2]) for c in shape.box_corners(boxes["horn_r"])]
        b = shape.box_corners(boxes["horn_l"])
        for c in a:
            self.assertTrue(any(math.dist(c, d) < 1e-9 for d in b), c)
        # the copy is also a proper (non-inverted) box: outward normals still point out
        model = shape.build_model(spec_of({"parts": [src]}))
        self.assertGreaterEqual(agree_fraction(model.mesh.positions, model.mesh.normals, model.mesh.tris), 0.999)

    def test_bones_swap_sides(self):
        for a, b in (("arm_r", "arm_l"), ("forearm_r", "forearm_l"), ("hand_r", "hand_l"), ("thigh_r", "thigh_l"),
                     ("shin_r", "shin_l"), ("arm_l", "arm_r"), ("head", "head"), ("chest", "chest"), ("root", "root")):
            boxes = by_id(shape.resolve({"parts": [box(id="x_r", bone=a, mirror=True, min=[0.5, 1.0, 0.0])]})[0])
            self.assertEqual(boxes["x_l"]["bone"], b)

    def test_attachment_mirror_flags_are_resolved_to_explicit_boxes(self):
        boxes, _ = shape.resolve({"attachments": [{"id": "horns", "preset": "horns"}]})
        ids = [b["id"] for b in boxes]
        self.assertIn("horns_r", ids)
        self.assertIn("horns_l", ids)
        self.assertTrue(all("mirror" not in b for b in boxes))
        r, l = by_id(boxes)["horns_r"], by_id(boxes)["horns_l"]
        self.assertAlmostEqual(r["min"][0] + r["size"][0], -l["min"][0])


class GeometryTests(unittest.TestCase):
    def norm(self, **kw):
        kw.setdefault("id", "b")
        return by_id(shape.resolve({"parts": [box(**kw)]})[0])[kw["id"]]

    def test_taper_scales_the_top_face_about_the_vertical_axis(self):
        b = self.norm(min=[1.0, 0.0, 2.0], size=[0.4, 1.0, 0.6], taper=[0.5, 0.25])
        c = shape.box_corners(b)
        bottom = [p for p in c if abs(p[1]) < EPS]
        top = [p for p in c if abs(p[1] - 1.0) < EPS]
        self.assertEqual((len(bottom), len(top)), (4, 4))
        self.assertAlmostEqual(extents(bottom)[0], 0.4)
        self.assertAlmostEqual(extents(bottom)[2], 0.6)
        self.assertAlmostEqual(extents(top)[0], 0.2)
        self.assertAlmostEqual(extents(top)[2], 0.15)
        self.assertAlmostEqual(sum(p[0] for p in top) / 4, 1.2)  # still centred on the box's vertical axis
        self.assertAlmostEqual(sum(p[2] for p in top) / 4, 2.3)

    def test_quarter_turn_swaps_extents(self):
        size = [1.0, 2.0, 0.5]
        mn = [-0.5, 0.0, -0.25]
        for rot, expect in (([0, 0, 90], [2.0, 1.0, 0.5]), ([90, 0, 0], [1.0, 0.5, 2.0]), ([0, 90, 0], [0.5, 2.0, 1.0])):
            c = shape.box_corners(self.norm(min=mn, size=size, rotate=rot))
            for got, want in zip(extents(c), expect):
                self.assertAlmostEqual(got, want)
            # rotation about the (default) box centre keeps the centre
            for i in range(3):
                self.assertAlmostEqual((max(p[i] for p in c) + min(p[i] for p in c)) / 2, mn[i] + size[i] / 2)

    def test_rotation_is_rz_ry_rx_about_the_pivot(self):
        b = self.norm(min=[0, 0, 0], size=[1, 1, 1], pivot=[0, 0, 0], rotate=[90, 0, 90])
        c = shape.box_corners(b)
        # corner index = ix + 2*iy + 4*iz; X is applied first
        for got, want in ((c[2], (0, 0, 1)), (c[1], (0, 1, 0))):
            for g, w in zip(got, want):
                self.assertAlmostEqual(g, w)
        # explicit pivot: rotating 180 deg about the z axis through x=1 maps x -> 2-x, y -> -y
        b = self.norm(min=[0, 0, 0], size=[1, 1, 1], pivot=[1, 0, 0], rotate=[0, 0, 180])
        c = shape.box_corners(b)
        self.assertAlmostEqual(min(p[0] for p in c), 1.0)
        self.assertAlmostEqual(max(p[0] for p in c), 2.0)
        self.assertAlmostEqual(min(p[1] for p in c), -1.0)


# --------------------------------------------------------------------------- mesh + EF json
HORN_SHAPE = {
    "proportions": {"head": 1.3, "shoulders": 1.4, "fist": 1.8},
    "parts": [
        box(id="horn_r", bone="head", min=[0.12, 2.05, -0.05], size=[0.14, 0.5, 0.14], taper=[0.15, 0.15],
            rotate=[0, 0, -25], mirror=True, material="bone", color="#e8dcc0"),
        box(id="tail", bone="root", min=[-0.06, 0.5, 0.12], size=[0.12, 0.7, 0.12], taper=[0.3, 0.3], rotate=[60, 0, 0]),
        box(id="plate", bone="chest", min=[-0.3, 1.2, -0.2], size=[0.6, 0.3, 0.05], layer="base", region="torso"),
    ],
}


class MeshJsonTests(StubPresets):
    def setUp(self):
        self.model = shape.build_model(spec_of(HORN_SHAPE))
        self.vertices = self.model.mesh_json["vertices"]

    def test_check_mesh_json_is_clean(self):
        self.assertEqual(efmodel.check_mesh_json(self.model.mesh_json), [])
        self.assertEqual(efmodel.check_mesh_json(shape.build_model(spec_of({})).mesh_json), [])
        brute = shape.build_model(spec_of({"silhouette": "brute", "attachments": [{"id": "horns", "preset": "horns", "scale": 1.5}]}))
        self.assertEqual(efmodel.check_mesh_json(brute.mesh_json), [])

    def test_all_twelve_parts_with_non_empty_arrays(self):
        parts = self.vertices["parts"]
        self.assertEqual(set(parts), set(efmodel.EF_PARTS))
        self.assertEqual(len(parts), 12)
        for name, obj in parts.items():
            self.assertGreater(len(obj["array"]), 0, name)
            self.assertEqual(len(obj["array"]) % 9, 0, name)

    def test_boxes_land_in_the_part_of_their_layer_and_bone(self):
        parts = self.vertices["parts"]
        tris_of = {}
        for t in self.model.mesh.tris:
            tris_of.setdefault(t.part, 0)
            tris_of[t.part] += 1
        # real triangles per EF part == sum over the boxes mapped to it
        for name in efmodel.EF_PARTS:
            expect = sum(tris_of[b["id"]] for b in self.model.boxes if shape.ef_part(b) == name)
            got = len(parts[name]["array"]) // 9
            self.assertEqual(got, expect if expect else 1, name)
        self.assertEqual({b["id"]: shape.ef_part(b) for b in self.model.boxes if b["id"] in ("head", "horn_r", "horn_l", "tail", "plate", "fist_l", "leg_r_lower", "arm_r_upper")},
                         {"head": "head", "horn_r": "hat", "horn_l": "hat", "tail": "jacket", "plate": "torso",
                          "fist_l": "leftArm", "leg_r_lower": "rightLeg", "arm_r_upper": "rightArm"})

    def test_joint_ids_are_the_rig_indices_of_each_boxes_bone(self):
        v = self.vertices
        self.assertEqual(v["vcounts"]["array"], [1] * (len(v["positions"]["array"]) // 3))
        self.assertEqual(v["weights"]["array"], [1.0])
        pairs = v["vindices"]["array"]
        self.assertEqual(pairs[1::2], [0] * (len(pairs) // 2))
        joints = pairs[0::2]
        names = {j["index"]: j["name"] for j in JOINTS}
        expect = {"head": "Head", "tail": "Root", "plate": "Chest", "horn_l": "Head", "fist_r": "Tool_R",
                  "arm_l_lower": "Hand_L", "arm_r_upper": "Arm_R", "leg_l_lower": "Leg_L", "leg_r_upper": "Thigh_R",
                  "pelvis": "Root", "abdomen": "Torso"}
        for bi, b in enumerate(self.model.boxes):
            for j in joints[8 * bi:8 * bi + 8]:
                self.assertEqual(j, shape.BONES[b["bone"]]["index"])
                if b["id"] in expect:
                    self.assertEqual(names[j], expect[b["id"]])

    def test_positions_are_blender_z_up_and_round_trip_through_the_loader(self):
        m = self.model
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text(efmodel.dumps(m.mesh_json), encoding="utf-8")
            loaded = efmesh.load_mesh(p, m.tpl)
            raw = efmesh.read_raw(p)
        n = len(m.mesh.positions)
        for a, b in zip(loaded.positions[:n], m.mesh.positions):
            self.assertLess(math.dist(a, b), 1e-5)
        # stored as (x, -z, y)
        x, y, z = m.mesh.positions[0]
        self.assertEqual(raw["positions"]["array"][:3], [x, -z + 0.0, y])
        empty_parts = len(efmodel.EF_PARTS) - len({shape.ef_part(b) for b in m.boxes})
        self.assertEqual(len(loaded.tris), len(m.mesh.tris) + empty_parts)  # one anchor triangle per empty part
        self.assertGreaterEqual(agree_fraction(loaded.positions, loaded.normals, loaded.tris), 0.95)
        self.assertEqual(efmesh.frame(loaded, m.tpl)[0], -1)

    def test_validator_accepts_the_stock_biped_mesh(self):
        ref = data("versions")["epicfight"]["assets_ref"]
        path = efassets.default_cache_dir() / "ef" / ref[:8] / "biped.json"
        if not path.is_file():
            self.skipTest("EF biped mesh not cached")
        stock = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(efmodel.check_mesh_json({"vertices": stock["vertices"]}), [])

    def test_front_is_minus_z_and_the_right_arm_is_plus_x(self):
        m = shape.build_model(spec_of({}))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text(efmodel.dumps(m.mesh_json), encoding="utf-8")
            loaded = efmesh.load_mesh(p, m.tpl)
        self.assertEqual(efmesh.frame(loaded, m.tpl), (-1, 1))

    def test_empty_parts_get_a_degenerate_triangle_at_their_joint_pivot(self):
        m = shape.build_model(spec_of({}))  # base humanoid only: every overlay part is empty
        v = m.mesh_json["vertices"]
        pos = v["positions"]["array"]
        pairs = v["vindices"]["array"]
        for name, joint in (("hat", "Head"), ("jacket", "Chest"), ("rightSleeve", "Arm_R"), ("leftSleeve", "Arm_L"),
                            ("rightPants", "Thigh_R"), ("leftPants", "Thigh_L")):
            arr = v["parts"][name]["array"]
            self.assertEqual(len(arr), 9, name)
            self.assertEqual(arr[0], arr[3])
            self.assertEqual(arr[0], arr[6])
            p = arr[0]
            j = [x for x in JOINTS if x["name"] == joint][0]
            self.assertEqual(pairs[2 * p], j["index"])
            x, y, z = j["pos"]
            for got, want in zip(pos[3 * p:3 * p + 3], (x, -z, y)):
                self.assertAlmostEqual(got, want, places=4)
        self.assertEqual(efmodel.check_mesh_json(m.mesh_json), [])

    def test_floats_are_rounded_to_five_decimals_without_negative_zero(self):
        text = efmodel.dumps(self.model.mesh_json)
        self.assertNotIn("-0.0,", text)
        self.assertNotIn("-0.0]", text)
        self.assertNotIn(" ", text)
        for arr in (self.vertices["positions"]["array"], self.vertices["normals"]["array"], self.vertices["uvs"]["array"]):
            for x in arr:
                self.assertEqual(x, round(x, 5))

    def test_write_mesh_json_converts_axes_and_pads_missing_parts(self):
        tri = ((0, 0, 0), (1, 1, 0), (2, 2, 0))
        d = efmodel.write_mesh_json([(1.0, 2.0, 3.0), (2.0, 2.0, 3.0), (1.0, 3.0, 3.0)], [(0.0, 0.0, 1.0)],
                                    [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)], {"head": [tri]}, [9, 9, 9])
        v = d["vertices"]
        self.assertEqual(v["positions"]["array"][:9], [1.0, -3.0, 2.0, 2.0, -3.0, 2.0, 1.0, -3.0, 3.0])
        self.assertEqual(v["normals"]["array"][:3], [0.0, -1.0, 0.0])
        self.assertEqual(v["parts"]["head"]["array"], [0, 0, 0, 1, 1, 0, 2, 2, 0])
        self.assertEqual(len(v["parts"]), 12)
        self.assertEqual(efmodel.check_mesh_json(d), [])
        with self.assertRaises(BossForgeError):
            efmodel.write_mesh_json([(0, 0, 0)], [], [], {"tail": []}, [0])
        with self.assertRaises(BossForgeError):
            efmodel.write_mesh_json([(0, 0, 0)], [], [], {}, [0, 1])

    def test_check_mesh_json_reports_structural_problems(self):
        def broken(mutate):
            d = copy.deepcopy(self.model.mesh_json)
            mutate(d["vertices"])
            return efmodel.check_mesh_json(d)

        self.assertTrue(broken(lambda v: v["parts"].pop("hat")))
        self.assertTrue(broken(lambda v: v["parts"]["head"]["array"].__setitem__(0, 10 ** 6)))
        self.assertTrue(broken(lambda v: v["parts"]["head"]["array"].pop()))
        self.assertTrue(broken(lambda v: v["parts"]["head"].__setitem__("array", [])))
        self.assertTrue(broken(lambda v: v["vcounts"]["array"].pop()))
        self.assertTrue(broken(lambda v: v["vindices"]["array"].__setitem__(0, 99)))
        self.assertTrue(broken(lambda v: v["weights"]["array"].__setitem__(0, 0.5)))
        self.assertTrue(broken(lambda v: v["positions"]["array"].pop()))
        self.assertTrue(broken(lambda v: v.pop("uvs")))
        self.assertTrue(efmodel.check_mesh_json({}))

        def flip(v):
            for obj in v["parts"].values():
                a = obj["array"]
                for i in range(0, len(a), 9):
                    a[i:i + 3], a[i + 3:i + 6] = a[i + 3:i + 6], a[i:i + 3]
        errs = broken(flip)
        self.assertTrue(any("winding" in e for e in errs), errs)


class UvOrientationTests(unittest.TestCase):
    RULES = {  # outward normal -> (direction u grows along, direction v grows along)
        (0, 0, -1): ((-1, 0, 0), (0, -1, 0)),
        (0, 0, 1): ((1, 0, 0), (0, -1, 0)),
        (-1, 0, 0): ((0, 0, 1), (0, -1, 0)),
        (1, 0, 0): ((0, 0, -1), (0, -1, 0)),
        (0, 1, 0): ((1, 0, 0), (0, 0, 1)),
        (0, -1, 0): ((1, 0, 0), (0, 0, -1)),
    }

    def test_textures_read_upright_and_unmirrored_from_outside(self):
        m = shape.build_model(spec_of({}))
        seen = set()
        for t in m.mesh.tris:
            n = tuple(round(c) for c in m.mesh.normals[t.n[0]])
            seen.add(n)
            ud, vd = self.RULES[n]
            for i, j in itertools.combinations(range(3), 2):
                dp = [m.mesh.positions[t.p[i]][k] - m.mesh.positions[t.p[j]][k] for k in range(3)]
                du = m.mesh.uvs[t.t[i]][0] - m.mesh.uvs[t.t[j]][0]
                dv = m.mesh.uvs[t.t[i]][1] - m.mesh.uvs[t.t[j]][1]
                pu = sum(a * b for a, b in zip(dp, ud))
                pv = sum(a * b for a, b in zip(dp, vd))
                if abs(pu) > 1e-6 and abs(du) > 1e-9:
                    self.assertGreater(du * pu, 0, (t.part, n))
                if abs(pv) > 1e-6 and abs(dv) > 1e-9:
                    self.assertGreater(dv * pv, 0, (t.part, n))
        self.assertEqual(seen, set(self.RULES))


# --------------------------------------------------------------------------- atlas
class AtlasTests(StubPresets):
    def test_every_face_rect_is_inside_the_atlas_and_separated_by_a_gutter(self):
        for sh in ({}, HORN_SHAPE, {"silhouette": "brute", "attachments": [{"id": "horns", "preset": "horns"}]}):
            m = shape.build_model(spec_of(sh))
            W, H = m.atlas
            self.assertEqual(W, H)
            self.assertIn(W, (16, 32, 64, 128, 256, 512))
            rects = face_rects(m)
            self.assertEqual(len(rects), len(m.mesh.tris) // 2)
            for r in rects:
                self.assertGreaterEqual(r[1], -TEXEL_TOL)
                self.assertGreaterEqual(r[2], -TEXEL_TOL)
                self.assertLessEqual(r[3], W + TEXEL_TOL)
                self.assertLessEqual(r[4], H + TEXEL_TOL)
                self.assertGreaterEqual(r[3] - r[1], 2 - TEXEL_TOL)
                self.assertGreaterEqual(r[4] - r[2], 2 - TEXEL_TOL)
                for v in r[1:]:  # texel-aligned
                    self.assertAlmostEqual(v, round(v), delta=TEXEL_TOL)
            for a, b in itertools.combinations(rects, 2):
                self.assertFalse(overlaps(a, b), (a, b))
            self.assertEqual(list(m.atlas), m.tpl["authored_uv"])

    def test_density_sets_the_texel_size_and_box_density_multiplies_it(self):
        m = shape.build_model(spec_of({"texture": {"density": 16}, "parts": [box(id="cube", size=[0.5, 0.5, 0.5]),
                                                                          box(id="dense", size=[0.5, 0.5, 0.5], density=2.0, min=[1, 2, 0])]}))
        rects = {}
        for r in face_rects(m):
            rects.setdefault(r[0], (round(r[3] - r[1]), round(r[4] - r[2])))
        self.assertEqual(rects["head"], (8, 8))   # 0.5 block * 16 texels
        self.assertEqual(rects["cube"], (8, 8))
        self.assertEqual(rects["dense"], (16, 16))

    def test_tiny_faces_get_at_least_two_texels(self):
        m = shape.build_model(spec_of({"parts": [box(id="needle", size=[0.01, 0.01, 0.01])]}))
        for r in face_rects(m):
            if r[0] == "needle":
                self.assertGreaterEqual(r[3] - r[1], 2 - TEXEL_TOL)
                self.assertGreaterEqual(r[4] - r[2], 2 - TEXEL_TOL)

    def test_skip_faces_removes_those_faces(self):
        full = shape.build_model(spec_of({"parts": [box(id="cube")]}))
        cut = shape.build_model(spec_of({"parts": [box(id="cube", skip_faces=["bottom", "back"])]}))
        count = lambda m: sum(1 for t in m.mesh.tris if t.part == "cube") // 2
        self.assertEqual(count(full), 6)
        self.assertEqual(count(cut), 4)

    def test_density_is_halved_with_a_warning_when_the_atlas_would_exceed_512(self):
        parts = [box(id="slab_%d" % i, bone="chest", min=[-1.0, 0.0, -1.0], size=[2.0, 2.0, 2.0]) for i in range(20)]
        m = shape.build_model(spec_of({"parts": parts}))
        self.assertLessEqual(m.atlas[0], 512)
        self.assertEqual([e.code for e in m.warnings], ["density_reduced"])
        self.assertEqual(m.warnings[0].path, "/texture/density")
        width = [round(r[3] - r[1]) for r in face_rects(m) if r[0] == "slab_0"][0]
        self.assertEqual(width, 32)  # 2 blocks at the reduced density of 16
        self.assertEqual(efmodel.check_mesh_json(m.mesh_json), [])
        self.assertEqual(shape.build_model(spec_of({})).warnings, [])

    def test_texture_too_large_when_even_density_8_does_not_fit(self):
        parts = [box(id="slab_%d" % i, bone="chest", min=[-1.5, 0.0, -1.5], size=[3.0, 3.0, 3.0]) for i in range(100)]
        with self.assertRaises(BossForgeError) as cm:
            shape.build_model(spec_of({"parts": parts}))
        self.assertEqual(cm.exception.err.code, "texture_too_large")

    def test_pack_rects(self):
        side, pos = shape.pack_rects([(10, 10)] * 4)
        self.assertEqual(side, 32)
        self.assertEqual(len(set(pos)), 4)
        self.assertIsNone(shape.pack_rects([(600, 4)]))
        self.assertIsNone(shape.pack_rects([(300, 300)] * 4))


# --------------------------------------------------------------------------- template / outputs
class TemplateTests(StubPresets):
    def test_tpl_follows_the_contract(self):
        m = shape.build_model(spec_of(HORN_SHAPE))
        t = m.tpl
        for key, value in (("humanoid", True), ("mesh_name", None), ("model_id", "roundtable:entity/test_boss"),
                           ("armature_id", "epicfight:entity/biped"), ("renderer", "player"), ("overlay_parts", []),
                           ("overlay_to_base", True), ("uv_origin", "top"), ("front_sign", -1), ("right_sign", 1),
                           ("axis_mode", "x,z,-y"), ("unit_scale", 1.0), ("index_order", "pos,uv,nrm"),
                           ("weapon_capable", True), ("custom", True)):
            self.assertEqual(t[key], value, key)
        self.assertEqual(t["authored_uv"], list(m.atlas))
        self.assertEqual(t["model_height"], m.height)
        self.assertEqual(m.mesh.part_regions, t["part_regions"])
        self.assertEqual(set(t["part_regions"]), {b["id"] for b in m.boxes})

    def test_regions_and_styles(self):
        m = shape.build_model(spec_of(HORN_SHAPE))
        pr = m.tpl["part_regions"]
        self.assertEqual((pr["head"], pr["pelvis"], pr["chest"], pr["fist_l"], pr["arm_r_lower"], pr["leg_l_upper"], pr["leg_r_lower"]),
                         ("head", "torso", "torso", "arm_l", "arm_r", "leg_l", "leg_r"))
        self.assertEqual(pr["horn_r"], "box:horn_r")      # overlay, no region
        self.assertEqual(pr["horn_l"], "box:horn_l")
        self.assertEqual(pr["tail"], "box:tail")
        self.assertEqual(pr["plate"], "torso")             # explicit region wins
        m2 = shape.build_model(spec_of({"parts": [box(id="pad", bone="arm_r", region="arm_l"),
                                                   box(id="belt", bone="root", layer="base", min=[-0.3, 0.9, -0.2])]}))
        self.assertEqual(m2.tpl["part_regions"]["pad"], "arm_l")
        self.assertEqual(m2.tpl["part_regions"]["belt"], "torso")  # base boxes default to the bone's region
        style = m.tpl["part_style"]
        self.assertEqual(set(style), {"horn_r", "horn_l"})
        self.assertEqual(style["horn_r"]["material"], "bone")
        self.assertEqual(style["horn_l"]["color"], "#e8dcc0")
        self.assertIsNone(style["horn_r"]["accent"])

    def test_height_and_bounds_cover_every_vertex(self):
        m = shape.build_model(spec_of(HORN_SHAPE))
        (x0, y0, z0), (x1, y1, z1) = m.bounds
        pts = m.mesh.positions
        self.assertEqual((x0, y0, z0), (min(p[0] for p in pts), min(p[1] for p in pts), min(p[2] for p in pts)))
        self.assertEqual((x1, y1, z1), (max(p[0] for p in pts), max(p[1] for p in pts), max(p[2] for p in pts)))
        self.assertEqual(m.height, y1)
        self.assertEqual(shape.build_model(spec_of({})).height, ANAT["head_top_y"])
        self.assertGreater(m.height, 2.4)  # larger head + horns

    def test_deterministic(self):
        a = shape.build_model(spec_of(HORN_SHAPE))
        b = shape.build_model(spec_of(copy.deepcopy(HORN_SHAPE)))
        self.assertEqual(efmodel.dumps(a.mesh_json), efmodel.dumps(b.mesh_json))
        self.assertEqual(a.mesh.uvs, b.mesh.uvs)
        self.assertEqual(a.boxes, b.boxes)
        fa, _ = shape.generate(spec_of(HORN_SHAPE), None)
        fb, _ = shape.generate(spec_of(HORN_SHAPE), None)
        self.assertEqual(fa[0].data, fb[0].data)

    def test_generate_emits_one_compact_mesh_file(self):
        files, lang = shape.generate(spec_of(HORN_SHAPE), None)
        self.assertEqual(lang, {})
        self.assertEqual(len(files), 1)
        f = files[0]
        self.assertEqual(f.relpath, "src/main/resources/assets/roundtable/animmodels/entity/test_boss.json")
        self.assertEqual(f.kind, "json")
        text = f.data.decode("utf-8")
        self.assertNotIn("\n", text)
        self.assertNotIn(": ", text)
        d = json.loads(text)
        self.assertEqual(set(d["vertices"]), {"positions", "normals", "uvs", "vcounts", "weights", "vindices", "parts"})
        self.assertEqual(efmodel.check_mesh_json(d), [])

    def test_missing_shape_is_an_error(self):
        with self.assertRaises(BossForgeError) as cm:
            shape.build_model({"id": "x_boss", "mod_id": "m"})
        self.assertEqual(cm.exception.err.code, "required")


# --------------------------------------------------------------------------- silhouettes / attachments
class ResolveTests(StubPresets):
    def test_silhouette_proportions_are_overridden_by_the_shape_and_clamped(self):
        _, props = shape.resolve({"silhouette": "brute"})
        self.assertEqual((props["shoulders"], props["bulk"], props["head"]), (1.4, 1.2, 0.8))
        self.assertEqual(props["arm_thickness"], shape.PROPORTION_RANGES["arm_thickness"][1])  # 9.0 clamped
        _, props = shape.resolve({"silhouette": "brute", "proportions": {"shoulders": 1.1}})
        self.assertEqual((props["shoulders"], props["bulk"]), (1.1, 1.2))

    def test_attachments_remove_replace_and_append(self):
        boxes, _ = shape.resolve({
            "silhouette": "brute",
            "remove": ["crest"],
            "attachments": [{"id": "horns", "preset": "horns", "scale": 2.0}, {"id": "extra", "preset": "spikes"}],
            "parts": [box(id="orb", min=[0, 2.6, 0])],
        })
        ids = [b["id"] for b in boxes]
        self.assertFalse(any(i.startswith("crest") for i in ids))
        self.assertIn("extra_0", ids)
        self.assertIn("orb", ids)
        horn = by_id(boxes)["horns_r"]
        self.assertAlmostEqual(horn["size"][1], 0.8)  # the shape's scale=2.0 replaced the silhouette's horns
        self.assertEqual(ids.count("horns_r"), 1)
        # horns sit on the proportioned head (silhouette head 0.8 -> top at 1.5 + 0.4)
        self.assertAlmostEqual(horn["min"][1], 1.9)
        self.assertEqual(ids.index("extra_0") > ids.index("horns_r"), True)

    def test_attachments_see_the_effective_proportions(self):
        boxes, _ = shape.resolve({"proportions": {"head": 2.0}, "attachments": [{"id": "horns", "preset": "horns"}]})
        self.assertAlmostEqual(by_id(boxes)["horns_r"]["min"][1], 2.5)

    def test_parts_come_last_and_all_boxes_are_explicit(self):
        boxes, _ = shape.resolve({"attachments": [{"id": "horns", "preset": "horns"}], "parts": [box(id="orb")]})
        self.assertEqual(boxes[-1]["id"], "orb")
        for b in boxes:
            self.assertEqual(set(b), {"id", "bone", "min", "size", "taper", "rotate", "pivot", "material", "color", "accent", "region", "layer", "skip_faces", "density"})
        self.assertEqual(by_id(boxes)["orb"]["layer"], "overlay")
        self.assertEqual(by_id(boxes)["orb"]["pivot"], [0.1, 2.1, 0.1])


# --------------------------------------------------------------------------- validation
class ValidationTests(StubPresets):
    def part_errors(self, **kw):
        return codes(shape.validate_shape({"parts": [box(**kw)]}))

    def test_box_field_errors_have_json_pointers(self):
        cases = (
            ({"bone": "elbow"}, ("/parts/0/bone", "bad_bone")),
            ({"size": [0.0, 1, 1]}, ("/parts/0/size", "bad_size")),
            ({"size": [1, 1]}, ("/parts/0/size", "bad_size")),
            ({"size": [1, -1, 1]}, ("/parts/0/size", "bad_size")),
            ({"taper": [0.01, 1]}, ("/parts/0/taper", "bad_taper")),
            ({"taper": [1, 3.5]}, ("/parts/0/taper", "bad_taper")),
            ({"taper": [1]}, ("/parts/0/taper", "bad_taper")),
            ({"rotate": [1, 2]}, ("/parts/0/rotate", "bad_rotate")),
            ({"rotate": [1, 2, "x"]}, ("/parts/0/rotate", "bad_rotate")),
            ({"pivot": [0, 0]}, ("/parts/0/pivot", "bad_rotate")),
            ({"material": "marble"}, ("/parts/0/material", "bad_material")),
            ({"color": "#12"}, ("/parts/0/color", "bad_color")),
            ({"color": "teal"}, ("/parts/0/color", "bad_color")),
            ({"accent": 5}, ("/parts/0/accent", "bad_color")),
            ({"region": "tail"}, ("/parts/0/region", "bad_region")),
            ({"id": "Horn"}, ("/parts/0/id", "bad_id")),
            ({"id": "9horn"}, ("/parts/0/id", "bad_id")),
            ({"id": 5}, ("/parts/0/id", "bad_id")),
            ({"layer": "middle"}, ("/parts/0/layer", "bad_layer")),
            ({"skip_faces": ["north"]}, ("/parts/0/skip_faces", "bad_face")),
            ({"density": 9}, ("/parts/0/density", "out_of_range")),
            ({"colour": "primary"}, ("/parts/0/colour", "unknown_key")),
        )
        for kw, expected in cases:
            self.assertIn(expected, self.part_errors(**kw), kw)
        self.assertEqual(self.part_errors(), set())
        for ok in ({"taper": [0.05, 3.0]}, {"color": "primary", "accent": "#aabbcc", "material": "stone", "region": "arm_l"},
                   {"skip_faces": ["front", "left"], "density": 4}, {"region": None, "taper": None}):
            self.assertEqual(self.part_errors(**ok), set(), ok)

    def test_missing_required_box_fields(self):
        errs = codes(shape.validate_shape({"parts": [{}]}))
        for expected in (("/parts/0/id", "bad_id"), ("/parts/0/bone", "bad_bone"), ("/parts/0/min", "bad_min"), ("/parts/0/size", "bad_size")):
            self.assertIn(expected, errs)
        self.assertIn(("/parts/0", "bad_type"), codes(shape.validate_shape({"parts": [5]})))

    def test_duplicate_ids(self):
        self.assertIn(("/parts/1/id", "duplicate_box_id"), codes(shape.validate_shape({"parts": [box(id="orb"), box(id="orb")]})))
        self.assertIn(("/parts/0/id", "duplicate_box_id"), codes(shape.validate_shape({"parts": [box(id="head")]})))
        # a mirror copy that collides with an explicit box
        errs = shape.validate_shape({"parts": [box(id="horn_r", mirror=True, min=[0.5, 2, 0]), box(id="horn_l", min=[-0.5, 2, 0])]})
        self.assertIn(("/parts/1/id", "duplicate_box_id"), codes(errs))
        # attachment boxes colliding with a part
        errs = shape.validate_shape({"attachments": [{"id": "horns", "preset": "horns"}], "parts": [box(id="horns_r")]})
        self.assertIn(("/parts/0/id", "duplicate_box_id"), codes(errs))

    def test_too_many_boxes(self):
        parts = [box(id="p_%d" % i, min=[-0.5 + 0.02 * (i % 40), 0.5 + 0.05 * (i // 40), 0.0], size=[0.1, 0.1, 0.1]) for i in range(390)]
        errs = shape.validate_shape({"parts": parts})
        self.assertIn(("", "too_many_boxes"), codes(errs))
        self.assertEqual(shape.validate_shape({"parts": parts[:386]}), [])  # 14 base + 386 = 400
        self.assertEqual(shape.MAX_BOXES, 400)

    def test_out_of_bounds_uses_the_rotated_vertices(self):
        self.assertIn(("/parts/0", "out_of_bounds"), self.part_errors(min=[4.5, 1, 0]))
        self.assertIn(("/parts/0", "out_of_bounds"), self.part_errors(min=[0, 6.5, 0]))
        self.assertIn(("/parts/0", "out_of_bounds"), self.part_errors(min=[0, -1.5, 0]))
        self.assertIn(("/parts/0", "out_of_bounds"), self.part_errors(min=[0, 1, 4.2]))
        inside = dict(min=[-0.05, 0.5, -0.05], size=[0.1, 5.0, 0.1])
        self.assertEqual(self.part_errors(**inside), set())
        self.assertIn(("/parts/0", "out_of_bounds"), self.part_errors(pivot=[0, 0.5, 0], rotate=[0, 0, 90], **inside))
        self.assertEqual(self.part_errors(min=[3.9, 1, -3.9], size=[0.1, 5.0, 7.8]), set())

    def test_shape_level_errors(self):
        self.assertIn(("/rig", "bad_rig"), codes(shape.validate_shape({"rig": "quadruped"})))
        self.assertEqual(shape.validate_shape({"rig": "biped"}), [])
        errs = shape.validate_shape({"silhouette": "gargoyle"})
        self.assertEqual([(e.path, e.code) for e in errs], [("/silhouette", "unknown_silhouette")])
        self.assertIn("brute", errs[0].message)
        self.assertIn("plain", errs[0].message)
        errs = shape.validate_shape({"attachments": [{"id": "fx", "preset": "tentacles"}]})
        self.assertEqual([(e.path, e.code) for e in errs], [("/attachments/0/preset", "unknown_preset")])
        self.assertIn("horns", errs[0].message)
        self.assertIn(("/attachments/0/scale", "out_of_range"), codes(shape.validate_shape({"attachments": [{"id": "h", "preset": "horns", "scale": 9}]})))
        self.assertIn(("/attachments/0/id", "bad_id"), codes(shape.validate_shape({"attachments": [{"id": "Bad Id", "preset": "horns"}]})))
        self.assertIn(("/attachments/1/id", "duplicate_attachment_id"), codes(shape.validate_shape({"attachments": [{"id": "h", "preset": "horns"}, {"id": "h", "preset": "horns"}]})))
        self.assertIn(("/remove/0", "unknown_remove"), codes(shape.validate_shape({"silhouette": "brute", "remove": ["cape"]})))
        self.assertIn(("/colour", "unknown_key"), codes(shape.validate_shape({"colour": 1})))
        self.assertIn(("", "bad_type"), codes(shape.validate_shape([])))
        self.assertIn(("/parts", "bad_type"), codes(shape.validate_shape({"parts": {}})))

    def test_texture_density_range(self):
        for bad in (7.9, 64.1, 0, 128):
            self.assertIn(("/texture/density", "out_of_range"), codes(shape.validate_shape({"texture": {"density": bad}})), bad)
        for ok in (8, 32, 64):
            self.assertEqual(shape.validate_shape({"texture": {"density": ok}}), [])
        self.assertIn(("/texture/density", "bad_type"), codes(shape.validate_shape({"texture": {"density": "high"}})))

    def test_resolve_raises_the_first_error_and_exposes_all(self):
        with self.assertRaises(BossForgeError) as cm:
            shape.resolve({"parts": [box(bone="elbow"), box(id="orb", material="marble")]})
        self.assertEqual((cm.exception.err.path, cm.exception.err.code), ("/parts/0/bone", "bad_bone"))
        self.assertEqual({(e.path, e.code) for e in cm.exception.errors}, {("/parts/0/bone", "bad_bone"), ("/parts/1/material", "bad_material")})

    def test_validation_never_mutates_its_input(self):
        sh = copy.deepcopy(HORN_SHAPE)
        before = copy.deepcopy(sh)
        shape.validate_shape(sh)
        shape.build_model(spec_of(sh))
        self.assertEqual(sh, before)


# --------------------------------------------------------------------------- the real ShapeKit, when present
@unittest.skipUnless(REAL_PRESETS is not None, "presets.py not present")
class RealPresetsTests(unittest.TestCase):
    def test_every_silhouette_builds_a_valid_mesh(self):
        for name in REAL_PRESETS.list_silhouettes():
            m = shape.build_model(spec_of({"silhouette": name}))
            self.assertEqual(efmodel.check_mesh_json(m.mesh_json), [], name)
            self.assertLessEqual(len(m.boxes), shape.MAX_BOXES, name)
            self.assertLessEqual(m.atlas[0], 512, name)

    def test_every_preset_with_defaults_expands_cleanly(self):
        for name in REAL_PRESETS.list_presets():
            if name in shape.REFERENCE_PRESETS:  # needs the spec's reference views: tests/test_relief.py
                continue
            sh = {"attachments": [{"id": "att", "preset": name}]}
            self.assertEqual(shape.validate_shape(sh), [], name)
            m = shape.build_model(spec_of(sh))
            self.assertEqual(efmodel.check_mesh_json(m.mesh_json), [], name)


if __name__ == "__main__":
    unittest.main()
