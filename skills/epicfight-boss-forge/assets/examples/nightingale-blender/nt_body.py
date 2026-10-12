"""Nightingale body: head, torso robe + blue collar, sleeves with flared blue cuffs, hands, legs, boots (cuff, sole, rune plate), skirt
(light grey-blue wall, blue hem band, lace frill), front and back tabard. Hair / wings / ribbons / harness live in their own parts,
pauldrons / belt / pouches in `nt_extras`.

The body is on the ART PIXEL grid (`nt_common.px`: 1 pixel = 1 texel = 0.0325 blocks, head cube = 8 px): every number below is a
count of pixels unless it says otherwise. x = pixels outward from the centre line (the character's right is +x, mirrored by the caller),
y = pixels forward (torso back at -2, front at +6), z = pixels above the soles (collar 44, belt 27.5-29.5, hip 23, hem 14, knee 12).
"""
from __future__ import annotations

import math

from nt_common import K, px, arm_rule, centre_rule, leg_rule, skirt_rule, torso_rule, Z_HIP, Z_KNEE, HEAD_BOX, Z_COLLAR


# ------------------------------------------------------------------------------------------------------- head and neck
def add_head(m: K.Model, P: dict) -> None:
    """Skin head cube (8 x 8 x 8 art pixels, `nt_common.HEAD_BOX`; the face is painted on its front) and a short neck."""
    hx, y0, y1, z0, z1 = HEAD_BOX
    K.block(m, (-hx, y0, z0), (hx, y1, z1), "skin", "Head", density=P["face_density"])
    # neck: from the collar up into the head
    K.block(m, (-0.045, y0 + 0.03, Z_COLLAR - 0.01), (0.045, y1 - 0.03, z0 + 0.02), "skin",
            K.rule_blend(2, Z_COLLAR, z0 + 0.02, "Chest", "Head"))


# ------------------------------------------------------------------------------------------------------- torso and arms
# Every textured face is a whole number of pixels wide / tall (UV islands are whole texels); parts that sit on each other overlap by
# 1 px instead of touching, so no coplanar internal caps are left behind.
CY = px(2.0)   # centre of the torso / arm / leg depth (y)


def add_torso(m: K.Model, P: dict) -> None:
    """Slim robe shell from the collar (z 44) over the waist (belt z 31-33) to the hips (z 23, 12.5 px wide), 8 px deep, plus the
    blue stand-up collar ring around the neck (trim, 7 x 9 px, z 42.5 -> 44.5)."""
    secs = [(px(44), 0.0, CY, px(5.0), px(4.0)), (px(31), 0.0, CY, px(4.5), px(4.0)), (px(23), 0.0, CY, px(6.25), px(4.0))]
    K.stacked_rects(m, secs, "robe", torso_rule(), smooth=False)
    K.stacked_rects(m, [(px(44.5), 0.0, CY, px(3.5), px(4.5)), (px(42.5), 0.0, CY, px(3.5), px(4.5))], "trim", torso_rule())


def add_arm(m: K.Model, P: dict) -> None:
    """Right arm (+x), mirrored by the caller: 5 x 5 px sleeve z 43 -> 30, its centre leaning out from x 7.75 to 9.25 (landmarks: 0.24 at
    the upper arm, 0.30 at the wrist; a 0.25 px gap to the torso), blue cuff 6 x 6 px (z 31 -> 27: flared 0.5 px each side), 4 x 4 px
    hand (z 28 -> 22) splayed 1 px further out. Tag `arm` (the volume the cloth checks test against)."""
    mk = m.mark()
    m.tag = "arm"
    w = arm_rule("R")
    sleeve = [(px(43), px(7.75), CY, px(2.5), px(2.5)), (px(37), px(8.5), CY, px(2.5), px(2.5)), (px(30), px(9.25), CY, px(2.5), px(2.5))]
    K.stacked_rects(m, sleeve, "robe", w)
    cx = px(9.25)
    K.block(m, (cx - px(3.0), CY - px(3.0), px(27)), (cx + px(3.0), CY + px(3.0), px(31)), "trim", w)    # cuff
    hx = px(10.25)
    K.block(m, (hx - px(2.0), CY - px(2.0), px(22)), (hx + px(2.0), CY + px(2.0), px(28)), "skin", w)    # hand
    m.mirror(mk)
    m.tag = ""


# ------------------------------------------------------------------------------------------------------- legs and boots
LEG_X = px(2.75)   # centre of the right leg / boot (x)


def add_legs(m: K.Model, P: dict) -> None:
    """Right leg (+x), mirrored by the caller: 4 x 4 px skin thigh / shin z 23 -> 11 (bare below the skirt), blue boot cuff 5 x 5 px
    (z 12 -> 9), grey boot shaft 4 x 4 px (z 10 -> 4), foot 4 x 7 px (z 1 -> 4) with a 5 x 4 px toe cap, a 5 x 9 px sole slab (z 0 -> 1,
    `boots` painted lighter per face) and the flat 3 x 3 px rune plate (group `rune`) on the back of the shaft. Tag `leg`."""
    mk = m.mark()
    m.tag = "leg"
    w = leg_rule("R")
    cx = LEG_X
    K.stacked_rects(m, [(px(23), cx, CY, px(2.0), px(2.0)), (px(17), cx, CY, px(2.0), px(2.0)), (px(11), cx, CY, px(2.0), px(2.0))], "skin", w)
    K.block(m, (cx - px(2.5), CY - px(2.5), px(9)), (cx + px(2.5), CY + px(2.5), px(12)), "trim", "Leg_R")        # cuff
    K.block(m, (cx - px(2.0), px(0.0), px(4)), (cx + px(2.0), px(4.0), px(10)), "boots", "Leg_R")                  # shaft
    K.block(m, (cx - px(2.0), px(0.0), px(1)), (cx + px(2.0), px(7.0), px(4)), "boots", "Leg_R")                   # foot
    K.block(m, (cx - px(2.5), px(4.0), px(1)), (cx + px(2.5), px(8.0), px(5)), "boots", "Leg_R")                   # toe cap
    K.block(m, (cx - px(2.5), px(-0.5), px(0)), (cx + px(2.5), px(8.5), px(1)), "boots", "Leg_R")                  # sole
    K.block(m, (cx - px(1.5), px(-0.5), px(5)), (cx + px(1.5), px(0.0), px(8)), "rune", "Leg_R")                   # heel rune plate
    m.mirror(mk)
    m.tag = ""


# ------------------------------------------------------------------------------------------------------- skirt
def _skirt_half_ring(z: float, hx: float, hy: float, cy: float, gap: float, thick: float, n: int, power: float = 3.0,
                     droop: float = 0.0):
    """Closed ring of the RIGHT half of the skirt wall: outer + inner polyline of a rounded rectangle (superellipse) from the back
    centre (x = 0) round the side to the front tip at x = gap (the front opening is +-gap wide, for the outer AND the inner polyline, so
    the wall's front edge is a straight cut at x = gap). `droop` lowers the ring by up to that much towards the front tip (the hem steps
    down towards the slit). Order: outer k = 0..n, then inner k = n..0 (the inner point of column k is `ring[2 * n + 1 - k]`)."""
    def pt(rx, ry, k):
        a1 = math.acos(min(1.0, (gap / rx) ** (power / 2.0)))
        phi = -math.pi / 2.0 + (a1 + math.pi / 2.0) * k / n
        c, s = math.cos(phi), math.sin(phi)
        return (rx * math.copysign(abs(c) ** (2.0 / power), c), cy + ry * math.copysign(abs(s) ** (2.0 / power), s),
                z - droop * K.smoothstep(0.0, a1, phi))

    outer = [pt(hx, hy, k) for k in range(n + 1)]
    inner = [pt(hx - thick, hy - thick, k) for k in range(n, -1, -1)]
    return outer + inner


def _skirt_dims(t: float):
    """(hx, hy, cy) of the skirt wall at t = 0 (hip) .. 1 (hem): 13.5 px wide at the hip (0.5 px wider than the torso hip: the swung
    thigh passes inside the wall) flaring to 19.5 px at the hem (landmarks: hem width 0.67 with the band), 8 -> 9.5 px deep."""
    e = t ** 1.15
    return K.lerp(px(6.75), px(9.75), e), K.lerp(px(4.0), px(4.75), e), K.lerp(px(2.0), px(1.75), t)


def add_skirt(m: K.Model, P: dict) -> None:
    """Robe skirt below the hip (z 23 -> hem 14), open at the front (`skirt_gap` px slit half width), split at the back centre (two
    halves built as the right half + mirror, each following its own thigh: `skirt_rule`, no vertex shared between the legs). On it: a
    blue hem band (trim, 2 px), a lace frill (`lace`) of short tapering tabs hanging 1-3 px below the hem, and the front / back
    tabard (`_tabard`)."""
    z_top, z_hem = px(23), px(14)
    segs, n = P["skirt_segments"], P["skirt_columns"]
    gap = px(P["skirt_gap"])
    droop = P["skirt_droop"]
    thick = px(1.0)
    rule = skirt_rule("R", drop=P["skirt_drop"])
    rings = []
    for i in range(segs + 1):
        t = i / float(segs)
        hx, hy, cy = _skirt_dims(t)
        rings.append(_skirt_half_ring(K.lerp(z_top, z_hem, t), hx, hy, cy, gap, thick, n, droop=droop * t * t))
    mk = m.mark()
    K.loft(m, rings, "skirt", rule, caps=(False, True), smooth=True, flat_cols=(n, 2 * n + 1))
    # blue hem band: 2 px tall, 0.5 px proud of the wall (its top ring sits where the wall's t = 1 - 2 / 9 ring droops: droop * t^2)
    hx, hy, cy = _skirt_dims(1.0)
    bx, by = hx + px(0.5), hy + px(0.5)
    K.loft(m, [_skirt_half_ring(z_hem + px(2.0), bx, by, cy, gap, px(1.5), n, droop=droop * 0.6),
               _skirt_half_ring(z_hem, bx, by, cy, gap, px(1.5), n, droop=droop)], "trim", rule, caps=(False, True),
           smooth=False, flat_cols=(n, 2 * n + 1))
    # lace frill: one tab per ring column hanging from the hem band, tapering inwards (0.5 px inside the wall at the top, 1.25 px at the
    # bottom: the sheet's under skirt is narrower than the panel hem); its lower edge zigzags in whole pixels, 3 px below the hem at the
    # back centre down to 1 px at the front slit (landmarks: jagged back hem at z 0.36-0.42). The tabs follow the thigh above the knee
    # joint (z 0.386) and the shin below z 0.356 (smooth in between): a lace that follows the thigh down to the boots is pushed into the
    # shin when the knee bends.
    if P.get("lace", True):
        lace_rule = K.rule_chain(2, [(Z_KNEE, "Thigh_R"), (Z_KNEE - 0.03, "Leg_R")])
        top = _skirt_half_ring(z_hem, hx - px(0.5), hy - px(0.5), cy, gap, px(1.0), n, droop=droop)
        bot = _skirt_half_ring(z_hem, hx - px(1.25), hy - px(0.75), cy, gap, px(1.0), n, droop=droop)
        steps = (3, 2, 3, 2, 2, 1, 1)
        for k in range(n):
            drop = px(steps[int(round(k * (len(steps) - 1) / float(max(1, n - 1))))])
            ia, ib = 2 * n + 1 - k, 2 * n + 1 - (k + 1)
            upper = [top[k], top[k + 1], top[ib], top[ia]]
            lower = [(x, y, z - drop) for x, y, z in (bot[k], bot[k + 1], bot[ib], bot[ia])]
            K.loft(m, [upper, lower], "lace", lace_rule, caps=(True, True))
    m.mirror(mk)
    _tabard(m, P, [(px(31), px(6.75)), (px(24), px(6.75)), (px(19), px(7.0)), (px(14.5), px(7.25))])      # front: stem from the belt, panel below
    _tabard(m, P, [(px(26), px(-2.75)), (px(20), px(-3.3)), (px(14.5), px(-4.0))])                        # back, under the pouches (7 x 11.5 px)


def _tabard(m: K.Model, P: dict, levels) -> None:
    """One closed cream panel (7 px wide, 1 px thick; ONE UV island so its pattern is continuous) hanging at the given
    (z, y of the centre line) levels. Vertices at x = +-lr in the face: the two halves follow their own thigh (`centre_rule`), only the
    strip between them stretches when the legs part."""
    hw, lr, th = px(3.5), P["tabard_lr"], px(0.5)
    rings = [[(hw, y + th, z), (lr, y + th, z), (-lr, y + th, z), (-hw, y + th, z),
              (-hw, y - th, z), (-lr, y - th, z), (lr, y - th, z), (hw, y - th, z)] for z, y in levels]
    K.loft(m, rings, "tabard", centre_rule(hip_z=Z_HIP + 0.1, drop=P["skirt_drop"] + 0.06, lr_half=lr), caps=(True, True))


def add_body(m: K.Model, P: dict) -> None:
    add_head(m, P)
    add_torso(m, P)
    add_arm(m, P)
    add_legs(m, P)
    add_skirt(m, P)
