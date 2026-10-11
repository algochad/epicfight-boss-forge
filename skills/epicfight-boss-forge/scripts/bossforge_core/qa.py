"""Quality checks: the mobpatch validator and the project-level `run`.

`validate_mobpatch` re-implements the rules of Epic Fight's `MobPatchReloadListener` (branch 1.20.1, pinned
ref in versions.json). EF skips a whole patch with only a log warning when one rule fails, so every
violation here is a hard error.
"""
from __future__ import annotations

import json
from typing import Any, List, Optional

from .spec import Err

# --------------------------------------------------------------------------- EF enums (lowercase names)
FACTIONS = ("neutral", "undead", "blaze", "enderman", "illager", "piglins", "wither", "villager", "zombified_piglin")
# yesman.epicfight.api.animation.LivingMotions (minus the datapack-editor option `all`)
LIVING_MOTIONS = ("inaction", "idle", "confront", "angry", "float", "walk", "run", "swim", "fly", "sneak", "kneel", "fall",
                  "sit", "mount", "death", "chase", "spellcast", "jump", "celebrate", "landing_recovery", "creative_fly",
                  "creative_idle", "sleep", "digging", "admire", "climb", "drink", "eat", "none", "aim", "block",
                  "block_shield", "reload", "shot", "spectate")
STUN_TYPES = ("knockdown", "short", "long", "hold", "neutralize", "fall", "none")
# CapabilityItem.WeaponCategories
WEAPON_CATEGORIES = ("not_weapon", "axe", "fist", "greatsword", "hoe", "pickaxe", "shovel", "sword", "uchigatana",
                     "spear", "tachi", "trident", "longsword", "dagger", "shield", "crossbow", "bow")
# CapabilityItem.Styles
STYLES = ("common", "one_hand", "two_hand", "mount", "ranged", "sheath", "ochs")
PREDICATES = ("health", "random_chance", "within_distance", "within_eye_height", "within_angle", "within_angle_horizontal")
COMPARATORS = ("greater_absolute", "less_absolute", "greater_ratio", "less_ratio")
FLOAT_ATTRIBUTES = ("impact", "armor_negation", "stun_armor", "attack_damage", "chasing_speed", "scale")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _allowed(values) -> str:
    return ", ".join(values)


class _Checker:
    def __init__(self, catalog_module):
        self.errs: List[Err] = []
        self.catalog = catalog_module

    def err(self, path: str, code: str, message: str) -> None:
        self.errs.append(Err(path, "mobpatch_" + code, message))

    # -- primitives
    def obj(self, v: Any, path: str) -> bool:
        if not isinstance(v, dict):
            self.err(path, "bad_type", "expected an object")
            return False
        return True

    def lst(self, v: Any, path: str) -> bool:
        if not isinstance(v, list):
            self.err(path, "bad_type", "expected a list")
            return False
        return True

    def string(self, v: Any, path: str) -> bool:
        if not isinstance(v, str) or not v:
            self.err(path, "bad_type", "expected a non-empty string")
            return False
        return True

    def number(self, v: Any, path: str) -> bool:
        if not _is_num(v):
            self.err(path, "bad_type", "expected a number (EF reads it as NumericTag)")
            return False
        return True

    def animation(self, v: Any, path: str) -> None:
        if not isinstance(v, str) or not self.catalog.resolve(v):
            self.err(path, "unknown_animation", "%r is not an Epic Fight animation (`epicfight:<key>` with <key> in the catalog); EF skips the whole patch" % (v,))

    # -- sections
    def resource(self, d: dict, key: str) -> None:
        v = d.get(key)
        if key not in d:
            self.err("/" + key, "missing_key", "required key %r is missing" % key)
        elif not isinstance(v, str) or ":" not in v:
            self.err("/" + key, "bad_type", "%s must be a resource id `namespace:path`" % key)

    def attributes(self, d: dict) -> None:
        if "attributes" not in d:
            return
        a = d["attributes"]
        if not self.obj(a, "/attributes"):
            return
        for k in FLOAT_ATTRIBUTES:
            if k in a and (not isinstance(a[k], float)):
                self.err("/attributes/" + k, "attribute_type", "%s must be a JSON float such as 1.0 (EF ignores integer-typed values for it), got %r" % (k, a[k]))
        if "max_strikes" in a and not _is_int(a["max_strikes"]):
            self.err("/attributes/max_strikes", "attribute_type", "max_strikes must be an integer, got %r" % (a["max_strikes"],))
        if isinstance(a.get("scale"), float) and a["scale"] <= 0:
            self.err("/attributes/scale", "bad_value", "scale must be > 0")

    def living_motions(self, m: Any, path: str) -> None:
        if not self.obj(m, path):
            return
        for k, v in m.items():
            if k not in LIVING_MOTIONS:
                self.err("%s/%s" % (path, k), "unknown_livingmotion", "living motion %r is unknown; allowed: %s" % (k, _allowed(LIVING_MOTIONS)))
            self.animation(v, "%s/%s" % (path, k))

    def stun_animations(self, m: Any, path: str) -> None:
        if not self.obj(m, path):
            return
        for k, v in m.items():
            if k not in STUN_TYPES:
                self.err("%s/%s" % (path, k), "unknown_stun_type", "stun type %r is unknown; allowed: %s" % (k, _allowed(STUN_TYPES)))
            self.animation(v, "%s/%s" % (path, k))

    def categories(self, v: Any, path: str, list_only: bool) -> None:
        if isinstance(v, str) and not list_only:
            v = [v]
        if not isinstance(v, list) or not v:
            self.err(path, "bad_type", "weapon_categories must be a non-empty JSON list of strings (EF reads it with getList(..., 8); anything else yields no categories)")
            return
        for i, c in enumerate(v):
            if not isinstance(c, str) or c not in WEAPON_CATEGORIES:
                self.err("%s/%d" % (path, i), "unknown_category", "weapon category %r is not a lowercase EF category; allowed: %s" % (c, _allowed(WEAPON_CATEGORIES)))

    def style(self, v: Any, path: str) -> None:
        if not isinstance(v, str) or v not in STYLES:
            self.err(path, "unknown_style", "style %r is unknown; allowed: %s" % (v, _allowed(STYLES)))

    def condition(self, c: Any, path: str) -> None:
        if not self.obj(c, path):
            return
        name = c.get("predicate")
        if not isinstance(name, str):
            self.err(path + "/predicate", "missing_key", "condition needs a string `predicate`")
            return
        if name not in PREDICATES:
            self.err(path + "/predicate", "unknown_predicate", "predicate %r is unknown; allowed: %s" % (name, _allowed(PREDICATES)))
            return
        if name == "health":
            self.number(c.get("health"), path + "/health")
            comp = c.get("comparator")
            if not isinstance(comp, str) or comp.lower() not in COMPARATORS:
                self.err(path + "/comparator", "bad_condition", "comparator %r must be one of: %s" % (comp, _allowed(COMPARATORS)))
            elif comp.endswith("ratio") and _is_num(c.get("health")) and not 0.0 <= c["health"] <= 1.0:
                self.err(path + "/health", "bad_condition", "ratio comparators take a health fraction in [0, 1]")
        elif name == "random_chance":
            if self.number(c.get("chance"), path + "/chance") and not 0.0 <= c["chance"] <= 1.0:
                self.err(path + "/chance", "bad_condition", "chance must be within [0, 1]")
        elif name != "within_eye_height":
            lo = self.number(c.get("min"), path + "/min")
            hi = self.number(c.get("max"), path + "/max")
            if lo and hi and c["min"] > c["max"]:
                self.err(path, "bad_condition", "min must not exceed max")

    def series_list(self, series: Any, path: str) -> int:
        if not self.lst(series, path):
            return 0
        for i, s in enumerate(series):
            sp = "%s/%d" % (path, i)
            if not self.obj(s, sp):
                continue
            if "weight" not in s:
                self.err(sp + "/weight", "missing_key", "series needs a numeric `weight`")
            elif self.number(s["weight"], sp + "/weight") and s["weight"] < 0:
                self.err(sp + "/weight", "bad_value", "weight must be >= 0")
            if "cooldown" in s and (not _is_int(s["cooldown"]) or s["cooldown"] < 0):
                self.err(sp + "/cooldown", "attribute_type", "cooldown must be a non-negative integer (ticks), got %r" % (s["cooldown"],))
            for flag in ("canBeInterrupted", "looping"):
                if flag in s and not isinstance(s[flag], bool):
                    self.err("%s/%s" % (sp, flag), "bad_type", "%s must be true or false" % flag)
            behaviors = s.get("behaviors")
            if not self.lst(behaviors, sp + "/behaviors"):
                continue
            if not behaviors:
                self.err(sp + "/behaviors", "no_behavior", "series has no behaviors")
            for j, b in enumerate(behaviors):
                bp = "%s/behaviors/%d" % (sp, j)
                if not self.obj(b, bp):
                    continue
                self.animation(b.get("animation"), bp + "/animation")
                conds = b.get("conditions", [])
                if self.lst(conds, bp + "/conditions"):
                    for k, c in enumerate(conds):
                        self.condition(c, "%s/conditions/%d" % (bp, k))
        return len(series)


def validate_mobpatch(d: Any, catalog_module: Optional[Any] = None) -> List[Err]:
    """Check a parsed mobpatch object against the EF parser's rules. `catalog_module` needs `resolve(str) -> bool`."""
    if catalog_module is None:
        from . import catalog as catalog_module  # noqa: F811
    c = _Checker(catalog_module)
    if not c.obj(d, ""):
        return c.errs
    if d.get("disabled") is True:
        return c.errs
    if "preset" in d:
        if not isinstance(d["preset"], str) or not d["preset"]:
            c.err("/preset", "bad_type", "preset must be a string")
        return c.errs
    c.resource(d, "model")
    c.resource(d, "armature")
    if "renderer" not in d:
        c.err("/renderer", "missing_key", "required key 'renderer' is missing")
    elif not isinstance(d["renderer"], str) or not d["renderer"]:
        c.err("/renderer", "bad_type", "renderer must be a non-empty string")
    elif d["renderer"] == "epicfight:custom":
        # RenderEngine.registerCustomEntityRenderer reads the boolean `humanoid` (not `isHumanoid`) to pick
        # PCustomHumanoidEntityRenderer (HumanoidMesh, item layers) over PCustomEntityRenderer (plain mesh).
        if d.get("humanoid") is not d.get("isHumanoid", False):
            c.err("/humanoid", "bad_renderer", "renderer `epicfight:custom` needs a boolean `humanoid` key equal to `isHumanoid` (EF reads `humanoid` to pick the renderer)")
    elif isinstance(d.get("model"), str) and not d["model"].startswith("epicfight:"):
        c.err("/renderer", "model_ignored", "renderer %r draws the stock Epic Fight mesh and ignores `model` %r; use renderer `epicfight:custom` with `humanoid` for a custom mesh" % (d["renderer"], d["model"]))
    if "faction" not in d:
        c.err("/faction", "missing_key", "required key 'faction' is missing")
    elif not isinstance(d["faction"], str) or d["faction"].lower() not in FACTIONS:
        c.err("/faction", "unknown_faction", "faction %r is unknown; allowed: %s" % (d["faction"], _allowed(FACTIONS)))
    humanoid = d.get("isHumanoid", False)
    if "isHumanoid" in d and not isinstance(humanoid, bool):
        c.err("/isHumanoid", "bad_type", "isHumanoid must be true or false")
        humanoid = False
    c.attributes(d)
    if "default_livingmotions" not in d:
        c.err("/default_livingmotions", "missing_key", "required key 'default_livingmotions' is missing")
    else:
        c.living_motions(d["default_livingmotions"], "/default_livingmotions")
    if "stun_animations" in d:
        c.stun_animations(d["stun_animations"], "/stun_animations")

    if "combat_behavior" not in d:
        c.err("/combat_behavior", "missing_key", "required key 'combat_behavior' is missing")
    elif humanoid:
        _humanoid_sections(c, d)
    else:
        n = c.series_list(d["combat_behavior"], "/combat_behavior")
        if isinstance(d["combat_behavior"], list):
            if n == 0:
                c.err("/combat_behavior", "no_behavior", "combat_behavior has no behavior series; the boss would never attack")
            for i, s in enumerate(d["combat_behavior"]):
                if isinstance(s, dict) and "behavior_series" in s:
                    c.err("/combat_behavior/%d" % i, "combat_shape",
                          "non-humanoid patches take a flat list of behavior series; `behavior_series` entries are for isHumanoid:true")
    return c.errs


def _humanoid_sections(c: _Checker, d: dict) -> None:
    motions = d.get("humanoid_weapon_motions", [])
    if c.lst(motions, "/humanoid_weapon_motions"):
        for i, e in enumerate(motions):
            p = "/humanoid_weapon_motions/%d" % i
            if not c.obj(e, p):
                continue
            if "weapon_categories" not in e:
                c.err(p + "/weapon_categories", "missing_key", "entry needs weapon_categories")
            else:
                c.categories(e["weapon_categories"], p + "/weapon_categories", list_only=False)
            c.style(e.get("style"), p + "/style")
            if "livingmotions" not in e:
                c.err(p + "/livingmotions", "missing_key", "entry needs livingmotions")
            else:
                c.living_motions(e["livingmotions"], p + "/livingmotions")
    entries = d["combat_behavior"]
    if not c.lst(entries, "/combat_behavior"):
        return
    if not entries:
        c.err("/combat_behavior", "no_behavior", "combat_behavior has no entries; the boss would never attack")
    for i, e in enumerate(entries):
        p = "/combat_behavior/%d" % i
        if not c.obj(e, p):
            continue
        if "weapon_categories" not in e:
            c.err(p + "/weapon_categories", "combat_shape",
                  "humanoid patches need entries {weapon_categories: [..], style, behavior_series: [..]}; a flat series list is for isHumanoid:false")
        else:
            c.categories(e["weapon_categories"], p + "/weapon_categories", list_only=True)
        c.style(e.get("style"), p + "/style")
        if "behavior_series" not in e:
            c.err(p + "/behavior_series", "missing_key", "entry needs behavior_series")
        else:
            n = c.series_list(e["behavior_series"], p + "/behavior_series")
            if n == 0 and isinstance(e["behavior_series"], list):
                c.err(p + "/behavior_series", "no_behavior", "entry has no behavior series")


def validate_mobpatch_text(text: str, catalog_module: Optional[Any] = None) -> List[Err]:
    """Parse JSON text, then `validate_mobpatch`."""
    try:
        d = json.loads(text)
    except ValueError as e:
        return [Err("", "mobpatch_bad_json", "mobpatch is not valid JSON: %s" % e)]
    return validate_mobpatch(d, catalog_module)


# --------------------------------------------------------------------------- project-level checks
import re
from pathlib import Path

from . import manifest as _manifest
from . import spec as _spec
from .pngio import png_size
from .sprites import sprite_resolution

_LONG = ("longsword", "greatsword", "tachi", "spear")
_STRIP = re.compile(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', re.S)
_LANG_LITERAL = re.compile(r'"((?:message|bossforge|itemGroup|item|entity|advancements)\.[a-z0-9_.]+[a-z0-9_])"')


def _balanced(src: str) -> bool:
    bare = _STRIP.sub("", src)
    return (bare.count("{") == bare.count("}") and bare.count("(") == bare.count(")")
            and bare.count("[") == bare.count("]"))


def _item_names(n: dict) -> List[dict]:
    """Registered items of a normalized boss spec: {name, sprite: size|None, gui: size|None, kind}."""
    items = []
    for w in n["weapons"]:
        long = w["category"] in _LONG
        size = sprite_resolution(w["category"], w.get("appearance"))
        items.append({"name": w["id"], "sprite": size, "gui": size // 2 if long else None, "kind": "weapon"})
    items.append({"name": n["id"] + "_essence", "sprite": 16, "gui": None, "kind": "essence"})
    if n["encounter"].get("trigger") == "summon_item":
        items.append({"name": n["id"] + "_sigil", "sprite": 16, "gui": None, "kind": "sigil"})
    if n["tier"] != "hidden":
        items.append({"name": n["id"] + "_spawn_egg", "sprite": None, "gui": None, "kind": "egg"})
    return items


def run(project, spec_raw: Optional[dict] = None, cache_dir=None) -> List[_spec.Err]:
    """Check a generated project. Codes prefixed `warn_` are warnings; everything else is a hard error."""
    from . import catalog
    root = Path(project)
    errs: List[Err] = []
    man = _manifest.load(root)
    if man is None:
        return [Err("/bossforge/manifest.json", "qa_no_manifest", "no bossforge/manifest.json: not a bossforge project")]
    mod = man["mod_id"]
    res = root / "src" / "main" / "resources"
    # manifest-owned files exist; JSON parses; hash drift is only a warning
    for rel, digest in man.get("generated", {}).items():
        f = root / rel
        if not f.is_file():
            errs.append(Err("/" + rel, "qa_missing_file", "manifest lists %s but it does not exist" % rel))
            continue
        data = f.read_bytes()
        if _manifest.sha256(data) != digest:
            errs.append(Err("/" + rel, "warn_modified", "%s differs from the generated version (hand-edited?)" % rel))
        if rel.endswith(".json") or rel.endswith(".mcmeta"):
            try:
                json.loads(data.decode("utf-8"))
            except ValueError as e:
                errs.append(Err("/" + rel, "qa_bad_json", "%s is not valid JSON: %s" % (rel, e)))
        if rel.endswith(".java"):
            src = data.decode("utf-8")
            if "{{" in src:
                errs.append(Err("/" + rel, "qa_unreplaced_placeholder", "%s contains an unreplaced {{ placeholder" % rel))
            m = re.search(r"public\s+(?:final\s+|abstract\s+)*(?:class|interface|record|enum)\s+(\w+)", src)
            if not m or m.group(1) != Path(rel).stem:
                errs.append(Err("/" + rel, "qa_class_name", "public type in %s must be named %s" % (rel, Path(rel).stem)))
            if not _balanced(src):
                errs.append(Err("/" + rel, "qa_unbalanced", "%s has unbalanced braces/parentheses" % rel))
    lang_path = res / "assets" / mod / "lang" / "en_us.json"
    lang = json.loads(lang_path.read_text(encoding="utf-8")) if lang_path.is_file() else {}
    # referenced lang keys
    for rel in man.get("generated", {}):
        f = root / rel
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace") if rel.endswith((".java", ".json")) else ""
        keys = set()
        if rel.endswith(".java"):
            keys.update(k for k in _LANG_LITERAL.findall(text))
        elif rel.endswith(".json") and "/advancements/" in rel:
            keys.update(re.findall(r'"translate"\s*:\s*"([^"]+)"', text))
        for k in sorted(keys):
            if k not in lang and not k.endswith("."):
                errs.append(Err("/" + rel, "qa_missing_lang", "lang key %s is referenced but missing in %s" % (k, lang_path.name)))
    templates = _spec.data("templates")
    tiers = _spec.data("tiers")
    for bid, entry in man.get("bosses", {}).items():
        sp = root / entry["spec"]
        if not sp.is_file():
            errs.append(Err("/bosses/" + bid, "qa_missing_spec", "spec %s is missing" % entry["spec"]))
            continue
        n, verrs, _w = _spec.check(json.loads(sp.read_text(encoding="utf-8")))
        errs.extend(Err("/bosses/%s%s" % (bid, e.path), e.code, e.message) for e in verrs)
        if verrs:
            continue
        d = n["derived"]
        tpl = templates[d["template"]]
        pre = "/bosses/" + bid
        # mobpatch
        mp = res / "data" / mod / "epicfight_mobpatch" / (bid + ".json")
        if not mp.is_file():
            errs.append(Err(pre, "qa_missing_file", "mobpatch %s missing" % mp.relative_to(root)))
        else:
            patch = json.loads(mp.read_text(encoding="utf-8"))
            errs.extend(Err(pre + e.path, e.code, e.message) for e in validate_mobpatch(patch, catalog))
            if d["scale"] is not None and patch.get("attributes", {}).get("scale") != d["scale"]:
                errs.append(Err(pre, "qa_scale_mismatch", "mobpatch scale %s != size.height/model_height %s" % (patch.get("attributes", {}).get("scale"), d["scale"])))
        # entity texture (+ custom mesh)
        sc = n["appearance"]["texture"]["scale"]
        tex = res / "assets" / mod / "textures" / "entity" / (bid + ".png")
        model = None
        if d.get("custom"):
            from . import efmodel, shape
            model_file = res / "assets" / mod / "animmodels" / "entity" / (bid + ".json")
            if not model_file.is_file():
                errs.append(Err(pre, "qa_missing_file", "custom EF mesh %s missing" % model_file.relative_to(root)))
            else:
                try:
                    problems = efmodel.check_mesh_json(json.loads(model_file.read_text(encoding="utf-8")))
                except ValueError as e:
                    problems = ["not valid JSON: %s" % e]
                errs.extend(Err(pre, "qa_bad_mesh", p) for p in problems)
            if mp.is_file() and json.loads(mp.read_text(encoding="utf-8")).get("model") != d["model_id"]:
                errs.append(Err(pre, "qa_model_mismatch", "mobpatch model must be %s" % d["model_id"]))
            model = shape.build_model(n)
        if not tex.is_file():
            errs.append(Err(pre, "qa_missing_file", "entity texture missing"))
        else:
            want = tuple(model.atlas) if model else (tpl["authored_uv"][0] * sc, tpl["authored_uv"][1] * sc)
            if png_size(tex) != want:
                errs.append(Err(pre, "qa_texture_size", "entity texture is %s, expected %s" % (png_size(tex), want)))
            else:
                try:
                    from . import skin
                    from .pngio import decode_png
                    if model:
                        mesh, mtpl = model.mesh, model.tpl
                    else:
                        from . import efassets, efmesh
                        mesh_path = efassets.mesh_file(tpl["mesh_name"], cache_dir or efassets.default_cache_dir(),
                                                       _spec.data("versions")["epicfight"]["assets_ref"], None, True)
                        mesh, mtpl = efmesh.load_mesh(mesh_path, tpl), tpl
                    cov = skin.coverage_of(decode_png(tex), mesh, mtpl)
                    if cov is not None and cov < 0.90:
                        errs.append(Err(pre, "qa_texture_coverage", "entity texture covers %.0f%% of the UV area (needs >= 90%%)" % (cov * 100)))
                except _spec.BossForgeError:
                    errs.append(Err(pre, "warn_coverage_skipped", "EF mesh not cached; texture coverage not checked"))
        # items
        for it in _item_names(n):
            nm = it["name"]
            if not (res / "assets" / mod / "models" / "item" / (nm + ".json")).is_file():
                errs.append(Err(pre, "qa_missing_file", "item model for %s missing" % nm))
            if "item.%s.%s" % (mod, nm) not in lang:
                errs.append(Err(pre, "qa_missing_lang", "lang key item.%s.%s missing" % (mod, nm)))
            for suffix, size in (("", it["sprite"]), ("_gui", it["gui"])):
                if size is None:
                    continue
                png = res / "assets" / mod / "textures" / "item" / (nm + suffix + ".png")
                if not png.is_file():
                    errs.append(Err(pre, "qa_missing_file", "item texture %s%s.png missing" % (nm, suffix)))
                elif png_size(png) != (size, size):
                    errs.append(Err(pre, "qa_sprite_size", "%s%s.png is %s, expected %dx%d" % (nm, suffix, png_size(png), size, size)))
        if "entity.%s.%s" % (mod, bid) not in lang:
            errs.append(Err(pre, "qa_missing_lang", "lang key entity.%s.%s missing" % (mod, bid)))
        # tier contract
        for k in tiers[n["tier"]]["stats"]:
            if k not in n["stats"]:
                errs.append(Err(pre, "qa_tier_stat", "stat %s missing" % k))
        if not n["phases"]:
            errs.append(Err(pre, "qa_tier_phases", "no phases"))
        trig = n["encounter"]["trigger"]
        need = None
        if trig == "summon_item":
            if n["encounter"]["summon_item"].get("recipe") is not None:
                need = "data/%s/recipes/%s_sigil.json" % (mod, bid)
        elif trig == "command":
            need = "data/%s/functions/summon_%s.mcfunction" % (mod, bid)
        elif trig == "natural_spawn":
            need = "data/%s/forge/biome_modifier/%s_spawn.json" % (mod, bid)
        if need and not (res / need).is_file():
            errs.append(Err(pre, "qa_trigger_resource", "trigger %s needs %s" % (trig, need)))
        if not (res / "data" / mod / "advancements" / "bosses" / (bid + ".json")).is_file():
            errs.append(Err(pre, "qa_missing_file", "advancement missing"))
        if not (res / "data" / mod / "loot_tables" / "entities" / (bid + ".json")).is_file():
            errs.append(Err(pre, "qa_missing_file", "loot table missing"))
        for w in n["weapons"]:
            ob = w["obtain"]
            if ob["drop"] <= 0 and not ob["craft"]:
                errs.append(Err(pre + "/weapons/" + w["id"], "qa_unobtainable", "weapon %s has no drop chance and is not craftable" % w["id"]))
            if not (res / "data" / mod / "capabilities" / "weapons" / (w["id"] + ".json")).is_file():
                errs.append(Err(pre, "qa_missing_file", "EF capability for %s missing" % w["id"]))
    for wid, entry in man.get("weapons", {}).items():
        sp = root / entry["spec"]
        if not sp.is_file():
            errs.append(Err("/weapons/" + wid, "qa_missing_spec", "spec %s is missing" % entry["spec"]))
            continue
        w = json.loads(sp.read_text(encoding="utf-8"))
        errs.extend(Err("/weapons/%s%s" % (wid, e.path), e.code, e.message) for e in _spec.validate_weapon_spec(w))
        if "item.%s.%s" % (mod, wid) not in lang:
            errs.append(Err("/weapons/" + wid, "qa_missing_lang", "lang key item.%s.%s missing" % (mod, wid)))
        if not (res / "data" / mod / "capabilities" / "weapons" / (wid + ".json")).is_file():
            errs.append(Err("/weapons/" + wid, "qa_missing_file", "EF capability missing"))
    if spec_raw is not None:
        sid = spec_raw.get("id")
        if sid not in man.get("bosses", {}) and sid not in man.get("weapons", {}):
            errs.append(Err("/id", "qa_not_registered", "%s is not registered in the project manifest (run add-boss)" % sid))
    return errs
