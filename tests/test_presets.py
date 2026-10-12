import _path  # noqa
import copy
import math
import re
import unittest

from bossforge_core import presets, shape, spec
from bossforge_core.spec import BossForgeError

REQUIRED = ("crown horns helm hood mane pauldrons cape skirt tail wings halo orbs extra_arms claws tusks fangs "
            "ribcage core spine_spikes shield gauntlets greaves belt banner jaw hairdo ribbons pouch").split()
STYLES = {
    "crown": ("band", "spiked", "tall", "broken"),
    "horns": ("curved", "straight", "ram", "antlers", "demon", "bull"),
    "helm": ("bucket", "visor", "horned", "plumed", "great"),
    "pauldrons": ("round", "spiked", "layered", "skull", "asymmetric"),
    "cape": ("short", "long", "tattered"),
    "tail": ("lizard", "spiked", "whip", "club"),
    "wings": ("bat", "feathered", "angel", "seraph", "skeletal", "flame"),
    "shield": ("round", "kite", "tower"),
    "hairdo": ("bun", "twin_buns", "ponytail", "braid"),
    "ribbons": ("wrist", "hem", "back", "all"),
}
SILHOUETTES = ("plain knight warlord brute colossus caster beast fiend skeletal_king wraith insectoid").split()
# variants that are asymmetric about x = 0 on purpose
ASYMMETRIC = {("pauldrons", "style", "asymmetric"), ("shield", None, None), ("crown", "style", "broken"),
              ("belt", "style", "sash"), ("banner", "style", "pennant"), ("cape", "style", "tattered"), ("pouch", None, None)}
PALETTE = set(spec.PALETTE_KEYS)
BONES = set(shape.BONES)


def explicit(boxes):
    """Boxes with every default filled in and mirror copies expanded (what shape.resolve produces)."""
    out = []
    for b in boxes:
        nb = {"id": b["id"], "bone": b["bone"], "min": list(b["min"]), "size": list(b["size"]),
              "taper": list(b.get("taper", [1.0, 1.0])), "rotate": list(b.get("rotate", [0.0, 0.0, 0.0])),
              "skip_faces": [], "region": None}
        nb["pivot"] = list(b.get("pivot") or [b["min"][i] + b["size"][i] / 2.0 for i in range(3)])
        out.append(nb)
        if b.get("mirror"):
            out.append(shape.mirror_box(nb))
    return out


def corners(boxes):
    return [c for b in explicit(boxes) for c in shape.box_corners(b)]


def extent(boxes):
    pts = corners(boxes)
    return [(min(p[i] for p in pts), max(p[i] for p in pts)) for i in range(3)]


def props(**kw):
    p = shape.default_proportions()
    p.update(kw)
    return p


def expand(att, proportions=None):
    return presets.expand(att, proportions or props(), shape.ANATOMY)


def variants(name):
    """Every enum value / bool value / int bound of a preset as a ready-to-expand attachment (others default)."""
    params = presets.PRESETS[name]["params"]
    base = {"id": name, "preset": name}
    out = [(dict(base), None, None)]
    for key, p in params.items():
        if key in presets.COMMON_PARAMS:
            continue
        if p["type"] == "enum":
            vals = p["allowed"]
        elif p["type"] == "bool":
            vals = [True, False]
        elif p["type"] == "color":
            vals = ["#123456"]
        elif p["type"] == "colors":
            vals = [["#123456"]]
        else:
            vals = list(p["range"])
        for v in vals:
            if v != p["default"]:
                a = dict(base)
                a[key] = v
                out.append((a, key, v))
    return out


def all_variants():
    for name in presets.PRESETS:
        if name in shape.REFERENCE_PRESETS:  # tests/test_relief.py
            continue
        for att, key, val in variants(name):
            yield name, att, key, val


def mirror_gap(boxes):
    """Largest distance from a corner to the closest corner of the x-mirrored point cloud."""
    pts = corners(boxes)
    cell = 0.01
    grid = {}
    for p in pts:
        grid.setdefault((round(p[0] / cell), round(p[1] / cell), round(p[2] / cell)), []).append(p)
    worst = 0.0
    for p in pts:
        k = (round(-p[0] / cell), round(p[1] / cell), round(p[2] / cell))
        best = 1e9
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for q in grid.get((k[0] + dx, k[1] + dy, k[2] + dz), ()):
                        best = min(best, math.sqrt((q[0] + p[0]) ** 2 + (q[1] - p[1]) ** 2 + (q[2] - p[2]) ** 2))
        worst = max(worst, best)
    return worst


class PresetCatalogTest(unittest.TestCase):
    def test_all_requested_presets_exist_with_documented_styles(self):
        self.assertEqual(set(REQUIRED) | set(shape.REFERENCE_PRESETS), set(presets.list_presets()))
        for name, styles in STYLES.items():
            self.assertEqual(list(styles), presets.PRESETS[name]["params"][
                "pose" if name == "extra_arms" else "style"]["allowed"], name)
        wings = presets.PRESETS["wings"]["params"]["spread"]
        self.assertEqual((10.0, 80.0), tuple(wings["range"]))

    def test_every_preset_documents_the_common_params(self):
        for name, p in presets.PRESETS.items():
            self.assertTrue(p["description"], name)
            for key in presets.COMMON_PARAMS:
                if name in shape.REFERENCE_PRESETS and key == "scale":  # relief: the size comes from the art
                    continue
                self.assertIn(key, p["params"], "%s lacks %s" % (name, key))
            if "scale" in p["params"]:
                self.assertEqual((0.5, 2.5), tuple(p["params"]["scale"]["range"]))

    def test_documented_defaults_form_a_valid_attachment(self):
        for name, p in presets.PRESETS.items():
            if name in shape.REFERENCE_PRESETS:
                continue
            att = {"preset": name}
            att.update({k: v["default"] for k, v in p["params"].items()})
            self.assertEqual([], [e.to_dict() for e in presets.validate_attachment(att)], name)
            self.assertTrue(expand(att), name)


class ExpansionTest(unittest.TestCase):
    def test_every_variant_expands_to_valid_boxes(self):
        n = 0
        for name, att, key, val in all_variants():
            n += 1
            where = "%s %s=%s" % (name, key, val)
            boxes = expand(att)
            self.assertTrue(boxes, where)
            full = explicit(boxes)
            self.assertLessEqual(len(full), presets.MAX_BOXES, where)
            ids = [b["id"] for b in full]
            self.assertEqual(len(ids), len(set(ids)), "duplicate ids in " + where)
            for b in boxes:
                self.assertTrue(b["id"].startswith(att["id"] + "_"), where)
                self.assertIn(b["bone"], BONES, where)
                self.assertTrue(all(c >= 0.01 for c in b["size"]), "%s %s" % (where, b["id"]))
                tx, tz = b.get("taper", [1.0, 1.0])
                self.assertTrue(0.05 <= tx <= 3.0 and 0.05 <= tz <= 3.0, "%s %s taper" % (where, b["id"]))
                self.assertIn(b["material"], spec.MATERIALS, where)
                for ref in (b["color"], b["accent"]):
                    self.assertTrue(ref in PALETTE or (ref.startswith("#") and len(ref) == 7), "%s %s" % (where, ref))
            # the real pipeline accepts it and the model stays inside the contract limits
            resolved, _ = shape.resolve({"attachments": [att]})
            self.assertGreater(len(resolved), 14, where)
        self.assertGreater(n, 80)

    def test_expansion_is_deterministic_and_does_not_touch_its_input(self):
        att = {"id": "w", "preset": "wings", "style": "bat", "spread": 33.0, "scale": 1.3}
        before = copy.deepcopy(att)
        p = props(shoulders=1.2)
        first = presets.expand(att, p, shape.ANATOMY)
        self.assertEqual(first, presets.expand(att, p, shape.ANATOMY))
        self.assertEqual(before, att)
        self.assertEqual(props(shoulders=1.2), p)
        self.assertTrue(all(b["id"].startswith("w_") for b in first))

    def test_symmetric_presets_mirror_about_x0(self):
        checked = 0
        for name, att, key, val in all_variants():
            if (name, key, tuple(val) if isinstance(val, list) else val) in ASYMMETRIC or (name, None, None) in ASYMMETRIC:
                continue
            checked += 1
            self.assertLess(mirror_gap(expand(att)), 2e-3, "%s %s=%s is not symmetric" % (name, key, val))
        self.assertGreater(checked, 80)

    def test_orbit_presets_stay_symmetric_for_every_count(self):
        for style in ("orbs", "shards", "blades"):
            for count in range(2, 9):
                att = {"id": "o", "preset": "orbs", "style": style, "count": count}
                self.assertLess(mirror_gap(expand(att)), 2e-3, "orbs %s x%d" % (style, count))
        for count in range(3, 10):
            for style in ("spikes", "plates", "blades"):
                self.assertLess(mirror_gap(expand({"id": "s", "preset": "spine_spikes", "style": style, "count": count})), 2e-3)

    def test_intentionally_asymmetric_presets_really_are(self):
        for name, key, val in ASYMMETRIC:
            att = {"id": name, "preset": name}
            if key:
                att[key] = val
            self.assertGreater(mirror_gap(expand(att)), 0.05, "%s %s=%s" % (name, key, val))

    def test_parts_are_skinned_to_the_bones_they_should_follow(self):
        expected = {
            "crown": {"head"}, "horns": {"head"}, "helm": {"head"}, "halo": {"head"}, "tusks": {"head"}, "fangs": {"head"},
            "jaw": {"head"}, "hood": {"head", "chest"}, "mane": {"head", "chest"}, "pauldrons": {"arm_r"},
            "cape": {"chest", "torso", "root"}, "skirt": {"root", "thigh_r", "shin_r"}, "tail": {"root"}, "wings": {"chest"},
            "orbs": {"chest"}, "extra_arms": {"chest"}, "claws": {"hand_r"}, "ribcage": {"chest"}, "core": {"chest"},
            "spine_spikes": {"chest", "torso"}, "shield": {"forearm_l"}, "gauntlets": {"forearm_r", "hand_r"},
            "greaves": {"shin_r"}, "belt": {"torso", "thigh_r"}, "banner": {"chest", "torso"},
            "hairdo": {"head"}, "ribbons": {"hand_r", "shin_r", "head"}, "pouch": {"torso"},
        }
        self.assertEqual(set(REQUIRED), set(expected))
        for name, bones in expected.items():
            used = {b["bone"] for b in expand({"id": name, "preset": name})}
            self.assertTrue(used <= bones, "%s uses %s, expected within %s" % (name, sorted(used), sorted(bones)))
            self.assertTrue(used & bones, name)
        # asymmetric pauldrons put the big plate on the chosen arm and a small one on the other
        left = expand({"id": "p", "preset": "pauldrons", "style": "asymmetric", "side": "left"})
        self.assertEqual({"arm_l", "arm_r"}, {b["bone"] for b in left})
        big = max(left, key=lambda b: b["size"][0])
        self.assertEqual("arm_l", big["bone"])

    def test_mirrored_boxes_use_right_side_ids(self):
        for name, att, key, val in all_variants():
            for b in expand(att):
                if b.get("mirror"):
                    self.assertTrue(b["id"].endswith("_r"), "%s: %s" % (name, b["id"]))

    def test_scale_grows_free_standing_parts_proportionally(self):
        axes = {"crown": (0, 1, 2), "halo": (0, 1, 2), "tail": (0, 1, 2), "orbs": (0, 1, 2), "horns": (1, 2),
                "wings": (0, 1, 2), "core": (0, 1), "extra_arms": (1, 2)}
        for name, ax in axes.items():
            e1 = extent(expand({"id": name, "preset": name}))
            e2 = extent(expand({"id": name, "preset": name, "scale": 2.0}))
            for i in ax:
                ratio = (e2[i][1] - e2[i][0]) / (e1[i][1] - e1[i][0])
                self.assertAlmostEqual(2.0, ratio, delta=0.2, msg="%s axis %d" % (name, i))
        for name in ("halo", "tail", "orbs", "core", "wings"):
            e1 = extent(expand({"id": name, "preset": name}))
            eh = extent(expand({"id": name, "preset": name, "scale": 0.5}))
            for i in (0, 1):
                ratio = (eh[i][1] - eh[i][0]) / (e1[i][1] - e1[i][0])
                self.assertAlmostEqual(0.5, ratio, delta=0.1, msg="%s axis %d" % (name, i))

    def test_scale_never_shrinks_a_preset_when_increased(self):
        for name in presets.PRESETS:
            if name in shape.REFERENCE_PRESETS:  # no `scale`: the size comes from the reference
                continue
            small = extent(expand({"id": name, "preset": name, "scale": 0.5}))
            mid = extent(expand({"id": name, "preset": name}))
            big = extent(expand({"id": name, "preset": name, "scale": 2.0}))
            for i in range(3):
                self.assertLessEqual(small[i][1] - small[i][0], mid[i][1] - mid[i][0] + 1e-6, "%s %d" % (name, i))
                self.assertLessEqual(mid[i][1] - mid[i][0], big[i][1] - big[i][0] + 1e-6, "%s %d" % (name, i))
            growth = max((big[i][1] - big[i][0]) / (mid[i][1] - mid[i][0]) for i in range(3))
            self.assertGreater(growth, 1.1, name)

    def test_color_material_overrides_apply_to_the_main_parts_only(self):
        boxes = expand({"id": "c", "preset": "crown", "color": "#112233", "material": "stone"})
        ring = [b for b in boxes if b["id"] in ("c_front", "c_back", "c_side_r")]
        self.assertTrue(ring)
        for b in ring:
            self.assertEqual(("stone", "#112233"), (b["material"], b["color"]))
        gem = [b for b in boxes if b["id"] == "c_gem"][0]
        self.assertEqual("plate_gold", gem["material"])  # the gem keeps its own glow look
        default = [b for b in expand({"id": "c", "preset": "crown"}) if b["id"] == "c_front"][0]
        self.assertEqual(("plate_gold", "accent"), (default["material"], default["color"]))
        arm = [b for b in expand({"id": "t", "preset": "wings", "style": "feathered", "accent": "#abcdef"}) if b["id"].startswith("t_arm_")]
        self.assertEqual({"#abcdef"}, {b["color"] for b in arm})  # `accent` recolors the trim parts (the wing arm)


class ProportionTest(unittest.TestCase):
    def y_range(self, att, **kw):
        e = extent(expand(att, props(**kw)))
        return e[1]

    def test_horns_and_crown_rise_with_the_head(self):
        for name in ("horns", "crown", "halo", "helm"):
            att = {"id": name, "preset": name}
            lo = self.y_range(att)
            hi = self.y_range(att, head=1.6)
            self.assertGreater(hi[1] - lo[1], 0.28, name)  # the top follows the head (big heads also get bigger parts)
            if name != "helm":
                self.assertGreater(hi[0] - lo[0], 0.2, name)  # and so does the base

    def test_pauldrons_move_out_with_the_shoulders(self):
        att = {"id": "p", "preset": "pauldrons"}
        narrow = extent(expand(att, props()))[0]
        wide = extent(expand(att, props(shoulders=1.6)))[0]
        self.assertGreater(wide[1] - narrow[1], 0.1)
        self.assertLess(wide[0] - narrow[0], -0.1)
        # arm thickness also moves them (arms sit outside the chest)
        thick = extent(expand(att, props(arm_thickness=2.0)))[0]
        self.assertGreater(thick[1] - narrow[1], 0.1)

    def test_pauldrons_follow_the_shoulder_height_with_torso_height(self):
        att = {"id": "p", "preset": "pauldrons"}
        a = extent(expand(att, props()))[1]
        b = extent(expand(att, props(torso_height=1.4)))[1]
        self.assertGreater(b[1] - a[1], 0.15)

    def test_cape_and_tail_sit_behind_a_deeper_torso(self):
        for name in ("cape", "banner", "wings"):
            att = {"id": name, "preset": name}
            a = extent(expand(att, props()))[2]
            b = extent(expand(att, props(torso_depth=1.8)))[2]
            self.assertGreater(b[0] - a[0], 0.05, name)

    def test_wings_follow_chest_height_and_shoulders(self):
        att = {"id": "w", "preset": "wings"}
        a = extent(expand(att, props()))
        b = extent(expand(att, props(torso_height=1.4)))
        self.assertGreater(b[1][1] - a[1][1], 0.1)

    def test_claws_follow_the_fists(self):
        att = {"id": "c", "preset": "claws"}
        base = extent(expand(att, props()))
        wide = extent(expand(att, props(shoulders=1.6)))
        self.assertGreater(wide[0][1] - base[0][1], 0.15)
        big = extent(expand(att, props(fist=3.0)))
        low = extent(expand(att, props()))
        self.assertLess(big[1][0], low[1][0])  # bigger fists hang lower, the claws go with them

    def test_jaw_and_tusks_stay_on_a_scaled_head(self):
        for name in ("jaw", "tusks", "fangs"):
            att = {"id": name, "preset": name}
            a = extent(expand(att, props()))
            b = extent(expand(att, props(head=1.5)))
            self.assertLess(b[2][0], a[2][0] - 0.05, name)  # a larger head pushes the face, and its fangs, forward


class ClearanceTest(unittest.TestCase):
    PROP_SETS = None

    @classmethod
    def prop_sets(cls):
        if cls.PROP_SETS is None:
            cls.PROP_SETS = [props()] + [props(**presets.silhouette(n)["proportions"]) for n in SILHOUETTES]
        return cls.PROP_SETS

    def assert_clear(self, att, proportions, eps=0.002):
        bases = shape.base_boxes(proportions)
        for b in explicit(expand(att, proportions)):
            for c in shape.box_corners(b):
                for base in bases:
                    inside = all(base["min"][i] + eps < c[i] < base["min"][i] + base["size"][i] - eps for i in range(3))
                    self.assertFalse(inside, "%s of %s pokes into %s at %s" % (b["id"], att["preset"], base["id"], [round(v, 3) for v in c]))

    def test_capes_and_skirts_do_not_intersect_the_legs_or_back(self):
        for name in ("cape", "skirt"):
            for style in STYLES.get(name, ("tabard", "robe", "kilt")):
                for p in self.prop_sets():
                    self.assert_clear({"id": name, "preset": name, "style": style}, p)

    def test_wings_and_banners_clear_the_back_at_normal_proportions(self):
        for name in ("wings", "banner"):
            for style in STYLES.get(name, ("standard", "pennant", "sashimono")):
                self.assert_clear({"id": name, "preset": name, "style": style}, props(), eps=0.02)  # poles/wings are anchored 1cm into the back

    def test_skirt_boxes_ride_the_legs_so_they_can_move(self):
        boxes = expand({"id": "s", "preset": "skirt", "style": "robe"})
        for b in boxes:
            if b["id"].startswith(("s_fr", "s_bk", "s_sd")):
                self.assertIn(b["bone"], ("thigh_r", "shin_r"), b["id"])
        self.assertEqual({"thigh_r", "shin_r", "root"}, {b["bone"] for b in boxes})

    def test_cape_segments_ride_chest_torso_and_root(self):
        bones = [b["bone"] for b in expand({"id": "c", "preset": "cape", "style": "long"})]
        self.assertEqual(["chest", "torso", "root"], bones)


class ValidationTest(unittest.TestCase):
    def codes(self, att):
        return [(e.path, e.code) for e in presets.validate_attachment(att)]

    def test_valid_attachment_has_no_errors(self):
        self.assertEqual([], self.codes({"id": "h", "preset": "horns", "style": "ram", "scale": 1.2, "color": "#aabbcc",
                                         "accent": "eyes", "material": "bone", "length": 1}))

    def test_unknown_preset(self):
        self.assertEqual([("/preset", "unknown_preset")], self.codes({"preset": "wingz"}))
        self.assertEqual([("/preset", "unknown_preset")], self.codes({}))
        err = presets.validate_attachment({"preset": "wingz"})[0]
        self.assertIn("wings", err.message)  # lists the allowed names

    def test_enum_values_ranges_types_colors_materials(self):
        self.assertEqual([("/style", "unknown_value")], self.codes({"preset": "horns", "style": "wavy"}))
        self.assertEqual([("/scale", "out_of_range")], self.codes({"preset": "horns", "scale": 3.0}))
        self.assertEqual([("/scale", "out_of_range")], self.codes({"preset": "horns", "scale": 0.4}))
        self.assertEqual([("/spread", "out_of_range")], self.codes({"preset": "wings", "spread": 85}))
        self.assertEqual([("/spread", "out_of_range")], self.codes({"preset": "wings", "spread": 5}))
        self.assertEqual([("/count", "out_of_range")], self.codes({"preset": "orbs", "count": 9}))
        self.assertEqual([("/color", "bad_color")], self.codes({"preset": "horns", "color": "reddish"}))
        self.assertEqual([("/accent", "bad_color")], self.codes({"preset": "horns", "accent": "#12345"}))
        self.assertEqual([("/material", "bad_material")], self.codes({"preset": "horns", "material": "wood"}))
        self.assertEqual([("/scale", "bad_type")], self.codes({"preset": "horns", "scale": "big"}))
        self.assertEqual([("/scale", "bad_type")], self.codes({"preset": "horns", "scale": True}))
        self.assertEqual([("/count", "bad_type")], self.codes({"preset": "orbs", "count": 3.5}))
        self.assertEqual([("/gem", "bad_type")], self.codes({"preset": "crown", "gem": "yes"}))
        self.assertEqual([("/style", "bad_type")], self.codes({"preset": "horns", "style": 3}))
        self.assertEqual([("/id", "bad_type")], self.codes({"preset": "horns", "id": "Horns!"}))
        self.assertEqual([("/colour", "unknown_value")], self.codes({"preset": "horns", "colour": "primary"}))
        self.assertEqual([("", "bad_type")], self.codes("horns"))

    def test_every_problem_is_reported(self):
        got = self.codes({"preset": "wings", "style": "x", "spread": 99, "color": "nope"})
        self.assertEqual({("/style", "unknown_value"), ("/spread", "out_of_range"), ("/color", "bad_color")}, set(got))

    def test_expand_raises_the_first_problem(self):
        with self.assertRaises(BossForgeError) as cm:
            presets.expand({"preset": "horns", "style": "wavy"}, props(), shape.ANATOMY)
        self.assertEqual(("/style", "unknown_value"), (cm.exception.err.path, cm.exception.err.code))

    def test_shape_validation_prefixes_the_attachment_pointer(self):
        errs = shape.validate_shape({"attachments": [{"id": "h", "preset": "horns", "style": "wavy"}]})
        self.assertIn(("/attachments/0/style", "unknown_value"), [(e.path, e.code) for e in errs])


class SilhouetteTest(unittest.TestCase):
    def test_required_silhouettes_exist(self):
        self.assertTrue(set(SILHOUETTES) <= set(presets.list_silhouettes()))

    def test_default_silhouette_per_archetype(self):
        expected = {"knight": "knight", "undead": "skeletal_king", "beast": "beast", "demon": "fiend",
                    "elemental": "colossus", "golem": "colossus", "wraith": "wraith", "cultist": "caster"}
        for arch, name in expected.items():
            self.assertEqual(name, presets.default_silhouette(arch))
        self.assertEqual(set(expected), set(spec.data("archetypes")))  # every archetype has a default look
        self.assertEqual("plain", presets.default_silhouette("nonsense"))

    def test_silhouette_entries_are_complete_and_valid(self):
        for name in presets.list_silhouettes():
            sil = presets.silhouette(name)
            self.assertEqual({"description", "archetype", "proportions", "attachments"}, set(sil), name)
            self.assertTrue(sil["description"], name)
            self.assertIn(sil["archetype"], spec.data("archetypes"), name)
            for k, v in sil["proportions"].items():
                lo, hi = shape.PROPORTION_RANGES[k]
                self.assertTrue(lo <= v <= hi, "%s %s=%s" % (name, k, v))
            ids = [a["id"] for a in sil["attachments"]]
            self.assertEqual(len(ids), len(set(ids)), name)
            for a in sil["attachments"]:
                self.assertEqual([], presets.validate_attachment(a), "%s/%s" % (name, a["id"]))

    def test_every_silhouette_resolves_validates_and_builds(self):
        for name in presets.list_silhouettes():
            self.assertEqual([], shape.validate_shape({"silhouette": name}), name)
            boxes, applied = shape.resolve({"silhouette": name})
            self.assertLessEqual(len(boxes), shape.MAX_BOXES, name)
            model = shape.build_model({"shape": {"silhouette": name}, "mod_id": "m", "id": "boss"})
            self.assertLessEqual(max(model.atlas), 512, name)
            self.assertGreater(model.height, 1.8, name)
            self.assertLess(model.height, 3.2, name)

    def test_plain_is_the_unmodified_humanoid(self):
        boxes, props_ = shape.resolve({"silhouette": "plain"})
        self.assertEqual(14, len(boxes))
        self.assertEqual(shape.default_proportions(), props_)

    def test_silhouettes_are_clearly_different(self):
        names = [n for n in presets.list_silhouettes()]
        sils = {n: presets.silhouette(n) for n in names}
        for n in names:
            if n != "plain":
                self.assertGreaterEqual(len({a["preset"] for a in sils[n]["attachments"]}), 3, n)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                pa = {x["preset"] for x in sils[a]["attachments"]}
                pb = {x["preset"] for x in sils[b]["attachments"]}
                keys = set(sils[a]["proportions"]) | set(sils[b]["proportions"])
                prop_diff = sum(abs(sils[a]["proportions"].get(k, 1.0) - sils[b]["proportions"].get(k, 1.0)) for k in keys)
                self.assertGreaterEqual(len(pa ^ pb) + prop_diff, 1.5, "%s and %s look alike" % (a, b))

    def test_each_silhouette_has_its_signature_features(self):
        def has(name, preset, **kw):
            return any(a["preset"] == preset and all(a.get(k) == v for k, v in kw.items()) for a in presets.silhouette(name)["attachments"])

        brute = presets.silhouette("brute")["proportions"]
        self.assertLessEqual(brute["head"], 0.85)
        self.assertGreaterEqual(brute["shoulders"], 1.4)
        self.assertGreaterEqual(brute["arm_thickness"], 1.6)
        self.assertGreaterEqual(brute["fist"], 2.0)
        self.assertTrue(has("brute", "jaw") and has("brute", "spine_spikes"))
        caster = presets.silhouette("caster")["proportions"]
        self.assertLess(caster["bulk"], 1.0)
        self.assertLess(caster["shoulders"], 1.0)
        for p in ("hood", "cape", "skirt", "halo", "orbs"):
            self.assertTrue(has("caster", p), p)
        self.assertTrue(has("caster", "cape", style="tattered"))
        colossus = presets.silhouette("colossus")["proportions"]
        self.assertGreaterEqual(colossus["leg_thickness"], 1.8)
        self.assertGreaterEqual(colossus["torso_height"], 1.3)
        self.assertTrue(has("colossus", "pauldrons") and has("colossus", "core"))
        for p in ("horns", "wings", "tail", "claws"):
            self.assertTrue(has("fiend", p), p)
        self.assertTrue(has("fiend", "horns", style="demon") and has("fiend", "wings", style="bat") and has("fiend", "tail", style="spiked"))
        self.assertTrue(has("skeletal_king", "ribcage") and has("skeletal_king", "crown", style="spiked") and has("skeletal_king", "cape", style="tattered"))
        self.assertLess(presets.silhouette("skeletal_king")["proportions"]["torso_width"], 1.0)
        self.assertTrue(has("insectoid", "extra_arms") and has("insectoid", "fangs", style="mandibles") and has("insectoid", "spine_spikes"))
        self.assertLess(presets.silhouette("insectoid")["proportions"]["arm_thickness"], 1.0)

    def test_silhouette_is_returned_as_a_copy(self):
        sil = presets.silhouette("knight")
        sil["attachments"].clear()
        sil["proportions"]["head"] = 99
        again = presets.silhouette("knight")
        self.assertTrue(again["attachments"])
        self.assertNotEqual(99, again["proportions"]["head"])

    def test_unknown_silhouette_error(self):
        with self.assertRaises(BossForgeError) as cm:
            presets.silhouette("nope")
        self.assertEqual("unknown_silhouette", cm.exception.err.code)
        self.assertIn("knight", cm.exception.err.message)

    def test_shape_attachment_replaces_and_remove_drops_silhouette_parts(self):
        base, _ = shape.resolve({"silhouette": "fiend"})
        ids = {b["id"] for b in base}
        self.assertTrue(any(i.startswith("wings_") for i in ids))
        removed, _ = shape.resolve({"silhouette": "fiend", "remove": ["wings"]})
        self.assertFalse(any(b["id"].startswith("wings_") for b in removed))
        swapped, _ = shape.resolve({"silhouette": "fiend", "attachments": [{"id": "wings", "preset": "wings", "style": "flame"}]})
        self.assertTrue(any(b["id"].startswith("wings_fl") for b in swapped))
        self.assertFalse(any(b["id"].startswith("wings_m") for b in swapped))


def luminance(color):
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def convex_hull(points):
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def front_cells(boxes, step=0.02):
    """Grid cells (ix, iy) covered by the front-view silhouette (x, y) of the given boxes (mirror copies included)."""
    cells = set()
    for b in explicit(boxes):
        hull = convex_hull([(c[0], c[1]) for c in shape.box_corners(b)])
        xs, ys = [p[0] for p in hull], [p[1] for p in hull]
        for ix in range(int(min(xs) / step), int(max(xs) / step) + 2):
            for iy in range(int(min(ys) / step), int(max(ys) / step) + 2):
                x, y = ix * step, iy * step
                if all((hull[(i + 1) % len(hull)][0] - hull[i][0]) * (y - hull[i][1])
                       - (hull[(i + 1) % len(hull)][1] - hull[i][1]) * (x - hull[i][0]) >= -1e-9 for i in range(len(hull))):
                    cells.add((ix, iy))
    return cells


def face_hits(boxes, proportions=None):
    """Ids of boxes whose bounding box enters the open face: between the eyes/brow span, below the bangs line, in
    front of the face plane."""
    body = presets.body_from(proportions or props())
    he = body.he
    zf = body.hcz - he / 2.0
    hits = []
    for b in explicit(boxes):
        pts = shape.box_corners(b)
        lo = [min(c[i] for c in pts) for i in range(3)]
        hi = [max(c[i] for c in pts) for i in range(3)]
        if (lo[0] < 0.38 * he and hi[0] > -0.38 * he and lo[1] < body.head_top - 0.3 * he and hi[1] > body.head_bot
                and lo[2] < zf + 0.005):
            hits.append(b["id"])
    return hits


class SeraphWingsTest(unittest.TestCase):
    ATT = {"id": "w", "preset": "wings", "style": "seraph", "spread": 15.0}

    def test_rises_well_above_the_head_and_hangs_to_hip_or_knee_height(self):
        body = presets.body_from(None)
        e = extent(expand(self.ATT))
        self.assertGreaterEqual(e[1][1], body.head_top + 0.4)
        self.assertLess(e[1][0], body.hip)  # the long tips hang below the hip joint
        self.assertGreater(e[1][0], 0.2)    # but stay off the floor

    def test_materials_feathers_use_vane_material_and_an_override_reaches_all(self):
        boxes = expand(self.ATT)
        self.assertEqual({"silk_feather"}, {b["material"] for b in boxes if b["id"].startswith("w_f")})
        self.assertEqual({"silk"}, {b["material"] for b in boxes if b["id"].startswith("w_st")})
        self.assertEqual({"leather"}, {b["material"] for b in expand(dict(self.ATT, material="leather"))})

    def test_leading_edge_is_a_stack_of_thick_blocks_not_a_rod(self):
        stairs = [b for b in expand(self.ATT) if b["id"].startswith("w_st")]
        self.assertGreaterEqual(len(stairs), 6)
        for b in stairs:
            self.assertGreaterEqual(min(b["size"][0], b["size"][1]), 0.14)
            self.assertNotIn("rotate", b)  # axis-aligned blocks give the stepped silhouette
        xs = [b["min"][0] for b in stairs]
        ys = [b["min"][1] for b in stairs]
        self.assertEqual(sorted(xs), xs)  # every step climbs up and out
        self.assertEqual(sorted(ys), ys)

    def test_sail_has_many_rows_and_a_reasonable_box_count(self):
        boxes = expand(self.ATT)
        rows = {b["id"].split("_")[1] for b in boxes if b["id"].startswith("w_f")}
        self.assertGreaterEqual(len(rows), 5)
        self.assertLessEqual(len(explicit(boxes)), 110)

    def test_sail_is_solid_from_front_and_back_for_usual_spreads(self):
        for spread in (10.0, 15.0, 25.0, 30.0, 35.0, 45.0):
            att = dict(self.ATT, spread=spread)
            feathers = [b for b in expand(att) if b["id"].startswith("w_f") and b["id"].endswith("_r")]
            rows = {}
            for ix, iy in front_cells(feathers):
                if ix >= 0:  # the right wing only: the two wings leave a gap behind the spine
                    rows.setdefault(iy, []).append(ix)
            low = min(rows)
            for iy in range(low + 24, max(rows) + 1):  # above the jagged lower edge (0.48 blocks)
                xs = sorted(rows.get(iy, ()))
                self.assertTrue(xs, "row %d empty at spread %s" % (iy, spread))
                gaps = [(a * 0.02, b * 0.02) for a, b in zip(xs, xs[1:]) if b - a > 1]
                self.assertEqual([], gaps, "holes in the sail at y=%.2f spread %s" % (iy * 0.02, spread))

    def test_rows_are_layered_in_depth_and_the_middle_row_is_longest(self):
        rows = {}
        for b in expand(self.ATT):
            if b["id"].startswith("w_f") and b["id"].endswith("_r"):
                rows.setdefault(int(b["id"].split("_")[1][1:]), []).append(b)
        self.assertGreaterEqual(len(rows), 5)
        mean_z = {k: sum(b["min"][2] for b in rows[k]) / len(rows[k]) for k in rows}
        for parity in (0, 1):  # odd rows are shifted along the strut by half a feather, so compare like with like
            ks = sorted(k for k in rows if k % 2 == parity)
            self.assertEqual(sorted(mean_z[k] for k in ks), [mean_z[k] for k in ks])  # front row nearest, back row farthest
        self.assertLess(mean_z[min(rows)] + 0.08, mean_z[max(rows)])
        mean_len = {k: sum(b["size"][1] for b in rows[k]) / len(rows[k]) for k in rows}
        mid = max(mean_len, key=mean_len.get)
        self.assertEqual(mid, (max(rows) + min(rows)) // 2)
        self.assertLess(mean_len[min(rows)], mean_len[mid] * 0.8)
        self.assertAlmostEqual(mean_len[min(rows)], mean_len[max(rows)], delta=0.1)

    def test_colors_run_white_to_lilac_to_tip_from_both_sides(self):
        att = dict(self.ATT, color="#ffffff", accent="#c0b0ff", tip="#6040c0")
        row_color = {}
        for b in expand(att):
            if b["id"].startswith("w_f") and b["id"].endswith("_r"):
                row_color.setdefault(int(b["id"].split("_")[1][1:]), []).append(b["color"])
        n = max(row_color)
        mean = {k: sum(luminance(c) for c in v) / len(v) for k, v in row_color.items()}
        for k in range(n // 2):  # darker towards the middle row, mirrored on the back half
            self.assertGreater(mean[k], mean[k + 1], k)
            self.assertAlmostEqual(mean[k], mean[n - k], delta=1.0, msg=k)
        self.assertEqual("#ffffff", min(row_color[0], key=lambda c: -luminance(c)))  # outer rows start at the `color`
        stairs = {b["color"] for b in expand(att) if b["id"].startswith("w_st")}
        self.assertEqual({"#ffffff"}, stairs)

    def test_wings_fan_out_broadly_at_the_documented_spread(self):
        body = presets.body_from(props())
        att = dict(self.ATT, spread=30.0)
        e = extent(expand(att))
        self.assertGreaterEqual(e[0][1], 1.25)   # each wing reaches 1.25-1.6 blocks out from the centre line
        self.assertLessEqual(e[0][1], 1.6)
        self.assertGreaterEqual(e[1][1], body.head_top + 0.3)
        # the leading edge sweeps outwards, and the lowest feather tips end further out than the top of the edge
        stairs = [b for b in expand(att) if b["id"].startswith("w_st")]
        top_x = max(b["min"][0] + b["size"][0] for b in stairs)
        feathers = [b for b in expand(att) if b["id"].startswith("w_f") and b["id"].endswith("_r")]
        low = min(min(c[1] for c in shape.box_corners(b)) for b in explicit(feathers))
        out = max(max(c[0] for c in shape.box_corners(b)) for b in explicit(feathers))
        self.assertGreater(out, top_x + 0.3)   # the feathers fan out beyond the top of the leading edge
        self.assertLess(low, body.hip)
        narrow = extent(expand(dict(self.ATT, spread=15.0)))[0][1]
        self.assertGreater(e[0][1], narrow + 0.3)

    def test_colour_runs_smoothly_down_the_rows(self):
        att = dict(self.ATT, color="#ffffff", accent="#e0d8ff")
        rows = {}
        for b in expand(att):
            if b["id"].startswith("w_f") and b["id"].endswith("_r"):
                rows.setdefault(int(b["id"].split("_")[1][1:]), []).append(luminance(b["color"]))
        mean = [sum(rows[k]) / len(rows[k]) for k in sorted(rows)]
        half = mean[:len(mean) // 2 + 1]
        steps = [a - b for a, b in zip(half, half[1:])]
        self.assertTrue(all(st > 0 for st in steps), steps)  # lighter at the top, darker towards the longest row
        self.assertLess(max(steps), 0.5 * (half[0] - half[-1]))  # no abrupt jump between neighbouring rows
        self.assertGreater(half[-1], 120)  # soft lilac tips, not saturated purple

    def test_tip_defaults_to_a_darker_violet_of_the_accent(self):
        def tip_of(att):
            rows = {}
            for b in expand(att):
                if b["id"].startswith("w_f") and b["id"].endswith("_r"):
                    rows.setdefault(int(b["id"].split("_")[1][1:]), []).append(luminance(b["color"]))
            return min(sum(v) / len(v) for v in rows.values())

        pale = tip_of(dict(self.ATT, accent="#e0d8ff"))
        dark = tip_of(dict(self.ATT, accent="#a090ff"))
        self.assertLess(dark, pale)  # the tip follows the accent
        explicit_tip = tip_of(dict(self.ATT, accent="#e0d8ff", tip="#101030"))
        self.assertLess(explicit_tip, pale - 40)

    def test_palette_key_colors_are_accepted(self):
        boxes = expand(dict(self.ATT, color="primary", accent="secondary", tip="accent"))
        for b in boxes:
            self.assertTrue(b["color"] in PALETTE or b["color"].startswith("#"), b["id"])

    def test_stays_inside_the_model_limits_at_every_spread_and_scale(self):
        for spread in (10.0, 30.0, 80.0):
            for scale in (0.5, 1.0, 2.0):
                boxes, _ = shape.resolve({"attachments": [dict(self.ATT, spread=spread, scale=scale)]})
                self.assertLessEqual(len(boxes), shape.MAX_BOXES)
        shape.resolve({"attachments": [dict(self.ATT, spread=30.0, scale=2.5)]})
        with self.assertRaises(BossForgeError) as cm:  # a 10-block span does not fit the model limits: a clear error
            shape.resolve({"attachments": [dict(self.ATT, spread=80.0, scale=2.5)]})
        self.assertEqual("out_of_bounds", cm.exception.err.code)

    def test_spread_opens_the_wings(self):
        narrow = extent(expand(dict(self.ATT, spread=10.0)))
        wide = extent(expand(dict(self.ATT, spread=45.0)))
        self.assertGreater(wide[0][1], narrow[0][1] + 0.2)
        self.assertLess(wide[1][1], narrow[1][1] - 0.2)


class HairdoTest(unittest.TestCase):
    ALL = {"id": "h", "preset": "hairdo", "ribbon_loops": True, "pins": True}

    def test_the_face_stays_open_for_every_style_and_head_size(self):
        for style in STYLES["hairdo"]:
            for head in (0.6, 1.0, 1.6, 2.2):
                p = props(head=head)
                self.assertEqual([], face_hits(expand(dict(self.ALL, style=style), p), p), "%s head=%s" % (style, head))
        sil = presets.silhouette("caster")["proportions"]
        self.assertEqual([], face_hits(expand(self.ALL, props(**sil)), props(**sil)))

    def test_hair_is_in_the_hair_colour_and_the_ribbons_are_white_blue(self):
        boxes = expand(self.ALL)
        main = [b for b in boxes if b["id"] in ("h_top", "h_back", "h_side_r")]
        self.assertEqual(3, len(main))
        for b in main:
            self.assertEqual(("hair", "hair"), (b["color"], b["material"].replace("hair_soft", "hair")))
        loops = [b for b in boxes if b["id"].startswith("h_loop_")]
        self.assertEqual({"#f4f8ff", "#9cc2ff"}, {b["color"] for b in loops})
        recolored = [b for b in expand(dict(self.ALL, accent="#ff0000")) if b["id"].startswith("h_loop_")]
        self.assertIn("#ff0000", {b["color"] for b in recolored})

    def test_side_locks_frame_the_face_down_to_the_chest(self):
        body = presets.body_from(props())
        locks = [b for b in explicit(expand(self.ALL)) if b["id"].startswith("h_lk")]
        self.assertEqual(6, len(locks))
        lo = min(c[1] for b in locks for c in shape.box_corners(b))
        self.assertLess(lo, body.head_bot - 0.1)    # past the chin
        self.assertGreater(lo, body.chest_bot)      # not below the chest
        for b in locks:
            self.assertGreaterEqual(min(abs(c[0]) for c in shape.box_corners(b)), body.he / 2.0 - 0.03)  # beside the face
        off = expand(dict(self.ALL, locks=False, bangs=False))
        self.assertFalse([b for b in off if b["id"].startswith(("h_lk", "h_bang"))])

    def test_styles_put_the_hair_where_they_say(self):
        body = presets.body_from(props())
        for style in ("bun", "twin_buns"):
            e = extent(expand(dict(self.ALL, style=style)))
            self.assertGreater(e[1][1], body.head_top + 0.1, style)  # a bun sits above the head
        buns = [b for b in expand(dict(self.ALL, style="twin_buns")) if b["id"].startswith("h_bn")]
        self.assertTrue(buns and all(b["id"].endswith("_r") and b.get("mirror") for b in buns))
        single = [b for b in expand(dict(self.ALL, style="bun")) if b["id"].startswith("h_bn")]
        self.assertTrue(single and not any(b.get("mirror") for b in single))
        self.assertGreater(min(b["min"][2] for b in single), body.hcz - 0.1)  # on the back half of the head
        self.assertTrue({b["color"] for b in single} == {"hair"})            # the dark band is a material, not a color
        self.assertIn("leather", {b["material"] for b in single})
        for style in ("ponytail", "braid"):
            e = extent(expand(dict(self.ALL, style=style)))
            self.assertLess(e[1][0], body.head_bot - 0.3, style)             # hangs below the head
            self.assertGreater(e[2][1], body.hcz + body.he / 2.0 + 0.2, style)  # behind it

    def test_ribbon_loops_hang_behind_the_head_and_pins_sit_in_front_of_the_bangs(self):
        body = presets.body_from(props())
        zb = body.hcz + body.he / 2.0
        zf = body.hcz - body.he / 2.0
        loops = [b for b in explicit(expand(self.ALL)) if b["id"].startswith("h_loop_")]
        self.assertEqual(8, len(loops))
        self.assertTrue(all(min(c[2] for c in shape.box_corners(b)) > zb for b in loops))
        pins = [b for b in expand(self.ALL) if b["id"].startswith(("h_leaf", "h_pin"))]
        self.assertEqual({"#35c9b8", "#e6c050"}, {b["color"] for b in pins})
        self.assertTrue(all(b["min"][2] < zf for b in pins))
        plain = expand({"id": "h", "preset": "hairdo"})
        self.assertFalse([b for b in plain if b["id"].startswith(("h_loop", "h_leaf", "h_pin"))])

    def test_back_hair_is_a_narrow_curtain_not_a_wider_block(self):
        body = presets.body_from(props())
        boxes = explicit(expand(self.ALL))
        curtain = [b for b in boxes if b["id"].startswith("h_cu")]
        self.assertGreaterEqual(len(curtain), 2)
        lo = min(c[1] for b in curtain for c in shape.box_corners(b))
        self.assertLess(lo, body.head_bot - 0.1)      # hangs below the head, to the shoulder blades
        self.assertGreater(lo, body.chest_bot)
        for b in curtain:
            xs = [c[0] for c in shape.box_corners(b)]
            self.assertLessEqual(max(xs) - min(xs), body.he + 1e-6, b["id"])
        # no hair box of the cap or the curtain sticks out more than a few cm beside the head
        for b in boxes:
            if b["id"].startswith(("h_top", "h_back", "h_cu", "h_side")):
                self.assertLessEqual(max(abs(c[0]) for c in shape.box_corners(b)), body.he / 2.0 + 0.045, b["id"])
        back = [b for b in boxes if b["id"] == "h_back"][0]
        self.assertLess(max(c[1] for c in shape.box_corners(back)) - min(c[1] for c in shape.box_corners(back)), 0.7 * body.he)

    def test_hair_follows_the_head_size(self):
        for style in ("bun", "ponytail"):
            a = extent(expand({"id": "h", "preset": "hairdo", "style": style}, props()))
            b = extent(expand({"id": "h", "preset": "hairdo", "style": style}, props(head=1.6)))
            self.assertGreater(b[0][1] - b[0][0], a[0][1] - a[0][0] + 0.15, style)
            self.assertGreater(b[1][1], a[1][1] + 0.25, style)


class RibbonsTest(unittest.TestCase):
    def test_every_style_hangs_on_its_own_bones(self):
        bones = {"wrist": {"hand_r"}, "hem": {"shin_r"}, "back": {"head"}, "all": {"hand_r", "shin_r", "head"}}
        for style, want in bones.items():
            got = {b["bone"] for b in expand({"id": "r", "preset": "ribbons", "style": style})}
            self.assertEqual(want, got, style)

    def test_ribbons_are_thin_tilted_multi_segment_strips_in_two_tones(self):
        boxes = expand({"id": "r", "preset": "ribbons", "style": "all"})
        chains = {}
        for b in boxes:
            chains.setdefault(b["id"].split("_")[1].rstrip("0123456789"), []).append(b)
        self.assertEqual({"wa", "wb", "ha", "hb", "bk"}, set(chains))
        for name, chain in chains.items():
            self.assertGreaterEqual(len(chain), 2, name)
            for b in chain:
                self.assertIn("rotate", b, "%s must tilt so it drifts" % name)
                self.assertLessEqual(b["size"][0], 0.12, name)
                self.assertLessEqual(b["size"][2], 0.03, name)
        self.assertEqual({"#f4f8ff", "#bcd6ff"}, {b["color"] for b in boxes})
        recolored = expand({"id": "r", "preset": "ribbons", "color": "#112233", "accent": "#445566"})
        self.assertEqual({"#112233", "#445566"}, {b["color"] for b in recolored})

    def test_wrist_ribbons_fall_past_the_hem_and_flare_outwards(self):
        body = presets.body_from(props())
        e = extent(expand({"id": "r", "preset": "ribbons", "style": "wrist"}))
        self.assertLess(e[1][0], 0.2)  # down to the boots
        self.assertGreater(e[0][1], body.arm_cx + body.arm_t / 2.0 + 0.15)  # they flare out beyond the arms
        self.assertGreater(e[2][1], 0.15)  # and trail backwards

    def test_length_stretches_and_the_floor_is_never_crossed(self):
        base = {"id": "r", "preset": "ribbons", "style": "all"}
        short = extent(expand(dict(base, length=0.5)))
        long_ = extent(expand(dict(base, length=2.0)))
        self.assertGreater(long_[2][1] - long_[2][0], short[2][1] - short[2][0] + 0.3)  # the excess trails on the floor
        for length in (0.5, 1.0, 2.0):
            for scale in (0.5, 1.0, 2.5):
                e = extent(expand(dict(base, length=length, scale=scale)))
                self.assertGreaterEqual(e[1][0], 0.0, "length %s scale %s" % (length, scale))

    def test_ribbons_do_not_enter_the_face(self):
        for style in ("wrist", "hem", "back", "all"):
            self.assertEqual([], face_hits(expand({"id": "r", "preset": "ribbons", "style": style})), style)


class SkirtSlitAndHemTest(unittest.TestCase):
    def sel(self, boxes, regex):
        return [b for b in explicit(boxes) if re.match(regex, b["id"])]

    @staticmethod
    def x_range(b):
        xs = [c[0] for c in shape.box_corners(b)]
        return min(xs), max(xs)

    @staticmethod
    def y_range(b):
        ys = [c[1] for c in shape.box_corners(b)]
        return min(ys), max(ys)

    def test_slits_flank_a_centre_panel_and_open_over_the_legs(self):
        body = presets.body_from(props())
        opened = expand({"id": "s", "preset": "skirt", "style": "robe", "slit": True, "scale": 0.65})
        closed = expand({"id": "s", "preset": "skirt", "style": "robe", "scale": 0.65})
        self.assertFalse(self.sel(closed, r"s_fp"))
        panel = self.sel(opened, r"s_fp\d")
        self.assertTrue(panel)
        self.assertTrue(all(b["id"].endswith(("_r", "_l")) for b in panel))  # one half per leg, so the legs stay free
        outer = [b for b in self.sel(opened, r"s_fr\d") if self.x_range(b)[1] > 0]  # right front panels, top to bottom
        outer.sort(key=lambda b: -self.y_range(b)[1])
        self.assertGreaterEqual(len(outer), 2)
        top_inner = min(abs(c[0]) for c in shape.box_corners(outer[0]))
        low_inner = min(abs(c[0]) for c in shape.box_corners(outer[-1]))
        self.assertLess(top_inner, 0.02)  # closed above the slit
        panel_edge = max(self.x_range(b)[1] for b in panel if self.x_range(b)[1] > 0)
        self.assertGreater(low_inner, panel_edge + 0.03)  # a real gap between the centre panel and the outer panel
        leg_lo, leg_hi = body.leg_cx - body.leg_t / 2.0, body.leg_cx + body.leg_t / 2.0
        self.assertTrue(leg_lo < low_inner and panel_edge < leg_hi)  # the gap sits over the leg, which shows lit
        # the trim and the hem layer run across the whole width: the panels meet at the hem
        trim = [b for b in self.sel(opened, r"s_frt") if self.x_range(b)[1] > 0][0]
        self.assertLess(self.x_range(trim)[0], 0.02)
        self.assertGreater(self.x_range(trim)[1], low_inner + 0.1)
        # the centre panel stands proud of the front panel, ends above the trim, and the back stays closed
        fr = [b for b in self.sel(opened, r"s_fr\d") if self.x_range(b)[1] > 0][0]
        self.assertGreater(panel[0]["size"][2], fr["size"][2])
        lowest_panel = min(self.y_range(b)[0] for b in panel)
        self.assertGreater(lowest_panel, min(self.y_range(b)[0] for b in self.sel(opened, r"s_frt")) + 0.05)
        self.assertTrue(all(min(abs(c[0]) for c in shape.box_corners(b)) < 0.02 for b in self.sel(opened, r"s_bk\d")))
        self.assertEqual({"thigh_r", "shin_r", "root"}, {b["bone"] for b in opened})

    def test_panel_alone_adds_the_centre_panel_without_opening_the_front(self):
        boxes = expand({"id": "s", "preset": "skirt", "style": "robe", "panel": True})
        self.assertTrue(self.sel(boxes, r"s_fp\d"))
        for b in self.sel(boxes, r"s_fr\d"):
            self.assertLess(min(abs(c[0]) for c in shape.box_corners(b)), 0.02)
        self.assertEqual({"#f2ead3"}, {b["color"] for b in boxes if b["id"].startswith("s_fp")})

    def test_hem_layer_is_lighter_smooth_and_hangs_below_the_normal_hem(self):
        plain = expand({"id": "s", "preset": "skirt", "style": "robe", "scale": 0.65})
        layered = expand({"id": "s", "preset": "skirt", "style": "robe", "scale": 0.65, "hem": True})
        extra = [b for b in layered if b["id"] not in {p["id"] for p in plain}]
        self.assertTrue(extra)
        self.assertTrue(all(b["id"].startswith("s_h") for b in extra))
        layer = [b for b in extra if re.match(r"s_h[fbs]\d", b["id"])]
        edge = [b for b in extra if re.match(r"s_h[fbs]t", b["id"])]
        self.assertTrue(layer and edge)
        self.assertEqual({"#e8eefa"}, {b["color"] for b in layer})
        self.assertEqual({"lace"}, {b["material"] for b in layer})  # a smooth frill material, never the checker-grained cloth
        self.assertTrue(all(b["color"] == "accent" for b in edge))  # a thin trim closes the lower edge
        recolored = expand({"id": "s", "preset": "skirt", "style": "robe", "scale": 0.65, "hem": True, "hem_color": "#102030"})
        self.assertEqual({"#102030"}, {b["color"] for b in recolored if re.match(r"s_h[fbs]\d", b["id"])})
        low = lambda boxes: min(c[1] for b in explicit(boxes) for c in shape.box_corners(b))
        self.assertLess(low(layer), low(plain) - 0.04)
        self.assertGreater(low(layer), 0.0)
        self.assertLessEqual(len({b["bone"] for b in extra}), 1)
        # the layer sticks out of the panels so the faces never coincide
        panel = [b for b in plain if b["id"].startswith("s_fr0")][0]
        under = [b for b in layer if b["id"].startswith("s_hf")][0]
        self.assertGreater(under["size"][2], panel["size"][2])

    def test_material_override_reaches_every_skirt_box(self):
        for opts in ({}, {"slit": True, "hem": True}, {"panel": True, "hem": True, "style": "kilt"}):
            boxes = expand(dict({"id": "s", "preset": "skirt", "material": "silk"}, **opts))
            self.assertEqual({"silk"}, {b["material"] for b in boxes}, opts)
        default = expand({"id": "s", "preset": "skirt", "style": "robe", "hem": True, "panel": True})
        self.assertEqual({"cloth", "plate_gold", "lace"}, {b["material"] for b in default})  # trim stays gold without an override

    def test_slit_and_hem_clear_the_body_for_every_style_and_silhouette(self):
        for style in ("tabard", "robe", "kilt"):
            for scale in (0.65, 1.0):
                for p in ClearanceTest.prop_sets():
                    att = {"id": "s", "preset": "skirt", "style": style, "slit": True, "hem": True, "scale": scale}
                    ClearanceTest().assert_clear(att, p)

    def test_default_skirt_is_unchanged_by_the_new_options(self):
        a = expand({"id": "s", "preset": "skirt", "style": "robe"})
        b = expand({"id": "s", "preset": "skirt", "style": "robe", "slit": False, "panel": False, "hem": False})
        self.assertEqual(a, b)

    def test_halves_meet_at_the_centre_without_a_seam(self):
        for b in self.sel(expand({"id": "s", "preset": "skirt", "hem": True, "panel": True}), r"s_(fr|bk|fp|hf|hb)\d"):
            if b["id"].endswith("_r"):
                self.assertAlmostEqual(0.0, min(c[0] for c in shape.box_corners(b)), delta=0.004, msg=b["id"])


class ArmClearTest(unittest.TestCase):
    """The arms (sleeve, cuff, hand) must stay visible and unclipped next to the skirt and the pouch."""
    NIGHT = None

    @classmethod
    def night(cls):
        if cls.NIGHT is None:
            cls.NIGHT = props(**dict(presets.silhouette("caster")["proportions"], head=1.0, shoulders=0.9, arm_thickness=0.75, leg_thickness=0.75))
        return cls.NIGHT

    @staticmethod
    def hand_zone(proportions):
        """(x0, x1, y0, y1) of the right fist and the lowest part of the forearm (cuff), seen from the front."""
        bases = {b["id"]: b for b in shape.base_boxes(proportions)}
        fist, low = bases["fist_r"], bases["arm_r_lower"]
        return (min(fist["min"][0], low["min"][0]), max(fist["min"][0] + fist["size"][0], low["min"][0] + low["size"][0]),
                fist["min"][1], low["min"][1] + 0.08)

    def occluders(self, attachments, proportions):
        x0, x1, y0, y1 = self.hand_zone(proportions)
        hits = []
        for att in attachments:
            for b in explicit(expand(att, proportions)):
                if b["bone"].endswith("_l") or b["bone"] in ("arm_r", "forearm_r", "hand_r"):
                    continue
                pts = shape.box_corners(b)
                bx0, bx1 = min(c[0] for c in pts), max(c[0] for c in pts)
                by0, by1 = min(c[1] for c in pts), max(c[1] for c in pts)
                if bx1 > x0 + 0.004 and bx0 < x1 - 0.004 and by1 > y0 + 0.004 and by0 < y1 - 0.004:
                    hits.append(b["id"])
        return hits

    def test_default_skirt_and_pouch_do_cover_the_hands_which_arm_clear_fixes(self):
        night = self.night()
        plain = [{"id": "skirt", "preset": "skirt", "style": "robe", "scale": 0.65, "slit": True, "hem": True},
                 {"id": "pouch", "preset": "pouch"}]
        self.assertTrue(self.occluders(plain, night))  # detector check: the old defaults really cover the hands
        clear = [dict(a, arm_clear=True) for a in plain]
        self.assertEqual([], self.occluders(clear, night))

    def test_arm_clear_skirt_leaves_the_hands_free_for_every_style_and_silhouette(self):
        for style in ("tabard", "robe", "kilt"):
            for scale in (0.5, 0.65, 1.0, 1.5):
                for opts in ({}, {"slit": True, "hem": True}, {"panel": True, "hem": True}):
                    for p in ClearanceTest.prop_sets():
                        att = dict({"id": "s", "preset": "skirt", "style": style, "scale": scale, "arm_clear": True}, **opts)
                        self.assertEqual([], self.occluders([att], p), "%s %s %s" % (style, scale, opts))

    def test_arm_clear_pouch_stays_between_the_centre_and_the_arm_for_both_sides(self):
        for side in ("right", "left"):
            for vials in (0, 1, 4):
                for p in ClearanceTest.prop_sets():
                    body = presets.body_from(p)
                    boxes = explicit(expand({"id": "p", "preset": "pouch", "side": side, "vials": vials, "arm_clear": True}, p))
                    for b in boxes:
                        pts = shape.box_corners(b)
                        self.assertLessEqual(max(abs(c[0]) for c in pts), body.arm_cx - body.arm_t / 2.0 - 0.002, "%s %s" % (side, b["id"]))
        # the default pouch is unchanged by the new option
        a = expand({"id": "p", "preset": "pouch"})
        self.assertEqual(a, expand({"id": "p", "preset": "pouch", "arm_clear": False}))

    def test_arm_clear_skirt_flares_only_below_the_hands_and_stays_clear_of_the_body(self):
        night = self.night()
        body = presets.body_from(night)
        arm_inner = body.arm_cx - body.arm_t / 2.0
        att = {"id": "s", "preset": "skirt", "style": "robe", "scale": 0.65, "slit": True, "hem": True, "arm_clear": True}
        hands = body.fist_bot - 0.08
        for b in explicit(expand(att, night)):
            pts = shape.box_corners(b)
            if min(c[1] for c in pts) >= hands - 1e-6:
                self.assertLessEqual(max(abs(c[0]) for c in pts), arm_inner, b["id"])  # above the hands: inside the arms
        top = [b for b in explicit(expand(att, night)) if b["id"].startswith(("s_fr0", "s_bk0"))]
        wide = [b for b in explicit(expand(att, night)) if b["id"].startswith(("s_fr1", "s_bk1"))]
        self.assertGreater(max(max(abs(c[0]) for c in shape.box_corners(b)) for b in wide), max(max(abs(c[0]) for c in shape.box_corners(b)) for b in top) + 0.12)
        for style in ("tabard", "robe", "kilt"):
            for scale in (0.65, 1.0):
                for p in ClearanceTest.prop_sets():
                    ClearanceTest().assert_clear({"id": "s", "preset": "skirt", "style": style, "slit": True, "hem": True, "scale": scale, "arm_clear": True}, p)

    def test_arm_clear_skirt_keeps_the_ids_and_the_default_unchanged(self):
        a = expand({"id": "s", "preset": "skirt", "slit": True, "hem": True, "scale": 0.65})
        b = expand({"id": "s", "preset": "skirt", "slit": True, "hem": True, "scale": 0.65, "arm_clear": False})
        self.assertEqual(a, b)
        ids = {x["id"] for x in expand({"id": "s", "preset": "skirt", "slit": True, "hem": True, "scale": 0.65, "arm_clear": True})}
        for want in ("s_fr0_0_r", "s_fr1_0_r", "s_fp0_0_r", "s_bk0_0_r", "s_frt_0_r", "s_band", "s_hf0_r", "s_hft0_r"):
            self.assertIn(want, ids)

    def test_every_arm_box_rides_the_arm_bones_and_presets_keep_off_them(self):
        want = {"arm_r_upper": "arm_r", "arm_r_lower": "forearm_r", "fist_r": "hand_r",
                "arm_l_upper": "arm_l", "arm_l_lower": "forearm_l", "fist_l": "hand_l"}
        for b in shape.base_boxes(self.night()):
            if b["id"] in want:
                self.assertEqual(want[b["id"]], b["bone"], b["id"])
        arm_bones = {"arm_r", "forearm_r", "hand_r", "arm_l", "forearm_l", "hand_l"}
        for name in ("skirt", "pouch", "ribbons", "hairdo", "wings"):
            used = {x["bone"] for x in expand({"id": name, "preset": name}, self.night())}
            if name == "ribbons":
                self.assertEqual({"hand_r"}, used & arm_bones)  # the wrist ribbons follow the hand
            else:
                self.assertFalse(used & arm_bones, name)

    def test_layered_pauldrons_cover_only_the_shoulder_cap(self):
        body = presets.body_from(self.night())
        upper = body.arm_top - body.elbow
        for scale, share in ((1.0, 0.45), (0.8, 0.36)):
            boxes = explicit(expand({"id": "p", "preset": "pauldrons", "style": "layered", "scale": scale}, self.night()))
            low = min(c[1] for b in boxes for c in shape.box_corners(b))
            self.assertGreaterEqual(low, body.arm_top - share * upper, "scale %s" % scale)  # the sleeve shows below

    def test_wrist_ribbons_hang_outside_the_arm_next_to_the_hand(self):
        body = presets.body_from(self.night())
        boxes = explicit(expand({"id": "r", "preset": "ribbons", "style": "wrist"}, self.night()))
        right = [b for b in boxes if b["bone"] == "hand_r"]
        outer = max(c[0] for b in right for c in shape.box_corners(b))
        self.assertGreater(outer, body.arm_cx + body.arm_t / 2.0 + 0.1)
        for b in right:  # no ribbon box starts inside the arm column in front of the sleeve
            pts = shape.box_corners(b)
            if min(c[0] for c in pts) < body.arm_cx + body.arm_t / 2.0 - 0.05:
                self.assertGreater(min(c[2] for c in pts), body.arm_t / 2.0 - 0.01, b["id"])  # only behind the arm


class GreavesBootTest(unittest.TestCase):
    def test_material_override_reaches_every_box(self):
        for style in ("plate", "spiked", "sabaton"):
            boxes = expand({"id": "g", "preset": "greaves", "style": style, "material": "silk", "cuff": True, "sole": True})
            self.assertEqual({"silk"}, {b["material"] for b in boxes}, style)
            default = expand({"id": "g", "preset": "greaves", "style": style})
            self.assertIn("plate_gold", {b["material"] for b in default})  # existing looks are unchanged without an override

    def test_cuff_and_sole_are_optional_boot_parts(self):
        plain = expand({"id": "g", "preset": "greaves", "style": "sabaton"})
        boots = expand({"id": "g", "preset": "greaves", "style": "sabaton", "cuff": True, "sole": True, "cuff_color": "#2a4a90"})
        self.assertEqual(plain, expand({"id": "g", "preset": "greaves", "style": "sabaton", "cuff": False, "sole": False}))
        ids = {b["id"] for b in boots} - {b["id"] for b in plain}
        self.assertEqual({"g_cuff_r", "g_sole_r"}, ids)
        self.assertNotIn("g_knee_r", {b["id"] for b in boots})  # boots have no knee plate
        cuff = [b for b in boots if b["id"] == "g_cuff_r"][0]
        self.assertEqual("#2a4a90", cuff["color"])
        sole = [b for b in boots if b["id"] == "g_sole_r"][0]
        boot = [b for b in boots if b["id"] == "g_boot_r"][0]
        self.assertLess(sole["size"][1], 0.06)
        self.assertGreater(sole["min"][2], boot["min"][2] - 0.1)
        for style in ("plate", "spiked"):  # the cuff also works on shin armor and keeps the knee plate; the sole is a boot part
            armor = expand({"id": "g", "preset": "greaves", "style": style, "cuff": True, "sole": True})
            self.assertIn("g_cuff_r", {b["id"] for b in armor})
            self.assertIn("g_knee_r", {b["id"] for b in armor})
            self.assertNotIn("g_sole_r", {b["id"] for b in armor})

    def test_boots_clear_the_body(self):
        for p in ClearanceTest.prop_sets():
            ClearanceTest().assert_clear({"id": "g", "preset": "greaves", "style": "sabaton", "cuff": True, "sole": True}, p, eps=0.03)


class PouchTest(unittest.TestCase):
    def test_sits_on_the_chosen_side_and_stays_off_the_body(self):
        for side, sign in (("right", 1.0), ("left", -1.0)):
            boxes = expand({"id": "p", "preset": "pouch", "side": side})
            for b in explicit(boxes):
                if b["id"] != "p_belt":
                    self.assertGreater(sign * (b["min"][0] + b["size"][0] / 2.0), 0.0, "%s %s" % (side, b["id"]))
        for p in ClearanceTest.prop_sets():
            ClearanceTest().assert_clear({"id": "p", "preset": "pouch", "belt": False}, p, eps=0.005)

    def test_vials_count_and_colours_cycle(self):
        for n in range(0, 5):
            boxes = expand({"id": "p", "preset": "pouch", "vials": n, "colors": ["#111111", "#222222"]})
            vials = [b for b in boxes if b["id"].startswith("p_vial")]
            self.assertEqual(n, len(vials))
            self.assertEqual(n, len([b for b in boxes if b["id"].startswith("p_cork")]))
            self.assertEqual(["#111111", "#222222", "#111111", "#222222"][:n], [b["color"] for b in vials])
        self.assertEqual(["#e04ad8", "#58e07a", "#4ad8f0"],
                         [b["color"] for b in expand({"id": "p", "preset": "pouch"}) if b["id"].startswith("p_vial")])

    def test_vials_stand_on_top_of_the_pouch(self):
        boxes = expand({"id": "p", "preset": "pouch", "vials": 4})
        body = [b for b in boxes if b["id"] == "p_body"][0]
        for b in boxes:
            if b["id"].startswith("p_vial"):
                self.assertGreaterEqual(b["min"][1] + b["size"][1], body["min"][1] + body["size"][1] + 0.05)

    def test_belt_is_optional_and_wraps_the_waist(self):
        with_belt = expand({"id": "p", "preset": "pouch"})
        without = expand({"id": "p", "preset": "pouch", "belt": False})
        belt = [b for b in with_belt if b["id"] == "p_belt"]
        self.assertEqual(1, len(belt))
        self.assertFalse([b for b in without if b["id"] == "p_belt"])
        body = presets.body_from(props())
        self.assertGreater(belt[0]["size"][0], 2 * body.aw)
        self.assertLessEqual(belt[0]["size"][1], 0.06)


class NewPresetValidationTest(unittest.TestCase):
    def codes(self, att):
        return [(e.path, e.code) for e in presets.validate_attachment(att)]

    def test_new_parameters_are_validated(self):
        self.assertEqual([("/tip", "bad_color")], self.codes({"preset": "wings", "tip": "violet"}))
        self.assertEqual([], self.codes({"preset": "wings", "style": "seraph", "tip": "#8a75e7"}))
        self.assertEqual([("/ribbon_loops", "bad_type")], self.codes({"preset": "hairdo", "ribbon_loops": "yes"}))
        self.assertEqual([("/style", "unknown_value")], self.codes({"preset": "hairdo", "style": "afro"}))
        self.assertEqual([("/length", "out_of_range")], self.codes({"preset": "ribbons", "length": 2.5}))
        self.assertEqual([("/length", "out_of_range")], self.codes({"preset": "ribbons", "length": 0.4}))
        self.assertEqual([("/style", "unknown_value")], self.codes({"preset": "ribbons", "style": "chest"}))
        self.assertEqual([("/slit", "bad_type")], self.codes({"preset": "skirt", "slit": 1}))
        self.assertEqual([("/hem", "bad_type")], self.codes({"preset": "skirt", "hem": "yes"}))
        self.assertEqual([("/vials", "out_of_range")], self.codes({"preset": "pouch", "vials": 5}))
        self.assertEqual([("/vials", "bad_type")], self.codes({"preset": "pouch", "vials": 2.5}))
        self.assertEqual([("/side", "unknown_value")], self.codes({"preset": "pouch", "side": "back"}))

    def test_colors_list_is_validated(self):
        self.assertEqual([], self.codes({"preset": "pouch", "colors": ["#112233", "primary"]}))
        self.assertEqual([("/colors", "bad_type")], self.codes({"preset": "pouch", "colors": "#112233"}))
        self.assertEqual([("/colors", "out_of_range")], self.codes({"preset": "pouch", "colors": []}))
        self.assertEqual([("/colors", "out_of_range")], self.codes({"preset": "pouch", "colors": ["#111111"] * 5}))
        self.assertEqual([("/colors/1", "bad_color")], self.codes({"preset": "pouch", "colors": ["#111111", "green"]}))
        self.assertEqual([("/colors/0", "bad_type")], self.codes({"preset": "pouch", "colors": [3]}))

    def test_new_presets_are_deterministic(self):
        for name, att, key, val in all_variants():
            if name in ("hairdo", "ribbons", "pouch", "skirt", "wings"):
                self.assertEqual(expand(att), expand(att), "%s %s=%s" % (name, key, val))


class NewPresetBoundsTest(unittest.TestCase):
    def test_new_presets_stay_inside_the_model_limits_at_the_scale_extremes(self):
        for att in ({"preset": "hairdo", "ribbon_loops": True, "pins": True}, {"preset": "hairdo", "style": "ponytail"},
                    {"preset": "hairdo", "style": "braid"}, {"preset": "hairdo", "style": "twin_buns"},
                    {"preset": "ribbons", "style": "all", "length": 2.0}, {"preset": "pouch", "vials": 4},
                    {"preset": "pouch", "side": "left"}, {"preset": "skirt", "slit": True, "hem": True},
                    {"preset": "skirt", "style": "kilt", "slit": True, "hem": True}, {"preset": "skirt", "style": "tabard", "slit": True, "hem": True},
                    {"preset": "wings", "style": "seraph", "spread": 15.0}):
            for scale in (0.5, 1.0, 2.5):
                a = dict(att, id="x", scale=scale)
                boxes, _ = shape.resolve({"attachments": [a]})  # raises on out_of_bounds / too_many_boxes
                self.assertLessEqual(len(boxes) - 14, presets.MAX_BOXES, "%s scale %s" % (att, scale))

    def test_proportions_move_the_new_head_and_hip_pieces(self):
        wide = props(shoulders=1.5, torso_width=1.5)
        for att in ({"preset": "pouch"}, {"preset": "ribbons", "style": "wrist"}):
            a = extent(expand(dict(att, id="x"), props()))
            b = extent(expand(dict(att, id="x"), wide))
            self.assertGreater(b[0][1], a[0][1] + 0.05, att)  # they follow the wider body outwards


class AngelShapeTest(unittest.TestCase):
    SHAPE = {
        "silhouette": "caster", "remove": ["hood", "halo", "orbs", "cape"],
        "proportions": {"head": 1.0, "shoulders": 0.9, "arm_thickness": 0.75, "leg_thickness": 0.75},
        "attachments": [
            {"id": "wings", "preset": "wings", "style": "seraph", "spread": 15.0},
            {"id": "skirt", "preset": "skirt", "style": "robe", "scale": 0.65, "slit": True, "hem": True},
            {"id": "hairdo", "preset": "hairdo", "style": "bun", "ribbon_loops": True, "pins": True},
            {"id": "ribbons", "preset": "ribbons", "style": "all"},
            {"id": "pouch", "preset": "pouch"},
            {"id": "pauldrons", "preset": "pauldrons", "style": "layered"},
        ],
        "texture": {"density": 48},
    }

    def test_whole_angel_resolves_within_limits_and_builds_an_atlas(self):
        self.assertEqual([], shape.validate_shape(self.SHAPE))
        boxes, _ = shape.resolve(self.SHAPE)
        self.assertLessEqual(len(boxes), shape.MAX_BOXES)
        model = shape.build_model({"shape": self.SHAPE, "mod_id": "m", "id": "angel"})
        self.assertLessEqual(max(model.atlas), 512)



if __name__ == "__main__":
    unittest.main()
