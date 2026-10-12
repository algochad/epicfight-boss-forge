import _path  # noqa
import copy
import json
import math
import tempfile
import unittest
from pathlib import Path

from bossforge_core import holdpose, spec, sprites, weapon
from bossforge_core.pngio import decode_png, png_size
from bossforge_core.spec import BossForgeError, Err, GUARD_SHAPES, POMMEL_SHAPES

CATS = ("sword", "axe", "dagger", "longsword", "greatsword", "tachi", "spear")
LONG = ("longsword", "greatsword", "tachi", "spear")
EF_TYPES = {c: "epicfight:" + c for c in CATS}
AP = {"blade": "#cfe3ff", "guard": "#d4aa32", "grip": "#5a3a22", "accent": "#46a0ff", "gem": "#46a0ff", "glow": False, "motif": "fuller"}


def components(canvas):
    """8-connected components of fully opaque pixels (the translucent glow halo is excluded)."""
    w, h = canvas.w, canvas.h
    todo = {(x, y) for y in range(h) for x in range(w) if canvas.get(x, y)[3] == 255}
    sizes = []
    while todo:
        stack = [todo.pop()]
        n = 0
        while stack:
            x, y = stack.pop()
            n += 1
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    q = (x + dx, y + dy)
                    if q in todo:
                        todo.remove(q)
                        stack.append(q)
        sizes.append(n)
    return sizes


def opaque_rows(canvas):
    return [y for y in range(canvas.h) for x in range(canvas.w) if canvas.get(x, y)[3] == 255]


def weapon_spec(category="longsword", **extra):
    w = {"id": "test_blade", "mod_id": "testmod", "display_name": "Test Blade", "category": category}
    w.update(extra)
    return w


def standalone(category="longsword", essence_item=None, **extra):
    raw = weapon_spec(category, **extra)
    n = spec.normalize_weapon_spec(raw)
    with tempfile.TemporaryDirectory() as td:
        ctx = spec.make_context(td, "testmod", "com.bossforge.testmod", td)
        files, lang = weapon.generate_standalone(n, ctx, essence_item)
    return {f.relpath: f for f in files}, lang


def boss(tier="major", **over):
    raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
    raw["tier"] = tier
    raw.update(over)
    n = spec.normalize(raw)
    with tempfile.TemporaryDirectory() as td:
        ctx = spec.make_context(td, n["mod_id"], "com.bossforge." + n["mod_id"], td)
        files, lang = weapon.generate(n, ctx)
    return n, {f.relpath: f for f in files}, lang


def res(path):
    return "src/main/resources/" + path


def jload(files, path):
    return json.loads(files[res(path)].data.decode("utf-8"))


class SpriteTests(unittest.TestCase):
    def test_every_category_is_one_connected_diagonal_weapon(self):
        for cat in CATS:
            for res_px in (32, 16):
                for motif in ("plain", "fuller", "runes", "serrated"):
                    for glow in (False, True):
                        cv = sprites.render_weapon(cat, dict(AP, motif=motif, glow=glow), res_px, 77)
                        self.assertEqual((cv.w, cv.h), (res_px, res_px))
                        self.assertEqual(len(components(cv)), 1, "%s res=%d motif=%s glow=%s" % (cat, res_px, motif, glow))

    def test_weapons_point_up_right_and_fill_the_canvas(self):
        for cat in CATS:
            for res_px in (32, 16):
                cv = sprites.render_weapon(cat, AP, res_px, 1)
                pts = [(x, y) for y in range(res_px) for x in range(res_px) if cv.get(x, y)[3] == 255]
                ys = [p[1] for p in pts]
                self.assertGreaterEqual(max(ys) - min(ys), res_px * 0.6, "%s res=%d too short" % (cat, res_px))
                top = [p[0] for p in pts if p[1] == min(ys)]
                bottom = [p[0] for p in pts if p[1] == max(ys)]
                self.assertGreater(sum(top) / len(top), sum(bottom) / len(bottom), "%s tip must be right of the pommel" % cat)

    def test_glow_adds_translucent_halo_only_when_enabled(self):
        plain = sprites.render_weapon("sword", AP, 16, 3)
        glow = sprites.render_weapon("sword", dict(AP, glow=True), 16, 3)
        def halo(cv):
            return [(x, y) for y in range(cv.h) for x in range(cv.w) if 0 < cv.get(x, y)[3] < 255]
        self.assertEqual(halo(plain), [])
        self.assertGreater(len(halo(glow)), 10)
        self.assertEqual({p for p in opaque_rows(plain)}, {p for p in opaque_rows(glow)})

    def test_colors_follow_the_appearance_and_palette_keys(self):
        red = sprites.render_weapon("sword", dict(AP, blade="#ff0000"), 32, 1)
        reddish = sum(1 for y in range(32) for x in range(32) if red.get(x, y)[3] == 255 and red.get(x, y)[0] > red.get(x, y)[1] + 80)
        self.assertGreater(reddish, 25)
        keyed = sprites.render_weapon("sword", dict(AP, blade="primary"), 32, 1, {"primary": "#ff0000"})
        self.assertEqual(keyed.data, red.data)
        self.assertNotEqual(red.data, sprites.render_weapon("sword", AP, 32, 1).data)
        self.assertEqual(sprites.render_weapon("axe", AP, 32, 5).data, sprites.render_weapon("axe", AP, 32, 5).data)

    def test_bad_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            sprites.render_weapon("wand", AP, 32, 1)
        with self.assertRaises(ValueError):
            sprites.render_weapon("sword", AP, 24, 1)
        with self.assertRaises(ValueError):
            sprites.render_weapon("sword", dict(AP, blade="nope"), 32, 1)

    def test_essence_and_sigil_sprites(self):
        pal = {"primary": "#9aa5b8", "accent": "#d4aa32"}
        for glow in (False, True):
            e = sprites.render_essence(pal, glow, 4)
            self.assertEqual((e.w, e.h), (16, 16))
            self.assertEqual(len(components(e)), 1)
            halo = [1 for y in range(16) for x in range(16) if 0 < e.get(x, y)[3] < 255]
            self.assertEqual(bool(halo), glow)
        s = sprites.render_sigil(pal, 4)
        self.assertEqual((s.w, s.h), (16, 16))
        self.assertEqual(len(components(s)), 1)
        accent = [1 for y in range(16) for x in range(16) if s.get(x, y)[:3] == (0xd4, 0xaa, 0x32)]
        self.assertGreater(len(accent), 15)
        self.assertEqual(s.data, sprites.render_sigil(pal, 4).data)


class StandaloneFilesTests(unittest.TestCase):
    def test_texture_sizes_per_category(self):
        for cat in CATS:
            files, _ = standalone(cat)
            size = 32 if cat in LONG else 16
            self.assertEqual(png_size(files[res("assets/testmod/textures/item/test_blade.png")].data), (size, size), cat)
            gui = res("assets/testmod/textures/item/test_blade_gui.png")
            if cat in LONG:
                self.assertEqual(png_size(files[gui].data), (16, 16), cat)
            else:
                self.assertNotIn(gui, files, cat)

    def test_gui_texture_is_downsample_of_main(self):
        files, _ = standalone("greatsword")
        main = decode_png(files[res("assets/testmod/textures/item/test_blade.png")].data)
        gui = decode_png(files[res("assets/testmod/textures/item/test_blade_gui.png")].data)
        self.assertEqual(gui.data, main.downsampled(2).data)

    def test_capability_types_and_values(self):
        defaults = {"sword": (1.1, 1, 0.0), "axe": (1.8, 1, 5.0), "dagger": (0.6, 1, 0.0), "longsword": (2.5, 2, 0.0),
                    "greatsword": (4.0, 4, 10.0), "tachi": (2.0, 2, 5.0)}
        for cat, (impact, strikes, negation) in defaults.items():
            files, _ = standalone(cat)
            cap = jload(files, "data/testmod/capabilities/weapons/test_blade.json")
            self.assertEqual(cap["type"], EF_TYPES[cat])
            self.assertEqual(list(cap["attributes"]), ["common"])
            attrs = cap["attributes"]["common"]
            self.assertEqual(list(attrs), ["armor_negation", "impact", "max_strikes"])
            self.assertIsInstance(attrs["max_strikes"], int)
            self.assertIsInstance(attrs["impact"], float)
            self.assertIsInstance(attrs["armor_negation"], float)
            self.assertEqual((attrs["impact"], attrs["max_strikes"], attrs["armor_negation"]), (impact, strikes, negation))

    def test_capability_float_text_and_overrides(self):
        files, _ = standalone("sword", ef={"impact": 2, "armor_negation": 3, "max_strikes": 3.0, "damage_bonus": 1, "speed_bonus": 0})
        text = files[res("data/testmod/capabilities/weapons/test_blade.json")].data.decode()
        self.assertIn('"impact": 2.0', text)
        self.assertIn('"armor_negation": 3.0', text)
        self.assertIn('"max_strikes": 3,', text)
        self.assertIn('"damage_bonus": 1.0', text)
        self.assertNotIn("speed_bonus", text)

    def test_spear_capability_has_both_grips(self):
        files, _ = standalone("spear")
        cap = jload(files, "data/testmod/capabilities/weapons/test_blade.json")
        self.assertEqual(cap["type"], "epicfight:spear")
        self.assertEqual(cap["attributes"]["one_hand"], {"armor_negation": 8.0, "impact": 1.9, "max_strikes": 1})
        self.assertEqual(cap["attributes"]["two_hand"], {"armor_negation": 0.0, "impact": 1.3, "max_strikes": 3})

    def test_model_json_per_category(self):
        for cat in CATS:
            files, _ = standalone(cat)
            model = jload(files, "assets/testmod/models/item/test_blade.json")
            self.assertEqual(model, weapon.model_json(cat, "testmod", "test_blade"))
            if cat in ("sword", "axe"):
                self.assertEqual(model, {"parent": "minecraft:item/handheld", "textures": {"layer0": "testmod:item/test_blade"}})
            elif cat == "dagger":
                self.assertEqual(model, {"parent": "epicfight:item/dagger", "textures": {"layer0": "testmod:item/test_blade"}})
            else:
                self.assertEqual(model["loader"], "forge:separate_transforms")
                self.assertEqual(model["gui_light"], "front")
                self.assertEqual(model["base"], {"parent": "epicfight:item/" + cat, "textures": {"layer0": "testmod:item/test_blade"}})
                gui = {"parent": "epicfight:item/" + cat, "textures": {"layer0": "testmod:item/test_blade_gui"}}
                self.assertEqual(model["perspectives"], {"gui": gui, "fixed": gui, "ground": gui})

    def test_lang_keys(self):
        _, lang = standalone("sword", lore=["First.", "Second."], display_name="Test Blade")
        self.assertEqual(lang, {"item.testmod.test_blade": "Test Blade", "item.testmod.test_blade.desc.1": "First.",
                                "item.testmod.test_blade.desc.2": "Second."})

    def test_unknown_color_is_a_bossforge_error(self):
        with self.assertRaises(BossForgeError) as cm:
            standalone("sword", appearance={"blade": "shiny"})
        self.assertEqual(cm.exception.err.code, "bad_color")

    def test_output_is_deterministic(self):
        a, _ = standalone("tachi")
        b, _ = standalone("tachi")
        self.assertEqual({k: v.data for k, v in a.items()}, {k: v.data for k, v in b.items()})
        c, _ = standalone("tachi", id="other_blade")
        self.assertIn(res("assets/testmod/textures/item/other_blade.png"), c)
        self.assertIn(res("data/testmod/capabilities/weapons/other_blade.json"), c)

    def test_standalone_has_no_boss_files_but_a_preview(self):
        files, _ = standalone("sword")
        self.assertEqual(sorted(k for k in files if "loot_tables" in k or "essence" in k or "sigil" in k or "spawn_egg" in k), [])
        self.assertEqual(png_size(files["bossforge/previews/test_blade_weapon.png"].data), (128, 128))


class RecipeTests(unittest.TestCase):
    def test_default_recipes_are_valid_for_every_category(self):
        for cat in CATS:
            r = weapon.default_recipe(cat, "mymod:boss_essence")
            errs = []
            spec.validate_recipe(errs, "/r", r)
            self.assertEqual(errs, [], cat)
            self.assertLessEqual(len(r["pattern"]), 3)
            self.assertLessEqual(len(r["pattern"][0]), 3)
            self.assertEqual(r["key"]["E"], "mymod:boss_essence")
            self.assertEqual(set(r["key"]), set("".join(r["pattern"])) - {" "}, cat)

    def test_default_patterns(self):
        pats = {c: weapon.default_recipe(c, "a:b")["pattern"] for c in CATS}
        self.assertEqual(pats["sword"], [" E ", " E ", " S "])
        self.assertEqual(pats["dagger"], [" E ", " S "])
        self.assertEqual(pats["axe"], ["EE ", "ES ", " S "])
        self.assertEqual(pats["longsword"], pats["tachi"])
        self.assertEqual(pats["greatsword"], ["EEE", " N ", " S "])
        self.assertEqual(pats["spear"], ["  E", " S ", "S  "])

    def test_recipe_file_uses_essence_and_resolves_item_objects(self):
        files, _ = standalone("greatsword")
        r = jload(files, "data/testmod/recipes/test_blade.json")
        self.assertEqual(r["type"], "minecraft:crafting_shaped")
        self.assertEqual(r["category"], "equipment")
        self.assertEqual(r["result"], {"item": "testmod:test_blade", "count": 1})
        self.assertEqual(r["key"]["E"], {"item": "minecraft:diamond"})
        self.assertEqual(r["key"]["N"], {"item": "minecraft:netherite_ingot"})

    def test_essence_item_argument_beats_spec_beats_diamond(self):
        files, _ = standalone("sword", essence_item="a:from_arg")
        self.assertEqual(jload(files, "data/testmod/recipes/test_blade.json")["key"]["E"], {"item": "a:from_arg"})
        raw = spec.normalize_weapon_spec(dict(weapon_spec("sword"), essence_item="b:from_spec"))
        with tempfile.TemporaryDirectory() as td:
            ctx = spec.make_context(td, "testmod", "com.bossforge.testmod", td)
            by_spec = {f.relpath: f for f in weapon.generate_standalone(raw, ctx)[0]}
            by_arg = {f.relpath: f for f in weapon.generate_standalone(raw, ctx, "a:from_arg")[0]}
        self.assertEqual(jload(by_spec, "data/testmod/recipes/test_blade.json")["key"]["E"], {"item": "b:from_spec"})
        self.assertEqual(jload(by_arg, "data/testmod/recipes/test_blade.json")["key"]["E"], {"item": "a:from_arg"})
        with tempfile.TemporaryDirectory() as td:
            ctx = spec.make_context(td, "testmod", "com.bossforge.testmod", td)
            with self.assertRaises(BossForgeError) as cm:
                weapon.generate_standalone(raw, ctx, "not an id")
        self.assertEqual(cm.exception.err.code, "bad_resource_id")

    def test_no_recipe_when_not_craftable(self):
        files, _ = standalone("sword", obtain={"craft": False})
        self.assertNotIn(res("data/testmod/recipes/test_blade.json"), files)

    def test_recipe_override_is_used(self):
        override = {"pattern": ["AA", "B "], "key": {"A": "minecraft:iron_ingot", "B": "minecraft:stick"}}
        files, _ = standalone("sword", obtain={"craft": True, "recipe": override})
        r = jload(files, "data/testmod/recipes/test_blade.json")
        self.assertEqual(r["pattern"], ["AA", "B "])
        self.assertEqual(r["key"], {"A": {"item": "minecraft:iron_ingot"}, "B": {"item": "minecraft:stick"}})

    def test_recipe_override_validation_errors(self):
        good = {"pattern": ["AA", "B "], "key": {"A": "minecraft:iron_ingot", "B": "minecraft:stick"}}
        bad = {
            "four rows": dict(good, pattern=["A", "A", "A", "A"]),
            "four columns": dict(good, pattern=["AAAA"]),
            "unequal rows": dict(good, pattern=["AA", "B"]),
            "unkeyed char": dict(good, pattern=["AX", "B "]),
            "bad item id": dict(good, key={"A": "ironingot", "B": "minecraft:stick"}),
            "multi-char key": dict(good, key={"AA": "minecraft:iron_ingot", "B": "minecraft:stick"}),
            "no pattern": {"key": good["key"]},
        }
        for label, recipe in bad.items():
            errs = []
            spec.validate_weapon(errs, "/w", dict(weapon_spec("sword"), obtain={"recipe": recipe}))
            self.assertTrue(any(e.code == "recipe_invalid" and e.path.startswith("/w/obtain/recipe") for e in errs), label)
            with self.assertRaises(BossForgeError, msg=label) as cm:
                weapon.recipe_json("testmod", "test_blade", recipe)
            self.assertEqual(cm.exception.err.code, "recipe_invalid")
        errs = []
        spec.validate_weapon(errs, "/w", dict(weapon_spec("sword"), obtain={"recipe": good}))
        self.assertEqual(errs, [])


class BossFilesTests(unittest.TestCase):
    def test_king_arthur_weapon_set(self):
        n, files, lang = boss("major")
        self.assertEqual(png_size(files[res("assets/roundtable/textures/item/excalibur.png")].data), (32, 32))
        self.assertEqual(png_size(files[res("assets/roundtable/textures/item/excalibur_gui.png")].data), (16, 16))
        self.assertEqual(png_size(files[res("assets/roundtable/textures/item/king_arthur_essence.png")].data), (16, 16))
        self.assertEqual(png_size(files[res("assets/roundtable/textures/item/king_arthur_sigil.png")].data), (16, 16))
        for name in ("king_arthur_essence", "king_arthur_sigil"):
            m = jload(files, "assets/roundtable/models/item/%s.json" % name)
            self.assertEqual(m, {"parent": "minecraft:item/generated", "textures": {"layer0": "roundtable:item/" + name}})
        self.assertEqual(jload(files, "assets/roundtable/models/item/king_arthur_spawn_egg.json"),
                         {"parent": "minecraft:item/template_spawn_egg"})
        self.assertEqual(jload(files, "data/roundtable/capabilities/weapons/excalibur.json")["type"], "epicfight:longsword")
        r = jload(files, "data/roundtable/recipes/excalibur.json")
        self.assertEqual(r["key"]["E"], {"item": "roundtable:king_arthur_essence"})
        self.assertEqual(lang["item.roundtable.excalibur"], "Excalibur")
        self.assertEqual(lang["item.roundtable.excalibur.desc.2"], "Its edge remembers every oath.")
        self.assertEqual(lang["item.roundtable.king_arthur_essence"], "King Arthur's Essence")
        self.assertEqual(lang["item.roundtable.king_arthur_sigil"], "The Once and Future King Sigil")
        self.assertEqual(lang["item.roundtable.king_arthur_spawn_egg"], "King Arthur Spawn Egg")
        self.assertEqual(png_size(files["bossforge/previews/king_arthur_weapons.png"].data), (256, 256))
        self.assertNotIn(res("data/roundtable/loot_tables/chests/king_arthur_sigil.json"), files)
        # the generator must not write encounter-owned files
        self.assertEqual([k for k in files if "advancements" in k or k.endswith(".mcfunction") or "biome_modifier" in k
                          or k.endswith("king_arthur_sigil.json") and "recipes" in k], [])

    def test_loot_table_pools_and_conditions(self):
        n, files, _ = boss("major")
        loot = jload(files, "data/roundtable/loot_tables/entities/king_arthur.json")
        self.assertEqual(loot["type"], "minecraft:entity")
        weapon_pool, essence_pool = loot["pools"]
        self.assertEqual(weapon_pool["rolls"], 1)
        self.assertEqual(weapon_pool["entries"], [{"type": "minecraft:item", "name": "roundtable:excalibur"}])
        self.assertEqual(weapon_pool["conditions"], [{"condition": "minecraft:random_chance", "chance": 0.25}])
        self.assertEqual(essence_pool["rolls"], {"type": "minecraft:uniform", "min": 3, "max": 4})
        self.assertEqual(essence_pool["entries"], [{"type": "minecraft:item", "name": "roundtable:king_arthur_essence"}])
        self.assertNotIn("conditions", essence_pool)

    def test_only_droppable_weapons_get_loot_pools(self):
        second = {"id": "rusty_knife", "display_name": "Rusty Knife", "category": "dagger", "obtain": {"drop": 0, "craft": True}}
        raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
        n, files, _ = boss("major", weapons=raw["weapons"] + [second])
        loot = jload(files, "data/roundtable/loot_tables/entities/king_arthur.json")
        names = [e["name"] for p in loot["pools"] for e in p["entries"]]
        self.assertEqual(names, ["roundtable:excalibur", "roundtable:king_arthur_essence"])
        self.assertIn(res("data/roundtable/recipes/rusty_knife.json"), files)
        self.assertEqual(png_size(files["bossforge/previews/king_arthur_weapons.png"].data), (256 + 8 + 128, 256))

    def test_hidden_tier_sigil_is_loot_only(self):
        raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
        tier_defaults = [{k: v for k, v in w.items() if k != "obtain"} for w in raw["weapons"]]
        n, files, lang = boss("hidden", weapons=tier_defaults)
        self.assertEqual(jload(files, "data/roundtable/loot_tables/chests/king_arthur_sigil.json"),
                         {"type": "minecraft:chest", "pools": [{"rolls": 1, "entries": [
                             {"type": "minecraft:item", "name": "roundtable:king_arthur_sigil"}]}]})
        self.assertNotIn(res("assets/roundtable/models/item/king_arthur_spawn_egg.json"), files)
        self.assertNotIn("item.roundtable.king_arthur_spawn_egg", lang)
        self.assertIn(res("assets/roundtable/textures/item/king_arthur_sigil.png"), files)
        self.assertNotIn(res("data/roundtable/recipes/excalibur.json"), files)  # hidden weapons are loot-only
        loot = jload(files, "data/roundtable/loot_tables/entities/king_arthur.json")
        self.assertEqual(loot["pools"][0]["conditions"][0]["chance"], 1.0)
        self.assertEqual(loot["pools"][1]["rolls"], {"type": "minecraft:uniform", "min": 4, "max": 5})

    def test_command_trigger_has_no_sigil(self):
        n, files, lang = boss("quest", encounter={"trigger": "command"})
        self.assertNotIn(res("assets/roundtable/textures/item/king_arthur_sigil.png"), files)
        self.assertNotIn("item.roundtable.king_arthur_sigil", lang)
        self.assertNotIn(res("data/roundtable/loot_tables/chests/king_arthur_sigil.json"), files)
        self.assertIn(res("assets/roundtable/models/item/king_arthur_spawn_egg.json"), files)

    def test_boss_without_weapons_has_essence_loot_and_no_preview(self):
        n, files, lang = boss("mini", weapons=[], main_weapon=None, encounter={"trigger": "natural_spawn"})
        loot = jload(files, "data/roundtable/loot_tables/entities/king_arthur.json")
        self.assertEqual(len(loot["pools"]), 1)
        self.assertEqual(loot["pools"][0]["rolls"], {"type": "minecraft:uniform", "min": 1, "max": 1})
        self.assertNotIn("bossforge/previews/king_arthur_weapons.png", files)
        self.assertFalse([k for k in files if "capabilities" in k or "recipes" in k])
        self.assertFalse([k for k in lang if ".desc." in k])

    def test_boss_output_is_deterministic(self):
        _, a, la = boss("major")
        _, b, lb = boss("major")
        self.assertEqual({k: v.data for k, v in a.items()}, {k: v.data for k, v in b.items()})
        self.assertEqual(list(la.items()), list(lb.items()))
        self.assertTrue(all(not k.startswith("/") and "\\" not in k for k in a))

    def test_glowing_eyes_add_essence_halo(self):
        n, files, _ = boss("major")  # King Arthur: eyes glow
        ess = decode_png(files[res("assets/roundtable/textures/item/king_arthur_essence.png")].data)
        self.assertTrue(any(0 < ess.get(x, y)[3] < 255 for y in range(16) for x in range(16)))
        raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
        feats = copy.deepcopy(raw["appearance"]["features"])
        for f in feats:
            if f["type"] == "eyes":
                f["glow"] = False
        n2, files2, _ = boss("major", appearance=dict(raw["appearance"], features=feats))
        ess2 = decode_png(files2[res("assets/roundtable/textures/item/king_arthur_essence.png")].data)
        self.assertFalse(any(0 < ess2.get(x, y)[3] < 255 for y in range(16) for x in range(16)))


def shapes_of(cat):
    return spec.data("weapon-categories")[cat]["shapes"]


def alpha_count(cv):
    return sum(1 for y in range(cv.h) for x in range(cv.w) if cv.get(x, y)[3] == 255)


def tip_extent(cv):
    """Farthest opaque pixel along the up-right weapon axis (x - y grows toward the tip)."""
    return max(x - y for y in range(cv.h) for x in range(cv.w) if cv.get(x, y)[3] == 255)


class ShapeTests(unittest.TestCase):
    def test_every_shape_renders_one_component_that_differs_from_the_default(self):
        for cat in CATS:
            sh = shapes_of(cat)
            for res_px in (32, 16):
                default = sprites.render_weapon(cat, AP, res_px, 9)
                self.assertEqual(sprites.render_weapon(cat, dict(AP, shape=sh["default"]), res_px, 9).data, default.data, cat)
                for shape in sh["allowed"]:
                    for motif in ("fuller", "runes", "serrated"):
                        for glow in (False, True):
                            cv = sprites.render_weapon(cat, dict(AP, shape=shape, motif=motif, glow=glow), res_px, 9)
                            self.assertEqual(len(components(cv)), 1, "%s/%s res=%d motif=%s glow=%s" % (cat, shape, res_px, motif, glow))
                    if shape != sh["default"]:
                        other = sprites.render_weapon(cat, dict(AP, shape=shape), res_px, 9)
                        diff = sum(1 for i in range(0, len(other.data), 4) if other.data[i:i + 4] != default.data[i:i + 4])
                        self.assertGreaterEqual(diff, 4, "%s/%s res=%d looks like the default" % (cat, shape, res_px))

    def test_shapes_are_distinct_from_each_other(self):
        for cat in CATS:
            for res_px in (32, 16):
                seen = {}
                for shape in shapes_of(cat)["allowed"]:
                    seen.setdefault(bytes(sprites.render_weapon(cat, dict(AP, shape=shape), res_px, 9).data), []).append(shape)
                self.assertTrue(all(len(v) == 1 for v in seen.values()), "%s res=%d duplicates: %s" % (cat, res_px, list(seen.values())))

    def test_every_guard_and_pommel_combination_stays_connected(self):
        for cat in CATS:
            for res_px in (32, 16):
                for guard in GUARD_SHAPES:
                    for pommel in POMMEL_SHAPES:
                        for glow in (False, True):
                            cv = sprites.render_weapon(cat, dict(AP, guard_shape=guard, pommel_shape=pommel, glow=glow), res_px, 3)
                            self.assertEqual(len(components(cv)), 1, "%s res=%d guard=%s pommel=%s" % (cat, res_px, guard, pommel))

    def test_guard_and_pommel_shapes_change_the_sprite(self):
        for cat in CATS:
            for res_px in (32, 16):
                default = sprites.render_weapon(cat, AP, res_px, 3).data
                for pommel in POMMEL_SHAPES:
                    if pommel == "disc" or (cat == "spear" and pommel == "none"):
                        continue  # a spear has a plain butt unless given a gem/spike/ring cap
                    self.assertNotEqual(sprites.render_weapon(cat, dict(AP, pommel_shape=pommel), res_px, 3).data, default, (cat, pommel))
                if cat in ("axe", "spear"):
                    self.assertNotEqual(sprites.render_weapon(cat, dict(AP, guard_shape="none"), res_px, 3).data, default, cat)
                    continue
                for guard in GUARD_SHAPES:
                    if guard != "bar":
                        self.assertNotEqual(sprites.render_weapon(cat, dict(AP, guard_shape=guard), res_px, 3).data, default, (cat, guard))

    def test_length_grows_the_weapon_monotonically(self):
        for cat in CATS:
            for res_px in (32, 16):
                ext = [tip_extent(sprites.render_weapon(cat, dict(AP, length=v), res_px, 3)) for v in (0.8, 0.9, 1.0, 1.1, 1.15)]
                self.assertEqual(ext, sorted(ext), "%s res=%d %s" % (cat, res_px, ext))
                self.assertLess(ext[0], ext[-1], "%s res=%d %s" % (cat, res_px, ext))
            self.assertLess(tip_extent(sprites.render_weapon(cat, dict(AP, length=0.8), 32, 3)),
                            tip_extent(sprites.render_weapon(cat, AP, 32, 3)), cat)

    def test_width_grows_the_weapon_monotonically(self):
        for cat in CATS:
            for res_px in (32, 16):
                counts = [alpha_count(sprites.render_weapon(cat, dict(AP, width=v), res_px, 3)) for v in (0.7, 0.85, 1.0, 1.2, 1.4)]
                self.assertEqual(counts, sorted(counts), "%s res=%d %s" % (cat, res_px, counts))
                self.assertLess(counts[0], counts[-1], "%s res=%d %s" % (cat, res_px, counts))

    def test_length_and_width_extremes_stay_one_component_inside_the_canvas(self):
        for cat in CATS:
            for shape in shapes_of(cat)["allowed"]:
                for res_px in (32, 16):
                    for ln, wd in ((0.8, 0.7), (1.15, 1.4), (0.8, 1.4), (1.15, 0.7)):
                        cv = sprites.render_weapon(cat, dict(AP, shape=shape, length=ln, width=wd), res_px, 5)
                        self.assertEqual(len(components(cv)), 1, (cat, shape, res_px, ln, wd))
                        self.assertGreater(alpha_count(cv), 10)

    def test_invalid_options_are_rejected_with_the_allowed_values(self):
        bad = [dict(shape="trident"), dict(shape="nope"), dict(guard_shape="spiked"), dict(pommel_shape="orb"),
               dict(length=1.5), dict(length=0.5), dict(width=0.2), dict(width=2.0), dict(length="long")]
        for opts in bad:
            with self.assertRaises(ValueError, msg=str(opts)) as cm:
                sprites.render_weapon("sword", dict(AP, **opts), 32, 1)
            self.assertIsInstance(cm.exception, sprites.ShapeError)
        with self.assertRaises(sprites.ShapeError) as cm:
            sprites.render_weapon("sword", dict(AP, shape="trident"), 32, 1)
        self.assertIn("straight", str(cm.exception))
        sprites.render_weapon("spear", dict(AP, shape="trident"), 32, 1)  # allowed for a spear

    def test_generate_reports_a_bad_shape_as_bossforge_error(self):
        with self.assertRaises(BossForgeError) as cm:
            standalone("sword", appearance={"shape": "trident"})
        self.assertEqual(cm.exception.err.code, "unknown_value")

    def test_appearance_options_reach_the_generated_texture(self):
        plain, _ = standalone("longsword")
        for opts in (dict(shape="wavy"), dict(guard_shape="winged"), dict(pommel_shape="spike"), dict(length=0.8), dict(width=1.4)):
            files, _ = standalone("longsword", appearance=opts)
            key = res("assets/testmod/textures/item/test_blade.png")
            self.assertNotEqual(files[key].data, plain[key].data, opts)
            gui = decode_png(files[res("assets/testmod/textures/item/test_blade_gui.png")].data)
            self.assertEqual(gui.data, decode_png(files[key].data).downsampled(2).data)

    def test_shapes_are_deterministic(self):
        for cat in CATS:
            for shape in shapes_of(cat)["allowed"]:
                a = sprites.render_weapon(cat, dict(AP, shape=shape, guard_shape="ornate", pommel_shape="ring"), 32, 21)
                b = sprites.render_weapon(cat, dict(AP, shape=shape, guard_shape="ornate", pommel_shape="ring"), 32, 21)
                self.assertEqual(a.data, b.data)


STAFF = dict(AP, shape="crystal_staff", blade="#bfe9ff", guard="#f0c850", grip="#4aa0e8", gem="#2ea8ff")
STAFF_SHAPES = ("crystal_staff", "orb_staff", "diamond_staff")


class StaffTests(unittest.TestCase):
    def test_staffs_default_to_64_and_other_spear_shapes_stay_32(self):
        for shape in STAFF_SHAPES:
            self.assertEqual(sprites.sprite_resolution("spear", dict(STAFF, shape=shape)), 64)
            self.assertEqual(sprites.sprite_resolution("spear", dict(STAFF, shape=shape, resolution=32)), 32)
        for shape in ("leaf", "trident", "hooked", "broad"):
            self.assertEqual(sprites.sprite_resolution("spear", {"shape": shape}), 32)
        self.assertEqual(sprites.sprite_resolution("spear", None), 32)
        self.assertEqual(sprites.sprite_resolution("sword", None), 16)

    def test_resolution_the_shape_does_not_offer_is_rejected(self):
        bad = [("spear", dict(shape="leaf", resolution=64)), ("sword", dict(resolution=64)), ("sword", dict(resolution=32)),
               ("spear", dict(shape="crystal_staff", resolution=16)), ("spear", dict(shape="crystal_staff", resolution="64")),
               ("spear", dict(shape="crystal_staff", resolution=True))]
        for cat, ap in bad:
            with self.assertRaises(sprites.ShapeError, msg=str((cat, ap))) as cm:
                sprites.sprite_resolution(cat, ap)
            self.assertIn("allowed values", str(cm.exception))
        with self.assertRaises(sprites.ShapeError):
            sprites.render_weapon("spear", {"shape": "leaf"}, 64, 1)
        with self.assertRaises(sprites.ShapeError):
            sprites.render_weapon("longsword", AP, 64, 1)

    def test_staff_is_one_connected_piece_at_every_option_and_resolution(self):
        for shape in STAFF_SHAPES:
            for res_px in (64, 32, 16):
                for rings in (0, 2, 4):
                    for spikes in (2, 3, 5):
                        for glow in (False, True):
                            cv = sprites.render_weapon("spear", dict(STAFF, shape=shape, rings=rings, spikes=spikes, glow=glow), res_px, 5)
                            self.assertEqual((cv.w, cv.h), (res_px, res_px))
                            self.assertEqual(len(components(cv)), 1, (shape, res_px, rings, spikes, glow))
                for ln, wd in ((0.8, 0.7), (1.15, 1.4), (0.8, 1.4), (1.15, 0.7)):
                    cv = sprites.render_weapon("spear", dict(STAFF, shape=shape, length=ln, width=wd), res_px, 5)
                    self.assertEqual(len(components(cv)), 1, (shape, res_px, ln, wd))

    def test_staff_shows_gold_ring_orb_crystal_and_the_shaft_band_colour(self):
        def opaque(cv):
            return [cv.get(x, y) for y in range(cv.h) for x in range(cv.w) if cv.get(x, y)[3] == 255]

        def reddish(cv):
            return sum(1 for p in opaque(cv) if p[0] > p[1] + 80 and p[0] > p[2] + 80)

        pix = opaque(sprites.render_weapon("spear", STAFF, 64, 3))
        gold = [p for p in pix if p[0] > p[2] + 60 and p[1] > p[2] + 30]  # warm tones only come from the guard colour
        pale = [p for p in pix if min(p[:3]) > 180]  # crystal
        self.assertGreater(len(gold), 40)
        self.assertGreater(len(pale), 60)
        self.assertEqual(reddish(sprites.render_weapon("spear", STAFF, 64, 3)), 0)
        self.assertGreater(reddish(sprites.render_weapon("spear", dict(STAFF, gem="#ff2020"), 64, 3)), 25)  # the orb
        self.assertGreater(reddish(sprites.render_weapon("spear", dict(STAFF, grip="#c02020"), 64, 3)), 15)  # the shaft bands

    def test_rings_spikes_and_shape_change_only_the_staffs(self):
        base = sprites.render_weapon("spear", STAFF, 64, 3).data
        self.assertNotEqual(sprites.render_weapon("spear", dict(STAFF, rings=0), 64, 3).data, base)
        self.assertNotEqual(sprites.render_weapon("spear", dict(STAFF, rings=4), 64, 3).data, base)
        self.assertNotEqual(sprites.render_weapon("spear", dict(STAFF, spikes=5), 64, 3).data, base)
        orb = dict(STAFF, shape="orb_staff")
        self.assertNotEqual(sprites.render_weapon("spear", dict(orb, spikes=5), 64, 3).data,
                            sprites.render_weapon("spear", dict(orb, spikes=2), 64, 3).data)
        leaf = dict(AP, shape="leaf")
        self.assertEqual(sprites.render_weapon("spear", dict(leaf, rings=4, spikes=5), 32, 3).data,
                         sprites.render_weapon("spear", leaf, 32, 3).data)
        for opts in (dict(rings=5), dict(rings=-1), dict(rings=1.5), dict(spikes=1), dict(spikes=6), dict(spikes=True)):
            with self.assertRaises(sprites.ShapeError, msg=str(opts)):
                sprites.render_weapon("spear", dict(STAFF, **opts), 64, 3)

    def test_generated_files_follow_the_sprite_resolution(self):
        for opts, size in ((dict(shape="crystal_staff"), 64), (dict(shape="orb_staff"), 64), (dict(shape="diamond_staff"), 64),
                           (dict(shape="crystal_staff", resolution=32), 32), (dict(shape="diamond_staff", resolution=32), 32),
                           (dict(shape="leaf"), 32)):
            files, _ = standalone("spear", appearance=opts)
            main = decode_png(files[res("assets/testmod/textures/item/test_blade.png")].data)
            gui = decode_png(files[res("assets/testmod/textures/item/test_blade_gui.png")].data)
            self.assertEqual((main.w, main.h), (size, size), opts)
            self.assertEqual((gui.w, gui.h), (size // 2, size // 2), opts)
            self.assertEqual(gui.data, main.downsampled(2).data)
            model = json.loads(files[res("assets/testmod/models/item/test_blade.json")].data.decode("utf-8"))
            self.assertEqual(model["loader"], "forge:separate_transforms")  # the texture size never changes the model parent
            self.assertEqual(model["base"]["parent"], "epicfight:item/spear")
            self.assertEqual(png_size(files["bossforge/previews/test_blade_weapon.png"].data), (size * 8, size * 8))

    def test_spec_validation_reports_bad_staff_options(self):
        cases = [
            (dict(shape="crystal_staff", resolution=16), "resolution", "unknown_value"),
            (dict(shape="leaf", resolution=64), "resolution", "unknown_value"),
            (dict(rings=5), "rings", "out_of_range"),
            (dict(rings=1.5), "rings", "bad_type"),
            (dict(spikes=1), "spikes", "out_of_range"),
            (dict(shape="wand"), "shape", "unknown_value"),
        ]
        for ap, key, code in cases:
            errs = []
            spec.validate_weapon(errs, "/w", weapon_spec("spear", appearance=ap))
            self.assertEqual([(e.path, e.code) for e in errs], [("/w/appearance/" + key, code)], ap)
        errs = []
        spec.validate_weapon(errs, "/w", weapon_spec("spear", appearance=dict(shape="crystal_staff", resolution=64, rings=3, spikes=4)))
        self.assertEqual(errs, [])
        errs = []
        spec.validate_weapon(errs, "/w", weapon_spec("sword", appearance=dict(resolution=32)))
        self.assertEqual([e.path for e in errs], ["/w/appearance/resolution"])

    def test_staff_is_deterministic_and_follows_the_colours(self):
        a = sprites.render_weapon("spear", STAFF, 64, 21)
        self.assertEqual(a.data, sprites.render_weapon("spear", STAFF, 64, 21).data)
        gold = sprites.render_weapon("spear", dict(STAFF, guard="#ff0000"), 64, 21)
        reddish = sum(1 for y in range(64) for x in range(64) if gold.get(x, y)[3] == 255 and gold.get(x, y)[0] > gold.get(x, y)[1] + 80)
        self.assertGreater(reddish, 40)
        orange = sprites.render_weapon("spear", dict(STAFF, gem="#ff6a1a"), 64, 21)
        self.assertGreater(sum(1 for y in range(64) for x in range(64)
                               if orange.get(x, y)[3] == 255 and orange.get(x, y)[0] > 200 and orange.get(x, y)[2] < 120), 25)

    def test_boss_staff_files_match_the_sizes_qa_expects(self):
        from bossforge_core import qa
        raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
        weapons = [dict(raw["weapons"][0], id="glowing_staff", category="spear", appearance=dict(STAFF, glow=True)),
                   dict(raw["weapons"][0], id="plain_spear", category="spear", appearance={})]
        n, files, _ = boss("major", weapons=weapons, main_weapon="glowing_staff")
        expected = {it["name"]: (it["sprite"], it["gui"]) for it in qa._item_names(n) if it["kind"] == "weapon"}
        self.assertEqual(expected, {"glowing_staff": (64, 32), "plain_spear": (32, 16)})
        for name, (size, gui) in expected.items():
            base = "assets/roundtable/textures/item/" + name
            self.assertEqual(png_size(files[res(base + ".png")].data), (size, size))
            self.assertEqual(png_size(files[res(base + "_gui.png")].data), (gui, gui))
        self.assertEqual(png_size(files["bossforge/previews/king_arthur_weapons.png"].data)[1], 64 * 8)


DIAMOND = dict(AP, shape="diamond_staff", blade="#86bdf0", guard="#e0b948", grip="#6fb4e6", gem="#4fdcf5")


def opaque(cv):
    return [cv.get(x, y) for y in range(cv.h) for x in range(cv.w) if cv.get(x, y)[3] == 255]


def reddish(cv):
    return sum(1 for p in opaque(cv) if p[0] > p[1] + 80 and p[0] > p[2] + 80)


class DiamondStaffTests(unittest.TestCase):
    """diamond_staff: a banded shaft in a gold collar under an open rhombus frame (crystal bars on the near two sides, gold
    bars on the far two) around a bright orb with a small bead on the far corner, as in the Nightingale reference."""

    def test_defaults_resolution_scale_and_grip(self):
        self.assertEqual(sprites.sprite_resolution("spear", DIAMOND), 64)
        self.assertEqual(sprites.sprite_resolution("spear", dict(DIAMOND, resolution=32)), 32)
        with self.assertRaises(sprites.ShapeError):
            sprites.sprite_resolution("spear", dict(DIAMOND, resolution=16))
        self.assertTrue(sprites.is_staff("spear", DIAMOND))
        self.assertFalse(sprites.is_staff("sword", DIAMOND))
        # a short sceptre: 0.3 of a biped, gripped near the butt (the crystal staff keeps 1.0 / 0.40)
        self.assertEqual(sprites.staff_default(DIAMOND, "visual_scale"), 0.3)
        self.assertEqual(sprites.staff_default(DIAMOND, "grip"), 0.26)
        for shape in ("crystal_staff", "orb_staff"):
            self.assertEqual(sprites.staff_default({"shape": shape}, "visual_scale"), 1.0)
            self.assertEqual(sprites.staff_default({"shape": shape}, "grip"), sprites.STAFF_GRIP)
        staff = {"category": "spear", "appearance": dict(DIAMOND)}
        for boss_scale in (None, 1.0, 1.5, 3.0):
            self.assertEqual(weapon.effective_visual_scale(staff, boss_scale, True), 0.3)
        self.assertEqual(weapon.effective_visual_scale(dict(staff, visual_scale=0.5), 3.0, True), 0.5)
        self.assertEqual(weapon.effective_visual_scale({"category": "spear", "appearance": {"shape": "crystal_staff"}}, 3.0, True), 1.0)

    def test_one_connected_piece_for_every_option_and_resolution(self):
        for res_px in (64, 32, 16):
            for frame in (0.7, 1.0, 1.3):
                for length in (0.8, 1.15):
                    for width in (0.7, 1.4):
                        for rings in (0, 4):
                            for glow in (False, True):
                                ap = dict(DIAMOND, frame=frame, length=length, width=width, rings=rings, glow=glow)
                                cv = sprites.render_weapon("spear", ap, res_px, 5)
                                self.assertEqual(len(components(cv)), 1, (res_px, frame, length, width, rings, glow))
                                self.assertGreater(alpha_count(cv), 40)
        for rings in (1, 2, 3):
            self.assertEqual(len(components(sprites.render_weapon("spear", dict(DIAMOND, rings=rings), 64, 5))), 1)

    def test_frame_option_is_validated_and_resizes_only_the_head(self):
        for bad in (0.69, 1.31, "1", True, None):
            with self.assertRaises(sprites.ShapeError, msg=str(bad)):
                sprites.render_weapon("spear", dict(DIAMOND, frame=bad), 64, 3)
            with self.assertRaises(sprites.ShapeError, msg=str(bad)):
                sprites.staff_geometry(dict(DIAMOND, frame=bad))
        spans = []
        for frame in (0.7, 0.85, 1.0, 1.15, 1.3):
            cv = sprites.render_weapon("spear", dict(DIAMOND, frame=frame), 64, 3)
            across = [x + y for y in range(64) for x in range(64) if cv.get(x, y)[3] == 255]  # grows with the distance from the axis
            spans.append(max(across) - min(across))
        self.assertEqual(spans, sorted(spans))
        self.assertLess(spans[0], spans[-1])
        # the shaft and the butt do not move: the foot of the sprite is the same pixel for every frame
        feet = set()
        for f in (0.7, 1.3):
            cv = sprites.render_weapon("spear", dict(DIAMOND, frame=f), 64, 3)
            feet.add(min((x - y, x, y) for y in range(64) for x in range(64) if cv.get(x, y)[3] == 255))
        self.assertEqual(len(feet), 1)
        base = sprites.render_weapon("spear", DIAMOND, 64, 3).data
        self.assertEqual(sprites.render_weapon("spear", dict(DIAMOND, frame=1.0), 64, 3).data, base)  # 1.0 is the default
        self.assertEqual(sprites.render_weapon("spear", dict(AP, shape="leaf", frame=1.3), 32, 3).data,
                         sprites.render_weapon("spear", dict(AP, shape="leaf"), 32, 3).data)  # other shapes ignore it

    def test_head_is_wider_than_long_like_the_reference(self):
        # the reference head measures about 55 px along the staff and 68 across it (near-orthographic front view)
        d = sprites._diamond_dims({"length": 1.0, "frame": 1.0})
        cv = sprites.render_weapon("spear", DIAMOND, 64, 3)
        head = [(x, y) for y in range(64) for x in range(64)
                if cv.get(x, y)[3] == 255 and math.sqrt(0.5) * (0.5 * (x - y) + 28.0) > d["bottom"]]  # past the near corner
        across = [x + y for x, y in head]  # both sums scale alike: 0.354 design px per sprite px step
        along = [x - y for x, y in head]
        self.assertGreater(max(across) - min(across), 1.15 * (max(along) - min(along)))

    def test_parts_follow_the_colours(self):
        cv = sprites.render_weapon("spear", DIAMOND, 64, 3)
        pix = opaque(cv)
        self.assertGreater(len([p for p in pix if p[0] > p[2] + 60 and p[1] > p[2] + 30]), 60)  # gold bars, collar and ferrule
        self.assertGreater(len([p for p in pix if min(p[:3]) > 180]), 40)  # crystal bars and facets
        self.assertEqual(reddish(cv), 0)
        self.assertGreater(reddish(sprites.render_weapon("spear", dict(DIAMOND, gem="#ff2020"), 64, 3)), 60)  # orb and bead
        self.assertGreater(reddish(sprites.render_weapon("spear", dict(DIAMOND, grip="#c02020"), 64, 3)), 15)  # shaft bands
        self.assertGreater(reddish(sprites.render_weapon("spear", dict(DIAMOND, guard="#ff0000"), 64, 3)), 60)  # gold parts

    def test_rings_change_the_shaft_and_spikes_do_nothing(self):
        base = sprites.render_weapon("spear", DIAMOND, 64, 3).data
        for rings in (0, 1, 3, 4):
            self.assertNotEqual(sprites.render_weapon("spear", dict(DIAMOND, rings=rings), 64, 3).data, base, rings)
        self.assertEqual(sprites.render_weapon("spear", dict(DIAMOND, spikes=5), 64, 3).data, base)
        self.assertEqual(sprites.render_weapon("spear", DIAMOND, 64, 3).data, base)  # deterministic
        self.assertNotEqual(sprites.render_weapon("spear", DIAMOND, 64, 4).data, base)  # the seed only sparkles a few pixels
        other = sprites.render_weapon("spear", dict(DIAMOND, shape="crystal_staff"), 64, 3).data
        self.assertNotEqual(other, base)

    def test_geometry_matches_the_painted_sprite(self):
        for frame in (0.7, 1.0, 1.3):
            for length in (0.8, 1.0, 1.15):
                ap = dict(DIAMOND, frame=frame, length=length)
                g = sprites.staff_geometry(ap)
                cv = sprites.render_weapon("spear", ap, 64, 5)

                def pix(p, along=0.0):
                    return cv.get(int((p[0] + along * g["axis"][0]) * 64), int((1.0 - p[1] - along * g["axis"][1]) * 64))

                self.assertEqual(pix(g["tip"], -1 / 64.0)[3], 255, (frame, length))  # the bead ends at the axis end
                self.assertEqual(pix(g["butt"], 1 / 64.0)[3], 255, (frame, length))
                self.assertLess(pix(g["tip"], 4 / 64.0)[3], 255, (frame, length))
                core = pix(g["orb"])  # the middle of the frame is the orb's bright core in the gem colour
                self.assertEqual(core[3], 255)
                self.assertGreater(min(core[:3]), 150, (frame, length, core))
                self.assertGreater(core[2], core[0] + 30)
                frac = math.dist(g["butt"], g["orb"]) / math.dist(g["butt"], g["tip"])
                self.assertTrue(0.6 < frac < 0.9, (frame, length, frac))
        a = sprites.staff_geometry(dict(DIAMOND, frame=0.7))["orb"]
        b = sprites.staff_geometry(dict(DIAMOND, frame=1.3))["orb"]
        self.assertLess(math.dist(a, sprites.staff_geometry(DIAMOND)["tip"]), math.dist(b, sprites.staff_geometry(DIAMOND)["tip"]))

    def test_spec_validation_of_frame_and_visual_scale(self):
        cases = [
            (dict(shape="diamond_staff", frame=1.5), "frame", "out_of_range"),
            (dict(shape="diamond_staff", frame=0.5), "frame", "out_of_range"),
            (dict(shape="diamond_staff", frame="big"), "frame", "bad_type"),
            (dict(shape="diamond_staff", resolution=16), "resolution", "unknown_value"),
        ]
        for ap, key, code in cases:
            errs = []
            spec.validate_weapon(errs, "/w", weapon_spec("spear", appearance=ap))
            self.assertEqual([(e.path, e.code) for e in errs], [("/w/appearance/" + key, code)], ap)
        for extra in (dict(frame=0.7), dict(frame=1.3, rings=3, spikes=4, resolution=32, length=0.8)):
            errs = []
            spec.validate_weapon(errs, "/w", weapon_spec("spear", appearance=dict(shape="diamond_staff", **extra)))
            self.assertEqual(errs, [], extra)
        n = spec.normalize_weapon_spec(weapon_spec("spear", appearance=dict(shape="diamond_staff", frame=0.9)))
        self.assertEqual(n["appearance"]["frame"], 0.9)
        # a sceptre may be shorter than the 0.4 floor of the other weapons
        for vs, ap, ok in ((0.25, dict(shape="diamond_staff"), True), (0.3, dict(shape="crystal_staff"), True), (0.2, dict(shape="diamond_staff"), False),
                           (0.3, dict(shape="leaf"), False)):
            errs = []
            spec.validate_weapon(errs, "/w", weapon_spec("spear", appearance=ap, visual_scale=vs))
            self.assertEqual([(e.path, e.code) for e in errs], [] if ok else [("/w/visual_scale", "out_of_range")], (vs, ap))
        errs = []
        spec.validate_weapon(errs, "/w", weapon_spec("sword", visual_scale=0.3))
        self.assertEqual([(e.path, e.code) for e in errs], [("/w/visual_scale", "out_of_range")])

    def test_model_uses_the_diamond_staffs_own_scale_and_grip(self):
        files, _ = standalone("spear", appearance=dict(shape="diamond_staff", frame=0.9, length=0.8))
        m = jload(files, "assets/testmod/models/item/test_blade.json")
        d = m["base"]["display"]["thirdperson_righthand"]
        ap = dict(shape="diamond_staff", frame=0.9, length=0.8)
        g = sprites.staff_geometry(ap)
        self.assertEqual(d, holdpose.staff_display(ap, 0.3, None))
        self.assertAlmostEqual(math.dist(holdpose.place(d, g["butt"]), holdpose.place(d, g["tip"])), 1.8 * 0.3, delta=0.003)
        origin, _ = holdpose.hand_frame()
        grip = (g["butt"][0] + 0.26 * (g["tip"][0] - g["butt"][0]), g["butt"][1] + 0.26 * (g["tip"][1] - g["butt"][1]))
        for a, b in zip(holdpose.place(d, grip), origin):
            self.assertAlmostEqual(a, b, delta=0.002)
        self.assertEqual(weapon.audit_staff_model(m, dict(id="w", category="spear", appearance=ap)), [])

    def test_predicted_hold_numbers(self):
        # where the orb ends up in body space (x right, y up, z back; blocks at entity scale 1) for the Nightingale staff
        ap = dict(shape="diamond_staff", frame=0.9, length=0.8)
        g = sprites.staff_geometry(ap)
        up = holdpose.staff_display(ap, 0.3)  # the default: upright, leaning 10 degrees forward
        orb = holdpose.place(up, g["orb"])
        self.assertAlmostEqual(orb[0], 0.376, delta=0.01)
        self.assertTrue(1.03 < orb[1] < 1.12, orb)  # between hip (hand 0.80) and chest
        self.assertAlmostEqual(angle_deg(holdpose.direction(up, g["butt"], g["tip"]), (0, 1, 0)), 10.0, delta=0.05)
        for pose in ("hold_spear", "idle", "bind", "walk_spear"):
            self.assertTrue(1.03 < holdpose.place(up, g["orb"], pose)[1] < 1.12, pose)
        # the reference carries it nearly horizontal, the head away from the body at hip height
        carry = holdpose.staff_display(ap, 0.3, {"lean": 0, "side": 82, "offset": [-0.4, 0.0, 0.14]})
        orb = holdpose.place(carry, g["orb"])
        self.assertTrue(0.55 < orb[0] < 0.75 and 0.82 < orb[1] < 0.95 and abs(orb[2]) < 0.1, orb)
        self.assertAlmostEqual(holdpose.direction(carry, g["butt"], g["tip"])[1], math.sin(math.radians(8.0)), delta=0.01)

    def test_every_hold_knob_works_up_to_a_horizontal_staff(self):
        ap = dict(shape="diamond_staff", frame=0.9, length=0.8)
        g = sprites.staff_geometry(ap)
        for hold in ({"side": 90, "lean": 0}, {"side": -90, "lean": 0}, {"side": 82, "lean": 0}, {"side": 60, "lean": 30},
                     {"side": -75, "lean": 15, "facing": "side"}):
            d = holdpose.staff_display(ap, 0.3, hold)
            want = holdpose._target_axis(hold["lean"], hold["side"])
            self.assertLess(angle_deg(holdpose.direction(d, g["butt"], g["tip"]), want), 0.05, hold)
            self.assertTrue(all(abs(v) <= 80.0 for v in d["translation"]) and all(abs(v) <= 4.0 for v in d["scale"]))
        self.assertEqual(holdpose.check_hold({"side": 90, "lean": 0}), [])
        for bad in (91, -90.5):
            self.assertEqual([e[:2] for e in holdpose.check_hold({"side": bad})], [("/hold/side", "out_of_range")])
        # the sprite plane cannot face along its own axis
        errs = holdpose.check_hold({"side": 90, "lean": 0, "facing": "side"})
        self.assertEqual([e[:2] for e in errs], [("/hold/facing", "out_of_range")])
        with self.assertRaises(BossForgeError):
            holdpose.staff_display(ap, 0.3, {"side": 90, "lean": 0, "facing": "side"})
        self.assertEqual(holdpose.check_hold({"side": 80, "lean": 0, "facing": "side"}), [])


def rotate(rx, ry, rz, v):
    """Independent copy of Minecraft's item rotation (Z, then Y, then X; degrees)."""
    x, y, z = v
    c, s = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    x, y = x * c - y * s, x * s + y * c
    c, s = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    x, z = x * c + z * s, -x * s + z * c
    c, s = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    y, z = y * c - z * s, y * s + z * c
    return x, y, z


def grip_in_hand(entry, left, grip):
    """Hand-space position of the grip point under one display entry, applying Minecraft's left-hand mirroring."""
    rx, ry, rz = entry["rotation"]
    tx, ty, tz = entry["translation"]
    sx, sy, sz = entry["scale"]
    if left:
        ry, rz, tx = -ry, -rz, -tx
    v = rotate(rx, ry, rz, (grip[0] * sx, grip[1] * sy, 0.0))
    return (tx + v[0], ty + v[1], tz + v[2])


HAND_KEYS = ("thirdperson_righthand", "thirdperson_lefthand", "firstperson_righthand", "firstperson_lefthand")
PARENT_SCALE = {  # parent third-person right-hand scale, from the shipped parent models
    "sword": (0.85, 0.85, 0.85), "axe": (0.85, 0.85, 0.85), "dagger": (0.8, 0.8, 1.0), "longsword": (1.5, 1.5, 0.85),
    "greatsword": (1.7, 1.7, 0.85), "tachi": (1.7, 1.7, 0.85), "spear": (2.5, 2.5, 1.0),
}


def display_of(model):
    return model["base"].get("display") if "base" in model else model.get("display")


class VisualScaleTests(unittest.TestCase):
    def test_scale_one_adds_no_display_block(self):
        for cat in CATS:
            m = weapon.model_json(cat, "m", "w")
            self.assertEqual(m, weapon.model_json(cat, "m", "w", 1.0))
            self.assertNotIn("display", json.dumps(m))

    def test_display_block_placement(self):
        for cat in CATS:
            m = weapon.model_json(cat, "m", "w", 0.8)
            if cat in LONG:
                self.assertNotIn("display", m)
                self.assertEqual(sorted(m["base"]["display"]), sorted(HAND_KEYS))
                for p in m["perspectives"].values():
                    self.assertNotIn("display", p)
                self.assertEqual(m["base"]["textures"], {"layer0": "m:item/w"})
                self.assertEqual(m["perspectives"], weapon.model_json(cat, "m", "w")["perspectives"])
            else:
                self.assertEqual(sorted(m["display"]), sorted(HAND_KEYS))
                self.assertEqual(m["textures"], {"layer0": "m:item/w"})
                self.assertEqual(m["parent"], weapon.model_json(cat, "m", "w")["parent"])

    def test_scale_multiplies_xy_and_keeps_rotation_and_z(self):
        for cat in CATS:
            for s in (0.5, 0.8, 1.3):
                d = display_of(weapon.model_json(cat, "m", "w", s))
                px, py, pz = PARENT_SCALE[cat]
                tp = d["thirdperson_righthand"]
                self.assertEqual(tp["scale"], [round(px * s, 4), round(py * s, 4), pz], (cat, s))
                one = display_of(weapon.model_json(cat, "m", "w", 0.999))
                for key in HAND_KEYS:
                    self.assertEqual(d[key]["rotation"], one[key]["rotation"], (cat, key))
                    self.assertEqual(d[key]["scale"][2], one[key]["scale"][2], (cat, key))

    def test_known_parent_values(self):
        d = display_of(weapon.model_json("longsword", "m", "w", 0.5))
        self.assertEqual(d["thirdperson_righthand"]["scale"], [0.75, 0.75, 0.85])
        self.assertEqual(d["thirdperson_righthand"]["rotation"], [0.0, -90.0, 40.0])
        self.assertEqual(d["firstperson_righthand"]["scale"], [0.85, 0.85, 0.85])
        d = display_of(weapon.model_json("sword", "m", "w", 0.5))
        self.assertEqual(d["thirdperson_righthand"]["scale"], [0.425, 0.425, 0.85])
        self.assertEqual(d["firstperson_lefthand"]["rotation"], [0.0, 90.0, -25.0])

    def test_the_grip_stays_in_the_hand(self):
        for cat in CATS:
            grip = sprites.hold_point(cat)
            self.assertTrue(abs(grip[0]) <= 8 and abs(grip[1]) <= 8, cat)
            ref = weapon.display_json(cat, 1.0)  # scale 1.0 reproduces the parent transforms
            for s in (0.4, 0.7, 0.82, 1.25, 1.5):
                d = weapon.display_json(cat, s)
                for key in HAND_KEYS:
                    left = "left" in key
                    want = grip_in_hand(ref[key], left, grip)
                    got = grip_in_hand(d[key], left, grip)
                    for a, b in zip(want, got):
                        self.assertAlmostEqual(a, b, delta=0.002, msg="%s %s s=%s" % (cat, key, s))
                # the translation really moved (otherwise the grip would drift with the scale)
                self.assertNotEqual(d["thirdperson_righthand"]["translation"], ref["thirdperson_righthand"]["translation"], cat)

    def test_translation_numbers_are_clean_floats(self):
        text = json.dumps(weapon.model_json("tachi", "m", "w", 0.73))
        self.assertNotIn("-0.0,", text)
        for cat in CATS:
            for entry in weapon.display_json(cat, 0.77).values():
                for v in entry["translation"] + entry["scale"] + entry["rotation"]:
                    self.assertIsInstance(v, float)
                    self.assertEqual(v, round(v, 4))

    def test_explicit_visual_scale_in_standalone_weapons(self):
        files, _ = standalone("greatsword", visual_scale=0.6)
        m = jload(files, "assets/testmod/models/item/test_blade.json")
        self.assertEqual(m["base"]["display"]["thirdperson_righthand"]["scale"], [1.02, 1.02, 0.85])
        self.assertNotIn("display", m["perspectives"]["gui"])
        files, _ = standalone("greatsword")
        self.assertNotIn("display", jload(files, "assets/testmod/models/item/test_blade.json")["base"])
        files, _ = standalone("sword", visual_scale=1.0)
        self.assertNotIn("display", jload(files, "assets/testmod/models/item/test_blade.json"))

    def test_boss_main_weapon_shrinks_with_the_mesh_scale(self):
        n, files, _ = boss("major")
        self.assertEqual(n["derived"]["scale"], 1.5)
        d = jload(files, "assets/roundtable/models/item/excalibur.json")["base"]["display"]
        self.assertEqual(d["thirdperson_righthand"]["scale"], [round(1.5 * 0.82, 4)] * 2 + [0.85])
        self.assertEqual(d["firstperson_righthand"]["scale"], [round(1.7 * 0.82, 4)] * 2 + [0.85])

    def test_auto_scale_rule(self):
        for scale, want in ((1.0, 1.0), (1.2, 1.0), (1.21, 0.91), (1.5, 0.82), (2.0, 0.71), (3.0, 0.7), (5.0, 0.7)):
            self.assertEqual(weapon.effective_visual_scale({}, scale, True), want, scale)
        self.assertEqual(weapon.effective_visual_scale({}, 2.0, False), 1.0)
        self.assertEqual(weapon.effective_visual_scale({}, None, True), 1.0)
        self.assertEqual(weapon.effective_visual_scale({"visual_scale": 1.4}, 2.0, True), 1.4)
        self.assertEqual(weapon.effective_visual_scale({"visual_scale": 0.5}, 1.0, False), 0.5)

    def test_auto_scale_only_touches_the_main_weapon_and_respects_explicit_values(self):
        raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
        second = {"id": "rusty_knife", "display_name": "Rusty Knife", "category": "dagger"}
        third = {"id": "old_axe", "display_name": "Old Axe", "category": "axe", "visual_scale": 0.5}
        n = spec.normalize(dict(raw, weapons=raw["weapons"] + [second, third]))
        for scale, expect_display in ((1.1, False), (2.0, True)):
            n["derived"]["scale"] = scale
            with tempfile.TemporaryDirectory() as td:
                ctx = spec.make_context(td, n["mod_id"], "com.bossforge.roundtable", td)
                files = {f.relpath: f for f in weapon.generate(n, ctx)[0]}
            main = jload(files, "assets/roundtable/models/item/excalibur.json")
            self.assertEqual("display" in main["base"], expect_display, scale)
            self.assertNotIn("display", jload(files, "assets/roundtable/models/item/rusty_knife.json"))
            self.assertEqual(jload(files, "assets/roundtable/models/item/old_axe.json")["display"]["thirdperson_righthand"]["scale"],
                             [0.425, 0.425, 0.85])
        n["main_weapon"] = None
        n["derived"]["scale"] = 2.0
        with tempfile.TemporaryDirectory() as td:
            ctx = spec.make_context(td, n["mod_id"], "com.bossforge.roundtable", td)
            files = {f.relpath: f for f in weapon.generate(n, ctx)[0]}
        self.assertNotIn("display", jload(files, "assets/roundtable/models/item/excalibur.json")["base"])

    def test_explicit_scale_beats_the_automatic_one_on_the_main_weapon(self):
        raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
        weapons = copy.deepcopy(raw["weapons"])
        weapons[0]["visual_scale"] = 1.0
        n, files, _ = boss("major", weapons=weapons)
        self.assertNotIn("display", jload(files, "assets/roundtable/models/item/excalibur.json")["base"])
        weapons[0]["visual_scale"] = 0.5
        n, files, _ = boss("major", weapons=weapons)
        d = jload(files, "assets/roundtable/models/item/excalibur.json")["base"]["display"]
        self.assertEqual(d["thirdperson_righthand"]["scale"], [0.75, 0.75, 0.85])


def dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def angle_deg(a, b):
    return math.degrees(math.acos(max(-1.0, min(1.0, dot(a, b)))))


def staff_ends(appearance):
    g = sprites.staff_geometry(appearance)
    return g, g["butt"], g["tip"]


class HoldPoseTests(unittest.TestCase):
    """The hand frames and the solver behind a staff's `thirdperson_righthand` display transform."""

    def test_stored_frames_are_rotations(self):
        d = spec.data("hold-poses")
        self.assertEqual(d["reference_height"], 1.8)
        self.assertIn("hold_spear", d["poses"])
        for name, pose in d["poses"].items():
            ax = pose["axes"]
            for i in range(3):
                for j in range(3):
                    self.assertAlmostEqual(dot(ax[i], ax[j]), 1.0 if i == j else 0.0, delta=3e-3, msg=(name, i, j))
            x, y, z = ax
            cross = (x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0])
            self.assertGreater(dot(cross, z), 0.99, name)  # right-handed
            origin, rot = holdpose.hand_frame(name)
            self.assertTrue(0.6 < origin[1] < 1.2, name)  # the hand is hip to chest high

    def test_stored_frames_are_what_the_epic_fight_files_give(self):
        try:
            derived = holdpose.derive_frames(offline=True)
        except BossForgeError as e:
            self.skipTest("Epic Fight assets not available: %s" % e)
        stored = spec.data("hold-poses")["poses"]
        self.assertEqual(sorted(derived), sorted(stored))
        for name, pose in derived.items():
            for a, b in zip(pose["origin"], stored[name]["origin"]):
                self.assertAlmostEqual(a, b, delta=1e-3, msg=name)
            for ra, rb in zip(pose["axes"], stored[name]["axes"]):
                for a, b in zip(ra, rb):
                    self.assertAlmostEqual(a, b, delta=1e-3, msg=name)
            self.assertAlmostEqual(pose["loop_seconds"], stored[name]["loop_seconds"], delta=1e-3)

    def test_tool_r_origin_matches_what_the_engine_reported(self):
        # `bossforge render-test` printed Epic Fight's own Tool_R pose matrix while the Nightingale boss idled in hold_spear:
        # origin (0.250, 0.826, 0.415) in body space (RT_POSE, EF 20.14.17). The stored item frame is pose * T(0,0,-0.13) * Rx(-90).
        origin, rf = holdpose.hand_frame("hold_spear")
        rw = holdpose._mul(rf, holdpose.rot_xyz(90, 0, 0))
        tool = [origin[i] + 0.13 * rw[i][2] for i in range(3)]
        for got, want in zip(tool, (0.250, 0.826, 0.415)):
            self.assertAlmostEqual(got, want, delta=0.01)

    def test_minecraft_rotation_order_and_euler_round_trip(self):
        s = math.sqrt(0.5)
        # Epic Fight's own spear display [0,-90,35] turns the sprite diagonal (up-right) about Z first, then Y
        v = holdpose._apply(holdpose.rot_xyz(0, -90, 35), (s, s, 0.0))
        for got, want in zip(v, (0.0, math.sin(math.radians(80)), math.cos(math.radians(80)))):
            self.assertAlmostEqual(got, want, places=6)
        self.assertAlmostEqual(holdpose._apply(holdpose.rot_xyz(0, 0, 90), (1, 0, 0))[1], 1.0, places=6)
        self.assertAlmostEqual(holdpose._apply(holdpose.rot_xyz(90, 0, 0), (0, 1, 0))[2], 1.0, places=6)
        for rx in (-170, -90, -33.5, 0, 12, 90, 160):
            for ry in (-80, -45, 0, 30, 85):
                for rz in (-120, -35, 0, 55, 179):
                    m = holdpose.rot_xyz(rx, ry, rz)
                    back = holdpose.rot_xyz(*holdpose.euler_xyz(m))
                    for r1, r2 in zip(m, back):
                        for a, b in zip(r1, r2):
                            self.assertAlmostEqual(a, b, places=6, msg=(rx, ry, rz))

    def test_epic_fights_spear_display_lays_a_staff_across_the_body(self):
        # the bug the user saw: the stock epicfight:item/spear transform, in the hold_spear pose, points the sprite axis sideways
        ef_spear = {"rotation": [0, -90, 35], "translation": [0.0, 2, 1.5], "scale": [2.5, 2.5, 1.0]}
        g, butt, tip = staff_ends({"shape": "crystal_staff"})
        axis = holdpose.direction(ef_spear, butt, tip)
        self.assertGreater(abs(axis[0]), 0.95)  # along the wielder's left-right axis
        self.assertLess(abs(axis[1]), 0.2)  # not upright at all
        pb, pt = holdpose.place(ef_spear, butt), holdpose.place(ef_spear, tip)
        self.assertGreater(math.dist(pb, pt), 3.0)  # and 3.5 blocks long: twice the wielder's height

    def test_staff_stands_upright_in_the_hold_pose(self):
        for shape in STAFF_SHAPES:
            for length in (0.8, 1.0, 1.15):
                ap = {"shape": shape, "length": length}
                g, butt, tip = staff_ends(ap)
                for hold in ({}, {"lean": 0}, {"lean": 25, "side": 15}, {"lean": -10, "side": -20}):
                    d = holdpose.staff_display(ap, 1.0, hold)
                    want = holdpose._target_axis(hold.get("lean", holdpose.DEFAULT_LEAN), hold.get("side", 0.0))
                    got = holdpose.direction(d, butt, tip)
                    self.assertLess(angle_deg(got, want), 0.05, (shape, length, hold))
                    # the sprite plane faces the wielder's front, so the weapon sheet's front face reads from the front
                    self.assertGreater(dot(holdpose.normal(d), (0, 0, -1)), 0.9, (shape, length, hold))
                    # the staff is as long as the stock biped is tall (1.8 blocks at entity scale 1)
                    self.assertAlmostEqual(math.dist(holdpose.place(d, butt), holdpose.place(d, tip)), 1.8, delta=0.003)

    def test_the_hand_grips_the_axis_at_the_grip_fraction(self):
        origin, _ = holdpose.hand_frame()
        for grip in (0.1, sprites.STAFF_GRIP, 0.8):
            ap = {"shape": "crystal_staff"}
            g, butt, tip = staff_ends(ap)
            d = holdpose.staff_display(ap, 1.0, {"grip": grip})
            pt = (butt[0] + grip * (tip[0] - butt[0]), butt[1] + grip * (tip[1] - butt[1]))
            for a, b in zip(holdpose.place(d, pt), origin):
                self.assertAlmostEqual(a, b, delta=0.002, msg=grip)

    def test_visual_scale_is_the_staff_length_in_wielder_heights(self):
        ap = {"shape": "orb_staff"}
        g, butt, tip = staff_ends(ap)
        for s in (0.4, 0.85, 1.0, 1.3):
            d = holdpose.staff_display(ap, s)
            self.assertAlmostEqual(math.dist(holdpose.place(d, butt), holdpose.place(d, tip)), 1.8 * s, delta=0.003)
            self.assertEqual(d["scale"][0], d["scale"][1])
            self.assertEqual(d["scale"][2], 1.0)
        with self.assertRaises(BossForgeError) as cm:
            holdpose.staff_display(ap, 4.0)  # display scale would pass Minecraft's clamp of 4
        self.assertEqual(cm.exception.err.code, "out_of_range")

    def test_facing_side_and_offset(self):
        ap = {"shape": "crystal_staff"}
        g, butt, tip = staff_ends(ap)
        side = holdpose.staff_display(ap, 1.0, {"facing": "side"})
        self.assertGreater(dot(holdpose.normal(side), (1, 0, 0)), 0.9)
        self.assertLess(angle_deg(holdpose.direction(side, butt, tip), holdpose._target_axis(10, 0)), 0.05)
        base = holdpose.staff_display(ap, 1.0)
        moved = holdpose.staff_display(ap, 1.0, {"offset": [0.1, 0.0, -0.2]})
        mid = (0.5, 0.5)
        _, rf = holdpose.hand_frame()
        want = holdpose._apply(rf, (0.1, 0.0, -0.2))
        for a, b, w in zip(holdpose.place(moved, mid), holdpose.place(base, mid), want):
            self.assertAlmostEqual(a - b, w, delta=0.002)
        self.assertEqual(moved["rotation"], base["rotation"])

    def test_other_poses_stay_close_to_upright(self):
        # the model is solved for hold_spear (the pose EF plays for a held spear/staff, mobs and players); the same display
        # in the other poses a staff wielder shows (measured with the stored frames): at most ~15 degrees from the solved lean
        ap = {"shape": "crystal_staff"}
        g, butt, tip = staff_ends(ap)
        d = holdpose.staff_display(ap)
        for pose, limit in (("hold_spear", 10.5), ("idle", 15.0), ("bind", 15.0), ("walk_spear", 17.0), ("walk", 22.0)):
            axis = holdpose.direction(d, butt, tip, pose)
            self.assertLess(angle_deg(axis, (0, 1, 0)), limit, pose)

    def test_the_grip_is_within_the_hand_for_every_pose(self):
        ap = {"shape": "crystal_staff"}
        g, butt, tip = staff_ends(ap)
        d = holdpose.staff_display(ap)
        grip = (butt[0] + sprites.STAFF_GRIP * (tip[0] - butt[0]), butt[1] + sprites.STAFF_GRIP * (tip[1] - butt[1]))
        for pose in holdpose.pose_names():
            self.assertLess(math.dist(holdpose.place(d, grip, pose), holdpose.hand_frame(pose)[0]), 0.35, pose)

    def test_minecraft_limits_are_respected(self):
        for shape in STAFF_SHAPES:
            for length in (0.8, 1.15):
                for hold in ({}, {"offset": [1, 1, 1]}, {"offset": [-1, -1, -1], "grip": 0.8}, {"lean": 60, "side": 45}):
                    d = holdpose.staff_display({"shape": shape, "length": length}, 1.5, hold)
                    self.assertTrue(all(abs(v) <= 4.0 for v in d["scale"]))
                    self.assertTrue(all(abs(v) <= 80.0 for v in d["translation"]))
                    for v in d["rotation"] + d["translation"] + d["scale"]:
                        self.assertIsInstance(v, float)
                        self.assertTrue(math.isfinite(v))
                        self.assertNotEqual(repr(v), "-0.0")

    def test_the_sprite_really_lies_on_the_axis_the_solver_uses(self):
        for shape in STAFF_SHAPES:
            ap = dict(STAFF, shape=shape, glow=True)
            g = sprites.staff_geometry(ap)
            cv = sprites.render_weapon("spear", ap, 64, 5)

            def pix(p, along=0.0):
                x = p[0] + along * g["axis"][0]
                y = p[1] + along * g["axis"][1]
                return cv.get(int(x * 64), int((1.0 - y) * 64))

            # the axis ends on opaque pixels and nothing opaque follows 4 pixels beyond either end
            self.assertEqual(pix(g["tip"], -1 / 64.0)[3], 255, shape)
            self.assertEqual(pix(g["butt"], 1 / 64.0)[3], 255, shape)
            self.assertLess(pix(g["tip"], 4 / 64.0)[3], 255, shape)
            self.assertLess(pix(g["butt"], -4 / 64.0)[3], 255, shape)
            # the orb sits at 60-80 % of the axis and its centre is the bright gem core
            frac = math.dist(g["butt"], g["orb"]) / math.dist(g["butt"], g["tip"])
            self.assertTrue(0.6 < frac < 0.8, (shape, frac))
            core = pix(g["orb"])
            self.assertEqual(core[3], 255)
            self.assertGreater(min(core[:3]), 150, (shape, core))
            self.assertGreaterEqual(core[2], core[0])

    def test_axis_length_follows_the_length_option(self):
        lens = [sprites.staff_geometry({"shape": "crystal_staff", "length": v})["length"] for v in (0.8, 0.9, 1.0, 1.1, 1.15)]
        self.assertTrue(all(b >= a - 1e-9 for a, b in zip(lens, lens[1:])), lens)  # the longest options hit the canvas cap together
        self.assertLess(lens[0], lens[-1])
        self.assertLess(lens[-1], math.sqrt(2.0))  # inside the unit square
        self.assertEqual(sprites.staff_geometry({"shape": "crystal_staff", "length": 1.0})["axis"], (math.sqrt(0.5), math.sqrt(0.5)))

    def test_bad_inputs(self):
        self.assertTrue(sprites.is_staff("spear", {"shape": "orb_staff"}))
        self.assertFalse(sprites.is_staff("spear", {"shape": "leaf"}))
        self.assertFalse(sprites.is_staff("spear", None))
        self.assertFalse(sprites.is_staff("sword", {"shape": "crystal_staff"}))
        with self.assertRaises(ValueError):
            sprites.staff_geometry({"shape": "leaf"})
        with self.assertRaises(BossForgeError):
            holdpose.hand_frame("flying_carpet")
        for bad, code in (({"lean": 99}, "out_of_range"), ({"lean": "up"}, "bad_type"), ({"facing": "back"}, "unknown_value"),
                          ({"offset": [0, 0]}, "bad_type"), ({"offset": [2, 0, 0]}, "bad_type"), ({"turn": 1}, "unknown_key"),
                          ({"grip": 0.95}, "out_of_range"), ("fast", "bad_type")):
            errs = holdpose.check_hold(bad)
            self.assertEqual([e[1] for e in errs][:1], [code], bad)
            if isinstance(bad, dict):
                with self.assertRaises(BossForgeError):
                    holdpose.staff_display({"shape": "crystal_staff"}, 1.0, bad)
        self.assertEqual(holdpose.check_hold(None), [])
        self.assertEqual(holdpose.check_hold({}), [])


class StaffModelTests(unittest.TestCase):
    """The generated item model of a staff: shape of the JSON, display validity, untouched perspectives."""

    PARENT = "epicfight:item/spear"

    def staff_model(self, visual_scale=1.0, hold=None, shape="crystal_staff"):
        return weapon.model_json("spear", "m", "w", visual_scale, dict(STAFF, shape=shape), hold)

    def test_staff_always_gets_a_hand_display_even_at_scale_one(self):
        for shape in STAFF_SHAPES:
            m = self.staff_model(1.0, None, shape)
            self.assertEqual(m["loader"], "forge:separate_transforms")
            self.assertEqual(m["base"]["parent"], self.PARENT)
            self.assertEqual(sorted(m["base"]["display"]), sorted(HAND_KEYS))
            self.assertNotIn("display", m)
            self.assertEqual(m["base"]["textures"], {"layer0": "m:item/w"})

    def test_other_spear_shapes_keep_the_parent_model(self):
        for shape in ("leaf", "trident", "hooked", "broad"):
            self.assertEqual(weapon.model_json("spear", "m", "w", 1.0, {"shape": shape}), weapon.model_json("spear", "m", "w", 1.0))
            self.assertNotIn("display", json.dumps(weapon.model_json("spear", "m", "w", 1.0, {"shape": shape})))

    def test_only_the_right_hand_entry_differs_from_the_scaled_parent(self):
        for s in (0.7, 1.0, 1.2):
            staff = self.staff_model(s)["base"]["display"]
            plain = weapon.display_json("spear", s)
            for key in HAND_KEYS:
                if key == "thirdperson_righthand":
                    self.assertNotEqual(staff[key], plain[key])
                    self.assertEqual(staff[key], holdpose.staff_display(dict(STAFF), s, None))
                else:
                    self.assertEqual(staff[key], plain[key], key)

    def test_inventory_ground_and_frame_perspectives_are_unchanged(self):
        m = self.staff_model(0.85, {"lean": 20})
        plain = weapon.model_json("spear", "m", "w")
        self.assertEqual(m["perspectives"], plain["perspectives"])
        self.assertEqual(m["gui_light"], "front")
        for p in m["perspectives"].values():
            self.assertNotIn("display", p)  # gui/ground/fixed keep the parent's transforms
            self.assertEqual(p["parent"], self.PARENT)
            self.assertEqual(p["textures"], {"layer0": "m:item/w_gui"})

    def test_the_json_is_valid_for_minecraft(self):
        text = json.dumps(self.staff_model(1.3, {"lean": 30, "side": 20, "offset": [0.2, -0.1, 0.1]}))
        data = json.loads(text)
        for key, entry in data["base"]["display"].items():
            self.assertEqual(sorted(entry), ["rotation", "scale", "translation"], key)
            for part, n in (("rotation", 3), ("translation", 3), ("scale", 3)):
                self.assertEqual(len(entry[part]), n, (key, part))
                for v in entry[part]:
                    self.assertIsInstance(v, float)
                    self.assertTrue(math.isfinite(v))
            self.assertTrue(all(abs(v) <= 4.0 for v in entry["scale"]), key)  # Minecraft clamps the scale to +-4
            self.assertTrue(all(abs(v) <= 80.0 for v in entry["translation"]), key)  # ... and the translation to +-80/16 blocks
        self.assertNotIn("NaN", text)
        self.assertNotIn("-0.0,", text)

    def test_hold_knobs_reach_the_model(self):
        base = self.staff_model()["base"]["display"]["thirdperson_righthand"]
        for hold in ({"lean": 0}, {"side": 12}, {"grip": 0.6}, {"facing": "side"}, {"offset": [0.1, 0, 0]}):
            self.assertNotEqual(self.staff_model(1.0, hold)["base"]["display"]["thirdperson_righthand"], base, hold)

    def test_standalone_staff_files_and_spec_validation(self):
        files, _ = standalone("spear", visual_scale=0.9, hold={"lean": 5}, appearance=dict(STAFF))
        m = jload(files, "assets/testmod/models/item/test_blade.json")
        self.assertEqual(m["base"]["display"]["thirdperson_righthand"], holdpose.staff_display(dict(STAFF), 0.9, {"lean": 5}))
        self.assertEqual(spec.validate_weapon_spec(weapon_spec("spear", appearance=dict(STAFF), hold={"lean": 5, "grip": 0.5})), [])
        codes = {e.path: e.code for e in spec.validate_weapon_spec(weapon_spec("spear", appearance=dict(STAFF), hold={"lean": 99, "x": 1}))}
        self.assertEqual(codes, {"/hold/lean": "out_of_range", "/hold/x": "unknown_key"})
        for cat, ap in (("sword", {}), ("spear", {"shape": "leaf"}), ("greatsword", {})):
            errs = spec.validate_weapon_spec(weapon_spec(cat, appearance=ap, hold={"lean": 5}))
            self.assertEqual([(e.path, e.code) for e in errs], [("/hold", "hold_not_applicable")], cat)

    def test_staffs_do_not_shrink_with_the_boss_scale(self):
        staff = {"category": "spear", "appearance": dict(STAFF)}
        for scale in (1.0, 1.5, 3.0):
            self.assertEqual(weapon.effective_visual_scale(staff, scale, True), 1.0)
        self.assertEqual(weapon.effective_visual_scale(dict(staff, visual_scale=0.8), 3.0, True), 0.8)
        self.assertEqual(weapon.effective_visual_scale({"category": "spear", "appearance": {"shape": "leaf"}}, 1.5, True), 0.82)

    def test_audit_flags_the_old_epic_fight_spear_transform_and_passes_the_generated_one(self):
        w = {"id": "w", "category": "spear", "appearance": dict(STAFF)}
        good = self.staff_model()
        self.assertEqual(weapon.audit_staff_model(good, w), [])
        self.assertEqual(weapon.audit_staff_model(self.staff_model(0.8, {"lean": 25, "side": -10}), dict(w, hold={"lean": 25, "side": -10})), [])
        # what the generator wrote before: the parent spear transform with the visual_scale applied (the user's giant sideways staff)
        old = weapon.model_json("spear", "m", "w", 0.85)
        self.assertEqual([c for c, _ in weapon.audit_staff_model(old, w)], ["qa_staff_lies_flat"])
        # the model does not match the weapon's hold knobs either
        self.assertEqual([c for c, _ in weapon.audit_staff_model(good, dict(w, hold={"lean": 40}))], ["qa_staff_lies_flat"])
        plain = weapon.model_json("spear", "m", "w", 1.0)
        self.assertEqual([c for c, _ in weapon.audit_staff_model(plain, w)], ["qa_staff_no_display"])
        self.assertEqual([c for c, _ in weapon.audit_staff_model(None, w)], ["qa_staff_no_display"])
        broken = json.loads(json.dumps(good))
        broken["base"]["display"]["thirdperson_righthand"]["scale"] = [9.0, 9.0, 1.0]
        self.assertEqual([c for c, _ in weapon.audit_staff_model(broken, w)], ["qa_staff_bad_display"])
        broken["base"]["display"]["thirdperson_righthand"]["scale"] = ["x", 1, 1]
        self.assertEqual([c for c, _ in weapon.audit_staff_model(broken, w)], ["qa_staff_bad_display"])
        for cat in ("sword", "spear", "greatsword"):
            self.assertEqual(weapon.audit_staff_model(None, {"category": cat, "appearance": {"shape": "leaf"} if cat == "spear" else {}}), [])

    def test_boss_staff_model_is_independent_of_the_mesh_scale(self):
        raw = json.loads(Path(_path.EXAMPLE).read_text(encoding="utf-8"))
        weapons = [dict(raw["weapons"][0], id="glow_staff", category="spear", appearance=dict(STAFF), hold={"lean": 8})]
        weapons[0].pop("visual_scale", None)
        n, files, _ = boss("major", weapons=weapons, main_weapon="glow_staff")
        self.assertEqual(n["derived"]["scale"], 1.5)  # a big boss: a plain weapon would shrink to 0.82
        d = jload(files, "assets/roundtable/models/item/glow_staff.json")["base"]["display"]["thirdperson_righthand"]
        self.assertEqual(d, holdpose.staff_display(dict(STAFF), 1.0, {"lean": 8}))


if __name__ == "__main__":
    unittest.main()
