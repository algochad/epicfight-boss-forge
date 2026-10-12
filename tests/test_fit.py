import _path  # noqa
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from bossforge_core import cli, fit, refmask, reference, shape, spec
from bossforge_core.pngio import Canvas, read_png, write_png
from bossforge_core.reference import View


def load_raw():
    return json.loads(Path(_path.SHIPPED_EXAMPLE).read_text(encoding="utf-8"))


def build(raw):
    n, errs, _w = spec.check(raw)
    assert not errs, errs
    return n, shape.build_model(n)


def silhouette_view(model, name, s=60.0, margin=12):
    """A View whose mask is exactly the model's own silhouette in this view (calibration s, centred, feet on the ground)."""
    probe = View.synthetic(name, bytes(4), bytes(1), 1, 1, s, 0.0, 0.0)
    polys = fit.model_polys(model, probe)
    us = [u for _i, p in polys for u, _v in p]
    half = max(abs(min(us)), abs(max(us)))
    w = int(2 * half * s + 2 * margin) + 1
    h = int(model.height * s + 2 * margin) + 1
    cx, gy = w / 2.0, h - margin
    mask01 = fit.rasterize(polys, probe._sign, s, cx, gy, w, h)
    rgba = bytearray(b"\x6e\x6e\x6e\xff" * (w * h))
    for i, v in enumerate(mask01):
        if v:
            rgba[i * 4:i * 4 + 4] = b"\xc8\x3c\x3c\xff"
    return View.synthetic(name, rgba, mask01, w, h, s, gy, cx), polys


class FitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = load_raw()
        cls.n, cls.model = build(cls.raw)

    def test_own_silhouette_has_iou_one_in_all_four_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in reference.VIEW_NAMES:
                view, _polys = silhouette_view(self.model, name)
                res = fit.fit_views(self.model, {name: view}, None, tmp)
                r = res["views"][name]
                self.assertEqual(r["iou"], 1.0, name)
                self.assertEqual((r["missing_px"], r["extra_px"]), (0, 0))
                self.assertEqual(r["hints"], [])
                self.assertTrue((Path(tmp) / ("fit_%s.png" % name)).is_file())

    def test_a_missing_part_is_reported_with_numbers_hints_and_colours(self):
        raw2 = copy.deepcopy(self.raw)
        raw2["shape"]["attachments"] = [a for a in raw2["shape"]["attachments"] if a["id"] != "shield"]
        _n2, model2 = build(raw2)
        view, _ = silhouette_view(self.model, "front")
        with tempfile.TemporaryDirectory() as tmp:
            res = fit.fit_views(model2, {"front": view}, ["front"], tmp)
            r = res["views"]["front"]
            self.assertLess(r["iou"], 0.97)
            self.assertGreater(r["missing_px"], 150)
            self.assertEqual(r["extra_px"], 0)
            self.assertEqual(r["ref_px"], r["model_px"] + r["missing_px"])
            joined = "\n".join(r["hints"])
            self.assertIn("wider", joined)
            self.assertIn("(x-)", joined)  # the king's shield is on the character's left
            self.assertTrue(any(h.startswith("y 1.0-1.2") and "gauntlets" in h for h in r["hints"]), r["hints"])
            self.assertEqual(r["bbox_ref"], r["bbox_model"])  # the shield stays inside the overall extents
            png = read_png(Path(tmp) / "fit_front.png")
            colours = {tuple(png.data[i:i + 3]) for i in range(0, len(png.data), 4)}
            self.assertTrue(any(c[1] > 120 and c[0] < 100 for c in colours))  # both = green
            self.assertTrue(any(c[2] > 150 and c[0] < 100 for c in colours))  # reference only = blue

    def test_hints_name_extra_geometry_too(self):
        # model has the shield, the reference silhouette does not
        raw2 = copy.deepcopy(self.raw)
        raw2["shape"]["attachments"] = [a for a in raw2["shape"]["attachments"] if a["id"] != "shield"]
        _n2, model2 = build(raw2)
        view, _ = silhouette_view(model2, "front")
        with tempfile.TemporaryDirectory() as tmp:
            r = fit.fit_views(self.model, {"front": view}, None, tmp)["views"]["front"]
        self.assertEqual(r["missing_px"], 0)
        self.assertGreater(r["extra_px"], 150)
        self.assertIn("sticks out", "\n".join(r["hints"]))

    def test_unknown_view_name_is_an_error(self):
        view, _ = silhouette_view(self.model, "front")
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(spec.BossForgeError) as cm:
                fit.fit_views(self.model, {"front": view}, ["back"], tmp)
        self.assertEqual(cm.exception.err.code, "reference_unknown_view")

    def test_auto_calibrate_recovers_a_known_offset(self):
        for name in ("front", "right"):
            view, polys = silhouette_view(self.model, name)
            off = View.synthetic(name, view.rgba, view.mask, view.width, view.height, view.px_per_block * 1.06,
                                 view.ground_y - 4.0, view.center_x + 7.0)
            res = fit.auto_calibrate(off, polys)
            self.assertAlmostEqual(res["px_per_block"], view.px_per_block, delta=view.px_per_block * 0.01, msg=name)
            self.assertAlmostEqual(res["center_x"], view.center_x, delta=1.5, msg=name)
            self.assertAlmostEqual(res["ground_y"], view.ground_y, delta=2.0, msg=name)
            self.assertGreater(res["iou_after"], 0.97)
            self.assertLess(res["iou_before"], res["iou_after"])
            self.assertLessEqual(res["evaluations"], fit.AUTO_MAX_EVALS + 8)

    def test_iou_counts_and_reduce_mask(self):
        a = bytearray([1, 1, 0, 0])
        b = bytearray([0, 1, 1, 0])
        self.assertEqual(fit.iou_counts(a, b), (1, 3, 1, 1))
        big = bytearray(8 * 8)
        for y in range(8):
            big[y * 8:y * 8 + 4] = b"\xff" * 4
        small, w, h = fit.reduce_mask(big, 8, 8, 4)
        self.assertEqual((w, h, list(small)), (2, 2, [1, 0, 1, 0]))


class CommandTests(unittest.TestCase):
    """fit / reference-info through the command line on a spec whose reference images are the model's own silhouettes."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        raw = load_raw()
        n, model = build(raw)
        views = {}
        for name in ("front", "right"):
            v, _ = silhouette_view(model, name, s=48.0)
            c = Canvas(v.width, v.height, bytearray(v.rgba))
            (d / "refs").mkdir(exist_ok=True)
            write_png(d / "refs" / (name + ".png"), c)
            views[name] = {"image": "refs/%s.png" % name, "ground_y": v.ground_y, "center_x": v.center_x, "px_per_block": 48.0,
                           "mask": {"mode": "auto", "erode": 0, "seal": 0}}
        raw["reference"] = {"views": views}
        cls.spec = d / "boss.json"
        cls.spec.write_text(json.dumps(raw), encoding="utf-8")
        cls.dir = d

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, json.loads(buf.getvalue())

    def test_fit_prints_iou_and_writes_overlays(self):
        out = self.dir / "fit"
        code, res = self.run_cli("fit", str(self.spec), "--out", str(out))
        self.assertEqual(code, 0, res)
        self.assertEqual(sorted(res["views"]), ["front", "right"])
        for name, r in res["views"].items():
            self.assertGreaterEqual(r["iou"], 0.95, name)
            self.assertTrue(Path(r["overlay"]).is_file())
        self.assertGreaterEqual(res["mean_iou"], 0.95)
        code, res = self.run_cli("fit", str(self.spec), "--out", str(out), "--views", "right")
        self.assertEqual(sorted(res["views"]), ["right"])

    def test_fit_auto_calibrate_reports_values_for_the_spec(self):
        code, res = self.run_cli("fit", str(self.spec), "--out", str(self.dir / "fit2"), "--views", "front", "--auto-calibrate")
        self.assertEqual(code, 0, res)
        cal = res["views_calibration"]["front"]
        self.assertAlmostEqual(cal["px_per_block"], 48.0, delta=0.6)
        self.assertGreaterEqual(res["views"]["front"]["auto_calibrate"]["iou_after"], res["views"]["front"]["auto_calibrate"]["iou_before"])

    def test_fit_without_reference_or_with_bad_view_fails_cleanly(self):
        raw = load_raw()
        p = self.dir / "plain.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        code, res = self.run_cli("fit", str(p), "--out", str(self.dir / "x"))
        self.assertEqual(code, 1)
        self.assertEqual(res["errors"][0]["code"], "no_reference")
        code, res = self.run_cli("fit", str(self.spec), "--out", str(self.dir / "x"), "--views", "back")
        self.assertEqual(code, 1)
        self.assertEqual(res["errors"][0]["code"], "reference_unknown_view")

    def test_reference_info_writes_a_grid_and_lists_figures(self):
        img = self.dir / "refs" / "front.png"
        out = self.dir / "grid.png"
        code, res = self.run_cli("reference-info", str(img), "--grid", "25", "--out", str(out))
        self.assertEqual(code, 0, res)
        src = read_png(img)
        self.assertEqual((res["width"], res["height"]), (src.w, src.h))
        grid = read_png(out)
        self.assertEqual((grid.w, grid.h), (src.w, src.h))
        self.assertNotEqual(grid.data, src.data)
        self.assertEqual(len(res["figures"]), 1)
        x0, y0, x1, y1 = res["figures"][0]["bbox"]
        self.assertGreater(x1 - x0, 20)
        self.assertGreater(y1 - y0, 20)
        code, res = self.run_cli("reference-info", str(img), "--out", str(self.dir / "zoom.png"), "--crop", "0,0,40,40", "--zoom", "3")
        self.assertEqual(code, 0)
        self.assertEqual((read_png(self.dir / "zoom.png").w, read_png(self.dir / "zoom.png").h), (120, 120))
        (self.dir / "x.png").write_bytes(b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        code, res = self.run_cli("reference-info", str(self.dir / "x.png"))
        self.assertEqual(code, 1)
        self.assertEqual(res["errors"][0]["code"], "reference_bad_image")


if __name__ == "__main__":
    unittest.main()
