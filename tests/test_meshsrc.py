"""meshsrc: Blender bundles as the body of a boss (the `mesh` spec block). No Blender needed: bundles are written by the
tests (`meshsrc.write_bundle`), either synthetic (sculpted-style spheres / lathes with smooth weights) or the real path
(the King Arthur box model converted with `bundle_from_shape`, compared with the `shape` spec it came from)."""
import _path  # noqa
import contextlib
import copy
import io
import json
import math
import shutil
import tempfile
import unittest
from pathlib import Path

from bossforge_core import cli, efmesh, efmodel, fit, meshsrc, pngio, reference, rendertest, scaffold, shape, skin, spec
from bossforge_core.spec import BossForgeError

KING = _path.SHIPPED_EXAMPLE
MAIN = """package org.example.fake;

import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;

@Mod(FakeMod.MOD_ID)
public class FakeMod {
    public static final String MOD_ID = "roundtable";

    public FakeMod() {
        IEventBus modBus = FMLJavaModLoadingContext.get().getModEventBus();
    }
}
"""


def J(name):
    return efmodel.joint_by_name(name)["index"]


# --------------------------------------------------------------------------- synthetic geometry
class Builder:
    """Surfaces of revolution about the y axis with smooth normals, EF joint weights and one UV rect per piece."""

    def __init__(self):
        self.pos, self.nrm, self.uv, self.infl, self.tris, self.groups = [], [], [], [], [], []

    def group(self, name):
        if name not in self.groups:
            self.groups.append(name)
        return self.groups.index(name)

    def lathe(self, group, profile, nseg, rect, weight_fn, cx=0.0, cz=0.0):
        """profile = [(y, rx, rz)] bottom -> top (radius 0 at both ends closes it); rect = (u0, v0, u1, v1), v from the top row;
        weight_fn(y, x, z) -> [(joint, weight)]. Angle 0 looks at the front (-z), +90 degrees is the character's right (+x)."""
        g = self.group(group)
        rings = len(profile)
        base, ubase = len(self.pos), len(self.uv)
        u0, v0, u1, v1 = rect
        for r, (y, rx, rz) in enumerate(profile):
            for s in range(nseg + 1):
                a = 2.0 * math.pi * s / nseg
                x, z = cx + rx * math.sin(a), cz - rz * math.cos(a)
                self.pos.append((x, y, z))
                self.infl.append(weight_fn(y, x, z))
                self.uv.append((u0 + (u1 - u0) * s / nseg, v1 - (v1 - v0) * r / (rings - 1)))
        for r, (y, rx, rz) in enumerate(profile):
            lo, hi = profile[max(r - 1, 0)], profile[min(r + 1, rings - 1)]
            dy = hi[0] - lo[0]
            drx = (hi[1] - lo[1]) / dy if dy else 0.0
            drz = (hi[2] - lo[2]) / dy if dy else 0.0
            for s in range(nseg + 1):
                a = 2.0 * math.pi * s / nseg
                if rx < 1e-6 or rz < 1e-6:
                    self.nrm.append((0.0, -1.0 if r == 0 else 1.0, 0.0))
                    continue
                nx, nz = math.sin(a) / rx, -math.cos(a) / rz
                ny = -(drx * math.sin(a) ** 2 / rx + drz * math.cos(a) ** 2 / rz)
                ln = math.sqrt(nx * nx + ny * ny + nz * nz)
                self.nrm.append((nx / ln, ny / ln, nz / ln))
        for r in range(rings - 1):
            for s in range(nseg):
                a = base + r * (nseg + 1) + s
                c = a + nseg + 1
                ua = ubase + r * (nseg + 1) + s
                uc = ua + nseg + 1
                p, t = (a, c, c + 1, a + 1), (ua, uc, uc + 1, ua + 1)  # counter-clockwise seen from outside
                for tri in ((0, 1, 2), (0, 2, 3)):
                    self.tris.append((tuple((p[i], t[i], p[i]) for i in tri), g))

    def sphere(self, group, center, radii, nseg, nring, rect, weight_fn):
        cx, cy, cz = center
        rx, ry, rz = radii
        profile = [(cy - ry * math.cos(math.pi * r / nring), rx * math.sin(math.pi * r / nring), rz * math.sin(math.pi * r / nring))
                   for r in range(nring + 1)]
        self.lathe(group, profile, nseg, rect, weight_fn, cx=cx, cz=cz)

    def quad(self, group, corners, rect, joint):
        """One flat quad (4 corners counter-clockwise seen from the front) with a rigid joint."""
        g = self.group(group)
        u0, v0, u1, v1 = rect
        p0, u_0 = len(self.pos), len(self.uv)
        ax, ay, az = [corners[1][i] - corners[0][i] for i in range(3)]
        bx, by, bz = [corners[3][i] - corners[0][i] for i in range(3)]
        n = (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)
        ln = math.sqrt(sum(v * v for v in n))
        self.nrm.append(tuple(v / ln for v in n))
        ni = len(self.nrm) - 1
        for corner in corners:
            self.pos.append(tuple(corner))
            self.infl.append([(J(joint), 1.0)])
        self.uv.extend(((u0, v1), (u1, v1), (u1, v0), (u0, v0)))
        for tri in ((0, 1, 2), (0, 2, 3)):
            self.tris.append((tuple((p0 + i, u_0 + i, ni) for i in tri), g))

    def write(self, out_dir, stem):
        return meshsrc.write_bundle(out_dir, stem, self.pos, self.nrm, self.uv, self.tris, self.groups, self.infl, source="test")


def single(joint):
    return lambda y, x, z: [(J(joint), 1.0)]


def blend(low, high, y0, y1):
    def fn(y, x, z):
        t = min(1.0, max(0.0, (y - y0) / (y1 - y0)))
        return [(J(low), 1.0 - t), (J(high), t)] if 0.0 < t < 1.0 else [(J(high if t >= 1.0 else low), 1.0)]
    return fn


def character(detail=1, size=512, hair=True):
    """A rounded humanoid: sphere head, optional hair shell, torso, arms, legs and a skirt (groups skin, hair, robe)."""
    b = Builder()
    px = 1.0 / size

    def rect(x, y, w, h):
        return (x * px, y * px, (x + w) * px, (y + h) * px)

    b.sphere("skin", (0.0, 1.76, 0.0), (0.26, 0.28, 0.27), 20 * detail, 14 * detail, rect(2, 2, 160, 128), single("Head"))
    if hair:  # a cap on the upper half of the head only, so the face stays free
        b.sphere("hair", (0.0, 1.86, 0.03), (0.285, 0.23, 0.30), 20 * detail, 14 * detail, rect(170, 2, 160, 128), single("Head"))
    torso = [(0.80, 0.0, 0.0), (0.82, 0.20, 0.12), (1.00, 0.22, 0.13), (1.12, 0.19, 0.12), (1.30, 0.24, 0.14), (1.46, 0.22, 0.13),
             (1.52, 0.09, 0.09), (1.58, 0.0, 0.0)]
    b.lathe("skin", torso, 20 * detail, rect(2, 140, 200, 100), blend("Torso", "Chest", 0.9, 1.3))
    for side, sx, nm in (("r", 1.0, "R"), ("l", -1.0, "L")):
        arm = [(0.80, 0.0, 0.0), (0.84, 0.07, 0.07), (1.09, 0.08, 0.08), (1.35, 0.09, 0.09), (1.42, 0.07, 0.07), (1.44, 0.0, 0.0)]
        b.lathe("skin", arm, 12 * detail, rect(210 + (0 if side == "r" else 100), 140, 96, 100), blend("Hand_" + nm, "Arm_" + nm, 0.98, 1.2), cx=sx * 0.375)
    for side, sx, nm in (("r", 1.0, "R"), ("l", -1.0, "L")):
        leg = [(0.0, 0.0, 0.0), (0.02, 0.07, 0.10), (0.1, 0.08, 0.09), (0.386, 0.09, 0.09), (0.76, 0.115, 0.115), (0.78, 0.0, 0.0)]
        b.lathe("skin", leg, 12 * detail, rect(2 + (0 if side == "r" else 100), 250, 96, 110), blend("Leg_" + nm, "Thigh_" + nm, 0.30, 0.46), cx=sx * 0.125)
    skirt = [(0.25, 0.0, 0.0), (0.26, 0.52, 0.52), (0.55, 0.38, 0.36), (0.80, 0.27, 0.15), (0.82, 0.0, 0.0)]
    b.lathe("robe", skirt, 24 * detail, rect(2, 370, 256, 120), lambda y, x, z: [(J("Thigh_R" if x >= 0 else "Thigh_L"), 1.0)])
    return b


def plane_bundle(out_dir, stem="plane", groups=(("skin", (0.1, 0.1, 0.6, 0.6)),), y0=0.0, joint="Chest", half=0.25):
    """Tiny bundle: one vertical quad per group (1 block tall, 2 x `half` wide, facing the front = -z), UV rects in unit coordinates."""
    b = Builder()
    for name, rect in groups:
        b.quad(name, [(-half, y0, 0.0), (half, y0, 0.0), (half, y0 + 1.0, 0.0), (-half, y0 + 1.0, 0.0)], rect, joint)
    return b.write(out_dir, stem)


def mesh_spec(mesh, **over):
    raw = {"schema": "epicfight-boss-forge/1", "id": "synth_boss", "mod_id": "testmod", "display_name": "Synth", "tier": "mini",
           "lore": {"summary": "s", "backstory": "b", "description": "d"}, "size": {"height": 2.1, "width": 1.0},
           "body": {"template": "biped"}, "appearance": {"archetype": "knight", "overlay": [], "features": []}, "mesh": mesh}
    raw.update(over)
    return raw


def codes(items):
    return sorted(i.code for i in items)


def make_project(root: Path, mod_id="roundtable"):
    (root / "src/main/java/org/example/fake").mkdir(parents=True)
    (root / "src/main/java/org/example/fake/FakeMod.java").write_text(MAIN.replace("roundtable", mod_id))
    (root / "gradle.properties").write_text("mod_id=%s\n" % mod_id)
    return root


class TmpCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()


def painted(n, model=None):
    model = model or shape.build_model(n)
    canvas, info = skin.paint(model.mesh, model.tpl, n["appearance"], int(n["seed"]))
    return model, canvas, info


def texel_at(model, cover, xy):
    """(group, region, position, normal) of the surface point a texel shows."""
    ti, l0, l1, l2 = cover[xy]
    tri = model.mesh.tris[ti]
    pa, pb, pc = (model.mesh.positions[i] for i in tri.p)
    na, nb, nc = (model.mesh.normals[i] for i in tri.n)
    pos = tuple(pa[k] * l0 + pb[k] * l1 + pc[k] * l2 for k in range(3))
    nrm = tuple(na[k] * l0 + nb[k] * l1 + nc[k] * l2 for k in range(3))
    ln = math.sqrt(sum(v * v for v in nrm)) or 1.0
    return tri.part, model.mesh.region_of_tri(tri), pos, tuple(v / ln for v in nrm)


# --------------------------------------------------------------------------- bundle loading
class BundleTests(TmpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.b = character()
        cls.paths = cls.b.write(cls.dir / "char", "char")
        cls.bundle = meshsrc.load_bundle(cls.paths["efmesh"])

    def test_the_writer_makes_a_valid_ef_mesh_with_a_sidecar(self):
        mj = json.loads(Path(self.paths["efmesh"]).read_text(encoding="utf-8"))
        self.assertEqual(efmodel.check_mesh_json(mj), [])
        meta = json.loads(Path(self.paths["meshmeta"]).read_text(encoding="utf-8"))
        self.assertEqual(meta["schema"], "bossforge-mesh/1")
        self.assertEqual(meta["groups"], ["skin", "hair", "robe"])
        self.assertEqual(meta["joint_names"], meshsrc.joint_names())
        for part in efmodel.EF_PARTS:
            self.assertEqual(len(meta["tri_groups"][part]), len(mj["vertices"]["parts"][part]["array"]) // 9)
        self.assertEqual(meshsrc.meta_path_of(self.paths["efmesh"]).name, "char.meshmeta.json")
        self.assertEqual(meshsrc.meta_path_of("/x/foo.json").name, "foo.meshmeta.json")

    def test_load_gives_model_space_geometry_groups_and_normalized_weights(self):
        b = self.bundle
        self.assertEqual(b.groups, ["skin", "hair", "robe"])
        self.assertEqual(len(b.tris) + b.degenerate, sum(len(v["array"]) // 9 for v in b.mesh_json["vertices"]["parts"].values()))
        self.assertGreater(b.degenerate, 0)  # the empty overlay parts carry anchor triangles
        lo, hi = b.bounds
        self.assertAlmostEqual(lo[1], 0.0, places=4)
        self.assertAlmostEqual(hi[1], 2.09, places=2)
        for infl in b.influences:
            self.assertAlmostEqual(sum(w for _j, w in infl), 1.0, places=3)
        self.assertEqual(b.max_influences, 2)
        # model space: front = -z (the face sphere's z centre 0, front vertices at z < 0), right = +x
        head = [b.positions[i] for _p, pi, _t, _n, g in b.tris if g == 0 for i in pi if b.positions[i][1] > 1.5]
        self.assertLess(min(p[2] for p in head), -0.25)

    def test_regions_come_from_the_dominant_joint_of_each_triangle(self):
        b = self.bundle
        regions = {}
        for (part, pi, _t, _n, g), r in zip(b.tris, b.tri_regions):
            regions.setdefault(b.groups[g], set()).add(r)
        self.assertEqual(regions["skin"], {"head", "torso", "arm_l", "arm_r", "leg_l", "leg_r"})
        self.assertEqual(regions["hair"], {"head"})
        self.assertEqual(regions["robe"], {"leg_l", "leg_r"})  # the skirt is weighted to the thighs
        # arms: the vertices on the character's right (+x) are Arm_R / Hand_R
        xs = {r: [b.positions[i][0] for (_p, pi, *_), rr in zip(b.tris, b.tri_regions) if rr == r for i in pi] for r in ("arm_r", "arm_l")}
        self.assertGreater(min(xs["arm_r"]), 0.0)
        self.assertLess(max(xs["arm_l"]), 0.0)

    def test_bundles_are_cached_by_file_identity(self):
        self.assertIs(meshsrc.load_bundle(self.paths["efmesh"]), self.bundle)

    def test_missing_and_broken_files_name_their_error_code(self):
        d = self.dir / "broken"
        d.mkdir()
        with self.assertRaises(BossForgeError) as cm:
            meshsrc.load_bundle(d / "nope.efmesh.json")
        self.assertEqual(cm.exception.err.code, "mesh_missing_file")
        (d / "bad.efmesh.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(BossForgeError) as cm:
            meshsrc.load_bundle(d / "bad.efmesh.json")
        self.assertEqual(cm.exception.err.code, "mesh_bad_json")
        (d / "empty.efmesh.json").write_text('{"vertices": {}}', encoding="utf-8")
        with self.assertRaises(BossForgeError) as cm:
            meshsrc.load_bundle(d / "empty.efmesh.json")
        self.assertEqual(cm.exception.err.code, "mesh_bad_json")
        self.assertIn("vertices.positions", cm.exception.err.message)
        shutil.copy(self.paths["efmesh"], d / "nometa.efmesh.json")
        with self.assertRaises(BossForgeError) as cm:
            meshsrc.load_bundle(d / "nometa.efmesh.json")
        self.assertEqual(cm.exception.err.code, "mesh_missing_meta")

    def mutated(self, name, fn):
        d = self.dir / ("mut_" + name)
        d.mkdir(exist_ok=True)
        shutil.copy(self.paths["efmesh"], d / "m.efmesh.json")
        meta = json.loads(Path(self.paths["meshmeta"]).read_text(encoding="utf-8"))
        fn(meta)
        (d / "m.meshmeta.json").write_text(json.dumps(meta), encoding="utf-8")
        return d / "m.efmesh.json"

    def test_sidecar_problems_are_mesh_bad_meta_or_mesh_bad_group(self):
        def code(name, fn):
            with self.assertRaises(BossForgeError) as cm:
                meshsrc.load_bundle(self.mutated(name, fn))
            return cm.exception.err.code
        self.assertEqual(code("schema", lambda m: m.update(schema="other/1")), "mesh_bad_meta")
        self.assertEqual(code("groups", lambda m: m.update(groups=[])), "mesh_bad_meta")
        self.assertEqual(code("dup", lambda m: m.update(groups=["skin", "skin", "robe"])), "mesh_bad_meta")
        self.assertEqual(code("upper", lambda m: m.update(groups=["Skin", "hair", "robe"])), "mesh_bad_group")
        self.assertEqual(code("dots", lambda m: m.update(groups=["skin", "hair.001", "robe"])), "mesh_bad_group")
        self.assertEqual(code("joints", lambda m: m.update(joint_names=list(reversed(m["joint_names"])))), "mesh_bad_meta")
        self.assertEqual(code("height", lambda m: m.update(height=-1)), "mesh_bad_meta")
        self.assertEqual(code("gb", lambda m: m.update(group_bones={"skin": "Nope"})), "mesh_bad_meta")
        self.assertEqual(code("tg", lambda m: m.pop("tri_groups")), "mesh_bad_meta")
        self.assertEqual(code("short", lambda m: m["tri_groups"].update(head=m["tri_groups"]["head"][:-1])), "mesh_bad_meta")
        self.assertEqual(code("range", lambda m: m["tri_groups"].update(head=[9] * len(m["tri_groups"]["head"]))), "mesh_bad_meta")

    def test_anchor_only_parts_may_leave_their_tri_groups_empty(self):
        p = self.mutated("anchor", lambda m: m["tri_groups"].update({"hat": [], "jacket": []}))
        self.assertEqual(len(meshsrc.load_bundle(p).tris), len(self.bundle.tris))


# --------------------------------------------------------------------------- the spec block
class SpecTests(TmpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.paths = character().write(cls.dir / "char", "char")

    def spec_for(self, **mesh):
        m = {"file": "char/char.efmesh.json"}
        m.update(mesh)
        raw = mesh_spec(m)
        raw["_spec_dir"] = str(self.dir)
        return raw

    def check(self, raw):
        n, errs, warns = spec.check(raw)
        return n, errs, warns

    def test_a_valid_mesh_spec_is_custom_biped_with_the_bundle_height(self):
        n, errs, warns = self.check(self.spec_for(groups={"skin": {"material": "skin_soft", "color": "skin"},
                                                          "hair": {"region": "private"}, "robe": {"region": "private"}}))
        self.assertEqual((errs, warns), ([], []))
        d = n["derived"]
        self.assertTrue(d["custom"])
        self.assertEqual(d["template"], "biped")
        self.assertEqual(d["model_id"], "testmod:entity/synth_boss")
        self.assertAlmostEqual(d["model_height"], 2.09, places=2)
        self.assertAlmostEqual(d["scale"], round(2.1 / d["model_height"], 3), places=3)
        self.assertEqual(n["mesh"]["texture"], {"size": 512, "density": 32, "padding": 4})
        model = shape.build_model(n)  # shape.build_model dispatches
        self.assertIsInstance(model, meshsrc.MeshModel)
        self.assertEqual(round(model.height, 4), d["model_height"])

    def test_mesh_and_shape_and_iron_golem_are_errors(self):
        raw = self.spec_for()
        raw["shape"] = {"silhouette": "knight"}
        _n, errs, _w = self.check(raw)
        self.assertIn("mesh_and_shape", codes(errs))
        raw = self.spec_for()
        raw["body"] = {"template": "iron_golem"}
        _n, errs, _w = self.check(raw)
        self.assertIn("mesh_requires_biped", codes(errs))
        with self.assertRaises(BossForgeError) as cm:
            m = self.spec_for()
            m["shape"] = {"silhouette": "knight"}
            shape.build_model(spec.normalize(m))
        self.assertEqual(cm.exception.err.code, "mesh_and_shape")

    def test_file_and_bundle_errors(self):
        _n, errs, _w = self.check(self.spec_for(file="char/missing.efmesh.json"))
        self.assertEqual([(e.path, e.code) for e in errs], [("/mesh/file", "mesh_missing_file")])
        _n, errs, _w = self.check(mesh_spec({}))
        self.assertIn(("/mesh/file", "required"), [(e.path, e.code) for e in errs])
        d = self.dir / "nometa"
        d.mkdir(exist_ok=True)
        shutil.copy(self.paths["efmesh"], d / "x.efmesh.json")
        _n, errs, _w = self.check(dict(mesh_spec({"file": "nometa/x.efmesh.json"}), _spec_dir=str(self.dir)))
        self.assertEqual([e.code for e in errs], ["mesh_missing_meta"])
        (d / "x.meshmeta.json").write_text("[]", encoding="utf-8")
        _n, errs, _w = self.check(dict(mesh_spec({"file": "nometa/x.efmesh.json"}), _spec_dir=str(self.dir)))
        self.assertEqual([e.code for e in errs], ["mesh_bad_meta"])
        (d / "x.efmesh.json").write_text("{", encoding="utf-8")
        _n, errs, _w = self.check(dict(mesh_spec({"file": "nometa/x.efmesh.json"}), _spec_dir=str(self.dir)))
        self.assertEqual([e.code for e in errs], ["mesh_bad_json"])
        _n, errs, _w = self.check(mesh_spec("char.json"))
        self.assertIn(("/mesh", "bad_type"), [(e.path, e.code) for e in errs])

    def test_unknown_keys_groups_and_value_errors(self):
        raw = self.spec_for(extra=1, groups={"skin": {"bogus": 1, "material": "marble", "color": "pink", "accent": "#12", "region": "wing"},
                                             "ghost": {}, "hair": []},
                            texture={"size": 500, "dens": 3})
        _n, errs, _w = self.check(raw)
        got = {(e.path, e.code) for e in errs}
        self.assertIn(("/mesh/extra", "unknown_key"), got)
        self.assertIn(("/mesh/groups/skin/bogus", "unknown_key"), got)
        self.assertIn(("/mesh/groups/skin/material", "unknown_material"), got)
        self.assertIn(("/mesh/groups/skin/color", "bad_color"), got)
        self.assertIn(("/mesh/groups/skin/accent", "bad_color"), got)
        self.assertIn(("/mesh/groups/skin/region", "unknown_region"), got)
        self.assertIn(("/mesh/groups/hair", "bad_type"), got)
        self.assertIn(("/mesh/texture/size", "out_of_range"), got)
        self.assertIn(("/mesh/texture/dens", "unknown_key"), got)
        # a group that is not in the bundle (structure is fine, the bundle is read)
        _n, errs, _w = self.check(self.spec_for(groups={"ghost": {"material": "cloth"}}))
        self.assertEqual([(e.path, e.code) for e in errs], [("/mesh/groups/ghost", "mesh_bad_group")])
        for tex, code in (({"size": 4096}, "out_of_range"), ({"size": "big"}, "bad_type"), ({"density": 2}, "out_of_range"),
                          ({"padding": 40}, "out_of_range"), ({"padding": 1.5}, "bad_type")):
            _n, errs, _w = self.check(self.spec_for(texture=tex))
            self.assertEqual([e.code for e in errs], [code], tex)

    def test_feature_targets_are_checked_against_the_groups(self):
        raw = self.spec_for(groups={"robe": {"material": "silk", "region": "private"}})
        raw["appearance"]["features"] = [{"type": "trim", "region": "box:robe", "where": "hem"},
                                         {"type": "trim", "region": "part:ro*", "where": "hem"},
                                         {"type": "trim", "region": "box:wing_*", "where": "hem"}]
        _n, errs, warns = self.check(raw)
        self.assertEqual(errs, [])
        self.assertEqual([(w.path, w.code) for w in warns], [("/appearance/features/2/region", "unknown_box")])

    def test_spec_load_records_the_spec_dir_for_mesh_specs(self):
        p = self.dir / "boss.json"
        raw = self.spec_for()
        raw.pop("_spec_dir")
        p.write_text(json.dumps(raw), encoding="utf-8")
        self.assertEqual(spec.load(p)["_spec_dir"], str(self.dir.resolve()))
        n, errs, _w = spec.check(spec.load(p))
        self.assertEqual(errs, [])
        # a spec without a mesh or reference block keeps its old shape (no _spec_dir)
        q = self.dir / "plain.json"
        q.write_text(json.dumps({"id": "x"}), encoding="utf-8")
        self.assertNotIn("_spec_dir", spec.load(q))

    def test_model_height_follows_the_bundle_when_it_changes(self):
        d = self.dir / "hchange"
        plane_bundle(d, "p", y0=0.0)
        raw = dict(mesh_spec({"file": "p.efmesh.json"}), _spec_dir=str(d))
        n, errs, _w = spec.check(raw)
        self.assertEqual(errs, [])
        self.assertAlmostEqual(n["derived"]["model_height"], 1.0, places=3)
        plane_bundle(d, "p", y0=0.5)  # same path, new content: the height cache must not serve the old number
        n, errs, _w = spec.check(raw)
        self.assertAlmostEqual(n["derived"]["model_height"], 1.5, places=3)


class WarningTests(TmpCase):
    def warn(self, paths_dir, stem="p", **mesh):
        m = {"file": stem + ".efmesh.json"}
        m.update(mesh)
        n, errs, warns = spec.check(dict(mesh_spec(m), _spec_dir=str(paths_dir)))
        self.assertEqual(errs, [])
        return warns

    def test_uvs_outside_the_unit_square(self):
        d = self.dir / "uvout"
        plane_bundle(d, "p", groups=(("skin", (0.1, 0.1, 0.5, 0.5)), ("hair", (0.8, 0.1, 1.3, 0.6))))
        w = [x for x in self.warn(d) if x.code == "mesh_uv_outside"]
        self.assertEqual(len(w), 1)
        self.assertIn("hair (2)", w[0].message)

    def test_low_texel_density_names_the_group(self):
        d = self.dir / "dens"
        # 0.5 x 1 block quads: a 16 x 16 texel rect is 22.6 texels / block (ok), a 4 x 4 texel rect 5.7 (too coarse) on the 512 atlas
        plane_bundle(d, "p", groups=(("skin", (0.05, 0.05, 16 / 512 + 0.05, 16 / 512 + 0.05)), ("robe", (0.5, 0.5, 4 / 512 + 0.5, 4 / 512 + 0.5))))
        w = [x for x in self.warn(d) if x.code == "mesh_low_density"]
        self.assertEqual([x.path for x in w], ["/mesh/groups/robe"])
        self.assertIn("5.7 texels per block", w[0].message)
        # the same UVs on a 64 atlas are 8x coarser
        w = [x for x in self.warn(d, texture={"size": 64}) if x.code == "mesh_low_density"]
        self.assertEqual(sorted(x.path for x in w), ["/mesh/groups/robe", "/mesh/groups/skin"])
        # and on 2048 they are dense enough
        self.assertEqual([x for x in self.warn(d, texture={"size": 2048}) if x.code == "mesh_low_density"], [])

    def test_overlapping_uv_islands(self):
        d = self.dir / "overlap"
        plane_bundle(d, "p", groups=(("skin", (0.1, 0.1, 0.6, 0.6)), ("hair", (0.1, 0.1, 0.6, 0.6))))
        self.assertIn("mesh_uv_overlap", codes(self.warn(d)))
        d = self.dir / "nooverlap"
        plane_bundle(d, "p", groups=(("skin", (0.1, 0.1, 0.4, 0.4)), ("hair", (0.5, 0.5, 0.9, 0.9))))
        self.assertEqual(self.warn(d), [])

    def test_floating_or_sunken_meshes_and_stale_sidecar_height(self):
        d = self.dir / "ground"
        plane_bundle(d, "p", y0=0.4)
        self.assertEqual(codes(self.warn(d)), ["mesh_not_on_ground"])
        d = self.dir / "height"
        plane_bundle(d, "p")
        mp = d / "p.meshmeta.json"
        meta = json.loads(mp.read_text())
        meta["height"] = 1.5
        mp.write_text(json.dumps(meta))
        self.assertEqual(codes(self.warn(d)), ["mesh_height_mismatch"])

    def test_wide_groups_in_a_shared_region_frame_are_flagged_until_private(self):
        d = self.dir / "wide"
        # one ordinary chest quad and one 3.2 blocks wide 'wing_f' quad
        b = Builder()
        b.quad("skin", [(-0.25, 0.0, 0.0), (0.25, 0.0, 0.0), (0.25, 1.0, 0.0), (-0.25, 1.0, 0.0)], (0.1, 0.1, 0.5, 0.5), "Chest")
        b.quad("wing_f", [(-1.6, 0.5, 0.1), (1.6, 0.5, 0.1), (1.6, 1.5, 0.1), (-1.6, 1.5, 0.1)], (0.5, 0.5, 0.9, 0.9), "Chest")
        b.write(d, "p")
        w = [x for x in self.warn(d) if x.code == "mesh_region_frame_wide"]
        self.assertEqual([x.path for x in w], ["/mesh/groups/wing_f"])
        self.assertIn("private", w[0].message)
        self.assertEqual([x for x in self.warn(d, groups={"wing_f": {"region": "private"}}) if x.code == "mesh_region_frame_wide"], [])

    def test_swapped_sides_are_flagged(self):
        d = self.dir / "swap"
        b = Builder()
        b.quad("skin", [(-0.4, 0.0, 0.0), (-0.3, 0.0, 0.0), (-0.3, 1.0, 0.0), (-0.4, 1.0, 0.0)], (0.1, 0.1, 0.5, 0.5), "Arm_R")
        b.quad("hair", [(0.3, 0.0, 0.0), (0.4, 0.0, 0.0), (0.4, 1.0, 0.0), (0.3, 1.0, 0.0)], (0.5, 0.5, 0.9, 0.9), "Arm_L")
        b.write(d, "p")
        self.assertIn("mesh_left_right_swapped", codes(self.warn(d)))

    def test_a_clean_synthetic_character_has_no_warnings(self):
        d = self.dir / "clean"
        character().write(d, "p")
        self.assertEqual(self.warn(d, groups={"hair": {"region": "private"}, "robe": {"region": "private"}}), [])


# --------------------------------------------------------------------------- painting a sculpted-style mesh
class PaintTests(TmpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.paths = character().write(cls.dir / "char", "char")

    def n_for(self, features=None, groups=None, regions=None, **mesh):
        m = {"file": "char/char.efmesh.json",
             "groups": groups if groups is not None else {"skin": {"material": "skin_soft", "color": "skin"},
                                                          "hair": {"material": "hair_soft", "color": "hair", "region": "private"},
                                                          "robe": {"material": "silk", "color": "primary", "accent": "accent", "region": "private"}}}
        m.update(mesh)
        raw = mesh_spec(m)
        raw["_spec_dir"] = str(self.dir)
        raw["appearance"]["palette"] = {"primary": "#7a5aa8", "secondary": "#e8e0f0", "accent": "#d4aa32", "skin": "#f0c8a8",
                                        "hair": "#f0e6c0", "eyes": "#3a7ae0"}
        raw["appearance"]["features"] = features or []
        if regions:
            raw["appearance"]["regions"] = regions
        n, errs, _w = spec.check(raw)
        self.assertEqual(errs, [])
        return n

    def test_painting_is_deterministic_and_matches_the_declared_atlas(self):
        n = self.n_for([{"type": "eyes", "style": "human", "color": "eyes"}, {"type": "emblem", "shape": "cross", "region": "torso"}],
                       texture={"size": 256})
        _m, c1, i1 = painted(n)
        meshsrc._BUNDLES.clear()
        meshsrc._MODELS.clear()
        _m, c2, i2 = painted(n)
        self.assertEqual((c1.w, c1.h), (256, 256))
        self.assertEqual(bytes(c1.data), bytes(c2.data))
        self.assertEqual(i1["coverage"], 1.0)

    def test_eyes_land_on_the_front_of_the_sculpted_head(self):
        n = self.n_for([{"type": "eyes", "style": "human", "color": "eyes"}], texture={"size": 256})
        model, canvas, _info = painted(n)
        _m, base, _i = painted(self.n_for([], texture={"size": 256}), model)
        cover = skin._rasterize(model.mesh, model.tpl, 256, 256)
        a, b = {xy: canvas.get(*xy) for xy in cover}, {xy: base.get(*xy) for xy in cover}
        diff = [xy for xy in cover if a[xy] != b[xy]]
        self.assertGreater(len(diff), 20)
        xs = []
        for xy in diff:
            group, region, pos, nrm = texel_at(model, cover, xy)
            self.assertEqual((group, region), ("skin", "head"), xy)
            self.assertLess(nrm[2], -0.5, "eye texel faces the front")
            self.assertLess(pos[2], 0.0)
            self.assertTrue(1.70 < pos[1] < 1.90, pos)
            xs.append(pos[0])
        self.assertLess(min(xs), -0.04)  # one eye on each side of the face
        self.assertGreater(max(xs), 0.04)
        # the eye colour really is the palette eyes colour
        self.assertTrue(any(abs(a[xy][0] - 0x3a) < 40 and abs(a[xy][2] - 0xe0) < 60 for xy in diff))

    def test_the_head_frame_ignores_private_groups_but_not_ordinary_ones(self):
        n = self.n_for([{"type": "eyes", "style": "human", "color": "eyes"}])
        model, canvas, _ = painted(n)
        boxes = skin._region_boxes(model.mesh, set())
        lo, hi = boxes["head"]
        self.assertAlmostEqual(hi[1], 2.04, places=2)  # the skin sphere, not the hair shell (private: its own box)
        self.assertIn("box:hair", boxes)
        self.assertGreater(boxes["box:hair"][1][1], hi[1] - 0.001)
        # the same hair group without `private` joins the head frame
        n2 = self.n_for([], groups={"hair": {"material": "hair_soft", "color": "hair"}})
        model2 = shape.build_model(n2)
        self.assertGreater(skin._region_boxes(model2.mesh, set())["head"][1][1], hi[1] + 0.04)

    def test_group_style_beats_region_style_beats_archetype_default(self):
        regions = {"head": {"material": "cloth", "color": "#0000ff"}, "torso": {"material": "cloth", "color": "#00ff00"},
                   "arms": {"material": "cloth", "color": "#ffff00"}, "legs": {"material": "cloth", "color": "#ff00ff"}}
        n = self.n_for([], groups={"robe": {"material": "cloth", "color": "#ff0000", "region": "private"}}, regions=regions,
                       texture={"size": 256})
        model, canvas, _ = painted(n)
        cover = skin._rasterize(model.mesh, model.tpl, 256, 256)
        px = {xy: canvas.get(*xy) for xy in cover}
        mean = {}
        for xy in cover:
            group, region, _pos, _nrm = texel_at(model, cover, xy)
            key = "robe" if group == "robe" else region
            acc = mean.setdefault(key, [0, 0, 0, 0])
            for k in range(3):
                acc[k] += px[xy][k]
            acc[3] += 1
        mean = {k: tuple(c / acc[3] for c in acc[:3]) for k, acc in mean.items()}
        dominant = lambda c: "rgb"[max(range(3), key=lambda i: c[i])] if max(c) - min(c) > 40 else "?"
        self.assertEqual(dominant(mean["robe"]), "r", mean)  # the group style (private region, own colour)
        self.assertEqual(dominant(mean["head"]), "b", mean)  # no group style: the derived region's appearance.regions entry
        self.assertEqual(dominant(mean["torso"]), "g", mean)
        self.assertGreater(mean["arm_r"][0], 150)  # arms: yellow (r + g)
        self.assertGreater(mean["arm_r"][1], 150)
        self.assertLess(mean["arm_r"][2], 90)
        self.assertGreater(mean["leg_l"][0], 150)  # legs: magenta (r + b)
        self.assertGreater(mean["leg_l"][2], 150)
        self.assertLess(mean["leg_l"][1], 90)

    def test_box_and_part_targets_select_groups(self):
        feats = [{"type": "repaint", "material": "cloth", "color": "#ff0000", "region": "box:robe"},
                 {"type": "repaint", "material": "cloth", "color": "#00ff00", "regions": ["part:hai*"]}]
        n = self.n_for(feats, texture={"size": 256})
        model, canvas, _ = painted(n)
        cover = skin._rasterize(model.mesh, model.tpl, 256, 256)
        px = {xy: canvas.get(*xy) for xy in cover}
        seen = {}
        for xy in cover:
            group = texel_at(model, cover, xy)[0]
            r, g, b = px[xy][:3]
            seen.setdefault(group, set()).add("r" if r > 150 and g < 90 else "g" if g > 150 and r < 90 else "o")
        self.assertEqual(seen["robe"], {"r"})
        self.assertEqual(seen["hair"], {"g"})
        self.assertNotIn("r", seen["skin"])
        self.assertNotIn("g", seen["skin"])
        # the same via plain part: names on a non-private (derived region) group
        n = self.n_for([{"type": "repaint", "material": "cloth", "color": "#ff0000", "region": "box:sk*"}], texture={"size": 256})
        model, canvas, _ = painted(n)
        cover = skin._rasterize(model.mesh, model.tpl, 256, 256)
        px = {xy: canvas.get(*xy) for xy in cover}
        skin_px = [px[xy] for xy in cover if texel_at(model, cover, xy)[0] == "skin"]
        self.assertTrue(all(p[0] > 150 and p[1] < 90 for p in skin_px))

    def test_island_borders_are_padded_by_the_configured_texels(self):
        for padding in (0, 1, 4):
            n = self.n_for([], texture={"size": 256, "padding": padding})
            model, canvas, _ = painted(n)
            cover = set(skin._rasterize(model.mesh, model.tpl, 256, 256))
            opaque = {(i % 256, i // 256) for i in range(256 * 256) if canvas.data[4 * i + 3] > 0}
            grown = set(cover)
            for _ in range(padding):
                grown |= {(x + dx, y + dy) for x, y in grown for dx in (-1, 0, 1) for dy in (-1, 0, 1) if 0 <= x + dx < 256 and 0 <= y + dy < 256}
            self.assertEqual(opaque, grown, "padding %d" % padding)
        self.assertGreater(len(opaque), len(cover))

    def test_texture_size_is_the_atlas_size(self):
        n = self.n_for([], texture={"size": 256})
        model, canvas, _ = painted(n)
        self.assertEqual((canvas.w, canvas.h), (256, 256))
        self.assertEqual(model.atlas, (256, 256))
        self.assertEqual(model.tpl["authored_uv"], [256, 256])

    def test_texture_larger_than_the_limit_is_an_error(self):
        n = self.n_for([])
        model = shape.build_model(n)
        tpl = dict(model.tpl, authored_uv=[4096, 4096])
        with self.assertRaises(BossForgeError) as cm:
            skin.paint(model.mesh, tpl, n["appearance"], 1)
        self.assertEqual(cm.exception.err.code, "texture_too_large")
        self.assertEqual(cm.exception.err.path, "/mesh/texture/size")

    def test_model_info_reports_density_coverage_and_regions(self):
        n = self.n_for([])
        info = shape.build_model(n).info
        self.assertEqual(info["triangles"], len(meshsrc.load_bundle(self.dir / "char/char.efmesh.json").tris))
        self.assertEqual(sorted(info["groups"]), ["hair", "robe", "skin"])
        g = info["groups"]["skin"]
        self.assertEqual(g["region"], "derived")
        self.assertEqual(sorted(g["derived_regions"]), ["arm_l", "arm_r", "head", "leg_l", "leg_r", "torso"])
        self.assertGreater(g["texels_per_block"], 30)
        self.assertEqual(info["groups"]["hair"]["region"], "private")
        self.assertEqual(g["style"], {"material": "skin_soft", "color": "skin"})
        self.assertGreater(info["texture"]["uv_coverage"], 0.3)
        self.assertEqual(info["texture"]["uv_overlap"], 0.0)
        self.assertEqual(info["regions"]["box:hair"], info["groups"]["hair"]["triangles"])

    def test_every_appearance_feature_runs_on_an_imported_mesh(self):
        feats = [{"type": "eyes", "style": "anime", "color": "eyes"}, {"type": "brow", "color": "hair"}, {"type": "scar", "side": "left"},
                 {"type": "hair", "style": "long", "color": "hair"}, {"type": "beard", "length": "short"},
                 {"type": "emblem", "shape": "sun", "region": "torso", "face": "front", "color": "accent"},
                 {"type": "trim", "region": "torso", "where": "belt", "color": "accent"}, {"type": "plates", "regions": ["torso", "arms"]},
                 {"type": "cracks"}, {"type": "wear", "amount": 0.3}, {"type": "runes", "regions": ["legs"]},
                 {"type": "stripes", "regions": ["arms"]}, {"type": "mouth"}, {"type": "blush"}, {"type": "bangs"},
                 {"type": "gradient", "region": "box:robe", "from": "primary", "to": "secondary"},
                 {"type": "filigree", "region": "box:robe", "pattern": "meander", "where": "border"},
                 {"type": "sparkle", "region": "part:robe"}, {"type": "repaint", "region": "part:hair", "material": "hair_soft"}]
        n = self.n_for(feats, texture={"size": 256})
        _m, canvas, info = painted(n)
        self.assertEqual(info["coverage"], 1.0)
        self.assertNotEqual(bytes(canvas.data), bytes(painted(self.n_for([], texture={"size": 256}))[1].data))


# --------------------------------------------------------------------------- command line + reference bake on a mesh spec
class CommandTests(TmpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.paths = character().write(cls.dir / "char", "char")
        raw = mesh_spec({"file": "char/char.efmesh.json",
                         "groups": {"skin": {"material": "skin_soft", "color": "skin"},
                                    "hair": {"material": "hair_soft", "color": "hair", "region": "private"},
                                    "robe": {"material": "silk", "color": "primary", "region": "private"}},
                         "texture": {"size": 256}})
        raw["appearance"]["features"] = [{"type": "eyes", "style": "human", "color": "eyes"}]
        cls.spec = cls.dir / "boss.json"
        cls.spec.write_text(json.dumps(raw), encoding="utf-8")

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, json.loads(buf.getvalue())

    def test_validate_and_shape_info(self):
        code, res = self.run_cli("validate", str(self.spec))
        self.assertEqual((code, res["ok"], res["custom_shape"], res["template"]), (0, True, True, "biped"))
        self.assertAlmostEqual(res["model_height"], 2.09, places=2)
        code, res = self.run_cli("shape", str(self.spec), "--out", str(self.dir / "shapeout"))
        self.assertEqual(code, 0, res)
        self.assertEqual(res["boxes"], 3)  # one entry per group
        self.assertEqual(res["atlas"], [256, 256])
        mj = json.loads((self.dir / "shapeout" / res["files"][0]).read_text(encoding="utf-8"))
        self.assertEqual(efmodel.check_mesh_json(mj), [])

    def test_validate_reports_bundle_errors_with_exit_code_1(self):
        p = self.dir / "bad.json"
        raw = json.loads(self.spec.read_text())
        raw["mesh"]["file"] = "char/nope.efmesh.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        code, res = self.run_cli("validate", str(p))
        self.assertEqual(code, 1)
        self.assertEqual(res["errors"][0]["code"], "mesh_missing_file")

    def test_skin_writes_texture_preview_and_closeup_and_reports_the_mesh(self):
        code, res = self.run_cli("skin", str(self.spec), "--out", str(self.dir / "skinout"))
        self.assertEqual(code, 0, res)
        self.assertEqual((res["texture"]["width"], res["texture"]["coverage"]), (256, 1.0))
        self.assertTrue(Path(res["texture"]["path"]).is_file())
        self.assertTrue(Path(res["preview"]).is_file())
        self.assertTrue(Path(res["closeup"]).is_file())
        self.assertEqual(sorted(res["mesh"]["groups"]), ["hair", "robe", "skin"])
        self.assertEqual(pngio.png_size(res["texture"]["path"]), (256, 256))

    def test_fit_and_reference_bake_work_on_a_mesh_spec(self):
        n, errs, _w = spec.check(dict(json.loads(self.spec.read_text()), _spec_dir=str(self.dir)))
        self.assertEqual(errs, [])
        model = shape.build_model(n)
        from test_fit import silhouette_view
        raw = json.loads(self.spec.read_text())
        (self.dir / "refs").mkdir(exist_ok=True)
        views = {}
        for name in ("front", "right"):
            v, polys = silhouette_view(model, name, s=48.0)
            self.assertEqual({g for g, _p in polys}, {"skin", "hair", "robe"})
            pngio.write_png(self.dir / "refs" / (name + ".png"), pngio.Canvas(v.width, v.height, bytearray(v.rgba)))
            views[name] = {"image": "refs/%s.png" % name, "ground_y": v.ground_y, "center_x": v.center_x, "px_per_block": 48.0,
                           "mask": {"mode": "auto", "erode": 0, "seal": 0}}
        raw["reference"] = {"views": views, "bake": {"views": ["front"], "regions": ["box:robe"], "smear": False, "min_facing": 0.3}}
        sp = self.dir / "ref_boss.json"
        sp.write_text(json.dumps(raw), encoding="utf-8")
        code, res = self.run_cli("fit", str(sp), "--out", str(self.dir / "fitout"))
        self.assertEqual(code, 0, res)
        for name, r in res["views"].items():
            self.assertEqual(r["iou"], 1.0, name)
        # bake: only the robe group (a `box:` target of the mesh spec) is painted from the reference (silhouette colour 200,60,60)
        code, res = self.run_cli("skin", str(sp), "--out", str(self.dir / "bakeout"))
        self.assertEqual(code, 0, res)
        self.assertGreater(res["bake"]["total"]["direct"], 200)
        tex = pngio.read_png(res["texture"]["path"])
        model = shape.build_model(spec.check(dict(raw, _spec_dir=str(self.dir)))[0])
        cover = skin._rasterize(model.mesh, model.tpl, 256, 256)
        by_group = {}
        for xy in cover:
            g = texel_at(model, cover, xy)[0]
            i = 4 * (xy[1] * tex.w + xy[0])
            by_group.setdefault(g, []).append(tuple(tex.data[i:i + 3]) == (200, 60, 60))
        self.assertGreater(sum(by_group["robe"]), 200)
        self.assertEqual(sum(by_group["skin"]) + sum(by_group["hair"]), 0)


# --------------------------------------------------------------------------- the real path: a shape model as a bundle
class ShapeTwinTests(TmpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.raw = json.loads(KING.read_text(encoding="utf-8"))
        cls.n, errs, _w = spec.check(cls.raw)
        assert not errs, errs
        cls.shape_model = shape.build_model(cls.n)
        cls.twin = meshsrc.spec_from_shape(cls.raw, cls.dir / "king")
        cls.twin1 = copy.deepcopy(cls.twin)
        cls.twin1["mesh"]["texture"]["padding"] = 1
        cls.n2, errs, cls.warns2 = spec.check(cls.twin)
        assert not errs, errs
        cls.mesh_model = shape.build_model(cls.n2)
        cls._canvases = {}

    @classmethod
    def canvas(cls, key):
        """Painted canvases of the shape spec and of its mesh twin (default padding / one texel), painted once."""
        cache = cls._canvases
        if key not in cache:
            if key == "shape":
                cache[key] = painted(cls.n, cls.shape_model)[1:]
            elif key == "mesh":
                cache[key] = painted(cls.n2, cls.mesh_model)[1:]
            else:
                n1, errs, _w = spec.check(cls.twin1)
                assert not errs, errs
                cache[key] = painted(n1)[1:]
        return cache[key]

    def test_the_bundle_is_the_shape_mesh_json_with_one_group_per_box(self):
        self.assertIsInstance(self.mesh_model, meshsrc.MeshModel)
        self.assertEqual(self.mesh_model.mesh_json, self.shape_model.mesh_json)
        self.assertEqual(sorted(meshsrc.load_bundle(self.dir / "king/king_arthur.efmesh.json").groups), sorted(b["id"] for b in self.shape_model.boxes))
        self.assertEqual(len(self.mesh_model.mesh.tris), len(self.shape_model.mesh.tris))
        self.assertEqual(self.warns2, [])
        d1, d2 = self.n["derived"], self.n2["derived"]
        for k in ("template", "scale", "model_height", "custom", "model_id", "hitbox"):
            self.assertEqual(d1[k], d2[k], k)
        self.assertEqual(self.mesh_model.atlas, self.shape_model.atlas)
        for a, b in zip(sorted(self.mesh_model.mesh.positions), sorted(self.shape_model.mesh.positions)):
            self.assertEqual(a, b)

    def test_per_triangle_regions_match_the_shape_regions(self):
        sm, mm = self.shape_model.mesh, self.mesh_model.mesh
        shape_regions = {}
        for tri in sm.tris:
            shape_regions[tri.part] = sm.region_of(tri.part)
        for tri in mm.tris:
            self.assertEqual(mm.region_of_tri(tri), shape_regions[tri.part], tri.part)

    def test_skin_output_is_identical_to_the_shape_spec_with_one_texel_padding(self):
        c_shape, i_shape = self.canvas("shape")
        c_mesh, i_mesh = self.canvas("pad1")
        self.assertEqual((c_shape.w, c_shape.h), (c_mesh.w, c_mesh.h))
        self.assertEqual(bytes(c_shape.data), bytes(c_mesh.data))
        self.assertEqual(i_shape["coverage"], i_mesh["coverage"])

    def test_default_padding_only_adds_texels_around_the_islands(self):
        da, db = self.canvas("shape")[0].data, self.canvas("mesh")[0].data
        total = same = extra = 0
        for i in range(0, len(da), 4):
            if da[i + 3] > 0:
                total += 1
                same += da[i:i + 4] == db[i:i + 4]
            elif db[i + 3] > 0:
                extra += 1
        self.assertEqual(same, total)  # everything the shape pipeline painted is unchanged
        self.assertGreater(extra, 1000)  # the wider default padding adds texels around the islands

    def test_fit_silhouettes_match_the_box_hulls_in_all_four_views(self):
        from test_fit import silhouette_view
        with tempfile.TemporaryDirectory() as tmp:
            for name in reference.VIEW_NAMES:
                view, _polys = silhouette_view(self.shape_model, name)
                res = fit.fit_views(self.mesh_model, {name: view}, None, tmp)["views"][name]
                self.assertEqual((res["iou"], res["missing_px"], res["extra_px"]), (1.0, 0, 0), name)
                polys = fit.model_polys(self.mesh_model, view)
                self.assertTrue({g for g, _p in polys} <= {b["id"] for b in self.shape_model.boxes})

    def test_a_missing_part_is_named_by_group_in_the_fit_hints(self):
        from test_fit import silhouette_view
        raw = copy.deepcopy(self.raw)
        raw["shape"]["attachments"] = [a for a in raw["shape"]["attachments"] if a["id"] != "shield"]
        view, _ = silhouette_view(shape.build_model(spec.normalize(raw)), "front")  # the reference has no shield
        with tempfile.TemporaryDirectory() as tmp:
            r = fit.fit_views(self.mesh_model, {"front": view}, None, tmp)["views"]["front"]
        self.assertGreater(r["extra_px"], 150)
        self.assertIn("sticks out", "\n".join(r["hints"]))
        self.assertTrue(any("shield" in h for h in r["hints"]), r["hints"])  # the outermost group is a shield box

    def test_add_boss_stages_the_bundle_wires_the_patch_and_passes_qa(self):
        proj = make_project(self.dir / "proj")
        spec_path = self.dir / "king" / "boss.json"
        raw = {k: v for k, v in self.twin.items() if k != "_spec_dir"}
        (self.dir / "king" / "king_arthur.blend").write_bytes(b"BLENDER-test")
        spec_path.write_text(json.dumps(raw), encoding="utf-8")
        res = scaffold.add_boss(spec_path, proj, self.dir / "empty-cache", offline=True)
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["qa"]["ok"], res["qa"])
        models = proj / "bossforge" / "models"
        self.assertEqual(sorted(p.name for p in models.iterdir()), ["king_arthur.blend", "king_arthur.efmesh.json", "king_arthur.meshmeta.json"])
        self.assertEqual((models / "king_arthur.efmesh.json").read_bytes(), (self.dir / "king" / "king_arthur.efmesh.json").read_bytes())
        copy_spec = json.loads((proj / "bossforge/bosses/king_arthur.boss.json").read_text(encoding="utf-8"))
        self.assertEqual(copy_spec["mesh"]["file"], "../models/king_arthur.efmesh.json")
        resdir = proj / "src/main/resources"
        game = json.loads((resdir / "assets/roundtable/animmodels/entity/king_arthur.json").read_text(encoding="utf-8"))
        self.assertEqual(game, self.shape_model.mesh_json)
        patch = json.loads((resdir / "data/roundtable/epicfight_mobpatch/king_arthur.json").read_text(encoding="utf-8"))
        self.assertEqual((patch["renderer"], patch["humanoid"], patch["model"]), ("epicfight:custom", True, "roundtable:entity/king_arthur"))
        self.assertEqual(patch["attributes"]["scale"], self.n["derived"]["scale"])
        self.assertEqual(patch["attributes"]["scale"], round(3.0 / self.mesh_model.height, 3))
        self.assertEqual(pngio.png_size(resdir / "assets/roundtable/textures/entity/king_arthur.png"), (512, 512))
        doc = (proj / "docs/bosses/king_arthur.md").read_text(encoding="utf-8")
        self.assertIn("## Mesh", doc)
        # render-test asserts the vertex count Epic Fight must report
        n = spec.normalize(spec.load(proj / "bossforge/bosses/king_arthur.boss.json"))
        self.assertEqual(rendertest.expected_mesh_vertices(proj, "roundtable", "king_arthur", n["derived"]),
                         len(self.shape_model.mesh_json["vertices"]["positions"]["array"]) // 3)
        # regenerating from the project copy changes nothing; a hand-edited game mesh is reported as stale by qa
        again = scaffold.add_boss(proj / "bossforge/bosses/king_arthur.boss.json", proj, self.dir / "empty-cache", offline=True)
        self.assertEqual(again["files"]["written"] + again["files"]["updated"] + again["files"]["conflicts"], [])
        self.assertEqual(cli.main(["qa", "--project", str(proj)]), 0)
        game["vertices"]["positions"]["array"][0] += 0.5
        (resdir / "assets/roundtable/animmodels/entity/king_arthur.json").write_text(json.dumps(game), encoding="utf-8")
        from bossforge_core import qa
        self.assertIn("qa_model_stale", [e.code for e in qa.run(proj, None)])

    def test_stage_bundle_leaves_specs_without_mesh_alone(self):
        raw = KING.read_bytes()
        self.assertEqual(meshsrc.stage_bundle(raw, KING), (raw, []))
        self.assertEqual(meshsrc.stage_bundle(b"not json", KING), (b"not json", []))

    def test_shape_specs_keep_their_old_behaviour(self):
        self.assertNotIn("mesh", self.n)
        self.assertEqual(self.n["shape"]["silhouette"], self.raw["shape"].get("silhouette", self.n["shape"]["silhouette"]))
        self.assertIsInstance(self.shape_model, shape.ShapeModel)
        tri = self.shape_model.mesh.tris[0]
        self.assertEqual(self.shape_model.mesh.region_of_tri(tri), self.shape_model.mesh.region_of(tri.part))
        self.assertEqual(meshsrc.retarget(["box:a*", "part:b", "head", "*"]), ["part:a*", "part:b", "head", "*"])
        feats = [{"type": "trim", "region": "box:x"}, {"type": "eyes"}, {"type": "plates", "regions": ["torso", "box:y*"]}]
        got = meshsrc.retarget_features(feats)
        self.assertEqual([f.get("region") for f in got[:1]], ["part:x"])
        self.assertEqual(got[2]["regions"], ["torso", "part:y*"])
        self.assertEqual(feats[0]["region"], "box:x")  # the input is not mutated


if __name__ == "__main__":
    unittest.main()
