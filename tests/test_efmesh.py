import _path  # noqa
import json
import tempfile
import unittest
from pathlib import Path

from bossforge_core import efmesh
from bossforge_core.spec import BossForgeError


def box_part(base):
    # one quad = 2 triangles = 6 vertices = 18 ints (pos, uv, nrm)
    idx = []
    for tri in ((0, 1, 2), (0, 2, 3)):
        for k in tri:
            idx += [base + k, base + k, 0]
    return {"array": idx}


def write_mesh(path, bad=False):
    # Blender Z-up: head at +z (up), rightArm at +x, leftArm at -x
    def quad(cx, cz):
        return [cx - 0.1, 0.0, cz - 0.1, cx + 0.1, 0.0, cz - 0.1, cx + 0.1, 0.0, cz + 0.1, cx - 0.1, 0.0, cz + 0.1]
    pos = quad(0.0, 1.8) + quad(0.4, 1.2) + quad(-0.4, 1.2)
    uvs = []
    for ox in (0, 9, 17):
        for (u, v) in ((0, 0), (8, 0), (8, 8), (0, 8)):
            uvs += [(ox + u) / 64.0, (9 + v) / 64.0]
    parts = {"head": box_part(0), "rightArm": box_part(4), "leftArm": box_part(8)}
    if bad:
        parts["head"]["array"].pop()
    with open(path, "w") as f:
        json.dump({"vertices": {"positions": {"array": pos}, "normals": {"array": [0.0, -1.0, 0.0]},
                                "uvs": {"array": uvs}, "parts": parts}}, f)


TEMPLATE = {"axis_mode": "x,z,-y", "unit_scale": 1.0, "index_order": "pos,uv,nrm", "front_sign": -1,
            "part_regions": {"head": "head", "rightArm": "arm_r", "leftArm": "arm_l"}}


class MeshTests(unittest.TestCase):
    def test_load_converts_axes_and_maps_regions(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            write_mesh(p)
            m = efmesh.load_mesh(p, TEMPLATE)
            self.assertEqual(len(m.tris), 6)
            # Blender (x, y, z) -> (x, z, -y): head quad centre z=1.8 becomes y=1.8
            lo, hi = m.bounds(["head"])
            self.assertAlmostEqual(hi[1], 1.9)
            self.assertAlmostEqual(lo[1], 1.7)
            self.assertEqual(m.region_of("rightArm"), "arm_r")
            self.assertEqual(efmesh.frame(m, TEMPLATE), (-1, 1))
            self.assertEqual(efmesh.infer_authored_size(m.uvs), (64, 64))

    def test_bad_part_array_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            write_mesh(p, bad=True)
            with self.assertRaises(BossForgeError) as cm:
                efmesh.load_mesh(p, TEMPLATE)
            self.assertEqual(cm.exception.err.code, "bad_part_array")


if __name__ == "__main__":
    unittest.main()
