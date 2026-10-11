"""Java and encounter-resource generation for the Forge 1.20.1 scaffold.

* ``core_files(ctx)``: shared core classes and the static mod-level event classes (identical for every mod).
* ``generate(n, ctx)``: per-boss ``<Cls>Entity.java`` plus the encounter/progression resources and lang keys.
* ``generate_registry(project_root, ctx, manifest)``: ``BossRegistry.java`` for every boss and standalone weapon
  recorded in the project manifest.

Nothing here writes to disk; callers hand the returned ``GenFile`` list to ``manifest.write_files``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import TEMPLATES_DIR
from .spec import (BossForgeError, BuildContext, GenFile, data, jstr, load, normalize, normalize_weapon_spec,
                   pascal)

CORE_CLASSES = ("AbilitySpec", "AbstractBossEntity", "BossAbilities", "BossProfile", "BossRenderer",
                "BossSigilItem", "BossTier", "BossWeaponItem", "EffectSpec")
MOD_CLASSES = ("BossClientEvents", "BossForgeEvents", "BossModEvents")
REGISTRY_CLASS = "BossRegistry"

# Reserved words that cannot be package-name segments (contextual keywords such as `record` are legal).
JAVA_KEYWORDS = frozenset(
    "abstract assert boolean break byte case catch char class const continue default do double else enum extends "
    "final finally float for goto if implements import instanceof int interface long native new package private "
    "protected public return short static strictfp super switch synchronized this throw throws transient try void "
    "volatile while true false null _".split())

_PLACEHOLDER = re.compile(r"\{\{([A-Z_]+)\}\}")
_TEMPLATES: Dict[str, str] = {}

FRAME_BY_TIER = {"major": "challenge", "hidden": "challenge", "quest": "goal", "mini": "task"}
SIGIL_STACK_SIZE = 16


# --------------------------------------------------------------------------- templates
def load_template(kind: str, name: str) -> str:
    key = kind + "/" + name
    if key not in _TEMPLATES:
        p = TEMPLATES_DIR / kind / name
        try:
            _TEMPLATES[key] = p.read_text(encoding="utf-8")
        except OSError as e:
            raise BossForgeError("", "template_missing", "cannot read template %s: %s" % (p, e))
    return _TEMPLATES[key]


def render(text: str, values: Dict[str, str], name: str = "template") -> str:
    """Substitute ``{{NAME}}`` placeholders in one pass (substituted text is never rescanned)."""
    def sub(m):
        k = m.group(1)
        if k not in values:
            raise BossForgeError("", "template_placeholder", "%s uses unknown placeholder {{%s}}" % (name, k))
        return values[k]
    return _PLACEHOLDER.sub(sub, text)


def java_rel(ctx: BuildContext) -> str:
    """Project-relative source directory of the mod package, with forward slashes."""
    return "src/main/java/" + ctx.package.replace(".", "/")


def check_package_segment(segment: str, path: str, what: str) -> None:
    if segment in JAVA_KEYWORDS:
        raise BossForgeError(path, "reserved_java_word",
                             "%s %r is a Java keyword and cannot be used as a package name; pick another" % (what, segment))


def boss_class(boss_id: str) -> str:
    return pascal(boss_id)


def _check_boss_id(boss_id: str) -> str:
    check_package_segment(boss_id, "/id", "boss id")
    cls = boss_class(boss_id)
    if cls + "Entity" in set(CORE_CLASSES) | set(MOD_CLASSES) | {REGISTRY_CLASS}:
        raise BossForgeError("/id", "reserved_class_name",
                             "boss id %r would generate class %sEntity, which clashes with a generated core class" % (boss_id, cls))
    return cls


# --------------------------------------------------------------------------- java literals
def jlit(s: Optional[str]) -> str:
    return "null" if s is None else '"%s"' % jstr(s)


def jdouble(x) -> str:
    return repr(float(x)) + "D"


def jfloat(x) -> str:
    return repr(float(x)) + "F"


def jbool(b) -> str:
    return "true" if b else "false"


def _json(obj) -> bytes:
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# --------------------------------------------------------------------------- shared core
def core_files(ctx: BuildContext) -> List[GenFile]:
    """Shared core (``boss/core``) and static mod-level classes (``boss``), substituted for this mod."""
    base = java_rel(ctx)
    vals = {"PACKAGE": ctx.package, "MOD_ID": ctx.mod_id}
    files: List[GenFile] = []
    for name in CORE_CLASSES:
        text = render(load_template("java", name + ".java.tpl"), vals, name)
        files.append(GenFile("%s/boss/core/%s.java" % (base, name), text.encode("utf-8"), "java"))
    for name in MOD_CLASSES:
        text = render(load_template("java", name + ".java.tpl"), vals, name)
        files.append(GenFile("%s/boss/%s.java" % (base, name), text.encode("utf-8"), "java"))
    return files


# --------------------------------------------------------------------------- per-boss entity
def _require(n: dict, *keys):
    cur = n
    for k in keys:
        if not isinstance(cur, dict) or k not in cur or cur[k] is None:
            raise BossForgeError("/" + "/".join(keys), "required", "normalized spec is missing %s" % "/".join(keys))
        cur = cur[k]
    return cur


def _ability_java(a: dict) -> str:
    t = a.get("type")
    phases = "".join(", %d" % int(p) for p in (a.get("phases") or []))
    if t == "on_hit_effect":
        return "AbilitySpec.onHitEffect(%s, %d, %d, %s%s)" % (
            jlit(a["effect"]), int(a["duration"]), int(a["amplifier"]), jfloat(a["chance"]), phases)
    if t == "aura":
        return "AbilitySpec.aura(%s, %s, %d, %d, %d%s)" % (
            jlit(a["effect"]), jfloat(a["radius"]), int(a["interval"]), int(a["duration"]), int(a["amplifier"]), phases)
    if t == "summon":
        return "AbilitySpec.summon(%s, %d, %d, %s)" % (jlit(a["entity"]), int(a["count"]), int(a["phase"]), jfloat(a["radius"]))
    if t == "regen":
        return "AbilitySpec.regen(%s%s)" % (jfloat(a["per_second"]), phases)
    if t == "enrage":
        return "AbilitySpec.enrage(%d, %s, %s)" % (int(a["phase"]), jfloat(a["speed_pct"]), jfloat(a["damage_pct"]))
    raise BossForgeError("/abilities", "unknown_ability", "cannot generate ability %r" % (t,))


def profile_args(n: dict, mod_id: str) -> str:
    """Argument list (without the surrounding parentheses) of ``new BossProfile(...)``."""
    st = _require(n, "stats")
    for k in ("health", "armor", "armor_toughness", "attack_damage", "movement_speed", "knockback_resistance",
              "follow_range", "xp_reward"):
        _require(n, "stats", k)
    enc = _require(n, "encounter")
    bar = enc.get("boss_bar") or {}
    sounds = _require(n, "sounds")
    _require(n, "sounds", "hurt")
    _require(n, "sounds", "death")
    phases = n.get("phases") or []
    main = n.get("main_weapon")
    main_id = "%s:%s" % (mod_id, main) if main else None
    abilities = [_ability_java(a) for a in (n.get("abilities") or [])]
    if abilities:
        abil = "List.of(\n" + ",\n".join("                " + a for a in abilities) + ")"
    else:
        abil = "List.of()"
    lines = [
        "        %s, %s," % (jlit(n["id"]), jlit(mod_id)),
        "        %s, %s, %s, %s, %s, %s, %s," % (
            jdouble(st["health"]), jdouble(st["armor"]), jdouble(st["armor_toughness"]), jdouble(st["attack_damage"]),
            jdouble(st["movement_speed"]), jdouble(st["knockback_resistance"]), jdouble(st["follow_range"])),
        "        %d," % int(st["xp_reward"]),
        "        %s," % jlit(main_id),
        "        %s, BossEvent.BossBarColor.%s, BossEvent.BossBarOverlay.%s," % (
            jbool(bar.get("show", True)), str(bar.get("color", "purple")).upper(), str(bar.get("overlay", "progress")).upper()),
        "        new float[] {%s}," % ", ".join(jfloat(p["health_below"]) for p in phases),
        "        new boolean[] {%s}," % ", ".join(jbool(p.get("announce")) for p in phases),
        "        " + abil + ",",
        "        %s, %s, %s" % (jlit(sounds["hurt"]), jlit(sounds["death"]), jlit(sounds.get("ambient"))),
    ]
    return "\n".join(lines)


def entity_file(n: dict, ctx: BuildContext) -> GenFile:
    boss_id = n["id"]
    cls = _check_boss_id(boss_id)
    vals = {"PACKAGE": ctx.package, "BOSS_ID": boss_id, "CLASS": cls, "PROFILE_ARGS": profile_args(n, ctx.mod_id)}
    text = render(load_template("java", "BossEntity.java.tpl"), vals, "BossEntity")
    return GenFile("%s/boss/%s/%sEntity.java" % (java_rel(ctx), boss_id, cls), text.encode("utf-8"), "java")


# --------------------------------------------------------------------------- encounter + progression resources
def _ingredient(ref: str) -> dict:
    return {"tag": ref[1:]} if ref.startswith("#") else {"item": ref}


def _sigil_recipe(mod: str, boss_id: str, recipe: dict) -> dict:
    return {
        "type": "minecraft:crafting_shaped",
        "category": "misc",
        "pattern": list(recipe["pattern"]),
        "key": {ch: _ingredient(item) for ch, item in recipe["key"].items()},
        "result": {"item": "%s:%s_sigil" % (mod, boss_id), "count": 1},
    }


def _advancement(n: dict, mod: str) -> dict:
    boss_id = n["id"]
    weapons = [w for w in n.get("weapons", []) if isinstance(w, dict) and w.get("id")]
    icon = "%s:%s" % (mod, weapons[0]["id"]) if weapons else "%s:%s_essence" % (mod, boss_id)
    tier = n.get("tier")
    return {
        "display": {
            "icon": {"item": icon},
            "title": {"translate": "advancements.%s.%s.title" % (mod, boss_id)},
            "description": {"translate": "advancements.%s.%s.description" % (mod, boss_id)},
            "frame": FRAME_BY_TIER.get(tier, "task"),
            "show_toast": True,
            "announce_to_chat": True,
            "hidden": tier == "hidden",
        },
        "criteria": {
            "kill": {
                "trigger": "minecraft:player_killed_entity",
                "conditions": {"entity": {"type": "%s:%s" % (mod, boss_id)}},
            }
        },
        "requirements": [["kill"]],
    }


def generate(n: dict, ctx: BuildContext) -> Tuple[List[GenFile], Dict[str, str]]:
    """Per-boss Java entity, encounter/progression resources and their lang keys.

    Items (weapons, essence, sigil, spawn egg), their models/textures/loot and the weapon recipes belong to the
    weapon generator; the sigil *recipe* is generated here because it is part of the encounter.
    """
    boss_id = n["id"]
    mod = ctx.mod_id
    if n.get("mod_id") not in (None, mod):
        raise BossForgeError("/mod_id", "mod_id_mismatch", "spec mod_id %r differs from the project mod id %r" % (n.get("mod_id"), mod))
    res = "src/main/resources/"
    files: List[GenFile] = [entity_file(n, ctx)]
    lang: Dict[str, str] = {}
    enc = _require(n, "encounter")
    trigger = enc.get("trigger")

    if trigger == "summon_item":
        si = enc.get("summon_item") or {}
        if si.get("recipe"):
            files.append(GenFile("%sdata/%s/recipes/%s_sigil.json" % (res, mod, boss_id),
                                 _json(_sigil_recipe(mod, boss_id, si["recipe"])), "json"))
        lang["message.%s.sigil.wrong_block" % mod] = "This sigil does not react to this block."
        lang["message.%s.sigil.wrong_dimension" % mod] = "This sigil does not react in this dimension."
    elif trigger == "command":
        text = render(load_template("misc", "summon.mcfunction.tpl"), {"MOD_ID": mod, "BOSS_ID": boss_id}, "summon.mcfunction")
        files.append(GenFile("%sdata/%s/functions/summon_%s.mcfunction" % (res, mod, boss_id), text.encode("utf-8"), "mcfunction"))
    elif trigger == "natural_spawn":
        ns = enc.get("natural_spawn") or {}
        obj = {
            "type": "forge:add_spawns",
            "biomes": ns.get("biomes", "#minecraft:is_overworld"),
            "spawners": {"type": "%s:%s" % (mod, boss_id), "weight": int(ns.get("weight", 1)), "minCount": 1, "maxCount": 1},
        }
        files.append(GenFile("%sdata/%s/forge/biome_modifier/%s_spawn.json" % (res, mod, boss_id), _json(obj), "json"))

    files.append(GenFile("%sdata/%s/advancements/bosses/%s.json" % (res, mod, boss_id), _json(_advancement(n, mod)), "json"))

    name = n.get("display_name") or boss_id
    title = n.get("title") or ""
    lang["entity.%s.%s" % (mod, boss_id)] = name
    lang["advancements.%s.%s.title" % (mod, boss_id)] = "%s Vanquished" % name
    lang["advancements.%s.%s.description" % (mod, boss_id)] = "Defeat %s%s." % (name, ", " + title if title else "")
    mod_name = n.get("mod_name") or mod
    lang["itemGroup.%s.bosses" % mod] = mod_name if mod_name.lower().endswith("bosses") else "%s Bosses" % mod_name
    for i, p in enumerate(n.get("phases") or [], 1):
        if p.get("announce"):
            lang["bossforge.%s.%s.phase.%d" % (mod, boss_id, i)] = p["announce"]
    return files, lang


# --------------------------------------------------------------------------- registry
def _hex_int(color: str, path: str) -> int:
    if not isinstance(color, str) or not re.match(r"^#[0-9a-fA-F]{6}$", color):
        raise BossForgeError(path, "bad_color", "expected #rrggbb, got %r" % (color,))
    return int(color[1:], 16)


def _weapon_field(w: dict) -> Tuple[str, str]:
    """(constant name, field declaration) of one weapon item."""
    wid = w["id"]
    const = "WEAPON_" + wid.upper()
    effects = [e for e in (w.get("effects") or []) if e.get("on", "hit") == "hit"]
    if effects:
        elist = "List.of(" + ", ".join(
            "new EffectSpec(%s, %d, %d, %s)" % (jlit(e["effect"]), int(e["duration"]), int(e["amplifier"]), jfloat(e["chance"]))
            for e in effects) + ")"
    else:
        elist = "List.of()"
    decl = (
        "    public static final RegistryObject<Item> %s = ITEMS.register(%s,\n"
        "            () -> new BossWeaponItem(new BossTier(%d), %d, %s, new Item.Properties().rarity(Rarity.%s),\n"
        "                    %s, %d, MOD_ID, %s));\n"
    ) % (const, jlit(wid), int(w["durability"]), int(w["damage"]) - 1, jfloat(w["attack_speed"]),
         str(w.get("rarity", "rare")).upper(), elist, len(w.get("lore") or []), jlit(wid))
    return const, decl


def _claim(items: Dict[str, str], item_id: str, owner: str) -> None:
    if item_id in items:
        raise BossForgeError("/items/" + item_id, "duplicate_item_id",
                             "item id %r is used by both %s and %s; item ids must be unique within a mod" % (item_id, items[item_id], owner))
    items[item_id] = owner


def _load_specs(root: Path, manifest: dict, key: str, normalizer, overrides: Optional[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for item_id, entry in sorted((manifest.get(key) or {}).items()):
        if overrides and item_id in overrides:
            out[item_id] = overrides[item_id]
            continue
        rel = (entry or {}).get("spec")
        p = root / rel if rel else None
        if p is None or not p.is_file():
            raise BossForgeError("/%s/%s" % (key, item_id), "spec_missing",
                                 "manifest lists %s %r but its spec file %s does not exist" % (key[:-1], item_id, rel))
        out[item_id] = normalizer(load(p))
    for item_id, spec in sorted((overrides or {}).items()):
        out.setdefault(item_id, spec)
    return out


def generate_registry(project_root, ctx: BuildContext, manifest: dict, bosses: Optional[Dict[str, dict]] = None,
                      weapons: Optional[Dict[str, dict]] = None) -> GenFile:
    """Build ``BossRegistry.java`` from every boss and standalone weapon spec in ``manifest``.

    ``bosses`` / ``weapons`` map ids to already-normalized specs and take precedence over the files named in the
    manifest (used for a spec that has not been copied into the project yet, e.g. during ``--dry-run``).
    """
    root = Path(project_root)
    pkg = ctx.package
    boss_specs = _load_specs(root, manifest, "bosses", normalize, bosses)
    weapon_specs = _load_specs(root, manifest, "weapons", normalize_weapon_spec, weapons)
    templates = data("templates")

    imports: List[str] = []
    entity_fields: List[str] = []
    item_blocks: List[str] = []
    weapon_consts: List[str] = []
    egg_consts: List[str] = []
    essence_consts: List[str] = []
    tab_consts: List[str] = []
    defs: List[str] = []
    items: Dict[str, str] = {}
    classes: Dict[str, str] = {}

    for bid in sorted(boss_specs):
        n = boss_specs[bid]
        cls = _check_boss_id(bid)
        if cls in classes:
            raise BossForgeError("/bosses/" + bid, "duplicate_class",
                                 "boss ids %r and %r both generate class %sEntity" % (classes[cls], bid, cls))
        classes[cls] = bid
        derived = n.get("derived") or {}
        template = derived.get("template")
        if template not in templates:
            raise BossForgeError("/bosses/" + bid, "unresolved_template", "boss %r has no resolved body template" % bid)
        size = _require(n, "size")
        enc = _require(n, "encounter")
        trigger = enc.get("trigger")
        ent = "ENTITY_" + bid.upper()
        imports.append("import %s.boss.%s.%sEntity;" % (pkg, bid, cls))
        entity_fields.append(
            "    public static final RegistryObject<EntityType<%sEntity>> %s = ENTITIES.register(%s,\n"
            "            () -> EntityType.Builder.of(%sEntity::new, MobCategory.MONSTER).sized(%s, %s).setTrackingRange(10).build(%s));\n"
            % (cls, ent, jlit(bid), cls, jfloat(size["width"]), jfloat(size["height"]), jlit(bid)))

        for w in n.get("weapons") or []:
            _claim(items, w["id"], "boss %s" % bid)
            const, decl = _weapon_field(w)
            item_blocks.append(decl)
            weapon_consts.append(const)
            tab_consts.append(const)

        _claim(items, bid + "_essence", "boss %s" % bid)
        ess = "ESSENCE_" + bid.upper()
        item_blocks.append("    public static final RegistryObject<Item> %s = ITEMS.register(%s, () -> new Item(new Item.Properties()));\n"
                           % (ess, jlit(bid + "_essence")))
        essence_consts.append(ess)
        tab_consts.append(ess)

        if trigger == "summon_item":
            si = enc.get("summon_item") or {}
            _claim(items, bid + "_sigil", "boss %s" % bid)
            sig = "SIGIL_" + bid.upper()
            item_blocks.append(
                "    public static final RegistryObject<Item> %s = ITEMS.register(%s,\n"
                "            () -> new BossSigilItem(new Item.Properties().stacksTo(%d).rarity(Rarity.RARE), %s, %s, %s, %s, MOD_ID));\n"
                % (sig, jlit(bid + "_sigil"), SIGIL_STACK_SIZE, ent, jlit(si.get("required_block")), jlit(si.get("dimension")),
                   jbool(si.get("consume", True))))
            tab_consts.append(sig)

        if n.get("tier") != "hidden":
            _claim(items, bid + "_spawn_egg", "boss %s" % bid)
            pal = (n.get("appearance") or {}).get("palette") or {}
            egg = "EGG_" + bid.upper()
            item_blocks.append(
                "    public static final RegistryObject<Item> %s = ITEMS.register(%s,\n"
                "            () -> new ForgeSpawnEggItem(%s, 0x%06X, 0x%06X, new Item.Properties()));\n"
                % (egg, jlit(bid + "_spawn_egg"), ent, _hex_int(pal.get("primary"), "/appearance/palette/primary"),
                   _hex_int(pal.get("secondary"), "/appearance/palette/secondary")))
            egg_consts.append(egg)
            tab_consts.append(egg)

        defs.append("new BossDef(%s, %s, %sEntity.PROFILE, %s, %s)" % (
            jlit(bid), ent, cls, jbool(templates[template]["humanoid"]), jbool(trigger == "natural_spawn")))

    for wid in sorted(weapon_specs):
        _claim(items, wid, "standalone weapon")
        const, decl = _weapon_field(weapon_specs[wid])
        item_blocks.append(decl)
        weapon_consts.append(const)
        tab_consts.append(const)

    for first in (weapon_consts, egg_consts, essence_consts):
        if first:
            icon = "new ItemStack(%s.get())" % first[0]
            break
    else:
        icon = "new ItemStack(Items.NETHER_STAR)"
    if defs:
        boss_defs = "List.of(\n" + ",\n".join("            " + d for d in defs) + ")"
    else:
        boss_defs = "List.of()"
    vals = {
        "PACKAGE": pkg,
        "MOD_ID": ctx.mod_id,
        "ENTITY_IMPORTS": "\n".join(imports),
        "ENTITY_FIELDS": "\n".join(entity_fields),
        "ITEM_FIELDS": "\n".join(item_blocks),
        "TAB_ICON": icon,
        "TAB_ITEMS": "\n".join("                output.accept(%s.get());" % c for c in tab_consts) or "                // no items registered yet",
        "BOSS_DEFS": boss_defs,
    }
    text = render(load_template("java", "BossRegistry.java.tpl"), vals, REGISTRY_CLASS)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return GenFile("%s/boss/%s.java" % (java_rel(ctx), REGISTRY_CLASS), text.encode("utf-8"), "java")
