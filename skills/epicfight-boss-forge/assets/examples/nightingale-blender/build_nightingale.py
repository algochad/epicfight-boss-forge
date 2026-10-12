"""Build the Nightingale as a real Blender model for the Epic Fight biped rig and export the EF mesh bundle.

    blender -b --factory-startup --python build_nightingale.py -- --out DIR [--id nightingale] [--checks] [--set key=value ...]
    bossforge blender ... (any runner that sets BFK_ARGS {"out": DIR, "id": ..., "params": {...}})

Deterministic: no randomness outside `params["seed"]`, same bytes every run. The model is code (kit: `bfk.modeling`), parts live in
`nt_body`, `nt_hair`, `nt_wings`, `nt_extras`; reference measurements and weight rules in `nt_common`. Outputs in DIR:
`<id>.efmesh.json` + `<id>.meshmeta.json` (BlenderKit's `bfk.efio.export_efmesh`), `<id>.blend` (the editable scene: mesh object +
EF_Biped rig, UV map `UVMap`, one material slot per group) and `build_report.json`.
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import nt_common  # noqa: E402
from nt_common import GROUPS, GROUP_NAMES, K, PREVIEW  # noqa: E402


PARTS = ("nt_hair", "nt_ribbons", "nt_wings", "nt_extras")  # add(m, P) modules after the body; `params["parts"]` overrides


def build_model(over=None):
    """Pure-Python geometry (no bpy): returns (Model, params)."""
    import nt_body
    P = nt_common.params(over)
    m = K.Model(GROUP_NAMES, atlas=P["atlas"], margin=P["margin"], density=dict(GROUPS), default_density=64.0)
    nt_body.add_body(m, P)
    for name in P["parts"] or PARTS:
        try:
            mod = __import__(name)
        except ImportError:
            continue
        mod.add(m, P)
    return m, P


def build_scene(over=None, image=None, reset=None):
    """Model -> Blender scene (EF_Biped rig + mesh object). Returns a dict with model, built, obj, rig, params. `reset` wipes the
    current .blend first (default: only when Blender runs headless, so exec'ing this in a live session never deletes the scene)."""
    import bpy
    import bfk
    from bfk import rig as bfk_rig
    m, P = build_model(over)
    built = m.build()
    if bpy.app.background if reset is None else reset:
        bfk.reset_scene()
    arm = bfk_rig.build_rig()
    obj = K.to_blender(built, "Nightingale", m.groups, rig=arm, colors=PREVIEW, image=image)
    return {"model": m, "built": built, "obj": obj, "rig": arm, "params": P}


def run(args):
    """Runner entry. args: out (directory, required), id ("nightingale"), params ({}), checks (False: also measure skinning totals of
    the exported bundle and cloth / ribbon / wing poke-through at the sample poses of `nt_checks.POSES`). Builds, saves
    `<id>.blend` (unless `save_blend` is false), exports the bundle with `bfk.efio.export_efmesh`, writes `build_report.json`, returns
    the report dict."""
    import bpy
    from bfk import efio
    out = args["out"]
    os.makedirs(out, exist_ok=True)
    mesh_id = args.get("id", "nightingale")
    res = build_scene(args.get("params"))
    built, obj, arm = res["built"], res["obj"], res["rig"]
    rep = efio.export_efmesh(obj, arm, out, mesh_id, max_influences=3, source="%s.blend" % mesh_id)
    result = {"ok": bool(rep.get("ok")), "id": mesh_id, "triangles": built.triangles(), "vertices": len(built.positions),
              "atlas": built.atlas, "texel_scale": round(built.scale, 3), "density_texels_per_block": built.density_report,
              "weights": K.weight_report(obj, [g.name for g in obj.vertex_groups]),
              "export": {k: rep.get(k) for k in ("ok", "triangles", "positions", "uvs", "normals", "groups", "max_influences", "height", "bounds",
                                                  "facing", "check_mesh_json", "errors", "warnings", "influence_histogram", "triangles_by_part")},
              "files": [os.path.basename(f) for f in rep.get("files", [])]}
    if args.get("checks"):
        import nt_checks
        with open(os.path.join(out, mesh_id + ".efmesh.json")) as fh:
            result["bundle_weights"] = nt_checks.bundle_weight_check(json.load(fh))
        result["poke_through"] = nt_checks.pose_report(res)  # leaves the rig in the rest pose and removes its temporary objects
    if args.get("save_blend", True):
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(out, mesh_id + ".blend"), compress=True)
    with open(os.path.join(out, "build_report.json"), "w") as fh:
        json.dump(result, fh, indent=1, default=str)
    return result


def _cli_args():
    import bfk
    a = bfk.get_args()
    if a:
        return a
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = {"params": {}}
    it = iter(argv)
    for tok in it:
        if tok == "--out":
            out["out"] = next(it)
        elif tok == "--id":
            out["id"] = next(it)
        elif tok == "--checks":
            out["checks"] = True
        elif tok == "--set":
            k, v = next(it).split("=", 1)
            try:
                out["params"][k] = json.loads(v)
            except ValueError:
                out["params"][k] = v
    return out


if __name__ == "__main__":
    import bfk
    bfk.emit(run(_cli_args()))
