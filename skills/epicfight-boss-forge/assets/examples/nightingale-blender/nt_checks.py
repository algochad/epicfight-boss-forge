"""Measurements on the built Nightingale (Blender only): skinning totals and cloth-vs-leg poke-through at sample poses."""
from __future__ import annotations

from nt_common import K

# name -> [(joint, axis, degrees)]  world axes through the joint (bfk.rig.pose_bone_world_rotate): +X swings a hanging leg / arm
# FORWARD, -Y swings the character's right limb out sideways (the left one needs +Y).
POSES = {
    "rest": [],
    "right_leg_forward_60": [("Thigh_R", "X", 60)],
    "right_leg_back_60": [("Thigh_R", "X", -60)],
    "left_leg_forward_60": [("Thigh_L", "X", 60)],
    "legs_split_60": [("Thigh_R", "X", 60), ("Thigh_L", "X", -60)],
    "legs_out_30": [("Thigh_R", "Y", -30), ("Thigh_L", "Y", 30)],
    "knee_bend_60": [("Thigh_R", "X", 60), ("Leg_R", "X", -60)],
    "arms_forward_60": [("Arm_R", "X", 60), ("Arm_L", "X", 60)],
    "arms_side_60": [("Arm_R", "Y", -60), ("Arm_L", "Y", 60)],
    "arms_back_20": [("Arm_R", "X", -20), ("Arm_L", "X", -20)],
    "arms_back_45": [("Arm_R", "X", -45), ("Arm_L", "X", -45)],
    "head_turn_40": [("Head", "Z", 40)],
    "torso_twist_30": [("Torso", "Z", 30)],
}
CLOTH = ("skirt", "tabard", "robe", "leather", "vial_g", "vial_v", "vial_r")


def weights_total(built) -> dict:
    """Pure-model skinning numbers: every vertex's weights sum to 1, influences per vertex, joints used."""
    tot_lo, tot_hi, mx = 9.0, 0.0, 0
    hist = {}
    joints = set()
    for w in built.weights:
        s = sum(w.values())
        tot_lo, tot_hi = min(tot_lo, s), max(tot_hi, s)
        mx = max(mx, len(w))
        hist[len(w)] = hist.get(len(w), 0) + 1
        joints.update(w)
    return {"vertices": len(built.weights), "min_total": tot_lo, "max_total": tot_hi, "max_influences": mx,
            "influence_histogram": dict(sorted(hist.items())), "joints": sorted(joints)}


SOFT = CLOTH + ("ribbon", "wing_a", "wing_b", "wing_c", "wing_d")


def pose_report(res: dict, poses=None, margin: float = 0.002) -> dict:
    """Poke-through at sample poses. For every pose count the samples of the soft groups (`CLOTH` for the legs, `SOFT` = cloth +
    ribbons + wings for the arms: every vertex, edge midpoint and face centre of their faces) that sit deeper than `margin`
    inside the posed LEG volume (skin, boot cuffs, boots) resp. ARM volume (sleeves, cuffs, fists). Returns
    {pose: {"legs": {inside, max_depth, samples}, "arms": {...}}}; rest included."""
    import bpy
    from bfk import rig as bfk_rig
    built, obj, arm, m = res["built"], res["obj"], res["rig"], res["model"]
    volumes = {"legs": (K.to_blender(built, "legs_only", m.groups, rig=arm, tags={"leg"}), CLOTH, "leg"),
               "arms": (K.to_blender(built, "arms_only", m.groups, rig=arm, tags={"arm"}), SOFT, "arm")}
    faces_of = {}
    for key, (_vol, groups, tag) in volumes.items():  # the soft faces of a volume exclude the volume's own faces
        faces_of[key] = [f for f, g, t in zip(built.faces, built.groups, built.tags) if m.groups[g] in groups and t != tag]
    out = {}
    for name, ops in (poses or POSES).items():
        bfk_rig.reset_pose(arm)
        for j, ax, deg in ops:
            bfk_rig.pose_bone_world_rotate(arm, j, ax, deg)
        pts = K.evaluated_points(obj)
        rep = {}
        for key, (vol, _groups, _tag) in volumes.items():
            samples = []
            for f in faces_of[key]:
                q = [pts[i] for i in f]
                samples.extend(q)
                samples.append(K.centroid(q))
                samples.extend(K.vlerp(q[k], q[(k + 1) % len(q)], 0.5) for k in range(len(q)))
            hits = K.points_inside(vol, samples, margin=margin, with_depth=True)
            rep[key] = {"inside": len(hits), "max_depth": round(max((d for _, d in hits), default=0.0), 4), "samples": len(samples)}
        out[name] = rep
    bfk_rig.reset_pose(arm)
    for vol, _g, _t in volumes.values():
        bpy.data.objects.remove(vol, do_unlink=True)
    return out


def bundle_weight_check(mesh_json: dict) -> dict:
    """Numbers from the EXPORTED EF mesh JSON (pure Python): for every position the sum of its influence weights, the number of
    influences, positions without influence, and the joints used. EF's layout: `vcounts[i]` influences for position i, read as
    (joint index, weight index) pairs from `vindices`, weights from the shared `weights` array."""
    v = mesh_json["vertices"]
    counts = v["vcounts"]["array"]
    idx = v["vindices"]["array"]
    wts = v["weights"]["array"]
    n = len(v["positions"]["array"]) // 3
    pos = lo = 0
    lo, hi = 9.0, 0.0
    joints = set()
    empty = mx = 0
    for i in range(n):
        c = counts[i]
        if c == 0:
            empty += 1
            continue
        tot = 0.0
        for k in range(c):
            joints.add(idx[pos + 2 * k])
            tot += wts[idx[pos + 2 * k + 1]]
        pos += 2 * c
        lo, hi, mx = min(lo, tot), max(hi, tot), max(mx, c)
    return {"positions": n, "without_weights": empty, "max_influences": mx, "min_total": lo, "max_total": hi, "joints_used": len(joints)}
