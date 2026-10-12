"""bfk: Blender-side half of the bossforge Blender pipeline (pure bpy, plain functions, no operators).

Importable from headless runners (`_bootstrap.py` sets sys.path) and from a live Blender:

    import sys; sys.path[:0] = ["<skill>/scripts/blender"]; import bfk

Importing `bfk` also puts `<skill>/scripts` on sys.path so the stdlib-only `bossforge_core` (efmodel, rigs.json) can be
imported by the modules. Modules: `rig` (EF biped armature), `efio` (EF mesh bundle <-> Blender mesh), `view` (rendering),
`modeling` (building helpers). A task script does:

    import bfk; from bfk import efio
    args = bfk.get_args()
    bfk.emit(efio.run(args))
"""
import json
import os
import sys

GENERATOR = "bfk/1"
BLENDER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.dirname(BLENDER_DIR)
for _p in (SCRIPTS_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def get_args(default=None):
    """Task arguments: the JSON dict in env BFK_ARGS (headless runner), else `default` ({} when None)."""
    raw = os.environ.get("BFK_ARGS")
    if raw:
        return json.loads(raw)
    return dict(default or {})


def emit(result):
    """Print the single `BFK_RESULT {json}` line the headless runner (and the MCP caller) reads."""
    print("BFK_RESULT " + json.dumps(result, separators=(",", ":"), default=_jsonable))
    sys.stdout.flush()
    return result


def _jsonable(o):
    try:
        return list(o)  # mathutils vectors/matrices
    except TypeError:
        return str(o)


def reset_scene():
    """Empty the current .blend (factory settings without the default cube/camera/light). Wipes the scene, so it refuses to
    run in a live (non-background) Blender; headless runners only."""
    import bpy
    if not bpy.app.background:
        raise RuntimeError("bfk.reset_scene() wipes the open scene and only runs in headless Blender (blender -b)")
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def result_error(code, message, **extra):
    """Failure result dict in the shape every CLI command prints."""
    d = {"ok": False, "errors": [{"path": "", "code": code, "message": message}]}
    d.update(extra)
    return d
