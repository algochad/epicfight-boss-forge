"""Nightingale extras: shoulder pauldrons, the belt pouch with its potion vials (reference pixels via `REF`)."""
from __future__ import annotations

from nt_common import REF as R, K


def _pauldron(m: K.Model, P: dict) -> None:
    """Right pauldron (mirrored by the caller): a slightly slanted slab over the shoulder, front view x 148-190, y 166-222; the
    top sits closer to the body than the bottom (outer edge x 155 -> 147). Follows Chest at the top and Arm_R below."""
    cy = R.y(530)
    w = K.rule_blend(2, R.z(168), R.z(215), "Chest", "Arm_R")
    bot_cx, top_cx = R.x(165.5), R.x(171.5)
    K.block(m, (bot_cx - R.d(20), cy - R.d(24), R.z(222)), (bot_cx + R.d(20), cy + R.d(24), R.z(167)), "pauldron", w,
            bevel=R.d(2.5), top_shift=(top_cx - bot_cx, 0.0), top_scale=(0.93, 1.0))


def _pouch(m: K.Model, P: dict) -> None:
    """Brown belt pouch on the character's right hip (front view x 166-190, y 262-308; side view x 548-572) with three vials
    standing in its front: green, violet, rose (groups vial_g, vial_v, vial_r so each gets its own colour)."""
    x0, x1 = R.x(190), R.x(166)
    y0, y1 = R.y(552), R.y(573)
    z0, z1 = R.z(306), R.z(264)
    w = K.rule_blend(2, R.z(264), R.z(306), "Torso", "Root")
    K.block(m, (x0, y0, z0), (x1, y1, z1), "leather", w, bevel=R.d(1.5))
    # lid band
    K.block(m, (x0 - 0.004, y0 - 0.004, R.z(280)), (x1 + 0.004, y1 + 0.004, R.z(272)), "leather", w)
    for k, g in enumerate(("vial_g", "vial_v", "vial_r")):
        cx = K.lerp(x0, x1, (k + 0.5) / 3.0)
        K.block(m, (cx - R.d(3.2), y1 - 0.002, R.z(296)), (cx + R.d(3.2), y1 + R.d(5.5), R.z(279)), g, w)


def add(m: K.Model, P: dict) -> None:
    mk = m.mark()
    _pauldron(m, P)
    m.mirror(mk)  # left pauldron
    _pouch(m, P)  # the pouch hangs on the character's right hip only
