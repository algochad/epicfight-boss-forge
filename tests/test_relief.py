import _path  # noqa
import copy
import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path

from bossforge_core import efmodel, presets, relief, shape, spec
from bossforge_core.pngio import Canvas, write_png
from bossforge_core.reference import View
from bossforge_core.spec import BossForgeError

EXAMPLES = Path(__file__).resolve().parent.parent / "skills" / "epicfight-boss-forge" / "assets" / "examples"

W = H = 200
S = 100.0  # px per block
GY = 190.0  # ground row
CX = 100.0  # centre column
CELL = 0.1  # 10 px: the grid lines of a cell 0.1 fall on pixel boundaries (190 and 100 are multiples of 10)


def make_view(name, fn, color=(200, 100, 50), s=S, gy=GY, cx=CX, width=W, height=H):
    """A synthetic View whose mask is fn(px, py) (pixel centres)."""
    mask = bytearray(width * height)
    rgba = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            i = y * width + x
            rgba[i * 4:i * 4 + 4] = bytes((color[0], color[1], color[2], 255))
            if fn(x + 0.5, y + 0.5):
                mask[i] = 255
    return View.synthetic(name, rgba, mask, width, height, s, gy, cx)


def cells_view(name, cells, cell_px=10, **kw):
    """A view whose mask is the union of whole cells; cells = {(col, row)} on the 10 px pixel grid (row 0 = top)."""
    def fn(px, py):
        return (int(px) // cell_px, int(py) // cell_px) in cells
    return make_view(name, fn, **kw)


def rel(**kw):
    att = {"id": "r", "preset": "relief", "mode": "extrude", "view": "front", "bone": "chest", "cell": CELL, "align": False,
           "thickness": 0.06, "z": 0.2, "max_boxes": 200}
    att.update(kw)
    return att


def build(att, views, notes=None):
    return shape.resolve({"attachments": [att]}, views=views, notes=notes)[0]


def relief_boxes(boxes, prefix="r_"):
    return [b for b in boxes if b["id"].startswith(prefix)]


def front_cells(boxes, cell=CELL):
    """The set of (k, j) grid cells (x index, y index) the front projection of axis-aligned boxes covers."""
    out = set()
    for b in boxes:
        x0, y0 = b["min"][0], b["min"][1]
        x1, y1 = x0 + b["size"][0], y0 + b["size"][1]
        for k in range(int(round(x0 / cell)), int(round(x1 / cell))):
            for j in range(int(round(y0 / cell)), int(round(y1 / cell))):
                out.add((k, j))
    return out


def front_pixel_cells_to_grid(cells):
    """View-pixel cells (col, row of 10 px) -> model grid cells (k, j) of the front view: x = (100 - px)/100."""
    return {(9 - c, 18 - r) for c, r in cells}


def overlap_area(boxes):
    """Total pairwise overlap of the front projections of boxes (must be 0 for a meshing)."""
    total = 0.0
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            w = min(a["min"][0] + a["size"][0], b["min"][0] + b["size"][0]) - max(a["min"][0], b["min"][0])
            h = min(a["min"][1] + a["size"][1], b["min"][1] + b["size"][1]) - max(a["min"][1], b["min"][1])
            if w > 1e-9 and h > 1e-9:
                total += w * h
    return total


def face_sizes(model, box_id, atlas=None):
    """{face: (w, h)} texel rectangles of a box in the generated atlas."""
    box = [b for b in model.boxes if b["id"] == box_id][0]
    faces = [f for f in shape.FACES if f not in box["skip_faces"]]
    tris = [t for t in model.mesh.tris if t.part == box_id]
    side = model.atlas[0]
    out = {}
    for k, face in enumerate(faces):
        t = tris[2 * k]
        u0 = t.t[0]
        uv = model.mesh.uvs
        out[face] = (round(abs(uv[u0 + 1][0] - uv[u0][0]) * side), round(abs(uv[u0][1] - uv[u0 + 3][1]) * side))
    return out


def spec_of(sh, views=None, **extra):
    n = {"id": "boss", "mod_id": "roundtable", "shape": sh}
    if views is not None:
        n["_views"] = views
    n.update(extra)
    return n


class MeshingTest(unittest.TestCase):
    def check_partition(self, rows, rects):
        seen = {}
        for c0, c1, r0, r1 in rects:
            for r in range(r0, r1 + 1):
                for c in range(c0, c1 + 1):
                    self.assertEqual(rows[r][c], 1, "rect covers a background cell")
                    self.assertNotIn((r, c), seen, "cells covered twice")
                    seen[(r, c)] = 1
        want = {(r, c) for r, row in enumerate(rows) for c, v in enumerate(row) if v}
        self.assertEqual(set(seen), want, "foreground cells left uncovered")

    def test_random_masks_are_covered_exactly_once(self):
        rnd = random.Random(7)
        for density in (0.15, 0.5, 0.85, 1.0):
            for _ in range(6):
                R, C = rnd.randint(1, 14), rnd.randint(1, 14)
                rows = [[1 if rnd.random() < density else 0 for _c in range(C)] for _r in range(R)]
                rects = relief.mesh_rects(rows)
                self.check_partition(rows, rects)

    def test_simple_shapes(self):
        self.assertEqual(relief.mesh_rects([[1, 1, 1], [1, 1, 1]]), [(0, 2, 0, 1)])
        self.assertEqual(relief.mesh_rects([[0, 0], [0, 0]]), [])
        self.assertEqual(relief.mesh_rects([]), [])
        l_shape = [[1, 0], [1, 0], [1, 1]]
        self.assertEqual(len(relief.mesh_rects(l_shape)), 2)
        checker = [[(r + c) % 2 for c in range(4)] for r in range(4)]
        self.assertEqual(len(relief.mesh_rects(checker)), 8)

    def test_the_smaller_of_row_first_and_column_first_wins(self):
        # a tall bar and a wide bar: either order needs 2 rects; a 1-wide column of a staircase prefers columns
        rows = [[1, 0, 0, 0], [1, 1, 0, 0], [1, 1, 1, 0], [1, 1, 1, 1]]
        rects = relief.mesh_rects(rows)
        self.check_partition(rows, rects)
        self.assertEqual(len(rects), 4)
        tall = [[1, 0, 1, 0, 1]] * 6  # three columns: column-first = 3 rects, row-first = 3 per row merged = 3
        self.assertEqual(len(relief.mesh_rects(tall)), 3)

    def test_deterministic(self):
        rnd = random.Random(3)
        rows = [[1 if rnd.random() < 0.6 else 0 for _c in range(12)] for _r in range(12)]
        self.assertEqual(relief.mesh_rects(rows), relief.mesh_rects(copy.deepcopy(rows)))


class ExtrudeTest(unittest.TestCase):
    def random_cells(self, seed, side=16):
        """A random blob on the 10 px grid: pixel cells (col, row) on both sides of the centre."""
        rnd = random.Random(seed)
        cells = set()
        for r in range(2, 18):
            for c in range(1, 19):
                if rnd.random() < 0.55:
                    cells.add((c, r))
        return cells

    def test_boxes_cover_exactly_the_foreground_cells(self):
        cells = self.random_cells(11)
        v = cells_view("front", cells)
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200]), {"front": v}))
        self.assertEqual(front_cells(boxes), front_pixel_cells_to_grid(cells))
        self.assertEqual(overlap_area(boxes), 0.0)

    def test_slab_geometry_depth_and_style(self):
        v = cells_view("front", {(c, r) for c in range(3, 8) for r in range(4, 9)})
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], z=0.3, thickness=0.08, material="silk", color="#102030"), {"front": v}))
        self.assertEqual(len(boxes), 1)
        b = boxes[0]
        self.assertEqual(b["id"], "r_s0")
        self.assertEqual(b["bone"], "chest")
        self.assertEqual(b["min"], [0.2, 1.0, 0.26])  # x = (100 - 80)/100 .. , y = (190 - 90)/100, z centre 0.3 - 0.04
        self.assertEqual(b["size"], [0.5, 0.5, 0.08])
        self.assertEqual(b["material"], "silk")
        self.assertEqual(b["slab"], "z")
        self.assertEqual(b["edge_texels"], 1)

    def test_cells_use_half_coverage_and_are_clipped_to_the_rect(self):
        # a bar 14 px wide: the 10 px cell row gets 100 %, the next cell 40 % (dropped)
        v = make_view("front", lambda px, py: 50 <= px < 64 and 100 <= py < 150)
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200]), {"front": v}))
        x0 = min(b["min"][0] for b in boxes)
        x1 = max(b["min"][0] + b["size"][0] for b in boxes)
        self.assertAlmostEqual(x1 - x0, 0.1, places=6)
        # a rect that cuts through a foreground cell: the box stops at the rect, and the cell needs >= half of itself
        v = make_view("front", lambda px, py: 40 <= px < 120 and 100 <= py < 150)
        boxes = relief_boxes(build(rel(rect=[55, 100, 115, 150]), {"front": v}))
        xs = [b["min"][0] for b in boxes] + [b["min"][0] + b["size"][0] for b in boxes]
        self.assertAlmostEqual(max(xs), (100 - 55) / 100.0, places=6)
        self.assertAlmostEqual(min(xs), (100 - 115) / 100.0, places=6)

    def test_mirror_builds_the_right_half_and_mirrors_it(self):
        cells = self.random_cells(5)
        left_only = {(c, r) for c, r in cells if c >= 10}  # image right = character's LEFT (x < 0) in the front view
        v = cells_view("front", cells)
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], mirror=True), {"front": v}))
        got = front_cells(boxes)
        right = {(k, j) for k, j in front_pixel_cells_to_grid(cells) if k >= 0}
        self.assertEqual({(k, j) for k, j in got if k >= 0}, right)
        self.assertEqual({(-k - 1, j) for k, j in got if k >= 0}, {(k, j) for k, j in got if k < 0})
        ids = {b["id"] for b in boxes}
        self.assertTrue(any(i.endswith("_r") for i in ids) and any(i.endswith("_l") for i in ids))
        self.assertEqual(overlap_area(boxes), 0.0)
        self.assertGreater(len(left_only), 0)  # the left half of the art really differs from what was built

    def test_mirror_merges_boxes_that_touch_the_centre_line(self):
        v = cells_view("front", {(c, r) for c in range(2, 18) for r in range(4, 9)})
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], mirror=True), {"front": v}))
        centre = [b for b in boxes if b["id"].endswith("_c")]
        self.assertEqual(len(centre), 1)
        self.assertAlmostEqual(centre[0]["min"][0], -0.8, places=6)
        self.assertAlmostEqual(centre[0]["size"][0], 1.6, places=6)
        # a sided bone cannot be merged across the centre: the pair stays two boxes
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], mirror=True, bone="arm_r"), {"front": v}))
        self.assertEqual(sorted(b["id"] for b in boxes), ["r_s0_l", "r_s0_r"])
        self.assertEqual({b["bone"] for b in boxes}, {"arm_r", "arm_l"})

    def test_mirror_with_a_rect_right_of_the_centre_keeps_the_pair(self):
        v = cells_view("front", {(c, r) for c in range(2, 8) for r in range(4, 9)})
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], mirror=True), {"front": v}))
        self.assertEqual(sorted(b["id"] for b in boxes), ["r_s0_l", "r_s0_r"])

    def test_back_view_x_runs_with_the_image(self):
        v = cells_view("back", {(c, r) for c in range(11, 15) for r in range(4, 9)})  # px 110..150 -> x 0.1..0.5
        boxes = relief_boxes(build(rel(view="back", rect=[0, 0, 200, 200], z=0.5), {"back": v}))
        self.assertEqual(len(boxes), 1)
        self.assertAlmostEqual(boxes[0]["min"][0], 0.1, places=6)
        self.assertAlmostEqual(boxes[0]["size"][0], 0.4, places=6)
        self.assertAlmostEqual(boxes[0]["min"][2] + boxes[0]["size"][2] / 2, 0.5, places=6)

    def test_side_view_extrudes_along_x(self):
        # right view: z = (100 - px)/100; character front (-z) is to the image right
        v = cells_view("right", {(c, r) for c in range(13, 16) for r in range(4, 9)})  # px 130..160 -> z -0.6..-0.3
        boxes = relief_boxes(build(rel(view="right", rect=[0, 0, 200, 200], z=0.4, thickness=0.2), {"right": v}))
        self.assertEqual(len(boxes), 1)
        b = boxes[0]
        self.assertEqual(b["slab"], "x")
        self.assertAlmostEqual(b["min"][2], -0.6, places=6)
        self.assertAlmostEqual(b["size"][2], 0.3, places=6)
        self.assertAlmostEqual(b["min"][0], 0.3, places=6)
        self.assertAlmostEqual(b["size"][0], 0.2, places=6)
        with self.assertRaises(BossForgeError) as cm:
            build(rel(view="right", rect=[0, 0, 200, 200], mirror=True), {"right": v})
        self.assertEqual(cm.exception.err.code, "relief_bad_mirror")

    def test_layers_stack_slabs(self):
        v = cells_view("front", {(c, r) for c in range(3, 8) for r in range(4, 9)})
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], layers=3, layer_step=0.1, thickness=0.05, z=0.2), {"front": v}))
        self.assertEqual(sorted(b["id"] for b in boxes), ["r_l0s0", "r_l1s0", "r_l2s0"])
        zs = sorted(b["min"][2] + b["size"][2] / 2 for b in boxes)
        self.assertEqual([round(z, 6) for z in zs], [0.2, 0.3, 0.4])

    def test_hidden_faces_between_neighbours_are_skipped(self):
        cells = {(c, r) for c in range(3, 8) for r in range(4, 9)} | {(c, r) for c in range(8, 10) for r in range(4, 6)}
        v = cells_view("front", cells)
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200]), {"front": v}))
        self.assertEqual(len(boxes), 2)
        faces = sorted(tuple(b["skip_faces"]) for b in boxes)
        self.assertTrue(any(f for f in faces), "touching rectangles should hide the faces that face each other")
        # a lone box keeps every face
        v = cells_view("front", {(4, 5)})
        self.assertEqual(relief_boxes(build(rel(rect=[0, 0, 200, 200]), {"front": v}))[0]["skip_faces"], [])

    def test_reference_colour_per_box(self):
        def fn(px, py):
            return 20 <= px < 60 and 100 <= py < 140

        v = make_view("front", fn, color=(10, 120, 250))
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200]), {"front": v}))
        self.assertEqual({b["color"] for b in boxes}, {"#0a78fa"})
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], ref_color=False, color="#abcdef"), {"front": v}))
        self.assertEqual({b["color"] for b in boxes}, {"#abcdef"})

    def test_rotate_and_pivot_are_passed_to_every_box_with_one_pivot(self):
        v = cells_view("front", {(c, r) for c in range(3, 8) for r in range(4, 9)} | {(c, r) for c in range(2, 4) for r in range(9, 11)})
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], rotate=[0.0, 0.0, -20.0]), {"front": v}))
        self.assertGreater(len(boxes), 1)
        self.assertEqual({tuple(b["rotate"]) for b in boxes}, {(0.0, 0.0, -20.0)})
        self.assertEqual(len({tuple(b["pivot"]) for b in boxes}), 1)
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], rotate=[0.0, 0.0, -20.0], pivot=[0.0, 1.0, 0.2]), {"front": v}))
        self.assertEqual({tuple(b["pivot"]) for b in boxes}, {(0.0, 1.0, 0.2)})

    def test_align_shifts_the_grid_to_the_art(self):
        # a 40 x 40 px square whose edges sit half a cell (5 px) off the 10 px grid
        v = make_view("front", lambda px, py: 45 <= px < 85 and 105 <= py < 145)
        notes = {}
        build(rel(rect=[0, 0, 200, 200]), {"front": v}, notes)
        self.assertLess(notes["relief"][0]["iou"], 0.9)
        self.assertEqual(notes["relief"][0]["phase"], [0.0, 0.0])
        notes = {}
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], align=True), {"front": v}, notes))
        self.assertEqual(notes["relief"][0]["iou"], 1.0)
        self.assertEqual(notes["relief"][0]["phase"], [0.5, 0.5])
        self.assertEqual(len(boxes), 1)
        self.assertAlmostEqual(boxes[0]["size"][0], 0.4, places=6)

    def test_align_never_breaks_max_boxes_and_is_deterministic(self):
        v = cells_view("front", self.random_cells(13))
        att = rel(rect=[0, 0, 200, 200], align=True, mirror=True, max_boxes=30)
        a = build(att, {"front": v})
        b = build(copy.deepcopy(att), {"front": v})
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertLessEqual(len(relief_boxes(a)), 30)
        # on perfectly aligned art the unshifted grid is already exact and stays the choice
        notes = {}
        build(rel(rect=[0, 0, 200, 200], align=True, max_boxes=386), {"front": v}, notes)
        self.assertEqual(notes["relief"][0]["iou"], 1.0)
        self.assertEqual(notes["relief"][0]["phase"], [0.0, 0.0])

    def test_deterministic(self):
        v = cells_view("front", self.random_cells(2))
        att = rel(rect=[0, 0, 200, 200], mirror=True)
        a = build(att, {"front": v})
        b = build(copy.deepcopy(att), {"front": v})
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_coarsening_to_max_boxes_warns_and_fits(self):
        v = cells_view("front", self.random_cells(9))
        notes = {}
        full = relief_boxes(build(rel(rect=[0, 0, 200, 200], max_boxes=386), {"front": v}))
        self.assertGreater(len(full), 40)
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], max_boxes=40), {"front": v}, notes))
        self.assertLessEqual(len(boxes), 40)
        codes = [w.code for w in notes["warnings"]]
        self.assertEqual(codes, ["relief_coarsened"])
        self.assertEqual(notes["warnings"][0].path, "/attachments/0")
        info = notes["relief"][0]
        self.assertGreater(info["cell"], info["cell_requested"])
        self.assertEqual(info["boxes"], len(boxes))
        # no coarsening when it fits
        notes = {}
        build(rel(rect=[0, 0, 200, 200], max_boxes=386), {"front": v}, notes)
        self.assertNotIn("warnings", notes)

    def test_max_boxes_that_cannot_be_reached_is_an_error(self):
        v = cells_view("front", {(c, r) for c in range(3, 8) for r in range(4, 9)})
        with self.assertRaises(BossForgeError) as cm:
            build(rel(rect=[0, 0, 200, 200], mirror=True, max_boxes=1), {"front": v})
        self.assertEqual(cm.exception.err.code, "relief_max_boxes")

    def test_measured_iou(self):
        cells = self.random_cells(4)
        v = cells_view("front", cells)
        notes = {}
        build(rel(rect=[0, 0, 200, 200]), {"front": v}, notes)
        self.assertEqual(notes["relief"][0]["iou"], 1.0)
        # a disc is only approximated by cells: IoU below 1 but high
        disc = make_view("front", lambda px, py: (px - 100) ** 2 + (py - 100) ** 2 < 70 ** 2)
        notes = {}
        build(rel(rect=[0, 0, 200, 200], cell=0.05), {"front": disc}, notes)
        iou = notes["relief"][0]["iou"]
        self.assertTrue(0.9 < iou < 1.0, iou)
        self.assertEqual(notes["relief"][0]["ref_px"] - notes["relief"][0]["missing_px"] + notes["relief"][0]["extra_px"],
                         notes["relief"][0]["model_px"])

    def test_silhouette_stats_of_shifted_rects(self):
        v = make_view("front", lambda px, py: 50 <= px < 100 and 50 <= py < 100)
        rect = (0.0, 0.0, 200.0, 200.0)
        exact = relief.silhouette_stats(v, rect, [(0.0, 0.5, 0.9, 1.4)])
        self.assertEqual(exact["iou"], 1.0)
        half = relief.silhouette_stats(v, rect, [(0.0, 0.25, 0.9, 1.4)])
        self.assertAlmostEqual(half["iou"], 0.5, places=3)

    def test_errors(self):
        v = cells_view("front", {(4, 5)})
        views = {"front": v}

        def code(att, views=views):
            with self.assertRaises(BossForgeError) as cm:
                build(att, views)
            return cm.exception.err.code

        self.assertEqual(code(rel(rect=[0, 0, 200, 200]), views={}), "relief_no_reference")
        self.assertEqual(code(rel(view="back", rect=[0, 0, 200, 200])), "relief_unknown_view")
        self.assertEqual(code(rel()), "relief_bad_rect")  # extrude needs a rect
        self.assertEqual(code(rel(rect=[0, 0, 300, 200])), "relief_bad_rect")  # outside the image
        coarse = make_view("front", lambda px, py: 40 <= px < 60 and 100 <= py < 150, s=50.0)  # 0.02 blocks = 1 px
        self.assertEqual(code(rel(rect=[0, 0, 200, 200], cell=0.02), views={"front": coarse}), "relief_cell_too_small")
        self.assertEqual(code(rel(rect=[150, 0, 200, 50])), "relief_empty")
        with self.assertRaises(BossForgeError) as cm:  # the path points into the attachment
            shape.resolve({"attachments": [rel(rect=[0, 0, 300, 200])]}, views=views)
        self.assertEqual(cm.exception.err.path, "/attachments/0/rect")

    def test_model_limits_are_reported_not_silently_clipped(self):
        # 5 px per block: the 200 px wide image spans 40 blocks -> far outside |x| <= 4
        v = make_view("front", lambda px, py: 0 <= px < 200 and 150 <= py < 190, s=5.0, gy=190.0, cx=100.0)
        with self.assertRaises(BossForgeError) as cm:
            build(rel(rect=[0, 0, 200, 200], cell=0.5), {"front": v})
        self.assertEqual(cm.exception.err.code, "relief_out_of_bounds")
        self.assertIn("|x| <= 4", cm.exception.err.message)
        ok = make_view("front", lambda px, py: 0 <= px < 200 and 150 <= py < 190, s=60.0, gy=190.0, cx=100.0)
        boxes = relief_boxes(build(rel(rect=[0, 0, 200, 200], cell=0.2), {"front": ok}))
        self.assertTrue(boxes)

    def test_preset_registration_and_parameter_validation(self):
        self.assertIn("relief", presets.list_presets())
        params = presets.PRESETS["relief"]["params"]
        self.assertNotIn("scale", params)
        self.assertEqual(params["max_boxes"]["range"], [1, 386])
        errs = {(e.path, e.code) for e in presets.validate_attachment(rel(rect=[10, 10, 5, 50], cell=0.001, layers=9, mode="blob",
                                                                         view="up", bone="tail", z=9.0, thickness=0.0))}
        for want in (("/rect", "out_of_range"), ("/cell", "out_of_range"), ("/layers", "out_of_range"), ("/mode", "unknown_value"),
                     ("/view", "unknown_value"), ("/bone", "unknown_value"), ("/z", "out_of_range"), ("/thickness", "out_of_range")):
            self.assertIn(want, errs)
        self.assertEqual({(e.path, e.code) for e in presets.validate_attachment(rel(rect="x"))}, {("/rect", "bad_type")})
        self.assertEqual({(e.path, e.code) for e in presets.validate_attachment(rel(views={"up": [0, 0, 5, 5]}))}, {("/views/up", "unknown_value")})
        self.assertEqual({(e.path, e.code) for e in presets.validate_attachment(rel(rotate=[1, 2]))}, {("/rotate", "bad_type")})
        self.assertEqual({(e.path, e.code) for e in presets.validate_attachment(rel(scale=1.0))}, {("/scale", "unknown_value")})
        self.assertEqual(presets.validate_attachment(rel(rect=[0, 0, 5, 5], pivot=[0, 1, 0])), [])


class HullTest(unittest.TestCase):
    def front_two_bars(self):
        # x bars at px 40..60 and 140..160 (x = 0.6..0.4 and -0.4..-0.6), y 0.2..1.2
        return make_view("front", lambda px, py: (40 <= px < 60 or 140 <= px < 160) and 70 <= py < 170)

    def side_bar(self):
        # z = (100 - px)/100: px 90..130 -> z 0.1..-0.3, y 0.2..1.0 (shorter than the front bars)
        return make_view("right", lambda px, py: 90 <= px < 130 and 90 <= py < 170)

    def hull(self, **kw):
        att = {"id": "h", "preset": "relief", "mode": "hull", "views": {"front": [0, 0, 200, 200], "right": [0, 0, 200, 200]},
               "bone": "head", "cell": CELL, "max_boxes": 100}
        att.update(kw)
        views = {"front": self.front_two_bars(), "right": self.side_bar()}
        notes = {}
        boxes = relief_boxes(shape.resolve({"attachments": [att]}, views=views, notes=notes)[0], "h_")
        return boxes, notes

    def test_product_of_front_x_intervals_and_side_z_intervals(self):
        boxes, notes = self.hull()
        self.assertEqual(len(boxes), 2)  # two x intervals x one z interval, merged over all bands
        spans = sorted((round(b["min"][0], 6), round(b["min"][0] + b["size"][0], 6)) for b in boxes)
        self.assertEqual(spans, [(-0.6, -0.4), (0.4, 0.6)])
        for b in boxes:
            self.assertAlmostEqual(b["min"][2], -0.3, places=6)
            self.assertAlmostEqual(b["size"][2], 0.4, places=6)
            self.assertAlmostEqual(b["min"][1], 0.2, places=6)  # the common height of both silhouettes: 0.2..1.0
            self.assertAlmostEqual(b["size"][1], 0.8, places=6)
            self.assertEqual(b["bone"], "head")
        info = notes["relief"][0]
        self.assertEqual(info["mode"], "hull")
        self.assertEqual(info["iou_right"], 1.0)
        self.assertTrue(0.7 < info["iou_front"] < 1.0)  # the front bars are taller than the side silhouette
        self.assertEqual(info["iou"], min(info["iou_front"], info["iou_right"]))

    def test_hull_mirror_uses_the_right_half(self):
        boxes, _ = self.hull(mirror=True)
        self.assertEqual(sorted(b["id"] for b in boxes), ["h_h0_l", "h_h0_r"])
        spans = sorted((round(b["min"][0], 6), round(b["min"][0] + b["size"][0], 6)) for b in boxes)
        self.assertEqual(spans, [(-0.6, -0.4), (0.4, 0.6)])

    def test_bands_split_when_the_intervals_change(self):
        def fn(px, py):  # a step: wide at the bottom (py >= 120), narrow on top
            return (80 <= px < 120) if py >= 120 else (90 <= px < 110)
        front = make_view("front", lambda px, py: fn(px, py) and 50 <= py < 170)
        side = make_view("right", lambda px, py: 90 <= px < 110 and 50 <= py < 170)
        att = {"id": "h", "preset": "relief", "mode": "hull", "views": {"front": [0, 0, 200, 200], "right": [0, 0, 200, 200]},
               "bone": "head", "cell": CELL, "max_boxes": 100}
        boxes = relief_boxes(shape.resolve({"attachments": [att]}, views={"front": front, "right": side})[0], "h_")
        self.assertEqual(len(boxes), 2)
        widths = sorted(round(b["size"][0], 6) for b in boxes)
        self.assertEqual(widths, [0.2, 0.4])

    def test_bad_views(self):
        views = {"front": self.front_two_bars(), "right": self.side_bar()}
        for vw in ({"front": [0, 0, 200, 200]}, {"front": [0, 0, 200, 200], "back": [0, 0, 200, 200]},
                   {"right": [0, 0, 200, 200], "left": [0, 0, 200, 200]}):
            with self.assertRaises(BossForgeError) as cm:
                shape.resolve({"attachments": [{"id": "h", "preset": "relief", "mode": "hull", "views": vw}]}, views=views)
            self.assertEqual(cm.exception.err.code, "relief_bad_views")
        with self.assertRaises(BossForgeError) as cm:  # no common height
            shape.resolve({"attachments": [{"id": "h", "preset": "relief", "mode": "hull",
                                            "views": {"front": [0, 0, 200, 30], "right": [0, 120, 200, 200]}}]}, views=views)
        self.assertEqual(cm.exception.err.code, "relief_bad_rect")

    def test_hull_without_shared_foreground_and_out_of_range_rects(self):
        views = {"front": self.front_two_bars(), "right": self.side_bar()}
        att = {"id": "h", "preset": "relief", "mode": "hull", "views": {"front": [0, 0, 200, 60], "right": [0, 0, 200, 60]}}
        with self.assertRaises(BossForgeError) as cm:
            shape.resolve({"attachments": [att]}, views=views)
        self.assertEqual(cm.exception.err.code, "relief_empty")
        att = {"id": "h", "preset": "relief", "mode": "hull", "views": {"front": [0, 0, 300, 200], "right": [0, 0, 200, 200]}}
        with self.assertRaises(BossForgeError) as cm:
            shape.resolve({"attachments": [att]}, views=views)
        self.assertEqual((cm.exception.err.path, cm.exception.err.code), ("/attachments/0/views/front", "relief_bad_rect"))

    def test_slivers_clipped_by_the_rect_keep_the_minimum_box_size(self):
        # a rect edge that leaves just over half of a 0.02 cell: the box would be 0.01 or a hair less
        v = make_view("front", lambda px, py: 40 <= px < 120 and 100 <= py < 150)
        boxes = relief_boxes(build(rel(rect=[0, 100, 101.01, 150], cell=0.02), {"front": v}))
        self.assertTrue(boxes)
        for b in boxes:
            self.assertTrue(all(c >= 0.01 for c in b["size"]), b)
        shape.validate_shape({"attachments": [rel(rect=[0, 100, 101.01, 150], cell=0.02)]}, spec_of({}, {"front": v}))

    def test_hull_is_deterministic(self):
        a, _ = self.hull()
        b, _ = self.hull()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))


class ModelTest(unittest.TestCase):
    def wing_views(self):
        cells = {(c, r) for c in range(2, 9) for r in range(3, 17) if c + (r - 3) // 3 < 12}
        cells |= {(19 - c, r) for c, r in cells}  # symmetric art: the mirrored half matches the left half
        return {"front": cells_view("front", cells)}

    def blob_views(self):
        rnd = random.Random(21)
        cells = {(c, r) for c in range(1, 19) for r in range(2, 18) if rnd.random() < 0.55}
        return {"front": cells_view("front", cells)}

    def model(self, **kw):
        sh = {"attachments": [rel(rect=[0, 0, 200, 200], mirror=True, id="wing", **kw)], "texture": {"density": 48}}
        return shape.build_model(spec_of(sh, self.wing_views()))

    def test_build_model_with_relief_reports_counts_and_measurements(self):
        m = self.model()
        self.assertEqual(efmodel.check_mesh_json(m.mesh_json), [])
        self.assertEqual(m.box_counts["base"], 14)
        self.assertGreaterEqual(m.box_counts["wing"], 2)
        self.assertEqual(sum(m.box_counts.values()), len(m.boxes))
        self.assertEqual(m.relief[0]["id"], "wing")
        self.assertGreater(m.relief[0]["iou"], 0.9)
        self.assertTrue(0.0 < m.atlas_fill <= 1.0)

    def test_per_preset_box_counts_for_stock_shapes(self):
        m = shape.build_model(spec_of({"silhouette": "knight", "parts": [{"id": "x", "bone": "head", "min": [0, 2, 0], "size": [0.1, 0.1, 0.1]}]}))
        self.assertEqual(m.box_counts["base"], 14)
        self.assertEqual(m.box_counts["parts"], 1)
        self.assertGreater(m.box_counts["helm"], 0)
        self.assertEqual(sum(m.box_counts.values()), len(m.boxes))

    def test_slabs_only_allocate_edge_strips_on_their_thin_faces(self):
        thin = self.model()
        fat = self.model(edge_texels=0)
        box = [b for b in thin.boxes if b["id"].startswith("wing_") and not b["skip_faces"]][0]
        s_thin = face_sizes(thin, box["id"])
        self.assertEqual(s_thin["left"][0], 1)  # z extent of the slab: one texel
        self.assertEqual(s_thin["top"][1], 1)
        self.assertGreater(s_thin["front"][0], 2)
        self.assertEqual(s_thin["front"], face_sizes(fat, box["id"])["front"])  # the big faces keep their resolution
        self.assertGreater(face_sizes(fat, box["id"])["left"][0], 1)
        self.assertLess(thin.atlas_fill, fat.atlas_fill)

    def test_edge_texels_keys_on_free_boxes(self):
        part = {"id": "slab", "bone": "chest", "min": [-0.3, 1.0, 0.2], "size": [0.6, 0.6, 0.02], "slab": "z", "edge_texels": 2}
        m = shape.build_model(spec_of({"parts": [part], "texture": {"density": 32}}))
        s = face_sizes(m, "slab")
        self.assertEqual((s["left"][0], s["right"][0], s["top"][1], s["bottom"][1]), (2, 2, 2, 2))
        self.assertEqual(s["front"], (19, 19))
        plain = {"id": "slab", "bone": "chest", "min": [-0.3, 1.0, 0.2], "size": [0.6, 0.6, 0.02]}
        for extra, code in (({"slab": "w"}, "bad_slab"), ({"edge_texels": 1}, "bad_slab"),
                            ({"slab": "z", "edge_texels": 0}, "out_of_range"), ({"slab": "z", "edge_texels": 9}, "out_of_range"),
                            ({"slab": "z", "edge_texels": 1.5}, "out_of_range")):
            p = dict(plain, **extra)
            self.assertIn(code, {e.code for e in shape.validate_shape({"parts": [p]})}, extra)
        self.assertEqual(shape.validate_shape({"parts": [dict(plain, slab="x")]}), [])

    def test_hidden_faces_shrink_the_mesh(self):
        v = {"front": cells_view("front", {(c, r) for c in range(3, 8) for r in range(4, 9)} | {(c, r) for c in range(8, 10) for r in range(4, 6)})}
        m = shape.build_model(spec_of({"attachments": [rel(rect=[0, 0, 200, 200])]}, v))
        faces = sum(1 for b in m.boxes if b["id"].startswith("r_") for _ in range(6 - len(b["skip_faces"])))
        self.assertLess(faces, 12)

    def test_relief_needs_the_reference_in_validation(self):
        errs = shape.validate_shape({"attachments": [rel(rect=[0, 0, 200, 200])]})
        self.assertEqual({(e.path, e.code) for e in errs}, {("/attachments/0/view", "relief_no_reference")})
        errs = shape.validate_shape({"attachments": [rel(rect=[0, 0, 200, 200])]}, spec_of({}, self.wing_views()))
        self.assertEqual(errs, [])

    def test_coarsening_warning_reaches_the_model_warnings(self):
        sh = {"attachments": [rel(rect=[0, 0, 200, 200], mirror=True, id="wing", max_boxes=12)]}
        m = shape.build_model(spec_of(sh, self.blob_views()))
        self.assertEqual([(w.path, w.code) for w in m.warnings], [("/attachments/0", "relief_coarsened")])
        self.assertLessEqual(m.box_counts["wing"], 12)

    def test_real_files_end_to_end(self):
        base = json.loads((EXAMPLES / "king-arthur" / "boss.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            c = Canvas(60, 60)
            c.fill((90, 90, 90, 255))
            mask = Canvas(60, 60)
            mask.fill((0, 0, 0, 255))
            for y in range(10, 40):
                for x in range(12, 36):
                    c.set(x, y, (250, 240, 200, 255))
                    mask.set(x, y, (255, 255, 255, 255))
            write_png(d / "art.png", c)
            write_png(d / "mask.png", mask)
            base["reference"] = {"views": {"front": {"image": "art.png", "mask": {"mode": "image", "image": "mask.png", "erode": 0},
                                                     "ground_y": 50, "center_x": 30, "px_per_block": 25}}}
            base["shape"]["attachments"].append({"id": "wing", "preset": "relief", "view": "front", "rect": [0, 0, 60, 60],
                                                 "cell": 0.2, "z": 0.2, "bone": "chest"})
            p = d / "boss.json"
            p.write_text(json.dumps(base), encoding="utf-8")
            n, errs, warns = spec.check(spec.load(p))
            self.assertEqual([e.to_dict() for e in errs], [])
            m = shape.build_model(n)
            self.assertGreater(m.box_counts["wing"], 0)
            self.assertGreater(m.relief[0]["iou"], 0.85)
            self.assertEqual({b["color"] for b in m.boxes if b["id"].startswith("wing_")}, {"#faf0c8"})
            # the spec is not valid without the images: the error is a relief error pointing at the attachment
            broken = spec.check(json.loads(p.read_text(encoding="utf-8")))[1]
            self.assertTrue(any(e.code.startswith("reference_") or e.code.startswith("relief_") for e in broken))


class AtlasTest(unittest.TestCase):
    CUBES = [dict(id="cube_%d" % i, bone="chest", min=[-1.0, 0.0, -1.0], size=[2.0, 2.0, 2.0]) for i in range(20)]

    def test_default_atlas_is_512_and_density_is_reduced_beyond_it(self):
        m = shape.build_model(spec_of({"parts": self.CUBES}))
        self.assertLessEqual(m.atlas[0], 512)
        self.assertEqual([w.code for w in m.warnings], ["density_reduced"])

    def test_atlas_1024_holds_what_512_cannot_at_full_density(self):
        m = shape.build_model(spec_of({"parts": self.CUBES, "texture": {"atlas": 1024}}))
        self.assertEqual(m.atlas, (1024, 1024))
        self.assertEqual(m.warnings, [])
        self.assertEqual(m.tpl["authored_uv"], [1024, 1024])
        self.assertEqual(efmodel.check_mesh_json(m.mesh_json), [])

    def test_the_smallest_square_that_fits_is_still_chosen(self):
        a = shape.build_model(spec_of({"silhouette": "plain"}))
        b = shape.build_model(spec_of({"silhouette": "plain", "texture": {"atlas": 1024}}))
        c = shape.build_model(spec_of({"silhouette": "plain", "texture": {"atlas": 512}}))
        self.assertLess(a.atlas[0], 512)
        self.assertEqual(efmodel.dumps(a.mesh_json), efmodel.dumps(b.mesh_json))
        self.assertEqual(efmodel.dumps(a.mesh_json), efmodel.dumps(c.mesh_json))

    def test_too_large_even_at_1024(self):
        parts = [dict(id="slab_%d" % i, bone="chest", min=[-1.5, 0.0, -1.5], size=[3.0, 3.0, 3.0]) for i in range(400 - 14)]
        with self.assertRaises(BossForgeError) as cm:
            shape.build_model(spec_of({"parts": parts, "texture": {"atlas": 1024}}))
        self.assertEqual(cm.exception.err.code, "texture_too_large")

    def test_atlas_validation(self):
        for value, code in ((256, "out_of_range"), (2048, "out_of_range"), ("1024", "bad_type"), (1024.5, "out_of_range")):
            errs = shape.validate_shape({"texture": {"atlas": value}})
            self.assertIn(("/texture/atlas", code), {(e.path, e.code) for e in errs}, value)
        self.assertEqual(shape.validate_shape({"texture": {"atlas": 512, "density": 48}}), [])
        self.assertEqual(shape.validate_shape({"texture": {"atlas": 1024.0}}), [])
        self.assertEqual(shape.MAX_BOXES, 400)

    def test_pack_rects_honours_the_limit(self):
        self.assertIsNone(shape.pack_rects([(300, 300)] * 4))
        side, _pos = shape.pack_rects([(300, 300)] * 4, 1024)
        self.assertEqual(side, 1024)
        self.assertEqual(shape.pack_rects([(10, 10)] * 4, 1024)[0], 32)


class BackwardCompatibilityTest(unittest.TestCase):
    """Hashes of the models of the shipped examples as built by the code before relief/atlas existed."""
    HEAD = {
        "nightingale": ("4284ddf5934f434c52a8c73c3f5979c91d9e10da65411f3fc9c038c519e36353",
                        "bef01940bbc7b377adfca9720cd065ecacad3359a49c02133db7b7a705a0003c", 235, [512, 512], 2.4699),
        "king-arthur": ("27693ec8ba1514ca5b7909d374b5deb0030aa9c94184ea21d8a50d0f03ac24fb",
                        "169cf4a6258f740a7d8b2dd79100f2055f3b7acb9bc688c4777b0420ff4453b0", 69, [512, 512], 2.522),
    }

    def test_examples_build_byte_identically(self):
        for name, (mesh_hash, box_hash, count, atlas, height) in self.HEAD.items():
            n, errs, _warns = spec.check(spec.load(EXAMPLES / name / "boss.json"))
            self.assertEqual(errs, [], name)
            m = shape.build_model(n)
            self.assertEqual(hashlib.sha256(efmodel.dumps(m.mesh_json).encode()).hexdigest(), mesh_hash, name)
            self.assertEqual(hashlib.sha256(json.dumps(m.boxes, sort_keys=True).encode()).hexdigest(), box_hash, name)
            self.assertEqual((len(m.boxes), list(m.atlas), m.height, m.warnings), (count, atlas, height, []), name)
            self.assertEqual(m.relief, [])
            self.assertEqual(sum(m.box_counts.values()), count)

    def test_boxes_without_slab_keep_their_exact_keys(self):
        for b in shape.resolve({"silhouette": "knight"})[0]:
            self.assertNotIn("slab", b)
            self.assertNotIn("edge_texels", b)


if __name__ == "__main__":
    unittest.main()
