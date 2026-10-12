"""The Epic Fight biped armature in Blender: build it, find it, pose it.

Conventions (see references/blender-pipeline.md):
  * storage space = Blender world space: Z up, character faces +Y, character's right = +X, feet at z = 0, 1 unit = 1 block.
    A model-space (y-up, front = -z) point (x, y, z) maps to (x, -z, y) (`efmodel.to_blender`).
  * bones are named exactly like the EF joints; the bone HEAD sits on the joint position of `assets/data/rigs.json`
    (`biped.joints`), the tail points to the first child joint (`Root` -> `Torso`; leaves get a short bone that continues
    the parent's direction). No bone is connected, so every head is exactly on its joint.
  * bone roll is Blender's default (0), NOT EF's joint frame: EF joint rotations are expressed in model-aligned axes, so
    rotate with `pose_bone_world_rotate` (world axes about the joint), never by writing bone-local rotations.
"""
import math

import bpy
from mathutils import Matrix, Vector

import bfk  # noqa: F401  (puts scripts/ on sys.path for bossforge_core)
from bossforge_core import efmodel
from bossforge_core.spec import data as _data

ARMATURE_NAME = "EF_Biped"
ARMATURE_ID = "epicfight:entity/biped"
LEAF_LENGTH = 0.05
# parent of every EF biped joint (EF's biped.json armature hierarchy, joint order of rigs.json biped.joints)
PARENT = {
    "Root": None,
    "Thigh_R": "Root", "Leg_R": "Thigh_R", "Knee_R": "Thigh_R",
    "Thigh_L": "Root", "Leg_L": "Thigh_L", "Knee_L": "Thigh_L",
    "Torso": "Root", "Chest": "Torso", "Head": "Chest",
    "Shoulder_R": "Chest", "Arm_R": "Shoulder_R", "Hand_R": "Arm_R", "Tool_R": "Hand_R", "Elbow_R": "Arm_R",
    "Shoulder_L": "Chest", "Arm_L": "Shoulder_L", "Hand_L": "Arm_L", "Tool_L": "Hand_L", "Elbow_L": "Arm_L",
}
# the bone tail normally points to the first child in joint order; Root's first child (Thigh_R) is sideways
TAIL_CHILD = {"Root": "Torso"}


def _joints():
    return _data("rigs")["biped"]["joints"]


def joint_names():
    """EF joint names in joint-index order (= vertex group / bone order)."""
    return [j["name"] for j in sorted(_joints(), key=lambda j: j["index"])]


def joint_index(name):
    """Joint index of an EF joint name (the index `vindices` of the mesh JSON uses)."""
    for j in _joints():
        if j["name"] == name:
            return j["index"]
    raise KeyError("no EF biped joint %r (joints: %s)" % (name, ", ".join(joint_names())))


def joint_name(index):
    return joint_names()[index]


def bone_for_bossforge_bone(name):
    """EF joint/bone name for a bossforge bone name of rigs.json `biped.bones` (e.g. "arm_r" -> "Arm_R")."""
    bones = _data("rigs")["biped"]["bones"]
    if name not in bones:
        raise KeyError("unknown bossforge bone %r (bones: %s)" % (name, ", ".join(sorted(bones))))
    return bones[name]


def joint_head(name):
    """Rest position of an EF joint in storage space (Blender world space) as a Vector."""
    for j in _joints():
        if j["name"] == name:
            return Vector(efmodel.to_blender(j["pos"]))
    raise KeyError("no EF biped joint %r" % (name,))


def children(name):
    return [n for n in joint_names() if PARENT[n] == name]


def tail_joint(name):
    """Joint the bone `name` points to, or None for a leaf."""
    if name in TAIL_CHILD:
        return TAIL_CHILD[name]
    kids = children(name)
    return kids[0] if kids else None


def _bone_tails():
    """{joint: tail Vector}; leaves continue the parent's direction (or +Z for the root) for LEAF_LENGTH."""
    heads = {n: joint_head(n) for n in joint_names()}
    tails = {}
    for n in joint_names():  # parents come before children in joint order
        t = tail_joint(n)
        if t is not None and (heads[t] - heads[n]).length > 1e-5:
            tails[n] = heads[t].copy()
            continue
        par = PARENT[n]
        d = (tails[par] - heads[par]) if par is not None else Vector((0.0, 0.0, 1.0))
        if d.length < 1e-6:
            d = Vector((0.0, 0.0, 1.0))
        tails[n] = heads[n] + d.normalized() * LEAF_LENGTH
    return tails


def find_rig(name=None):
    """The EF biped armature object: the one called `name` (default EF_Biped), else any armature tagged `bossforge_rig`."""
    o = bpy.data.objects.get(name or ARMATURE_NAME)
    if o is not None and o.type == 'ARMATURE':
        return o
    if name:
        return None
    for o in bpy.data.objects:
        if o.type == 'ARMATURE' and o.get("bossforge_rig") == ARMATURE_ID:
            return o
    return None


def _mode_set(obj, mode):
    """bpy.ops.object.mode_set, with a context override when the caller has no suitable UI context (live Blender via MCP)."""
    try:
        bpy.ops.object.mode_set(mode=mode)
    except RuntimeError:
        with bpy.context.temp_override(active_object=obj, object=obj, selected_objects=[obj]):
            bpy.ops.object.mode_set(mode=mode)


def build_rig(name=ARMATURE_NAME, collection=None, display_type='OCTAHEDRAL', in_front=True):
    """Create the EF biped armature object `name` in the current .blend and return it.

    Bones, hierarchy and rest positions follow the contract above. The object sits at the origin with an identity
    transform and carries the custom properties `bossforge_rig` (armature id) and `bossforge_joints`.
    """
    arm = bpy.data.armatures.new(name)
    obj = bpy.data.objects.new(name, arm)
    (collection or bpy.context.scene.collection).objects.link(obj)
    arm.display_type = display_type
    obj.show_in_front = in_front
    obj["bossforge_rig"] = ARMATURE_ID
    obj["bossforge_joints"] = joint_names()
    prev_active = bpy.context.view_layer.objects.active
    prev_mode = bpy.context.mode
    if prev_active is not None and prev_mode != 'OBJECT':
        _mode_set(prev_active, 'OBJECT')
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    _mode_set(obj, 'EDIT')
    try:
        heads = {n: joint_head(n) for n in joint_names()}
        tails = _bone_tails()
        eb = {}
        for n in joint_names():
            b = arm.edit_bones.new(n)
            b.head = heads[n]
            b.tail = tails[n]
            b.roll = 0.0
            b.use_connect = False
            b.use_deform = True
            eb[n] = b
        for n in joint_names():
            if PARENT[n] is not None:
                eb[n].parent = eb[PARENT[n]]
    finally:
        _mode_set(obj, 'OBJECT')
    for n in joint_names():
        arm.bones[n]["ef_joint_index"] = joint_index(n)
    if prev_active is not None and prev_active.name in bpy.data.objects:
        bpy.context.view_layer.objects.active = prev_active
    return obj


def rig_report(arm_obj):
    """Measured facts about an armature object: bone names, parents, head positions and deviations from the EF rig."""
    names = joint_names()
    bones = arm_obj.data.bones
    extra = [b.name for b in bones if b.name not in names]
    missing = [n for n in names if n not in bones]
    max_dev = 0.0
    wrong_parent = []
    mw = arm_obj.matrix_world
    for n in names:
        b = bones.get(n)
        if b is None:
            continue
        max_dev = max(max_dev, (mw @ b.head_local - joint_head(n)).length)
        want = PARENT[n]
        have = b.parent.name if b.parent else None
        if want != have:
            wrong_parent.append(n)
    return {"bones": len(bones), "missing": missing, "extra": extra, "wrong_parent": wrong_parent,
            "max_head_deviation": max_dev}


def reset_pose(arm_obj):
    """Return every pose bone to the rest pose."""
    for pb in arm_obj.pose.bones:
        pb.rotation_mode = 'QUATERNION'
        pb.location = (0.0, 0.0, 0.0)
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pb.rotation_euler = (0.0, 0.0, 0.0)
        pb.scale = (1.0, 1.0, 1.0)
    bpy.context.view_layer.update()


def pose_bone_world_rotate(arm_obj, joint_name, axis, degrees):
    """Rotate pose bone `joint_name` by `degrees` about `axis` through its joint, with the axis in WORLD (storage) space.

    `axis` is 'X' | 'Y' | 'Z' (optionally '-X'...) or a 3-vector. The rotation is applied on top of the bone's current pose
    (calls compose in world axes, child joints follow). Positive degrees are right-handed (counter-clockwise looking
    down the axis towards the origin). EF joint rotations use model-aligned axes, so this is the one correct way to pose
    a joint by an angle; do not set bone-local rotation channels directly.
    """
    pb = arm_obj.pose.bones[joint_name]
    if isinstance(axis, str):
        sign = -1.0 if axis.startswith("-") else 1.0
        ax = Vector({"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}[axis.lstrip("+-").upper()]) * sign
    else:
        ax = Vector(axis)
    mw = arm_obj.matrix_world
    bpy.context.view_layer.update()
    head_w = mw @ pb.head
    rot = Matrix.Translation(head_w) @ Matrix.Rotation(math.radians(degrees), 4, ax.normalized()) @ Matrix.Translation(-head_w)
    pb.matrix = mw.inverted() @ (rot @ (mw @ pb.matrix))
    bpy.context.view_layer.update()


def evaluated_world_positions(mesh_obj):
    """World-space positions of the mesh's vertices after all modifiers (the posed result), in vertex order."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    eo = mesh_obj.evaluated_get(dg)
    me = eo.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
    try:
        mw = eo.matrix_world
        return [tuple(mw @ v.co) for v in me.vertices]
    finally:
        eo.to_mesh_clear()


VIEW_AZIMUTH = {"front": 0.0, "3q": 35.0, "3q_left": 35.0, "3q_right": -35.0, "side": 90.0, "left": 90.0, "right": -90.0, "back": 180.0}


def view_front_camera(target=(0.0, 0.0, 1.0), distance=6.0, azimuth=0.0, elevation=0.0, ortho=False, ortho_scale=2.6,
                      lens=50.0, name="BF_Camera", scene=None, make_active=True):
    """Create (or reuse) a camera that looks at the character's face and return it.

    The character faces +Y, so the default camera stands at +Y looking along -Y (Blender's numpad "front" view looks at the
    BACK of an EF model). `azimuth` degrees turn the camera about the vertical axis through `target`: 0 = front, 90 = the
    character's left side (camera at -X), -90 = its right side (+X), 180 = back; `elevation` raises it. `azimuth` may be a
    key of `VIEW_AZIMUTH` ("front", "3q", "side", "back").
    """
    scn = scene or bpy.context.scene
    if isinstance(azimuth, str):
        azimuth = VIEW_AZIMUTH[azimuth]
    cam = bpy.data.objects.get(name)
    if cam is None or cam.type != 'CAMERA':
        cam = bpy.data.objects.new(name, bpy.data.cameras.new(name))
        scn.collection.objects.link(cam)
    cd = cam.data
    cd.type = 'ORTHO' if ortho else 'PERSP'
    cd.ortho_scale = ortho_scale
    cd.lens = lens
    cd.clip_start = 0.05
    cd.clip_end = 100.0
    t = Vector(target)
    az = math.radians(azimuth)
    el = math.radians(elevation)
    off = Vector((-math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el))) * distance
    cam.location = t + off
    cam.rotation_euler = (t - cam.location).to_track_quat('-Z', 'Y').to_euler()
    if make_active:
        scn.camera = cam
    return cam


def run(args):
    """Runner entry: `{"out": FILE.blend, "name": "EF_Biped", "reset": bool}` -> the EF rig; with `out` it is saved as a new
    .blend. `reset` (default: true headless, false in a live Blender) first empties the scene."""
    if args.get("reset", bpy.app.background):
        bfk.reset_scene()
    obj = build_rig(args.get("name") or ARMATURE_NAME)
    out = args.get("out")
    if out:
        bpy.ops.wm.save_as_mainfile(filepath=str(out))
    rep = rig_report(obj)
    return {"ok": not (rep["missing"] or rep["extra"] or rep["wrong_parent"]), "name": obj.name, "out": out,
            "armature": ARMATURE_ID, "bones": rep["bones"], "joint_order": joint_names(),
            "max_head_deviation": rep["max_head_deviation"], "storage_space": "Z up, character faces +Y, feet z=0, units = blocks"}
