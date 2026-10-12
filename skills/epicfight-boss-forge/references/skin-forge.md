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
| `decal` `iridescent` `bevel` `posterize` `palette_snap` | pixel-art features, see "Pixel-art painting" below (`gradient` also gains `space: world`, `steps`, `dither`, `stops`) |

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

## Pixel-art painting (decals, world gradients, sheen, quantising)

Target look: Minecraft pixel art, about 16 texels per art block, flat 2-3 tone shading, hand-placed ornaments, stepped gradients. Everything below works in TEXEL units or world positions (never in per-texel random noise) and is deterministic. Face classes (`front`/`back`/`left`/`right`/`top`/`bottom`/`side`) of a FLAT UV island of an imported mesh come from the island's own normal (geometric normals turned outward by the vertex normals) for every texel of the island, so rim texels of a smooth-shaded slab never flip class and the two sides of a double-sided feather stay `front` and `back`; a curved island (a skirt wrapped around the body) is classified per texel.

| Type | Parameters (defaults) |
|---|---|
| `decal` | region / regions[] (`part:` / `box:` globs, body regions; none with `anchor: head_front`), face / faces[], `bitmap` (rows) or `file` (`decals/<name>.txt`), palette `{char: colour\|null}`, color (`#`), scale 1-8 or `auto` (1; `auto` for `head_front`), anchor `center\|top\|bottom\|left\|right\|top_left\|top_right\|bottom_left\|bottom_right\|head_front` (center), offset `[dx, dy]` texels, scope `island\|union` (island), mirror `auto\|none\|flip` (auto), align `upright\|island` (upright), repeat `x\|y\|xy` + gap texels + count, partial (false), skip_small (false), fit `none\|inside` (none), blend `opaque\|multiply` (opaque), opacity 0-1, project `{view, at, pixel, facing, depth}` |
| `gradient` (added keys) | space `local\|world` (local), axis `x\|y\|z` (y), abs, lo_<axis> / hi_<axis> (blocks), block (blocks per unit, 1), steps 2-32, dither 0-1, stops `[[pos, colour], ...]` |
| `iridescent` | region / regions[], face / faces[], strength 0-1 (0.6), palette (pink, peach, yellow, lilac, aqua), dir `[x, y, z]` ([0.55, 1, 0.4]), scale blocks per cycle (0.5), block (1), normal 0-1 (0), steps 0-64 (5; 0 = smooth), dither 0-1 (0), offset 0-1, mode `mix\|tint` (mix) |
| `bevel` | region / regions[], face / faces[] (front back left right side), top / bottom / left / right -1..1 (0), width 1-2 (1), min_size 1-32 (3), mode `shade\|outline` (shade), color (black), amount 0-1 (0.5) |
| `posterize` | region / regions[], face / faces[], levels 2-32 (4), mode `luma\|rgb` (luma), dither 0-1 |
| `palette_snap` | region / regions[], face / faces[], `palette` [colours] or `colors` 2-64 (median cut, `refbake.median_cut`), dither 0-1 |

**Decal files and bitmaps.** `decals/<name>.txt` next to the spec (`file` is relative to the spec): a `;` at column 0 starts a comment line, `@<char> <colour>` maps a character (`#rrggbb`, a palette key `primary|secondary|accent|skin|hair|eyes`, or `none` = transparent), every other non-blank line is a bitmap row (right-padded with transparent pixels, at most 128 x 128). Without a mapping `.` and space are transparent and `#` is the feature's `color` (default cream `#f3ecd2`); any other undefined character is `decal_bad_palette`. The JSON `palette` wins over the file's directives. Example (`decals/wingmark.txt`):

```
; wing mark, drawn for the character's right piece
@# #f6efd8
@g #c9a54a
..#...
.##g..
###gg.
.##g..
..#...
```

**Orientation (per UV island, derived from the mesh).** One bitmap pixel covers `scale` x `scale` texels, no stretching. The painter fits each island's frame (world position as an affine function of the texel, the island's outward normal, `up` = world +Y projected on the surface, on horizontal faces the character's front; `right` = up x normal), so the picture reads UPRIGHT as seen from outside whatever rotation or mirroring the exporter gave the UV island (islands within 12 degrees of the texel axes snap to them: exact pixels; `align: island` snaps every island, so the picture tilts with a tilted feather but stays crisp). Two faces chained through a shared UV corner are separate islands (8-connected texel blobs). The bitmap is authored for the piece on the character's RIGHT side (+x, viewer-left in the front view); `mirror: auto` stamps the mirror image on LEFT pieces, anchors and offsets included, so a pair is an exact mirror pair; centre pieces (|x| < 0.03 block) are never flipped. `anchor` places the bitmap in the island's texel bounding box (`scope: union`: in the projected union of all targeted islands), `repeat` tiles whole copies along the axis (`partial: true` also a clipped copy), `fit: inside` slides the picture to the nearest spot where every opaque pixel lands on the island (tapered islands), `skip_small` gives islands smaller than the bitmap no copy and no warning. `blend: multiply` multiplies the base colour with the bitmap colour (a grey bitmap shades the base).

**World projection** (`project`, for curved surfaces such as a cone skirt where island frames land on the sides): every targeted texel whose normal faces the viewer (`dot(normal, toward viewer) >= facing`, 0.3) is looked up in the bitmap by its WORLD position (orthographic, nearest cell, no resampling). `view` `front|back|left|right|top|bottom` is the side the picture is seen from; `at` = world position in model blocks of the bitmap's `anchor`: `[x, y]` front/back, `[z, y]` left/right, `[x, z]` top/bottom; `pixel` = blocks per bitmap pixel (`auto` = median texel size times `scale`); only the nearest layer within `depth` blocks (0.05) of each projected cell is stamped, so inner walls and the far side stay clean. `mirror: auto` also stamps the reflection x -> -x (not for a centred decal); `repeat` needs `count`.

**Warnings** (never errors, in the `skin` report): `decal_too_big` (bitmap larger than the island, clipped), `decal_partial` (opaque pixels fell off the island), `decal_stretched` (UV texels are more than 1.25:1 stretched there), `decal_no_target` (nothing targeted / nothing faces the view), `face_grid` (see the face below). Validation errors: `decal_missing_file`, `decal_bad_bitmap`, `decal_bad_palette`, `decal_project_conflict`, `needs_world_space`, plus the usual `required` / `bad_type` / `out_of_range` / `unknown_value` / `bad_color`.

**The 8x8 face.** At 16 texels per art block the head front island is 8 x 8 texels. `{"type": "decal", "anchor": "head_front", "file": "decals/face.txt"}` stamps an 8x8 (or 8x9) bitmap pixel by pixel on the head front texels (region `head`, face `front`; `scale: auto` picks whole texels per art pixel, 16 x 16 texels -> 2). The report carries `face: {texels, bitmap, scale, texels_per_art_pixel, exact}` and warns `face_grid` when the head front is not an exact multiple of the bitmap. The procedural face features (`eyes anime`, `brow`, `mouth`, `blush`) also work at 8x8 (eyes 2 x 2 texels, hard-edged blush), mirror-symmetric on the texel grid; `bangs` keeps its uneven strands.

**World gradients.** `{"type": "gradient", "space": "world", "regions": ["part:wing_a", "part:wing_b", "part:wing_c", "part:wing_d"], "lo_y": 0.75, "hi_y": 1.85, "stops": [[0, "#a49df0"], [0.45, "#d9d5fb"], [1, "#ffffff"]], "steps": 7, "texture": 0.4}`: the colour depends on the world height only, so it is continuous across every island and group (the tint follows each group's own mean brightness, so a white and a violet feather take the same ramp). `lo_<axis>` / `hi_<axis>` are blocks times `block` (set `block` to the model blocks of your authoring unit, e.g. 0.52 for art blocks), or give fractions `lo` / `hi` of the targeted bounding box; `lo > hi` reverses the ramp; `axis: x` with `abs: true` ramps with the distance from the symmetry plane. `steps` cuts N flat bands, `dither` turns band edges into a 4x4 Bayer stipple.

**Iridescent wings.** `{"type": "iridescent", "regions": ["part:wing_a", "part:wing_b"], "faces": ["back"], "strength": 0.65, "steps": 5, "scale": 0.45, "normal": 0.2}` lays a stepped pastel sheen on the back faces only; the front faces keep the gradient. The material `iridescent` is the same sheen as a base material.

**`quality: "pixel"`.** On a `mesh.groups` entry, a `regions` / `overlay` entry, `repaint` (`quality`) or globally (`appearance.texture.quality`): the materials `silk`, `silk_feather`, `lace`, `cloth`, `hair`, `hair_soft`, `plate_steel`, `plate_gold`, `leather`, `skin`, `skin_soft` switch to hard 2-3 tone variants: low-frequency noise sampled in texel units (the same chunky look at any density), regular patterns (2-texel strands and weave, one-texel diagonal lace mesh, a feather plank with a lighter first and darker last column, a rib and a darker tip row) and no per-texel random speckle; `skin_soft` is flat. `posterize` / `palette_snap` (put them LAST in the list) quantise whatever the features produced.

**Speed.** The `skin` of the 512 atlas of the Nightingale bundle at 16 texels per art block (about 19,000 painted texels, 17 features) takes about 1.5 s to paint (about 7 s for the whole `skin` command including the preview renders); a 1024 atlas at 80 texels per block (164,000 texels) took about 20 s.


Panels left to right: front, three-quarter, back, texture sheet (custom shapes: front, three-quarter, side, back, texture). Check: face (eyes, brow, scar, beard) on the FRONT panel; crown/hair at the top; torso emblem on the chest; plate overlay on torso, arms, legs; no large flat or empty areas; glow colors readable; ornaments crisp (strokes one cell wide, symmetric about the centre, no muddy noise) and not cut by a box seam. The preview shows the whole boss at one scale, so a boss with big wings is small in it: read `<id>_closeup.png` for the face, chest and skirt. Describe what you see to the user. Change `palette`, `regions`, `overlay`, `features`, `seed`, `texture.scale`; do not edit the PNG.

## Reference baking

`reference.bake` makes the painter copy the REAL pixels of calibrated reference views (concept art, character sheets: `reference.views`, see `reference-fit.md`) into the atlas instead of inventing the surface. It is texel driven: every atlas texel knows its 3D position, normal and orientation, is projected into each bake view, and gets the colour of the reference pixels under it. Procedural materials and features stay as the fallback for everything no view can see. Without a `reference.bake` block the painter is unchanged.

```json
"reference": {
  "views": {"front": {"image": "refs/sheet.png", "crop": [0, 66, 462, 525], "ground_y": 516, "center_x": 224.5, "px_per_block": 203},
            "back":  {"image": "refs/sheet.png", "crop": [462, 66, 748, 525], "ground_y": 515, "center_x": 686, "px_per_block": 203}},
  "bake": {"views": ["front", "back"], "min_facing": 0.5, "depth_tolerance": 0.06, "smear": true,
           "palette": 0, "regions": ["*"], "exclude": ["box:wings_*"], "order": "last", "blend": 1.0, "footprint": 1.0}
}
```

| Key | Values (default) | Meaning |
|---|---|---|
| `views` | list of `front` `back` `right` `left`, each defined in `reference.views` (all defined views, in that order) | views to bake from; the ORDER breaks ties between equally facing views |
| `min_facing` | 0.1-1.0 (0.5) | a view may colour a texel only when `dot(normal, toward_camera) >= min_facing` (0.5 = faces up to 60 degrees off the camera) |
| `depth_tolerance` | 0-0.5 blocks (0.06) | how far behind the model's own depth buffer a texel may lie and still count as visible (thin overlay boxes, coplanar neighbours) |
| `smear` | true/false (true) | texels no view samples directly: grazing sampling (facing 0.2 up to `min_facing`) and the surface fill below. `false` leaves them procedural |
| `palette` | 0 (off) or 8-64 | median-cut quantisation of ALL baked colours to that many colours (deterministic; a sixth of the entries are outlier slots, so a small glowing emblem or an eye keeps its own colour): crisp pixel-art look, removes noise. Too few colours flatten faces: use 48-64 for a full character |
| `regions` | `["*"]` or body regions (`head torso arms legs hands feet arm_l ...`), `box:<glob>`, `part:<glob>` | what may be baked; the same targeting as feature `regions` |
| `exclude` | same syntax ([]) | removed from `regions`; use it for parts whose geometry does not match yet (wings before `relief`), glowing features you want to keep procedural |
| `order` | `last` or `first` (last) | `last`: the bake overwrites the procedural result, features only decide texels the bake cannot reach; `first`: the bake is the base layer and features (eyes, emblems, filigree) draw over it |
| `blend` | 0-1 (1.0) | share of the baked colour mixed over the procedural one |
| `footprint` | 0.25-1.0 (1.0) | size of the sampled part of each texel's footprint. 1.0 = true box filter of the whole texel (lowest colour error, softest); 0.5-0.6 samples the centre of the texel only (crisper pixel-art edges, a little noisier). Never below one reference pixel |
| `sample` | `footprint` \| `center` (footprint) | `center` = the colour of the ONE reference pixel at the texel centre (no averaging, no blending of neighbours); the pixel-faithful mode for pixel-art references |
| `snap` | integer 0-64 (0 = off) | the art's pixel size in reference pixels: the sample point of every texel snaps to the middle of the `snap` x `snap` art pixel that contains it (and, without `sample: center`, the whole art pixel is averaged), so a texel never straddles two art pixels. `snap_origin` `[x, y]` (reference pixels) is a corner of the art pixel grid (default 0, 0) |

Private pixel-faithful bake (never commit the result: reference images and textures baked from them stay out of the repo, examples must not bake): when the concept art IS pixel art (every art pixel an `n` x `n` block of identical reference pixels after upscaling), set `"sample": "center", "snap": n` (find `n` and the grid corner with `reference-info --grid`; `snap_origin` = `[x, y]` of a pixel corner), a model whose texel grid is the art's pixel grid (16 texels per art block), `"palette": 24-32` and `"smear": false`. Each texel then copies exactly one art pixel instead of a footprint average. The repo examples carry no `reference.bake`: they are procedural and decal art only.

All values are validated by `validate` / `skin` (`unknown_key`, `bad_type`, `out_of_range`, `unknown_value`, `unknown_region`, `reference_unknown_view`, `duplicate_view`, `required` under `/reference/bake`).

### What happens to a texel

1. **Facing.** Views whose facing is below `min_facing` are skipped. The remaining views are tried best facing first (ties inside 0.01 go to the order of `bake.views`). All texels of one face therefore take the same view; a face is mixed from two views only where its best view cannot see some texels.
2. **Occlusion.** The model is rendered into a depth buffer per view at the reference resolution. The texel (centre and four corners of its footprint) must not lie more than `depth_tolerance` blocks (plus the slope of its own plane over half a pixel) behind it, so a wing behind an arm never gets the arm's pixels, hair in front of wings stays hair.
3. **Footprint.** The texel's own square, projected through the face (rotated boxes give a rotated parallelogram, sampled with sub-boxes), is averaged over the FOREGROUND pixels of the view only (exact area-weighted box filter from summed-area tables; the sheet background, brick wall and so on is never averaged in). At least half of the footprint must be foreground: a direct sample.
4. **Silhouette edge.** A footprint mostly on background takes the colour of the nearest interior foreground pixel within a few footprints (`edge`), so no background colour or half-blended edge pixel leaks into the model's outline. Further away than that the texel is `mask_miss`: the model sticks out of the reference there.
5. **Smear** (only with `smear: true`). Visible texels at grazing facing are sampled the same way (`smeared`). Everything still unbaked is filled from baked texels of the SAME box along the surface, across box edges, within 0.3 block: a face half hidden behind an arm takes its visible neighbours, a side or top face takes the blend of the two baked faces it joins (weights 1/hops^2 of the nearest two source faces; the strength fades out over the second half of the reach so there is no hard seam). `filled` texels never copy an occluder's pixels.
6. `palette`, `blend`, then the usual dilation into the atlas gutters.

Not baked at all (stay procedural): texels outside `regions`, texels the fill cannot reach, and `procedural` texels of boxes no view sees. Side faces of boxes are only baked directly when a `right` / `left` view is baked.

### What `skin` prints

`bossforge skin SPEC --out DIR` adds a `bake` object to its JSON (numbers: the fitted Nightingale, 3 views):

```json
"bake": {"views": {"front": {"facing": 12528, "direct": 9079, "edge": 406, "smeared": 0, "occluded": 2976, "mask_miss": 67, "left_procedural": 3043},
                   "back": {...}, "right": {...}},
         "total": {"texels": 67534, "direct": 17553, "edge": 1580, "smeared": 44, "filled": 42078, "procedural": 6279, "visible": 22812, "mask_miss": 3679, "occluded": 9925, "unseen": 34753},
         "direct_ratio": 0.7695,
         "seams": {"same_view_pairs": 0, "same_view_mean_delta": null, "cross_view_pairs": 1831, "cross_view_mean_delta": 58.79},
         "compare": {"front": {"compared_px": 106317, "baked_px": 103989, "unbaked_px": 2328, "mean_abs_error": 12.18, "within_24_share": 0.8168,
                               "texel_block_px": 4, "texel_block_mean_abs_error": 5.68, "reference_only_px": 4013, "model_only_px": 5409,
                               "image": "DIR/bossforge/previews/<id>_bake_front.png"}, "back": {...}, "right": {...}}}
```

- per view: `facing` texels facing the view, of them `direct` samples, `edge` (nearest interior colour), `smeared` (grazing), `occluded` (hidden by the model itself), `mask_miss` (visible, but the reference is background there), `left_procedural` (facing it but not painted from it).
- `total`: `texels` targeted, `direct` / `edge` / `smeared` baked from the reference, `filled` from neighbours, `procedural` untouched; `visible` = `direct + edge + mask_miss` (texels a view sees); `occluded` / `unseen` (no view faces them) are never visible. `direct_ratio` = `direct / visible`.
- Warning `bake_low_coverage` (path `/reference/bake`) when `direct_ratio` < 0.6: most of what the views see lands on reference background, so the calibration (`px_per_block`, `center_x`, `ground_y`) or the geometry does not match the reference (a part of the model outside the reference crop or mask counts as well: the back view of the Nightingale sheet misses its right wing under the weapon sheet). Run `bossforge fit` (`reference-fit.md`) and fix that FIRST: baking a mis-registered model copies the wrong pixels (a cross ends up on the head). `bake_view_unused` (a view sampled nothing), `bake_nothing_visible` (no texel faces any bake view: wrong `views`, `regions` or `min_facing`).
- `seams`: mean colour difference (0-255 per channel) across box edges between baked texels, split by whether both sides came from the same view or from different views. The cross-view number is what baking from two images adds at an edge (the views disagree about brightness, outline or registration); as a rule of thumb 0-25 is a clean seam, around 60 (the Nightingale example) means visible hard edges at box corners that the reference itself does not have: fix the registration of the views, or bake that part from one view only (`regions` / `exclude`).
- `compare`: for every view a PNG `[reference | the model rendered through that view with the FINAL texture | difference]` (difference: grey = colour error x3, blue = reference only, red = model only) and numbers over the pixels where the reference is foreground and the model shows a directly baked texel of a face looking at the view: `mean_abs_error` (0-255 per channel, pixel by pixel), `within_24_share` (pixels with every channel within 24), `texel_block_mean_abs_error` (the same after averaging both images over texel-sized blocks of `texel_block_px` pixels: detail finer than one texel cannot be copied, this is the number that says whether the COLOURS are right), `unbaked_px` (overlap pixels showing procedural / filled texels), `reference_only_px` / `model_only_px` (silhouette mismatch: geometry work, not a bake problem).

### Workflow and tuning

1. Calibrate the views and fit the geometry first (`reference-fit.md`): a good `fit` IoU is the precondition of a good bake.
2. Add `"bake": {"views": ["front", "back"]}`, run `skin`, read `direct_ratio` and open the `_bake_<view>.png` images. Colour error is concentrated at box edges where the model is a few pixels off the art.
3. `exclude` parts that do not match yet (`box:wings_*`) so their wrong pixels do not land on the wrong boxes, or bake them from a view that matches (`relief` parts are generated from the reference silhouette and bake at a `direct_ratio` near 1 for the view they were built from).
4. Pixel-art look: `palette` 48-64, `footprint` 0.5-0.6. Keep procedural detail where it should win: `order: "first"`, or `exclude` the face and let `eyes` / `mouth` features draw it.
5. Add a `right` view (a side sheet) to bake the side faces directly instead of filling them. A character without a left view: save a mirrored copy of the side image (`magick side.png -flop side_left.png`) and define `left` on it with `center_x` = image width - the right view's `center_x` and the mirrored `crop`; a symmetric character then gets both sides from real pixels.

### Measured, and what is not exact

- Self-consistency (the Nightingale model rendered through its own front and back view at 203 px per block, then baked back): every visible texel is a direct sample (`direct_ratio` 1.0) and the model re-rendered with the baked texture differs from that reference by 2.2 / 2.3 per channel on average (98 % of the pixels within 24), 1.5 / 1.6 after texel-block averaging (2.5 pixel by pixel at 144 px per block). That is the resampling floor of the box filter on an unaligned grid, not a bake error.
- The Nightingale sheets (`sheet_ortho_front_back.png` front + back, `sheet_front_side_back.png` right) on the relief-fitted model (217 boxes, atlas 512, density 48; 24 s for `skin` with bake and review images): `direct_ratio` 0.77 (front 0.95, back 0.59 because its crop cuts the right wing, right 0.88), colour error against the real art 12.2 / 11.2 / 17.3 per channel pixel by pixel, 5.7 / 5.3 / 4.5 after texel-block averaging, 82 / 84 / 72 % of the pixels within 24. Face, robe filigree, cuffs, hem, the glowing cross and the white-to-lavender wing gradient reproduce at texel resolution; what remains is a softer version of the art.
- Not exact: a texel is 1/`shape.texture.density` block (48 -> about 4 reference pixels at 203 px per block), so outlines 1-2 px wide, eye highlights and JPEG noise are averaged or lost, and the art's own pixel grid is not aligned with the texel grid (edges blend between neighbouring texels; `footprint` < 1 trades that for aliasing). The model is boxes: where an arm or a hair mass is a few pixels off the art, the neighbouring pixels (wing, wall edge) land on its edge texels (the grey ghosting at the sleeves). The reference carries its own lighting and shadows, they are baked in, and no tone matching is done between views (see `seams`). Surfaces no view sees (backs of wings, hidden sides, the inside of the skirt, the left side without a left view) are filled from neighbours or procedural, never real art. Boxes that overlap in the image share pixels: the nearest box gets them, boxes within `depth_tolerance` behind it get the same colours.
- Cost: pure Python, about 4 s for 67 000 texels and three 1024-pixel views on top of the 6 s the plain painter needs, plus 3 s for the review images.

## Painting an imported mesh

A boss whose body was modelled in Blender (`references/blender-pipeline.md`) has a `mesh` block instead of `shape`: the model and its UV layout come from the bundle, the painter still makes the texture. Everything else of the spec (`appearance`, `weapons`, `reference`, `fit`, `reference.bake`, `add-boss`, `qa`, `render-test`) works as for a `shape` boss.

```json
"mesh": {"file": "models/nightingale.efmesh.json",
         "groups": {"skin":   {"material": "skin_soft", "color": "skin"},
                    "robe":   {"material": "silk", "color": "primary", "accent": "accent"},
                    "hair":   {"material": "hair_soft", "color": "hair", "region": "private"},
                    "wing_f": {"material": "silk_feather", "color": "primary", "region": "private"}},
         "texture": {"size": 512, "padding": 4}}
```

- Bundle: `<stem>.efmesh.json` (the Epic Fight mesh JSON: 12 non-empty parts, weights on the biped joints) + `<stem>.meshmeta.json` (`bossforge-mesh/1`: the group names, the group of every triangle). `file` is relative to the spec (or absolute). A bundle comes from `bossforge blender export`, or from `meshsrc.bundle_from_shape(n, out_dir)` (an existing `shape` boss as a bundle, one group per box id; `meshsrc.spec_from_shape(raw, out_dir)` returns the equivalent `mesh` spec) to start a Blender edit of a box boss. Mutually exclusive with `shape` (`mesh_and_shape`) and with `body.template: iron_golem` (`mesh_requires_biped`); the rest pose is the EF biped bind pose, the character faces -z (right arm at +x, feet on y=0).
- `texture.size`: 64-2048, a power of two (default 512): the size of the generated PNG and the atlas the UVs (all within 0..1, v from the top row = `1 - v` of Blender) were laid out for. `texture.padding` (0-16, default 4): texels the painted islands are grown outwards, so mipmaps and filtering never bleed the empty background into the model; the shape painter's one-texel border is `padding: 1`. `texture.density` (8-64) is accepted for symmetry with `shape` and is not used: the painter paints every UV triangle texel from its 3D position and normal, so a seam in 3D is a seam in no texture.
- Groups are Blender material slots (lower-case `[a-z0-9_]`, `.001` suffixes stripped by the exporter). `groups.<name>` sets the group's default `material` / `color` / `accent` (they win over `appearance.regions`, exactly as a shape box's own style) and optionally its `region`.
- Regions. Every triangle belongs to a body region (`head torso arm_l arm_r leg_l leg_r`) chosen by the vertex weights: the region whose joints carry most of the weight of its three vertices (`Head` -> head, `Root Torso Chest Shoulder_*` -> torso, `Arm Elbow Hand Tool` -> arm, `Thigh Leg Knee` -> leg, side by the joint's R/L). `appearance.regions.<region>`, `overlay` and the region features (`eyes brow scar hair beard mouth blush bangs` on the head, `emblem trim plates ...` on `torso`, `hands` / `feet`) target those regions. `region: "<one of the six>"` forces a group into one; `region: "private"` makes the group a region of its own (`box:<group>`), exactly like the overlay boxes of a shape: default features skip it and only a feature that names it (`box:<group>`, `part:<group>`) paints it.
- Frames. Eyes, brow, emblem, trim, gradient, `hands` and `feet` are positioned inside the bounding box of their region (the head front: x across, y up the head box, in the front-facing texels). All NON-private groups of a region share one frame. Put everything that is not the body itself into `private` groups: hair shells, hats, crowns, horns, wings, capes, staffs, floating ornaments (the frame of a head with a halo would be twice as tall and the eyes would land on the forehead). `validate` warns `mesh_region_frame_wide` for a group that is much larger than the stock region and would distort the frame. A sculpted head is fine: the eyes are painted where the texel normals face front (-z) inside the head box, whatever the topology.
- Targeting groups: `box:<glob>` and `part:<glob>` both select GROUPS by name (`"region": "part:robe"`, `"regions": ["box:wing_*"]`), also for `reference.bake.regions` / `exclude`; an unknown name is `unknown_box`. Texels carry the island they belong to, so materials that shade along a UV rectangle (`silk_feather`) follow the island.
- Measurements, printed by `skin` under `mesh` (and by `shape` for the group list): `groups.<name>`: `triangles`, `surface_blocks2`, `texels_per_block` (area-weighted UV density), `uv_coverage` (share of the atlas), `region` (`derived` / the forced one / `private`), `derived_regions` (triangles per region), `bbox`, `style`; top level: `vertices`, `triangles`, `max_influences`, `height`, `regions`, `texture.uv_coverage`, `texture.uv_overlap`. The scale of the entity is `size.height / height` where `height` is the top of the mesh (feet on y=0, `mesh_not_on_ground` otherwise).
- Warnings: `mesh_uv_outside` (UVs outside 0..1: the texture only exists inside the unit square, the rest of those triangles stays empty), `mesh_low_density` (a group has fewer than 8 texels per block: enlarge its UV islands or raise `texture.size`), `mesh_uv_overlap` (more than 2 % of the covered texels belong to several triangles: overlapping or mirrored islands are painted from the LAST triangle, the other copy gets the wrong position and features), `mesh_uv_degenerate` (triangles without UV area stay empty), `mesh_not_on_ground`, `mesh_height_mismatch` (stale sidecar), `mesh_left_right_swapped`, `mesh_region_frame_wide`. Errors: `mesh_missing_file`, `mesh_bad_json` (also every `efmodel.check_mesh_json` problem), `mesh_missing_meta`, `mesh_bad_meta`, `mesh_bad_group` (group name outside `[a-z0-9_]{1,64}` or a `mesh.groups` key the bundle does not have), `mesh_and_shape`, `mesh_requires_biped`.
- `add-boss` copies the bundle to `bossforge/models/<boss id>.efmesh.json` / `.meshmeta.json` (and `<stem>.blend` when it sits next to the bundle) and rewrites `mesh.file` of the project copy to `../models/<boss id>.efmesh.json`; the game mesh `assets/<mod>/animmodels/entity/<id>.json` is the bundle's JSON; the mobpatch uses `epicfight:custom` + `humanoid: true` and `scale`. `qa` checks the JSON structure (`qa_bad_mesh`) and that the game file still equals the bundle (`qa_model_stale`); `render-test` asserts Epic Fight reports the bundle's vertex count.
- Cost and limits: the painter is pure Python, about 6 s for 1 200 triangles at 512 and 15 s for 32 000 (preview and closeup included); `fit` rasterizes every triangle's silhouette (30 000 triangles: 0.6 s per view, `--auto-calibrate` about 10 s). The cost is per painted texel (0.07-0.2 ms each on a loaded shared machine: the 110 000 painted texels of the test character took 8 s at 512 with `skin_soft` / `silk` styles and 9 s with the knight defaults; the same texels in a 2048 atlas took 21 s), so a 2048 atlas that is 40 % covered (1.7 million texels) takes several minutes: 512-1024 is the practical size, raise it only for a group that needs the density. `filigree` was designed for box faces: on a sculpted surface it lays its cells out per UV island and face direction, so use it on flat-ish panels (robe fronts, cuffs).
- Measured against the box path: the King Arthur `shape` boss converted with `bundle_from_shape` paints byte-identical textures with `padding: 1` and the same texels (plus the wider padding) with the default; `fit` gives IoU 1.0 in all four views against the box hulls.
