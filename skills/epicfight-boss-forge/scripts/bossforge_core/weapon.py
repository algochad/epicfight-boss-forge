"""Weapon generator: item sprites, models, Epic Fight capability, recipes, loot tables, lang keys.

`generate(n, ctx)` handles a normalized boss spec (all weapons + essence + sigil + spawn egg model + loot);
`generate_standalone(w, ctx)` handles one normalized weapon-only spec (see spec.normalize_weapon_spec).
Both return `(files, lang)` and never touch the disk.
"""
from __future__ import annotations

import json
import zlib
from typing import Dict, List, Optional, Tuple

from . import sprites
from .pngio import Canvas, hconcat, write_png
from .spec import BossForgeError, GenFile, RES_ID_RE, Err, data, validate_recipe

RES = "src/main/resources/"
PREVIEW_BG = (36, 40, 50, 255)
PREVIEW_SCALE = 8

# default recipe pattern per category; E = essence, S = stick, N = netherite ingot
_PATTERNS = {
    "sword": [" E ", " E ", " S "],
    "dagger": [" E ", " S "],
    "axe": ["EE ", "ES ", " S "],
    "longsword": [" E ", "NEN", " S "],
    "tachi": [" E ", "NEN", " S "],
    "greatsword": ["EEE", " N ", " S "],
    "spear": ["  E", " S ", "S  "],
}
_FIXED_KEYS = {"S": "minecraft:stick", "N": "minecraft:netherite_ingot"}


def _dump(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _cat(category: str) -> dict:
    cats = data("weapon-categories")
    if category not in cats:
        raise BossForgeError("/category", "unknown_value", "%r is not allowed; allowed values: %s" % (category, ", ".join(cats)))
    return cats[category]


# --------------------------------------------------------------------------------------------- pure builders
def default_recipe(category: str, essence_item: str) -> dict:
    """Default shaped recipe `{pattern, key}` (key maps a char to an item id) for a weapon category."""
    _cat(category)
    pattern = list(_PATTERNS[category])
    used = set("".join(pattern))
    key: Dict[str, str] = {}
    for ch, item in (("E", essence_item), ("S", _FIXED_KEYS["S"]), ("N", _FIXED_KEYS["N"])):
        if ch in used:
            key[ch] = item
    return {"pattern": pattern, "key": key}


def model_json(category: str, mod_id: str, item_id: str) -> dict:
    """Item model JSON for a weapon: handheld/dagger parent models or Epic Fight's separate_transforms."""
    m = _cat(category)["model"]
    tex = "%s:item/%s" % (mod_id, item_id)
    if m["kind"] in ("handheld", "epic_parent"):
        return {"parent": m["parent"], "textures": {"layer0": tex}}
    gui = {"parent": m["parent"], "textures": {"layer0": tex + "_gui"}}
    return {
        "loader": "forge:separate_transforms",
        "gui_light": "front",
        "base": {"parent": m["parent"], "textures": {"layer0": tex}},
        "perspectives": {"gui": gui, "fixed": dict(gui), "ground": dict(gui)},
    }


def _ef_block(src: dict, defaults: dict) -> dict:
    merged = dict(defaults)
    merged.update({k: v for k, v in src.items() if k in ("impact", "armor_negation", "max_strikes", "damage_bonus", "speed_bonus")})
    out = {
        "armor_negation": float(merged.get("armor_negation", 0.0)),
        "impact": float(merged.get("impact", 0.0)),
        "max_strikes": int(merged.get("max_strikes", 1)),
    }
    for k in ("damage_bonus", "speed_bonus"):
        if merged.get(k):
            out[k] = float(merged[k])
    return out


def capability_json(w: dict) -> dict:
    """data/<mod>/capabilities/weapons/<id>.json content. max_strikes is an int; every other number a float."""
    cat = _cat(w["category"])
    ef = w.get("ef") or {}
    attrs = {}
    for block, defaults in cat["ef_blocks"].items():
        src = ef if block == "common" else (ef.get(block) or {})
        attrs[block] = _ef_block(src, defaults)
    return {"type": cat["ef_type"], "attributes": attrs}


def recipe_json(mod_id: str, weapon_id: str, recipe: dict) -> dict:
    errs: List[Err] = []
    validate_recipe(errs, "/obtain/recipe", recipe)
    if errs:
        raise BossForgeError(errs[0].path, errs[0].code, errs[0].message)
    return {
        "type": "minecraft:crafting_shaped",
        "category": "equipment",
        "pattern": list(recipe["pattern"]),
        "key": {ch: {"item": item} for ch, item in recipe["key"].items()},
        "result": {"item": "%s:%s" % (mod_id, weapon_id), "count": 1},
    }


def _num(v):
    return int(v) if float(v) == int(v) else float(v)


def loot_table(n: dict) -> dict:
    """data/<mod>/loot_tables/entities/<boss>.json: one chance pool per droppable weapon plus the essence pool."""
    mod, boss = n["mod_id"], n["id"]
    pools = []
    for w in n.get("weapons") or []:
        drop = float((w.get("obtain") or {}).get("drop", 0.0))
        if drop > 0:
            pools.append({
                "rolls": 1,
                "entries": [{"type": "minecraft:item", "name": "%s:%s" % (mod, w["id"])}],
                "conditions": [{"condition": "minecraft:random_chance", "chance": drop}],
            })
    ess = (n.get("rewards") or {}).get("essence") or {"min": 1, "max": 1}
    pools.append({
        "rolls": {"type": "minecraft:uniform", "min": _num(ess["min"]), "max": _num(ess["max"])},
        "entries": [{"type": "minecraft:item", "name": "%s:%s_essence" % (mod, boss)}],
    })
    return {"type": "minecraft:entity", "pools": pools}


def sigil_chest_table(mod_id: str, boss_id: str) -> dict:
    return {
        "type": "minecraft:chest",
        "pools": [{"rolls": 1, "entries": [{"type": "minecraft:item", "name": "%s:%s_sigil" % (mod_id, boss_id)}]}],
    }


# --------------------------------------------------------------------------------------------- per weapon
def _png(canvas: Canvas) -> bytes:
    return write_png(None, canvas)


def _weapon_files(w: dict, mod: str, essence_ref: str, palette: Optional[dict]) -> Tuple[List[GenFile], Dict[str, str], Canvas]:
    wid = w["id"]
    category = w.get("category")
    cat = _cat(category)
    size = cat["model"]["size"]
    seed = zlib.crc32(wid.encode("utf-8"))
    try:
        sprite = sprites.render_weapon(category, w.get("appearance"), size, seed, palette)
    except ValueError as e:
        raise BossForgeError("/weapons/%s/appearance" % wid, "bad_color", str(e))
    base = RES + "assets/%s/" % mod
    files = [GenFile(base + "textures/item/%s.png" % wid, _png(sprite), "texture")]
    if cat["model"]["kind"] == "separate_transforms":
        gui = sprite.downsampled(2)
        files.append(GenFile(base + "textures/item/%s_gui.png" % wid, _png(gui), "texture"))
    files.append(GenFile(base + "models/item/%s.json" % wid, _dump(model_json(category, mod, wid)), "json"))
    files.append(GenFile(RES + "data/%s/capabilities/weapons/%s.json" % (mod, wid), _dump(capability_json(w)), "json"))
    ob = w.get("obtain") or {}
    if ob.get("craft"):
        recipe = ob.get("recipe") or default_recipe(category, essence_ref)
        files.append(GenFile(RES + "data/%s/recipes/%s.json" % (mod, wid), _dump(recipe_json(mod, wid, recipe)), "json"))
    lang = {"item.%s.%s" % (mod, wid): w.get("display_name") or wid}
    for i, line in enumerate(w.get("lore") or [], 1):
        lang["item.%s.%s.desc.%d" % (mod, wid, i)] = line
    return files, lang, sprite


def _item_model(mod: str, name: str) -> bytes:
    return _dump({"parent": "minecraft:item/generated", "textures": {"layer0": "%s:item/%s" % (mod, name)}})


def _preview(sprs: List[Canvas], relpath: str) -> GenFile:
    panels = []
    for sp in sprs:
        panel = Canvas(sp.w * PREVIEW_SCALE, sp.h * PREVIEW_SCALE)
        panel.fill(PREVIEW_BG)
        panel.blit(sp.scaled(PREVIEW_SCALE), 0, 0)
        panels.append(panel)
    return GenFile(relpath, _png(hconcat(panels, gap=8, bg=PREVIEW_BG)), "preview")


# --------------------------------------------------------------------------------------------- public API
def generate(n: dict, ctx) -> Tuple[List[GenFile], Dict[str, str]]:
    """All item/loot/recipe resources for a boss spec: weapons, essence, sigil, spawn egg model, loot tables, preview."""
    mod, boss = n["mod_id"], n["id"]
    palette = (n.get("appearance") or {}).get("palette") or {}
    essence_ref = "%s:%s_essence" % (mod, boss)
    files: List[GenFile] = []
    lang: Dict[str, str] = {}
    sprs: List[Canvas] = []
    for w in n.get("weapons") or []:
        wf, wl, sp = _weapon_files(w, mod, essence_ref, palette)
        files.extend(wf)
        lang.update(wl)
        sprs.append(sp)

    base = RES + "assets/%s/" % mod
    seed = n.get("seed")
    if seed is None:
        seed = zlib.crc32(boss.encode("utf-8"))
    glow = any(f.get("type") == "eyes" and f.get("glow") for f in (n.get("appearance") or {}).get("features") or [] if isinstance(f, dict))
    name = n.get("display_name") or boss

    files.append(GenFile(base + "textures/item/%s_essence.png" % boss, _png(sprites.render_essence(palette, glow, seed)), "texture"))
    files.append(GenFile(base + "models/item/%s_essence.json" % boss, _item_model(mod, boss + "_essence"), "json"))
    lang["item.%s.%s_essence" % (mod, boss)] = "%s's Essence" % name

    if (n.get("encounter") or {}).get("trigger") == "summon_item":
        files.append(GenFile(base + "textures/item/%s_sigil.png" % boss, _png(sprites.render_sigil(palette, seed)), "texture"))
        files.append(GenFile(base + "models/item/%s_sigil.json" % boss, _item_model(mod, boss + "_sigil"), "json"))
        lang["item.%s.%s_sigil" % (mod, boss)] = "%s Sigil" % (n.get("title") or name)

    if n.get("tier") != "hidden":
        files.append(GenFile(base + "models/item/%s_spawn_egg.json" % boss, _dump({"parent": "minecraft:item/template_spawn_egg"}), "json"))
        lang["item.%s.%s_spawn_egg" % (mod, boss)] = "%s Spawn Egg" % name

    files.append(GenFile(RES + "data/%s/loot_tables/entities/%s.json" % (mod, boss), _dump(loot_table(n)), "json"))
    if n.get("tier") == "hidden" and (n.get("encounter") or {}).get("trigger") == "summon_item":
        files.append(GenFile(RES + "data/%s/loot_tables/chests/%s_sigil.json" % (mod, boss), _dump(sigil_chest_table(mod, boss)), "json"))
    if sprs:
        files.append(_preview(sprs, "bossforge/previews/%s_weapons.png" % boss))
    return files, lang


def generate_standalone(w: dict, ctx, essence_item: Optional[str] = None) -> Tuple[List[GenFile], Dict[str, str]]:
    """Resources for one weapon-only spec. `E` of the default recipe is `essence_item`, else w['essence_item'], else diamond."""
    mod = w.get("mod_id") or ctx.mod_id
    essence = essence_item or w.get("essence_item") or "minecraft:diamond"
    if not RES_ID_RE.match(essence):
        raise BossForgeError("/essence_item", "bad_resource_id", "essence_item %r must be namespace:path" % (essence,))
    files, lang, sprite = _weapon_files(w, mod, essence, None)
    files.append(_preview([sprite], "bossforge/previews/%s_weapon.png" % w["id"]))
    return files, lang
