import _path  # noqa
import unittest

from bossforge_core import efassets, efmesh
from bossforge_core.spec import data

REF = data("versions")["epicfight"]["assets_ref"]
CACHE = efassets.default_cache_dir()


def cached(name):
    return (CACHE / "ef" / REF[:8] / (name + ".json")).is_file()


@unittest.skipUnless(cached("biped") and cached("iron_golem"), "EF meshes not cached (run fetch-ef-assets)")
class RealMeshTests(unittest.TestCase):
    def load(self, tname):
        tpl = data("templates")[tname]
        path = efassets.mesh_file(tpl["mesh_name"], CACHE, REF, offline=True)
        return efmesh.load_mesh(path, tpl), tpl

    def test_parts_and_sizes(self):
        for tname, nparts in (("biped", 12), ("iron_golem", 7)):
            m, tpl = self.load(tname)
            self.assertEqual(len(m.parts()), nparts)
            self.assertEqual(set(m.parts()), set(tpl["part_regions"]))
            self.assertEqual(list(efmesh.estimate_authored_size(m)), tpl["authored_uv"])
            overlay = set(tpl["overlay_parts"])
            lo, hi = m.bounds([p for p in m.parts() if p not in overlay])
            self.assertAlmostEqual((hi[1] - lo[1]) / tpl["model_height"], 1.0, delta=0.01)

    def test_topmost_vertex_is_head_and_front_island_covered(self):
        m, tpl = self.load("biped")
        top = max(m.tris, key=lambda t: max(m.positions[i][1] for i in t.p))
        self.assertIn(top.part, ("head", "hat"))
        # head front face: normal along front_sign, UV centre inside the (8,8)-(16,16) pixel box of the 64x64 layout
        W, H = tpl["authored_uv"]
        hits = 0
        for t in m.tris:
            if t.part != "head":
                continue
            n = m.normals[t.n[0]]
            if n[2] * tpl["front_sign"] > 0.9:
                cx = sum(m.uvs[i][0] for i in t.t) / 3 * W
                cy = sum(m.uvs[i][1] for i in t.t) / 3 * H
                if 8 <= cx <= 16 and 8 <= cy <= 16:
                    hits += 1
        self.assertGreaterEqual(hits, 2)


if __name__ == "__main__":
    unittest.main()
