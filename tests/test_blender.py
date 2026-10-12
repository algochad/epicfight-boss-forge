"""Blender pipeline: runner (stdlib, fake Blender), rig, EF mesh import/export round trips, pose convention, artist-mesh export.

Tests that start Blender are skipped when no Blender executable is found; the runner tests use a fake executable.
"""
import _path  # noqa
import contextlib
import io
import json
import math
import os
import stat
import tempfile
import unittest
from pathlib import Path

from bossforge_core import blender, cli, efmodel, pngio
from bossforge_core.spec import BossForgeError

from _path import SHIPPED_EXAMPLE

BLENDER = blender.find_blender()
needs_blender = unittest.skipUnless(BLENDER, "Blender not found")
EF_BIPED = next(iter(sorted(Path(os.path.expanduser("~/.cache/epicfight-boss-forge/ef")).glob("*/biped.json"))), None)
needs_biped = unittest.skipUnless(EF_BIPED, "EF stock biped.json is not in the cache (bossforge fetch-ef-assets)")


# ----------------------------------------------------------------------------- mesh comparison / skinning helpers
def decode_mesh(d):
    v = d["vertices"]
    P, N, U = v["positions"]["array"], v["normals"]["array"], v["uvs"]["array"]
    W, vc, vi = v["weights"]["array"], v["vcounts"]["array"], v["vindices"]["array"]
    infl, k = [], 0
    for c in vc:
        infl.append(tuple(sorted((vi[k + 2 * i], W[vi[k + 2 * i + 1]]) for i in range(c))))
        k += 2 * c
    parts = {}
    for name, obj in v["parts"].items():
        arr, tris, anchors = obj["array"], [], 0
        for t in range(0, len(arr), 9):
            cs = []
            for c in range(3):
                p, u, n = arr[t + 3 * c:t + 3 * c + 3]
                cs.append((tuple(P[3 * p:3 * p + 3]), tuple(U[2 * u:2 * u + 2]), tuple(N[3 * n:3 * n + 3]), infl[p], p))
            if len({c[4] for c in cs}) < 3:
                anchors += 1
                continue
            tris.append(tuple(cs))
        parts[name] = (tris, anchors)
    return parts


def _tri_key(tri):
    return tuple(sorted(tuple(round(x, 3) for x in c[0]) for c in tri))


def compare_meshes(a, b):
    """Triangle-by-triangle comparison of two EF mesh dicts (independent of vertex numbering)."""
    A, B = decode_mesh(a), decode_mesh(b)
    out = {"parts": {}, "max_pos": 0.0, "max_uv": 0.0, "max_nrm": 0.0, "max_w": 0.0, "joint_mismatch": 0, "unmatched": 0, "tris": 0}
    for name in efmodel.EF_PARTS:
        ta, aa = A[name]
        tb, ab = B[name]
        out["parts"][name] = (len(ta), len(tb), aa, ab)
        idx = {}
        for t in tb:
            idx.setdefault(_tri_key(t), []).append(t)
        for t in ta:
            out["tris"] += 1
            lst = idx.get(_tri_key(t))
            if not lst:
                out["unmatched"] += 1
                continue
            best = None
            for cand in lst:
                for r in range(3):
                    rc = cand[r:] + cand[:r]
                    e = max(math.dist(x[0], y[0]) for x, y in zip(t, rc))
                    if best is None or e < best[0]:
                        best = (e, rc, cand)
            e, rc, cand = best
            lst.remove(cand)
            out["max_pos"] = max(out["max_pos"], e)
            for x, y in zip(t, rc):
                out["max_uv"] = max(out["max_uv"], math.dist(x[1], y[1]))
                out["max_nrm"] = max(out["max_nrm"], math.dist(x[2], y[2]))
                jx, jy = dict(x[3]), dict(y[3])
                if set(jx) != set(jy):
                    out["joint_mismatch"] += 1
                else:
                    out["max_w"] = max([out["max_w"]] + [abs(jx[j] - jy[j]) for j in jx])
    return out


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def hierarchy_world(biped):
    """{joint: (world 4x4 row-major, parent)} from the armature hierarchy of an EF mesh JSON (storage space)."""
    out = {}

    def walk(node, parent_m, parent):
        t = node["transform"]
        local = [t[0:4], t[4:8], t[8:12], t[12:16]]
        m = _matmul(parent_m, local) if parent_m else local
        out[node["name"]] = (m, parent)
        for c in node.get("children", []):
            walk(c, m, node["name"])

    for r in biped["armature"]["hierarchy"]:
        walk(r, None, None)
    return out


def _rot3(axis, deg):
    n = math.sqrt(sum(x * x for x in axis))
    x, y, z = (c / n for c in axis)
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    C = 1 - c
    return [[c + x * x * C, x * y * C - z * s, x * z * C + y * s],
            [y * x * C + z * s, c + y * y * C, y * z * C - x * s],
            [z * x * C - y * s, z * y * C + x * s, c + z * z * C]]


AXES = {"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1), "-X": (-1, 0, 0)}


def pose_deltas(world, rotations):
    """Per-joint (R, t) with p' = R p + t for rotations [(joint, axis name, degrees)] about the posed joint in world axes."""
    ident = ([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0])
    delta = {n: ident for n in world}

    def subtree(j):
        res = {j}
        for n, (_m, par) in world.items():
            if par in res:
                res.add(n)
        return res

    for joint, axis, deg in rotations:
        R0, t0 = delta[joint]
        m = world[joint][0]
        head = [m[0][3], m[1][3], m[2][3]]
        cur = [sum(R0[i][k] * head[k] for k in range(3)) + t0[i] for i in range(3)]
        R = _rot3(AXES[axis], deg)
        t = [cur[i] - sum(R[i][k] * cur[k] for k in range(3)) for i in range(3)]
        for d in subtree(joint):
            Rd, td = delta[d]
            delta[d] = ([[sum(R[i][k] * Rd[k][j] for k in range(3)) for j in range(3)] for i in range(3)],
                        [sum(R[i][k] * td[k] for k in range(3)) + t[i] for i in range(3)])
    return delta


def skin_positions(mesh, names, delta):
    v = mesh["vertices"]
    P, W, vc, vi = v["positions"]["array"], v["weights"]["array"], v["vcounts"]["array"], v["vindices"]["array"]
    out, k = [], 0
    for i, c in enumerate(vc):
        p = P[3 * i:3 * i + 3]
        acc = [0.0, 0.0, 0.0]
        for q in range(c):
            j, wi = vi[k + 2 * q], vi[k + 2 * q + 1]
            R, t = delta[names[j]]
            for a in range(3):
                acc[a] += W[wi] * (sum(R[a][b] * p[b] for b in range(3)) + t[a])
        k += 2 * c
        out.append(tuple(acc))
    return out


def write_script(directory, name, body):
    p = Path(directory) / name
    p.write_text(body, encoding="utf-8")
    return p


# ----------------------------------------------------------------------------- the runner (fake Blender, stdlib only)
@unittest.skipIf(os.name == "nt", "shell-script fake blender")
class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.script = self.dir / "task.py"
        self.script.write_text("pass\n")

    def tearDown(self):
        self.tmp.cleanup()

    def fake(self, body):
        p = self.dir / "fakeblender"
        p.write_text("#!/bin/sh\n" + body)
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
        return str(p)

    def test_explicit_path_wins_and_missing_explicit_is_none(self):
        exe = self.fake("exit 0\n")
        self.assertEqual(blender.find_blender(exe), Path(exe))
        self.assertIsNone(blender.find_blender(str(self.dir / "nope")))

    def test_env_blender(self):
        exe = self.fake("exit 0\n")
        old = os.environ.get("BLENDER")
        os.environ["BLENDER"] = exe
        try:
            self.assertEqual(blender.find_blender(), Path(exe))
        finally:
            if old is None:
                del os.environ["BLENDER"]
            else:
                os.environ["BLENDER"] = old

    def code(self, exe, **kw):
        with self.assertRaises(BossForgeError) as cm:
            blender.run(self.script, {"a": 1}, blender=exe, **kw)
        return cm.exception.err.code, cm.exception.err.message

    def test_missing_blender(self):
        code, _msg = self.code(str(self.dir / "does-not-exist"))
        self.assertEqual(code, "blender_missing")

    def test_result_line_and_args_env(self):
        exe = self.fake('echo noise\necho "BFK_RESULT {\\"ok\\":true,\\"args\\":$BFK_ARGS,\\"script\\":\\"$(basename $BFK_SCRIPT)\\"}"\necho "BFK_RESULT {\\"ok\\":true,\\"last\\":1}"\n')
        self.assertEqual(blender.run(self.script, {"a": 1}, blender=exe), {"ok": True, "last": 1})
        exe = self.fake('echo "BFK_RESULT {\\"ok\\":true,\\"args\\":$BFK_ARGS,\\"script\\":\\"$(basename $BFK_SCRIPT)\\",\\"tmp\\":\\"$BFK_TMP\\"}"\n')
        res = blender.run(self.script, {"a": 1}, blender=exe)
        self.assertEqual((res["args"], res["script"]), ({"a": 1}, "task.py"))
        self.assertTrue(res["tmp"])

    def test_command_line(self):
        exe = self.fake('echo "BFK_RESULT {\\"ok\\":true}"\necho "$@" > %s\n' % (self.dir / "argv.txt"))
        blend = self.dir / "m.blend"
        blend.write_bytes(b"x")
        blender.run(self.script, {}, blend=blend, blender=exe)
        argv = (self.dir / "argv.txt").read_text().split()
        self.assertEqual(argv[:3], ["-b", "--factory-startup", "--python-exit-code"])
        self.assertLess(argv.index(str(blend)), argv.index("--python"))
        self.assertTrue(argv[argv.index("--python") + 1].endswith("_bootstrap.py"))

    def test_failure_modes(self):
        self.assertEqual(self.code(self.fake("echo oops >&2\nexit 3\n"))[0], "blender_failed")
        code, msg = self.code(self.fake("echo 'Traceback boom' >&2\nexit 1\n"))
        self.assertIn("boom", msg)
        self.assertEqual(self.code(self.fake('echo "BFK_RESULT {\\"ok\\":false,\\"_exception\\":\\"ValueError: x\\"}"\nexit 1\n'))[0], "blender_failed")
        self.assertEqual(self.code(self.fake("echo hello\n"))[0], "blender_no_result")
        self.assertEqual(self.code(self.fake("sleep 5\n"), timeout=1)[0], "blender_timeout")

    def test_parse_result_takes_last_valid_line(self):
        self.assertEqual(blender.parse_result('x\nBFK_RESULT {"a":1}\nBFK_RESULT not json\n'), {"a": 1})
        self.assertIsNone(blender.parse_result("nothing"))

    def test_mcp_probe_is_informational(self):
        self.assertIsInstance(blender.mcp_port_reachable(port=9, timeout=0.05), bool)

    def test_blender_commands_are_registered(self):
        p = cli.build_parser()
        a = p.parse_args(["blender", "doctor"])
        self.assertEqual(a.fn.__name__, "cmd_blender_doctor")
        self.assertEqual(p.parse_args(["--blender", "/x/y", "blender", "rig", "--out", "a.blend"]).blender, "/x/y")


class CliContractTests(unittest.TestCase):
    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, json.loads(buf.getvalue())

    def test_doctor_without_blender_is_a_json_failure(self):
        code, out = self.run_cli("--blender", "/nonexistent/blender", "blender", "doctor")
        self.assertEqual(code, 1)
        self.assertFalse(out["ok"])
        self.assertEqual(out["errors"][0]["code"], "blender_missing")
        self.assertIn("mcp", out)

    def test_import_without_blender_is_a_json_failure(self):
        with tempfile.TemporaryDirectory() as d:
            code, out = self.run_cli("--blender", "/nonexistent/blender", "blender", "rig", "--out", str(Path(d) / "r.blend"))
        self.assertEqual((code, out["errors"][0]["code"]), (1, "blender_missing"))

    def test_explicit_blender_path_does_not_outlive_the_invocation(self):
        from bossforge_core import blender as _blender
        before = _blender._EXPLICIT
        self.run_cli("--blender", "/nonexistent/blender", "blender", "doctor")
        self.assertEqual(_blender._EXPLICIT, before)


# ----------------------------------------------------------------------------- Blender itself
@needs_blender
class BlenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        out = json.loads(buf.getvalue())
        return code, out

    def shape_mesh(self):
        if not hasattr(BlenderTests, "_shape"):
            d = self.dir / "ka"
            code, out = self.cli("shape", str(SHIPPED_EXAMPLE), "--out", str(d))
            self.assertEqual(code, 0, out)
            BlenderTests._shape = d / out["files"][0]
        return BlenderTests._shape

    def test_doctor(self):
        code, out = self.cli("blender", "doctor")
        self.assertEqual(code, 0, out)
        self.assertTrue(out["ok"])
        self.assertEqual(set(out["checks"]), {"rig", "mesh_api", "bossforge_core", "render"})
        self.assertTrue(all(c["ok"] for c in out["checks"].values()))
        self.assertIn("BLENDER_WORKBENCH", out["engines"])
        self.assertIn("reachable", out["mcp"])
        self.assertEqual(out["checks"]["render"]["size"], [64, 64])

    def test_rig_matches_ef_biped(self):
        blend = self.dir / "rig.blend"
        code, out = self.cli("blender", "rig", "--out", str(blend))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["bones"], 20)
        self.assertLess(out["max_head_deviation"], 1e-6)
        script = write_script(self.dir, "check_rig.py", '''
import bfk, json
from bfk import rig
from bossforge_core import efmodel
a = bfk.get_args()
o = rig.find_rig()
bones = {b.name: b for b in o.data.bones}
res = {"ok": True, "names": [b.name for b in o.data.bones], "parents": {n: (b.parent.name if b.parent else None) for n, b in bones.items()},
       "heads": {n: list(b.head_local) for n, b in bones.items()}, "tails": {n: list(b.tail_local) for n, b in bones.items()},
       "connected": [n for n, b in bones.items() if b.use_connect], "deform": all(b.use_deform for b in bones.values()),
       "joint_names": rig.joint_names(), "bossforge": {k: rig.bone_for_bossforge_bone(k) for k in ["arm_r", "forearm_l", "shin_r", "root"]},
       "index": {n: rig.joint_index(n) for n in rig.joint_names()}, "loc": list(o.location), "scale": list(o.scale), "rot": list(o.rotation_euler),
       "prop": o.get("bossforge_rig")}
bfk.emit(res)
''')
        res = blender.run(script, {}, blend=blend)
        joints = efmodel._rig()["joints"]
        self.assertEqual(res["joint_names"], [j["name"] for j in joints])
        self.assertEqual(sorted(res["names"]), sorted(res["joint_names"]))
        for j in joints:
            want = efmodel.to_blender(j["pos"])
            self.assertLess(max(abs(a - b) for a, b in zip(res["heads"][j["name"]], want)), 1e-6, j["name"])
            self.assertEqual(res["index"][j["name"]], j["index"])
            t, h = res["tails"][j["name"]], res["heads"][j["name"]]
            self.assertGreater(math.dist(t, h), 0.01)  # no zero-length bones
        self.assertEqual(res["connected"], [])
        self.assertTrue(res["deform"])
        self.assertEqual(res["bossforge"], {"arm_r": "Arm_R", "forearm_l": "Hand_L", "shin_r": "Leg_R", "root": "Root"})
        self.assertEqual((res["loc"], res["scale"], res["rot"]), ([0, 0, 0], [1, 1, 1], [0, 0, 0]))
        self.assertEqual(res["prop"], "epicfight:entity/biped")
        self.assertEqual(res["parents"]["Chest"], "Torso")
        self.assertEqual(res["parents"]["Elbow_R"], "Arm_R")
        self.assertEqual(res["parents"]["Root"], None)
        if EF_BIPED:  # the hierarchy hard-coded in bfk.rig equals EF's own
            world = hierarchy_world(json.loads(EF_BIPED.read_text()))
            self.assertEqual(res["parents"], {n: p for n, (_m, p) in world.items()})
            for n, (m, _p) in world.items():  # rigs.json positions (4 decimals) = product of EF's joint matrices
                self.assertLess(max(abs(m[i][3] - res["heads"][n][i]) for i in range(3)), 1e-4, n)

    def test_king_arthur_shape_round_trip(self):
        src = self.shape_mesh()
        blend = self.dir / "ka.blend"
        code, imp = self.cli("blender", "import", str(src), "--out", str(blend))
        self.assertEqual(code, 0, imp)
        self.assertEqual(imp["influence_histogram"], {"1": imp["positions"]})
        self.assertEqual(imp["groups"], ["skin"])
        out_dir = self.dir / "ka_out"
        code, exp = self.cli("blender", "export", str(blend), "--out", str(out_dir), "--id", "king_arthur")
        self.assertEqual(code, 0, exp)
        self.assertEqual(exp["errors"], [])
        self.assertEqual(exp["check_mesh_json"], [])
        a = json.loads(src.read_text())
        b = json.loads((out_dir / "king_arthur.efmesh.json").read_text())
        self.assertEqual(efmodel.check_mesh_json(b), [])
        cmp = compare_meshes(a, b)
        for part, (na, nb, aa, ab) in cmp["parts"].items():
            self.assertEqual((na, aa), (nb, ab), part)  # same triangle (and placeholder) count per part
        self.assertEqual(cmp["unmatched"], 0)
        self.assertLess(cmp["max_pos"], 1e-4)
        self.assertLess(cmp["max_uv"], 1e-4)
        self.assertLess(cmp["max_nrm"], 1e-3)  # Blender stores custom normals as int16 pairs
        self.assertEqual(cmp["joint_mismatch"], 0)
        self.assertEqual(cmp["max_w"], 0.0)
        meta = json.loads((out_dir / "king_arthur.meshmeta.json").read_text())
        self.assertEqual((meta["schema"], meta["id"], meta["units"], meta["generator"]), ("bossforge-mesh/1", "king_arthur", "blocks", "bfk/1"))
        self.assertEqual(meta["joint_names"], [j["name"] for j in efmodel._rig()["joints"]])
        for part in efmodel.EF_PARTS:
            self.assertEqual(len(meta["tri_groups"][part]), len(b["vertices"]["parts"][part]["array"]) // 9)
        zs = b["vertices"]["positions"]["array"][2::3]
        self.assertAlmostEqual(meta["height"], round(max(zs) - min(zs), 5), places=4)

    @needs_biped
    def test_stock_biped_round_trip_and_pose(self):
        blend = self.dir / "biped.blend"
        code, imp = self.cli("blender", "import", str(EF_BIPED), "--out", str(blend))
        self.assertEqual(code, 0, imp)
        self.assertGreater(max(int(k) for k in imp["influence_histogram"]), 1)  # smooth weights, vcounts > 1
        out_dir = self.dir / "biped_out"
        code, exp = self.cli("blender", "export", str(blend), "--out", str(out_dir), "--id", "biped")
        self.assertEqual(code, 0, exp)
        a = json.loads(EF_BIPED.read_text())
        b = json.loads((out_dir / "biped.efmesh.json").read_text())
        cmp = compare_meshes(a, b)
        for part, (na, nb, aa, ab) in cmp["parts"].items():
            self.assertEqual((na, aa), (nb, ab), part)
        self.assertEqual(cmp["unmatched"], 0)
        self.assertLess(cmp["max_pos"], 1e-4)
        self.assertLess(cmp["max_uv"], 1e-4)
        self.assertEqual(cmp["joint_mismatch"], 0)
        self.assertLess(cmp["max_w"], 1e-4)
        self.assertEqual(exp["max_influences"], imp and max(int(k) for k in imp["influence_histogram"]))

        # pose: Blender (pose_bone_world_rotate on the EF_Biped rig) vs linear blend skinning of the JSON with EF's own matrices
        poses = {"arm_r_x60": [("Arm_R", "X", 60)], "arm_r_y60": [("Arm_R", "Y", 60)], "arm_r_negx60": [("Arm_R", "-X", 60)],
                 "legs": [("Thigh_R", "X", 45), ("Leg_R", "X", -70)],
                 "chain": [("Arm_R", "X", 60), ("Hand_R", "Z", 40), ("Thigh_L", "X", -50), ("Head", "Z", 30), ("Chest", "X", 15)]}
        script = write_script(self.dir, "pose.py", '''
import bfk
from bfk import efio, rig
a = bfk.get_args()
res = efio.import_efmesh(a["source"])
obj, arm = res["object"], res["armature_object"]
out = {}
for name, rots in a["poses"].items():
    rig.reset_pose(arm)
    for joint, axis, deg in rots:
        rig.pose_bone_world_rotate(arm, joint, axis, deg)
    out[name] = [list(p) for p in rig.evaluated_world_positions(obj)]
bfk.emit({"ok": True, "poses": out})
''')
        res = blender.run(script, {"source": str(EF_BIPED), "poses": poses})
        world = hierarchy_world(a)
        names = a["armature"]["joints"]
        rest = skin_positions(a, names, pose_deltas(world, []))
        for name, rots in poses.items():
            py = skin_positions(a, names, pose_deltas(world, rots))
            err = max(math.dist(p, q) for p, q in zip(py, res["poses"][name]))
            moved = max(math.dist(p, q) for p, q in zip(rest, py))
            self.assertGreater(moved, 0.2, name)  # the pose really moves the mesh
            self.assertLess(err, 1e-3, name)

    def test_import_with_sidecar_and_texture_then_edit_and_export(self):
        src = self.shape_mesh()
        # a bundle with a sidecar: two groups, split by the part of each triangle
        mesh = json.loads(src.read_text())
        bundle = self.dir / "bundle"
        bundle.mkdir()
        (bundle / "kt.efmesh.json").write_text(json.dumps(mesh))
        groups = ["skin", "robe"]
        tri_groups = {p: [(1 if p in ("torso", "jacket", "rightPants", "leftPants") else 0)] * (len(o["array"]) // 9) for p, o in mesh["vertices"]["parts"].items()}
        (bundle / "kt.meshmeta.json").write_text(json.dumps({"schema": "bossforge-mesh/1", "id": "kt", "height": 2.5, "units": "blocks", "groups": groups, "tri_groups": tri_groups}))
        tex = self.dir / "tex.png"
        cv = pngio.Canvas(8, 8)
        cv.fill((200, 30, 30, 255))
        pngio.write_png(tex, cv)
        blend = self.dir / "kt.blend"
        code, imp = self.cli("blender", "import", str(bundle), "--out", str(blend), "--texture", str(tex))
        self.assertEqual(code, 0, imp)
        self.assertEqual(imp["groups"], groups)
        script = write_script(self.dir, "check_import.py", '''
import bfk, bpy
from bfk import efio, rig
obj = bpy.data.objects["kt"]
me = obj.data
res = {"ok": True, "slots": [s.material.name for s in obj.material_slots], "uv": [u.name for u in me.uv_layers],
       "vgroups": [g.name for g in obj.vertex_groups], "mods": [(m.type, m.object.name) for m in obj.modifiers], "parent": obj.parent.name,
       "custom_normals": bool(me.has_custom_normals), "slot_use": sorted({p.material_index for p in me.polygons})}
tex = []
for mat in bpy.data.materials:
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE":
            tex.append({"interp": n.interpolation, "image": n.image.name, "packed": n.image.packed_file is not None, "size": list(n.image.size)})
            linked = [l.to_node.type for l in n.outputs["Color"].links]
            tex[-1]["to"] = linked
res["tex"] = tex
res["v_first_uv"] = list(me.uv_layers["UVMap"].uv[0].vector)
bfk.emit(res)
''')
        res = blender.run(script, {}, blend=blend)
        self.assertEqual(res["slots"], groups)
        self.assertEqual(res["uv"], ["UVMap"])
        self.assertEqual(res["vgroups"], [j["name"] for j in efmodel._rig()["joints"]])
        self.assertEqual(res["mods"], [["ARMATURE", "EF_Biped"]])
        self.assertEqual(res["parent"], "EF_Biped")
        self.assertTrue(res["custom_normals"])
        self.assertEqual(res["slot_use"], [0, 1])
        self.assertEqual(len(res["tex"]), 2)
        for t in res["tex"]:
            self.assertEqual((t["interp"], t["packed"], t["size"], t["to"]), ("Closest", True, [8, 8], ["BSDF_PRINCIPLED"]))
        # the first loop's UV is v-flipped relative to the JSON (first triangle of the first non-empty part)
        v = mesh["vertices"]
        first_part = next(p for p in efmodel.EF_PARTS if len(v["parts"][p]["array"]) and v["parts"][p]["array"][0] != v["parts"][p]["array"][3])
        ui = v["parts"][first_part]["array"][1]
        self.assertAlmostEqual(res["v_first_uv"][0], v["uvs"]["array"][2 * ui], places=5)
        self.assertAlmostEqual(res["v_first_uv"][1], 1.0 - v["uvs"]["array"][2 * ui + 1], places=5)
        # the groups survive an export (material slots -> meshmeta groups, same per-triangle assignment)
        out_dir = self.dir / "kt_out"
        code, exp = self.cli("blender", "export", str(blend), "--out", str(out_dir))
        self.assertEqual(code, 0, exp)
        self.assertEqual(exp["id"], "kt")
        meta = json.loads((out_dir / "kt.meshmeta.json").read_text())
        self.assertEqual(sorted(meta["groups"]), ["robe", "skin"])
        names = {g: i for i, g in enumerate(meta["groups"])}
        for p in efmodel.EF_PARTS:
            want = names["robe" if p in ("torso", "jacket", "rightPants", "leftPants") else "skin"]
            real = meta["tri_groups"][p] if exp["triangles_by_part"][p] > 1 else []  # a lone triangle is the empty-part anchor
            self.assertTrue(real and all(g == want for g in real) or not real, p)
        self.assertEqual(set(meta["group_bones"]), {"skin", "robe"})

    def test_artist_mesh_export_warnings_and_rules(self):
        script = write_script(self.dir, "artist.py", '''
import bpy, bmesh, bfk, json
from bfk import efio, rig
from mathutils import Vector
a = bfk.get_args()
bfk.reset_scene()
arm = rig.build_rig()
bm = bmesh.new()
bmesh.ops.create_cube(bm, size=1.0)
for v in bm.verts:
    v.co.x *= 0.5; v.co.y *= 0.3; v.co.z += 0.9      # x +-0.25, y +-0.15, z 0.4..1.4
uvl = bm.loops.layers.uv.new("UVMap")
for f in bm.faces:
    for l in f.loops:
        l[uvl].uv = (0.5 + l.vert.co.x, l.vert.co.z / 1.5)
bm.faces.ensure_lookup_table()
for f in bm.faces:
    f.material_index = 0 if f.normal.z > 0.5 or f.normal.y > 0.5 else 1
bm.faces[2].loops[0][uvl].uv = (1.3, 0.2)             # outside 0..1
me = bpy.data.meshes.new("Body")
bm.to_mesh(me); bm.free()
me.materials.append(bpy.data.materials.new("Robe.001")); me.materials.append(bpy.data.materials.new("Skin"))
obj = bpy.data.objects.new("Body", me)
bpy.context.scene.collection.objects.link(obj)
mod = obj.modifiers.new("Armature", 'ARMATURE'); mod.object = arm
obj.parent = arm
for n in ("Torso", "Chest", "Head", "Shoulder_R", "Shoulder_L", "Arm_R", "Bone.001"):
    obj.vertex_groups.new(name=n)
for v in me.vertices:
    obj.vertex_groups["Torso"].add([v.index], 0.2, 'REPLACE')
    obj.vertex_groups["Chest"].add([v.index], 0.3, 'REPLACE')
    obj.vertex_groups["Bone.001"].add([v.index], 1.0, 'REPLACE')
    if v.co.z > 1.0:
        for n, w in (("Head", 0.25), ("Shoulder_R", 0.15), ("Shoulder_L", 0.05), ("Arm_R", 0.04)):
            obj.vertex_groups[n].add([v.index], w, 'REPLACE')
# vertex 0 has no EF weight at all
for g in obj.vertex_groups:
    if g.name != "Bone.001":
        g.remove([0])
obj.vertex_groups["Bone.001"].remove([0])
obj["unweighted_vertex_is_zero"] = True
res = {"ok": True}
res["report"] = efio.export_efmesh(obj, arm, a["out"], "synth")
# rest pose export is independent of the current pose
rig.pose_bone_world_rotate(arm, "Chest", "X", 30)
res["posed_moves_mesh"] = max((Vector(p) - v.co).length for p, v in zip(rig.evaluated_world_positions(obj), me.vertices))
efio.export_efmesh(obj, arm, a["out"] + "_posed", "synth")
res["pose_independent"] = open(a["out"] + "/synth.efmesh.json").read() == open(a["out"] + "_posed/synth.efmesh.json").read()
rig.reset_pose(arm)
# a hidden object still exports (and stays hidden)
obj.hide_viewport = True
obj.hide_set(True)
arr = obj.modifiers.new("Array", 'ARRAY')
arr.count = 2
arr.use_relative_offset = False
arr.use_constant_offset = True
arr.constant_offset_displace = (0.0, 0.0, 1.0)
hid = efio.export_efmesh(obj, arm, a["out"] + "_hidden", "synth")
res["hidden_ok"], res["hidden_tris"] = hid["ok"], hid["triangles"]   # modifier results are exported even when the object is hidden
res["hidden_state"] = [obj.hide_viewport, obj.hide_get()]
obj.modifiers.remove(arr)
obj.hide_viewport = False
obj.hide_set(False)
strict = efio.export_efmesh(obj, arm, a["out"] + "_strict", "synth", strict=True)
res["strict_ok"], res["strict_errors"] = strict["ok"], [e["code"] for e in strict["errors"]]
res["trim3"] = efio.export_efmesh(obj, arm, a["out"] + "_t3", "synth", max_influences=3)["max_influences"]
# the source object is left untouched: still quads, armature modifier still on
res["still_quads"] = all(p.loop_total == 4 for p in obj.data.polygons)
res["mod_visible"] = mod.show_viewport
# moved object: baked into the export
obj.parent = None
obj.location = (0.0, 0.5, 0.0)
moved = efio.export_efmesh(obj, arm, a["out"] + "_moved", "synth")
res["moved_codes"] = [w["code"] for w in moved["warnings"]]
res["moved_bounds"] = moved["bounds"]
# no UV map -> hard error
while me.uv_layers:
    me.uv_layers.remove(me.uv_layers[0])
res["no_uv"] = efio.run_export({"out": a["out"] + "_nouv", "object": "Body"})
bfk.emit(res)
''')
        out = self.dir / "synth_out"
        res = blender.run(script, {"out": str(out)})
        rep = res["report"]
        self.assertTrue(rep["ok"], rep)
        codes = {w["code"] for w in rep["warnings"]}
        self.assertTrue({"triangulated", "influences_trimmed", "unweighted", "ignored_groups", "uv_out_of_range"} <= codes, codes)
        self.assertEqual(rep["triangles"], 12)
        self.assertEqual(rep["groups"], ["robe", "skin"])  # 'Robe.001' -> robe, 'Skin' -> skin (material slot order)
        self.assertEqual(rep["check_mesh_json"], [])
        self.assertLessEqual(rep["max_influences"], 4)
        self.assertEqual(res["trim3"], 3)
        self.assertEqual(rep["positions"], 8 + sum(1 for p in rep["triangles_by_part"].values() if p == 1))  # 8 vertices + one anchor per empty part
        d = json.loads((out / "synth.efmesh.json").read_text())
        self.assertEqual(efmodel.check_mesh_json(d), [])
        v = d["vertices"]
        self.assertEqual(v["weights"]["array"][0], 1.0)
        self.assertEqual(len(v["normals"]["array"]) // 3, 6 + 1)  # flat cube: six face normals (+ the shared anchor normal)
        for c in range(len(v["vcounts"]["array"])):
            self.assertGreaterEqual(v["vcounts"]["array"][c], 1)
        # the unweighted vertex was bound like its neighbours: no vertex lost its influences
        self.assertTrue(all(c >= 1 for c in v["vcounts"]["array"]))
        # dominant joint decides the part: every real triangle is Torso/Chest weighted -> torso
        self.assertEqual(rep["triangles_by_part"]["torso"], 12)
        self.assertEqual(rep["triangles_by_part"]["head"], 1)
        meta = json.loads((out / "synth.meshmeta.json").read_text())
        self.assertEqual(meta["groups"], ["robe", "skin"])
        self.assertEqual(len(meta["tri_groups"]["torso"]), 12)
        self.assertEqual(sorted(set(meta["tri_groups"]["torso"])), [0, 1])
        self.assertAlmostEqual(meta["height"], 1.0, places=4)
        self.assertFalse(res["strict_ok"])
        self.assertEqual(res["strict_errors"], ["unweighted"])
        self.assertGreater(res["posed_moves_mesh"], 0.1)  # the pose did move the evaluated mesh ...
        self.assertTrue(res["pose_independent"])  # ... and the export is the rest pose anyway
        self.assertEqual((res["hidden_ok"], res["hidden_tris"], res["hidden_state"]), (True, 24, [True, True]))
        self.assertTrue(res["still_quads"])
        self.assertTrue(res["mod_visible"])
        self.assertIn("transform_baked", res["moved_codes"])
        self.assertAlmostEqual(res["moved_bounds"]["min"][1], 0.5 - 0.15, places=3)
        nouv = res["no_uv"]
        self.assertFalse(nouv["ok"])
        self.assertEqual(nouv["errors"][0]["code"], "no_uv")

    def test_import_export_error_paths(self):
        code, out = self.cli("blender", "import", str(self.dir / "missing.json"), "--out", str(self.dir / "x.blend"))
        self.assertEqual((code, out["ok"], out["errors"][0]["code"]), (1, False, "bad_bundle"))
        bad = self.dir / "notmesh.json"
        bad.write_text('{"vertices": {"positions": 1}}')
        code, out = self.cli("blender", "import", str(bad), "--out", str(self.dir / "x.blend"))
        self.assertEqual((code, out["errors"][0]["code"]), (1, "bad_mesh"))
        bad.write_text("not json at all")
        code, out = self.cli("blender", "import", str(bad), "--out", str(self.dir / "x.blend"))
        self.assertEqual((code, out["errors"][0]["code"]), (1, "bad_mesh"))
        golem = EF_BIPED.with_name("iron_golem.json") if EF_BIPED else None
        if golem is not None and golem.is_file():
            code, out = self.cli("blender", "import", str(golem), "--out", str(self.dir / "x.blend"))
            self.assertEqual((code, out["errors"][0]["code"]), (1, "wrong_armature"))
        blend = self.dir / "ka_err.blend"
        self.assertEqual(self.cli("blender", "import", str(self.shape_mesh()), "--out", str(blend))[0], 0)
        code, out = self.cli("blender", "export", str(blend), "--out", str(self.dir / "o"), "--object", "nope")
        self.assertEqual((code, out["errors"][0]["code"]), (1, "no_object"))
        code, out = self.cli("blender", "export", str(blend), "--out", str(self.dir / "o"), "--id", "Bad Id")
        self.assertEqual((code, out["errors"][0]["code"]), (1, "bad_id"))
        code, out = self.cli("blender", "export", str(self.dir / "nope.blend"), "--out", str(self.dir / "o"))
        self.assertEqual(code, 1)
        self.assertEqual(out["errors"][0]["code"], "blender_failed")
        # two skinned meshes in one file are ambiguous without --object
        script = write_script(self.dir, "two.py", '''
import bpy, bfk
from bfk import efio
a = bfk.get_args()
src = bpy.data.objects["king_arthur"]
twin = src.copy(); twin.data = src.data.copy(); twin.name = "twin"
bpy.context.scene.collection.objects.link(twin)
r = efio.run_export({"out": a["out"]})
r2 = efio.run_export({"out": a["out"], "object": "twin", "id": "twin"})
bfk.emit({"ok": True, "amb": r["errors"][0]["code"], "named_ok": r2["ok"]})
''')
        res = blender.run(script, {"out": str(self.dir / "two_out")}, blend=blend)
        self.assertEqual((res["amb"], res["named_ok"]), ("ambiguous_object", True))

    def test_facing_check_uses_the_feet(self):
        script = write_script(self.dir, "facing.py", '''
import bpy, bmesh, bfk
from bfk import efio, rig
a = bfk.get_args()
out = {"ok": True}
for label, y0, y1 in (("forward", -0.05, 0.35), ("backward", -0.35, 0.05), ("symmetric", -0.2, 0.2)):
    bfk.reset_scene()
    arm = rig.build_rig()
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co.x = 0.125 + v.co.x * 0.2
        v.co.y = (y0 + y1) / 2 + v.co.y * (y1 - y0)
        v.co.z = 0.025 + v.co.z * 0.05 + (0.3 if v.co.z > 0 else 0.0)
    uvl = bm.loops.layers.uv.new("UVMap")
    for f in bm.faces:
        for l in f.loops:
            l[uvl].uv = (0.5 + l.vert.co.x, 0.5 + l.vert.co.z)
    me = bpy.data.meshes.new("Foot"); bm.to_mesh(me); bm.free()
    obj = bpy.data.objects.new("Foot", me); bpy.context.scene.collection.objects.link(obj)
    g = obj.vertex_groups.new(name="Leg_R")
    g.add(list(range(len(me.vertices))), 1.0, 'REPLACE')
    rep = efio.export_efmesh(obj, arm, a["out"] + label, "foot")
    out[label] = {"verdict": rep["facing"]["verdict"], "codes": [w["code"] for w in rep["warnings"]], "ok": rep["ok"]}
bfk.emit(out)
''')
        res = blender.run(script, {"out": str(self.dir / "facing_")})
        self.assertEqual(res["forward"]["verdict"], "forward")
        self.assertNotIn("facing_backward", res["forward"]["codes"])
        self.assertEqual(res["backward"]["verdict"], "backward")
        self.assertIn("facing_backward", res["backward"]["codes"])
        self.assertTrue(res["backward"]["ok"])  # a warning, not an error
        self.assertEqual(res["symmetric"]["verdict"], "unknown")

    def test_view_front_camera_looks_at_the_face(self):
        script = write_script(self.dir, "cam.py", '''
import bfk, bpy, math
from bfk import rig
from mathutils import Vector
bfk.reset_scene()
out = {}
for az in ("front", "side", "back", 90.0, -90.0):
    cam = rig.view_front_camera(target=(0, 0, 1.0), distance=5.0, azimuth=az)
    bpy.context.view_layer.update()
    look = cam.matrix_world.to_quaternion() @ Vector((0, 0, -1))
    out[str(az)] = {"loc": [round(c, 4) for c in cam.location], "look": [round(c, 4) for c in look]}
bfk.emit({"ok": True, "cams": out})
''')
        res = blender.run(script, {})["cams"]
        self.assertEqual(res["front"]["loc"], [0, 5, 1])
        self.assertEqual(res["front"]["look"], [0, -1, 0])  # camera at +Y looks along -Y at the face
        self.assertEqual(res["back"]["loc"], [0, -5, 1])
        self.assertEqual(res["90.0"]["loc"], [-5, 0, 1])  # +90 = the character's left side (-X)
        self.assertEqual(res["-90.0"]["loc"], [5, 0, 1])
        self.assertEqual(res["side"]["loc"], res["90.0"]["loc"])


if __name__ == "__main__":
    unittest.main()
