import _path  # noqa
import copy
import json
import unittest

from bossforge_core import catalog, spec
from _path import EXAMPLE


def load():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def codes(s):
    return {e.code for e in spec.validate(s)}


class NormalizeTests(unittest.TestCase):
    def test_king_arthur_normalizes(self):
        n, errs, warns = spec.check(load())
        self.assertEqual(errs, [])
        tier = spec.data("tiers")["major"]
        for k, v in tier["stats"].items():
            self.assertEqual(n["stats"][k], v, k)
        self.assertEqual(len(n["phases"]), 3)
        self.assertEqual(n["derived"]["template"], "biped")
        mh = spec.data("templates")["biped"]["model_height"]
        self.assertEqual(n["derived"]["scale"], round(3.0 / mh, 3))
        self.assertEqual(n["size"]["depth"], n["size"]["width"])

    def test_stat_override_wins_and_float_types(self):
        s = load()
        s["stats"] = {"stun_armor": 20, "max_strikes": 5}
        n = spec.normalize(s)
        self.assertEqual(n["stats"]["stun_armor"], 20.0)
        self.assertIsInstance(n["stats"]["stun_armor"], float)
        self.assertIsInstance(n["stats"]["max_strikes"], int)

    def test_auto_template_prefers_golem_for_wide_weaponless(self):
        s = load()
        s.pop("weapons"); s.pop("main_weapon"); s["moves"] = [{"series": "combo"}]
        s["size"] = {"height": 2.6, "width": 1.6}
        s["moves"] = []
        self.assertEqual(spec.normalize(s)["derived"]["template"], "iron_golem")
        s["weapons"] = load()["weapons"]; s["main_weapon"] = "excalibur"
        self.assertEqual(spec.normalize(s)["derived"]["template"], "biped")


class ValidationTests(unittest.TestCase):
    def test_example_is_valid(self):
        self.assertEqual(spec.validate(load()), [])

    def test_error_codes(self):
        cases = []
        s = load(); s["size"]["height"] = 99; cases.append(("out_of_range", s))
        s = load(); s["size"]["depth"] = 2.0; cases.append(("depth_must_equal_width", s))
        s = load(); s["id"] = "Bad Id"; cases.append(("bad_id", s))
        s = load(); s["mod_id"] = "9x"; cases.append(("bad_mod_id", s))
        s = load(); s["stats"] = {"health": 1}; cases.append(("out_of_range", s))
        s = load(); s["phases"][2]["health_below"] = 0.7; cases.append(("phases_not_decreasing", s))
        s = load(); s["appearance"]["archetype"] = "pirate"; cases.append(("unknown_archetype", s))
        s = load(); s["appearance"]["features"].append({"type": "wings"}); cases.append(("unknown_feature", s))
        s = load(); s["moves"] = [{"series": "bogus"}]; cases.append(("unknown_series", s))
        s = load(); s["moves"] = [{"series": "custom", "animations": [{"animation": "epicfight:nope/x"}]}]; cases.append(("unknown_animation", s))
        s = load(); s["weapons"].append(copy.deepcopy(s["weapons"][0])); cases.append(("duplicate_weapon_id", s))
        s = load(); s["body"] = {"template": "iron_golem"}; cases.append(("weapon_not_wieldable", s))
        for code, s in cases:
            self.assertIn(code, codes(s), code)

    def test_size_error_points_at_height(self):
        s = load(); s["size"]["height"] = 99
        self.assertIn("/size/height", [e.path for e in spec.validate(s)])

    def test_scale_out_of_range(self):
        s = load(); s["size"] = {"height": 8.0, "width": 1.0}
        s["body"] = {"template": "biped"}
        s["size"]["height"] = 1.0
        self.assertNotIn("scale_out_of_range", codes(s))  # 1.0/2.0 = 0.5 is the allowed floor

    def test_recipe_validation(self):
        s = load(); s["weapons"][0]["obtain"] = {"recipe": {"pattern": ["EEEE"], "key": {"E": "minecraft:stick"}}}
        self.assertIn("recipe_invalid", codes(s))
        s = load(); s["weapons"][0]["obtain"] = {"recipe": {"pattern": ["EX"], "key": {"E": "minecraft:stick"}}}
        self.assertIn("recipe_invalid", codes(s))


class DataTests(unittest.TestCase):
    def test_tier_rows_match_plan(self):
        t = spec.data("tiers")
        expect = {
            "health": (400, 200, 80, 600), "armor": (12, 8, 4, 14), "armor_toughness": (6, 3, 0, 8),
            "attack_damage": (10, 7, 4, 14), "movement_speed": (0.26, 0.25, 0.25, 0.28),
            "knockback_resistance": (0.8, 0.5, 0.2, 1.0), "follow_range": (48, 40, 28, 56),
            "xp_reward": (500, 200, 50, 800), "impact": (3.0, 2.0, 1.0, 3.5), "armor_negation": (15.0, 8.0, 0.0, 20.0),
            "max_strikes": (3, 2, 1, 3), "stun_armor": (15.0, 7.5, 3.0, 22.5), "chasing_speed": (1.15, 1.1, 1.0, 1.2),
        }
        for k, row in expect.items():
            for tier, v in zip(("major", "quest", "mini", "hidden"), row):
                self.assertEqual(t[tier]["stats"][k], v, "%s.%s" % (tier, k))

    def test_every_preset_animation_in_catalog(self):
        p = spec.data("ef-presets")
        keys = catalog.keys()
        series = [s for e in p["biped"].values() for s in e["series"]] + p["iron_golem"]["series"]
        for s in series:
            for b in s["behaviors"]:
                self.assertIn(b["animation"], keys)


if __name__ == "__main__":
    unittest.main()
