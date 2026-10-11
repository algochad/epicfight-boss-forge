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

- `silhouette`: optional preset name. `proportions`: overrides on the silhouette's. `attachments`: appended after the silhouette's; an attachment whose `id` equals a silhouette attachment REPLACES it. `remove`: ids of silhouette attachments to drop. `parts`: free-form boxes. `texture.density`: texels per block, 8-64 (default 32; 48 gives a crisp face and detail; the atlas is auto-sized to a square power of two up to 512 and the density is reduced with warning `density_reduced` if it does not fit).
- Limits: <= 160 boxes after expansion, every vertex within |x| <= 4, y in [-1, 6], |z| <= 4 blocks (model space, before the mesh scale).
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
| `pauldrons` | Shoulder armor: round, spiked, layered plates, skulls or one huge asymmetric pauldron. | `style` round/spiked/layered/skull/asymmetric; `side` right/left |
| `cape` | Cloak hanging behind the body in rigid segments (short, long or tattered). | `style` short/long/tattered |
| `skirt` | Tabard, robe or kilt hanging from the waist on the thigh bones, so the legs stay free. | `style` tabard/robe/kilt; `trim` (default True) |
| `tail` | Tail trailing behind the pelvis, tilting down and back. | `style` lizard/spiked/whip/club; `length` 0.5-1.6 |
| `wings` | Pair of wings on the upper back; `spread` (10-80 degrees) tilts the main strut away from vertical. | `style` bat/feathered/angel/skeletal/flame; `spread` 10.0-80.0 |
| `orbs` | Floating orbs, shards or blades orbiting the torso (static pose). | `style` orbs/shards/blades; `count` 2-8; `radius` 0.6-2.0 |
| `extra_arms` | Static pair(s) of extra arms on the chest (rigid, no animation). | `pose` forward/splayed/raised; `count` 1-2 |
| `claws` | Claws, talons or blades on both fists. | `style` claws/talons/blades; `count` 2-5 |
| `ribcage` | Exposed ribs over the front of the chest. | `count` 3-6 |
| `core` | Glowing gem or reactor set into the chest. | `style` gem/reactor/crystal |
| `spine_spikes` | Row of spikes, plates or blades down the back. | `style` spikes/plates/blades; `count` 3-9 |
| `shield` | Shield strapped to a forearm: round, kite or tower. | `style` round/kite/tower; `side` left/right |
| `gauntlets` | Oversized armored forearms and fists. | `style` plate/spiked/knuckle |
| `greaves` | Shin armor with knee plates; optionally spiked or with heavy boots. | `style` plate/spiked/sabaton |
| `belt` | Belt with a buckle, hanging plates or a sash. | `style` plain/plated/sash |
| `banner` | Banner on a pole carried on the back. | `style` standard/pennant/sashimono |

Rigid pieces: capes, skirts, tails and wings are static boxes attached to a bone. They move with the body but do not flutter or flap. Skirts and tabards hang from the thigh bones in separate pieces so the legs stay free. Extra arms are static decoration.

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
 "layer": "overlay", "skip_faces": [], "density": 1.0}
```

Bones: `root torso chest head arm_r forearm_r hand_r arm_l forearm_l hand_l thigh_r shin_r thigh_l shin_l`. A box follows its bone rigidly (the EF `Hand_R` joint is the forearm; `hand_*` is the wrist where the weapon sits). Rotation: `rz < 0` tilts the top toward +x, `rx > 0` tilts it toward +z. A box with its own `material`/`color` is painted from that style only; boxes without a style take the region defaults of their bone. Check codes: `bad_bone bad_size bad_taper bad_rotate bad_material bad_color bad_region too_many_boxes out_of_bounds duplicate_box_id unknown_key unknown_silhouette unknown_preset`.

## Texture and appearance for custom shapes

`appearance.regions`, `palette` and `features` work as before on the base body (head front gets eyes/brow/scar/beard, torso gets the emblem/trim, plates and cracks cover the base regions). Attachments and parts are painted from their own materials; target one explicitly with a feature `region: "box:<id>"`. `appearance.overlay` entries are baked into base texels of the matching region and y range (there is no separate overlay layer), and `appearance.texture.scale` is ignored (use `shape.texture.density`).

## Known limits

- Static pose and rigid skinning: limbs crease slightly when they bend, cloth does not move.
- Only the EF biped rig: no quadrupeds, wings that flap, or extra limbs that animate. Anything non-humanoid in rig terms (spider, dragon, ravager) is outside this skill.
- The held weapon sits at the stock hand position; huge `fist` values make the weapon look small.
