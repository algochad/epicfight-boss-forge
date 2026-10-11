"""Boss spec: loading, normalization, validation, shared dataclasses.

Contracts used by every generator:
  * GenFile / BuildContext dataclasses
  * Err (JSON-pointer path + code + message) and BossForgeError
  * normalize(spec) -> fully defaulted dict (never mutates its input)
  * validate(spec) -> list[Err]
"""
from __future__ import annotations

import copy
import json
import math
import re
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import DATA_DIR

SCHEMA = "epicfight-boss-forge/1"
WEAPON_SCHEMA = "epicfight-boss-forge/weapon-1"

BOSS_ID_RE = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
MOD_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
RES_ID_RE = re.compile(r"^[a-z0-9_.-]+:[a-z0-9_./-]+$")

TIERS = ("major", "quest", "mini", "hidden")
FACTIONS = ("neutral", "undead", "blaze", "enderman", "illager", "piglins", "wither", "villager", "zombified_piglin")
BODY_TEMPLATES = ("auto", "biped", "iron_golem")
REGION_KEYS = ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r", "hands", "feet", "arms", "legs")
MATERIALS = ("skin", "hair", "plate_steel", "plate_gold", "leather", "chainmail", "cloth", "fur", "bone", "stone", "scales", "ember")
FEATURE_TYPES = ("eyes", "brow", "scar", "hair", "beard", "emblem", "trim", "plates", "cracks", "wear", "runes", "stripes")
PALETTE_KEYS = ("primary", "secondary", "accent", "skin", "hair", "eyes")
ABILITY_TYPES = ("on_hit_effect", "aura", "summon", "regen", "enrage")
TRIGGERS = ("summon_item", "command", "natural_spawn", )
RARITIES = ("common", "uncommon", "rare", "epic")
MOTIFS = ("plain", "fuller", "runes", "serrated")
BAR_COLORS = ("pink", "blue", "red", "green", "yellow", "purple", "white")
BAR_OVERLAYS = ("progress", "notched_6", "notched_10", "notched_12", "notched_20")
EF_FLOAT_STATS = ("impact", "armor_negation", "stun_armor", "attack_damage", "chasing_speed")
STAT_RANGES = {
    "health": (20, 4096), "armor": (0, 30), "armor_toughness": (0, 20), "attack_damage": (1, 200),
    "movement_speed": (0.1, 0.6), "knockback_resistance": (0, 1), "follow_range": (8, 128),
    "xp_reward": (0, 20000), "impact": (0, 1024), "armor_negation": (0, 100), "max_strikes": (1, 1024),
    "stun_armor": (0, 1024), "chasing_speed": (0.5, 2.0),
}
# feature parameter enums
FEATURE_ENUMS = {
    "eyes": {"style": ("human", "slit", "hollow", "visor")},
    "scar": {"side": ("left", "right")},
    "hair": {"style": ("short", "long", "bald", "hood")},
    "beard": {"length": ("short", "long")},
    "emblem": {"shape": ("sun", "cross", "skull", "rune_circle", "star", "crescent"),
               "face": ("front", "back", "left", "right")},
    "trim": {"where": ("hem", "cuff", "collar", "belt")},
    "stripes": {"orient": ("v", "h")},
}
SERIES_DEFAULT_PHASES = {"combo": "all", "heavy": "from2", "special": "last", "smash": "all"}


# --------------------------------------------------------------------------- errors / dataclasses
@dataclass
class Err:
    path: str
    code: str
    message: str

    def to_dict(self) -> Dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


class BossForgeError(Exception):
    def __init__(self, path: str, code: str, message: str):
        super().__init__("%s [%s]: %s" % (path, code, message))
        self.err = Err(path, code, message)


@dataclass
class GenFile:
    relpath: str
    data: bytes
    kind: str  # texture json java mcfunction doc preview


@dataclass
class BuildContext:
    project_root: Path
    mod_id: str
    package: str
    resources: Path
    java_root: Path
    cache_dir: Path
    offline: bool
    versions: dict
    force: bool = False
    dry_run: bool = False
    ef_jar: Optional[str] = None
    mesh_files: Dict[str, str] = field(default_factory=dict)  # template name -> explicit mesh path override


def make_context(project_root, mod_id: str, package: str, cache_dir, offline: bool = False, force: bool = False,
                 dry_run: bool = False, ef_jar: Optional[str] = None) -> BuildContext:
    root = Path(project_root)
    return BuildContext(project_root=root, mod_id=mod_id, package=package, resources=root / "src" / "main" / "resources",
                        java_root=root / "src" / "main" / "java" / Path(*package.split(".")), cache_dir=Path(cache_dir),
                        offline=offline, versions=data("versions"), force=force, dry_run=dry_run, ef_jar=ef_jar)


# --------------------------------------------------------------------------- data
_DATA: Dict[str, Any] = {}


def data(name: str) -> Any:
    if name not in _DATA:
        _DATA[name] = json.loads((DATA_DIR / (name + ".json")).read_text(encoding="utf-8"))
    return _DATA[name]


def reload_data() -> None:
    _DATA.clear()


def pascal(ident: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in ident.split("_") if p)


def title_case(ident: str) -> str:
    return " ".join(p.capitalize() for p in ident.split("_") if p)


def jstr(s: str) -> str:
    """Java string-literal body (no quotes): escape \\ " newlines and non-ASCII."""
    out = []
    for ch in s:
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o > 126:
            if o > 0xFFFF:
                o -= 0x10000
                out.append("\\u%04x\\u%04x" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
            else:
                out.append("\\u%04x" % o)
        else:
            out.append(ch)
    return "".join(out)


def load(path) -> dict:
    p = Path(path)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BossForgeError("", "file_not_found", "spec file not found: %s" % p)
    except json.JSONDecodeError as e:
        raise BossForgeError("", "bad_json", "%s: %s" % (p, e))


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# --------------------------------------------------------------------------- template pick
def _template_candidates(has_weapon: bool) -> List[str]:
    t = data("templates")
    return [k for k, v in t.items() if v["humanoid"] or not has_weapon]


def pick_template(requested: str, size: dict, has_weapon: bool) -> str:
    t = data("templates")
    if requested != "auto":
        return requested
    aspect = size["width"] / size["height"]
    best = None
    for name in _template_candidates(has_weapon):
        ref = t[name]["ref_hitbox"]
        d = abs(math.log(aspect / (ref["width"] / ref["height"])))
        if best is None or d < best[0] - 1e-12 or (abs(d - best[0]) <= 1e-12 and name == "biped"):
            best = (d, name)
    return best[1] if best else "biped"


# --------------------------------------------------------------------------- normalization
def default_recipe_summon() -> dict:
    return {"pattern": [" N ", "NSN", " N "], "key": {"N": "minecraft:netherite_ingot", "S": "minecraft:nether_star"}}


def phase_windows_default(series_id: str, phase_count: int) -> List[int]:
    allp = list(range(1, phase_count + 1))
    mode = SERIES_DEFAULT_PHASES.get(series_id, "all")
    if mode == "all":
        return allp
    if mode == "from2":
        return allp[1:] if phase_count > 1 else [1]
    return [phase_count] if phase_count > 1 else [1]


def _golem_phases(series_id: str, p: int) -> List[int]:
    allp = list(range(1, p + 1))
    if series_id == "smash":
        return allp
    return allp[1:] if p > 1 else [1]


def default_moves(template: str, category: Optional[str], phase_count: int) -> List[dict]:
    presets = data("ef-presets")
    moves = []
    if template == "iron_golem":
        for s in presets["iron_golem"]["series"]:
            moves.append({"series": s["id"], "phases": _golem_phases(s["id"], phase_count)})
        return moves
    entry = presets["biped"][category or "fist"]
    for s in entry["series"]:
        moves.append({"series": s["id"], "phases": phase_windows_default(s["id"], phase_count)})
    return moves


def normalize_weapon(w: dict, palette: Optional[dict] = None, tier: Optional[str] = None, standalone: bool = False) -> dict:
    """Apply category/appearance/obtain defaults to one weapon object."""
    cats = data("weapon-categories")
    out = copy.deepcopy(w)
    cat = cats.get(out.get("category"))
    pal = palette or {}
    if cat is None:
        return out  # validation reports it
    out.setdefault("rarity", "rare")
    out.setdefault("damage", cat["damage"])
    out.setdefault("attack_speed", cat["attack_speed"])
    out.setdefault("durability", cat["durability"])
    ef_in = out.get("ef") or {}
    ef: Dict[str, Any] = {}
    for block, vals in cat["ef_blocks"].items():
        if block == "common":
            merged = dict(vals)
            merged.update({k: v for k, v in ef_in.items() if k in ("impact", "armor_negation", "max_strikes", "damage_bonus", "speed_bonus")})
            ef.update(_ef_types(merged))
        else:
            merged = dict(vals)
            merged.update(ef_in.get(block) or {})
            ef[block] = _ef_types(merged)
    out["ef"] = ef
    ap = out.get("appearance") or {}
    out["appearance"] = {
        "blade": ap.get("blade", "#cfd6e4"),
        "guard": ap.get("guard", pal.get("accent", "#d4aa32")),
        "grip": ap.get("grip", "#5a3a22"),
        "accent": ap.get("accent", pal.get("accent", "#d4aa32")),
        "gem": ap.get("gem", pal.get("eyes", "#46a0ff")),
        "glow": bool(ap.get("glow", False)),
        "motif": ap.get("motif", "fuller"),
    }
    effects = []
    for e in out.get("effects") or []:
        d = {"on": "hit", "duration": 100, "amplifier": 0, "chance": 1.0}
        d.update(e)
        effects.append(d)
    out["effects"] = effects
    ob = out.get("obtain") or {}
    if tier is not None:
        tdef = data("tiers")[tier]
        drop, craft = tdef["weapon_drop"], tdef["weapon_craftable"]
    else:
        drop, craft = 0.0, True
    nob = {"drop": float(ob.get("drop", drop)), "craft": bool(ob.get("craft", craft))}
    if "recipe" in ob:
        nob["recipe"] = ob["recipe"]
    out["obtain"] = nob
    out["lore"] = list(out.get("lore") or [])
    out.setdefault("display_name", title_case(out.get("id", "weapon")))
    return out


def _ef_types(d: dict) -> dict:
    r = {}
    for k, v in d.items():
        if k == "max_strikes":
            r[k] = int(v)
        elif _is_num(v):
            r[k] = float(v)
        else:
            r[k] = v
    return r


def weapon_category(n: dict, weapon_id: Optional[str]) -> Optional[str]:
    for w in n.get("weapons", []):
        if w.get("id") == weapon_id:
            return w.get("category")
    return None


def normalize(spec: dict) -> dict:
    """Return a fully defaulted deep copy. Tolerates invalid input (validate reports problems)."""
    n = copy.deepcopy(spec)
    tiers = data("tiers")
    archs = data("archetypes")
    templates = data("templates")
    tier = n.get("tier") if n.get("tier") in tiers else None
    tdef = tiers.get(tier) if tier else None
    n.setdefault("schema", SCHEMA)
    n.setdefault("mod_name", title_case(n.get("mod_id", "") or ""))
    n.setdefault("title", "")
    if "seed" not in n:
        n["seed"] = zlib.crc32(str(n.get("id", "")).encode("utf-8"))
    lore = dict(n.get("lore") or {})
    for k in ("personality", "death_message"):
        lore.setdefault(k, "")
    n["lore"] = lore
    size = dict(n.get("size") or {})
    if "depth" not in size and "width" in size:
        size["depth"] = size["width"]
    if tdef:
        ds = tdef["default_size"]
        size.setdefault("height", ds["height"])
        size.setdefault("width", ds["width"])
        size.setdefault("depth", size["width"])
    n["size"] = size
    body = dict(n.get("body") or {})
    body.setdefault("template", "auto")
    n["body"] = body
    # appearance
    ap = dict(n.get("appearance") or {})
    arch_name = ap.get("archetype", "knight")
    arch = archs.get(arch_name) or archs["knight"]
    ap["archetype"] = arch_name
    ap.setdefault("description", "")
    pal = dict(arch["palette"])
    pal.update(ap.get("palette") or {})
    ap["palette"] = pal
    regions = copy.deepcopy(arch["regions"])
    for k, v in (ap.get("regions") or {}).items():
        base = dict(regions.get(k, {}))
        base.update(v if isinstance(v, dict) else {})
        regions[k] = base
    ap["regions"] = regions
    if "overlay" not in ap:
        ap["overlay"] = copy.deepcopy(arch["overlay"])
    if "features" not in ap:
        ap["features"] = copy.deepcopy(arch["features"])
    ap.setdefault("overlay_enabled", True)
    tex = dict(ap.get("texture") or {})
    tex.setdefault("scale", tdef["texture_scale"] if tdef else 2)
    ap["texture"] = tex
    n["appearance"] = ap
    n.setdefault("faction", arch["faction"])
    # stats
    stats = {}
    base_stats = tdef["stats"] if tdef else {}
    over = n.get("stats") or {}
    for k in STAT_RANGES:
        v = over.get(k, base_stats.get(k))
        if v is None:
            continue
        if k == "max_strikes" and _is_num(v):
            v = int(v)
        elif k in EF_FLOAT_STATS and _is_num(v):
            v = float(v)
        stats[k] = v
    n["stats"] = stats
    # phases
    if "phases" not in n and tdef:
        n["phases"] = [{"name": nm, "health_below": th} for nm, th in zip(tdef["phase_names"], tdef["phase_thresholds"])]
    n.setdefault("phases", [])
    # weapons
    palette = pal
    n["weapons"] = [normalize_weapon(w, palette, tier) if isinstance(w, dict) else w for w in (n.get("weapons") or [])]
    n.setdefault("main_weapon", None)
    # template
    wlist = [w for w in n["weapons"] if isinstance(w, dict)]
    has_weapon = bool(n.get("main_weapon"))
    template = None
    if n["body"]["template"] in templates or n["body"]["template"] == "auto":
        if _is_num(size.get("height")) and _is_num(size.get("width")) and size.get("height", 0) > 0:
            template = pick_template(n["body"]["template"], size, has_weapon)
    n["body"]["resolved"] = template
    # moves
    cat = weapon_category(n, n["main_weapon"]) if n.get("main_weapon") else None
    if "moves" not in n and template:
        n["moves"] = default_moves(template, cat, max(1, len(n["phases"])))
    n.setdefault("moves", [])
    n["moves"] = [_norm_move(m, len(n["phases"]) or 1) for m in n["moves"]]
    # abilities
    abil = []
    for a in n.get("abilities") or []:
        abil.append(_norm_ability(a) if isinstance(a, dict) else a)
    n["abilities"] = abil
    # encounter
    enc = copy.deepcopy(n.get("encounter") or {})
    if tdef:
        enc.setdefault("trigger", tdef["trigger"])
        bar = dict(tdef["boss_bar"])
        bar.update(enc.get("boss_bar") or {})
        enc["boss_bar"] = bar
    if enc.get("trigger") == "summon_item":
        si = dict(enc.get("summon_item") or {})
        si.setdefault("required_block", None)
        si.setdefault("dimension", None)
        si.setdefault("consume", True)
        if tier == "hidden":
            si["recipe"] = None
        elif "recipe" not in si:
            si["recipe"] = default_recipe_summon()
        enc["summon_item"] = si
    if enc.get("trigger") == "natural_spawn":
        ns = dict(enc.get("natural_spawn") or {})
        ns.setdefault("biomes", "#minecraft:is_overworld")
        ns.setdefault("weight", 1)
        enc["natural_spawn"] = ns
    n["encounter"] = enc
    rew = copy.deepcopy(n.get("rewards") or {})
    if tdef:
        ess = dict(tdef["essence"])
        ess.update(rew.get("essence") or {})
        rew["essence"] = ess
    n["rewards"] = rew
    sounds = dict(arch["sounds"])
    sounds.update(n.get("sounds") or {})
    n["sounds"] = sounds
    # derived
    derived = {"template": template, "scale": None, "hitbox": None, "model_height": None, "warnings": []}
    if _is_num(size.get("height")) and _is_num(size.get("width")):
        derived["hitbox"] = [size["width"], size["height"]]
    if template:
        mh = templates[template].get("model_height")
        derived["model_height"] = mh
        if mh and _is_num(size.get("height")):
            derived["scale"] = round(size["height"] / mh, 3)
        ref = templates[template]["ref_hitbox"]
        if _is_num(size.get("height")) and _is_num(size.get("width")) and size["height"] > 0:
            if size["width"] / size["height"] > 1.35 * (ref["width"] / ref["height"]):
                derived["warnings"].append(Err("/size/width", "wide_hitbox_for_template",
                                               "width/height exceeds 1.35x the %s reference proportion; mesh will look narrow in the hitbox" % template).to_dict())
    n["derived"] = derived
    return n


def _norm_move(m, phase_count: int):
    if not isinstance(m, dict):
        return m
    m = dict(m)
    if m.get("series") != "custom" and "phases" not in m:
        m["phases"] = phase_windows_default(m.get("series", "combo"), phase_count)
    if "phases" not in m:
        m["phases"] = list(range(1, phase_count + 1))
    return m


def _norm_ability(a: dict) -> dict:
    a = dict(a)
    t = a.get("type")
    if t == "on_hit_effect":
        a.setdefault("duration", 100); a.setdefault("amplifier", 0); a.setdefault("chance", 1.0)
    elif t == "aura":
        a.setdefault("radius", 6.0); a.setdefault("interval", 40); a.setdefault("duration", 60); a.setdefault("amplifier", 0)
    elif t == "summon":
        a.setdefault("count", 2); a.setdefault("phase", 2); a.setdefault("radius", 3.0)
    return a


# --------------------------------------------------------------------------- validation helpers
def _check_range(errs, path, v, lo, hi, integer=False):
    if not _is_num(v) or (integer and not _is_int(v)):
        errs.append(Err(path, "bad_type", "expected %s, got %r" % ("an integer" if integer else "a number", v)))
        return False
    if v < lo or v > hi:
        errs.append(Err(path, "out_of_range", "%r is outside the allowed range %s..%s" % (v, lo, hi)))
        return False
    return True


def _check_enum(errs, path, v, allowed):
    if v not in allowed:
        errs.append(Err(path, "unknown_value", "%r is not allowed; allowed values: %s" % (v, ", ".join(map(str, allowed)))))
        return False
    return True


def _check_str(errs, path, v, lo, hi, required=True):
    if not isinstance(v, str):
        errs.append(Err(path, "required" if v is None and required else "bad_type", "expected a string"))
        return False
    if len(v) < lo or len(v) > hi:
        errs.append(Err(path, "bad_length", "length %d outside %d..%d" % (len(v), lo, hi)))
        return False
    return True


def _check_color(errs, path, v, palette):
    from .colors import is_hex
    if isinstance(v, str) and (is_hex(v) or v in palette):
        return True
    errs.append(Err(path, "bad_color", "%r must be a palette key (%s) or #rrggbb" % (v, ", ".join(PALETTE_KEYS))))
    return False


def validate_recipe(errs, path, recipe):
    if not isinstance(recipe, dict) or not isinstance(recipe.get("pattern"), list) or not isinstance(recipe.get("key"), dict):
        errs.append(Err(path, "recipe_invalid", "recipe needs {pattern:[rows], key:{char:item id}}"))
        return
    pat = recipe["pattern"]
    if not 1 <= len(pat) <= 3 or not all(isinstance(r, str) for r in pat):
        errs.append(Err(path + "/pattern", "recipe_invalid", "pattern must have 1-3 string rows"))
        return
    if len(set(len(r) for r in pat)) != 1 or not 1 <= len(pat[0]) <= 3:
        errs.append(Err(path + "/pattern", "recipe_invalid", "rows must have equal length 1-3"))
        return
    for r in pat:
        for ch in r:
            if ch != " " and ch not in recipe["key"]:
                errs.append(Err(path + "/key", "recipe_invalid", "pattern char %r has no key entry" % ch))
    for ch, item in recipe["key"].items():
        if len(ch) != 1 or ch == " ":
            errs.append(Err(path + "/key", "recipe_invalid", "key %r must be a single non-space character" % ch))
        if not isinstance(item, str) or not RES_ID_RE.match(item):
            errs.append(Err(path + "/key/" + ch, "recipe_invalid", "item id %r must be namespace:path" % (item,)))


def validate_weapon(errs: List[Err], path: str, w: Any, palette: Optional[dict] = None) -> None:
    cats = data("weapon-categories")
    if not isinstance(w, dict):
        errs.append(Err(path, "bad_type", "weapon must be an object"))
        return
    wid = w.get("id")
    if not isinstance(wid, str) or not BOSS_ID_RE.match(wid):
        errs.append(Err(path + "/id", "bad_id", "weapon id %r must match %s" % (wid, BOSS_ID_RE.pattern)))
    _check_str(errs, path + "/display_name", w.get("display_name", ""), 1, 48)
    if w.get("category") not in cats:
        errs.append(Err(path + "/category", "unknown_value", "%r is not allowed; allowed values: %s" % (w.get("category"), ", ".join(cats))))
    if "rarity" in w:
        _check_enum(errs, path + "/rarity", w["rarity"], RARITIES)
    if "damage" in w:
        _check_range(errs, path + "/damage", w["damage"], 1, 40, integer=True)
    if "attack_speed" in w:
        _check_range(errs, path + "/attack_speed", w["attack_speed"], -4.0, 0.0)
    if "durability" in w:
        _check_range(errs, path + "/durability", w["durability"], 100, 5000, integer=True)
    ap = w.get("appearance") or {}
    if isinstance(ap, dict):
        for k in ("blade", "guard", "grip", "accent", "gem"):
            if k in ap:
                _check_color(errs, path + "/appearance/" + k, ap[k], palette or {})
        if "motif" in ap:
            _check_enum(errs, path + "/appearance/motif", ap["motif"], MOTIFS)
    for i, e in enumerate(w.get("effects") or []):
        ep = "%s/effects/%d" % (path, i)
        if not isinstance(e, dict) or not isinstance(e.get("effect"), str) or not RES_ID_RE.match(e.get("effect", "")):
            errs.append(Err(ep + "/effect", "bad_resource_id", "effect must be a namespace:path id"))
            continue
        if "chance" in e:
            _check_range(errs, ep + "/chance", e["chance"], 0.0, 1.0)
        if "duration" in e:
            _check_range(errs, ep + "/duration", e["duration"], 1, 72000, integer=True)
        if "amplifier" in e:
            _check_range(errs, ep + "/amplifier", e["amplifier"], 0, 255, integer=True)
    ob = w.get("obtain") or {}
    if isinstance(ob, dict):
        if "drop" in ob:
            _check_range(errs, path + "/obtain/drop", ob["drop"], 0.0, 1.0)
        if ob.get("recipe") is not None:
            validate_recipe(errs, path + "/obtain/recipe", ob["recipe"])
    lore = w.get("lore") or []
    if not isinstance(lore, list) or len(lore) > 4:
        errs.append(Err(path + "/lore", "bad_length", "lore must be a list of at most 4 strings"))
    else:
        for i, line in enumerate(lore):
            _check_str(errs, "%s/lore/%d" % (path, i), line, 0, 90)
    ef = w.get("ef")
    if isinstance(ef, dict) and w.get("category") in cats:
        for k, v in ef.items():
            if isinstance(v, dict):
                for kk, vv in v.items():
                    if not _is_num(vv):
                        errs.append(Err("%s/ef/%s/%s" % (path, k, kk), "bad_type", "expected a number"))
            elif not _is_num(v):
                errs.append(Err("%s/ef/%s" % (path, k), "bad_type", "expected a number"))


def validate_weapon_spec(spec: dict) -> List[Err]:
    """Standalone weapon.json: a weapon object plus mod_id (and optional essence_item)."""
    errs: List[Err] = []
    if not isinstance(spec, dict):
        return [Err("", "bad_type", "weapon spec must be an object")]
    if not isinstance(spec.get("mod_id"), str) or not MOD_ID_RE.match(spec.get("mod_id", "")):
        errs.append(Err("/mod_id", "bad_mod_id", "mod_id %r must match %s" % (spec.get("mod_id"), MOD_ID_RE.pattern)))
    validate_weapon(errs, "", spec)
    if "essence_item" in spec and not (isinstance(spec["essence_item"], str) and RES_ID_RE.match(spec["essence_item"])):
        errs.append(Err("/essence_item", "bad_resource_id", "essence_item must be namespace:path"))
    return errs


def normalize_weapon_spec(spec: dict) -> dict:
    out = normalize_weapon(spec, None, None, standalone=True)
    out["mod_id"] = spec.get("mod_id")
    if "essence_item" in spec:
        out["essence_item"] = spec["essence_item"]
    return out


def validate(spec: dict) -> List[Err]:
    return check(spec)[1]


def check(spec: dict) -> Tuple[dict, List[Err], List[Err]]:
    """Return (normalized, errors, warnings)."""
    errs: List[Err] = []
    warns: List[Err] = []
    if not isinstance(spec, dict):
        return {}, [Err("", "bad_type", "spec must be a JSON object")], []
    n = normalize(spec)
    if spec.get("schema") != SCHEMA:
        errs.append(Err("/schema", "bad_schema", "schema must be exactly %r" % SCHEMA))
    tiers, archs, templates = data("tiers"), data("archetypes"), data("templates")
    sid = spec.get("id")
    if not isinstance(sid, str) or not BOSS_ID_RE.match(sid):
        errs.append(Err("/id", "bad_id", "id %r must match %s" % (sid, BOSS_ID_RE.pattern)))
    mid = spec.get("mod_id")
    if not isinstance(mid, str) or not MOD_ID_RE.match(mid):
        errs.append(Err("/mod_id", "bad_mod_id", "mod_id %r must match %s" % (mid, MOD_ID_RE.pattern)))
    if "mod_name" in spec:
        _check_str(errs, "/mod_name", spec["mod_name"], 1, 60)
    if not _check_str(errs, "/display_name", spec.get("display_name"), 1, 48):
        pass
    if "title" in spec:
        _check_str(errs, "/title", spec["title"], 0, 64)
    if spec.get("tier") not in tiers:
        errs.append(Err("/tier", "unknown_value", "tier %r is not allowed; allowed values: %s" % (spec.get("tier"), ", ".join(tiers))))
    if "seed" in spec and not _is_int(spec["seed"]):
        errs.append(Err("/seed", "bad_type", "seed must be an integer"))
    # lore
    lore = spec.get("lore")
    if not isinstance(lore, dict):
        errs.append(Err("/lore", "required", "lore {summary, backstory, description} is required"))
    else:
        for k in ("summary", "backstory", "description"):
            if not lore.get(k):
                errs.append(Err("/lore/" + k, "required", "lore.%s is required" % k))
        for k in ("summary", "backstory", "description", "personality", "death_message"):
            if k in lore:
                _check_str(errs, "/lore/" + k, lore[k], 0, 1200)
    # size
    size = spec.get("size")
    if not isinstance(size, dict) and spec.get("tier") not in tiers:
        errs.append(Err("/size", "required", "size {height,width} is required"))
    elif isinstance(size, dict):
        if "height" in size:
            _check_range(errs, "/size/height", size["height"], 1.0, 8.0)
        elif spec.get("tier") not in tiers:
            errs.append(Err("/size/height", "required", "size.height is required"))
        if "width" in size:
            _check_range(errs, "/size/width", size["width"], 0.4, 4.0)
        elif spec.get("tier") not in tiers:
            errs.append(Err("/size/width", "required", "size.width is required"))
        if "depth" in size and size["depth"] != size.get("width"):
            errs.append(Err("/size/depth", "depth_must_equal_width", "Minecraft hitboxes are square: omit depth or set it equal to width"))
    # body
    tpl_req = (spec.get("body") or {}).get("template", "auto")
    _check_enum(errs, "/body/template", tpl_req, BODY_TEMPLATES)
    if "faction" in spec:
        _check_enum(errs, "/faction", spec["faction"], FACTIONS)
    # stats
    st = spec.get("stats")
    if st is not None:
        if not isinstance(st, dict):
            errs.append(Err("/stats", "bad_type", "stats must be an object"))
        else:
            for k, v in st.items():
                if k not in STAT_RANGES:
                    errs.append(Err("/stats/" + k, "unknown_stat", "unknown stat %r; allowed: %s" % (k, ", ".join(STAT_RANGES))))
                else:
                    lo, hi = STAT_RANGES[k]
                    _check_range(errs, "/stats/" + k, v, lo, hi, integer=(k == "max_strikes"))
    _validate_appearance(errs, warns, spec.get("appearance") or {}, n)
    # weapons
    ws = spec.get("weapons") or []
    if not isinstance(ws, list):
        errs.append(Err("/weapons", "bad_type", "weapons must be a list"))
        ws = []
    seen = set()
    for i, w in enumerate(ws):
        validate_weapon(errs, "/weapons/%d" % i, w, dict.fromkeys(PALETTE_KEYS))
        wid = w.get("id") if isinstance(w, dict) else None
        if wid in seen:
            errs.append(Err("/weapons/%d/id" % i, "duplicate_weapon_id", "weapon id %r is used twice" % wid))
        seen.add(wid)
    mw = spec.get("main_weapon")
    template = n["derived"]["template"]
    if mw is not None:
        if mw not in seen:
            errs.append(Err("/main_weapon", "unknown_weapon", "main_weapon %r is not in weapons (%s)" % (mw, ", ".join(sorted(x for x in seen if x)))))
        elif template and not templates[template]["humanoid"]:
            errs.append(Err("/main_weapon", "weapon_not_wieldable", "template %r is not humanoid and cannot wield a weapon; use body.template biped or drop main_weapon" % template))
    # phases
    phases = spec.get("phases")
    if phases is not None:
        if not isinstance(phases, list) or not 1 <= len(phases) <= 4:
            errs.append(Err("/phases", "bad_length", "phases must be a list of 1-4 entries"))
        else:
            prev = None
            for i, p in enumerate(phases):
                pp = "/phases/%d" % i
                if not isinstance(p, dict) or not _is_num(p.get("health_below")):
                    errs.append(Err(pp, "bad_type", "phase needs {name, health_below}"))
                    continue
                hb = p["health_below"]
                if not (0 < hb <= 1):
                    errs.append(Err(pp + "/health_below", "out_of_range", "health_below %r must be in (0, 1]" % hb))
                if i == 0 and hb != 1.0:
                    errs.append(Err(pp + "/health_below", "first_phase_not_one", "the first phase must have health_below 1.0"))
                if prev is not None and hb >= prev:
                    errs.append(Err(pp + "/health_below", "phases_not_decreasing", "health_below must strictly decrease (%r after %r)" % (hb, prev)))
                prev = hb
                if not isinstance(p.get("name"), str) or not p.get("name"):
                    errs.append(Err(pp + "/name", "required", "phase name is required"))
    phase_count = len(n.get("phases") or [])
    _validate_moves(errs, warns, spec.get("moves"), n, phase_count)
    _validate_abilities(errs, spec.get("abilities"), phase_count)
    _validate_encounter(errs, spec.get("encounter") or {}, n)
    rew = (spec.get("rewards") or {}).get("essence")
    if rew is not None:
        if not isinstance(rew, dict):
            errs.append(Err("/rewards/essence", "bad_type", "essence must be {min,max}"))
        else:
            for k in ("min", "max"):
                if k in rew:
                    _check_range(errs, "/rewards/essence/" + k, rew[k], 0, 64, integer=True)
            if _is_int(rew.get("min")) and _is_int(rew.get("max")) and rew["min"] > rew["max"]:
                errs.append(Err("/rewards/essence", "bad_range", "min must be <= max"))
    for k, v in (spec.get("sounds") or {}).items():
        if k not in ("hurt", "death", "ambient"):
            errs.append(Err("/sounds/" + k, "unknown_value", "allowed sounds: hurt, death, ambient"))
        elif not isinstance(v, str) or not RES_ID_RE.match(v):
            errs.append(Err("/sounds/" + k, "bad_resource_id", "sound must be a namespace:path registry id"))
    # derived
    d = n["derived"]
    if template and d.get("scale") is not None and not (0.5 <= d["scale"] <= 5.0):
        errs.append(Err("/size/height", "scale_out_of_range", "height %s on template %s gives mesh scale %s; allowed 0.5-5.0" % (size and size.get("height"), template, d["scale"])))
    warns.extend(Err(**w) for w in d["warnings"])
    if template and d.get("scale") is None:
        warns.append(Err("/body/template", "template_unmeasured", "template %s has no measured model_height yet" % template))
    return n, errs, warns


def _validate_appearance(errs, warns, ap, n):
    archs = data("archetypes")
    if not isinstance(ap, dict):
        errs.append(Err("/appearance", "bad_type", "appearance must be an object"))
        return
    if "archetype" in ap and ap["archetype"] not in archs:
        errs.append(Err("/appearance/archetype", "unknown_archetype", "unknown archetype %r; allowed: %s" % (ap["archetype"], ", ".join(archs))))
    npal = n["appearance"]["palette"]
    for k, v in (ap.get("palette") or {}).items():
        if k not in PALETTE_KEYS:
            errs.append(Err("/appearance/palette/" + k, "unknown_value", "palette keys: %s" % ", ".join(PALETTE_KEYS)))
        else:
            from .colors import is_hex
            if not is_hex(v):
                errs.append(Err("/appearance/palette/" + k, "bad_color", "palette values must be #rrggbb"))
    for k, v in (ap.get("regions") or {}).items():
        p = "/appearance/regions/" + k
        if k not in REGION_KEYS:
            errs.append(Err(p, "unknown_region", "unknown region %r; allowed: %s" % (k, ", ".join(REGION_KEYS))))
            continue
        if not isinstance(v, dict):
            errs.append(Err(p, "bad_type", "region entry must be an object"))
            continue
        if "material" in v:
            _check_enum_code(errs, p + "/material", v["material"], MATERIALS, "unknown_material")
        for ck in ("color", "accent"):
            if ck in v:
                _check_color(errs, p + "/" + ck, v[ck], npal)
    for i, o in enumerate(ap.get("overlay") or []):
        p = "/appearance/overlay/%d" % i
        if not isinstance(o, dict):
            errs.append(Err(p, "bad_type", "overlay entry must be an object"))
            continue
        _check_enum_code(errs, p + "/region", o.get("region"), REGION_KEYS, "unknown_region")
        _check_enum_code(errs, p + "/material", o.get("material"), MATERIALS, "unknown_material")
        y = o.get("y")
        if not (isinstance(y, list) and len(y) == 2 and all(_is_num(v) for v in y) and 0 <= y[0] <= y[1] <= 1):
            errs.append(Err(p + "/y", "bad_range", "y must be [lo, hi] with 0 <= lo <= hi <= 1"))
        for ck in ("color", "accent"):
            if ck in o:
                _check_color(errs, p + "/" + ck, o[ck], npal)
    for i, f in enumerate(ap.get("features") or []):
        p = "/appearance/features/%d" % i
        if not isinstance(f, dict):
            errs.append(Err(p, "bad_type", "feature must be an object"))
            continue
        t = f.get("type")
        if t not in FEATURE_TYPES:
            errs.append(Err(p + "/type", "unknown_feature", "unknown feature type %r; allowed: %s" % (t, ", ".join(FEATURE_TYPES))))
            continue
        for pk, allowed in FEATURE_ENUMS.get(t, {}).items():
            if pk in f:
                _check_enum(errs, "%s/%s" % (p, pk), f[pk], allowed)
        if "color" in f:
            _check_color(errs, p + "/color", f["color"], npal)
        if "region" in f and f["region"] not in REGION_KEYS:
            errs.append(Err(p + "/region", "unknown_region", "unknown region %r; allowed: %s" % (f["region"], ", ".join(REGION_KEYS))))
        for r in f.get("regions") or []:
            if r not in REGION_KEYS:
                errs.append(Err(p + "/regions", "unknown_region", "unknown region %r; allowed: %s" % (r, ", ".join(REGION_KEYS))))
    sc = (ap.get("texture") or {}).get("scale")
    if sc is not None and sc not in (1, 2, 4):
        errs.append(Err("/appearance/texture/scale", "unknown_value", "texture scale must be 1, 2 or 4"))
    tpl = n["derived"]["template"]
    if tpl:
        sc = n["appearance"]["texture"]["scale"]
        auv = data("templates")[tpl]["authored_uv"]
        if sc in (1, 2, 4) and max(auv) * sc > 512:
            errs.append(Err("/appearance/texture/scale", "texture_too_large", "texture %dx%d exceeds 512; lower texture.scale" % (auv[0] * sc, auv[1] * sc)))
        if not data("templates")[tpl]["overlay_parts"] and ap.get("overlay"):
            warns.append(Err("/appearance/overlay", "overlay_ignored", "template %s has no overlay layer; overlay entries are ignored" % tpl))


def _check_enum_code(errs, path, v, allowed, code):
    if v not in allowed:
        errs.append(Err(path, code, "%r is not allowed; allowed values: %s" % (v, ", ".join(allowed))))
        return False
    return True


def _validate_moves(errs, warns, moves, n, phase_count):
    from . import catalog
    template = n["derived"]["template"]
    if moves is None or not template:
        return
    if not isinstance(moves, list):
        errs.append(Err("/moves", "bad_type", "moves must be a list"))
        return
    presets = data("ef-presets")
    cat = weapon_category(n, n.get("main_weapon")) if n.get("main_weapon") else None
    if template == "iron_golem":
        valid = [s["id"] for s in presets["iron_golem"]["series"]]
    else:
        entry = presets["biped"].get(cat or "fist")
        valid = [s["id"] for s in entry["series"]] if entry else []
    for i, m in enumerate(moves):
        p = "/moves/%d" % i
        if not isinstance(m, dict):
            errs.append(Err(p, "bad_type", "move must be an object"))
            continue
        s = m.get("series")
        if s != "custom" and s not in valid:
            errs.append(Err(p + "/series", "unknown_series", "series %r not available for template %s weapon %s; allowed: custom, %s" % (s, template, cat or "fist", ", ".join(valid))))
        if s == "custom":
            anims = m.get("animations")
            if not isinstance(anims, list) or not anims:
                errs.append(Err(p + "/animations", "required", "custom moves need animations [{animation, min_range?, max_range?, chance?}]"))
            else:
                for j, a in enumerate(anims):
                    ap = "%s/animations/%d/animation" % (p, j)
                    key = a.get("animation") if isinstance(a, dict) else None
                    if not catalog.resolve(key):
                        errs.append(Err(ap, "unknown_animation", "%r is not an Epic Fight animation key (use `list animations --prefix`)" % (key,)))
                    else:
                        k = key[len("epicfight:"):]
                        if catalog.kind(k) != "attack":
                            warns.append(Err(ap, "non_attack_animation", "%s is not an attack animation" % key))
                        if k.startswith("biped/skill/"):
                            warns.append(Err(ap, "player_skill_animation", "%s is a player skill animation; mobs may not play it correctly" % key))
        for k, lo, hi in (("weight", 0, 10000), ("cooldown", 0, 100000), ("chance", 0, 1), ("range_scale", 0.1, 10)):
            if k in m:
                _check_range(errs, "%s/%s" % (p, k), m[k], lo, hi)
        for ph in m.get("phases") or []:
            if not _is_int(ph) or not 1 <= ph <= max(phase_count, 1):
                errs.append(Err(p + "/phases", "bad_phase", "phase %r outside 1..%d" % (ph, max(phase_count, 1))))


def _validate_abilities(errs, abilities, phase_count):
    if abilities is None:
        return
    if not isinstance(abilities, list):
        errs.append(Err("/abilities", "bad_type", "abilities must be a list"))
        return
    for i, a in enumerate(abilities):
        p = "/abilities/%d" % i
        if not isinstance(a, dict) or a.get("type") not in ABILITY_TYPES:
            errs.append(Err(p + "/type", "unknown_ability", "ability type must be one of: %s" % ", ".join(ABILITY_TYPES)))
            continue
        t = a["type"]
        for k in ("effect", "entity"):
            if k in a and not (isinstance(a[k], str) and RES_ID_RE.match(a[k])):
                errs.append(Err(p + "/" + k, "bad_resource_id", "%s must be a namespace:path id" % k))
        if t in ("on_hit_effect", "aura") and "effect" not in a:
            errs.append(Err(p + "/effect", "required", "%s needs effect" % t))
        if t == "summon" and "entity" not in a:
            errs.append(Err(p + "/entity", "required", "summon needs entity"))
        if t == "regen":
            if "per_second" not in a:
                errs.append(Err(p + "/per_second", "required", "regen needs per_second"))
            else:
                _check_range(errs, p + "/per_second", a["per_second"], 0.1, 100)
        if t == "enrage":
            for k in ("phase", "speed_pct", "damage_pct"):
                if k not in a:
                    errs.append(Err(p + "/" + k, "required", "enrage needs %s" % k))
        if "phase" in a and (not _is_int(a["phase"]) or not 1 <= a["phase"] <= max(phase_count, 1)):
            errs.append(Err(p + "/phase", "bad_phase", "phase %r outside 1..%d" % (a["phase"], max(phase_count, 1))))
        for ph in a.get("phases") or []:
            if not _is_int(ph) or not 1 <= ph <= max(phase_count, 1):
                errs.append(Err(p + "/phases", "bad_phase", "phase %r outside 1..%d" % (ph, max(phase_count, 1))))
        for k, lo, hi in (("chance", 0, 1), ("radius", 0.5, 64), ("interval", 1, 12000), ("duration", 1, 72000), ("amplifier", 0, 255), ("count", 1, 16)):
            if k in a:
                _check_range(errs, "%s/%s" % (p, k), a[k], lo, hi)


def _validate_encounter(errs, enc, n):
    if not isinstance(enc, dict):
        errs.append(Err("/encounter", "bad_type", "encounter must be an object"))
        return
    if "trigger" in enc:
        _check_enum(errs, "/encounter/trigger", enc["trigger"], TRIGGERS)
    bar = enc.get("boss_bar")
    if isinstance(bar, dict):
        if "color" in bar:
            _check_enum(errs, "/encounter/boss_bar/color", bar["color"], BAR_COLORS)
        if "overlay" in bar:
            _check_enum(errs, "/encounter/boss_bar/overlay", bar["overlay"], BAR_OVERLAYS)
    si = enc.get("summon_item")
    if isinstance(si, dict):
        for k in ("required_block", "dimension"):
            v = si.get(k)
            if v is not None and not (isinstance(v, str) and RES_ID_RE.match(v)):
                errs.append(Err("/encounter/summon_item/" + k, "bad_resource_id", "%s must be null or namespace:path" % k))
        if si.get("recipe") is not None:
            validate_recipe(errs, "/encounter/summon_item/recipe", si["recipe"])
    ns = enc.get("natural_spawn")
    if isinstance(ns, dict) and "weight" in ns:
        _check_range(errs, "/encounter/natural_spawn/weight", ns["weight"], 1, 1000, integer=True)
