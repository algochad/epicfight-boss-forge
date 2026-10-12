# Shape forge: custom boss silhouettes

A boss with a `shape` block gets its OWN mesh instead of the stock Epic Fight biped: different proportions, horns, wings, cape, tail, crown, pauldrons, extra arms, and free-form boxes. The generator emits a custom EF mesh JSON (`assets/<mod>/animmodels/entity/<id>.json`) skinned to the stock EF biped armature, so every EF biped animation (walk, attacks, stun, death) and every weapon moveset still works. No EF mesh download is needed. The texture is painted onto the generated mesh atlas by the same skin painter.

Use `shape` for any humanoid boss. Without a `shape` block the stock biped (or `iron_golem`) mesh is used; do that only when the user asks for the vanilla look.

## How to design a boss shape from a prompt

1. **Pick the silhouette** closest to the concept (table below). `bossforge list silhouettes` shows them all. A silhouette is a starting point: proportions + attachments.
2. **Define the character with 2-3 proportion overrides**: tiny head + giant fists (brute), gaunt and thin (skeletal), broad and armored (warlord). Boss silhouettes work because they are exaggerated.
3. **Add 2-4 signature pieces** with `attachments` (crown, horns, wings, tail, cape, pauldrons, shield...). More than ~6 pieces turns to noise. Remove silhouette pieces you do not want with `remove`.
4. **Add free-form `parts` for what makes this boss unique**: one oversized skull pauldron, a chained lantern, a rune slab on the back, a spear through the shoulder. Boxes are cheap and precise (see below).
5. **Paint it** with `appearance` (palette, regions, features). Attachments take palette keys, so changing the palette recolors the whole boss.
6. `bossforge shape boss.json --out DIR` prints box count, bounds, atlas and warnings; `bossforge skin boss.json --out DIR` paints and renders the preview (front, three-quarter, side, back, texture). READ it, judge the silhouette from the side and back as well, iterate.

If the user's description does not say what the body looks like (build, head gear, back piece, tail, arms, asymmetry, glow), ask for it in the intake batch instead of guessing: "What silhouette: hulking brute, armored knight, gaunt king, winged fiend, hooded caster, beast, insect? Signature pieces (crown, horns, wings, cape, tail, oversized weapon or arm)? Anything asymmetric?"

Design principles taken from boss mods (L_Ender's Cataclysm, Soulslike Weaponry, Bosses of Mass Destruction): one readable silhouette from far away (2-3 big masses), exaggerated proportions, a contrasting glow accent (eyes, core, gem, runes) in the boss's accent color, layered armor with trim, a signature crown/horns/wings/cape, and asymmetry where it helps.

## shape block

```json
"shape": {
  "rig": "biped",
  "silhouette": "knight",
  "proportions": {"head": 1.2, "shoulders": 1.15, "fist": 1.2},
  "remove": ["helm"],
  "attachments": [
    {"id": "crown", "preset": "crown", "style": "spiked", "color": "accent", "gem": true},
    {"id": "cape", "preset": "cape", "style": "long", "color": "secondary"}
  ],
  "parts": [
    {"id": "rune_slab", "bone": "chest", "min": [-0.2, 1.15, 0.14], "size": [0.4, 0.5, 0.06],
     "material": "stone", "color": "primary"}
  ],
  "texture": {"density": 48}
}
```

- `silhouette`: optional preset name. `proportions`: overrides on the silhouette's. `attachments`: appended after the silhouette's; an attachment whose `id` equals a silhouette attachment REPLACES it. `remove`: ids of silhouette attachments to drop. `parts`: free-form boxes. `texture.density`: texels per block, 8-64 (default 32; 48 gives a crisp face and detail). `texture.atlas`: 512 (default) or 1024, the largest atlas that may be used: the atlas is auto-sized to the smallest square power of two (16..`atlas`) that holds every face rect at the requested density, so a small model stays small even with `atlas` 1024; the density is halved (down to 8) with warning `density_reduced` only when even the `atlas`-sized square does not fit, and `texture_too_large` is raised when 8 does not fit either. A 1024 atlas is 4x the area (dense relief parts and baked reference textures need it) and the texture PNG gets 4x the pixels; leave `atlas` unset for specs that fit 512.
- Limits: <= 400 boxes after expansion (`shape.MAX_BOXES`; one stock preset adds at most 120 of them, `seraph` wings use 104, a `relief` attachment up to 386), every vertex within |x| <= 4, y in [-1, 6], |z| <= 4 blocks (model space, before the mesh scale). Many thin boxes are cheap in the atlas, but each one costs a few hundred texels at `texture.density` 48: if `density_reduced` appears, set `texture.atlas` 1024, lower `density` or remove small parts. A whole angel (seraph wings + slit skirt with hem layer + hairdo + ribbons + pouch + boots + pauldrons on the `caster` silhouette) is about 235 boxes: `bossforge shape` prints `boxes`, `box_counts` (boxes per owner: `base`, every attachment id, `parts`), `max_boxes`, `atlas`, `atlas_fill` (share of the atlas covered by face rects) and one `relief` entry per relief attachment, so you see how much of the budget each piece uses before adding more.
- `size.height` is the TOTAL height (crown and horn tips included): the mesh scale is `size.height / model height`. `size.width` is only the hitbox width; wide wings or pauldrons may stick out of it.

## Silhouettes

| Silhouette | Archetype | Look | Proportions that define it | Attachments |
|---|---|---|---|---|
| `plain` | knight | The stock humanoid proportions with no extra parts; start here and add `proportions` / `attachments` yourself. | stock | none |
| `knight` | knight | Armored champion: slightly broad, plumed helm, layered pauldrons, plate gauntlets and greaves, tassets and a long cloak. | head 0.95, shoulders 1.2, torso_width 1.1, arm_thickness 1.15, leg_thickness 1.1, fist 1.2, bulk 1.05 | helm (plumed), pauldrons (layered), gauntlets (plate), greaves (plate), belt (plated), cape (long) |
| `warlord` | knight | Hulking war chief: small horned helm on huge shoulders, one oversized spiked pauldron, spiked gauntlets, kilt and a banner on the back. | head 0.9, shoulders 1.45, torso_width 1.25, torso_depth 1.2, torso_height 1.1, arm_thickness 1.4, fist 1.5, leg_thickness 1.25, bulk 1.1 | helm (horned), pauldrons (asymmetric), gauntlets (spiked), greaves (spiked), skirt (kilt), banner (standard) |
| `brute` | beast | Tiny head, enormous shoulders, arms and fists: underbite jaw with tusks, spine spikes and a plain belt. | head 0.8, shoulders 1.5, torso_width 1.4, torso_depth 1.3, torso_height 1.1, arm_thickness 1.7, arm_length 1.15, fist 2.2, leg_thickness 1.3 | jaw (underbite), spine_spikes (spikes), belt (plain) |
| `colossus` | golem | Towering construct: stout legs, tall barrel torso, giant round pauldrons, plated fists and shins, glowing reactor core. | head 0.95, shoulders 1.35, torso_width 1.5, torso_height 1.3, arm_thickness 1.5, fist 1.8, leg_thickness 1.8 | pauldrons (round), core (reactor), gauntlets (plate), greaves (plate), belt (plain) |
| `caster` | cultist | Slim robed sorcerer: soft hood, long tattered cloak, robe skirt, a halo above the head and orbiting orbs. | head 1.0, shoulders 0.85, torso_width 0.8, torso_depth 0.9, arm_thickness 0.7, fist 0.8, leg_thickness 0.7, bulk 0.9 | hood (soft), cape (tattered), skirt (robe), halo (ring), orbs (orbs) |
| `beast` | beast | Hunched predator: big head with a lion mane and tusks, long arms with blade claws, thick tail. | head 1.2, shoulders 1.35, torso_width 1.2, torso_depth 1.15, arm_thickness 1.4, arm_length 1.25, fist 1.5, leg_thickness 1.1 | mane (lion), tusks (boar), claws (blades), tail (lizard) |
| `fiend` | demon | Winged demon: swept-back horns, bat wings, spiked tail, claws and fangs. | head 1.1, shoulders 1.25, torso_width 1.1, arm_thickness 1.2, fist 1.4 | horns (demon), wings (bat), tail (spiked), claws (claws), fangs (fangs) |
| `skeletal_king` | undead | Gaunt undead monarch: thin frame, exposed ribcage, spiked crown, skull pauldrons and a tattered cloak. | head 1.1, shoulders 0.95, torso_width 0.75, torso_depth 0.8, torso_height 1.1, arm_thickness 0.6, fist 0.9, leg_thickness 0.6, bulk 0.9 | ribcage, crown (spiked), pauldrons (skull), cape (tattered) |
| `wraith` | wraith | Hooded specter: very long thin arms with talons, pointed hood, tattered cloak and a floor-length tabard, floating shards. | head 1.0, shoulders 0.8, torso_width 0.75, torso_depth 0.8, arm_thickness 0.55, arm_length 1.35, fist 0.8, leg_thickness 0.5, bulk 0.9 | hood (pointed), cape (tattered), skirt (tabard), claws (talons), orbs (shards) |
| `insectoid` | beast | Mantis-like horror: thin limbs, two extra arm pairs, mandibles, antennae, back plates and blade claws. | head 0.9, shoulders 0.9, torso_width 0.75, torso_depth 0.9, torso_height 1.1, arm_thickness 0.6, arm_length 1.25, fist 0.9, leg_thickness 0.6 | extra_arms, fangs (mandibles), horns (straight), spine_spikes (plates), claws (blades) |

## Proportions

Multipliers, default 1.0. The skeleton joints (shoulders, elbows, knees, neck) stay fixed, so lengths only change a little; thickness and size change freely.

| Proportion | Range | Effect |
|---|---|---|
| `head` | 0.6-2.2 | head cube edge (grows upward) |
| `shoulders` | 0.7-2.0 | chest width |
| `torso_width` | 0.6-2.0 | torso width |
| `torso_depth` | 0.6-2.0 | torso depth |
| `torso_height` | 0.8-1.4 | chest/abdomen height |
| `arm_thickness` | 0.5-2.5 | arm thickness |
| `arm_length` | 0.8-1.4 | arm length (joints fixed, keep modest) |
| `fist` | 0.6-3.0 | hand/fist size at the wrist |
| `leg_thickness` | 0.5-2.5 | leg thickness |
| `bulk` | 0.6-2.0 | extra multiplier on torso, arm and leg widths |

## Attachments (presets)

Every preset also accepts `id` (default = preset name), `scale` (0.5-2.5), `color`, `accent`, `material` (palette keys or `#rrggbb`; materials from `bossforge list materials`). Presets are positioned on the proportioned body, so a bigger head carries its crown and horns with it. `bossforge list presets` prints the same data as JSON.

| Preset | What it adds | Parameters |
|---|---|---|
| `crown` | Crown or circlet around the top of the head. | `style` band/spiked/tall/broken; `gem` (default True) |
| `horns` | Pair of horns, antlers or a ram's curl growing from the head. | `style` curved/straight/ram/antlers/demon/bull; `length` 0.5-2.0 |
| `helm` | Helmet over the head: bucket, nasal visor, horned, plumed or great helm. | `style` bucket/visor/horned/plumed/great |
| `hood` | Cloth hood around the head with an optional shoulder cowl. | `style` soft/pointed; `cowl` (default True) |
| `mane` | Spiky ridge or shaggy mane over the head and the upper back. | `style` spiky/lion |
| `tusks` | Tusks rising from or hanging below the mouth. | `style` boar/curved/walrus |
| `fangs` | Fangs or mandibles at the mouth. | `style` fangs/mandibles/saber |
| `jaw` | Heavy underbite jaw with tusks for brutes, or a gaping maw. | `style` underbite/maw |
| `halo` | Floating ring, orbs or sunburst above the head. | `style` ring/orbs/sunburst |
| `pauldrons` | Shoulder armor: round, spiked, layered plates, skulls or one huge asymmetric pauldron. `layered` covers the top 42 % of the upper arm at `scale` 1 (34 % at 0.8, 27 % at 0.6), so the sleeve shows below; use `scale` 0.75 for a slim cap. | `style` round/spiked/layered/skull/asymmetric; `side` right/left |
| `cape` | Cloak hanging behind the body in rigid segments (short, long or tattered). | `style` short/long/tattered |
| `skirt` | Tabard, robe or kilt hanging from the waist on the thigh bones, so the legs stay free. `slit` cuts two slits into the front, one on each side of a centre front panel, from the hem trim up to the thigh (about 60 % of the skirt height), so the legs show in them; `panel` adds the centre panel (cream `#f2ead3`, 1 cm proud, ids `fp*`) without slits; `hem` adds a second, lighter and longer layer (`hem_color`, material `lace`, ids `h[fbs]<n>_*`) under the hem trim with its own thin trim at the lower edge (ids `h[fbs]t<n>_*`). The two hem trims run across the whole width (also under the slits), so the front panels meet at the hem. `material` reaches every box (trim, hem layer and centre panel too). `arm_clear` keeps the skirt and its belt band inside the arms' inner edge down to the hands and flares only below them (side panels start where the skirt has flared past the thighs). | `style` tabard/robe/kilt; `trim` (default True); `slit` (default False); `panel` (default False; implied by `slit`); `hem` (default False); `hem_color` (default `#e8eefa`); `arm_clear` (default False, see "Arms stay visible") |
| `tail` | Tail trailing behind the pelvis, tilting down and back. | `style` lizard/spiked/whip/club; `length` 0.5-1.6 |
| `wings` | Pair of wings on the upper back; `spread` (10-80 degrees) tilts the main strut away from vertical. `seraph` = raised angel wings as ONE dense fan-shaped sail (see "Seraph wings" below): a thick stair-stepped leading edge beside the head and 9 layered rows of broad overlapping feathers fanning out and down, white at the top, pale lavender, light violet tips; `color` white, `accent` lavender, `tip` violet. | `style` bat/feathered/angel/seraph/skeletal/flame; `spread` 10.0-80.0 (use ~30 for `seraph`); `tip` colour (default `#aa98f2`; derived from `accent` when `accent` is a hex colour and `tip` is not set) |
| `orbs` | Floating orbs, shards or blades orbiting the torso (static pose). | `style` orbs/shards/blades; `count` 2-8; `radius` 0.6-2.0 |
| `extra_arms` | Static pair(s) of extra arms on the chest (rigid, no animation). | `pose` forward/splayed/raised; `count` 1-2 |
| `claws` | Claws, talons or blades on both fists. | `style` claws/talons/blades; `count` 2-5 |
| `ribcage` | Exposed ribs over the front of the chest. | `count` 3-6 |
| `core` | Glowing gem or reactor set into the chest. | `style` gem/reactor/crystal |
| `spine_spikes` | Row of spikes, plates or blades down the back. | `style` spikes/plates/blades; `count` 3-9 |
| `shield` | Shield strapped to a forearm: round, kite or tower. | `style` round/kite/tower; `side` left/right |
| `gauntlets` | Oversized armored forearms and fists. | `style` plate/spiked/knuckle |
| `greaves` | Shin armor with knee plates; optionally spiked or with heavy boots. `cuff` adds a band around the top of the shin in `cuff_color` (for `sabaton` it replaces the knee plate: smooth boots with a cuff), `sole` a light sole under the `sabaton` boot. `material` reaches every box: use `silk` or `leather` for soft boots (the default `plate_steel` shows seams and rivets). | `style` plate/spiked/sabaton; `cuff` (default False); `cuff_color` (default `secondary`); `sole` (default False) |
| `belt` | Belt with a buckle, hanging plates or a sash. | `style` plain/plated/sash |
| `banner` | Banner on a pole carried on the back. | `style` standard/pennant/sashimono |
| `hairdo` | Hair volume on the head in the `hair` palette colour (material `hair_soft`): a thin cap over the top and the upper back with the face left open, a bun, twin buns, ponytail or braid, centre-parted bangs ending above the brows, side locks beside the face down past the chin to the chest, a narrow tapering curtain behind the head down to the shoulder blades (never wider than the head), optionally white-blue ribbon loops behind the head and teal/gold leaf pins at the front sides. `accent` recolours the ribbon loops. | `style` bun/twin_buns/ponytail/braid; `bangs` (default True); `locks` (default True); `ribbon_loops` (default False); `pins` (default False) |
| `ribbons` | Long thin ribbons drifting in 2-3 tilted, rigid segments: pairs from the wrists (past the hem), the hem sides, or the back of the head. White (`color`) and pale blue (`accent`) strips. Part of a ribbon that would sink below the floor trails along it. | `style` wrist/hem/back/all; `length` 0.5-2.0 |
| `pouch` | Belt pouch on one hip (character's own side) with small glass vials on top and an optional thin belt around the waist. | `side` right/left; `vials` 0-4; `colors` list of 1-4 vial colours (palette keys or `#rrggbb`, cycled); `belt` (default True); `arm_clear` (default False): squeezes the pouch and the belt between the centre line and the arm's inner edge (a small pouch of about 0.09 next to the centre panel) so it never hides the hand |
| `relief` | Boxes CUT OUT OF THE REFERENCE SILHOUETTES (needs the spec's `reference` block): stair-stepped wings, hair masses, hems, ornaments that no preset can express. Details and every parameter: "Relief parts" below. No `scale` parameter (the size comes from the art's calibration). | `mode` extrude/hull; `view`; `rect`; `views`; `bone`; `cell`; `thickness`; `z`; `layers`; `layer_step`; `max_boxes`; `mirror`; `edge_texels`; `ref_color`; `rotate`; `pivot` |

Rigid pieces: capes, skirts, tails and wings are static boxes attached to a bone. They move with the body but do not flutter or flap. Skirts and tabards hang from the thigh bones in separate pieces so the legs stay free. Extra arms are static decoration.

### Seraph wings, hair, ribbons and pouch (angel / healer look)

- `seraph` wings: the leading edge is 7 stacked axis-aligned blocks (stair-stepped silhouette) running from the shoulder blade out to the side of the head, then up and out at `spread` degrees from vertical. At `spread` 30 each wing reaches about 1.5 blocks out from the centre line, the top is about 0.45 blocks above the stock head top (2.0) and the longest feathers hang to about 0.6 (between the hip at 0.76 and the knee at 0.39); `spread` 15 gives a narrower 1.05, `spread` 45 a very wide 1.9 (scale 2.5 with spread >= 45 leaves the model limits: `out_of_bounds`). Nine rows of 5 feathers hang from the edge, layered in depth (front and back rows short, middle row longest), staggered by half a feather so no gap stays open; inner feathers hang straight, the outer ones splay outwards (up to 26 degrees) and lengths and angles vary for a jagged lower edge. The row index drives a 3-stop colour gradient: the two outer rows (seen from the front and from the back) take `color`, the middle rows blend through `accent`, the longest middle row takes `tip`; colours blend only when `color`/`accent`/`tip` are `#rrggbb` (a palette key snaps to the nearest stop instead). Neighbouring feathers differ slightly in tone. For a smoother, stronger fade add a painter `gradient` feature over all feathers: `{"type":"gradient","regions":["box:wings_f*"],"from":"#a99af0","lo":0.0,"hi":0.6}` (lilac at the lower tips, fading into the preset's own colours higher up). Materials: feathers `silk_feather`, leading edge `silk`; a `material` override reaches every box. Box ids: `st<i>_r/_l` leading-edge blocks, `f<row>_<j>_0_r/_l` feathers (`wings_f*` targets all of them).
- The wings add ~0.45 blocks above the head, and `size.height` is the total model height: the body shrinks accordingly. Raise `size.height` (about +15%) or lower `spread`/`scale` to keep the body size you intended.
- `hairdo` never covers the face front: boxes stay out of the eye/brow area (bangs end at about a quarter of the head height, pins sit above that, locks stay beside the head). The cap, the curtain (`cu*`) and the bun stay within a few cm of the head's width. Everything follows the head proportion. The `hair` palette colour comes from the appearance palette, so recolour the hair there. Box ids: `top`, `back`, `side_r/_l`, `bang_r/_l`, `lk*` side locks, `cu*` curtain, `bn*` bun (band `bnb*`), `loop_*` ribbon loops, `leaf_*`/`pin_*` pins.
- `ribbons` and `pouch` hang on the bones they name (`hand_*` for wrist ribbons, `shin_*` for hem ribbons, `head` for back ribbons, `torso` for the pouch), so they move with the body. `pouch` is the only preset that is intentionally one-sided.

### Arms stay visible (`arm_clear`)

The base arms are boxes on `arm_*` / `forearm_*` / `hand_*` (EF joints `Arm_R` 11, `Hand_R` 12, `Tool_R` 13 and the left ones), so they move with the EF animations. They sit flush against the chest (their inner edge equals the chest edge), and the stock fists hang at the belt line, so a skirt, belt or pouch that is wider than the chest covers the cuffs and hands and clips through a swinging arm. Set `arm_clear: true` on `skirt` and `pouch` for a long-sleeved or hand-visible character:

- `skirt` (all styles): above the hands' height (about 0.08 above the fists' lower edge, 0.68 on the stock body, 0.69 with the Nightingale proportions) the front and back panels, the centre panel and the belt band stay inside the arms' inner edge minus 1 cm; below it the skirt flares in a straight A-line to the hem. The side panels start where the skirt has flared wide enough to clear the thighs, so none is in the hand zone. With `slit` the slits open at the flare start. Ids stay the same (`fr0/fr1`, `fp*`, `sd*`, `h[fbs]*`).
- `pouch`: narrower (about 0.09 at scale 1) and moved inboard, its belt no wider than the chest.
- Sleeves and torso share the chest edge; to tell them apart use different `regions.arms` / `regions.torso` colours, a `trim` cuff, or a `gradient`/`stripes` feature, not geometry.

Known clipping when an arm swings (checked with the arm turned 10-60 degrees forward and 20-60 backward about the shoulder joint, caster proportions): with `arm_clear` the skirt and the pouch never touch the arm. Remaining unavoidable overlaps: `wings` (all styles; chest bone, behind the body) are crossed by a backward swing of 20 degrees or more and by the shoulder top at a forward swing of 45 degrees or more; the head-back `ribbons` (style `back`/`all`) by a 60-degree backward swing; `cape` (chest/torso) by a backward swing; `hood` / `mane` / `helm` and `extra_arms` sit on the shoulders by design. Presets on the arm bones (`pauldrons`, `gauntlets`, `claws`, `shield`, wrist `ribbons`) move with the arm and never clip it.

## Free-form boxes (`parts`)

Coordinates are model space in blocks in the biped bind pose: y up, feet at y=0, x=0 on the centerline, the character's RIGHT is +x, the character FACES -z. Anatomy: knee y 0.386, hip 0.761, waist 0.814, chest 1.114, shoulder 1.394 (x = +-0.375), elbow 1.094, wrist 0.822, neck 1.514, top of the stock head 2.0; legs at x = +-0.125; torso half width 0.25; head cube 0.5.

```
{"id": "pauldron_r", "bone": "arm_r",
 "min": [x, y, z], "size": [w, h, d],       # lowest corner and extent of the unrotated box
 "taper": [sx, sz],                          # scale of the TOP face (0.05-3.0): frustums for horns, spikes, skirts
 "rotate": [rx, ry, rz], "pivot": [x, y, z], # degrees; R = Rz*Ry*Rx (X first); pivot defaults to the box center
 "mirror": true,                             # also emit the x-mirrored copy ("_r"<->"_l", otherwise "<id>_m")
 "material": "plate_steel", "color": "primary", "accent": "accent",
 "region": null,                             # head torso arm_l arm_r leg_l leg_r to receive appearance features
 "layer": "overlay", "skip_faces": [], "density": 1.0,
 "slab": "z", "edge_texels": 1}                # optional: thin slab, see below
```

Bones: `root torso chest head arm_r forearm_r hand_r arm_l forearm_l hand_l thigh_r shin_r thigh_l shin_l`. A box follows its bone rigidly (the EF `Hand_R` joint is the forearm; `hand_*` is the wrist where the weapon sits). Rotation: `rz < 0` tilts the top toward +x, `rx > 0` tilts it toward +z. A box with its own `material`/`color` is painted from that style only; boxes without a style take the region defaults of their bone. Check codes: `bad_bone bad_size bad_taper bad_rotate bad_material bad_color bad_region bad_slab too_many_boxes out_of_bounds duplicate_box_id unknown_key unknown_silhouette unknown_preset`.

`slab` ("x", "y" or "z") marks the box as a thin slab along that LOCAL axis and `edge_texels` (integer 1-8, default 1, only with `slab`) is how many texels the four edge faces get across the thickness: normally every face gets `size * density` texels (at least 2 per side), so a 0.02-thick plate costs two 2-texel-wide strips per edge; with `slab: "z"` the left/right/top/bottom faces become `edge_texels` wide (strips along the silhouette), the big front/back faces keep their resolution. Boxes without `slab` allocate exactly as before. `relief` slabs set it by default (`edge_texels` 1).

## Texture and appearance for custom shapes

`appearance.regions`, `palette` and `features` work as before on the base body (head front gets eyes/brow/scar/beard, torso gets the emblem/trim, plates and cracks cover the base regions). Attachments and parts are painted from their own materials; target one explicitly with a feature `region: "box:<id>"`. `appearance.overlay` entries are baked into base texels of the matching region and y range (there is no separate overlay layer), and `appearance.texture.scale` is ignored (use `shape.texture.density`).

## Known limits

- Static pose and rigid skinning: limbs crease slightly when they bend, cloth does not move.
- Only the EF biped rig: no quadrupeds, wings that flap, or extra limbs that animate. Anything non-humanoid in rig terms (spider, dragon, ravager) is outside this skill.
- The held weapon sits at the stock hand position; huge `fist` values make the weapon look small.

## Relief parts (boxes cut out of the reference silhouettes)

`relief` is an attachment preset that turns a region of a REFERENCE view (concept art / character sheet, see `reference-fit.md` for `reference.views`, calibration and masks) into boxes. Use it for what no preset can express and that is mostly a SILHOUETTE: stair-stepped or feathered wings, hair masses, hems, ribbons, ornaments. Everything else (head, torso, limbs, armour) stays on the stock presets and `proportions`. The spec needs a top-level `reference` block with the view(s) used; `relief` without one fails with `relief_no_reference`.

```json
"reference": {"views": {"front": {"image": "refs/sheet.png", "crop": [0, 66, 462, 525], "ground_y": 516, "center_x": 224.5, "px_per_block": 203.0},
                        "right": {"image": "refs/sheet_b.png", "crop": [456, 66, 600, 525], "ground_y": 512, "center_x": 522, "px_per_block": 203.0}}},
"shape": {"silhouette": "caster", "remove": ["skirt"], "attachments": [
  {"id": "wing", "preset": "relief", "mode": "extrude", "view": "front", "rect": [0, 66, 188, 400], "bone": "chest",
   "mirror": true, "cell": 0.02, "thickness": 0.05, "z": 0.2, "max_boxes": 140, "material": "silk"},
  {"id": "hair", "preset": "relief", "mode": "hull", "views": {"front": [185, 72, 265, 180], "right": [455, 72, 533, 180]},
   "bone": "head", "mirror": true, "cell": 0.02, "max_boxes": 30, "material": "hair_soft"},
  {"id": "hem", "preset": "relief", "mode": "hull", "views": {"front": [148, 345, 305, 445], "right": [470, 345, 600, 445]},
   "bone": "thigh_r", "mirror": true, "cell": 0.02, "max_boxes": 40, "material": "silk"},
  {"id": "rib", "preset": "relief", "mode": "extrude", "view": "front", "rect": [90, 345, 182, 495], "bone": "torso",
   "mirror": true, "cell": 0.03, "thickness": 0.03, "z": 0.0, "max_boxes": 50, "material": "silk"}]}
```

All pixel coordinates (`rect`, `views`) are ORIGINAL image pixels (not crop-relative); the part is cut from the view's foreground mask (`reference.views.<view>.mask`), so the mask decides what is "character": draw the `rect` around the part, then look at the mask in `reference-info` and at `relief[]` of `bossforge shape` (below). The in-plane size comes only from the view's `px_per_block`, `ground_y`, `center_x` (model blocks, before the entity scale): calibrate to the BODY (feet at y = 0, the head cube 0.5 blocks) and wings, hair and hem come out at the right size relative to the body.

### Parameters (attachment keys)

| Key | Range / values | Default | Meaning |
|---|---|---|---|
| `mode` | `extrude`, `hull` | `extrude` | one view extruded into slabs / two-view visual hull |
| `view` | `front`, `back`, `right`, `left` | `front` | extrude: the view the part is cut from |
| `rect` | `[x0, y0, x1, y1]`, x0 < x1, y0 < y1, inside the image | required for `extrude` | extrude: pixel rectangle of the part (only the mask inside counts) |
| `views` | `{"front"\|"back": rect, "right"\|"left": rect}`, exactly one of each pair | required for `hull` | hull: front/back rect gives the x intervals per band, right/left rect the z intervals |
| `bone` | `root torso chest head arm_r forearm_r hand_r arm_l forearm_l hand_l thigh_r shin_r thigh_l shin_l` | `chest` | rigid binding; with `mirror` the mirrored copies use the `_r` <-> `_l` partner bone (a part on `thigh_r` becomes `thigh_r` + `thigh_l`) |
| `cell` | 0.02 - 1.0 blocks | 0.08 | grid cell edge in the view plane; must be >= 2 reference pixels (`relief_cell_too_small`); smaller = more exact, more boxes |
| `thickness` | 0.01 - 2.0 | 0.06 | extrude: slab depth (z extent for front/back views, x extent for right/left) |
| `z` | -4.0 - 4.0 | 0.0 | extrude: depth CENTRE of the slab (model z for front/back, model x for right/left; the character faces -z, so z > 0 is behind the chest) |
| `layers`, `layer_step` | 1-6, 0.0-1.0 | 1, 0.0 | extrude: `layers` copies of every slab stacked along the depth axis, `layer_step` apart (0 = `thickness`, touching) |
| `max_boxes` | 1 - 386 | 120 | box budget of THIS attachment after mirroring and layers; `cell` grows by 5 % steps until it fits (warning `relief_coarsened` with the cell used); `relief_max_boxes` if no coarser cell reaches it without losing the shape |
| `mirror` | bool | false | extrude (front/back views only; `relief_bad_mirror` for right/left): mesh only the character's RIGHT half (x >= 0, the image-left half of a front view) and mirror it; hull: same for x. `false` uses the whole rect (asymmetric parts) |
| `align` | bool | true | extrude: try the grid shifted by 0, 1/4, 1/2, 3/4 of a cell in both directions and keep the shift with the highest IoU among those that fit `max_boxes` (stair-stepped art gains 2-4 IoU points at the same box count); `false` = grid lines at multiples of `cell` |
| `edge_texels` | 0 - 8 | 1 | extrude: `slab` edge strips, see "Free-form boxes"; 0 = every face gets full texel resolution (use 0 when `thickness` is larger than `cell`: the side faces are then big) |
| `ref_color` | bool | true | colour every box with the mean colour of the foreground pixels under it (palette/#rrggbb strings otherwise from `color`, default `primary`); `material`/`color`/`accent` as for every preset; a bake overwrites the colours anyway |
| `rotate`, `pivot` | `[rx, ry, rz]` degrees, `[x, y, z]` | 0, bbox centre | turn the whole part about ONE pivot (every box gets the same `rotate`/`pivot`; no clipping to the model limits is done for rotated parts, the usual `out_of_bounds` check applies) |

There is no `scale`: the size comes from the calibration. Ids: `<id>_s<n>` (extrude; `_r`/`_l` with `mirror`, `_c` for a single box that spans the centre line, `l<k>s<n>` with layers) and `<id>_h<n>` (hull).

Codes (paths point into the attachment, e.g. `/shape/attachments/0/rect`): errors `relief_no_reference` (no `reference.views` loaded), `relief_unknown_view` (view not defined in the reference), `relief_bad_rect` (empty, outside the image, `rect` missing for extrude, no common height of the hull rects), `relief_bad_views` (hull needs exactly one of front/back and one of right/left), `relief_bad_mirror` (mirror on a right/left view), `relief_cell_too_small` (cell under 2 reference pixels), `relief_empty` (no foreground cell), `relief_max_boxes`, `relief_out_of_bounds`; parameter checks use `bad_type`, `out_of_range`, `unknown_value` like every preset; warning `relief_coarsened`.

### How the boxes are made

- extrude: the mask inside `rect` is sampled on a grid of `cell` blocks in the view plane (u across = x for front/back and z for right/left, v = y). A cell is foreground when at least half of it is (area-weighted, only the part inside `rect` counts); the foreground cells are merged into rectangles by greedy meshing (row-first and column-first, the smaller result wins), one slab box per rectangle with `thickness` along the view axis at `z`. The rectangles tile the foreground cells exactly (no overlap, no gap); they are clipped to `rect` (and to x >= 0 with `mirror`). A rectangle that touches the centre line becomes ONE centred box (`_c`) unless the bone is a `_r`/`_l` one or the part is rotated. Faces hidden by a neighbouring rectangle are skipped (`skip_faces`) and the thin edge faces are 1-texel strips (`slab`, `edge_texels`), so a relief box costs about `2 * w * h` texels at `texture.density` plus a few strips: the 112-box Nightingale wings plus hair, hem and ribbons (217 boxes in all) fill 26 % of a 512 atlas at density 48.
- hull: for every horizontal band of `cell` blocks (aligned to multiples of `cell`, only where both rects overlap in height) the front mask gives the x intervals and the side mask the z intervals; every (x interval x z interval) pair is one box, bands with identical intervals are merged vertically. It is the visual hull of two silhouettes: exact in both views, but it over-fills concavities that neither view shows (a hair mass behind the face needs a side rect that starts behind the face, otherwise its front face coincides with the head box). Hull parts do not use `thickness`, `z`, `layers`, `align`.
- Both modes check the model limits (|x| <= 4, y in [-1, 6], |z| <= 4) and raise `relief_out_of_bounds` with the offending extent instead of clipping silently (usually a wrong `px_per_block` or `rect`).

### Measure it (`bossforge shape`)

`bossforge shape SPEC` prints `box_counts` (boxes per owner, e.g. `{"base": 14, "wing": 112, "hair": 11, ...}`), `max_boxes`, `atlas`, `atlas_fill` and `relief`: one entry per relief attachment with `cell_requested`, `cell` (used), `phase`, `rects`, `boxes`, and the measured silhouette quality: `iou`, `ref_px`, `model_px`, `missing_px`, `extra_px` between the planar projection of the generated boxes and the reference mask inside the rect, at image resolution (hull: `iou_<view>` per view, `iou` = the lower one). `bossforge fit SPEC --views front,right` (see `reference-fit.md`) gives the same comparison for the WHOLE model with overlay PNGs. Read both after every change; the numbers, not the preview, decide whether a part is right.

Measured on the staged Nightingale sheets (calibration 203 px per block, `skills/epicfight-boss-forge/assets/examples/nightingale` body, wings replaced by `relief`):

| Part | Settings | Boxes | IoU in its rect |
|---|---|---|---|
| wings (one half, mirrored) | cell 0.03 / 0.025 / 0.02 | 72 / 88 / 112 (both wings) | 0.961 / 0.967 / 0.974 |
| hair mass (hull, back of the head) | cell 0.02 | 11 | front 0.949, side 0.928 |
| skirt hem region (hull) | cell 0.02 | 28 | front 0.913, side 0.936 |
| hem ribbons (extrude) | cell 0.03 | 26 | 0.843 |
| whole model, front view (`bossforge fit`) | stock `seraph` wings + presets -> the four relief parts | 235 -> 217 | 0.568 -> 0.919 |
| whole model, right view | same | | 0.458 -> 0.734 |

What is NOT exact: the wing silhouette is a stair-stepped approximation (IoU 0.974 at 112 boxes; the pixel-exact outline of one wing is about 390 row-run rectangles, more than the whole budget), `mirror` copies the right half although the art's two wings differ by about 0.04 blocks (use two non-mirrored attachments for an asymmetric part, twice the boxes), a slab is flat (the depth profile of a wing is invented: only `z` and `thickness`), the mask of the view decides the foreground (arms, hair or hands that overlap the rect are extruded too: draw the rect around the part), and the side view of the Nightingale sheet shows no wings at all, so the whole-model right-view IoU cannot reach 1 with wings in place.
