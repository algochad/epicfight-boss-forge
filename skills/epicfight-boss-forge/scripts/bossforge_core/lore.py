"""Boss design document generator (docs/bosses/<id>.md)."""
from __future__ import annotations

from typing import Dict, List, Tuple

from .spec import BuildContext, GenFile, data

STAT_LABELS = [
    ("health", "Health"), ("armor", "Armor"), ("armor_toughness", "Armor toughness"), ("attack_damage", "Attack damage"),
    ("movement_speed", "Movement speed"), ("knockback_resistance", "Knockback resistance"), ("follow_range", "Follow range"),
    ("xp_reward", "XP reward"), ("impact", "Impact (EF)"), ("armor_negation", "Armor negation % (EF)"),
    ("max_strikes", "Max strikes (EF)"), ("stun_armor", "Stun armor (EF)"), ("chasing_speed", "Chasing speed (EF)"),
]


def _table(header: List[str], rows: List[List[str]]) -> List[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c).replace("|", "/") for c in r) + " |")
    return out


def _move_rows(n: dict) -> List[List[str]]:
    from . import moves
    rows = []
    for s in moves.expand(n):
        anims = ", ".join(b["animation"].replace("epicfight:", "") for b in s["behaviors"])
        first = s["behaviors"][0]["conditions"] if s["behaviors"] else []
        window = []
        reach = ""
        for c in first:
            if c.get("predicate") == "health":
                window.append("hp %s %s" % ("<" if c["comparator"] == "less_ratio" else ">", c["health"]))
            if c.get("predicate") == "within_distance":
                reach = "%s-%s" % (c.get("min"), c.get("max"))
        rows.append([anims, " and ".join(window) or "always", reach or "-", s.get("cooldown", 0), s.get("weight", 0)])
    return rows


def generate(n: dict, ctx: BuildContext) -> Tuple[List[GenFile], Dict[str, str]]:
    d = n["derived"]
    lore = n["lore"]
    ap = n["appearance"]
    title = n["display_name"] + ((" - " + n["title"]) if n.get("title") else "")
    L: List[str] = ["# " + title, "", "*%s tier boss, %s*" % (n["tier"], d["template"]), ""]
    L += ["## Summary", "", lore["summary"], "", "## Backstory", "", lore["backstory"], "", "## Description", "", lore["description"], ""]
    if lore.get("personality"):
        L += ["## Personality", "", lore["personality"], ""]
    if lore.get("death_message"):
        L += ["## Death message", "", lore["death_message"], ""]
    L += ["## Stats", ""]
    L += _table(["Stat", "Value"], [[lbl, n["stats"][k]] for k, lbl in STAT_LABELS if k in n["stats"]])
    L += ["", "Size: %s blocks tall, %s wide (hitbox %s x %s); mesh scale %s." % (
        n["size"]["height"], n["size"]["width"], n["size"]["width"], n["size"]["height"], d["scale"]), ""]
    L += ["## Phases", ""]
    L += _table(["#", "Name", "Health below", "Announcement"],
                [[i + 1, p["name"], p["health_below"], p.get("announce", "")] for i, p in enumerate(n["phases"])])
    L += ["", "## Moveset", ""]
    L += _table(["Animations (epicfight:)", "Active when", "Reach (blocks, scaled)", "Cooldown (ticks)", "Weight"], _move_rows(n))
    L += ["", "## Abilities", ""]
    if n["abilities"]:
        for a in n["abilities"]:
            L.append("- `%s`: %s" % (a["type"], ", ".join("%s=%s" % (k, v) for k, v in a.items() if k != "type")))
    else:
        L.append("None.")
    L += ["", "## Weapons", ""]
    cats = data("weapon-categories")
    if n["weapons"]:
        for w in n["weapons"]:
            ob = w["obtain"]
            how = []
            if ob["drop"] > 0:
                how.append("%d%% boss drop" % round(ob["drop"] * 100))
            if ob["craft"]:
                how.append("craftable")
            ef = ", ".join("%s=%s" % (k, v) for k, v in w["ef"].items() if not isinstance(v, dict))
            L.append("- **%s** (`%s`, %s%s): damage %s, speed %+.1f, durability %s; EF: %s; obtain: %s" % (
                w["display_name"], w["id"], w["category"], ", main weapon" if n.get("main_weapon") == w["id"] else "",
                w["damage"], 4.0 + w["attack_speed"], w["durability"], ef or cats[w["category"]]["ef_type"], ", ".join(how) or "not obtainable"))
            for e in w["effects"]:
                L.append("  - on hit: %s %d ticks, amplifier %d, chance %s" % (e["effect"], e["duration"], e["amplifier"], e["chance"]))
    else:
        L.append("None (fists).")
    enc = n["encounter"]
    L += ["", "## Encounter", "", "Trigger: `%s`." % enc.get("trigger")]
    if enc.get("trigger") == "summon_item":
        si = enc["summon_item"]
        L.append("Use the `%s_sigil` item%s%s." % (n["id"], (" on " + si["required_block"]) if si.get("required_block") else "",
                                                   (" in " + si["dimension"]) if si.get("dimension") else ""))
        L.append("The sigil is %s." % ("found in loot only" if si.get("recipe") is None else "craftable"))
    elif enc.get("trigger") == "command":
        L.append("Run `/function %s:summon_%s` or `/summon %s:%s`." % (n["mod_id"], n["id"], n["mod_id"], n["id"]))
    else:
        ns = enc["natural_spawn"]
        L.append("Spawns naturally in `%s` (weight %s)." % (ns["biomes"], ns["weight"]))
    bar = enc["boss_bar"]
    L.append("Boss bar: %s." % ("%s / %s" % (bar["color"], bar["overlay"]) if bar.get("show") else "hidden"))
    ess = n["rewards"]["essence"]
    L += ["", "## Rewards", "", "- %s-%s `%s_essence` per kill" % (ess["min"], ess["max"], n["id"]), "- %s XP" % n["stats"].get("xp_reward"), ""]
    L += ["## Appearance", "", ap.get("description") or "(no description)", "", "Archetype: `%s`." % ap["archetype"], ""]
    for k, v in ap["palette"].items():
        L.append("- %s: `%s`" % (k, v))
    L.append("")
    L.append("Regions: " + ", ".join("%s=%s" % (k, v.get("material")) for k, v in ap["regions"].items()))
    L.append("")
    L.append("Overlay: " + (", ".join("%s %s %s" % (o["region"], o["material"], o["y"]) for o in ap["overlay"]) or "none"))
    L.append("")
    L.append("Features: " + ", ".join(f["type"] for f in ap["features"]))
    tpl = data("templates")[d["template"]]
    L += ["", "## Technical notes", "",
          "- Template `%s` (Epic Fight mesh `%s`), texture %dx%d." % (d["template"], tpl["mesh_name"],
                                                                        tpl["authored_uv"][0] * ap["texture"]["scale"],
                                                                        tpl["authored_uv"][1] * ap["texture"]["scale"]),
          "- Generated by epicfight-boss-forge; regenerate with `add-boss` after editing `bossforge/bosses/%s.boss.json`." % n["id"],
          "- Still to verify in game: mobpatch loads (no `Can't deserialize mob capability` log line), weapon hold pose, stun behavior, phase transitions, overlay layer visibility.", ""]
    rel = "docs/bosses/%s.md" % n["id"]
    return [GenFile(rel, "\n".join(L).encode("utf-8"), "doc")], {}
