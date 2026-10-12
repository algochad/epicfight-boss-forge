"""Shared frame of the Nightingale build: reference calibration, parameters, material groups, weight rules.

The concept sheets are orthographic at 203 px per block (`/tmp/nightingale-ref`, see the spec's `reference` block). The EF biped
is shorter and wider than the drawn character, so the model is built `REF.S` (< 1) times the size of the drawing: its joints
(neck 1.514, shoulder 1.394, elbow 1.094, knee 0.386 ...) then sit where the drawing has its neck, shoulder, elbow and knee. The
fit calibration of the spec is therefore `px_per_block = 203 / S`. All geometry code states measurements in reference PIXELS
(`REF.x`, `REF.z`, `REF.y` convert them to model blocks) so the numbers can be checked against the sheet directly.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_BLENDER = os.path.normpath(os.path.join(HERE, "..", "..", "..", "scripts", "blender"))
if SCRIPTS_BLENDER not in sys.path:
    sys.path.insert(0, SCRIPTS_BLENDER)

from bfk import modeling as K  # noqa: E402


class _Ref:
    """Pixel <-> model-block conversion for the three reference views (front/back share the sheet's scale)."""
    PPB = 203.0                 # pixels per block in the sheets
    S = 0.84                    # model blocks per sheet block (see module docstring)
    CX_FRONT, GY_FRONT = 224.5, 516.0   # front view: x px = CX - PPB * x   (character's right = image left)
    CX_BACK, GY_BACK = 686.0, 515.0     # back view:  x px = CX + PPB * x
    CX_SIDE, GY_SIDE = 522.0, 512.0     # right-side view: px = CX + PPB * y_storage (face to the image right)

    @property
    def k(self) -> float:
        return self.S / self.PPB

    def x(self, px: float) -> float:
        """Front-view image x -> storage x (character's right = +x)."""
        return (self.CX_FRONT - px) * self.k

    def xb(self, px: float) -> float:
        """Back-view image x -> storage x."""
        return (px - self.CX_BACK) * self.k

    def z(self, py: float) -> float:
        """Front-view image y -> storage z (height above the soles)."""
        return (self.GY_FRONT - py) * self.k

    def zb(self, py: float) -> float:
        return (self.GY_BACK - py) * self.k

    def y(self, px: float) -> float:
        """Side-view image x -> storage y (forward = +y)."""
        return (px - self.CX_SIDE) * self.k

    def zs(self, py: float) -> float:
        return (self.GY_SIDE - py) * self.k

    def d(self, npx: float) -> float:
        """A length in sheet pixels -> model blocks."""
        return npx * self.k

    def front(self, px: float, py: float, y: float = 0.0):
        return (self.x(px), y, self.z(py))


REF = _Ref()

# --------------------------------------------------------------------------------------- material groups (slot names)
# (group, texels per block). `wing_*` = feather rows from the top row (white) to the long lower flight feathers (violet).
GROUPS = [
    ("skin", 96.0), ("hair", 80.0), ("robe", 72.0), ("skirt", 72.0), ("tabard", 72.0), ("trim", 80.0), ("boots", 72.0),
    ("leather", 64.0), ("vial_g", 128.0), ("vial_v", 128.0), ("vial_r", 128.0), ("pauldron", 80.0), ("ornament", 96.0), ("ribbon", 64.0),
    ("wing_a", 80.0), ("wing_b", 80.0), ("wing_c", 80.0), ("wing_d", 80.0),
]
GROUP_NAMES = [g for g, _ in GROUPS]
# preview colours (the painter decides the real texture): sRGB 0..1
PREVIEW = {
    "skin": (0.95, 0.76, 0.62), "hair": (0.40, 0.23, 0.12), "robe": (0.55, 0.62, 0.78), "skirt": (0.50, 0.58, 0.76), "tabard": (0.82, 0.86, 0.95),
    "trim": (0.20, 0.38, 0.68), "boots": (0.62, 0.67, 0.78), "leather": (0.30, 0.20, 0.12), "vial_g": (0.35, 0.85, 0.55), "vial_v": (0.62, 0.35, 0.85), "vial_r": (0.92, 0.45, 0.60),
    "pauldron": (0.92, 0.94, 0.98), "ornament": (0.90, 0.82, 0.50), "ribbon": (0.92, 0.93, 0.99),
    "wing_a": (0.97, 0.97, 1.00), "wing_b": (0.90, 0.90, 1.00), "wing_c": (0.76, 0.74, 0.98), "wing_d": (0.62, 0.58, 0.95),
}

DEFAULTS = {
    "atlas": 1024,
    "margin": 4,
    "face_density": 2.4,      # head islands: density multiplier (crisp pixel-art face)
    "wing_density": 1.0,
    "wing_spread": 1.0,
    "wing_curl": 1.0,
    "skirt_segments": 4,
    "skirt_drop": 0.14,        # blocks below the hip over which the skirt changes from Root to the thighs
    "skirt_droop": 0.08,       # the hem steps down by this much towards the front slit (sheet: lace tabs, y 405 -> 425)
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
