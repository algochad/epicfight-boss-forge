"""Epic Fight mesh bundle <-> Blender: `import_efmesh` and `export_efmesh`.

The bundle is `<id>.efmesh.json` (the exact EF mesh JSON, Blender Z-up storage space, UV v from the top row) plus the
bossforge sidecar `<id>.meshmeta.json` (material-slot "groups", see references/blender-pipeline.md). Storage space is Blender
world space, so no coordinate conversion happens here; only the UV v flip (`v_blender = 1 - v_ef`).

Mapping: one EF *position* = one Blender vertex (weights live there, vertex groups are named by EF joint). One EF vertex
record `(position, uv, normal)` = one triangle corner = one Blender loop (UV per loop, normal per loop as a custom split
normal). The 12 EF parts are kept in the integer face attribute `bf_part` (1-based index into `efmodel.EF_PARTS`, 0 = decide
by the dominant joint on export). Placeholder triangles of empty parts (all three corners on the same position) are not
imported and are regenerated on export.
"""
import json
import math
import os
import re
import warnings
from pathlib import Path

import bpy
from mathutils import Vector

import bfk
from bfk import rig
from bossforge_core import efmodel

MAX_INFLUENCES = 4          # EF has no hard limit (see references/blender-pipeline.md); stock meshes use <= 3
MIN_WEIGHT = 0.001          # weights below this are dropped before normalising
WEIGHT_DECIMALS = 4
PART_ATTR = "bf_part"
DEFAULT_GROUP = "skin"
_GROUP_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SUFFIX_RE = re.compile(r"\.\d{3}$")

# dominant EF joint -> EF part
JOINT_PART = {}
for _j in ("Root", "Torso", "Chest"):
    JOINT_PART[_j] = "torso"
JOINT_PART["Head"] = "head"
for _s, _side in (("R", "right"), ("L", "left")):
    for _j in ("Shoulder", "Arm", "Hand", "Tool", "Elbow"):
        JOINT_PART["%s_%s" % (_j, _s)] = _side + "Arm"
    for _j in ("Thigh", "Leg", "Knee"):
        JOINT_PART["%s_%s" % (_j, _s)] = _side + "Leg"


class EfIoError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# ----------------------------------------------------------------------------- reading a bundle
def find_bundle(path, mesh_id=None):
    """Resolve a directory, `<id>.efmesh.json` or plain EF mesh JSON file -> {"mesh": Path, "meta": Path|None, "id": str}."""
    p = Path(path)
    if p.is_dir():
        cands = sorted(p.glob("*.efmesh.json"))
        if mesh_id:
            cands = [c for c in cands if c.name == mesh_id + ".efmesh.json"] or sorted(p.glob(mesh_id + ".json"))
        if not cands:
            cands = [c for c in sorted(p.glob("*.json")) if not c.name.endswith(".meshmeta.json") and _looks_like_mesh(c)]
        if len(cands) != 1:
            raise EfIoError("bad_bundle", "%s: expected exactly one EF mesh JSON, found %d (%s); pass the file or --id"
                            % (p, len(cands), ", ".join(c.name for c in cands[:6])))
        p = cands[0]
    if not p.is_file():
        raise EfIoError("bad_bundle", "%s does not exist" % p)
    stem = p.name[:-len(".efmesh.json")] if p.name.endswith(".efmesh.json") else p.stem
    meta = p.with_name(stem + ".meshmeta.json")
    return {"mesh": p, "meta": meta if meta.is_file() else None, "id": stem}


def _looks_like_mesh(path):
    try:
        with open(str(path), "r", encoding="utf-8") as f:
            head = f.read(4096)
        return '"vertices"' in head
    except OSError:
        return False


def _array(v, key):
    obj = v.get(key)
    if not isinstance(obj, dict) or not isinstance(obj.get("array"), list):
        raise EfIoError("bad_mesh", "vertices.%s must be an object with an 'array' list" % key)
    return obj["array"]


def parse_mesh(d):
    """EF mesh dict -> positions, normals, uvs (lists of tuples), influences (list of [(joint, weight)]), parts."""
    if not isinstance(d, dict) or not isinstance(d.get("vertices"), dict):
        raise EfIoError("bad_mesh", "not an Epic Fight mesh: top level needs a 'vertices' object")
    v = d["vertices"]
    pos_f = _array(v, "positions")
    nrm_f = _array(v, "normals") if "normals" in v else []
    uv_f = _array(v, "uvs") if "uvs" in v else []
    vcounts = _array(v, "vcounts")
    weights = _array(v, "weights")
    vind = _array(v, "vindices")
    parts = v.get("parts")
    if not isinstance(parts, dict):
        raise EfIoError("bad_mesh", "vertices.parts must be an object")
    if len(pos_f) % 3 or len(nrm_f) % 3 or len(uv_f) % 2:
        raise EfIoError("bad_mesh", "positions/normals need a multiple of 3 floats, uvs a multiple of 2")
    pos = [(pos_f[i], pos_f[i + 1], pos_f[i + 2]) for i in range(0, len(pos_f), 3)]
    nrm = [(nrm_f[i], nrm_f[i + 1], nrm_f[i + 2]) for i in range(0, len(nrm_f), 3)]
    uvs = [(uv_f[i], uv_f[i + 1]) for i in range(0, len(uv_f), 2)]
    if len(vcounts) != len(pos):
        raise EfIoError("bad_mesh", "vcounts has %d entries for %d positions" % (len(vcounts), len(pos)))
    infl = []
    k = 0
    for c in vcounts:
        lst = []
        for _ in range(c):
            lst.append((int(vind[k]), float(weights[vind[k + 1]])))
            k += 2
        infl.append(lst)
    if k != len(vind):
        raise EfIoError("bad_mesh", "vindices length does not match sum(vcounts)*2")
    out_parts = {}
    for name, obj in parts.items():
        arr = obj.get("array") if isinstance(obj, dict) else None
        if not isinstance(arr, list) or len(arr) % 9:
            raise EfIoError("bad_mesh", "part %s needs an 'array' whose length is a multiple of 9" % name)
        out_parts[name] = arr
    return pos, nrm, uvs, infl, out_parts


def _safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s) or "mesh"


# ----------------------------------------------------------------------------- materials
def _ensure_nodes(mat):
    if mat.node_tree is None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            mat.use_nodes = True
    return mat.node_tree


def _hsv_color(i):
    import colorsys
    r, g, b = colorsys.hsv_to_rgb((i * 0.381966) % 1.0, 0.45, 0.8)
    return (r, g, b, 1.0)


def make_group_material(name, index, image=None):
    """Material `name` (a bossforge group): Principled BSDF fed by `image` with Closest interpolation, or a flat tint."""
    mat = bpy.data.materials.new(name)
    nt = _ensure_nodes(mat)
    bsdf = next(n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED')
    bsdf.inputs["Roughness"].default_value = 1.0
    if image is not None:
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.interpolation = 'Closest'
        tex.location = (bsdf.location[0] - 320, bsdf.location[1])
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
    else:
        bsdf.inputs["Base Color"].default_value = _hsv_color(index)
    mat.diffuse_color = _hsv_color(index)
    return mat


def load_texture(path):
    img = bpy.data.images.load(str(path), check_existing=True)
    img.colorspace_settings.name = 'sRGB'
    try:
        img.pack()
    except RuntimeError:
        pass
    return img


# ----------------------------------------------------------------------------- import
def import_efmesh(source, name=None, arm_obj=None, texture=None, mesh_id=None, custom_normals=True, collection=None,
                  parent_to_rig=True):
    """Load an EF mesh bundle / EF mesh JSON into the current scene.

    `source`: bundle directory, `<id>.efmesh.json`, or any EF mesh JSON (a generated `animmodels/entity/<id>.json`, EF's own
    `biped.json`). Creates (or reuses `arm_obj`, else an existing `EF_Biped`, else a new one) the EF rig, a mesh object named
    `name` (default the bundle id) with vertex groups for all 20 joints (weights from the JSON, several per vertex), the UV
    map `UVMap`, custom split normals from the JSON normals, an Armature modifier on the rig and one material slot per
    sidecar group (or one `skin` slot). Returns a report dict that includes the objects under "object" / "armature_object".
    """
    b = find_bundle(source, mesh_id)
    with open(str(b["mesh"]), "r", encoding="utf-8") as f:
        raw = json.load(f)
    pos, nrm, uvs, infl, parts = parse_mesh(raw)
    emb = raw.get("armature") if isinstance(raw, dict) else None
    if isinstance(emb, dict) and isinstance(emb.get("joints"), list) and emb["joints"] != rig.joint_names():
        raise EfIoError("wrong_armature", "the mesh carries a different armature (%s ...); only the EF biped (%s ...) is supported"
                        % (", ".join(str(j) for j in emb["joints"][:5]), ", ".join(rig.joint_names()[:5])))
    meta = None
    if b["meta"] is not None:
        with open(str(b["meta"]), "r", encoding="utf-8") as f:
            meta = json.load(f)
    names = rig.joint_names()
    nj = len(names)
    for i, lst in enumerate(infl):
        for j, _w in lst:
            if j < 0 or j >= nj:
                raise EfIoError("bad_joint", "position %d uses joint %d, but the EF biped has joints 0..%d (is this the biped mesh?)" % (i, j, nj - 1))

    tris = []       # (part name, part ordinal, tri index in part, ((p, u, n) x 3))
    skipped = 0
    order = [p for p in efmodel.EF_PARTS if p in parts] + [p for p in parts if p not in efmodel.EF_PARTS]
    for part in order:
        arr = parts[part]
        for k in range(len(arr) // 9):
            tri = tuple((arr[9 * k + 3 * c], arr[9 * k + 3 * c + 1], arr[9 * k + 3 * c + 2]) for c in range(3))
            if len({t[0] for t in tri}) < 3:
                skipped += 1
                continue
            for p, u, n in tri:
                if not (0 <= p < len(pos)) or not (0 <= u < len(uvs)) or (nrm and not 0 <= n < len(nrm)):
                    raise EfIoError("bad_mesh", "part %s triangle %d has an index out of range" % (part, k))
            tris.append((part, k, tri))
    if not tris:
        raise EfIoError("bad_mesh", "the mesh has no triangles")

    used = sorted({p for _part, _k, tri in tris for p, _u, _n in tri})
    remap = {p: i for i, p in enumerate(used)}
    verts = [pos[p] for p in used]
    faces = [tuple(remap[c[0]] for c in tri) for _part, _k, tri in tris]
    mesh_name = name or b["id"]
    me = bpy.data.meshes.new(mesh_name)
    me.from_pydata(verts, [], faces)
    uvl = me.uv_layers.new(name="UVMap")
    flat_uv = []
    for _part, _k, tri in tris:
        for _p, u, _n in tri:
            flat_uv.extend((uvs[u][0], 1.0 - uvs[u][1]))
    uvl.uv.foreach_set("vector", flat_uv)
    me.shade_smooth()
    has_normals = bool(nrm) and custom_normals
    if has_normals:
        loop_n = []
        for _part, _k, tri in tris:
            for _p, _u, n in tri:
                x, y, z = nrm[n]
                ln = math.sqrt(x * x + y * y + z * z) or 1.0
                loop_n.append((x / ln, y / ln, z / ln))
        me.normals_split_custom_set(loop_n)
    attr = me.attributes.new(PART_ATTR, 'INT', 'FACE')
    attr.data.foreach_set("value", [(efmodel.EF_PARTS.index(part) + 1) if part in efmodel.EF_PARTS else 0 for part, _k, _t in tris])

    # material slots = groups
    groups = list(meta["groups"]) if meta and meta.get("groups") else [DEFAULT_GROUP]
    tri_group = []
    for part, k, _tri in tris:
        g = 0
        if meta and meta.get("tri_groups") and part in meta["tri_groups"]:
            tg = meta["tri_groups"][part]
            if k < len(tg):
                g = int(tg[k])
        tri_group.append(g if 0 <= g < len(groups) else 0)
    image = load_texture(texture) if texture else None
    for gi, g in enumerate(groups):
        me.materials.append(make_group_material(g, gi, image))
    me.polygons.foreach_set("material_index", tri_group)

    obj = bpy.data.objects.new(mesh_name, me)
    (collection or bpy.context.scene.collection).objects.link(obj)
    # vertex groups named by joint, group index = joint index
    for n in names:
        obj.vertex_groups.new(name=n)
    buckets = {}
    hist = {}
    for i, p in enumerate(used):
        lst = infl[p]
        hist[len(lst)] = hist.get(len(lst), 0) + 1
        for j, w in lst:
            buckets.setdefault((j, w), []).append(i)
    for (j, w), idx in buckets.items():
        obj.vertex_groups[j].add(idx, w, 'REPLACE')

    arm = arm_obj or rig.find_rig()
    created_rig = False
    if arm is None:
        arm = rig.build_rig(collection=collection)
        created_rig = True
    mod = obj.modifiers.new("Armature", 'ARMATURE')
    mod.object = arm
    mod.use_vertex_groups = True
    if parent_to_rig:
        obj.parent = arm
    obj["bossforge_mesh_id"] = b["id"]
    obj["bossforge_source"] = str(b["mesh"].name)
    if meta and "height" in meta:
        obj["bossforge_height"] = float(meta["height"])

    part_counts = {}
    for part, _k, _t in tris:
        part_counts[part] = part_counts.get(part, 0) + 1
    zs = [v[2] for v in verts]
    return {"ok": True, "object": obj, "armature_object": arm, "mesh": me, "id": b["id"], "files": [str(b["mesh"])] + ([str(b["meta"])] if b["meta"] else []),
            "positions": len(verts), "triangles": len(tris), "parts": part_counts, "placeholder_triangles_skipped": skipped,
            "groups": groups, "influence_histogram": {str(k): v for k, v in sorted(hist.items())},
            "custom_normals": has_normals, "rig_created": created_rig, "texture": str(texture) if texture else None,
            "height": round(max(zs) - min(zs), 5), "min_z": round(min(zs), 5)}


# ----------------------------------------------------------------------------- export
def sanitize_group(name):
    """Material slot name -> bossforge group name: drop Blender's `.001` suffix, lower-case, [a-z0-9_] only."""
    base = _SUFFIX_RE.sub("", name).strip().lower()
    base = re.sub(r"[^a-z0-9_]+", "_", base).strip("_")
    if not base or not base[0].isalpha():
        base = "g_" + base if base else DEFAULT_GROUP
    return base[:64]


def pick_objects(object_name=None, rig_name=None):
    """Resolve the mesh object and the rig for an export (explicit names win; otherwise the scene decides)."""
    arm = None
    if rig_name:
        arm = bpy.data.objects.get(rig_name)
        if arm is None or arm.type != 'ARMATURE':
            raise EfIoError("no_rig", "no armature object named %r" % rig_name)
    mesh = None
    if object_name:
        mesh = bpy.data.objects.get(object_name)
        if mesh is None or mesh.type != 'MESH':
            raise EfIoError("no_object", "no mesh object named %r (meshes: %s)" % (object_name, ", ".join(o.name for o in bpy.data.objects if o.type == 'MESH')))
    else:
        names = set(rig.joint_names())
        meshes = [o for o in bpy.context.scene.objects if o.type == 'MESH']
        def score(o):
            s = 0
            if any(m.type == 'ARMATURE' and m.object is not None for m in o.modifiers):
                s += 2
            if any(g.name in names for g in o.vertex_groups):
                s += 1
            return s
        ranked = sorted(meshes, key=score, reverse=True)
        if not ranked:
            raise EfIoError("no_object", "the scene has no mesh object")
        top = [o for o in ranked if score(o) == score(ranked[0])]
        if len(top) > 1:
            raise EfIoError("ambiguous_object", "several mesh objects qualify (%s); pass --object NAME" % ", ".join(o.name for o in top))
        mesh = top[0]
    if arm is None:
        for m in mesh.modifiers:
            if m.type == 'ARMATURE' and m.object is not None:
                arm = m.object
                break
    if arm is None:
        arm = rig.find_rig()
    return mesh, arm


class _Warn:
    def __init__(self):
        self.items = []

    def add(self, code, message, **extra):
        d = {"code": code, "message": message}
        d.update(extra)
        self.items.append(d)


def _evaluated_rest_mesh(mesh_obj):
    """Evaluated mesh (all modifiers except Armature) in the rest pose; the caller releases it with `eo.to_mesh_clear()`."""
    saved = []
    for m in mesh_obj.modifiers:
        if m.type == 'ARMATURE':
            saved.append((m, m.show_viewport))
            m.show_viewport = False
    hidden_vp = mesh_obj.hide_viewport
    try:
        was_hidden = mesh_obj.hide_get()
    except RuntimeError:  # not in the current view layer
        was_hidden = False
    if hidden_vp:
        mesh_obj.hide_viewport = False
    if was_hidden:
        mesh_obj.hide_set(False)
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        eo = mesh_obj.evaluated_get(dg)
        me = eo.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
    finally:
        for m, v in saved:
            m.show_viewport = v
        if was_hidden:
            mesh_obj.hide_set(True)
        if hidden_vp:
            mesh_obj.hide_viewport = True
    return eo, me


def _round(x):
    return efmodel.rnd(x)


def export_efmesh(mesh_obj, arm_obj, out_dir, mesh_id, max_influences=MAX_INFLUENCES, strict=False, source=None):
    """Write the bundle `<out_dir>/<mesh_id>.efmesh.json` + `.meshmeta.json` from a Blender mesh and return a report dict.

    The mesh is read in the rest pose through all modifiers except Armature (positions expressed in the armature's space, so a
    moved object is baked), triangulated (loop triangles; the source mesh is untouched), weights come from vertex groups
    named like EF joints (other groups are ignored with a warning), at most `max_influences` per vertex, normalised.
    `ok` is False (and nothing is written) when `efmodel.check_mesh_json` or a hard check fails; `strict` makes unweighted
    vertices a hard error. See references/blender-pipeline.md for the full list of warnings.
    """
    W = _Warn()
    errors = []
    names = rig.joint_names()
    jindex = {n: i for i, n in enumerate(names)}
    if not _GROUP_RE.match(mesh_id or ""):
        raise EfIoError("bad_id", "mesh id %r must match [a-z][a-z0-9_]{0,63}" % (mesh_id,))
    if arm_obj is None:
        W.add("no_rig", "no EF_Biped armature in the file; rig deviations are not checked")
    else:
        rep = rig.rig_report(arm_obj)
        if rep["extra"]:
            errors.append({"code": "extra_bones", "message": "armature has bones the EF biped does not have: %s (EF cannot use extra bones)" % ", ".join(rep["extra"])})
        if rep["missing"]:
            errors.append({"code": "missing_bones", "message": "armature lacks EF joints: %s" % ", ".join(rep["missing"])})
        if rep["wrong_parent"]:
            W.add("rig_hierarchy", "bones with a different parent than EF's hierarchy: %s" % ", ".join(rep["wrong_parent"]))
        if rep["max_head_deviation"] > 1e-3:
            W.add("rig_moved", "bone heads deviate up to %.4f from the EF rest pose; vertex positions are exported as they are, but the game binds the EF rest pose" % rep["max_head_deviation"])

    eo, me = _evaluated_rest_mesh(mesh_obj)
    try:
        return _export_from_mesh(mesh_obj, arm_obj, me, out_dir, mesh_id, max_influences, strict, source, W, errors, names, jindex)
    finally:
        eo.to_mesh_clear()


def _export_from_mesh(mesh_obj, arm_obj, me, out_dir, mesh_id, max_influences, strict, source, W, errors, names, jindex):
    nv = len(me.vertices)
    me.calc_loop_triangles()
    ltris = me.loop_triangles
    if len(ltris) == 0:
        raise EfIoError("no_faces", "the mesh has no faces")
    non_tri = sum(1 for p in me.polygons if p.loop_total != 3)
    if non_tri:
        W.add("triangulated", "%d faces are not triangles (quads/ngons) and were triangulated on export" % non_tri, count=non_tri)

    # world transform (into armature space, so rest-pose coordinates match the EF rig)
    m = mesh_obj.matrix_world.copy()
    if arm_obj is not None:
        m = arm_obj.matrix_world.inverted() @ m
    ident = all(abs(m[r][c] - (1.0 if r == c else 0.0)) < 1e-6 for r in range(4) for c in range(4))
    if not ident:
        W.add("transform_baked", "the object has a non-identity transform relative to the armature; it was baked into the exported positions")
    nmat = m.to_3x3().inverted().transposed()

    co = [0.0] * (3 * nv)
    me.vertices.foreach_get("co", co)
    P = []
    for i in range(nv):
        v = m @ Vector((co[3 * i], co[3 * i + 1], co[3 * i + 2]))
        P.append((v.x, v.y, v.z))

    nl = len(me.loops)
    uvl = me.uv_layers.active
    if uvl is None or len(me.uv_layers) == 0:
        raise EfIoError("no_uv", "the mesh has no UV map (every vertex of an EF mesh needs UVs); unwrap it first")
    uv_flat = [0.0] * (2 * nl)
    uvl.uv.foreach_get("vector", uv_flat)
    n_flat = [0.0] * (3 * nl)
    me.corner_normals.foreach_get("vector", n_flat)
    has_custom = bool(me.has_custom_normals)

    # weights per vertex
    vg_names = [g.name for g in mesh_obj.vertex_groups]
    ignored = set()
    inf = [None] * nv
    for i, v in enumerate(me.vertices):
        acc = {}
        for g in v.groups:
            nm = vg_names[g.group] if g.group < len(vg_names) else None
            if nm in jindex:
                if g.weight > 0.0:
                    acc[jindex[nm]] = acc.get(jindex[nm], 0.0) + g.weight
            elif nm is not None:
                ignored.add(nm)
        inf[i] = acc if acc else None
    if ignored:
        W.add("ignored_groups", "vertex groups that are not EF joints were ignored: %s" % ", ".join(sorted(ignored)), groups=sorted(ignored))
    if not any(vg_names) or all(x is None for x in inf):
        raise EfIoError("no_weights", "no vertex has a weight on a group named like an EF joint (%s ...); import or paint weights first" % ", ".join(names[:5]))

    used_verts = sorted({v for t in ltris for v in t.vertices})
    used_set = set(used_verts)
    loose_v = nv - len(used_verts)
    loose_e = sum(1 for e in me.edges if e.is_loose)
    if loose_v or loose_e:
        W.add("loose_geometry", "%d loose vertices and %d loose edges are not part of any face and were dropped" % (loose_v, loose_e), vertices=loose_v, edges=loose_e)

    # unweighted vertices: bind like their weighted neighbours
    unweighted = [v for v in used_verts if inf[v] is None]
    if unweighted:
        msg = "%d vertices have no EF-joint weight" % len(unweighted)
        if strict:
            errors.append({"code": "unweighted", "message": msg + " (strict)", "count": len(unweighted)})
            for v in unweighted:  # placeholder so the rest of the report can still be computed; nothing is written
                inf[v] = {0: 1.0}
        else:
            adj = {}
            for e in me.edges:
                a, b = e.vertices
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
            todo = set(unweighted)
            for _ in range(32):
                if not todo:
                    break
                fixed = {}
                for v in todo:
                    acc = {}
                    cnt = 0
                    for nb in adj.get(v, ()):
                        if inf[nb] is not None:
                            cnt += 1
                            for j, w in inf[nb].items():
                                acc[j] = acc.get(j, 0.0) + w
                    if cnt:
                        fixed[v] = {j: w / cnt for j, w in acc.items()}
                for v, d in fixed.items():
                    inf[v] = d
                    todo.discard(v)
            for v in todo:  # isolated from every weighted vertex: Root
                inf[v] = {0: 1.0}
            W.add("unweighted", msg + "; bound to the weights of their neighbours (isolated ones to Root)", count=len(unweighted))

    # normalise, trim, quantise
    trimmed = 0
    dropped_small = 0
    wlist = []
    widx = {}
    final = {}
    hist = {}
    for v in used_verts:
        items = sorted(inf[v].items(), key=lambda kv: (-kv[1], kv[0]))
        if len(items) > max_influences:
            trimmed += 1
            items = items[:max_influences]
        tot = sum(w for _j, w in items)
        keep = [(j, w / tot) for j, w in items if w / tot >= MIN_WEIGHT] or [(items[0][0], 1.0)]
        if len(keep) < len(items):
            dropped_small += 1
        tot = sum(w for _j, w in keep)
        q = [(j, round(w / tot, WEIGHT_DECIMALS)) for j, w in keep]
        diff = round(1.0 - sum(w for _j, w in q), WEIGHT_DECIMALS)
        if diff != 0.0:
            j0, w0 = q[0]
            q[0] = (j0, round(w0 + diff, WEIGHT_DECIMALS))
        final[v] = q
        hist[len(q)] = hist.get(len(q), 0) + 1
        for _j, w in q:
            if w not in widx:
                widx[w] = len(wlist)
                wlist.append(w)
    if trimmed:
        W.add("influences_trimmed", "%d vertices had more than %d joint influences; the smallest were dropped" % (trimmed, max_influences), count=trimmed, max_influences=max_influences)
    # a leading 1.0 mirrors the stock meshes (weights[0] == 1.0)
    if 1.0 not in widx:
        widx[1.0] = len(wlist)
        wlist.append(1.0)
    first = widx[1.0]
    if first != 0:
        wlist[0], wlist[first] = wlist[first], wlist[0]
        widx = {w: i for i, w in enumerate(wlist)}

    # positions, uvs, normals, triangles
    pos_new = {v: i for i, v in enumerate(used_verts)}
    uv_index = {}
    uv_out = []
    n_index = {}
    n_out = []
    uv_bad = 0
    tri_records = []  # (polygon index, ((p, u, n) x 3))
    flipped = 0
    zero_area = 0
    for t in ltris:
        rec = []
        for lp, vi in zip(t.loops, t.vertices):
            u, v = uv_flat[2 * lp], uv_flat[2 * lp + 1]
            if u < -1e-4 or u > 1.0001 or v < -1e-4 or v > 1.0001:
                uv_bad += 1
            key_u = (_round(u), _round(1.0 - v))
            ui = uv_index.get(key_u)
            if ui is None:
                ui = uv_index[key_u] = len(uv_out)
                uv_out.append(key_u)
            nv3 = nmat @ Vector((n_flat[3 * lp], n_flat[3 * lp + 1], n_flat[3 * lp + 2]))
            ln = nv3.length or 1.0
            key_n = (_round(nv3.x / ln), _round(nv3.y / ln), _round(nv3.z / ln))
            ni = n_index.get(key_n)
            if ni is None:
                ni = n_index[key_n] = len(n_out)
                n_out.append(key_n)
            rec.append((pos_new[vi], ui, ni))
        a, b, c = (Vector(P[vi]) for vi in t.vertices)
        cr = (b - a).cross(c - a)
        if cr.length_squared < 1e-14:
            zero_area += 1
        else:
            sn = sum((Vector(n_out[r[2]]) for r in rec), Vector((0.0, 0.0, 0.0)))
            if cr.dot(sn) < 0.0:
                flipped += 1
        tri_records.append((t.polygon_index, tuple(rec)))
    if uv_bad:
        W.add("uv_out_of_range", "%d triangle corners have UVs outside 0..1 (the texture is not tiled in EF; they will clamp or wrap)" % uv_bad, count=uv_bad)
    if zero_area:
        W.add("zero_area", "%d triangles have zero area" % zero_area, count=zero_area)
    if flipped:
        W.add("flipped_triangles", "%d triangles wind opposite to their normals (flipped faces? check Mesh > Normals > Recalculate Outside)" % flipped, count=flipped)

    # parts and groups
    pos_list = [P[v] for v in used_verts]
    part_attr = me.attributes.get(PART_ATTR)
    part_vals = None
    if part_attr is not None and part_attr.domain == 'FACE' and part_attr.data_type == 'INT':
        part_vals = [0] * len(me.polygons)
        part_attr.data.foreach_get("value", part_vals)
    slot_names = [sanitize_group(s.material.name) if s.material is not None else DEFAULT_GROUP for s in mesh_obj.material_slots]
    renamed = [(s.material.name, sanitize_group(s.material.name)) for s in mesh_obj.material_slots
               if s.material is not None and sanitize_group(s.material.name) != s.material.name and not _SUFFIX_RE.search(s.material.name)]
    if renamed:
        W.add("group_renamed", "material names were turned into group names: %s" % ", ".join("%s -> %s" % r for r in renamed))
    part_tris = {p: [] for p in efmodel.EF_PARTS}
    part_group_names = {p: [] for p in efmodel.EF_PARTS}
    joint_for_group = {}
    for pi, rec in tri_records:
        mi = me.polygons[pi].material_index
        gname = slot_names[mi] if 0 <= mi < len(slot_names) else DEFAULT_GROUP
        # dominant joint of the triangle by summed weight
        score = {}
        for p, _u, _n in rec:
            for j, w in final[used_verts[p]]:
                score[j] = score.get(j, 0.0) + w
        dom = max(score.items(), key=lambda kv: (kv[1], -kv[0]))[0]
        part = None
        if part_vals is not None and 1 <= part_vals[pi] <= len(efmodel.EF_PARTS):
            part = efmodel.EF_PARTS[part_vals[pi] - 1]
        if part is None:
            part = JOINT_PART[names[dom]]
        part_tris[part].append(rec)
        part_group_names[part].append(gname)
        gs = joint_for_group.setdefault(gname, {})
        gs[dom] = gs.get(dom, 0) + 1
    used_groups = {g for lst in part_group_names.values() for g in lst}
    groups = [g for g in dict.fromkeys(slot_names + [DEFAULT_GROUP]) if g in used_groups]  # material slot order
    gmap = {g: i for i, g in enumerate(groups)}
    part_groups = {p: [gmap[g] for g in part_group_names[p]] for p in efmodel.EF_PARTS}

    # placeholders for empty parts (as efmodel.write_mesh_json does): one degenerate triangle on the anchor joint
    n_real_pos = len(pos_list)
    vindices = []
    vcounts = []
    for v in used_verts:
        q = final[v]
        vcounts.append(len(q))
        for j, w in q:
            vindices.extend((j, widx[w]))
    anchor_uv = anchor_n = None
    for part in efmodel.EF_PARTS:
        if part_tris[part]:
            continue
        if anchor_uv is None:
            uv_out.append((0.0, 0.0))
            anchor_uv = len(uv_out) - 1
            n_out.append((0.0, 0.0, 1.0))
            anchor_n = len(n_out) - 1
        j = efmodel.joint_by_name(efmodel.PART_JOINT[part])
        pos_list.append(efmodel.to_blender(j["pos"]))
        vcounts.append(1)
        vindices.extend((j["index"], widx[1.0]))
        p = len(pos_list) - 1
        part_tris[part] = [((p, anchor_uv, anchor_n),) * 3]
        part_groups[part] = [0]

    parts_json = {}
    for part in efmodel.EF_PARTS:
        arr = []
        for rec in part_tris[part]:
            for p, u, n in rec:
                arr.extend((p, u, n))
        parts_json[part] = {"array": arr}
    flat_pos = [_round(c) for p in pos_list for c in p]
    flat_n = [c for n in n_out for c in n]
    flat_uv = [c for u in uv_out for c in u]
    mesh_json = {"vertices": {
        "positions": {"array": flat_pos}, "normals": {"array": flat_n}, "uvs": {"array": flat_uv},
        "vcounts": {"array": vcounts}, "weights": {"array": wlist}, "vindices": {"array": vindices},
        "parts": parts_json}}
    problems = efmodel.check_mesh_json(mesh_json)
    for pr in problems:
        errors.append({"code": "invalid_mesh", "message": pr})

    zs = [p[2] for p in pos_list[:n_real_pos]]
    height = max(zs) - min(zs)
    xs = [p[0] for p in pos_list[:n_real_pos]]
    ys = [p[1] for p in pos_list[:n_real_pos]]
    if min(zs) < -0.02 or min(zs) > 0.05:
        W.add("not_on_floor", "the lowest vertex is at z=%.3f; the rig's feet are at z=0 (the generator scales by the model height and puts the feet at the entity's feet)" % min(zs), min_z=round(min(zs), 4))
    facing = _facing(pos_list[:n_real_pos], final, used_verts, names)
    if facing["verdict"] == "backward":
        W.add("facing_backward", "the feet point towards -Y (%.3f); EF models face +Y. Rotate the mesh 180 degrees about Z" % facing["foot_offset_y"])

    mesh_meta = {
        "schema": "bossforge-mesh/1", "id": mesh_id, "height": round(height, 5), "units": "blocks", "joint_names": list(names),
        "groups": groups,
        "tri_groups": {p: part_groups[p] for p in efmodel.EF_PARTS},
        "group_bones": {g: names[max(d.items(), key=lambda kv: (kv[1], -kv[0]))[0]] for g, d in joint_for_group.items()},
        "source": source or (os.path.basename(bpy.data.filepath) if bpy.data.filepath else "live.blend"),
        "generator": bfk.GENERATOR,
    }
    out = Path(out_dir)
    files = []
    ok = not errors
    if ok:
        out.mkdir(parents=True, exist_ok=True)
        pm = out / (mesh_id + ".efmesh.json")
        pm.write_text(efmodel.dumps(mesh_json), encoding="utf-8")
        pj = out / (mesh_id + ".meshmeta.json")
        pj.write_text(json.dumps(mesh_meta, indent=1), encoding="utf-8")
        files = [str(pm), str(pj)]
    tri_by_part = {p: sum(1 for _ in part_tris[p]) for p in efmodel.EF_PARTS}
    return {"ok": ok, "id": mesh_id, "files": files, "errors": errors, "warnings": W.items,
            "positions": len(pos_list), "uvs": len(uv_out), "normals": len(n_out), "triangles": len(ltris),
            "triangles_by_part": tri_by_part, "groups": groups, "group_bones": mesh_meta["group_bones"],
            "max_influences": max(hist) if hist else 0, "influence_histogram": {str(k): v for k, v in sorted(hist.items())},
            "height": mesh_meta["height"], "bounds": {"min": [round(min(xs), 4), round(min(ys), 4), round(min(zs), 4)], "max": [round(max(xs), 4), round(max(ys), 4), round(max(zs), 4)]},
            "custom_normals": has_custom, "facing": facing, "object": mesh_obj.name, "rig": arm_obj.name if arm_obj else None,
            "check_mesh_json": problems}


def _facing(pos, final, used_verts, names):
    """Soles of the feet extend forward of the leg axis on a model that faces +Y. Returns the measured offset and a verdict."""
    low = [i for i, p in enumerate(pos) if p[2] < 0.06]
    leg = {"Thigh_R", "Leg_R", "Knee_R", "Thigh_L", "Leg_L", "Knee_L"}
    sel = []
    for i in low:
        q = final[used_verts[i]]
        if sum(w for j, w in q if names[j] in leg) > 0.5:
            sel.append(pos[i][1])
    if len(sel) < 4:
        return {"verdict": "unknown", "foot_offset_y": 0.0, "samples": len(sel)}
    off = sum(sel) / len(sel) - 0.001
    verdict = "forward" if off > 0.01 else ("backward" if off < -0.02 else "unknown")
    return {"verdict": verdict, "foot_offset_y": round(off, 4), "samples": len(sel)}


# ----------------------------------------------------------------------------- runner entries
def run_import(args):
    """`{"source", "out", "texture", "name", "id", "no_custom_normals", "reset"}` -> rig + textured mesh in the scene; with
    `out` the scene is saved as .blend. `reset` (default: true headless, false in a live Blender) first empties the scene."""
    if args.get("reset", bpy.app.background):
        bfk.reset_scene()
    try:
        res = import_efmesh(args["source"], name=args.get("name"), texture=args.get("texture"), mesh_id=args.get("id"),
                            custom_normals=not args.get("no_custom_normals", False))
    except EfIoError as e:
        return bfk.result_error(e.code, e.message)
    except (OSError, ValueError, KeyError) as e:  # unreadable file, invalid JSON, missing mesh members
        return bfk.result_error("bad_mesh", "%s: %s" % (type(e).__name__, e))
    obj, arm = res.pop("object"), res.pop("armature_object")
    res.pop("mesh", None)
    res["object"], res["armature"] = obj.name, arm.name
    out = args.get("out")
    if out:
        bpy.ops.wm.save_as_mainfile(filepath=str(out))
        res["out"] = str(out)
    return res


def run_export(args):
    """`{"out": DIR, "object", "rig", "id", "max_influences", "strict"}` on the open .blend -> bundle report."""
    try:
        mesh, arm = pick_objects(args.get("object"), args.get("rig"))
        mesh_id = args.get("id") or sanitize_group(_SUFFIX_RE.sub("", mesh.name))
        res = export_efmesh(mesh, arm, args["out"], mesh_id, max_influences=int(args.get("max_influences") or MAX_INFLUENCES),
                            strict=bool(args.get("strict")), source=args.get("source"))
    except EfIoError as e:
        return bfk.result_error(e.code, e.message)
    return res


def run(args):
    task = args.get("task")
    if task == "import":
        return run_import(args)
    if task == "export":
        return run_export(args)
    return bfk.result_error("bad_task", "efio.run needs args['task'] in ('import', 'export')")
