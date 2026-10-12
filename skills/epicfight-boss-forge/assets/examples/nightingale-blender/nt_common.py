"""Shared frame of the Nightingale build: the art pixel grid and texel density, parameters, material groups, weight rules.

The reference sheets (`reference.views`, 232 px per block) are drawn at about 0.8 of the EF biped's size, so the rig's joints (neck
1.514, shoulder 1.394, elbow 1.094, knee 0.386 ...) fall onto the drawing's chin, shoulder, elbow and knee. All geometry code states
its measurements in ART PIXELS (`px(n)`, 0.0325 block, 1 pixel = 1 texel at the default `texel_density`).
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_BLENDER = os.path.normpath(os.path.join(HERE, "..", "..", "..", "scripts", "blender"))
if SCRIPTS_BLENDER not in sys.path:
    sys.path.insert(0, SCRIPTS_BLENDER)

from bfk import modeling as K  # noqa: E402


# --------------------------------------------------------------------------------------- pixel grid and texel density
# The art is Minecraft pixel art: the head is a cube of 8 x 8 art pixels = 0.5 "art block" (1 art block = 16 art pixels). In the model the
# head is 0.26 blocks wide, so ONE ART PIXEL = ONE TEXEL = `PIXEL` model blocks and `BLOCK` = 16 pixels = 0.52 model blocks. Geometry
# goes on this grid (wing steps, feather widths, hem steps, vial sizes: multiples of `PIXEL`, see `px` / `snap`) so every UV island is a
# whole number of chunky texels. `params["texel_density"]` = texels per ART block (16 -> 1 texel per art pixel, head face 8 x 8);
# the density in MODEL blocks (what `bossforge skin` reports as `texels_per_block`) is `texel_density / BLOCK` = 30.8 at 16.
HEAD_W = 0.26                     # model blocks (the drawn head cube is 63 px of the 242 px per model block of the spec's calibration)
BLOCK = HEAD_W * 2.0              # 1 art block (head cube = 8 art pixels = half of it), in model blocks
PIXEL = BLOCK / 16.0              # 1 art pixel, in model blocks (0.0325)


def px(n: float = 1.0) -> float:
    """`n` art pixels in model blocks."""
    return n * PIXEL


def snap(v: float, step: float = 1.0) -> float:
    """Nearest multiple of `step` art pixels (a coordinate in model blocks goes on the pixel grid)."""
    return round(v / (step * PIXEL)) * step * PIXEL

# Fixed boxes the other parts clear (storage space, +y = front): the head cube `add_head` builds (hx half width, y0 back, y1 front, z0 chin,
# z1 top: a true cube of 8 art pixels = HEAD_W, front face at y 0.2069) and the torso shell (back / front surface at the shoulders, collar top).
HEAD_BOX = (HEAD_W / 2.0, 0.2069 - HEAD_W, 0.2069, 1.4524, 1.4524 + HEAD_W)
TORSO_BACK_Y = -0.064
TORSO_FRONT_Y = 0.188
Z_COLLAR = 1.4235


# --------------------------------------------------------------------------------------- material groups (slot names)
# (group, density multiplier of `texel_density`): 1.0 = one texel per art pixel; 2.0 = a 32-texel detail group (vials). `wing_*` = feather
# rows from the top row (white) to the long lower flight feathers (violet). `harness`, `harness_gold`, `glyph`, `laurel` are the groups of
# the new reference (back apparatus, floating tile, headband); an unused group costs nothing (empty material slot, not in the bundle).
GROUPS = [
    ("skin", 1.0), ("hair", 1.0), ("robe", 1.0), ("skirt", 1.0), ("tabard", 1.0), ("trim", 1.0), ("boots", 1.0),
    ("leather", 1.0), ("vial_g", 2.0), ("vial_v", 2.0), ("vial_r", 2.0), ("pauldron", 1.0), ("ornament", 1.0), ("ribbon", 1.0),
    ("wing_a", 1.0), ("wing_b", 1.0), ("wing_c", 1.0), ("wing_d", 1.0),
    ("harness", 1.0), ("harness_gold", 1.0), ("glyph", 1.0), ("laurel", 1.0),
    ("lace", 1.0), ("rune", 1.0),
]
GROUP_NAMES = [g for g, _ in GROUPS]


def group_densities(P: dict) -> dict:
    """{group: texels per model block} for `K.Model(density=...)`: `texel_density` per art block over `BLOCK`, times the group's
    multiplier. The head cube is multiplied once more by `face_density` in `nt_body.add_head`."""
    base = float(P["texel_density"]) / BLOCK
    return {g: base * mult for g, mult in GROUPS}


# preview colours (the painter decides the real texture): sRGB 0..1
PREVIEW = {
    "skin": (0.95, 0.76, 0.62), "hair": (0.40, 0.23, 0.12), "robe": (0.55, 0.62, 0.78), "skirt": (0.50, 0.58, 0.76), "tabard": (0.82, 0.86, 0.95),
    "trim": (0.20, 0.38, 0.68), "boots": (0.62, 0.67, 0.78), "leather": (0.30, 0.20, 0.12), "vial_g": (0.35, 0.85, 0.55), "vial_v": (0.62, 0.35, 0.85), "vial_r": (0.92, 0.45, 0.60),
    "pauldron": (0.92, 0.94, 0.98), "ornament": (0.90, 0.82, 0.50), "ribbon": (0.92, 0.93, 0.99),
    "wing_a": (0.97, 0.97, 1.00), "wing_b": (0.90, 0.90, 1.00), "wing_c": (0.76, 0.74, 0.98), "wing_d": (0.62, 0.58, 0.95),
    "harness": (0.55, 0.58, 0.62), "harness_gold": (0.80, 0.66, 0.30), "glyph": (0.70, 0.88, 1.00), "laurel": (0.78, 0.82, 0.88),
    "lace": (0.88, 0.89, 0.97), "rune": (0.45, 0.95, 1.00),
}

DEFAULTS = {
    "atlas": 512,
    "margin": 2,
    "texel_density": 16.0,    # texels per ART block (head cube = 0.5 block): 1 texel per art pixel, see the pixel grid above
    "face_density": 1.0,      # head cube: extra multiplier (1.0 = 8 x 8 texels per face, like the art)
    "wing_density": 1.0,
    "wing_spread": 1.0,
    "skirt_segments": 4,
    "skirt_drop": 0.14,        # blocks below the hip over which the skirt changes from Root to the thighs
    "skirt_droop": 0.065,      # the hem steps down by this much (2 px) towards the front slit
    "skirt_gap": 5.5,          # half width of the front slit of the skirt wall, in art pixels
    "lace": True,              # lace frill tabs below the skirt hem
    "tabard_lr": 0.015,        # half width of the strip in the tabard centre where the two thigh weights blend
    "skirt_columns": 7,        # segments around each half of the skirt wall
    "ribbon_segments": 10,
    "seed": 7,
    "parts": None,            # None = every part module; a list of module names builds a reduced model (tests)
}


def params(over=None) -> dict:
    p = dict(DEFAULTS)
    p.update(over or {})
    return p


# --------------------------------------------------------------------------------------- joints and weight rules
J = K.joint_positions()
Z_HIP = J["Thigh_R"][2]      # 0.761
Z_KNEE = J["Leg_R"][2]       # 0.386
Z_WAIST = J["Torso"][2]      # 0.814
Z_CHEST = J["Chest"][2]      # 1.114
Z_NECK = J["Head"][2]        # 1.514
Z_SHOULDER = J["Arm_R"][2]   # 1.394
Z_ELBOW = J["Hand_R"][2]     # 1.094
Z_WRIST = J["Tool_R"][2]     # 0.822
X_ARM = J["Arm_R"][0]        # 0.375
X_LEG = J["Thigh_R"][0]      # 0.125


def torso_rule():
    """Hip -> waist -> chest -> neck: Root / Torso / Chest / Head gradient along z."""
    return K.rule_chain(2, [(Z_HIP - 0.05, "Root"), (Z_HIP + 0.02, "Root"), (Z_WAIST + 0.08, "Torso"), (Z_CHEST - 0.12, "Torso"),
                            (Z_CHEST + 0.10, "Chest"), (Z_NECK - 0.02, "Chest"), (Z_NECK + 0.04, "Head")])


def arm_rule(side: str):
    """Sleeve / arm: Chest -> Arm -> Hand (forearm) -> Tool (hand) along z, smooth around the shoulder, elbow and wrist."""
    s = side
    return K.rule_chain(2, [(0.55, "Tool_" + s), (Z_WRIST - 0.04, "Tool_" + s), (Z_WRIST + 0.05, "Hand_" + s), (Z_ELBOW - 0.07, "Hand_" + s),
                            (Z_ELBOW + 0.07, "Arm_" + s), (Z_SHOULDER + 0.01, "Arm_" + s), (Z_SHOULDER + 0.11, "Chest")])


def leg_rule(side: str):
    """Skin / robe parts of one leg: Thigh above the knee, Leg below, smooth over +-0.07 around the knee joint."""
    return K.rule_chain(2, [(0.0, "Leg_" + side), (Z_KNEE - 0.07, "Leg_" + side), (Z_KNEE + 0.07, "Thigh_" + side)])


def skirt_rule(side: str, hip_z: float = None, drop: float = 0.14):
    """Skirt / tabard vertices of one half (side 'R' = x > 0): Root above `hip_z`, following that side's thigh `drop` blocks
    lower. Each half follows its OWN leg completely (no 50/50 vertices), so a swung leg drags the cloth in front of / behind it
    instead of pushing through it; the halves part at the centre line like a split skirt."""
    hip_z = Z_HIP + 0.02 if hip_z is None else hip_z
    thigh = "Thigh_" + side

    def rule(p):
        t = K.smoothstep(hip_z, hip_z - drop, p[2])
        return K.blend_weights({"Root": 1.0}, {thigh: 1.0}, t)
    return rule


def centre_rule(hip_z: float = None, drop: float = 0.14, lr_half: float = 0.015):
    """Like `skirt_rule` for a piece that spans both legs (the tabard): x < 0 follows Thigh_L, x > 0 Thigh_R, only the narrow
    strip |x| < lr_half blends the two (needs vertices at +-lr_half to keep that strip narrow)."""
    hip_z = Z_HIP + 0.02 if hip_z is None else hip_z

    def rule(p):
        t = K.smoothstep(hip_z, hip_z - drop, p[2])
        lr = K.smoothstep(-lr_half, lr_half, p[0])
        thigh = K.blend_weights({"Thigh_L": 1.0}, {"Thigh_R": 1.0}, lr)
        return K.blend_weights({"Root": 1.0}, thigh, t)
    return rule
