"""Runner: headless smoke test of Blender for the bossforge pipeline. Prints version, python, engines and API facts.

Builds the EF rig, a one-triangle mesh, renders 64x64 with Workbench and checks the PNG exists. Headless only (it wipes the
scene). Args: `tmp` (directory for the test render, default BFK_TMP or the system temp dir).
"""
import os
import re
import sys
import tempfile

import bpy

import bfk
from bfk import rig

args = bfk.get_args()
facts = {"ok": True, "blender": bpy.app.version_string, "blender_version": list(bpy.app.version),
         "python": sys.version.split()[0], "background": bool(bpy.app.background), "checks": {}}
checks = facts["checks"]


def engines():
    """Render engines this build accepts for scene.render.engine (read from the TypeError of an invalid assignment)."""
    scn = bpy.context.scene
    current = scn.render.engine
    try:
        scn.render.engine = "BFK_NO_SUCH_ENGINE"
    except TypeError as e:
        msg = str(e)
        found = [t for t in re.findall(r"'([A-Z0-9_]+)'", msg) if t != "BFK_NO_SUCH_ENGINE"]
        scn.render.engine = current
        return found
    scn.render.engine = current
    return []


bfk.reset_scene()
scene = bpy.context.scene
facts["engines"] = engines()
workbench = next((e for e in facts["engines"] if "WORKBENCH" in e), None)
facts["workbench_engine"] = workbench

try:
    obj = rig.build_rig()
    rep = rig.rig_report(obj)
    checks["rig"] = {"ok": not (rep["missing"] or rep["extra"] or rep["wrong_parent"]) and rep["max_head_deviation"] < 1e-6, "bones": rep["bones"]}
except Exception as e:  # noqa: BLE001
    checks["rig"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

try:
    me = bpy.data.meshes.new("t")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (0, 0, 1)], [], [(0, 1, 2)])
    me.shade_smooth()
    me.normals_split_custom_set([(0, -1, 0)] * 3)
    me.calc_loop_triangles()
    ob = bpy.data.objects.new("t", me)
    scene.collection.objects.link(ob)
    checks["mesh_api"] = {"ok": True, "custom_normals": bool(me.has_custom_normals),
                          "corner_normals": len(me.corner_normals) == 3, "loop_triangles": len(me.loop_triangles) == 1}
except Exception as e:  # noqa: BLE001
    checks["mesh_api"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

try:
    from bossforge_core import efmodel
    checks["bossforge_core"] = {"ok": efmodel.joint_count() == 20, "joints": efmodel.joint_count()}
except Exception as e:  # noqa: BLE001
    checks["bossforge_core"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

try:
    out_dir = args.get("tmp") or os.environ.get("BFK_TMP") or tempfile.gettempdir()
    png = os.path.join(out_dir, "bfk_doctor.png")
    cam = rig.view_front_camera(target=(0, 0, 0.3), distance=4.0)
    if workbench:
        scene.render.engine = workbench
    scene.render.resolution_x = scene.render.resolution_y = 64
    scene.render.resolution_percentage = 100
    scene.render.filepath = png
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)
    size = os.path.getsize(png) if os.path.isfile(png) else 0
    img = bpy.data.images.load(png)
    checks["render"] = {"ok": size > 0 and tuple(img.size) == (64, 64), "engine": scene.render.engine, "png_bytes": size, "size": list(img.size)}
    try:
        os.remove(png)
    except OSError:
        pass
except Exception as e:  # noqa: BLE001
    checks["render"] = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

facts["ok"] = all(c.get("ok") for c in checks.values())
bfk.emit(facts)
