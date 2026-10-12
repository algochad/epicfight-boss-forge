import _path  # noqa
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from bossforge_core import pngio, refbake, reference, skin, spec
from bossforge_core.efmesh import Mesh, Tri
from bossforge_core.spec import BossForgeError

FACES = ("front", "back", "left", "right", "top", "bottom")
PX = 16.0  # reference pixels per model block in most tests; density 16 => one texel = exactly one reference pixel


# --------------------------------------------------------------------------- a tiny box-built model
class Boxes:
    """Boxes (id, min, size, region[, rot_y, rot_z]) with one atlas rectangle per face, in model space (y up, front = -z,
    the character's right = +x). Rotations (degrees) turn the box about its centre: z first, then y."""

    def __init__(self, density=16, size=128, flip_x=False, flip_z=False):
        self.density = density
        self.size = size
        self.boxes = []
        self.faces = {}
        self.flip_x = flip_x  # the character's right is at -x (right_sign -1)
        self.flip_z = flip_z  # the character faces +z (front_sign +1)

    def add(self, bid, mn, sz, region="torso", rot_y=0.0, rot_z=0.0, dens=1.0):
        self.boxes.append((bid, mn, sz, region, rot_y, rot_z, dens))
        return self

    def build(self):
        size = self.size
        x = y = row = 0
        for bid, _mn, sz, _r, _ry, _rz, dens in self.boxes:
            w, h, dp = (max(2, int(round(c * self.density * dens))) for c in sz)
            dims = {"front": (w, h), "back": (w, h), "left": (dp, h), "right": (dp, h), "top": (w, dp), "bottom": (w, dp)}
            for f in FACES:
                fw, fh = dims[f]
                if x + fw > size:
                    x, y, row = 0, y + row + 2, 0
                self.faces[(bid, f)] = (x, y, fw, fh)
                x += fw + 2
                row = max(row, fh)
        pos, nrm, uvs, tris = [], [], [], []
        regions = {}
        for bid, mn, sz, region, ry, rz, _d in self.boxes:
            regions[bid] = region
            x0, y0, z0 = mn
            x1, y1, z1 = x0 + sz[0], y0 + sz[1], z0 + sz[2]
            cx, cy, cz = (x0 + x1) / 2, (y0 + y1) / 2, (z0 + z1) / 2
            cyr, syr = math.cos(math.radians(ry)), math.sin(math.radians(ry))
            czr, szr = math.cos(math.radians(rz)), math.sin(math.radians(rz))

            def rot(v, c=(cx, cy, cz)):
                dx, dy, dz = v[0] - c[0], v[1] - c[1], v[2] - c[2]
                dx, dy = czr * dx - szr * dy, szr * dx + czr * dy  # about z
                dx, dz = cyr * dx + syr * dz, -syr * dx + cyr * dz  # about y
                return (c[0] + dx, c[1] + dy, c[2] + dz)

            def rotn(n):
                dx, dy, dz = n
                dx, dy = czr * dx - szr * dy, szr * dx + czr * dy
                dx, dz = cyr * dx + syr * dz, -syr * dx + cyr * dz
                return (dx, dy, dz)

            quads = {
                "front": ([(x1, y1, z0), (x0, y1, z0), (x0, y0, z0), (x1, y0, z0)], (0, 0, -1)),
                "back": ([(x0, y1, z1), (x1, y1, z1), (x1, y0, z1), (x0, y0, z1)], (0, 0, 1)),
                "left": ([(x0, y1, z0), (x0, y1, z1), (x0, y0, z1), (x0, y0, z0)], (-1, 0, 0)),
                "right": ([(x1, y1, z1), (x1, y1, z0), (x1, y0, z0), (x1, y0, z1)], (1, 0, 0)),
                "top": ([(x1, y1, z1), (x0, y1, z1), (x0, y1, z0), (x1, y1, z0)], (0, 1, 0)),
                "bottom": ([(x1, y0, z0), (x0, y0, z0), (x0, y0, z1), (x1, y0, z1)], (0, -1, 0)),
            }
            for f in FACES:
                corners, normal = quads[f]
                rx, ry_, rw, rh = self.faces[(bid, f)]
                pi, ti = len(pos), len(uvs)
                pos.extend(rot(p) for p in corners)
                nrm.append(rotn(normal))
                ni = len(nrm) - 1
                for (u, v) in ((rx, ry_), (rx + rw, ry_), (rx + rw, ry_ + rh), (rx, ry_ + rh)):
                    uvs.append((u / float(size), v / float(size)))
                for tri in ((0, 1, 2), (0, 2, 3)):
                    tris.append(Tri(bid, tuple(pi + k for k in tri), tuple(ti + k for k in tri), (ni, ni, ni)))
        sx, sz = (-1.0 if self.flip_x else 1.0), (-1.0 if self.flip_z else 1.0)
        pos = [(sx * p[0], p[1], sz * p[2]) for p in pos]
        nrm = [(sx * n[0], n[1], sz * n[2]) for n in nrm]
        tpl = {"label": "Custom", "humanoid": True, "part_regions": regions, "part_style": {}, "overlay_parts": [],
               "overlay_to_base": True, "authored_uv": [size, size], "uv_origin": "top", "front_sign": int(-sz), "right_sign": int(sx),
               "axis_mode": "x,z,-y", "unit_scale": 1.0, "index_order": "pos,uv,nrm", "custom": True}
        return Mesh(pos, nrm, uvs, tris, regions), tpl


def appearance(**kw):
    ap = {"palette": {}, "regions": {"torso": {"material": "cloth", "color": "#808080"},
                                     "head": {"material": "cloth", "color": "#806060"}}, "features": []}
    ap.update(kw)
    return ap


W = H = 64


def pat_a(px, py):
    return ((px * 5) % 256, (py * 5 + 40) % 256, ((px + py) * 3) % 256)


def pat_b(px, py):
    return ((px * 7 + 90) % 256, (py * 3) % 256, ((px * 2 + py) * 5) % 256)


def view(name, pat=pat_a, mask=None, ppb=PX, gy=48.0, cx=32.0, w=W, h=H):
    rgba = bytearray()
    for y in range(h):
        for x in range(w):
            rgba += bytes(pat(x, y) + (255,))
    m = bytes([255]) * (w * h) if mask is None else bytes(mask)
    return reference.View.synthetic(name, rgba, m, w, h, ppb, gy, cx)


def bake(views, **kw):
    cfg = refbake.normalize_bake(dict({"views": list(views)}, **kw), list(views))
    return refbake.BakePlan(views, cfg)


def paint(builder, plan=None, ap=None, seed=3):
    mesh, tpl = builder.build()
    canvas, info = skin.paint(mesh, tpl, ap or appearance(), seed, ref_bake=plan)
    return canvas, info


def face_texels(builder, bid, face):
    x, y, w, h = builder.faces[(bid, face)]
    return x, y, w, h


def one_box():
    return Boxes().add("a", (-1.0, 0.0, -0.5), (2.0, 2.0, 1.0))  # front face spans pixels 16..48 of a 64px image


class Exactness(unittest.TestCase):
    def test_texel_centres_reproduce_the_reference_pixels_front_and_back(self):
        b = one_box()
        views = {"front": view("front", pat_a), "back": view("back", pat_b)}
        canvas, info = paint(b, bake(views, smear=False))
        for face, pat in (("front", pat_a), ("back", pat_b)):
            x, y, w, h = face_texels(b, "a", face)
            self.assertEqual((w, h), (32, 32))
            for j in range(h):
                for i in range(w):
                    self.assertEqual(canvas.get(x + i, y + j)[:3], pat(16 + i, 16 + j), (face, i, j))
        total = info["bake"]["total"]
        self.assertEqual(total["direct"], 2 * 32 * 32)
        self.assertEqual(total["edge"] + total["smeared"] + total["filled"], 0)
        self.assertEqual(info["bake"]["direct_ratio"], 1.0)
        self.assertEqual(info["warnings"], [])

    def test_footprint_is_the_box_filter_of_the_texel(self):
        b = Boxes(density=8).add("a", (-1.0, 0.0, -0.5), (2.0, 2.0, 1.0))  # texel = 2x2 reference pixels
        canvas, _ = paint(b, bake({"front": view("front")}, smear=False))
        x, y, w, h = face_texels(b, "a", "front")
        self.assertEqual((w, h), (16, 16))
        for j, i in ((0, 0), (3, 5), (15, 15), (7, 9)):
            px, py = 16 + 2 * i, 16 + 2 * j
            want = tuple(int(sum(pat_a(px + dx, py + dy)[c] for dx in (0, 1) for dy in (0, 1)) / 4.0 + 0.5) for c in range(3))
            self.assertEqual(canvas.get(x + i, y + j)[:3], want)

    def test_footprint_option_samples_the_inner_part_of_the_texel(self):
        b = Boxes(density=4).add("a", (-1.0, 0.0, -0.5), (2.0, 2.0, 1.0))  # texel = 4x4 reference pixels
        b.build()
        x, y, w, h = face_texels(b, "a", "front")
        self.assertEqual((w, h), (8, 8))

        def avg(px, py, lo, hi):
            pix = [pat_a(px + dx, py + dy) for dx in range(lo, hi) for dy in range(lo, hi)]
            return tuple(int(sum(p[c] for p in pix) / float(len(pix)) + 0.5) for c in range(3))

        whole, _ = paint(b, bake({"front": view("front")}, smear=False))
        inner, _ = paint(b, bake({"front": view("front")}, smear=False, footprint=0.5))
        for j, i in ((0, 0), (3, 5), (7, 7)):
            px, py = 16 + 4 * i, 16 + 4 * j
            self.assertEqual(whole.get(x + i, y + j)[:3], avg(px, py, 0, 4))
            self.assertEqual(inner.get(x + i, y + j)[:3], avg(px, py, 1, 3))  # the central 2x2 of the 4x4 pixels

    def test_rotated_box_texels_follow_the_projected_parallelogram(self):
        # a gradient image is reproduced by the box filter wherever the footprint is centred on the texel centre
        def grad(px, py):
            return (px * 3 + 20, py * 3 + 20, 128)
        b = Boxes().add("a", (-0.6, 0.4, -0.2), (1.2, 1.2, 0.4), rot_z=35.0)
        canvas, info = paint(b, bake({"front": view("front", grad)}, smear=False))
        x, y, w, h = face_texels(b, "a", "front")
        mesh, tpl = b.build()
        cover = skin._rasterize(mesh, tpl, 128, 128)
        errs = []
        for (tx, ty), (ti, l0, l1, l2) in cover.items():
            tri = mesh.tris[ti]
            if tri.part != "a" or ti % 12 not in (0, 1):  # the front face = the first two triangles of the box
                continue
            p = [mesh.positions[k] for k in tri.p]
            X = p[0][0] * l0 + p[1][0] * l1 + p[2][0] * l2
            Y = p[0][1] * l0 + p[1][1] * l1 + p[2][1] * l2
            px, py = 32.0 - PX * X, 48.0 - PX * Y
            want = grad(px, py)
            got = canvas.get(tx, ty)
            errs.append(max(abs(got[0] - want[0]), abs(got[1] - want[1])))
        self.assertGreater(len(errs), 300)
        self.assertLessEqual(sum(errs) / len(errs), 2.5)
        self.assertLessEqual(max(errs), 14)
        self.assertEqual(info["bake"]["total"]["direct"] + info["bake"]["total"]["edge"], len(errs))


class Conventions(unittest.TestCase):
    def test_meshes_with_the_mirrored_front_or_right_axis_bake_identically(self):
        views = {"front": view("front"), "back": view("back", pat_b)}

        def render(**flip):
            b = Boxes(**flip).add("a", (-1.0, 0.0, -0.5), (2.0, 2.0, 1.0)).add("c", (0.2, 0.2, 0.3), (0.5, 1.0, 0.4), "head", rot_y=20.0)
            mesh, tpl = b.build()
            plan = bake(views)
            canvas, info = skin.paint(mesh, tpl, appearance(), 3, ref_bake=plan)
            # procedural noise follows the (flipped) positions, so compare the baked texels only
            baked = [canvas.get(i % 128, i // 128)[:3] if plan.kinds[i] in (1, 2, 3) else None for i in range(128 * 128)]
            return bytes(plan.kinds), baked, info["bake"]["total"]

        plain = render()
        for flip in ({"flip_z": True}, {"flip_x": True}, {"flip_x": True, "flip_z": True}):
            self.assertEqual(render(**flip), plain, flip)
        self.assertGreater(plain[2]["direct"], 1000)


class Occlusion(unittest.TestCase):
    def _scene(self, gap):
        b = Boxes()
        b.add("front", (-1.0, 0.0, -0.8), (1.0, 2.0, 0.3), "torso")  # x -1..0 -> pixels 32..48
        b.add("behind", (-0.5, 0.0, -0.5 + gap), (1.0, 2.0, 0.3), "head")  # x -0.5..0.5 -> pixels 24..40
        return b

    def test_a_box_in_front_hides_the_box_behind(self):
        b = self._scene(1.0)
        views = {"front": view("front")}
        base, _ = paint(b)
        canvas, info = paint(b, bake(views, smear=False))
        x, y, w, h = face_texels(b, "behind", "front")
        self.assertEqual((w, h), (16, 32))
        for j in range(h):
            for i in range(w):
                got, old = canvas.get(x + i, y + j)[:3], base.get(x + i, y + j)[:3]
                if i < 8:  # pixels 24..32 are free
                    self.assertEqual(got, pat_a(24 + i, 16 + j), (i, j))
                else:  # pixels 32..40 are hidden behind the front box: never coloured with its pixels
                    self.assertEqual(got, old, (i, j))
        self.assertGreater(info["bake"]["total"]["occluded"], 0)
        self.assertGreater(info["bake"]["views"]["front"]["occluded"], 0)

    def test_depth_tolerance_decides_what_still_counts_as_visible(self):
        b = self._scene(0.0)  # the second box starts 0.3 behind the first one's back... its front face is 0.3 behind the first front face
        views = {"front": view("front")}
        tight = paint(b, bake(views, smear=False, depth_tolerance=0.05))[1]["bake"]["total"]["occluded"]
        loose = paint(b, bake(views, smear=False, depth_tolerance=0.5))[1]["bake"]["total"]["occluded"]
        self.assertGreater(tight, 0)
        self.assertEqual(loose, 0)


class Facing(unittest.TestCase):
    def _front_face_stats(self, rot_y, **kw):
        b = Boxes().add("a", (-0.5, 0.0, -0.25), (1.0, 2.0, 0.5), rot_y=rot_y)
        return paint(b, bake({"front": view("front")}, **kw))[1]["bake"]

    def test_min_facing_selects_the_faces_a_view_may_colour(self):
        # a box turned 50 degrees: its front face has facing cos(50) = 0.643, its right side face sin(50) = 0.766
        both = self._front_face_stats(50.0, min_facing=0.6, smear=False)["total"]
        self.assertEqual(both["direct"], 16 * 32 + 8 * 32)
        side = self._front_face_stats(50.0, min_facing=0.7, smear=False)["total"]
        self.assertEqual(side["direct"], 8 * 32)
        none = self._front_face_stats(50.0, min_facing=0.8, smear=False)["total"]
        self.assertEqual(none["direct"] + none["smeared"] + none["filled"], 0)
        self.assertEqual(none["visible"], 0)

    def test_smear_samples_grazing_faces_and_fills_hidden_ones(self):
        # turned 72.5 degrees the front face has facing 0.30 (grazing), the side face 0.95
        grazing = self._front_face_stats(72.5, min_facing=0.5, smear=True)
        self.assertEqual(grazing["total"]["direct"], 8 * 32)
        self.assertGreater(grazing["total"]["smeared"], 400)
        off = self._front_face_stats(72.5, min_facing=0.5, smear=False)
        self.assertEqual(off["total"]["smeared"] + off["total"]["filled"], 0)

    def test_side_and_top_faces_take_the_blend_of_the_baked_faces_they_join(self):
        b = one_box()
        b.build()
        base, _ = paint(b)
        canvas, info = paint(b, bake({"front": view("front", pat_a), "back": view("back", pat_b)}, smear=True))
        self.assertGreater(info["bake"]["total"]["filled"], 0)
        # the top face is edge-on in both views: texels at its front edge take the colour of the front face's top row
        fx, fy, fw, fh = face_texels(b, "a", "front")
        tx, ty, tw, th = face_texels(b, "a", "top")
        edge = [canvas.get(tx + i, ty + th - 1)[:3] for i in range(tw)]  # row nearest the front face (-z)
        d_front = sum(abs(edge[i][c] - canvas.get(fx + i, fy)[c]) for i in range(tw) for c in range(3)) / (3.0 * tw)
        d_proc = sum(abs(edge[i][c] - base.get(tx + i, ty + th - 1)[c]) for i in range(tw) for c in range(3)) / (3.0 * tw)
        self.assertLess(d_front, 8.0)
        self.assertLess(d_front, d_proc)
        # two texels in it is still filled, the middle of the 16 texel deep face is beyond the fill reach (procedural)
        near = (tx + 3, ty + th - 3)
        self.assertNotEqual(canvas.get(*near)[:3], base.get(*near)[:3])
        mid = (tx + 3, ty + th // 2)
        self.assertEqual(canvas.get(*mid)[:3], base.get(*mid)[:3])

    def test_a_face_is_never_mixed_from_two_views(self):
        # a box turned -45 degrees: its front face has facing 0.707 for BOTH the front and the right view, its left face
        # (256 texels) only sees the front, its right face (256 texels) only the right view. The tie goes to the first view
        # listed, as a whole face.
        b = Boxes().add("a", (-0.5, 0.0, -0.25), (1.0, 2.0, 0.5), rot_y=-45.0)
        v1 = {"front": view("front"), "right": view("right", pat_b)}
        s1 = paint(b, bake(v1, smear=False))[1]["bake"]["views"]
        self.assertEqual((s1["front"]["direct"], s1["right"]["direct"]), (512 + 256, 256))
        v2 = {"right": view("right", pat_b), "front": view("front")}
        s2 = paint(b, bake(v2, smear=False))[1]["bake"]["views"]
        self.assertEqual((s2["front"]["direct"], s2["right"]["direct"]), (256, 512 + 256))


    def test_seam_statistics_separate_edges_inside_one_view_from_edges_between_views(self):
        def smooth(px, py):
            return (px * 2 + 30, py * 2 + 30, 128)

        # turned -45 degrees: front face + left face see only the front view, the right face only the right view
        b = Boxes().add("a", (-0.5, 0.0, -0.25), (1.0, 2.0, 0.5), rot_y=-45.0)
        views = {"front": view("front", smooth), "right": view("right", pat_b)}
        seams = paint(b, bake(views, smear=False))[1]["bake"]["seams"]
        self.assertGreater(seams["same_view_pairs"], 20)
        self.assertGreater(seams["cross_view_pairs"], 20)
        self.assertLess(seams["same_view_mean_delta"], 12)
        self.assertGreater(seams["cross_view_mean_delta"], seams["same_view_mean_delta"] + 20)
        one = paint(b, bake({"front": view("front", smooth)}, smear=False))[1]["bake"]["seams"]
        self.assertEqual((one["cross_view_pairs"], one["cross_view_mean_delta"]), (0, None))


class Silhouette(unittest.TestCase):
    def test_background_is_never_averaged_and_edges_take_the_nearest_interior_colour(self):
        # foreground = pixels with px >= 20; everything left of it is a unique "wall" colour that must never show up
        wall = (1, 254, 1)
        self.assertNotIn(wall, {pat_a(x, y) for x in range(W) for y in range(H)})
        rgba = bytearray()
        mask = bytearray()
        for y in range(H):
            for x in range(W):
                rgba += bytes(wall + (255,) if x < 20 else pat_a(x, y) + (255,))
                mask.append(0 if x < 20 else 255)
        v = reference.View.synthetic("front", rgba, mask, W, H, PX, 48.0, 32.0)
        b = Boxes(density=8).add("a", (-1.0, 0.0, -0.5), (2.0, 2.0, 1.0))  # 2x2 px texels from px 16 on
        canvas, info = paint(b, bake({"front": v}, smear=False))
        x, y, w, h = face_texels(b, "a", "front")
        for j in range(h):
            for i in range(w):
                c = canvas.get(x + i, y + j)[:3]
                self.assertGreater(abs(c[1] - 254) + abs(c[0] - 1) + abs(c[2] - 1), 200, (i, j, c))
                if i < 2:  # fully background texels (px 16..19): the colour of the nearest interior pixel 21 (red channel 100..110)
                    self.assertTrue(98 <= c[0] <= 112, (i, j, c))
        total = info["bake"]["total"]
        self.assertEqual(total["direct"] + total["edge"], 16 * 16)
        self.assertEqual(total["edge"], 2 * 16)
        self.assertEqual(total["mask_miss"], 0)
        # a texel with nothing but foreground under it equals the box filter of its pixels
        i, j = 8, 4
        px, py = 16 + 2 * i, 16 + 2 * j
        want = tuple(int(sum(pat_a(px + dx, py + dy)[c] for dx in (0, 1) for dy in (0, 1)) / 4.0 + 0.5) for c in range(3))
        self.assertEqual(canvas.get(x + i, y + j)[:3], want)

    def test_a_model_far_outside_the_silhouette_warns_about_the_calibration(self):
        mask = bytearray(W * H)
        for y in range(H):
            for x in range(W):
                if x < 6:
                    mask[y * W + x] = 255
        v = view("front", mask=mask)
        stats = paint(one_box(), bake({"front": v}, smear=False))[1]
        codes = [w["code"] for w in stats["warnings"]]
        self.assertIn("bake_low_coverage", codes)
        self.assertGreater(stats["bake"]["total"]["mask_miss"], 100)
        self.assertLess(stats["bake"]["direct_ratio"], 0.6)
        self.assertIn("bake_view_unused", codes)

    def test_nothing_visible_is_reported(self):
        b = Boxes().add("a", (-0.5, 0.0, -0.25), (1.0, 2.0, 0.5), rot_y=10.0)
        stats = paint(b, bake({"front": view("front")}, min_facing=1.0, smear=False))[1]
        self.assertEqual(stats["bake"]["total"]["visible"], 0)
        self.assertEqual([w["code"] for w in stats["warnings"]], ["bake_nothing_visible"])


class Targeting(unittest.TestCase):
    def _two(self):
        return Boxes().add("a", (-1.0, 0.0, -0.5), (1.0, 1.0, 0.5), "torso").add("b", (0.0, 0.0, -0.5), (1.0, 1.0, 0.5), "head")

    def test_regions_limit_the_bake(self):
        b = self._two()
        base, _ = paint(b)
        canvas, _ = paint(b, bake({"front": view("front")}, smear=False, regions=["head"]))
        ax, ay, aw, ah = face_texels(b, "a", "front")
        bx, by, bw, bh = face_texels(b, "b", "front")
        self.assertEqual([canvas.get(ax + i, ay + j) for j in range(ah) for i in range(aw)], [base.get(ax + i, ay + j) for j in range(ah) for i in range(aw)])
        self.assertNotEqual([canvas.get(bx + i, by + j) for j in range(bh) for i in range(bw)], [base.get(bx + i, by + j) for j in range(bh) for i in range(bw)])

    def test_exclude_wins_over_regions_and_understands_part_globs(self):
        b = self._two()
        base, _ = paint(b)
        canvas, info = paint(b, bake({"front": view("front")}, smear=False, exclude=["part:a*"]))
        ax, ay, aw, ah = face_texels(b, "a", "front")
        self.assertEqual([canvas.get(ax + i, ay + j) for j in range(ah) for i in range(aw)], [base.get(ax + i, ay + j) for j in range(ah) for i in range(aw)])
        self.assertEqual(info["bake"]["total"]["texels"], sum(w * h for (bid, _f), (_x, _y, w, h) in b.faces.items() if bid == "b"))


class Finishing(unittest.TestCase):
    def test_order_decides_whether_features_or_the_bake_has_the_last_word(self):
        red = {"type": "gradient", "regions": ["part:a"], "from": "#ff0000", "to": "#ff0000", "amount": 1.0, "texture": 0.0}
        ap = appearance(features=[red])
        b = one_box()
        b.build()
        views = {"front": view("front")}
        x, y, w, h = face_texels(b, "a", "front")
        last, _ = paint(b, bake(views, smear=False, order="last"), ap)
        first, _ = paint(b, bake(views, smear=False, order="first"), ap)
        plain, _ = paint(b, None, ap)
        self.assertEqual(plain.get(x + 3, y + 3)[:3], (255, 0, 0))
        self.assertEqual(last.get(x + 3, y + 3)[:3], pat_a(19, 19))
        self.assertEqual(first.get(x + 3, y + 3)[:3], (255, 0, 0))  # features draw over the bake

    def test_blend_mixes_procedural_and_baked(self):
        b = one_box()
        base, _ = paint(b)
        half, _ = paint(b, bake({"front": view("front")}, smear=False, blend=0.5))
        x, y, w, h = face_texels(b, "a", "front")
        for i, j in ((0, 0), (5, 7), (20, 30)):
            old, new = base.get(x + i, y + j), pat_a(16 + i, 16 + j)
            got = half.get(x + i, y + j)
            for c in range(3):
                self.assertLessEqual(abs(got[c] - (old[c] + new[c]) / 2.0), 1.0)

    def test_palette_quantisation_is_deterministic_and_limits_the_colours(self):
        b = one_box()
        views = {"front": view("front"), "back": view("back", pat_b)}
        a, _ = paint(b, bake(views, smear=False, palette=8))
        again, _ = paint(b, bake(views, smear=False, palette=8))
        self.assertEqual(bytes(a.data), bytes(again.data))
        colours = set()
        for face in ("front", "back"):
            x, y, w, h = face_texels(b, "a", face)
            colours |= {a.get(x + i, y + j)[:3] for j in range(h) for i in range(w)}
        self.assertLessEqual(len(colours), 8)
        self.assertGreater(len(colours), 3)
        full, _ = paint(b, bake(views, smear=False))
        x, y, w, h = face_texels(b, "a", "front")
        self.assertGreater(len({full.get(x + i, y + j)[:3] for j in range(h) for i in range(w)}), 100)

    def test_median_cut_properties(self):
        hist = {(i * 4 % 256, i * 9 % 256, i * 13 % 256): 1 + i % 5 for i in range(300)}
        table = refbake.median_cut(hist, 16)
        self.assertEqual(set(table), set(hist))
        self.assertLessEqual(len(set(table.values())), 16)
        self.assertEqual(table, refbake.median_cut(dict(reversed(list(hist.items()))), 16))
        small = {(1, 2, 3): 4, (200, 100, 50): 1}
        self.assertEqual(refbake.median_cut(small, 8), {c: c for c in small})
        # a rare, distinct colour keeps its own palette entry
        mixed = {(i, i, i): 100 for i in range(0, 64, 2)}
        mixed[(0, 255, 255)] = 1
        self.assertIn((0, 255, 255), set(refbake.median_cut(mixed, 8).values()))

    def test_deterministic(self):
        views = {"front": view("front"), "back": view("back", pat_b)}
        one, _ = paint(one_box(), bake(views))
        two, _ = paint(one_box(), bake(views))
        self.assertEqual(bytes(one.data), bytes(two.data))

    def test_without_a_bake_the_painter_is_unchanged(self):
        b = one_box()
        a, info = paint(b)
        self.assertNotIn("bake", info)
        c, _ = paint(b, None)
        self.assertEqual(bytes(a.data), bytes(c.data))


class Validation(unittest.TestCase):
    def codes(self, bake_block, defined=("front", "back")):
        return {(e.path, e.code) for e in refbake.validate_bake(bake_block, "/reference/bake", defined)}

    def test_a_valid_block_has_no_errors(self):
        self.assertEqual(self.codes({}), set())
        self.assertEqual(self.codes({"views": ["front", "back"], "min_facing": 0.5, "depth_tolerance": 0.06, "smear": True, "palette": 16,
                                     "regions": ["head", "box:skirt_*", "part:fist_*"], "exclude": ["hands"], "order": "first", "blend": 0.5}), set())

    def test_every_problem_is_reported_with_a_code(self):
        got = self.codes({"views": ["front", "side"], "min_facing": 2, "depth_tolerance": -1, "smear": "yes", "palette": 4,
                          "regions": ["wings"], "exclude": "head", "order": "middle", "blend": 1.5, "turbo": True})
        self.assertIn(("/reference/bake/turbo", "unknown_key"), got)
        self.assertIn(("/reference/bake/views/1", "reference_unknown_view"), got)
        self.assertIn(("/reference/bake/min_facing", "out_of_range"), got)
        self.assertIn(("/reference/bake/depth_tolerance", "out_of_range"), got)
        self.assertIn(("/reference/bake/smear", "bad_type"), got)
        self.assertIn(("/reference/bake/palette", "out_of_range"), got)
        self.assertIn(("/reference/bake/regions/0", "unknown_region"), got)
        self.assertIn(("/reference/bake/exclude", "bad_type"), got)
        self.assertIn(("/reference/bake/order", "unknown_value"), got)
        self.assertIn(("/reference/bake/blend", "out_of_range"), got)
        self.assertIn(("/reference/bake/footprint", "out_of_range"), self.codes({"footprint": 0.1}))
        self.assertIn(("/reference/bake/footprint", "bad_type"), self.codes({"footprint": "1"}))

    def test_view_names_must_be_defined_and_unique(self):
        self.assertIn(("/reference/bake/views/0", "reference_unknown_view"), self.codes({"views": ["right"]}))
        self.assertIn(("/reference/bake/views/1", "duplicate_view"), self.codes({"views": ["front", "front"]}))
        self.assertIn(("/reference/bake/views", "bad_type"), self.codes({"views": []}))
        self.assertIn(("/reference/bake", "bad_type"), self.codes([]))
        self.assertIn(("/reference/bake/palette", "bad_type"), self.codes({"palette": 8.5}))
        self.assertIn(("/reference/bake/min_facing", "bad_type"), self.codes({"min_facing": "0.5"}))

    def test_defaults(self):
        cfg = refbake.normalize_bake({}, ["back", "left", "front"])
        self.assertEqual(cfg, {"views": ["front", "back", "left"], "min_facing": 0.5, "depth_tolerance": 0.06, "smear": True, "palette": 0,
                               "regions": ["*"], "exclude": [], "order": "last", "blend": 1.0, "footprint": 1.0})

    def test_plan_for_needs_the_views_it_names(self):
        n = {"reference": {"views": {"front": {}}, "bake": {"views": ["back"]}}, "_views": {"front": view("front")}}
        with self.assertRaises(BossForgeError) as cm:
            refbake.plan_for(n)
        self.assertEqual(cm.exception.err.code, "reference_unknown_view")
        self.assertIsNone(refbake.plan_for({"reference": {"views": {}}}))
        self.assertIsNone(refbake.plan_for({}))

    def test_spec_check_validates_the_bake_block(self):
        raw = spec.load(_path.EXAMPLE)
        view_block = {"image": "refs/none.png", "ground_y": 90, "center_x": 32, "px_per_block": 32}
        raw["reference"] = {"views": {"front": view_block}, "bake": {"min_facing": 5, "views": ["back"], "palette": 3}}
        _n, errs, _warns = spec.check(raw)
        got = {(e.path, e.code) for e in errs if e.path.startswith("/reference/bake")}
        self.assertEqual(got, {("/reference/bake/min_facing", "out_of_range"), ("/reference/bake/views/0", "reference_unknown_view"),
                               ("/reference/bake/palette", "out_of_range")})
        raw["reference"]["bake"] = {"views": ["front"], "palette": 16, "exclude": ["part:fist_*"]}
        _n, errs, _warns = spec.check(raw)
        self.assertEqual([e for e in errs if e.path.startswith("/reference")], [])


class EndToEnd(unittest.TestCase):
    """skin.generate_with_report on a real custom shape with views handed in through n['_views']."""

    @classmethod
    def setUpClass(cls):
        raw = spec.load(_path.EXAMPLE)
        raw["shape"] = {"parts": [{"id": "plate", "bone": "chest", "min": [-0.3, 1.0, -0.3], "size": [0.6, 0.4, 0.05], "color": "accent", "material": "plate_gold"}],
                        "texture": {"density": 16}}
        cls.raw = raw
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ctx = spec.make_context(cls.tmp.name, "roundtable", "com.bossforge.roundtable", Path(cls.tmp.name) / "nocache", offline=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _n(self, bake_block):
        raw = copy.deepcopy(self.raw)
        raw["reference"] = {"views": {"front": {"image": "x.png", "ground_y": 90, "center_x": 32, "px_per_block": 32}}, "bake": bake_block}
        n = spec.normalize(raw)
        w, h = 64, 100
        rgba = bytearray()
        for y in range(h):
            for x in range(w):
                rgba += bytes(pat_a(x, y) + (255,))
        n["_views"] = {"front": reference.View.synthetic("front", rgba, bytes([255]) * (w * h), w, h, 32.0, 90.0, 32.0)}
        return n

    def test_report_files_and_statistics(self):
        n = self._n({"views": ["front"]})
        files, _lang, report = skin.generate_with_report(n, self.ctx)
        b = report["bake"]
        self.assertEqual(b["settings"]["views"], ["front"])
        self.assertGreater(b["total"]["direct"], 300)
        self.assertEqual(set(b["views"]["front"]), {"facing", "direct", "edge", "smeared", "occluded", "mask_miss", "left_procedural"})
        rels = {f.relpath: f for f in files}
        self.assertIn("bossforge/previews/%s_bake_front.png" % n["id"], rels)
        self.assertEqual(b["compare"]["front"]["image"], str(Path(self.ctx.project_root) / ("bossforge/previews/%s_bake_front.png" % n["id"])))
        self.assertGreater(b["compare"]["front"]["compared_px"], 500)
        # the whole report is JSON (the CLI prints it)
        json.dumps(report)
        tex = [f for f in files if f.kind == "texture"][0]
        self.assertGreater(pngio.png_size(tex.data)[0], 0)
        again, _ = skin.generate(n, self.ctx)
        self.assertEqual([f.data for f in again], [f.data for f in files])

    def test_a_spec_without_a_bake_block_behaves_as_before(self):
        n = self._n({"views": ["front"]})
        del n["reference"]["bake"]
        _files, _lang, report = skin.generate_with_report(n, self.ctx)
        self.assertNotIn("bake", report)
        plain = copy.deepcopy(n)
        del plain["reference"]
        _f2, _l2, report2 = skin.generate_with_report(plain, self.ctx)
        self.assertEqual(report["texture"], report2["texture"])

    def test_unknown_bake_view_is_an_error(self):
        n = self._n({"views": ["back"]})
        with self.assertRaises(BossForgeError) as cm:
            skin.generate_with_report(n, self.ctx)
        self.assertEqual(cm.exception.err.code, "reference_unknown_view")


if __name__ == "__main__":
    unittest.main()
