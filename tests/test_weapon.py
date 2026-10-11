import _path  # noqa
import copy
import json
import tempfile
import unittest
from pathlib import Path

from bossforge_core import spec, sprites, weapon
from bossforge_core.pngio import decode_png, png_size
from bossforge_core.spec import BossForgeError, Err

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


if __name__ == "__main__":
    unittest.main()
