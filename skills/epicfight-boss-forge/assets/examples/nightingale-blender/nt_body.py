"""Nightingale body: head, torso robe, sleeves, hands, legs, boots, skirt, tabard, belt (everything but hair / wings / ribbons).

Measurements are reference pixels (front view unless noted) through `REF`; see nt_common.
"""
from __future__ import annotations

import math

from nt_common import REF as R, K, arm_rule, centre_rule, leg_rule, skirt_rule, torso_rule, Z_HIP


# ------------------------------------------------------------------------------------------------------- head and neck
def add_head(m: K.Model, P: dict) -> None:
    """Skin head cube (the face is painted on its front) and a short neck. Head: x 193-256, y 100-165, side-view depth 517-572."""
    hx = R.d(31.5)
    y0, y1 = R.y(517), R.y(572)
    z0, z1 = R.z(165), R.z(100)
    K.block(m, (-hx, y0, z0), (hx, y1, z1), "skin", "Head", density=P["face_density"])
    # neck: from the collar up into the head
    K.block(m, (-0.045, y0 + 0.03, R.z(172) - 0.01), (0.045, y1 - 0.03, z0 + 0.02), "skin",
            K.rule_blend(2, R.z(172), z0 + 0.02, "Chest", "Head"))


# ------------------------------------------------------------------------------------------------------- torso and arms
def add_torso(m: K.Model, P: dict) -> None:
    """Robe shell over the (omitted) body: shoulders y=172, belt y=262-268, hip y=335 (front x 188-262, side depth 506-567)."""
    cy = R.y(537)
    secs = [
        (R.z(172), 0.0, cy, R.d(38), R.d(30.5)),
        (R.z(262), 0.0, cy, R.d(38), R.d(30.5)),
        (R.z(268), 0.0, cy, R.d(40), R.d(31.0)),
        (R.z(335), 0.0, cy, R.d(50), R.d(31.5)),
    ]
    K.stacked_rects(m, secs, "robe", torso_rule(), smooth=False)
    # belt (leather) with a slightly bigger section
    K.stacked_rects(m, [(R.z(262), 0.0, cy, R.d(41.5), R.d(32.0)), (R.z(270), 0.0, cy, R.d(42.5), R.d(32.8))], "leather", torso_rule())


def add_arm(m: K.Model, P: dict) -> None:
    """Right arm (+x, image left): sleeve y=180-283 centred x=160 -> 152, cuff y=283-300, fist x 128-158 y 300-335. Mirrored by the caller."""
    mk = m.mark()
    m.tag = "arm"
    w = arm_rule("R")
    cy = R.y(530)
    sleeve = [(R.z(180), R.x(160), cy, R.d(26), R.d(20)), (R.z(230), R.x(157), cy, R.d(26.5), R.d(20)),
              (R.z(283), R.x(152), cy, R.d(27), R.d(20.5))]
    K.stacked_rects(m, sleeve, "robe", w)
    K.stacked_rects(m, [(R.z(283), R.x(152), cy, R.d(31), R.d(23)), (R.z(300), R.x(152), cy, R.d(31), R.d(23))], "trim", w)
    K.block(m, (R.x(142) - R.d(14), cy - R.d(14), R.z(335)), (R.x(142) + R.d(14), cy + R.d(14), R.z(300)), "skin", w)
    m.mirror(mk)
    m.tag = ""


# ------------------------------------------------------------------------------------------------------- legs and boots
def add_legs(m: K.Model, P: dict) -> None:
    """Right leg (+x): skin thigh/shin x=206 (width 28 px), y-centre 0.074; boot cuff y=430-455; boot y=455-516; toe to side x=575."""
    mk = m.mark()
    m.tag = "leg"
    w = leg_rule("R")
    cx = R.x(206)
    cy = R.y(536)
    hx, hy = R.d(14), R.d(16)
    K.stacked_rects(m, [(R.z(335), cx, cy, hx, hy), (R.z(380), cx, cy, hx, hy), (R.z(430), cx, cy, hx, hy)], "skin", w)
    # boot cuff
    K.stacked_rects(m, [(R.z(430), cx + 0.008, cy, R.d(20.5), R.d(21)), (R.z(455), cx + 0.008, cy, R.d(20.5), R.d(21))], "trim", "Leg_R")
    # boot shaft and foot
    K.block(m, (cx - R.d(16.5), R.y(521), R.zs(487)), (cx + R.d(16.5), R.y(552), R.z(455)), "boots", "Leg_R")
    K.block(m, (cx - R.d(16.5), R.y(521), 0.0), (cx + R.d(16.5), R.y(576), R.zs(487)), "boots", "Leg_R")
    m.mirror(mk)
    m.tag = ""


# ------------------------------------------------------------------------------------------------------- skirt
def _skirt_half_ring(z: float, hx: float, hy: float, cy: float, gap: float, thick: float, n: int, power: float = 3.0,
                     droop: float = 0.0):
    """Closed ring of the RIGHT half of the skirt wall: outer + inner polyline of a rounded rectangle (superellipse) from the back
    centre (x = 0) round the side to the front tip at x = gap (the front opening is +-gap wide). `droop` lowers the ring by up to
    that much towards the front tip (the sheet's hem steps down towards the slit)."""
    phi_g = math.acos(min(1.0, (gap / hx) ** (power / 2.0)))
    a0, a1 = -math.pi / 2.0, phi_g

    def pt(rx, ry, k):
        phi = a0 + (a1 - a0) * k / n
        c, s = math.cos(phi), math.sin(phi)
        return (rx * math.copysign(abs(c) ** (2.0 / power), c), cy + ry * math.copysign(abs(s) ** (2.0 / power), s),
                z - droop * K.smoothstep(0.0, a1, phi))

    outer = [pt(hx, hy, k) for k in range(n + 1)]
    inner = [pt(hx - thick, hy - thick, k) for k in range(n, -1, -1)]
    return outer + inner


def add_skirt(m: K.Model, P: dict) -> None:
    """Robe skirt below the hip (y 335 -> hem 408), open at the front (+-34 px), split at the back centre (two halves built as the
    right half + mirror, each following its own thigh: `skirt_rule`, no vertex shared between the legs); plus the tabard (front panel,
    x 205-250, hanging from the belt y 262 to y 405), one piece whose halves follow their own thigh (`centre_rule`)."""
    cy0 = R.y(537)
    z_top, z_hem = R.z(335), R.z(408)
    segs, n = P["skirt_segments"], P["skirt_columns"]
    rings = []
    for i in range(segs + 1):
        t = i / float(segs)
        z = K.lerp(z_top, z_hem, t)
        hx = K.lerp(R.d(50), R.d(73), t ** 1.15)
        hy = K.lerp(R.d(31.5), R.d(35), t ** 1.15)
        cy = K.lerp(cy0, R.y(535), t)
        rings.append(_skirt_half_ring(z, hx, hy, cy, R.d(34), 0.012, n, droop=P["skirt_droop"] * t * t))
    mk = m.mark()
    K.loft(m, rings, "skirt", skirt_rule("R", drop=P["skirt_drop"]), caps=(False, True), smooth=True, flat_cols=(n, 2 * n + 1))
    m.mirror(mk)
    # tabard: ONE closed panel (one UV island, so its pattern is continuous) whose front face has vertices at x = +-lr: the two halves
    # follow their own thigh (`centre_rule`), only the strip between them stretches when the legs part
    ty = R.y(567) + 0.011  # the tabard hangs straight down the front of the torso (front face at side-view x 567)
    hw, lr, th = R.d(22.5), P["tabard_lr"], 0.009
    levels = [(R.z(262), ty), (R.z(335), ty), (R.z(370), ty + 0.004), (R.z(405), ty + 0.01)]
    trings = [[(hw, y + th, z), (lr, y + th, z), (-lr, y + th, z), (-hw, y + th, z),
               (-hw, y - th, z), (-lr, y - th, z), (lr, y - th, z), (hw, y - th, z)] for z, y in levels]
    K.loft(m, trings, "tabard", centre_rule(hip_z=Z_HIP + 0.1, drop=P["skirt_drop"] + 0.06, lr_half=lr), caps=(True, True))


def add_body(m: K.Model, P: dict) -> None:
    add_head(m, P)
    add_torso(m, P)
    add_arm(m, P)
    add_legs(m, P)
    add_skirt(m, P)
