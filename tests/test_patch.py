import _path  # noqa
import copy
import json
import unittest

from bossforge_core import catalog, moves, patch, spec
from bossforge_core.qa import validate_mobpatch, validate_mobpatch_text
from bossforge_core.spec import BossForgeError, make_context
from _path import EXAMPLE, SHIPPED_EXAMPLE


def arthur():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def golem_spec():
    return {
        "schema": "epicfight-boss-forge/1", "id": "stone_warden", "mod_id": "roundtable", "display_name": "Stone Warden",
        "tier": "mini", "size": {"height": 2.6, "width": 1.6}, "appearance": {"archetype": "golem"},
        "lore": {"summary": "s", "backstory": "b", "description": "d"},
    }


def norm(raw):
    n, errs, _warns = spec.check(raw)
    assert errs == [], [e.to_dict() for e in errs]
    return n


def with_moves(series_moves):
    raw = arthur()
    raw["moves"] = series_moves
    return norm(raw)


def health_of(series):
    return [(c["comparator"], c["health"]) for c in series["behaviors"][0]["conditions"] if c["predicate"] == "health"]


def generated(n):
    ctx = make_context("/nonexistent", n["mod_id"], "com.example." + n["mod_id"], "/nonexistent-cache", offline=True)
    files, lang = patch.generate(n, ctx)
    assert len(files) == 1 and lang == {}
    return files[0]


class ValidOutputTests(unittest.TestCase):
    def test_king_arthur_validates_and_has_expected_shape(self):
        n = norm(arthur())
        f = generated(n)
        self.assertEqual(f.relpath, "src/main/resources/data/roundtable/epicfight_mobpatch/king_arthur.json")
        self.assertEqual(f.kind, "json")
        obj = json.loads(f.data.decode("utf-8"))
        self.assertEqual(validate_mobpatch(obj), [])
        self.assertEqual(list(obj), ["model", "armature", "renderer", "isHumanoid", "faction", "attributes",
                                     "default_livingmotions", "humanoid_weapon_motions", "stun_animations", "combat_behavior"])
        self.assertEqual(obj["renderer"], "player")
        self.assertIs(obj["isHumanoid"], True)
        self.assertEqual(list(obj["attributes"]), ["impact", "armor_negation", "max_strikes", "stun_armor", "attack_damage", "chasing_speed", "scale"])
        self.assertEqual(obj["attributes"]["scale"], n["derived"]["scale"])

    def test_greatsword_variant_validates(self):
        raw = arthur()
        raw["weapons"][0]["category"] = "greatsword"
        n = norm(raw)
        obj = patch.build(n)
        self.assertEqual(validate_mobpatch(obj), [])
        first = obj["combat_behavior"][0]
        self.assertEqual(first["weapon_categories"], ["greatsword"])
        self.assertEqual(first["style"], "two_hand")
        anims = [b["animation"] for s in first["behavior_series"] for b in s["behaviors"]]
        self.assertIn("epicfight:biped/combat/greatsword_auto1", anims)

    def test_every_weapon_category_validates(self):
        for cat in spec.data("weapon-categories"):
            raw = arthur()
            raw["weapons"][0]["category"] = cat
            raw.pop("moves", None)
            n = norm(raw)
            self.assertEqual(validate_mobpatch(patch.build(n)), [], cat)

    def test_unarmed_biped_has_single_fist_entry(self):
        raw = arthur()
        raw.pop("weapons"); raw.pop("main_weapon"); raw.pop("abilities")
        raw["size"] = {"height": 2.4, "width": 0.7}
        n = norm(raw)
        obj = patch.build(n)
        self.assertEqual(n["derived"]["template"], "biped")
        self.assertEqual([(e["weapon_categories"], e["style"]) for e in obj["combat_behavior"]], [(["fist"], "common")])
        self.assertEqual(obj["humanoid_weapon_motions"], [])
        self.assertEqual(validate_mobpatch(obj), [])

    def test_output_is_deterministic(self):
        n = norm(arthur())
        self.assertEqual(generated(n).data, generated(copy.deepcopy(n)).data)

    def test_stun_animations_follow_the_tier(self):
        major = patch.build(norm(arthur()))
        self.assertEqual(list(major["stun_animations"]), ["long", "knockdown"])
        raw = arthur()
        raw["tier"] = "mini"
        raw.pop("phases"); raw.pop("abilities"); raw.pop("encounter")
        mini = patch.build(spec.normalize(raw))
        self.assertEqual(list(mini["stun_animations"]), ["short", "long", "knockdown", "fall"])

    def test_weapon_motions_only_use_existing_catalog_keys(self):
        raw = arthur()
        raw["weapons"][0]["category"] = "greatsword"
        gs = patch.build(norm(raw))["humanoid_weapon_motions"]
        self.assertEqual([e["style"] for e in gs], ["two_hand", "common"])
        self.assertEqual(set(gs[0]["livingmotions"]), {"idle", "walk", "chase"})
        raw["weapons"][0]["category"] = "tachi"
        tachi = patch.build(norm(raw))["humanoid_weapon_motions"]
        self.assertEqual(set(tachi[0]["livingmotions"]), {"idle"})
        raw["weapons"][0]["category"] = "sword"
        self.assertEqual(patch.build(norm(raw))["humanoid_weapon_motions"], [])


class SerializationTests(unittest.TestCase):
    def test_float_and_int_typing_in_text(self):
        text = generated(norm(arthur())).data.decode("utf-8")
        self.assertIn('"stun_armor": 15.0', text)
        self.assertIn('"impact": 3.0', text)
        self.assertIn('"armor_negation": 15.0', text)
        self.assertIn('"max_strikes": 3', text)
        self.assertNotIn('"max_strikes": 3.0', text)
        self.assertIn('"cooldown": 60', text)
        self.assertNotIn('"cooldown": 60.0', text)
        self.assertIn('"weight": 100.0', text)

    def test_integer_stat_overrides_still_serialize_as_floats(self):
        raw = arthur()
        raw["stats"] = {"stun_armor": 20, "impact": 4, "attack_damage": 12, "chasing_speed": 1, "max_strikes": 5}
        text = generated(norm(raw)).data.decode("utf-8")
        for needle in ('"stun_armor": 20.0', '"impact": 4.0', '"attack_damage": 12.0', '"chasing_speed": 1.0', '"max_strikes": 5'):
            self.assertIn(needle, text)

    def test_weapon_categories_are_always_lists(self):
        obj = patch.build(norm(arthur()))
        for e in obj["combat_behavior"]:
            self.assertIsInstance(e["weapon_categories"], list)
        for e in obj["humanoid_weapon_motions"]:
            self.assertIsInstance(e["weapon_categories"], list)


class PhaseWindowTests(unittest.TestCase):
    def test_all_phases_have_no_health_predicate(self):
        series = moves.expand(with_moves([{"series": "combo", "phases": [1, 2, 3]}]))
        self.assertEqual(len(series), 1)
        self.assertEqual(health_of(series[0]), [])

    def test_open_ended_window_only_bounds_above(self):
        series = moves.expand(with_moves([{"series": "heavy", "phases": [2, 3]}]))
        self.assertEqual(len(series), 1)
        self.assertEqual(health_of(series[0]), [("less_ratio", 0.66)])

    def test_middle_window_bounds_both_sides(self):
        series = moves.expand(with_moves([{"series": "heavy", "phases": [2]}]))
        self.assertEqual(health_of(series[0]), [("less_ratio", 0.66), ("greater_ratio", 0.33)])

    def test_first_phase_only_bounds_below(self):
        series = moves.expand(with_moves([{"series": "combo", "phases": [1]}]))
        self.assertEqual(health_of(series[0]), [("greater_ratio", 0.66)])

    def test_gap_in_phases_yields_two_series(self):
        series = moves.expand(with_moves([{"series": "combo", "phases": [1, 3]}]))
        self.assertEqual([health_of(s) for s in series], [[("greater_ratio", 0.66)], [("less_ratio", 0.33)]])

    def test_health_predicates_only_on_first_behavior_and_come_first(self):
        series = moves.expand(with_moves([{"series": "combo", "phases": [2]}]))[0]
        self.assertEqual(len(series["behaviors"]), 2)
        self.assertEqual([c["predicate"] for c in series["behaviors"][1]["conditions"]], ["within_eye_height", "within_distance"])
        heavy = moves.expand(with_moves([{"series": "heavy", "phases": [2]}]))[0]
        names = [c["predicate"] for c in heavy["behaviors"][0]["conditions"]]
        self.assertEqual(names, ["health", "health", "random_chance", "within_eye_height", "within_distance"])

    def test_king_arthur_default_heavy_is_gated_to_phase_two_onwards(self):
        obj = patch.build(norm(arthur()))
        series = obj["combat_behavior"][0]["behavior_series"]
        self.assertEqual(len(series), 2)  # combo (all phases) + heavy (phases 2-3)
        self.assertEqual(series[0]["behaviors"][0]["conditions"][0], {"predicate": "within_eye_height"})
        self.assertEqual(series[1]["behaviors"][0]["conditions"][0],
                         {"predicate": "health", "health": 0.66, "comparator": "less_ratio"})

    def test_single_phase_boss_has_no_health_predicates(self):
        raw = arthur()
        raw.pop("phases"); raw.pop("abilities"); raw["tier"] = "mini"; raw.pop("encounter")
        n = spec.normalize(raw)
        self.assertEqual(len(n["phases"]), 1)
        for s in moves.expand(n):
            self.assertEqual(health_of(s), [])

    def test_out_of_range_phase_is_rejected(self):
        n = with_moves([{"series": "combo", "phases": [1]}])
        n["moves"] = [{"series": "combo", "phases": [4]}]
        with self.assertRaises(BossForgeError) as cm:
            moves.expand(n)
        self.assertEqual(cm.exception.err.code, "bad_phase")


class DistanceAndOverrideTests(unittest.TestCase):
    def test_max_distance_scales_with_model_scale_and_min_stays(self):
        n = norm(arthur())
        scale = n["derived"]["scale"]
        d = moves.expand(n)[0]["behaviors"][0]["conditions"][-1]
        self.assertEqual(d["predicate"], "within_distance")
        self.assertEqual(d["max"], round(2.5 * scale, 2))
        self.assertEqual(d["min"], 0.0)

    def test_range_scale_multiplies(self):
        n = with_moves([{"series": "combo", "range_scale": 2.0}])
        d = moves.expand(n)[0]["behaviors"][0]["conditions"][-1]
        self.assertEqual(d["max"], round(2.5 * n["derived"]["scale"] * 2.0, 2))

    def test_chance_override_replaces_preset_chance_on_first_behavior(self):
        n = with_moves([{"series": "heavy", "chance": 0.9}])
        conds = moves.expand(n)[0]["behaviors"][0]["conditions"]
        chances = [c["chance"] for c in conds if c["predicate"] == "random_chance"]
        self.assertEqual(chances, [0.9])

    def test_chance_override_adds_predicate_when_preset_has_none(self):
        n = with_moves([{"series": "combo", "chance": 0.5}])
        conds = moves.expand(n)[0]["behaviors"][0]["conditions"]
        self.assertEqual([c["predicate"] for c in conds], ["random_chance", "within_eye_height", "within_distance"])

    def test_series_overrides(self):
        n = with_moves([{"series": "heavy", "weight": 20, "cooldown": 200, "interruptible": True, "loop": True}])
        s = moves.expand(n)[0]
        self.assertEqual((s["weight"], s["cooldown"], s["canBeInterrupted"], s["looping"]), (20.0, 200, True, True))
        self.assertIsInstance(s["weight"], float)
        self.assertIsInstance(s["cooldown"], int)

    def test_custom_move_builds_behaviors_from_animations(self):
        n = with_moves([{"series": "custom", "phases": [3], "animations": [
            {"animation": "epicfight:biped/combat/mob_tachi_special", "max_range": 4.0},
            {"animation": "epicfight:biped/combat/mob_longsword1", "min_range": 1.0, "max_range": 2.0, "chance": 0.5}]}])
        s = moves.expand(n)[0]
        self.assertEqual(health_of(s), [("less_ratio", 0.33)])
        scale = n["derived"]["scale"]
        b0, b1 = s["behaviors"]
        self.assertEqual(b0["animation"], "epicfight:biped/combat/mob_tachi_special")
        self.assertEqual(b0["conditions"][-1], {"predicate": "within_distance", "min": 0.0, "max": round(4.0 * scale, 2)})
        self.assertEqual(b1["conditions"][0], {"predicate": "random_chance", "chance": 0.5})
        self.assertEqual(b1["conditions"][-1]["min"], 1.0)

    def test_custom_move_with_unknown_animation_raises(self):
        raw = arthur()
        raw["moves"] = [{"series": "custom", "animations": [{"animation": "epicfight:biped/combat/does_not_exist"}]}]
        n = spec.normalize(raw)  # bypass spec.check, which rejects this earlier
        with self.assertRaises(BossForgeError) as cm:
            moves.expand(n)
        self.assertEqual(cm.exception.err.code, "unknown_animation")
        self.assertEqual(cm.exception.err.path, "/moves/0/animations/0/animation")

    def test_unknown_series_raises(self):
        n = with_moves([{"series": "combo"}])
        n["moves"] = [{"series": "smash"}]  # golem-only series for a longsword boss
        with self.assertRaises(BossForgeError) as cm:
            moves.expand(n)
        self.assertEqual(cm.exception.err.code, "unknown_series")


class GolemTests(unittest.TestCase):
    def test_golem_patch_is_flat_and_non_humanoid(self):
        n = norm(golem_spec())
        self.assertEqual(n["derived"]["template"], "iron_golem")
        obj = patch.build(n)
        self.assertEqual(validate_mobpatch(obj), [])
        self.assertIs(obj["isHumanoid"], False)
        self.assertEqual(obj["renderer"], "minecraft:iron_golem")
        self.assertEqual(obj["stun_animations"], {})
        self.assertNotIn("humanoid_weapon_motions", obj)
        self.assertEqual(list(obj), ["model", "armature", "renderer", "isHumanoid", "faction", "attributes",
                                     "default_livingmotions", "stun_animations", "combat_behavior"])
        for s in obj["combat_behavior"]:
            self.assertIn("behaviors", s)
            self.assertNotIn("behavior_series", s)
        text = generated(n).data.decode("utf-8")
        self.assertIn('"stun_animations": {}', text)

    def test_golem_eye_height_only_behavior_has_no_distance(self):
        obj = patch.build(norm(golem_spec()))
        combo = [s for s in obj["combat_behavior"] if len(s["behaviors"]) == 3][0]
        self.assertEqual(combo["behaviors"][1]["conditions"], [{"predicate": "within_eye_height"}])
        self.assertEqual(combo["behaviors"][1]["animation"], "epicfight:iron_golem/attack4")

    def test_golem_default_phase_windows_with_three_phases(self):
        raw = golem_spec()
        raw["tier"] = "major"
        raw["size"] = {"height": 3.0, "width": 2.0}
        n = norm(raw)
        self.assertEqual(n["derived"]["template"], "iron_golem")
        series = moves.expand(n)
        smash = series[0]
        combos = series[1:]
        self.assertEqual(health_of(smash), [])
        self.assertEqual([health_of(s) for s in combos], [[("less_ratio", 0.66)]])


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.good = patch.build(norm(arthur()))

    def codes(self, obj):
        return {(e.code, e.path) for e in validate_mobpatch(obj)}

    def test_unknown_animation_is_an_error_with_pointer(self):
        obj = copy.deepcopy(self.good)
        obj["combat_behavior"][0]["behavior_series"][0]["behaviors"][1]["animation"] = "epicfight:biped/combat/nope"
        self.assertIn(("mobpatch_unknown_animation", "/combat_behavior/0/behavior_series/0/behaviors/1/animation"), self.codes(obj))

    def test_animation_without_namespace_is_rejected(self):
        obj = copy.deepcopy(self.good)
        obj["default_livingmotions"]["idle"] = "biped/living/idle"
        self.assertIn(("mobpatch_unknown_animation", "/default_livingmotions/idle"), self.codes(obj))

    def test_integer_typed_float_attribute_is_rejected(self):
        obj = copy.deepcopy(self.good)
        obj["attributes"]["stun_armor"] = 15
        obj["attributes"]["max_strikes"] = 3.0
        c = self.codes(obj)
        self.assertIn(("mobpatch_attribute_type", "/attributes/stun_armor"), c)
        self.assertIn(("mobpatch_attribute_type", "/attributes/max_strikes"), c)

    def test_missing_required_keys(self):
        for key in ("model", "armature", "faction", "renderer", "default_livingmotions", "combat_behavior"):
            obj = copy.deepcopy(self.good)
            del obj[key]
            self.assertIn(("mobpatch_missing_key", "/" + key), self.codes(obj), key)

    def test_unknown_faction_motion_and_stun_type(self):
        obj = copy.deepcopy(self.good)
        obj["faction"] = "goblins"
        obj["default_livingmotions"]["levitate"] = "epicfight:biped/living/idle"
        obj["stun_animations"]["stagger"] = "epicfight:biped/combat/hit_long"
        c = self.codes(obj)
        self.assertIn(("mobpatch_unknown_faction", "/faction"), c)
        self.assertIn(("mobpatch_unknown_livingmotion", "/default_livingmotions/levitate"), c)
        self.assertIn(("mobpatch_unknown_stun_type", "/stun_animations/stagger"), c)

    def test_epicfight_custom_renderer_needs_the_humanoid_key(self):
        obj = copy.deepcopy(self.good)
        obj["renderer"] = "epicfight:custom"
        obj["model"] = "roundtable:entity/test"
        self.assertIn(("mobpatch_bad_renderer", "/humanoid"), self.codes(obj))
        obj["humanoid"] = True
        self.assertNotIn("mobpatch_bad_renderer", {c for c, _p in self.codes(obj)})

    def test_a_custom_model_with_a_preset_renderer_is_rejected_because_ef_ignores_it(self):
        obj = copy.deepcopy(self.good)
        obj["model"] = "roundtable:entity/test"
        self.assertIn(("mobpatch_model_ignored", "/renderer"), self.codes(obj))

    def test_custom_shape_patch_uses_the_custom_renderer(self):
        raw = json.loads(SHIPPED_EXAMPLE.read_text(encoding="utf-8"))
        n = norm(raw)
        obj = patch.build(n)
        self.assertEqual(obj["renderer"], "epicfight:custom")
        self.assertIs(obj["humanoid"], True)
        self.assertIs(obj["isHumanoid"], True)
        self.assertEqual(obj["model"], n["derived"]["model_id"])
        self.assertEqual(obj["armature"], "epicfight:entity/biped")
        self.assertEqual(list(obj)[:6], ["model", "armature", "renderer", "isHumanoid", "humanoid", "faction"])
        self.assertEqual(validate_mobpatch(obj), [])

    def test_humanoid_categories_must_be_a_list_of_known_lowercase_names(self):
        obj = copy.deepcopy(self.good)
        obj["combat_behavior"][0]["weapon_categories"] = "longsword"
        obj["combat_behavior"][1]["weapon_categories"] = ["Longsword"]
        c = self.codes(obj)
        self.assertIn(("mobpatch_bad_type", "/combat_behavior/0/weapon_categories"), c)
        self.assertIn(("mobpatch_unknown_category", "/combat_behavior/1/weapon_categories/0"), c)

    def test_unknown_style(self):
        obj = copy.deepcopy(self.good)
        obj["combat_behavior"][0]["style"] = "three_hand"
        self.assertIn(("mobpatch_unknown_style", "/combat_behavior/0/style"), self.codes(obj))

    def test_shape_must_match_is_humanoid(self):
        humanoid_flat = copy.deepcopy(self.good)
        humanoid_flat["combat_behavior"] = humanoid_flat["combat_behavior"][0]["behavior_series"]
        self.assertIn(("mobpatch_combat_shape", "/combat_behavior/0/weapon_categories"), self.codes(humanoid_flat))
        golem = patch.build(norm(golem_spec()))
        golem["combat_behavior"] = [{"weapon_categories": ["fist"], "style": "common", "behavior_series": golem["combat_behavior"]}]
        c = self.codes(golem)
        self.assertIn(("mobpatch_combat_shape", "/combat_behavior/0"), c)

    def test_predicate_rules(self):
        obj = copy.deepcopy(self.good)
        conds = obj["combat_behavior"][0]["behavior_series"][0]["behaviors"][0]["conditions"]
        conds[:] = [
            {"predicate": "teleport"},
            {"predicate": "health", "health": 0.5, "comparator": "below"},
            {"predicate": "health", "comparator": "less_ratio"},
            {"predicate": "random_chance"},
            {"predicate": "random_chance", "chance": 1.5},
            {"predicate": "within_distance", "min": 0.0},
            {"predicate": "within_angle", "min": 10.0, "max": 5.0},
            {"predicate": "within_angle_horizontal", "min": 0.0, "max": 90.0},
            {"predicate": "within_eye_height"},
        ]
        base = "/combat_behavior/0/behavior_series/0/behaviors/0/conditions/"
        c = self.codes(obj)
        self.assertIn(("mobpatch_unknown_predicate", base + "0/predicate"), c)
        self.assertIn(("mobpatch_bad_condition", base + "1/comparator"), c)
        self.assertIn(("mobpatch_bad_type", base + "2/health"), c)
        self.assertIn(("mobpatch_bad_type", base + "3/chance"), c)
        self.assertIn(("mobpatch_bad_condition", base + "4/chance"), c)
        self.assertIn(("mobpatch_bad_type", base + "5/max"), c)
        self.assertIn(("mobpatch_bad_condition", base + "6"), c)
        self.assertFalse(any(p.startswith(base + "7") or p.startswith(base + "8") for _code, p in c))

    def test_empty_behavior_list_is_an_error(self):
        obj = copy.deepcopy(self.good)
        obj["combat_behavior"][0]["behavior_series"] = []
        self.assertIn(("mobpatch_no_behavior", "/combat_behavior/0/behavior_series"), self.codes(obj))

    def test_text_wrapper_reports_bad_json(self):
        self.assertEqual([e.code for e in validate_mobpatch_text("{not json")], ["mobpatch_bad_json"])
        self.assertEqual(validate_mobpatch_text(json.dumps(self.good)), [])


class DataConsistencyTests(unittest.TestCase):
    def test_every_preset_animation_exists_in_catalog(self):
        presets = spec.data("ef-presets")
        keys = []
        for cat in presets["biped"].values():
            for s in cat["series"]:
                keys += [b["animation"] for b in s["behaviors"]]
        for s in presets["iron_golem"]["series"]:
            keys += [b["animation"] for b in s["behaviors"]]
        self.assertGreater(len(keys), 20)
        for k in keys:
            self.assertTrue(catalog.resolve("epicfight:" + k), k)

    def test_template_motions_and_stun_animations_exist_in_catalog(self):
        for name, t in spec.data("templates").items():
            for v in list(t["living_motions"].values()) + list(t["stun_animations"].values()):
                self.assertTrue(catalog.resolve(v), "%s: %s" % (name, v))


if __name__ == "__main__":
    unittest.main()
