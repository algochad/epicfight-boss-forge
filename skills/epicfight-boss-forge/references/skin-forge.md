# Skin forge (procedural texture on the real EF mesh)

`bossforge skin boss.json --out DIR` paints a texture onto the Epic Fight mesh UV layout (triangle rasterization, per-texel 3D position drives noise, so patterns flow across faces) and renders a three-view preview using the same mesh and texture.

Outputs: `src/main/resources/assets/<mod>/textures/entity/<id>.png` and `bossforge/previews/<id>_preview.png` (front, three-quarter, back, texture sheet). Texture size = authored UV size x `appearance.texture.scale` (1, 2 or 4; default 2; max edge 512). Output is deterministic from `seed`.

## Mesh measurements (EF 20.14.17, branch 1.20.1, ref 9d25f19c)

| Value | biped | iron_golem |
|---|---|---|
| authored UV | 64x64 | 128x128 |
| unit scale | 1.0 | 1.0 |
| axis mode (raw Z-up -> y-up) | x,z,-y | x,z,-y |
| index order per vertex | pos,uv,nrm | pos,uv,nrm |
| UV origin | top | top |
| front sign (character faces) | -z | -z |
| model height (blocks, non-overlay parts) | 2.0 | 2.7748 |
| parts | head hat torso jacket rightArm rightSleeve leftArm leftSleeve rightLeg rightPants leftLeg leftPants | head chest core rightArm leftArm rightLeg leftLeg |
| overlay parts | hat jacket sleeves pants | none |

Texture size note: UV coordinates only give a lower bound (the biped UVs sit on a 16x32 grid); the real size comes from texel density (1 texel = 1/16 block).

## Appearance schema

`{description, archetype, palette{primary,secondary,accent,skin,hair,eyes}, regions{<key>:{material,color?,accent?}}, overlay:[{region,material,color?,accent?,y:[lo,hi],name?}], overlay_enabled, features:[...], texture:{scale}}`.

- Merge: archetype defaults, then spec. `palette` and `regions` merge per key; `overlay` and `features` REPLACE the archetype lists when present.
- Colors are palette keys or `#rrggbb`. `description` is free text for docs, never parsed.
- Region keys: `head torso arm_l arm_r leg_l leg_r hands feet arms legs` (`arms`/`legs` mean both sides). Precedence per texel: `arm_l|arm_r|leg_l|leg_r` > `hands`/`feet` (lowest 22% / 18% of the limb) > `arms`/`legs` > `torso`/`head`.
- `overlay` entries paint the overlay layer (hat/jacket/sleeves/pants islands, drawn slightly outside the body) inside `y` range (0 bottom .. 1 top of the region). They create worn armor, crowns, hems. `overlay_enabled:false` paints them onto the base layer instead (use if the overlay does not render in game). The golem has no overlay layer: entries are ignored with warning `overlay_ignored`.

Materials (`bossforge list materials`): `skin hair plate_steel plate_gold leather chainmail cloth fur bone stone scales ember`.

Features, applied in list order (`bossforge list features`):

| Type | Parameters (defaults) |
|---|---|
| `eyes` | style `human\|slit\|hollow\|visor` (human), color (palette eyes), glow (false), size 0.5-1.5 (1.0) |
| `brow` | color |
| `scar` | side `left\|right` (character's own side), color |
| `hair` | style `short\|long\|bald\|hood` (short), color |
| `beard` | length `short\|long`, color |
| `emblem` | shape `sun\|cross\|skull\|rune_circle\|star\|crescent`, region (torso), face (front), color, size (0.6), cx, cy |
| `trim` | region, where `hem\|cuff\|collar\|belt` (hem), color, width 0.03-0.25 (0.10) |
| `plates` | regions[], color, spacing blocks (0.35) |
| `cracks` | color, density 0-1 (0.5), regions[] |
| `wear` | amount 0-1 (0.5) |
| `runes` | color, density 0-1 (0.4), regions[] (torso, arms) |
| `stripes` | region, orient `v\|h`, count 1-8, width 0.02-0.2, color |

A feature that names a region the template lacks is skipped silently.

## Reading the preview

Panels left to right: front, three-quarter, back, texture sheet. Check: face (eyes, brow, scar, beard) on the FRONT panel; crown/hair at the top; torso emblem on the chest; plate overlay on torso, arms, legs; no large flat or empty areas; glow colors readable. Describe what you see to the user. Change `palette`, `regions`, `overlay`, `features`, `seed`, `texture.scale`; do not edit the PNG.
