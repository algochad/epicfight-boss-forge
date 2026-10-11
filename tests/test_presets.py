import _path  # noqa
import copy
import math
import unittest

from bossforge_core import presets, shape, spec
from bossforge_core.spec import BossForgeError

REQUIRED = ("crown horns helm hood mane pauldrons cape skirt tail wings halo orbs extra_arms claws tusks fangs "
            "ribcage core spine_spikes shield gauntlets greaves belt banner jaw").split()
STYLES = {
    "crown": ("band", "spiked", "tall", "broken"),
    "horns": ("curved", "straight", "ram", "antlers", "demon", "bull"),
    "helm": ("bucket", "visor", "horned", "plumed", "great"),
    "pauldrons": ("round", "spiked", "layered", "skull", "asymmetric"),
    "cape": ("short", "long", "tattered"),
    "tail": ("lizard", "spiked", "whip", "club"),
    "wings": ("bat", "feathered", "angel", "skeletal", "flame"),
    "shield": ("round", "kite", "tower"),
}
SILHOUETTES = ("plain knight warlord brute colossus caster beast fiend skeletal_king wraith insectoid").split()
# variants that are asymmetric about x = 0 on purpose
ASYMMETRIC = {("pauldrons", "style", "asymmetric"), ("shield", None, None), ("crown", "style", "broken"),
              ("belt", "style", "sash"), ("banner", "style", "pennant"), ("cape", "style", "tattered")}
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
        self.assertEqual(set(REQUIRED), set(presets.list_presets()))
        for name, styles in STYLES.items():
            self.assertEqual(list(styles), presets.PRESETS[name]["params"][
                "pose" if name == "extra_arms" else "style"]["allowed"], name)
        wings = presets.PRESETS["wings"]["params"]["spread"]
        self.assertEqual((10.0, 80.0), tuple(wings["range"]))

    def test_every_preset_documents_the_common_params(self):
        for name, p in presets.PRESETS.items():
            self.assertTrue(p["description"], name)
            for key in presets.COMMON_PARAMS:
                self.assertIn(key, p["params"], "%s lacks %s" % (name, key))
            self.assertEqual((0.5, 2.5), tuple(p["params"]["scale"]["range"]))

    def test_documented_defaults_form_a_valid_attachment(self):
        for name, p in presets.PRESETS.items():
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
            if (name, key, val) in ASYMMETRIC or (name, None, None) in ASYMMETRIC:
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
            self.assertLessEqual(len(boxes), 160, name)
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


if __name__ == "__main__":
    unittest.main()
