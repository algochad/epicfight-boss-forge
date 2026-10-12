# Reference fit (match a boss to pasted concept art)

When the user pastes concept art or a character sheet, the goal is a boss that matches it as closely as boxes on the Epic Fight biped rig allow. The pipeline has four measured steps; every step prints numbers and writes overlay PNGs you can read, because eyeballing a 3D preview against a picture is unreliable:

1. **Reference views** (`reference.views` in the spec): the image, the crop of the character, a foreground mask, and an exact orthographic calibration (pixels <-> model blocks).
2. **Fit** (`bossforge fit`): rasterises the generated boxes in each view and compares them with the mask: IoU, missing / extra pixels, per 0.2-block band hints, overlay PNGs, optional calibration search.
3. **Bake** (`reference.bake`, see `skin-forge.md` "Reference baking"): paints the atlas from the real reference pixels.
4. **Relief** (preset `relief`, see `shape-forge.md` "Relief parts"): boxes generated from a silhouette for what presets cannot express (stair-stepped wings, hair masses, hems).

Read `shape-forge.md` first (boxes, proportions, attachments); this file is the process.

## Honest limits (tell the user)

- Only ORTHOGRAPHIC views can be projected (front / back / side sheets). A perspective hero shot or 3/4 view cannot be calibrated: use it only to read colours and details, never as a `reference.views` image.
- AI-generated sheets are inconsistent between views: heights differ by 1-3 %, a part visible in one view (a wing) is missing in another, the head is offset from the feet by several pixels. Calibrate each view separately, accept that no model matches every view, and say which view you favoured.
- The result is boxes: no curves, no sub-texel detail, no geometry thinner than a box; silhouettes are matched at the pixel level of the art, never "exactly". A high IoU says the outline agrees, not that colours, shading, or depth (front and back views carry no depth: take z from a side view) are right.
- The mask is an estimate. `auto` removes a uniform or textured wall that is clearly different from the figure; it cannot remove background of the same colours as the figure and keeps see-through slits narrower than 3 px (see section 2).
- Never say "exact". Report the measured IoU per view, the largest remaining hints, and what is not reproduced.

## 0. Stage the image

The reader takes PNG only (8/16-bit, any colour type, interlaced or not). Convert everything else with ImageMagick and keep ONE working PNG per source (all pixel coordinates in the spec refer to that file):

```
file pasted.webp                      # what is it really? (a .png name may hold WebP / JPEG data)
magick pasted.webp -alpha on sheet.png    # webp / jpg / gif / tiff / bmp -> png
magick sheet.png -strip sheet.png          # drop colour profiles (optional)
magick sheet.png -resize 50% half.png      # only if > 2048 px: then work in the resized file's coordinates
```

Put the PNG next to the spec (`refs/sheet.png`). Image paths in the spec are relative to the spec file's directory or absolute. A file that is not a PNG gives error `reference_bad_image` with this hint.

## 1. Look at the sheet (`reference-info`)

```
bossforge reference-info refs/sheet.png --grid 50 --out /tmp/sheet_grid.png
bossforge reference-info refs/sheet.png --grid 10 --crop 170,60,290,170 --zoom 6 --out /tmp/head.png
```

The first writes the image with dashed gridlines every 50 px (solid red every 100) and labels = ORIGINAL pixel coordinates (white = x column, yellow = y row), and prints `width`, `height`, `auto_mask_bbox` and `figures`: the connected shapes the auto mask finds on the whole sheet (`bbox` = [x0, y0, x1, y1], `area`), largest first. The figures are your crop candidates: character views, weapon sheets, text labels, scale mannequins. READ the grid image. The second form zooms into a region (`--crop`, integer `--zoom`, labels stay in original pixels) so you can read pixel positions to 1 px: use it for the head, the soles, the centre line.

## 2. Crop and mask

```json
"reference": {"views": {"front": {
  "image": "refs/sheet.png",
  "crop": [0, 66, 462, 525],
  "mask": {"mode": "auto", "keep": [[224, 300]], "outline": 3},
  "ground_y": 516, "center_x": 224.5, "px_per_block": 203.0}}}
```

`crop` = [x0, y0, x1, y1] pixel rectangle around ONE character view (half-open; default the whole image). Everything outside is background. Keep labels ("FRONT"), neighbouring figures and mannequins outside it, or remove them with `keep` / `exclude`.

`mask` (all optional; default `{"mode": "auto", "erode": 1}`):

| key | values | meaning |
|---|---|---|
| `mode` | `auto` (default), `color`, `image`, `polygon` | how the foreground is found |
| `bg` | list of 1-8 `#rrggbb` | `color`: the background colours |
| `tolerance` | 0-255 | `color`: RGB distance to a `bg` colour that still counts as background (default 28). `auto`: margin added to the estimated background colour balls (default 6) |
| `image` | path | `image`: a PNG of the SAME size as the view image; white or opaque = character (alpha is used when the mask image has any transparency, else brightness >= 128) |
| `include` | list of polygons `[[x,y],...]` (>= 3 points, image px) | nothing outside the union of these is foreground. In `polygon` mode the union IS the foreground (a hand-drawn silhouette) |
| `exclude` | list of polygons | forced to background (text, a second figure, a prop you do not want) |
| `keep` | list of `[x,y]` points | keep only the foreground components that contain one of these points: picks one figure out of several that touch each other |
| `outline` | integer 0-8 (default 0) | peel up to N px of DARK pixels (all of R, G, B < 85) off the silhouette edge: removes the black outline and drop shadow of cartoon art without eroding the art itself (set 3 for art with a 1 px black outline) |
| `erode` | integer 0-12 (default 1; 0 in `polygon` mode) | finally shrink the mask by N px so edge pixels blended with the background do not pollute baked colours |
| `seal` | integer 0-3 (default 1) | `auto` / `color`: the background flood may not pass gaps up to 2 x seal px wide in the character's outline; 0 for outline-less art (flat cut-outs, silhouettes) |
| `min_island` | 0-1 (default 0.03) | drop foreground components smaller than this fraction of the largest (stray text, specks) |

Order of operations: base mask (auto / color / image / polygon) -> restrict to `crop` -> `include` -> `exclude` -> `keep` -> `outline` -> `erode`.

How `auto` works (so you know when it fails): it samples a 3 px ring along the crop border, clusters those colours, keeps the dominant cluster plus clusters chained to it within RGB distance 42 (a shaded or brick-textured wall is covered, white feather tips and black outlines touching the border are not), marks every pixel inside those colour balls as background-like, and floods from the border through background-like pixels (not across gaps in the outline). Everything not reached is foreground. A transparent PNG (more than 2 % transparent pixels) uses its alpha channel instead.

Measured on the staged sheets (`sheet_ortho_front_back.png`, grey brick wall, 1 px black outline): the front figure mask (crop `[0,66,462,525]`, `erode 0`) has IoU 0.991 against an independent mask built by flooding the wall up to the black outline, and 0.989 against ImageMagick `-fuzz 14%` flood fills from 40 border seeds (the difference is the outline fringe and the dark gap between the two boots that stays foreground). `sheet_front_side_back.png` separates all five figures on the whole sheet (front, side, back, two weapons) without crops.

Failure modes and the fix:

| symptom | cause | fix |
|---|---|---|
| mask is everything / half the wall | background colour touches the figure's colour, or the crop border lies inside the figure | tighter `crop`; `mode color` with `bg` read from the grid image; `include` polygon |
| figure missing parts of a similar colour as the wall (grey boots on a grey wall) | outline is open there | `seal 2`; or an `include` polygon around the figure plus `mode polygon` |
| second figure / labels attached | they touch | `keep` with a point on the figure; `exclude` polygons; smaller `crop` |
| 2-3 px slits between legs / arm and body stay foreground | enclosed background-coloured area is not distinguishable from grey cloth | `exclude` polygon over the slit |
| edge fringe | outline not peeled | `outline 3`, `erode 1` |

Recipes with ImageMagick for hard images (the mask PNG must have the SAME size as the view image):

```
# flood the wall from many seeds along the crop border (full-image coordinates; crop = [0,66,462,525] here),
# everything the flood did not reach is white = character; 14 % fuzz gave IoU 0.989 against `auto` on the staged sheet,
# 18 % already floods into grey-blue clothes: always read the result
args=(); for x in $(seq 0 23 461); do args+=(-draw "color $x,66 floodfill" -draw "color $x,524 floodfill"); done
for y in $(seq 66 23 524); do args+=(-draw "color 0,$y floodfill" -draw "color 461,$y floodfill"); done
magick sheet.png -alpha off -fuzz 14% -fill "#ff00ff" "${args[@]}" -fill white +opaque "#ff00ff" -fill black -opaque "#ff00ff" refs/mask.png
# flat colour key -> transparent PNG; the `auto` mode then uses the alpha channel
magick sheet.png -fuzz 8% -transparent "#8a8a8a" refs/cut.png
# read a colour:  magick sheet.png -format "%[pixel:p{5,5}]" info:
```

then `"mask": {"mode": "image", "image": "refs/mask.png"}` (add `keep` / `exclude` to cut the neighbours away). For a figure you can outline by eye use `mode polygon` with the points read from the zoomed grid image.

The mask is only trustworthy once you looked at it: `fit` writes `mask_<view>.png` (the cropped reference, the mask contour in red, everything outside the mask darkened). Read it for every view before trusting an IoU.

## 3. Calibrate

Orthographic projection, exact (model space = the generated mesh BEFORE the entity `scale`: y up, feet at y = 0, the character faces -z, its right is +x). `s` = `px_per_block`, `cx` = `center_x`, `gy` = `ground_y`, pixel (i, j) covers [i, i+1) x [j, j+1):

| view | camera is | `px` | the character faces |
|---|---|---|---|
| `front` | in front (at -z) | `cx - s*x` | the viewer (the character's right, +x, is on the image LEFT) |
| `back` | behind (at +z) | `cx + s*x` | away |
| `right` | at the character's right (+x) | `cx - s*z` | image RIGHT |
| `left` | at the character's left (-x) | `cx + s*z` | image LEFT |

and `py = gy - s*y` in all views. So in a side sheet look at where the face and the toes point: toes to the image right = `right`, toes to the image left = `left`. Use each view name once per spec (a second image for the same view cannot be added; pick the better one).

Find the numbers with zoomed grids (`reference-info --crop ... --zoom`):

- `ground_y`: the image row of the soles' lower edge = the model's y = 0 (where the dark outline starts under the boots).
- `center_x`: the centre line of the body: the gap between the feet, the middle of the face; for a side view the middle of the torso (the model's z = 0 is the body centre). Pick the average of the landmarks that agree; AI sheets drift several px between head and feet.
- `px_per_block` (or `top_y` + `top_model_y`: image row of the top of the head and its height in model blocks above the soles; then `px_per_block = (ground_y - top_y) / top_model_y`): measure from a landmark whose model size you know. The base biped at `shape.proportions` 1.0 is 2.0 blocks tall, the head cube 0.5 block (scale it with `proportions.head`), the torso 0.5 x 0.25 blocks (x `torso_width` / `torso_depth`), the arms 0.25 thick; `bossforge shape` prints `height` and `bounds`. Pick the landmark that stays faithful in your model (usually the body from the soles to the top of the head) and divide: e.g. the art's body from the soles to the hair top is 443 px and the model's body is 2.18 blocks tall => 203 px/block. If the art's head is clearly smaller or larger than the model head, choose the scale from the body and fix the head with `proportions.head` (0.6-2.2), do not rescale the whole view.
- Use the SAME `px_per_block` for all views of one character unless the sheet itself is inconsistent (check heights: the top of the head and the soles should give the same blocks in every view).

Check with `fit`: the overlay shows a yellow ground line, a dashed centre line and a labelled tick per block. `bossforge fit SPEC --out DIR --auto-calibrate` searches `px_per_block` (0.6-1.7 x the spec's value), `center_x` and `ground_y` for the highest IoU (coarse stage at 1/4 resolution, then full resolution, <= 600 evaluations, <= 30 s per view; the model's lowest point is held within 2 % of the reference's lowest row) and prints `views_calibration` to paste into the spec plus notes when values moved a lot. It only makes sense when the geometry is already roughly right; if parts of the model are far from the reference (wings, a skirt) the IoU optimum is pulled toward them. `--calibrate-exclude "wings_*,ribbons_*"` leaves boxes whose ids match the globs out of the search (the IoU, hints and overlays still use every box): calibrate on the body, then fix the rest. Nightingale (wings excluded) found 203-216 px/block for front/back/side; the grid measurement gave 203.

## 4. Fit and iterate

```
bossforge fit boss.json --out /tmp/fit [--views front,back] [--auto-calibrate] [--calibrate-exclude "wings_*"]
```

prints one JSON object: per view `iou`, `ref_px`, `model_px`, `missing_px` (reference only), `extra_px` (model only), `bbox_ref` / `bbox_model` in model blocks (`x` or `z` range and `y` range), `calibration`, `hints`, `overlay` (`fit_<view>.png`), `mask_preview` (`mask_<view>.png`); and `mean_iou`. The overlay is the cropped reference darkened: green = both, red = model only, blue = reference only.

`hints` are sentences, most mismatching first: the topmost / bottom / leftmost / rightmost extents ("the character's right (x+) extent: reference x=1.06, model x=1.11") and one per 0.2-block band `y a-b` (reference wider / model sticks out by N blocks on the character's right (x+) / left (x-), or on the front (z-) / back (z+) in side views, with the outermost model box in that band, or "the model has nothing here: add geometry"). Edges are in model blocks: apply them directly.

Loop: read the overlay and the hints, change the spec (the order that pays off):

1. Proportions first (`shape.proportions`: head, shoulders, torso_*, arm_*, leg_thickness, bulk): the base body boxes must land on the reference body. Re-run `fit`, then recalibrate (`--calibrate-exclude` the unfinished parts).
2. Attachment parameters (preset options such as wings `spread`, skirt `scale`) and `remove` for what the art does not have.
3. Free-form `parts` for single missing boxes.
4. Relief boxes (`preset relief`) for large irregular silhouettes: wings, hair masses, hems, capes.
5. Appearance only after the geometry: bake the colours from the reference (below).

Targets: a faithful body view reads IoU >= 0.9 (front/back, wings included), >= 0.85 for a side view. Lower is fine only when you can name the cause (a part the art shows in one view only, perspective, thin ornaments). Stop when the remaining hints are below ~0.05 block or are parts that boxes cannot make.

Staged Nightingale (the shipped example spec, the `views` block below, px_per_block 203): front IoU 0.568, back 0.335, right 0.458 before any geometry change. The front hints say the model's seraph wings reach 0.40 block too far out on the character's left and the whole model is 0.29 block higher than the art (the wings' top); the body itself (head, torso, legs, skirt) lies on the reference. The back sheet draws only ONE wing (the character's left), so 1.2 blocks of model wing on the right are "extra" there; the side sheet draws NO wings, so the model's wings stick out 0.4 block behind the body. Those two views contradict the front sheet: favour the front view for the wings and say so.

## 5. Bake and relief

- `reference.bake` paints the texture atlas from the real reference pixels, texel by texel (visible surface only, best-facing view wins, footprint box-filtered): see `skin-forge.md` "Reference baking". Needs masks and calibrations that pass `fit`; check them with `fit` first.
- `preset relief` generates boxes from a view's silhouette (`mode extrude` for flat shapes such as wings, `mode hull` for bulky shapes from two views): see `shape-forge.md` "Relief parts". Use it where proportion tuning stops paying; re-run `fit` after adding it.

## Spec reference (`reference.views`)

```json
"reference": {
  "views": {
    "front": {"image": "refs/sheet_a.png", "crop": [0, 66, 462, 525], "mask": {"mode": "auto", "keep": [[224, 300]], "outline": 3},
              "ground_y": 516, "center_x": 224.5, "px_per_block": 203.0},
    "back":  {"image": "refs/sheet_a.png", "crop": [462, 66, 748, 525],
              "mask": {"mode": "auto", "keep": [[680, 300]], "outline": 3, "exclude": [[[440, 396], [502, 396], [502, 526], [440, 526]]]},
              "ground_y": 515, "center_x": 686, "px_per_block": 203.0},
    "right": {"image": "refs/sheet_b.png", "crop": [456, 66, 600, 525], "mask": {"mode": "auto", "keep": [[526, 300]], "outline": 3},
              "ground_y": 512, "center_x": 522, "px_per_block": 203.0}
  }
}
```

(`/tmp/nightingale-ref/views.json` has exactly this block with absolute image paths; `views_sheet_b.json` takes front / right / back all from the second sheet.)

| key (per view `front` / `back` / `right` / `left`) | required | range |
|---|---|---|
| `image` | yes | PNG path (relative to the spec file, or absolute) |
| `crop` | no | `[x0, y0, x1, y1]` integers, `0 <= x0 < x1 <= width`, `0 <= y0 < y1 <= height` |
| `mask` | no | see the mask table in section 2 |
| `ground_y`, `center_x` | yes | numbers (image px; `ground_y` within 0..2 x height, `center_x` within -width..2 x width) |
| `px_per_block` | one of the two forms | 4-4096 |
| `top_y` + `top_model_y` | the other form | `top_y` < `ground_y` (image rows), `top_model_y` > 0 blocks |

`reference.bake` belongs to RefBake's section in `skin-forge.md`. Unknown keys are errors.

Error codes (spec validation and loading; `path` points at the key): `reference_bad_image` (missing / not a PNG: convert with `magick`; empty `views`; bad structure), `reference_bad_crop` (also: crop outside the image, checked from the PNG header), `reference_bad_calibration` (missing or out-of-range values, both calibration forms given), `reference_bad_mask` (unknown mode / key, missing `bg` / `image` / `include`, bad polygon or `keep` point, empty mask, size mismatch of the mask image), `reference_unknown_view` (not one of front / back / right / left), `unknown_value` (unknown key in `reference` or in a view). A missing image file is only a warning `reference_image_missing` in `validate` and an error when the views are loaded (`fit`, bake, relief); a file that exists but is not a PNG is an error in `validate` already.

Commands (all print one JSON object):

| command | purpose |
|---|---|
| `bossforge reference-info IMAGE [--grid N=50] [--crop x0,y0,x1,y1] [--zoom Z=1] [--out FILE]` | gridded copy of the image + size + auto mask figures |
| `bossforge fit SPEC --out DIR [--views a,b] [--auto-calibrate] [--calibrate-exclude globs]` | IoU, hints, overlays, optional calibration search |
| `bossforge validate SPEC` | structure check of the `reference` block (files are only warnings) |

Programmatic use (relief, bake): `from bossforge_core import reference; views = reference.views_for(n)` returns `{name: View}` for a normalised spec `n` (cached); `View.project(x, y, z)` -> `(px, py, depth)`, `unproject(px, py)`, `facing(nx, ny, nz)`, `mask_at`, `color_at`, `average(px0, py0, px1, py1)` (area-weighted, foreground only), `mask_cells(u0, v0, u1, v1)` (foreground fraction of a model-space rectangle).
