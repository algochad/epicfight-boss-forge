"""Epic Fight mobpatch generator: `data/<mod>/epicfight_mobpatch/<boss>.json`.

EF reads the numbers below through NBT type checks (`contains("stun_armor", TAG_DOUBLE)`), so
`impact armor_negation stun_armor attack_damage chasing_speed scale` are always emitted as JSON floats
(`15.0`) and `max_strikes` / `cooldown` as ints; an integer-typed float key is silently ignored by EF.
"""
from __future__ import annotations

import copy
import json
from typing import Dict, List, Tuple

from . import catalog, moves
from .spec import BuildContext, BossForgeError, GenFile, data

FLOAT_ATTRIBUTES = ("impact", "armor_negation", "stun_armor", "attack_damage", "chasing_speed")
# EF living-motion slots filled from the catalog: slot -> animation key pattern
WEAPON_MOTION_KEYS = (("idle", "biped/living/hold_%s"), ("walk", "biped/living/walk_%s"), ("chase", "biped/living/run_%s"))


def relpath(n: dict) -> str:
    return "src/main/resources/data/%s/epicfight_mobpatch/%s.json" % (n["mod_id"], n["id"])


def _attributes(n: dict) -> dict:
    stats = n.get("stats") or {}
    out: Dict[str, object] = {}
    for k in ("impact", "armor_negation", "max_strikes", "stun_armor", "attack_damage", "chasing_speed"):
        v = stats.get(k)
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise BossForgeError("/stats/" + k, "required", "stat %s is missing or not a number" % k)
        out[k] = float(v) if k in FLOAT_ATTRIBUTES else int(v)
    out["scale"] = float(moves.scale_of(n))
    return out


def _stun_animations(n: dict, template: dict) -> dict:
    tier = data("tiers").get(n.get("tier"))
    if tier is None:
        raise BossForgeError("/tier", "unknown_value", "tier %r is not defined" % (n.get("tier"),))
    allowed = set(tier["stun_animations"])
    return {k: v for k, v in template["stun_animations"].items() if k in allowed}


def weapon_motions(category: str, style: str) -> List[dict]:
    """`humanoid_weapon_motions` entries for a weapon category (natural style + common); only catalog-existing keys."""
    motions: Dict[str, str] = {}
    for slot, pattern in WEAPON_MOTION_KEYS:
        key = pattern % category
        if key in catalog.keys():
            motions[slot] = moves.EF_PREFIX + key
    if not motions:
        return []
    styles = [style] if style == "common" else [style, "common"]
    return [{"weapon_categories": [category], "style": s, "livingmotions": dict(motions)} for s in styles]


def _humanoid_combat(n: dict) -> List[dict]:
    cat = moves.preset_category(n)
    style = moves.natural_style(n)
    series = moves.expand(n)
    entries = [{"weapon_categories": [cat], "style": style, "behavior_series": series}]
    if style != "common":
        entries.append({"weapon_categories": [cat], "style": "common", "behavior_series": copy.deepcopy(series)})
    if cat != "fist":
        entries.append({"weapon_categories": ["fist"], "style": "common", "behavior_series": moves.fist_fallback(n)})
    return entries


def build(n: dict) -> dict:
    """The mobpatch object, with the exact key order EF documents."""
    t = moves.template_name(n)
    tpl = data("templates")[t]
    faction = n.get("faction")
    if not isinstance(faction, str) or not faction:
        raise BossForgeError("/faction", "required", "faction is missing")
    out: Dict[str, object] = {
        "model": tpl["model_id"],
        "armature": tpl["armature_id"],
        "renderer": tpl["renderer"],
        "isHumanoid": bool(tpl["humanoid"]),
        "faction": faction,
        "attributes": _attributes(n),
        "default_livingmotions": dict(tpl["living_motions"]),
    }
    if tpl["humanoid"]:
        cat = moves.preset_category(n)
        out["humanoid_weapon_motions"] = [] if cat == "fist" else weapon_motions(cat, moves.natural_style(n))
        out["stun_animations"] = _stun_animations(n, tpl)
        out["combat_behavior"] = _humanoid_combat(n)
    else:
        out["stun_animations"] = {}
        out["combat_behavior"] = moves.expand(n)
    return out


def serialize(obj: dict) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def generate(n: dict, ctx: BuildContext) -> Tuple[List[GenFile], Dict[str, str]]:
    patch = build(n)
    from .qa import validate_mobpatch  # local import: qa.py is shared and grows later
    errs = validate_mobpatch(patch)
    if errs:
        e = errs[0]
        raise BossForgeError(e.path, e.code, "generated mobpatch is invalid: " + e.message)
    return [GenFile(relpath(n), serialize(patch).encode("utf-8"), "json")], {}
