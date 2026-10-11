"""Epic Fight behavior-series expansion.

Turns the spec's ``moves`` (preset series ids and ``custom`` animation lists) into the serialized
series objects of an Epic Fight mobpatch ``combat_behavior``:

    {"weight": F, "canBeInterrupted": B, "looping": B, "cooldown": I,
     "behaviors": [{"conditions": [...], "animation": "epicfight:<key>"}]}

Rules (EF 20.14.x, verified against ``MobPatchReloadListener`` / ``HealthPoint`` / ``TargetInDistance``):

* Distances are authored in blocks at scale 1.0. Every ``max`` becomes ``round(max * scale * range_scale, 2)``
  (``min`` stays). Custom ``min_range``/``max_range`` follow the same convention.
* A move limited to phases is expanded into one series per contiguous phase window. The window's health
  band is expressed with ``health`` predicates prepended to the FIRST behavior only (EF gates series
  selection on the first behavior). ``less_ratio H`` is true while ``hp/max < H`` and ``greater_ratio L``
  while ``hp/max > L`` (both strict), so phase 1 needs no upper bound and the last phase no lower bound.
* Condition order inside a behavior: health predicates, random_chance, within_eye_height, within_distance.
* Floats stay floats (``weight``, ``chance``, ``health``, ``min``, ``max``); ``cooldown`` is an int.
"""
from __future__ import annotations

from typing import List, Optional

from . import catalog
from .spec import BossForgeError, data, weapon_category

EF_PREFIX = "epicfight:"
DEFAULT_CUSTOM_REACH = 2.5


# --------------------------------------------------------------------------- context lookups
def template_name(n: dict) -> str:
    t = (n.get("derived") or {}).get("template")
    if not t:
        raise BossForgeError("/body/template", "template_unresolved", "body template could not be resolved; run validate first")
    return t


def scale_of(n: dict) -> float:
    s = (n.get("derived") or {}).get("scale")
    if isinstance(s, bool) or not isinstance(s, (int, float)) or s <= 0:
        raise BossForgeError("/size/height", "scale_unavailable", "model scale could not be derived from size and template; run validate first")
    return float(s)


def preset_category(n: dict) -> Optional[str]:
    """Key into ef-presets `biped`: the main weapon category, `fist` when unarmed, None for non-humanoid templates."""
    t = template_name(n)
    if not data("templates")[t]["humanoid"]:
        return None
    mw = n.get("main_weapon")
    if not mw:
        return "fist"
    cat = weapon_category(n, mw)
    if cat not in data("ef-presets")["biped"]:
        raise BossForgeError("/main_weapon", "unknown_series", "main weapon %r has no Epic Fight preset for category %r" % (mw, cat))
    return cat


def natural_style(n: dict) -> Optional[str]:
    cat = preset_category(n)
    if cat is None:
        return None
    return data("ef-presets")["biped"][cat]["style"]


def preset_series(n: dict) -> List[dict]:
    t = template_name(n)
    presets = data("ef-presets")
    if t == "iron_golem":
        return presets["iron_golem"]["series"]
    return presets["biped"][preset_category(n)]["series"]


def phase_thresholds(n: dict) -> List[float]:
    out = []
    for p in n.get("phases") or []:
        if isinstance(p, dict) and isinstance(p.get("health_below"), (int, float)) and not isinstance(p.get("health_below"), bool):
            out.append(float(p["health_below"]))
    return out or [1.0]


# --------------------------------------------------------------------------- phase windows
def phase_windows(phases: List[int]) -> List[List[int]]:
    """Group a set of 1-based phase numbers into contiguous ascending runs: [1,2,4] -> [[1,2],[4]]."""
    runs: List[List[int]] = []
    for p in sorted(set(phases)):
        if runs and runs[-1][-1] == p - 1:
            runs[-1].append(p)
        else:
            runs.append([p])
    return runs


def window_predicates(run: List[int], thresholds: List[float]) -> List[dict]:
    """`health` conditions bounding the health band of phases run[0]..run[-1] (see module docstring)."""
    lo_phase, hi_phase = run[0], run[-1]
    upper = 1.0 if lo_phase == 1 else thresholds[lo_phase - 1]
    lower = thresholds[hi_phase] if hi_phase < len(thresholds) else 0.0
    conds: List[dict] = []
    if upper < 1.0:
        conds.append({"predicate": "health", "health": float(upper), "comparator": "less_ratio"})
    if lower > 0.0:
        conds.append({"predicate": "health", "health": float(lower), "comparator": "greater_ratio"})
    return conds


# --------------------------------------------------------------------------- series building
def _conditions(chance: Optional[float], mn: Optional[float], mx: Optional[float]) -> List[dict]:
    conds: List[dict] = []
    if chance is not None:
        conds.append({"predicate": "random_chance", "chance": float(chance)})
    conds.append({"predicate": "within_eye_height"})
    if mn is not None and mx is not None:
        conds.append({"predicate": "within_distance", "min": float(mn), "max": float(mx)})
    return conds


def _scaled(mx: Optional[float], factor: float) -> Optional[float]:
    return None if mx is None else float(round(mx * factor, 2))


def _preset_behaviors(preset: dict, factor: float, chance: Optional[float]) -> List[dict]:
    out = []
    for i, b in enumerate(preset["behaviors"]):
        ch = b.get("chance")
        if i == 0 and chance is not None:
            ch = chance
        mn = b.get("min")
        out.append({"anim": EF_PREFIX + b["animation"], "chance": ch, "min": mn, "max": _scaled(b.get("max"), factor)})
    return out


def _default_reach(n: dict) -> float:
    for s in preset_series(n):
        for b in s["behaviors"]:
            if b.get("max") is not None:
                return float(b["max"])
    return DEFAULT_CUSTOM_REACH


def _custom_behaviors(n: dict, move: dict, path: str, factor: float) -> List[dict]:
    anims = move.get("animations")
    if not isinstance(anims, list) or not anims:
        raise BossForgeError(path + "/animations", "required", "custom moves need animations [{animation, min_range?, max_range?, chance?}]")
    reach = _default_reach(n)
    out = []
    for j, a in enumerate(anims):
        ap = "%s/animations/%d" % (path, j)
        if not isinstance(a, dict):
            raise BossForgeError(ap, "bad_type", "animation entry must be an object")
        key = a.get("animation")
        if not catalog.resolve(key):
            raise BossForgeError(ap + "/animation", "unknown_animation", "%r is not an Epic Fight animation key" % (key,))
        mn = a.get("min_range", 0.0)
        mx = a.get("max_range", reach)
        out.append({"anim": key, "chance": a.get("chance"), "min": float(mn), "max": _scaled(float(mx), factor)})
    return out


def _emit(series_def: dict, behaviors: List[dict], run_conditions: List[dict]) -> dict:
    out_behaviors = []
    for i, b in enumerate(behaviors):
        conds = _conditions(b["chance"], b["min"], b["max"])
        if i == 0:
            conds = [dict(c) for c in run_conditions] + conds
        out_behaviors.append({"conditions": conds, "animation": b["anim"]})
    return {
        "weight": float(series_def["weight"]),
        "canBeInterrupted": bool(series_def["interrupt"]),
        "looping": bool(series_def["loop"]),
        "cooldown": int(series_def["cooldown"]),
        "behaviors": out_behaviors,
    }


def _move_phases(n: dict, move: dict, path: str) -> List[int]:
    count = max(1, len(n.get("phases") or []))
    raw = move.get("phases")
    if not raw:
        return list(range(1, count + 1))
    out = []
    for p in raw:
        if isinstance(p, bool) or not isinstance(p, int) or not 1 <= p <= count:
            raise BossForgeError(path + "/phases", "bad_phase", "phase %r outside 1..%d" % (p, count))
        out.append(p)
    return out


def expand_detailed(n: dict) -> List[dict]:
    """Expanded series with metadata: [{"id", "phases": [..window..], "series": {...}}], in move/window order."""
    moves = n.get("moves")
    if not isinstance(moves, list) or not moves:
        raise BossForgeError("/moves", "no_moves", "moves is empty; omit `moves` to use the tier defaults")
    scale = scale_of(n)
    thresholds = phase_thresholds(n)
    presets = {s["id"]: s for s in preset_series(n)}
    out: List[dict] = []
    for i, m in enumerate(moves):
        path = "/moves/%d" % i
        if not isinstance(m, dict):
            raise BossForgeError(path, "bad_type", "move must be an object")
        sid = m.get("series")
        factor = scale * float(m.get("range_scale", 1.0))
        chance = m.get("chance")
        if sid == "custom":
            base = {"weight": 100.0, "interrupt": False, "loop": False, "cooldown": 0}
            behaviors = _custom_behaviors(n, m, path, factor)
            if chance is not None:
                behaviors[0]["chance"] = chance
            label = m.get("id") or "custom"
        else:
            if sid not in presets:
                raise BossForgeError(path + "/series", "unknown_series",
                                     "series %r not available for template %s; allowed: custom, %s" % (sid, template_name(n), ", ".join(presets)))
            p = presets[sid]
            base = {"weight": p["weight"], "interrupt": p["canBeInterrupted"], "loop": p["looping"], "cooldown": p["cooldown"]}
            behaviors = _preset_behaviors(p, factor, chance)
            label = sid
        if "weight" in m:
            base["weight"] = m["weight"]
        if "cooldown" in m:
            base["cooldown"] = m["cooldown"]
        if "interruptible" in m:
            base["interrupt"] = m["interruptible"]
        if "loop" in m:
            base["loop"] = m["loop"]
        for run in phase_windows(_move_phases(n, m, path)):
            out.append({"id": label, "phases": run, "series": _emit(base, behaviors, window_predicates(run, thresholds))})
    return out


def expand(n: dict) -> List[dict]:
    """Serialized EF behavior series for the boss's template and main weapon category."""
    return [d["series"] for d in expand_detailed(n)]


def fist_fallback(n: dict) -> List[dict]:
    """Unarmed behavior series (EF fist preset, scaled, all phases) used when the weapon is not held."""
    scale = scale_of(n)
    out = []
    for p in data("ef-presets")["biped"]["fist"]["series"]:
        base = {"weight": p["weight"], "interrupt": p["canBeInterrupted"], "loop": p["looping"], "cooldown": p["cooldown"]}
        out.append(_emit(base, _preset_behaviors(p, scale, None), []))
    return out
