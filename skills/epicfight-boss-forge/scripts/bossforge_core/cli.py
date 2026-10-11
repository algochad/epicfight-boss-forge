"""bossforge command line interface. Every command prints exactly one JSON object on stdout."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import spec as S
from .spec import BossForgeError, Err, GenFile


def _out(obj: Dict[str, Any], code: int = 0) -> int:
    sys.stdout.write(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
    return code


def _fail(errors: List[Err], extra: Optional[dict] = None) -> int:
    d = {"ok": False, "errors": [e.to_dict() for e in errors]}
    if extra:
        d.update(extra)
    return _out(d, 1)


def _log(msg: str) -> None:
    sys.stderr.write(msg + "\n")


def _cache(args) -> Path:
    from . import efassets
    return Path(args.cache_dir) if args.cache_dir else efassets.default_cache_dir()


def _write_gen(root: Path, files: List[GenFile]) -> List[str]:
    paths = []
    for f in files:
        p = root / f.relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(f.data)
        paths.append(str(p))
    return paths


def _is_weapon_spec(d: dict) -> bool:
    return isinstance(d, dict) and (d.get("schema") == S.WEAPON_SCHEMA or ("category" in d and "tier" not in d))


def _boss_ctx(n: dict, out: Path, args) -> S.BuildContext:
    return S.make_context(out, n["mod_id"], "com.bossforge." + n["mod_id"], _cache(args), offline=args.offline,
                          ef_jar=getattr(args, "ef_jar", None))


def _load_valid(path: str):
    raw = S.load(path)
    n, errs, warns = S.check(raw)
    return raw, n, errs, warns


# ------------------------------------------------------------------ commands
def cmd_list(args) -> int:
    what = args.what
    if what == "tiers":
        t = S.data("tiers")
        return _out({"ok": True, "tiers": {k: {"stats": v["stats"], "phases": v["phase_names"], "trigger": v["trigger"],
                                               "default_size": v["default_size"]} for k, v in t.items()}})
    if what == "archetypes":
        return _out({"ok": True, "archetypes": {k: {"description": v["description"], "palette": v["palette"], "features": [f["type"] for f in v["features"]]}
                                                for k, v in S.data("archetypes").items()}})
    if what == "templates":
        return _out({"ok": True, "templates": {k: {kk: v[kk] for kk in ("label", "humanoid", "authored_uv", "ref_hitbox", "model_height", "weapon_capable")}
                                               for k, v in S.data("templates").items()}})
    if what == "materials":
        return _out({"ok": True, "materials": list(S.MATERIALS), "regions": list(S.REGION_KEYS), "palette_keys": list(S.PALETTE_KEYS)})
    if what == "features":
        return _out({"ok": True, "features": list(S.FEATURE_TYPES), "enums": {k: v for k, v in S.FEATURE_ENUMS.items()}})
    if what == "abilities":
        return _out({"ok": True, "abilities": {
            "on_hit_effect": "{effect, duration=100, amplifier=0, chance=1.0, phases?}",
            "aura": "{effect, radius=6.0, interval=40, duration=60, amplifier=0, phases?}",
            "summon": "{entity, count=2, phase=2, radius=3.0}",
            "regen": "{per_second, phases?}",
            "enrage": "{phase, speed_pct, damage_pct}"}})
    if what == "weapon-categories":
        return _out({"ok": True, "weapon_categories": S.data("weapon-categories")})
    if what == "series":
        p = S.data("ef-presets")
        res = {"biped": {cat: {"style": e["style"], "series": [{"id": s["id"], "animations": [b["animation"] for b in s["behaviors"]]} for s in e["series"]]}
                         for cat, e in p["biped"].items()},
               "iron_golem": [{"id": s["id"], "animations": [b["animation"] for b in s["behaviors"]]} for s in p["iron_golem"]["series"]]}
        return _out({"ok": True, "series": res})
    if what == "animations":
        from . import catalog
        return _out({"ok": True, "animations": catalog.list_keys(args.prefix or "")})
    if what == "silhouettes":
        from . import presets
        return _out({"ok": True, "silhouettes": {k: presets.silhouette(k) for k in presets.list_silhouettes()}})
    if what == "presets":
        from . import presets
        return _out({"ok": True, "presets": {k: presets.PRESETS[k] for k in presets.list_presets()}})
    return _out({"ok": False, "errors": [{"path": "", "code": "unknown_value", "message": what}]}, 2)


def _snake(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def cmd_init(args) -> int:
    tiers = S.data("tiers")
    if args.tier not in tiers:
        return _fail([Err("/tier", "unknown_value", "tier must be one of %s" % ", ".join(tiers))])
    ds = tiers[args.tier]["default_size"]
    arch = args.archetype or "knight"
    sp: Dict[str, Any] = {
        "schema": S.SCHEMA, "id": args.id, "mod_id": args.mod_id, "mod_name": args.mod_name or S.title_case(args.mod_id),
        "display_name": args.name, "title": "", "tier": args.tier,
        "lore": {"summary": args.summary or "%s, a %s boss." % (args.name, args.tier),
                 "backstory": args.backstory or "%s guards this land." % args.name,
                 "description": args.description or "A %s boss." % arch},
        "size": {"height": args.height if args.height is not None else ds["height"], "width": args.width if args.width is not None else ds["width"]},
        "body": {"template": args.template or "auto"},
        "appearance": {"description": "", "archetype": arch},
    }
    if args.weapon_category:
        wname = args.weapon_name or "%s's %s" % (args.name, args.weapon_category.title())
        wid = _snake(args.weapon_name) if args.weapon_name else _snake(args.id + "_" + args.weapon_category)
        sp["weapons"] = [{"id": wid, "display_name": wname, "category": args.weapon_category}]
        sp["main_weapon"] = wid
    sil = args.silhouette
    if sil != "none" and (args.template or "auto") != "iron_golem":
        from . import presets
        sp["shape"] = {"silhouette": sil or presets.default_silhouette(arch)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(sp, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    errs = S.validate(sp)
    return _out({"ok": not errs, "spec": args.out, "errors": [e.to_dict() for e in errs]}, 0 if not errs else 1)


def cmd_validate(args) -> int:
    raw = S.load(args.spec)
    if _is_weapon_spec(raw):
        errs = S.validate_weapon_spec(raw)
        return _out({"ok": not errs, "kind": "weapon", "errors": [e.to_dict() for e in errs]}, 0 if not errs else 1)
    n, errs, warns = S.check(raw)
    d = n.get("derived", {})
    res = {"ok": not errs, "kind": "boss", "template": d.get("template"), "custom_shape": d.get("custom"), "scale": d.get("scale"),
           "model_height": d.get("model_height"), "hitbox": d.get("hitbox"),
           "errors": [e.to_dict() for e in errs], "warnings": [w.to_dict() for w in warns]}
    return _out(res, 0 if not errs else 1)


def cmd_fetch(args) -> int:
    from . import efassets
    ref = args.ref or S.data("versions")["epicfight"]["assets_ref"]
    paths = {}
    for tname, t in S.data("templates").items():
        paths[t["mesh_name"]] = str(efassets.mesh_file(t["mesh_name"], _cache(args), ref, args.ef_jar, args.offline))
    return _out({"ok": True, "ref": ref, "meshes": paths})


def cmd_mesh_info(args) -> int:
    from . import efassets, efmesh
    tpl = S.data("templates").get(args.template)
    if tpl is None:
        return _fail([Err("/template", "unknown_value", "templates: %s" % ", ".join(S.data("templates")))])
    if args.mesh_file:
        path = Path(args.mesh_file)
    else:
        path = efassets.mesh_file(tpl["mesh_name"], _cache(args), S.data("versions")["epicfight"]["assets_ref"], None, args.offline)
    mesh = efmesh.load_mesh(path, tpl)
    info = efmesh.mesh_info(mesh, tpl, tpl["mesh_name"])
    info["ok"] = True
    info["mesh_file"] = str(path)
    return _out(info)


def _need_valid(args):
    raw, n, errs, warns = _load_valid(args.spec)
    if errs:
        return None, _fail(errs, {"warnings": [w.to_dict() for w in warns]})
    return (raw, n, warns), None


def cmd_skin(args) -> int:
    from . import skin
    ok, bad = _need_valid(args)
    if bad is not None:
        return bad
    raw, n, warns = ok
    out = Path(args.out)
    ctx = _boss_ctx(n, out, args)
    if args.mesh_file:
        ctx.mesh_files[n["derived"]["template"]] = args.mesh_file
    files, lang, report = skin.generate_with_report(n, ctx)
    _write_gen(out, files)
    res = {"ok": True}
    res.update(report)
    res["texture"] = dict(report["texture"], path=str(out / report["texture"]["path"]) if not Path(report["texture"]["path"]).is_absolute() else report["texture"]["path"])
    res["preview"] = str(out / report["preview"]) if not Path(str(report["preview"])).is_absolute() else report["preview"]
    if report.get("closeup"):
        res["closeup"] = str(out / report["closeup"])
    res["warnings"] = [w.to_dict() for w in warns] + list(report.get("warnings", []))
    return _out(res)


def cmd_weapon(args) -> int:
    from . import weapon, qa, scaffold
    raw = S.load(args.spec)
    if args.project:
        if not _is_weapon_spec(raw):
            return _fail([Err("", "not_weapon_spec", "--project needs a weapon-only spec (schema %s or a bare weapon object with mod_id)" % S.WEAPON_SCHEMA)])
        res = scaffold.add_weapon(args.spec, args.project, _cache(args), args.offline, args.force, args.dry_run, args.essence_item)
        return _out(res, 0 if res.get("ok") else 1)
    if not args.out:
        return _fail([Err("", "usage", "weapon needs --out DIR or --project DIR")])
    out = Path(args.out)
    if _is_weapon_spec(raw):
        errs = S.validate_weapon_spec(raw)
        if errs:
            return _fail(errs)
        w = S.normalize_weapon_spec(raw)
        ctx = S.make_context(out, w["mod_id"], "com.bossforge." + w["mod_id"], _cache(args), offline=True)
        files, lang = weapon.generate_standalone(w, ctx, args.essence_item or w.get("essence_item") or "minecraft:diamond")
        mod_id = w["mod_id"]
    else:
        ok, bad = _need_valid(args)
        if bad is not None:
            return bad
        raw, n, warns = ok
        ctx = _boss_ctx(n, out, args)
        files, lang = weapon.generate(n, ctx)
        mod_id = n["mod_id"]
    _write_gen(out, files)
    from . import manifest
    manifest.merge_lang(out, mod_id, lang)
    return _out({"ok": True, "files": [f.relpath for f in files], "lang_keys": sorted(lang)})


def cmd_patch(args) -> int:
    from . import patch
    ok, bad = _need_valid(args)
    if bad is not None:
        return bad
    raw, n, warns = ok
    out = Path(args.out)
    files, lang = patch.generate(n, _boss_ctx(n, out, args))
    _write_gen(out, files)
    return _out({"ok": True, "files": [f.relpath for f in files], "warnings": [w.to_dict() for w in warns]})


def cmd_shape(args) -> int:
    from . import shape
    ok, bad = _need_valid(args)
    if bad is not None:
        return bad
    raw, n, warns = ok
    if not n["derived"].get("custom"):
        return _fail([Err("/shape", "no_shape", "this spec has no `shape` block (it uses the stock EF mesh)")])
    out = Path(args.out)
    model = shape.build_model(n, _boss_ctx(n, out, args))
    files, _lang = shape.generate(n, _boss_ctx(n, out, args))
    _write_gen(out, files)
    lo, hi = model.bounds
    return _out({"ok": True, "files": [f.relpath for f in files], "boxes": len(model.boxes), "atlas": list(model.atlas),
                 "height": round(model.height, 4), "scale": n["derived"]["scale"],
                 "bounds": {"min": [round(v, 3) for v in lo], "max": [round(v, 3) for v in hi]},
                 "proportions": model.proportions, "warnings": [w.to_dict() for w in warns]})


def cmd_lore(args) -> int:
    from . import lore
    ok, bad = _need_valid(args)
    if bad is not None:
        return bad
    raw, n, warns = ok
    out = Path(args.out)
    files, lang = lore.generate(n, _boss_ctx(n, out, args))
    _write_gen(out, files)
    return _out({"ok": True, "files": [f.relpath for f in files]})


def cmd_new_mod(args) -> int:
    from . import scaffold
    res = scaffold.new_mod(args.mod_id, args.mod_name, args.out, group=args.group, author=args.author, description=args.description or "",
                           mdk_zip=args.mdk_zip, forge_version=args.forge_version, cache_dir=_cache(args), offline=args.offline)
    return _out(res)


def cmd_add_boss(args) -> int:
    from . import scaffold
    res = scaffold.add_boss(args.spec, args.project, _cache(args), args.offline, args.force, args.dry_run, args.ef_jar)
    return _out(res, 0 if res.get("ok") else 1)


def cmd_qa(args) -> int:
    from . import qa
    spec_raw = S.load(args.spec) if args.spec else None
    errs = qa.run(Path(args.project), spec_raw)
    hard = [e for e in errs if not e.code.startswith("warn_")]
    return _out({"ok": not hard, "errors": [e.to_dict() for e in hard], "warnings": [e.to_dict() for e in errs if e.code.startswith("warn_")]},
                0 if not hard else 1)


def cmd_render_test(args) -> int:
    from . import rendertest
    res = rendertest.run(args.project, args.boss, args.out, timeout=args.timeout, keep=args.keep)
    return _out(res, 0 if res.get("ok") else 1)


def cmd_catalog(args) -> int:
    from . import catalog, efassets
    data = catalog.build(args.ef_ref, args.out, efassets.http_get)
    return _out({"ok": True, "keys": len(data["keys"]), "out": args.out, "ref": args.ef_ref})


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="bossforge", description="Epic Fight boss generator (Forge 1.20.1)")
    p.add_argument("--cache-dir", help="mesh/MDK cache (default $BOSSFORGE_CACHE or ~/.cache/epicfight-boss-forge)")
    p.add_argument("--offline", action="store_true", help="never touch the network")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="print catalog data as JSON")
    s.add_argument("what", choices=["tiers", "archetypes", "templates", "materials", "features", "abilities", "weapon-categories", "series", "animations", "silhouettes", "presets"])
    s.add_argument("--prefix")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("init", help="write a valid starter boss spec")
    s.add_argument("--id", required=True); s.add_argument("--name", required=True); s.add_argument("--mod-id", required=True)
    s.add_argument("--tier", required=True); s.add_argument("--mod-name"); s.add_argument("--height", type=float); s.add_argument("--width", type=float)
    s.add_argument("--archetype"); s.add_argument("--weapon-category"); s.add_argument("--weapon-name"); s.add_argument("--template")
    s.add_argument("--summary"); s.add_argument("--backstory"); s.add_argument("--description")
    s.add_argument("--silhouette", help="custom body shape preset (default from archetype; 'none' = stock EF mesh)")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("validate", help="validate a boss or weapon spec")
    s.add_argument("spec"); s.set_defaults(fn=cmd_validate)

    s = sub.add_parser("fetch-ef-assets", help="download/extract Epic Fight meshes into the cache")
    s.add_argument("--ef-jar"); s.add_argument("--ref"); s.set_defaults(fn=cmd_fetch)

    s = sub.add_parser("mesh-info", help="print mesh diagnostics")
    s.add_argument("--template", required=True); s.add_argument("--mesh-file"); s.set_defaults(fn=cmd_mesh_info)

    s = sub.add_parser("skin", help="generate the entity texture and a preview")
    s.add_argument("spec"); s.add_argument("--out", required=True); s.add_argument("--mesh-file"); s.add_argument("--ef-jar")
    s.set_defaults(fn=cmd_skin)

    s = sub.add_parser("weapon", help="generate weapon files (--out) or add a weapon to a project (--project)")
    s.add_argument("spec"); s.add_argument("--out"); s.add_argument("--project"); s.add_argument("--essence-item")
    s.add_argument("--force", action="store_true"); s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_weapon)

    s = sub.add_parser("shape", help="build the custom EF mesh JSON for a spec with a `shape` block")
    s.add_argument("spec"); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_shape)

    s = sub.add_parser("patch", help="generate the Epic Fight mobpatch JSON")
    s.add_argument("spec"); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_patch)

    s = sub.add_parser("new-mod", help="create a Forge 1.20.1 mod project with an Epic Fight dependency")
    s.add_argument("--mod-id", required=True); s.add_argument("--mod-name", required=True); s.add_argument("--group", default="com.bossforge")
    s.add_argument("--author"); s.add_argument("--description"); s.add_argument("--out", required=True); s.add_argument("--mdk-zip")
    s.add_argument("--forge-version"); s.set_defaults(fn=cmd_new_mod)

    for name in ("add-boss", "build"):
        s = sub.add_parser(name, help="generate everything for a boss into a mod project")
        s.add_argument("spec"); s.add_argument("--project", required=True); s.add_argument("--force", action="store_true")
        s.add_argument("--dry-run", action="store_true"); s.add_argument("--ef-jar"); s.set_defaults(fn=cmd_add_boss)

    s = sub.add_parser("lore", help="write the boss design doc")
    s.add_argument("spec"); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_lore)

    s = sub.add_parser("qa", help="check a generated project")
    s.add_argument("--project", required=True); s.add_argument("--spec"); s.set_defaults(fn=cmd_qa)

    s = sub.add_parser("render-test", help="render the boss in a real Forge dev client and take screenshots (needs a display + JDK 17)")
    s.add_argument("--project", required=True); s.add_argument("--boss"); s.add_argument("--out")
    s.add_argument("--timeout", type=int, default=900); s.add_argument("--keep", action="store_true", help="keep the temporary project copy")
    s.set_defaults(fn=cmd_render_test)

    s = sub.add_parser("catalog", help="(maintainer) rebuild the Epic Fight animation catalog")
    s.add_argument("--ef-ref", required=True); s.add_argument("--out", required=True); s.set_defaults(fn=cmd_catalog)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args)
    except BossForgeError as e:
        return _fail([e.err])
