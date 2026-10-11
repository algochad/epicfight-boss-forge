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

## Custom shapes

Bosses with a `shape` block are painted onto their own generated mesh (see `shape-forge.md`): the texture is a square atlas sized from `shape.texture.density` (texels per block, default 32; use 48 for a crisp face and ornaments), `appearance.texture.scale` is ignored, there is no separate overlay layer (`overlay` entries are baked into base texels), and attachments/parts use their own materials. The preview has five panels: front, three-quarter, side, back, texture, and a second image `bossforge/previews/<id>_closeup.png` (path printed as `closeup` by `skin`) frames the body only (head, torso, arms, legs: wings, capes and halos fall off the panel edges): body front, body three-quarter and the face head-on. Eyes, brow, scar, beard, hair, mouth, blush and bangs go on the head box front; emblem, trim, plates, cracks, wear, runes, filigree and gradient on the base regions or on boxes (next section).

### Targeting boxes (`box:<id>`)

Every attachment and part box (skirt panels, pauldron plates, pouch, bun...) is its own region named `box:<box id>` (`bossforge shape` lists the boxes; mirrored copies end in `_r` / `_l`). A feature's `region` / `regions` may name one: `"region": "box:pouch"`. A name with `*`, `?` or `[..]` is a glob over the box ids: `"box:skirt_fr*"` = all front skirt boxes, `"box:skirt_fr[01]_*"` = the front panels without the hem trim boxes, `"box:pauldrons_pl*"` = every pauldron plate. `part:<glob>` matches the mesh part itself, which for a custom shape is the box id WHATEVER its region, so it also reaches the base body boxes (`part:fist_*` = the two fists, `part:arm_?_upper` = the upper arms, `part:arm_r_*` = every box of the right arm). A `box:` / `part:` target that matches no box of a custom shape is a warning `unknown_box`. `trim`, `stripes`, `emblem`, `cracks`, `runes`, `plates`, `filigree`, `gradient`, `repaint` and `sparkle` all take these names (`hands`, `feet`, `arms`, `legs` and the plain regions work as before); features without a `regions` list never touch box regions.

## Appearance schema

`{description, archetype, palette{primary,secondary,accent,skin,hair,eyes}, regions{<key>:{material,color?,accent?}}, overlay:[{region,material,color?,accent?,y:[lo,hi],name?}], overlay_enabled, features:[...], texture:{scale}}`.

- Merge: archetype defaults, then spec. `palette` and `regions` merge per key; `overlay` and `features` REPLACE the archetype lists when present.
- Colors are palette keys or `#rrggbb`. `description` is free text for docs, never parsed.
- Region keys: `head torso arm_l arm_r leg_l leg_r hands feet arms legs` (`arms`/`legs` mean both sides). Precedence per texel: `arm_l|arm_r|leg_l|leg_r` > `hands`/`feet` (lowest 22% / 18% of the limb) > `arms`/`legs` > `torso`/`head`.
- `overlay` entries paint the overlay layer (hat/jacket/sleeves/pants islands, drawn slightly outside the body) inside `y` range (0 bottom .. 1 top of the region). They create worn armor, crowns, hems. `overlay_enabled:false` paints them onto the base layer instead (use if the overlay does not render in game). The golem has no overlay layer: entries are ignored with warning `overlay_ignored`.

Materials (`bossforge list materials`): `skin skin_soft hair hair_soft plate_steel plate_gold leather chainmail cloth silk lace silk_feather fur bone stone scales ember`. `silk` is smooth fabric (soft tone shifts, no checker grain) for pale robes, `lace` is a light frill with a faint diagonal mesh, `silk_feather` shades one feather vane per box face (faint rib down the middle, soft edge shading, barely visible barbs, rare single sparkle texels; it uses the face rectangle, so it follows rotated boxes), `skin_soft` is smooth stylised skin, `hair_soft` is strand-shaded hair (one tone per strand that drifts along its length, a few bright streaks, a lighter gloss band across the upper fifth of each box, a darker underside, no grey flecks). `cloth` (checker weave), `leather` and `plate_steel` (seams) are grainy by design: on a dense custom-shape atlas they read as a checkerboard on pale fabric, so give such boxes `silk` / `lace` with `repaint`.

Features, applied in list order (`bossforge list features`):

| Type | Parameters (defaults) |
|---|---|
| `eyes` | style `human\|slit\|hollow\|visor\|anime` (human), color (palette eyes; the iris for `anime`), glow (false), size 0.5-1.5 (1.0), y 0.3-0.8 (0.55, `anime`), lash (dark brown, `anime`) |
| `brow` | color |
| `scar` | side `left\|right` (character's own side), color |
| `hair` | style `short\|long\|bald\|hood` (short), color, material `hair\|hair_soft` (hair), length 0.1-1.0 (1.0: how far down the back and sides of the head `long` hair reaches) |
| `bangs` | color (palette hair), depth 0.12-0.42 (0.30), part `center\|left\|right\|none` (center), locks (true), lock_width 0.04-0.25 (0.12), lock_length 0.3-1.0 (0.92), clear 0.15-0.6 (0.42), material `hair\|hair_soft` |
| `mouth` | style `small\|smile\|line` (small), color (rose), y 0.5-0.95 (0.80), width 0.05-0.4 (0.14), nose (true) |
| `blush` | color (rose), amount 0-1 (0.5), size 0.5-2 (1.0), y 0.4-0.95 (0.72) |
| `beard` | length `short\|long`, color, material `hair\|hair_soft` |
| `emblem` | shape `sun\|cross\|skull\|rune_circle\|star\|crescent\|winged_cross\|latin_cross`, region (torso), face (front), color, size (0.6), cx, cy, glow (false \| true \| 0-1), halo 1-8 texels (3), hot 0-1 (0.55), glow_color (the color), wing_color (pale blue-white; `winged_cross`) |
| `trim` | region / regions[] (torso), where `hem\|cuff\|collar\|belt` (hem), color, width 0.03-0.25 (0.10), at 0-1 (0: hem / cuff start this far up the REGION, so `at` > 0 puts a cuff above a hand) |
| `filigree` | region / regions[], face / faces[] (front), pattern `meander\|key\|lozenge\|cross_band\|wing\|line\|diamond_chain\|crest\|collar_wing\|lace` (meander), where `border\|band\|panel` (border), color (cream), cell 0-8 texels (0 = auto), size `auto\|small\|large`, inset 0-8 cells, sides[] `top\|bottom\|left\|right`, mirror (true); band: lo, hi; panel: fill, outline, width, height, cx, cy, motif_height, anchor, texture (see below) |
| `gradient` | region / regions[] (all body regions), face / faces[] (all), from, to (at least one), lo 0-1 (0), hi 0-1 (1), curve `smooth\|linear`, amount 0-1 (1), texture 0-1 (0.7) |
| `repaint` | regions[] (required: body regions, `box:<glob>`, `part:<glob>`), face / faces[], material (any material, required), color / accent (the box's own colours by default) |
| `sparkle` | region / regions[] (all body regions), faces[] (front back left right side), density 0-0.2 (0.012 = share of texels), strength 0-1 (0.85), color (white) |
| `plates` | regions[], color, spacing blocks (0.35) |
| `cracks` | color, density 0-1 (0.5), regions[] |
| `wear` | amount 0-1 (0.5) |
| `runes` | color, density 0-1 (0.4), regions[] (torso, arms) |
| `stripes` | region, orient `v\|h`, count 1-8, width 0.02-0.2, color |

A feature that names a region the template lacks is skipped silently. Numbers outside the ranges are rejected by `validate` (`out_of_range`); colours are palette keys or `#rrggbb` (`from`, `to`, `fill`, `outline`, `lash`, `glow_color`, `wing_color` too).

## Filigree: pixel-art ornaments

`filigree` draws crisp flat-colour strokes (no noise, no shading, 1 cell wide with 1 cell gaps) on one or more faces of the targeted texels. The rectangles of the faces (the two halves of a skirt panel, the upper and lower skirt segment, chest + abdomen + pelvis) are laid out on one grid from their world positions and merged into square cells of `cell` texels (auto = about 1/32 block: 1 texel at 32-48 texels per block, 2 at 64), so patterns run across box seams, stay mirror-symmetric, and keep the same size in blocks at any `shape.texture.density`. Separate clusters (left arm, right arm) each get their own ornament.

- `where: border`: a strip tiled along the OUTLINE of the face (any irregular outline; hollow-square corner pieces with a dot when the strip is 5 rows). `sides` limits it to some edges (`["bottom"]` = a hem border), `inset` starts it that many cells inside.
- `where: band`: the same strip across the height range `lo`..`hi` (fractions of the face height, 0 = bottom), centred in the band; use it for hems, cuffs, shoulder straps.
- `where: panel`: one centred motif. The panel is a rectangle `width` x `height` (fractions of the face, centred at `cx`, `cy` with `cy` 0 = top). `fill` paints the rectangle (keeping `texture` of the base brightness variation), `outline` draws a 1-cell outline on `sides`, the motif (`color`) sits inside, `motif_height` (fraction of the panel) and `anchor` (`center|top|bottom`) place it: a cream tabard with the motif near the hem is `fill` cream + `outline` blue + `anchor: bottom` + `motif_height: 0.4`.

| `pattern` | Border / band strip | Panel motif |
|---|---|---|
| `meander` | squared Greek-key hooks | a meander frame with a centre mark |
| `key` | stepped zig-zag line | nested stepped frames with a notch |
| `lozenge` | chain of small diamonds | a diamond outline with a plus in the middle |
| `cross_band` | plus signs | a cross with corner dots |
| `wing` | layered feather chevrons | a stem with three feather lines fanning out (a U-shaped crest when short) |
| `line` | a plain line (double when large): cuff edges, outlines | a one-cell rectangle outline |
| `diamond_chain` | diamonds sharing their tips | diamonds as wide as the box stacked tip to tip (the centre ornament of a tabard) |
| `crest` | battlement / ladder band | a bodice crest: two bars that flare at the top, carry two small inward tabs, and close in a stepped cup narrowing to one block (the cream U-shaped belly panel) |
| `collar_wing` | layered feather chevrons | two wing pieces either side of an empty middle (neck and cross): stepped feather lines rising outward |
| `lace` | scalloped arches over a solid line, outer row first (on a hem the arches hang at the bottom) | a frame filled with a chevron mesh |

`size: auto` uses the 3-row strips unless the face is at least 24 cells wide and tall, then the 5-row ones (`small` / `large` force one). On a 0.5-block-wide skirt at density 48 a border strip is about 3 texels deep; make the face's ornament bigger with `cell: 2` (chunkier pixels) rather than by widening the panel.

## Gradient

`gradient` blends the targeted texels vertically: `from` at height `lo`, `to` at `hi` (fractions of the targeted texels' own height, 0 = bottom, clamped beyond; the height is measured over ALL targeted texels, so `regions: ["box:skirt_fr*", "box:skirt_bk*"]` fades the whole skirt as one piece). An omitted end keeps the base colour: a blue hem that fades into the robe is `from: blue, lo: 0, hi: 0.3`, a robe that turns lavender toward the hem is `from: lavender, lo: 0, hi: 0.8`. `texture` keeps that share of the base material's brightness variation (0 = flat), `amount` scales the whole effect.

## Face recipe (stylised, readable at 32-64 texels per block)

Order matters: `hair` (back and sides), `bangs`, `eyes`, `mouth`, `blush`. All of them work on the head-front texel grid, so they scale with the density and the face stays symmetric.

```json
{"type": "hair", "style": "long", "color": "hair", "material": "hair_soft"},
{"type": "bangs", "color": "hair", "depth": 0.34, "material": "hair_soft"},
{"type": "eyes", "style": "anime", "color": "#5a3a22"},
{"type": "mouth"}, {"type": "blush"}
```

`eyes.anime`: a dark lash line on the upper lid, a dark iris on the inner side, a small cream eye white on the outer side and one white highlight texel (about 5 x 3 texels at density 48). `bangs` paints a fringe across the forehead (shorter at the parting, strands of uneven length) and a lock at each temple and never reaches below `clear`, so the eyes stay free. Use `skin_soft` for the head region for a smooth anime skin.

## Glowing emblems

`emblem` with `glow` adds a soft halo around the shape (`halo` texels wide, blended toward `glow_color`) and lightens the shape toward white (`hot`; texels at least two texels deep are whiter), so a cyan cross reads as a light source. `latin_cross` is the long-armed cross; `winged_cross` adds two stepped feather lines in `wing_color` beside it (use `latin_cross` plus a `collar_wing` filigree when the wings should be bigger and sit at the collar). Give the cross `size` 0.9 to use the chest width.

## Per-box materials, sleeves, hem, boots, wings

`repaint` re-shades targeted texels with another material and keeps the box's own colour, so any preset box can get a smooth or lace look without touching the preset: the checkerboard on pale boots and frills comes from `plate_steel` seams and `cloth` parity (`repaint` them with `silk` / `lace`), the feather boxes want `silk_feather`. `part:` targets reach the base body: fists are skin even when the whole arm is silk (`hands` region = silk, `{"type":"repaint","regions":["part:fist_*"],"material":"skin_soft","color":"skin"}`).

- Sleeve: a cuff ABOVE the hand is `trim` with `at` (fraction of the arm region's height; the forearm bottom is about 0.07): wide blue band `{"type":"trim","regions":["arms"],"where":"cuff","at":0.07,"width":0.17,"color":"secondary"}`, cream edge line above it `"at":0.24,"width":0.04`, a cream `filigree` band with `regions: ["part:arm_?_upper"]` and `faces: ["front","back","left","right"]` near the shoulder, and a lighter inner side with a flat `gradient` (`from` = `to` = the light colour, `amount` 0.35, `texture` 1.0) on `part:arm_r_*` face `left` / `part:arm_l_*` face `right` (the character's own inner side).
- Hem frill: `gradient` over `box:skirt_h[fbs][0-9]_*` from lavender at the bottom to white (`texture` 0.3 keeps a little fabric grain), plus a `filigree` `border` with `pattern: lace`, `sides: ["bottom"]` in white on the same boxes (all four faces).
- Boots: `repaint` `box:greaves_shin_*` / `box:greaves_boot_*` with `silk`, a blue cuff band `trim` `where: collar` `width: 0.25` on `box:greaves_shin_*`, a lighter sole `gradient` (`from` pale, `lo` 0, `hi` 0.35) on `box:greaves_boot_*`.
- Wings: the seraph feather boxes (`wings_f*`) use `silk_feather` by default (stairs and struts stay `silk`); `repaint` them yourself on other presets. Add a few more sparkles anywhere with `sparkle` (`density` 0.008-0.02).
- Hair: `hair_soft` gives every hair box vertical strands, a gloss band and a darker underside; `hair.length` 0.3-0.5 stops painted back and side hair above the neck; `bangs.locks: false` when a `hairdo` preset already carries locks.

Example (the Nightingale robe: glowing latin cross with collar wings, cream bodice crest, shoulder and sleeve filigree, tabard with a lozenge, lace frill, smooth boots; every `box:` glob matches the current skirt / greaves / pauldron preset ids):

```json
{"type": "repaint", "regions": ["box:greaves_shin_*", "box:greaves_boot_*", "box:greaves_knee_*", "box:skirt_*t_*", "box:skirt_band"], "material": "silk"},
{"type": "repaint", "regions": ["box:greaves_toe_*"], "material": "silk", "color": "#a9b4cc"},
{"type": "emblem", "shape": "latin_cross", "region": "torso", "face": "front", "color": "#8ff0ff", "size": 0.9, "cy": 0.72, "glow": true},
{"type": "trim", "region": "torso", "where": "collar", "color": "secondary", "width": 0.1},
{"type": "filigree", "regions": ["box:pauldrons_pl*"], "faces": ["front", "back"], "where": "band", "pattern": "key", "lo": 0.1, "hi": 0.9, "color": "#f2ead3"},
{"type": "filigree", "region": "torso", "face": "front", "where": "panel", "pattern": "collar_wing", "cy": 0.14, "width": 0.95, "height": 0.3, "color": "#e4eeff"},
{"type": "filigree", "region": "torso", "face": "front", "where": "panel", "pattern": "crest", "cy": 0.68, "width": 0.7, "height": 0.3, "color": "#f2ead3"},
{"type": "filigree", "region": "torso", "face": "back", "where": "panel", "pattern": "wing", "cy": 0.3, "width": 0.8, "height": 0.25, "color": "#e4eeff"},
{"type": "repaint", "regions": ["part:fist_*"], "material": "skin_soft", "color": "skin"},
{"type": "trim", "regions": ["arms"], "where": "cuff", "at": 0.07, "width": 0.17, "color": "secondary"},
{"type": "trim", "regions": ["arms"], "where": "cuff", "at": 0.24, "width": 0.04, "color": "#f2ead3"},
{"type": "filigree", "regions": ["part:arm_?_upper"], "faces": ["front", "back", "left", "right"], "where": "band", "pattern": "key", "lo": 0.4, "hi": 0.7, "color": "#f2ead3"},
{"type": "trim", "regions": ["box:greaves_shin_*"], "where": "collar", "color": "secondary", "width": 0.25},
{"type": "gradient", "regions": ["box:greaves_boot_*"], "from": "#e8eefa", "lo": 0.0, "hi": 0.35},
{"type": "gradient", "regions": ["box:skirt_h[fbs][0-9]_*"], "from": "#dcd5ff", "to": "#f4f6ff", "texture": 0.3},
{"type": "filigree", "regions": ["box:skirt_h[fbs][0-9]_*"], "faces": ["front", "back", "left", "right"], "where": "border", "pattern": "lace", "sides": ["bottom"], "color": "#ffffff"},
{"type": "filigree", "regions": ["box:skirt_fp*"], "where": "panel", "pattern": "lozenge", "width": 0.8, "height": 0.9, "outline": "secondary", "color": "#7e93bf", "anchor": "bottom", "motif_height": 0.45, "sides": ["left", "right"]},
{"type": "filigree", "regions": ["box:skirt_fr[01]_*"], "where": "border", "pattern": "key", "sides": ["left", "right"], "color": "#f2ead3"}
```

## Reading the preview

Panels left to right: front, three-quarter, back, texture sheet (custom shapes: front, three-quarter, side, back, texture). Check: face (eyes, brow, scar, beard) on the FRONT panel; crown/hair at the top; torso emblem on the chest; plate overlay on torso, arms, legs; no large flat or empty areas; glow colors readable; ornaments crisp (strokes one cell wide, symmetric about the centre, no muddy noise) and not cut by a box seam. The preview shows the whole boss at one scale, so a boss with big wings is small in it: read `<id>_closeup.png` for the face, chest and skirt. Describe what you see to the user. Change `palette`, `regions`, `overlay`, `features`, `seed`, `texture.scale`; do not edit the PNG.
