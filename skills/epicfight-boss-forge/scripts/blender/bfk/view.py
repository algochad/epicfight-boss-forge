"""bfk.view: render the textured, posed Epic Fight character from several angles (headless Blender, plain functions).

The renders are RGBA PNGs with a transparent background; the Python side (`bossforge_core.blenderview`) composites
them over the background, adds the ground grid / scale bar / labels and builds the montages. This module owns what
only Blender can do: load the bundle, put the nearest-filtered texture on every material (with the alpha cutout the
game uses), pose the rig, evaluate the deformed mesh, frame the cameras and render.

    import bfk; from bfk import view
    bfk.emit(view.run(bfk.get_args()))          # runner: scripts/blender/run_view.py

Space: Blender world space = EF storage space (Z up, the character faces +Y, feet at z = 0, units = blocks).

Pose descriptions (a frame is one pose that gets rendered in all requested views):
  * `ops`:    [[joint, axis, degrees], ...] applied in order through `rig.pose_bone_world_rotate` (WORLD axes, about the
              joint, children follow). joint = EF joint name.
  * `deltas`: {joint: 16 floats row-major} world-space deformation matrices (what `bossforge_core.efanim.blender_deltas`
              produces for an Epic Fight animation): joint i moves its vertices by D_i; see `apply_world_deltas`.
"""
import math
import os
import re
import time
import warnings

import bpy
from mathutils import Matrix, Vector

import bfk
from bfk import efio, rig

try:
    import numpy as np
except ImportError:  # pragma: no cover - Blender ships numpy
    np = None

ALPHA_CUTOFF = 0.1  # the game draws entity textures with a cutout shader: alpha below ~0.1 is discarded, the rest opaque
SENSOR_WIDTH = 36.0
# view name -> (azimuth, elevation) in degrees; rig.view_front_camera conventions (0 = front, +90 = the character's
# left side (camera at -X), -90 = its right side, 180 = back).
VIEW_ANGLES = {
    "front": (0.0, 0.0), "back": (180.0, 0.0), "side": (90.0, 0.0), "left": (90.0, 0.0), "right": (-90.0, 0.0),
    "3q": (35.0, 14.0), "3q_right": (-35.0, 14.0), "3q_back": (145.0, 14.0), "top": (0.0, 90.0),
    "closeup_head": (0.0, 0.0), "closeup_head_3q": (35.0, 8.0),
}
CLOSEUP_VIEWS = ("closeup_head", "closeup_head_3q")


class ViewError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


# ----------------------------------------------------------------------------- engines
def engine_ids(scene):
    """Every render engine id this Blender accepts (the TypeError of an invalid assignment lists them)."""
    try:
        scene.render.engine = "__bf_probe__"
    except TypeError as e:
        found = re.findall(r"'([A-Z0-9_]+)'", str(e))
        if found:
            return found
    return [i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]


def pick_engine(scene, want):
    """Set the render engine for `want` in ("workbench", "eevee") by looking the id up, never hardcoding it."""
    ids = engine_ids(scene)
    key = {"workbench": "WORKBENCH", "eevee": "EEVEE"}.get(want)
    if key is None:
        raise ViewError("bad_engine", "engine must be workbench or eevee, got %r" % (want,))
    cands = sorted([i for i in ids if key in i], key=len)
    if not cands:
        raise ViewError("engine_missing", "this Blender has no %s render engine (available: %s)" % (want, ", ".join(ids)))
    scene.render.engine = cands[0]
    return cands[0]


# ----------------------------------------------------------------------------- materials
def _ensure_nodes(mat):
    if mat.node_tree is None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # Material.use_nodes is deprecated in 5.x but still needed on older materials
            mat.use_nodes = True
    return mat.node_tree


def _principled(nt):
    return next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)


def load_cutout_texture(path, cutoff=ALPHA_CUTOFF):
    """Load the PNG with sRGB colour and apply the game's alpha cutout (alpha > cutoff -> opaque, else transparent)."""
    img = bpy.data.images.load(str(path), check_existing=True)
    img.colorspace_settings.name = 'sRGB'
    img.alpha_mode = 'STRAIGHT'
    clipped = 0
    if cutoff and np is not None and not img.is_float:
        buf = np.empty(img.size[0] * img.size[1] * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        a = buf[3::4]
        clipped = int(np.count_nonzero((a > 0.0) & (a < 1.0)))
        buf[3::4] = np.where(a > cutoff, 1.0, 0.0)
        img.pixels.foreach_set(buf)
        img.update()
    return img, clipped


def apply_texture(objects, texture, cutoff=ALPHA_CUTOFF):
    """Put the atlas on every material slot of `objects`: Closest interpolation, alpha cutout, the image node active
    (Workbench's Texture colour mode reads the active image node). Returns {"texture", "size", "materials"}."""
    img, clipped = load_cutout_texture(texture, cutoff)
    mats = []
    for ob in objects:
        if not ob.material_slots:
            ob.data.materials.append(bpy.data.materials.new(ob.name + "_mat"))
        for slot in ob.material_slots:
            if slot.material is None:
                slot.material = bpy.data.materials.new(ob.name + "_mat")
            mats.append(slot.material)
    done = set()
    for mat in mats:
        if mat.name in done:
            continue
        done.add(mat.name)
        _ensure_nodes(mat)
        nt = mat.node_tree
        bsdf = _principled(nt)
        tex = next((n for n in nt.nodes if n.type == 'TEX_IMAGE'), None)
        if tex is None:
            tex = nt.nodes.new("ShaderNodeTexImage")
            if bsdf is not None:
                tex.location = (bsdf.location[0] - 320, bsdf.location[1])
        tex.image = img
        tex.interpolation = 'Closest'
        tex.extension = 'CLIP'
        for n in nt.nodes:
            n.select = False
        tex.select = True
        nt.nodes.active = tex
        if bsdf is not None:
            for out_name, in_name in (("Color", "Base Color"), ("Alpha", "Alpha")):
                for lk in list(bsdf.inputs[in_name].links):
                    nt.links.remove(lk)
                nt.links.new(tex.outputs[out_name], bsdf.inputs[in_name])
            bsdf.inputs["Roughness"].default_value = 1.0
            for name in ("Specular IOR Level", "Specular"):
                if name in bsdf.inputs:
                    bsdf.inputs[name].default_value = 0.0
        try:
            mat.surface_render_method = 'DITHERED'
        except (AttributeError, TypeError):
            pass
    return {"texture": os.path.basename(str(texture)), "size": [img.size[0], img.size[1]], "materials": sorted(done),
            "semi_transparent_texels_clipped": clipped}


# ----------------------------------------------------------------------------- posing
def reset_pose(arm_obj):
    rig.reset_pose(arm_obj)


def resolve_joint(arm_obj, joint):
    if joint in arm_obj.pose.bones:
        return joint
    low = {b.name.lower(): b.name for b in arm_obj.pose.bones}
    if joint.lower() in low:
        return low[joint.lower()]
    raise ViewError("unknown_bone", "the rig has no bone %r (bones: %s)" % (joint, ", ".join(b.name for b in arm_obj.pose.bones)))


def apply_ops(arm_obj, ops):
    """[[joint, axis, degrees], ...] -> rotations about each joint in WORLD axes, composed in order."""
    for joint, axis, deg in ops:
        rig.pose_bone_world_rotate(arm_obj, resolve_joint(arm_obj, joint), axis, float(deg))


def _mat(rows16):
    return Matrix([rows16[0:4], rows16[4:8], rows16[8:12], rows16[12:16]])


def apply_world_deltas(arm_obj, deltas):
    """Pose the rig so that bone i's deformation (pose * rest^-1) equals the world-space matrix `deltas[i]`.

    Independent of bone rolls: target pose of bone i (armature space) is D_i * rest_i and matrix_basis follows from the
    parent's target: basis_i = (rest_p^-1 rest_i)^-1 (target_p^-1 target_i). Bones without a delta follow their parent.
    """
    mw = arm_obj.matrix_world
    mwi = mw.inverted()
    targets = {}
    bones = arm_obj.data.bones
    order = []
    seen = set()

    def visit(b):
        if b.name in seen:
            return
        if b.parent is not None:
            visit(b.parent)
        seen.add(b.name)
        order.append(b)

    for b in bones:
        visit(b)
    for b in order:
        pb = arm_obj.pose.bones[b.name]
        rest = b.matrix_local.copy()
        d = deltas.get(b.name)
        if b.parent is None:
            tgt = (mwi @ _mat(d) @ mw @ rest) if d is not None else rest
            basis = rest.inverted() @ tgt
        else:
            rel_rest = b.parent.matrix_local.inverted() @ rest
            ptgt = targets[b.parent.name]
            tgt = (mwi @ _mat(d) @ mw @ rest) if d is not None else ptgt @ rel_rest
            basis = rel_rest.inverted() @ (ptgt.inverted() @ tgt)
        targets[b.name] = tgt
        pb.rotation_mode = 'QUATERNION'
        pb.matrix_basis = basis
    bpy.context.view_layer.update()


def apply_frame(arm_obj, frame):
    reset_pose(arm_obj)
    if frame.get("deltas"):
        apply_world_deltas(arm_obj, frame["deltas"])
    if frame.get("ops"):
        apply_ops(arm_obj, frame["ops"])
    bpy.context.view_layer.update()


# ----------------------------------------------------------------------------- measuring
def render_meshes():
    return [o for o in bpy.context.scene.objects if o.type == 'MESH' and not o.hide_render and o.visible_get()]


def evaluated_points(objects):
    """{object name: (N, 3) world positions} of the evaluated (deformed) meshes."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    out = {}
    for ob in objects:
        eo = ob.evaluated_get(dg)
        me = eo.to_mesh()
        try:
            n = len(me.vertices)
            co = np.empty(n * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            co = co.reshape(n, 3)
            m = np.array(eo.matrix_world, dtype=np.float64)
            out[ob.name] = co @ m[:3, :3].T + m[:3, 3]
        finally:
            eo.to_mesh_clear()
    return out


def head_points(mesh_obj, pts, joint="Head", min_weight=0.5):
    """Positions of the vertices that belong mostly to the head bone (falls back to the top 18% of the mesh)."""
    vg = mesh_obj.vertex_groups.get(joint)
    n = len(mesh_obj.data.vertices)
    if vg is not None and len(pts) == n:
        idx = []
        gi = vg.index
        for v in mesh_obj.data.vertices:
            for g in v.groups:
                if g.group == gi and g.weight >= min_weight:
                    idx.append(v.index)
                    break
        if len(idx) >= 8:
            return pts[np.array(idx)]
    top = pts[:, 2].max()
    low = pts[:, 2].min()
    return pts[pts[:, 2] >= top - 0.18 * (top - low)]


def _bounds(p):
    return {"min": [float(v) for v in p.min(axis=0)], "max": [float(v) for v in p.max(axis=0)]}


# ----------------------------------------------------------------------------- cameras and lights
def basis_for(azimuth, elevation):
    """(forward, right, up, offset direction) of a camera looking at its target from azimuth/elevation (rig convention)."""
    az, el = math.radians(azimuth), math.radians(elevation)
    off = Vector((-math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el)))
    fwd = -off
    if elevation >= 89.9:
        right, up = Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0))
    else:
        right = fwd.cross(Vector((0.0, 0.0, 1.0))).normalized()
        up = right.cross(fwd).normalized()
    return fwd, right, up, off


def make_camera(scene, name="BF_ViewCam"):
    cam = bpy.data.objects.get(name)
    if cam is None or cam.type != 'CAMERA':
        cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        scene.collection.objects.link(cam)
    cd = cam.data
    cd.sensor_fit = 'HORIZONTAL'
    cd.sensor_width = SENSOR_WIDTH
    cd.clip_start = 0.05
    cd.clip_end = 400.0
    scene.camera = cam
    return cam


def place_camera(cam, target, azimuth, elevation, ortho, ortho_scale, lens=50.0):
    """Aim `cam` at `target` from azimuth/elevation. Orthographic: `ortho_scale` = blocks across the image width.
    Perspective: the distance is chosen so that the same width is visible at the target plane."""
    fwd, right, up, off = basis_for(azimuth, elevation)
    cd = cam.data
    cd.type = 'ORTHO' if ortho else 'PERSP'
    cd.ortho_scale = ortho_scale
    cd.lens = lens
    dist = 40.0 if ortho else max(ortho_scale * lens / SENSOR_WIDTH, 1.0)
    t = Vector(target)
    cam.location = t + off * dist
    if elevation >= 89.9:
        cam.rotation_euler = (0.0, 0.0, 0.0)
    else:
        cam.rotation_euler = fwd.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.view_layer.update()


def make_lights(scene, cam):
    """Key sun + fill sun parented to the camera, so every view is lit the same way (used by the EEVEE engine)."""
    lights = []
    for name, d, energy in (("BF_Key", (0.45, -0.55, -1.0), 2.6), ("BF_Fill", (-0.7, 0.25, -1.0), 0.9)):
        ld = bpy.data.lights.get(name) or bpy.data.lights.new(name, 'SUN')
        ld.energy = energy
        ld.angle = math.radians(12.0)
        ob = bpy.data.objects.get(name)
        if ob is None:
            ob = bpy.data.objects.new(name, ld)
            scene.collection.objects.link(ob)
        ob.parent = cam
        ob.location = (0.0, 0.0, 0.0)
        ob.rotation_euler = Vector(d).to_track_quat('-Z', 'Y').to_euler()
        lights.append(ob)
    return lights


def setup_render(scene, engine, light, width, height, samples=24):
    """Common render settings (transparent film, PNG RGBA, Standard colour) + the engine specific ones."""
    r = scene.render
    used = pick_engine(scene, engine)
    r.resolution_x, r.resolution_y, r.resolution_percentage = int(width), int(height), 100
    r.film_transparent = True
    r.use_compositing = False
    r.use_sequencer = False
    r.image_settings.file_format = 'PNG'
    r.image_settings.color_mode = 'RGBA'
    r.image_settings.color_depth = '8'
    try:
        scene.view_settings.view_transform = 'Standard'
        scene.view_settings.look = 'None'
    except TypeError:
        pass
    if engine == "workbench":
        sh = scene.display.shading
        sh.light = 'FLAT' if light == 'flat' else 'STUDIO'
        sh.show_object_outline = False
        sh.show_specular_highlight = False
        sh.show_cavity = False
        sh.show_shadows = False
        sh.show_xray = False
        sh.use_dof = False
        try:
            scene.display.render_aa = '8'
        except TypeError:
            pass
    else:
        try:
            scene.eevee.taa_render_samples = int(samples)
        except AttributeError:
            pass
    if scene.world is None:
        scene.world = bpy.data.worlds.new("BF_World")
    scene.world.color = (0.32, 0.32, 0.34)
    return used


def set_color_mode(scene, textured):
    sh = scene.display.shading
    sh.color_type = 'TEXTURE' if textured else 'MATERIAL'


def render_to(scene, path):
    """Render the scene to `path` (PNG) and also write `<path minus .png>.rgba`: width*height*4 straight-alpha bytes, top
    row first, so the stdlib side can read it without decoding the PNG. Returns the .rgba path."""
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path), check_existing=False)
    try:
        w, h = img.size
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        arr = np.rint(buf * 255.0).astype(np.uint8).reshape(h, w, 4)[::-1]
        raw_path = os.path.splitext(str(path))[0] + ".rgba"
        with open(raw_path, "wb") as f:
            f.write(arr.tobytes())
    finally:
        bpy.data.images.remove(img)
    return raw_path


def camera_meta(cam, scene, name):
    cd = cam.data
    m = cam.matrix_world
    return {"view": name, "type": cd.type, "matrix_world": [[float(m[r][c]) for c in range(4)] for r in range(4)],
            "ortho_scale": float(cd.ortho_scale), "lens": float(cd.lens), "sensor_width": float(cd.sensor_width),
            "width": scene.render.resolution_x, "height": scene.render.resolution_y}


# ----------------------------------------------------------------------------- the runner
def _auto_cameras(names, all_pts, head_pts_all, size, margin, projection, lens):
    """Camera specs for the named views framed on the union of every frame's points (consistent scale in all views)."""
    cams = []
    spans = []
    plans = {}
    for name in names:
        if name not in VIEW_ANGLES:
            raise ViewError("unknown_view", "unknown view %r (views: %s)" % (name, ", ".join(sorted(VIEW_ANGLES))))
        az, el = VIEW_ANGLES[name]
        fwd, right, up, _off = basis_for(az, el)
        if name in CLOSEUP_VIEWS:
            pts, mg = head_pts_all, 1.35
        else:
            pts, mg = all_pts, 1.0
        r = pts @ np.array(right)
        u = pts @ np.array(up)
        f = pts @ np.array(fwd)
        u0, u1, v0, v1 = r.min(), r.max(), u.min(), u.max()
        if el < 1.0 and name not in CLOSEUP_VIEWS:
            v0 = min(v0, 0.0)  # keep the ground line (z = 0) in frame
        span = max(u1 - u0, v1 - v0) * mg
        plans[name] = (az, el, (u0 + u1) / 2, (v0 + v1) / 2, (f.min() + f.max()) / 2, span, name in CLOSEUP_VIEWS)
        if name not in CLOSEUP_VIEWS:
            spans.append(span)
    common = (max(spans) if spans else 1.0) * (1.0 + margin)
    for name in names:
        az, el, uc, vc, fc, span, close = plans[name]
        fwd, right, up, _off = basis_for(az, el)
        target = right * uc + up * vc + fwd * fc
        scale = (span * (1.0 + margin * 0.5)) if close else common
        cams.append({"name": name, "azimuth": az, "elevation": el, "target": [target.x, target.y, target.z],
                     "ortho": projection != "persp", "ortho_scale": float(scale), "width": size, "height": size, "lens": lens})
    return cams


def run(args):
    """Render the character. `args` (all paths absolute):

      source        bundle dir / <id>.efmesh.json / EF mesh JSON, or null when the runner opened a .blend
      texture       PNG (alpha cutout + Closest are applied) or null (the importer's flat group tints)
      out           directory for the RGBA PNGs (raw/<frame>__<camera>.png)
      frames        [{"id", "label", "views": [...], "ops"|"deltas", "dump_positions"}], at least one
      cameras       optional explicit cameras [{"name","azimuth","elevation","target","ortho","ortho_scale","width","height"}]
                    rendered for every frame that lists the name in "views" (e.g. pixel aligned reference views)
      engine        "workbench" | "eevee"; light "studio" | "flat"; size (px); projection "ortho" | "persp"; lens
      object, rig   optional names of the mesh / armature in a .blend
    Returns {"ok", "frames": [{"id","label","bbox","head","images": {camera: {"file","camera"}}, "positions"?}], ...}.
    """
    t0 = time.time()
    scene = bpy.context.scene
    out = args["out"]
    raw_dir = os.path.join(out, "raw")
    os.makedirs(raw_dir, exist_ok=True)
    engine = args.get("engine") or "workbench"
    size = int(args.get("size") or 640)
    frames = args.get("frames") or [{"id": "rest", "label": "rest", "views": ["front"]}]
    info = {"ok": True}
    try:
        if args.get("source"):
            bfk.reset_scene()
            scene = bpy.context.scene
            res = efio.import_efmesh(args["source"], mesh_id=args.get("id"))
            mesh, arm = res["object"], res["armature_object"]
            info["import"] = {k: res[k] for k in ("id", "positions", "triangles", "groups", "height", "parts", "placeholder_triangles_skipped") if k in res}
        else:
            mesh, arm = efio.pick_objects(args.get("object"), args.get("rig"))
            info["import"] = {"id": mesh.name}
        if arm is None:
            raise ViewError("no_rig", "the scene has no EF armature (run `bossforge blender import` first)")
        rep = rig.rig_report(arm)
        info["rig"] = {k: rep[k] for k in ("bones", "missing", "extra", "wrong_parent", "max_head_deviation")}
        meshes = render_meshes()
        if mesh not in meshes:
            meshes.insert(0, mesh)
        textured = bool(args.get("texture"))
        if textured:
            info["texture"] = apply_texture(meshes, args["texture"])
        info["mesh"] = {"object": mesh.name, "vertices": len(mesh.data.vertices), "materials": [s.material.name for s in mesh.material_slots if s.material],
                        "render_objects": [o.name for o in meshes]}

        # pose every frame once to measure; the framing is the union so all views share one scale
        measured = []
        for fr in frames:
            apply_frame(arm, fr)
            pts = evaluated_points(meshes)
            allp = np.concatenate(list(pts.values()))
            hp = head_points(mesh, pts[mesh.name]) if mesh.name in pts else allp
            measured.append({"all": allp, "head": hp, "char": pts.get(mesh.name)})
        all_pts = np.concatenate([m["all"] for m in measured])
        head_all = np.concatenate([m["head"] for m in measured])

        cam = make_camera(scene)
        make_lights(scene, cam)
        used = setup_render(scene, engine, args.get("light") or "studio", size, size, int(args.get("samples") or 24))
        set_color_mode(scene, textured)
        info["engine"] = used
        info["blender"] = bpy.app.version_string

        wanted = []
        for fr in frames:
            for v in fr.get("views", []):
                if v not in wanted:
                    wanted.append(v)
        explicit = {c["name"]: c for c in (args.get("cameras") or [])}
        auto_names = [v for v in wanted if v not in explicit]
        auto = {c["name"]: c for c in _auto_cameras(auto_names, all_pts, head_all, size, float(args.get("margin", 0.1)),
                                                      args.get("projection") or "ortho", float(args.get("lens") or 50.0))} if auto_names else {}
        cameras = dict(auto)
        cameras.update(explicit)
        info["cameras"] = {k: {kk: vv for kk, vv in v.items() if kk != "name"} for k, v in cameras.items()}

        result_frames = []
        for fr, meas in zip(frames, measured):
            apply_frame(arm, fr)
            entry = {"id": fr["id"], "label": fr.get("label", fr["id"]), "bbox": _bounds(meas["all"]), "head": _bounds(meas["head"]),
                     "images": {}}
            if fr.get("dump_positions") and meas["char"] is not None:
                entry["positions"] = [[round(float(v), 7) for v in p] for p in meas["char"]]
            for v in fr.get("views", []):
                c = cameras[v]
                w, h = (int(c["width"]), int(c["height"])) if v in explicit else (int(fr.get("size") or size),) * 2
                scene.render.resolution_x, scene.render.resolution_y = w, h
                place_camera(cam, c["target"], c["azimuth"], c["elevation"], c["ortho"], c["ortho_scale"], float(c.get("lens") or 50.0))
                path = os.path.join(raw_dir, "%s__%s.png" % (fr["id"], v))
                raw_path = render_to(scene, path)
                entry["images"][v] = {"file": path, "rgba": raw_path, "camera": camera_meta(cam, scene, v)}
            result_frames.append(entry)
        info["frames"] = result_frames
        info["seconds"] = round(time.time() - t0, 2)
        return info
    except (ViewError, efio.EfIoError) as e:
        return bfk.result_error(e.code, str(e))
