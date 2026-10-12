"""Weapon generator: item sprites, models, Epic Fight capability, recipes, loot tables, lang keys.

`generate(n, ctx)` handles a normalized boss spec (all weapons + essence + sigil + spawn egg model + loot);
`generate_standalone(w, ctx)` handles one normalized weapon-only spec (see spec.normalize_weapon_spec).
Both return `(files, lang)` and never touch the disk.
"""
from __future__ import annotations

import json
import math
import zlib
from typing import Dict, List, Optional, Tuple

from . import holdpose, sprites
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


# Hand display transforms of each category's PARENT model, transcribed once from the Epic Fight 20.14.17 jar
# (assets/epicfight/models/item/{longsword,greatsword,tachi,spear,dagger}.json) and the vanilla 1.20.1
# client jar (assets/minecraft/models/item/handheld.json, used by sword and axe). Each entry is
# (rotation, translation in 1/16 block, scale); left-hand entries are stored as authored (Minecraft mirrors them on use).
_HANDHELD = {
    "thirdperson_righthand": ((0, -90, 55), (0, 4.0, 0.5), (0.85, 0.85, 0.85)),
    "thirdperson_lefthand": ((0, 90, -55), (0, 4.0, 0.5), (0.85, 0.85, 0.85)),
    "firstperson_righthand": ((0, -90, 25), (1.13, 3.2, 1.13), (0.68, 0.68, 0.68)),
    "firstperson_lefthand": ((0, 90, -25), (1.13, 3.2, 1.13), (0.68, 0.68, 0.68)),
}
_PARENT_DISPLAY = {
    "sword": _HANDHELD,
    "axe": _HANDHELD,
    "dagger": {
        "thirdperson_righthand": ((0, -90, 35), (0, 2, 0.3), (0.8, 0.8, 1.0)),
        "thirdperson_lefthand": ((0, 90, -35), (0, 2, 0.3), (0.8, 0.8, 1.0)),
        "firstperson_righthand": ((50, 90, 0), (2, 2.5, 0), (1.0, 1.0, 0.7)),
        "firstperson_lefthand": ((45, -90, 0), (2, 2.5, 0), (1.0, 1.0, 0.7)),
    },
    "longsword": {
        "thirdperson_righthand": ((0, -90, 40), (0, 8.0, 2.0), (1.5, 1.5, 0.85)),
        "thirdperson_lefthand": ((0, 90, -40), (0, 8.0, 2.0), (1.5, 1.5, 0.85)),
        "firstperson_righthand": ((0, -90, 25), (1.13, 3.2, 1.13), (1.7, 1.7, 0.85)),
        "firstperson_lefthand": ((0, 90, -25), (1.13, 3.2, 1.13), (1.7, 1.7, 0.85)),
    },
    "greatsword": {
        "thirdperson_righthand": ((0, -90, 55), (0, 7.5, -2), (1.7, 1.7, 0.85)),
        "thirdperson_lefthand": ((0, 90, -55), (0, 7.5, -2), (1.7, 1.7, 0.85)),
        "firstperson_righthand": ((0, -90, 25), (1.13, 3.2, 1.13), (1.2, 1.2, 0.85)),
        "firstperson_lefthand": ((0, 90, -25), (1.13, 3.2, 1.13), (1.2, 1.2, 0.85)),
    },
    "tachi": {
        "thirdperson_righthand": ((20, -90, 55), (0, 11.5, 2.5), (1.7, 1.7, 0.85)),
        "thirdperson_lefthand": ((0, 90, -55), (0, 13.25, -1.75), (1.7, 1.7, 0.85)),
        "firstperson_righthand": ((0, -90, 25), (1.13, 9.2, 1.13), (1.7, 1.7, 0.85)),
        "firstperson_lefthand": ((0, 90, -25), (1.13, 9.2, 1.13), (1.7, 1.7, 0.85)),
    },
    "spear": {
        "thirdperson_righthand": ((0, -90, 35), (0.0, 2, 1.5), (2.5, 2.5, 1.0)),
        "thirdperson_lefthand": ((0, 90, -35), (0.0, 2, 0.3), (1.5, 1.5, 1.0)),
        "firstperson_righthand": ((50, 90, 0), (2, 2.5, 0), (1.0, 1.0, 0.7)),
        "firstperson_lefthand": ((45, -90, 0), (2, 2.5, 0), (1.0, 1.0, 0.7)),
    },
}


def _rotate(rx: float, ry: float, rz: float, v):
    """Minecraft item rotation (degrees): a vector is turned about Z first, then Y, then X."""
    x, y, z = v
    c, s = math.cos(math.radians(rz)), math.sin(math.radians(rz))
    x, y = x * c - y * s, x * s + y * c
    c, s = math.cos(math.radians(ry)), math.sin(math.radians(ry))
    x, z = x * c + z * s, -x * s + z * c
    c, s = math.cos(math.radians(rx)), math.sin(math.radians(rx))
    y, z = y * c - z * s, y * s + z * c
    return x, y, z


def display_json(category: str, visual_scale: float, appearance: Optional[dict] = None, hold: Optional[dict] = None) -> dict:
    """`display` block for the four hand perspectives with the parent's X/Y scale multiplied by `visual_scale`.

    Minecraft applies translate -> rotate -> scale, so shrinking the model about its centre would drag the grip away
    from the hand. The translation is moved by R * ((1-s) * S * g), g being the sprite's grip point relative to the
    model centre, which keeps the grip exactly where the parent model puts it. Left-hand entries are first converted
    to the transform Minecraft really applies (it negates their Y/Z rotation and X translation) and converted back.

    Staff shapes (spear category) replace the parent's `thirdperson_righthand` entry: Epic Fight's spear transform lays
    the sprite diagonal across the hold_spear pose, a staff must stand upright; `holdpose.staff_display` solves it
    (`hold` = the weapon's `hold` knobs, `visual_scale` = staff length in wielder heights).
    """
    gx, gy = sprites.hold_point(category)
    s = float(visual_scale)
    out: Dict[str, dict] = {}
    for key, (rot, tr, sc) in _PARENT_DISPLAY[category].items():
        left = "lefthand" in key
        rx, ry, rz = float(rot[0]), float(rot[1]), float(rot[2])
        tx, ty, tz = float(tr[0]), float(tr[1]), float(tr[2])
        if left:
            ry, rz, tx = -ry, -rz, -tx
        dx, dy, dz = _rotate(rx, ry, rz, ((1.0 - s) * sc[0] * gx, (1.0 - s) * sc[1] * gy, 0.0))
        tx, ty, tz = tx + dx, ty + dy, tz + dz
        if left:
            tx = -tx
        out[key] = {
            "rotation": [float(rot[0]), float(rot[1]), float(rot[2])],
            "translation": [round(max(-80.0, min(80.0, v)), 4) + 0.0 for v in (tx, ty, tz)],
            "scale": [round(min(4.0, sc[0] * s), 4), round(min(4.0, sc[1] * s), 4), float(sc[2])],
        }
    if sprites.is_staff(category, appearance):
        out["thirdperson_righthand"] = holdpose.staff_display(appearance, s, hold)
    return out


def effective_visual_scale(w: dict, boss_scale: Optional[float] = None, is_main: bool = False) -> float:
    """Explicit `visual_scale` wins. Otherwise the boss's main weapon shrinks by 1/sqrt(mesh scale) once the mesh
    scale exceeds 1.2 (clamped 0.7-1.0; the mesh scale already enlarges whatever the boss holds). Else 1.0.
    Staffs never shrink: their held size is solved relative to the wielder (`holdpose.staff_display`) and defaults to
    the shape's own length (1.0 biped height, the diamond staff 0.3: `sprites.staff_default`)."""
    explicit = w.get("visual_scale")
    if explicit is not None:
        return float(explicit)
    if sprites.is_staff(w.get("category"), w.get("appearance")):
        return sprites.staff_default(w.get("appearance"), "visual_scale")
    if is_main and boss_scale is not None and boss_scale > 1.2:
        return max(0.7, min(1.0, round(1.0 / math.sqrt(boss_scale), 2)))
    return 1.0


def model_json(category: str, mod_id: str, item_id: str, visual_scale: float = 1.0, appearance: Optional[dict] = None,
               hold: Optional[dict] = None) -> dict:
    """Item model JSON for a weapon: handheld/dagger parent models or Epic Fight's separate_transforms.

    A `visual_scale` other than 1.0 adds a hand-perspective `display` block (top level for plain parents, inside
    `base` for separate_transforms so gui/fixed/ground keep the parent's look). Staff shapes always get one."""
    m = _cat(category)["model"]
    tex = "%s:item/%s" % (mod_id, item_id)
    staff = sprites.is_staff(category, appearance)
    display = display_json(category, visual_scale, appearance, hold) if staff or abs(float(visual_scale) - 1.0) > 1e-9 else None
    if m["kind"] in ("handheld", "epic_parent"):
        out = {"parent": m["parent"]}
        if display:
            out["display"] = display
        out["textures"] = {"layer0": tex}
        return out
    gui = {"parent": m["parent"], "textures": {"layer0": tex + "_gui"}}
    base = {"parent": m["parent"]}
    if display:
        base["display"] = display
    base["textures"] = {"layer0": tex}
    return {
        "loader": "forge:separate_transforms",
        "gui_light": "front",
        "base": base,
        "perspectives": {"gui": gui, "fixed": dict(gui), "ground": dict(gui)},
    }


def audit_staff_model(model: object, w: dict) -> List[Tuple[str, str]]:
    """Problems with the held-item transform of a staff's generated item model, as (qa code, message); [] for other weapons.

    A model generated before staffs got their own transform parents `epicfight:item/spear` unchanged: Epic Fight's
    hold_spear pose then lays the sprite sideways across the body (the symptom: a giant horizontal staff). This
    recomputes where the right-hand display puts the sprite axis and compares it with what the weapon's `hold`
    asks for."""
    if not sprites.is_staff(w.get("category"), w.get("appearance")):
        return []
    base = model.get("base") if isinstance(model, dict) else None
    entry = ((base or {}).get("display") or {}).get("thirdperson_righthand")
    keys = ("rotation", "translation", "scale")
    if not isinstance(entry, dict) or any(not isinstance(entry.get(k), list) or len(entry[k]) != 3 for k in keys):
        return [("qa_staff_no_display", "the staff's item model has no `base.display.thirdperson_righthand` transform, Epic Fight's spear transform "
                 "would lay it sideways in the hand; rerun add-boss")]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for k in keys for v in entry[k]):
        return [("qa_staff_bad_display", "the staff's right-hand display holds a non-numeric or non-finite value")]
    if any(abs(v) > 4.0 for v in entry["scale"]) or any(abs(v) > 80.0 for v in entry["translation"]):
        return [("qa_staff_bad_display", "the staff's right-hand display exceeds Minecraft's clamps (scale +-4, translation +-80); Minecraft would silently clip it")]
    geo = sprites.staff_geometry(w.get("appearance"))
    hold = w.get("hold") or {}
    want = holdpose._target_axis(float(hold.get("lean", holdpose.DEFAULT_LEAN)), float(hold.get("side", 0.0)))
    got = holdpose.direction(entry, geo["butt"], geo["tip"])
    off = math.degrees(math.acos(max(-1.0, min(1.0, sum(a * b for a, b in zip(got, want))))))
    if off > 5.0:
        return [("qa_staff_lies_flat", "in the hold_spear pose the staff's sprite axis is %.0f degrees away from the %.0f/%.0f degree lean the weapon asks "
                 "for (Epic Fight's own spear transform lays it sideways); rerun add-boss" % (off, float(hold.get("lean", holdpose.DEFAULT_LEAN)),
                                                                                         float(hold.get("side", 0.0))))]
    return []


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


def _weapon_files(w: dict, mod: str, essence_ref: str, palette: Optional[dict],
                  visual_scale: float = 1.0) -> Tuple[List[GenFile], Dict[str, str], Canvas]:
    wid = w["id"]
    category = w.get("category")
    cat = _cat(category)
    seed = zlib.crc32(wid.encode("utf-8"))
    try:
        size = sprites.sprite_resolution(category, w.get("appearance"))
        sprite = sprites.render_weapon(category, w.get("appearance"), size, seed, palette)
    except sprites.ShapeError as e:
        raise BossForgeError("/weapons/%s/appearance" % wid, "unknown_value", str(e))
    except ValueError as e:
        raise BossForgeError("/weapons/%s/appearance" % wid, "bad_color", str(e))
    base = RES + "assets/%s/" % mod
    files = [GenFile(base + "textures/item/%s.png" % wid, _png(sprite), "texture")]
    if cat["model"]["kind"] == "separate_transforms":
        gui = sprite.downsampled(2)
        files.append(GenFile(base + "textures/item/%s_gui.png" % wid, _png(gui), "texture"))
    files.append(GenFile(base + "models/item/%s.json" % wid, _dump(model_json(category, mod, wid, visual_scale, w.get("appearance"), w.get("hold"))), "json"))
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
    boss_scale = (n.get("derived") or {}).get("scale")
    for w in n.get("weapons") or []:
        scale = effective_visual_scale(w, boss_scale, bool(n.get("main_weapon")) and w.get("id") == n.get("main_weapon"))
        wf, wl, sp = _weapon_files(w, mod, essence_ref, palette, scale)
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
    files, lang, sprite = _weapon_files(w, mod, essence, None, effective_visual_scale(w))
    files.append(_preview([sprite], "bossforge/previews/%s_weapon.png" % w["id"]))
    return files, lang
