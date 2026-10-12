"""Nightingale extras on the art pixel grid (`nt_common.px`, see nt_body for the axes): stair-step pauldrons, small chest ornaments, belt,
the brown potion pouch on the character's right hip (front) and the three small potion pouches across the lower back. Every textured
face is a whole number of pixels."""
from __future__ import annotations

from nt_common import K, px, torso_rule

CY = px(2.0)   # centre of the torso depth (y), as in nt_body


def _pauldrons(m: K.Model, P: dict) -> None:
    """Right pauldron (mirrored by the caller): two layered plates, each 5 px wide, 4 px tall and 8 px deep, the lower one 1 px further
    out and 3 px down (x 4 -> 9 / 5 -> 10, z 46 -> 42 / 43 -> 39): the stair-step shoulder guard of the sheet (landmarks: box x 0.131-0.304,
    z 1.267-1.496, tops 0.04 over the shoulder line) over the 5 px sleeve (x 5 -> 10). Chest at the top, Arm_R below."""
    w = K.rule_blend(2, px(46), px(39), "Chest", "Arm_R")
    for k in range(2):
        x0 = px(4.0 + k)
        z1 = px(46 - 3 * k)
        K.block(m, (x0, CY - px(4.0), z1 - px(4)), (x0 + px(5), CY + px(4.0), z1), "pauldron", w)


def _chest_ornaments(m: K.Model, P: dict) -> None:
    """The two small wing ornaments that flank the cross on the chest: 3 x 2 px plates standing 0.5 px proud of the front (right one,
    mirrored by the caller; rows 3-5 under the shoulder line)."""
    K.block(m, (px(1.5), px(6.0), px(40.0)), (px(4.5), px(6.5), px(42.0)), "ornament", torso_rule())


def _buckle(m: K.Model, P: dict) -> None:
    """Small square gold buckle on the front of the belt, left of the centre (landmarks: x -0.11 -> -3.4 px; the right one is hidden by
    the hip pouch): a 2 x 2 px plate 0.5 px proud of the belt front (y 6.5, group ornament)."""
    K.block(m, (px(-4.5), px(6.25), px(31)), (px(-2.5), px(7.0), px(33)), "ornament", torso_rule())


def _belt(m: K.Model, P: dict) -> None:
    """Brown belt: 2 px tall ring (z 31 -> 33, landmarks 1.02-1.065) around the waist, 0.5 px proud of the robe on every side (10 x 9 px)."""
    K.stacked_rects(m, [(px(33), 0.0, CY, px(5.0), px(4.5)), (px(31), 0.0, CY, px(5.0), px(4.5))], "leather", torso_rule())


def _pouch(m: K.Model, P: dict) -> None:
    """Brown pouch on the character's right hip, hanging from the belt in front of the hip (4 x 2 x 5 px: x 3.5 -> 7.5, y 6.5 -> 8.5,
    z 26 -> 31; landmarks x 0.06-0.235, z 0.85-1.07): a 5 x 3 x 2 px lid over its top and four 1.5 x 2 px vial windows in its front
    (groups vial_r, vial_g / vial_v, vial_r)."""
    w = K.rule_blend(2, px(33), px(26), "Torso", "Root")   # above the hip joint (z 23.4): a part that followed the thigh would swing back into the arm
    K.block(m, (px(3.5), px(6.5), px(26)), (px(7.5), px(8.5), px(31)), "leather", w)
    K.block(m, (px(3.0), px(6.0), px(30)), (px(8.0), px(9.0), px(32)), "leather", w)   # lid
    for (cx, z0), g in zip(((4.75, 28.5), (6.25, 28.5), (4.75, 26.5), (6.25, 26.5)), ("vial_r", "vial_g", "vial_v", "vial_r")):
        K.block(m, (px(cx - 0.75), px(8.5), px(z0)), (px(cx + 0.75), px(9.0), px(z0 + 2)), g, w)


def _back_pouches(m: K.Model, P: dict) -> None:
    """Three small brown pouches across the lower back (x -3.5, 0, +3.5, each 3 x 2 x 4 px, z 27 -> 31 hanging from the belt; landmarks
    z 0.884-0.987) with a 2 x 2 px vial window on the back and a 1 x 1.5 px bottle neck above: purple (vial_r), violet (vial_v), cyan
    (vial_g) from left to right seen from behind (the back view has +x on its right, so purple is at x = -3.5)."""
    w = K.rule_blend(2, px(33), px(27), "Torso", "Root")
    for x, g in ((-3.5, "vial_r"), (0.0, "vial_v"), (3.5, "vial_g")):
        K.block(m, (px(x - 1.5), px(-4.5), px(27)), (px(x + 1.5), px(-2.5), px(31)), "leather", w)
        K.block(m, (px(x - 1.0), px(-4.75), px(28)), (px(x + 1.0), px(-4.5), px(30)), g, w)
        K.block(m, (px(x - 0.5), px(-3.75), px(31)), (px(x + 0.5), px(-2.75), px(32.5)), g, w)


def add(m: K.Model, P: dict) -> None:
    mk = m.mark()
    _pauldrons(m, P)
    _chest_ornaments(m, P)
    m.mirror(mk)  # left pauldron and ornament
    _belt(m, P)
    _buckle(m, P)        # one buckle, left of the centre (the right one is hidden by the pouch)
    _pouch(m, P)         # the pouch hangs on the character's right hip only
    _back_pouches(m, P)
