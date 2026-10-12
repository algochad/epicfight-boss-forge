# Weapon forge

Every boss weapon is a real Forge item the player can obtain, with an Epic Fight capability file so it behaves as an EF weapon (moveset, stun, strikes) in the player's hands and in the boss's.

## Weapon object

`{id, display_name, category, rarity (common|uncommon|rare|epic; rare), damage (1-40), attack_speed (-4.0..0.0), durability (100-5000), ef{impact, armor_negation, max_strikes, damage_bonus?, speed_bonus?}, appearance{blade, guard, grip, accent, gem, glow, motif plain|fuller|runes|serrated, shape, guard_shape, pommel_shape, length 0.8-1.15, width 0.7-1.4, resolution 32|64, rings 0-4, spikes 2-5}, visual_scale 0.4-1.5, hold{lean, side, grip, facing, offset} (staff shapes only), effects[{on:"hit", effect, duration:100, amplifier:0, chance:1.0}], obtain{drop, craft, recipe?}, lore[<=4 lines <=90 chars]}`. Spears take `ef: {one_hand:{...}, two_hand:{...}}`. `resolution`, `rings`, `spikes` and `hold` are optional and only matter for the staff shapes (below).

Appearance defaults: blade `#cfd6e4`, guard = boss accent, grip `#5a3a22`, accent = boss accent, gem = boss eyes, glow false, motif fuller, the category's default shape, guard `bar`, pommel `disc`, length/width 1.0.

## Weapon shapes (unique silhouettes)

| Category | `shape` options (default first) |
|---|---|
| sword | straight, broad, leaf, wavy, serrated, hooked |
| longsword | straight, broad, leaf, wavy, serrated |
| greatsword | broad, cleaver, wavy, serrated |
| tachi | curved, straight, hooked |
| dagger | straight, leaf, wavy, serrated, hooked |
| axe (head) | broad, crescent, double, cleaver |
| spear (head) | leaf, trident, hooked, broad, crystal_staff, orb_staff |

`guard_shape`: bar, winged, curved, ring, ornate, none (axe/spear: only `none` changes anything). `pommel_shape`: disc, gem, spike, ring, none. `length`/`width` stretch the blade. Match the weapon to the boss: a wavy flamberge for a fiend, a winged guard with a gem pommel for a holy king, a cleaver greatsword for a brute. Read `<boss>_weapons.png` before approving.

## Staffs (`category: "spear"`, `shape: "crystal_staff" | "orb_staff"`)

A mage staff uses the spear category on purpose: it gets EF's spear moveset, `epicfight:spear` capability and the `epicfight:item/spear` parent model (gui, ground and frame transforms). Only the sprite differs, and the generator writes its own held transform (see "Held placement") because Epic Fight's spear transform would lay a staff sideways across the body. Colours reuse the normal keys: `blade` = crystal, `guard` = gold, `grip` = shaft bands (crystal_staff) or wood (orb_staff), `gem` = orb, `accent` = glow halo (`glow: true`). `pommel_shape`/`guard_shape`/`motif` do not apply.

| Shape | Look (foot to tip) |
|---|---|
| `crystal_staff` | crystal point, gold foot band, shaft in alternating `grip` blue and aqua bands over pale crystal, gold collar, flared crystal, then a see-through gold ring with silver arcs and bars around a faceted glowing `gem` orb (bright core, highlight pixel, gold struts), and a stepped crystal spire on top |
| `orb_staff` | crystal ferrule, gold band, plain `grip` shaft, gold collar, orb in a gold ring, a fan of crystal shards on top |

- `spikes` (2-5, default 3): spire steps of `crystal_staff`, shards in the fan of `orb_staff`.
- `rings` (0-4, default 2): gold rings on the shaft. 0 = none (collar and foot band turn silver), 1 = collar, 2 = collar + foot band (the sheet look), 3 and 4 add one or two thin rings mid-shaft.
- `length` stretches the shaft and spire, `width` slims or widens every part.
- `resolution` (32 or 64): staffs default to **64**, because the orb, ring and spire are too fine for 32 px (a staff at 32 is still valid and complete, just coarser). Any other shape only offers its category's native size; asking for another value is a validation error (`unknown_value` at `/appearance/resolution`).

Resolution never changes the in-game size: `epicfight:item/spear` and the Forge `separate_transforms` perspectives parent vanilla `item/generated`, which maps the texture onto a fixed 16-unit square whatever its pixel size (EF itself ships a 32 px spear texture next to a 16 px `_gui` one). A 64 px staff gets a 32 px `_gui` texture, so the pair keeps the 2:1 ratio of the other long weapons. Held size is controlled by `visual_scale` only.

Example (the Celestial Mage staff):

```json
{"id": "glowing_staff", "display_name": "Glowing Staff", "category": "spear", "rarity": "epic",
 "appearance": {"shape": "crystal_staff", "blade": "#bfe9ff", "guard": "#f0c850", "grip": "#4aa0e8",
                "accent": "#7fd6ff", "gem": "#2ea8ff", "glow": true, "resolution": 64, "rings": 2, "spikes": 3}}
```

## Held placement and size

### How Epic Fight puts an item in a hand

Epic Fight does not use the vanilla hand placement. For a mob, and for a player in Epic Fight mode, `RenderItemBase.renderItemInHand` (EF 20.14.17, read with `javap`) draws the item with the vanilla item renderer in the display context `THIRD_PERSON_RIGHT_HAND` (main hand; `THIRD_PERSON_LEFT_HAND` and Tool_L for the off hand) under this matrix:

    entity placement and attributes.scale * pose[Tool_R] * T(0,0,-0.13) * Rx(-90) * D * T(-0.5,-0.5,-0.5) * sprite point

- `pose[Tool_R]` is the animated transform of the Tool_R joint, so **where a sprite points in game depends on the animation that is playing** (hold_spear, idle, walk_spear, an attack...). `T(0,0,-0.13) Rx(-90)` is a constant correction EF applies for Tool_R (`GLOBAL_MAINHAND_ITEM_TRANSFORMS`).
- `D` is the model's `thirdperson_righthand` display (translate, then rotate X*Y*Z, then scale), the only part an item model controls. The sprite is the unit quad of `item/generated` centred on (0.5, 0.5, 0.5), 1/16 block thick, so one sprite pixel of a 16 px sprite is 1/16 block whatever the resolution.
- The entity placement includes `attributes.scale`: **the item grows with the wielder**. A display scale is therefore best chosen relative to the wielder, never to a particular boss.
- Epic Fight's own sprites are drawn on the diagonal (butt bottom-left, tip top-right, like vanilla `handheld` tools; `iron_spear.png` is a 32 px diagonal from corner to corner) and each parent model carries a display tuned for that diagonal in the pose of its own category. Our sprites follow the same diagonal (a test asserts it), so nothing about the sprite orientation needs to change; only the display does.
- Epic Fight's spear display (`[0,-90,35]`, translation `[0,2,1.5]`, scale `[2.5,2.5,1]`) in the hold_spear pose that a boss holding a spear plays turns the sprite axis to (0.99, -0.09, -0.09) in the wielder's body space: **sideways across the hip, 3.5 blocks long, tip towards the wielder's right**. That is a lance carried low. A crystal staff drawn on that model looked exactly like this in the game (giant, horizontal, orb on the right) and `render-test` reproduced it. The hand frames are stored in `assets/data/hold-poses.json` (composed from EF's `biped.json` and the animation files; the stored Tool_R origin (0.251, 0.831, 0.420) matches the pose matrix Epic Fight itself reported in a dev client, (0.250, 0.826, 0.415)).

### Staffs: the generated held transform

For staff shapes (`spear` + `crystal_staff` / `orb_staff`) the generator replaces the parent's `thirdperson_righthand` entry with one it solves (`holdpose.staff_display`): the sprite axis points up, the hand grips the shaft, the sprite face looks forward. The solution is exact for the hold_spear pose (the animation a mob with a spear or staff plays; its mobpatch uses `epicfight:biped/living/hold_spear` for idle) and stays near upright in the other poses:

| Pose | Played when | Staff axis from vertical (default `lean` 10) |
|---|---|---|
| hold_spear | a boss holds the staff and idles | 10.0 degrees forward (exact) |
| idle | a player stands with the staff | 13.3 (leans back and slightly inward) |
| bind | arms hanging, the frame EF's hand correction copies from vanilla | 9.5 |
| walk_spear | walking with the staff | 14.7 |
| walk | plain walk | 20.0 |
| run_spear | running or chasing | 62.5 (the staff is carried forward) |

The staff is `visual_scale` times the stock biped's height long (1.8 blocks at entity scale 1, so a boss with `scale` 1.386 holds a 2.5 block staff, exactly as tall as its body). `visual_scale` 1.0 is the default and the automatic shrink for large bosses does not apply to staffs. The gui, ground and frame perspectives keep the parent's transform (an upright diagonal sprite in the inventory); the left-hand and first-person entries are the parent's scaled by `visual_scale` (not solved: see "What is verified").

`hold` knobs (all optional, staff shapes only; any other weapon with a `hold` is rejected with `hold_not_applicable`):

| Key | Default | Meaning |
|---|---|---|
| `lean` | 10 | degrees the top of the staff leans forward (-45..60) |
| `side` | 0 | degrees it leans outward, away from the body (-45..45) |
| `grip` | 0.40 | where the hand holds the staff, as a fraction of its length from the foot (0.1..0.8); 0.40 is the shaft just below the collar |
| `facing` | `front` | `front`: the sprite face looks forward (the weapon sheet's "front weapon" reads from the front); `side`: it looks to the wielder's right |
| `offset` | `[0,0,0]` | shift of the staff in blocks inside the hand frame, each within +-1 |

Tuning loop: `bossforge render-test --project DIR --boss ID`, read `*_hand-front`, `*_hand-side`, `*_hand-3q` and the `player-*` shots, change `lean`/`side`/`grip`/`offset`, regenerate (`add-boss`) and repeat. `bossforge qa` fails with `qa_staff_lies_flat` when a project still carries a staff model generated before this transform existed (or hand-edited so the staff is not where the weapon's `hold` asks).

Example with every knob:

```json
{"id": "glowing_staff", "category": "spear", "visual_scale": 1.0,
 "appearance": {"shape": "crystal_staff"},
 "hold": {"lean": 8, "side": 4, "grip": 0.42, "facing": "front", "offset": [0, 0, 0]}}
```

### Other weapons: size only

Swords, axes, daggers, longswords, greatswords, tachi and normal spear heads keep Epic Fight's own display for their parent model (it is tuned for the diagonal sprite in that category's hold animation). `visual_scale` (0.4-1.5) multiplies the X/Y scale of the third- and first-person hand entries only (gui, ground and frame unchanged) and the translation is moved so the grip stays in the hand. If omitted, the boss's `main_weapon` is shrunk automatically when the mesh scale is above 1.2 (`clamp(1/sqrt(scale), 0.7, 1.0)`), because Epic Fight's 1.5x-1.7x parent scales and the entity scale together make big bosses hold very large blades. The same item model is used when a player holds it.

### What is verified

With the Nightingale boss (scale 1.386) and the crystal staff in a Forge 1.20.1 dev client (`bossforge render-test`; screenshots read): the boss holds the staff upright in its right hand from the front, the side, the back and three-quarter views; the staff rises from about foot level to head height with its orb at chest height (2.5 blocks long for the 1.386-scale boss); a player in Epic Fight mode (standing, third person front and back) holds it upright at the right hip; in vanilla mode it is carried diagonally in front of the leg; the hotbar icon (gui) is the unchanged diagonal sprite; the pose matrices Epic Fight reported agree with `hold-poses.json` to 0.005 blocks. Projecting the predicted staff axis into the hand-front screenshot (camera from the logged position and angles) lands within about 0.1 block of the sprite. King Arthur's longsword (parent model unchanged) renders as Epic Fight's own longsword does, for the boss and a player.

Not verified: the off hand (left-hand entries are the parent's, scaled), first person (the parent's transform, scaled), the ground and item-frame perspectives, attack and run animations (the staff follows the hand, in run_spear it points forward), and bosses whose mesh does not follow the stock biped proportions.

## Categories (`bossforge list weapon-categories`)

| Category | EF type | Style | EF defaults (impact / strikes / armor negation) | Damage / speed / durability |
|---|---|---|---|---|
| sword | epicfight:sword | one_hand | 1.1 / 1 / 0.0 | 6 / -2.4 / 1200 |
| axe | epicfight:axe | one_hand | 1.8 / 1 / 5.0 | 8 / -3.0 / 1200 |
| dagger | epicfight:dagger | one_hand | 0.6 / 1 / 0.0 | 4 / -1.6 / 900 |
| longsword | epicfight:longsword | two_hand | 2.5 / 2 / 0.0 | 8 / -2.6 / 1561 |
| greatsword | epicfight:greatsword | two_hand | 4.0 / 4 / 10.0 | 10 / -3.0 / 1561 |
| tachi | epicfight:tachi | two_hand | 2.0 / 2 / 5.0 | 8 / -2.4 / 1561 |
| spear | epicfight:spear | two_hand | one_hand 1.9/1/8.0; two_hand 1.3/3/0.0 | 7 / -2.6 / 1561 |

EF attributes are ADDED to base armor negation 0.0, impact 0.5, max strikes 1.0. Displayed damage = `1 + modifier` (item modifier = `damage - 1`), displayed speed = `4.0 + attack_speed`.

## Files per weapon

- `assets/<mod>/textures/item/<id>.png` (32x32 for longsword, greatsword, tachi, spear; 64x64 for the spear staff shapes; 16x16 for sword, axe, dagger) and `<id>_gui.png` (half the size: 16x16, or 32x32 for a 64 px staff) for the four long categories.
- `assets/<mod>/models/item/<id>.json`: long categories use Forge `forge:separate_transforms` over `epicfight:item/<cat>` (the same shape EF ships); dagger parents `epicfight:item/dagger`; sword/axe use `minecraft:item/handheld`. A `visual_scale` other than 1.0 adds a `display` block (inside `base` for `separate_transforms`); staff shapes always carry one (`base.display.thirdperson_righthand` is the solved staff transform).
- `data/<mod>/capabilities/weapons/<id>.json`: `{"type":"epicfight:<cat>","attributes":{"common"|"one_hand"/"two_hand":{...}}}`. Floats for impact/armor_negation, int `max_strikes`. The item must be a registered Forge item or EF skips it; no Java capability code is needed.
- Lang keys `item.<mod>.<id>`, `item.<mod>.<id>.desc.<n>`.
- Item registration is generated into `BossRegistry.java` (class `BossWeaponItem` applies on-hit effects and lore).

Sprites are drawn procedurally (axis frame diagonal, parts: pommel, grip, guard with gem, blade with taper/curve/fuller/runes/serration, 1px outline, optional glow halo). Preview `bossforge/previews/<boss>_weapons.png` shows each sprite at 8x (a 64 px staff gives a 512 px panel); read it before approving.

## Obtainability

1. Boss loot table `data/<mod>/loot_tables/entities/<boss>.json`: one pool per weapon with `random_chance` = `obtain.drop`, plus the essence pool (`rewards.essence`).
2. Craft recipe `data/<mod>/recipes/<weapon>.json` when `obtain.craft`: default shaped patterns using `<boss>_essence` (E), stick (S), netherite ingot (N): sword `[" E "," E "," S "]`, dagger `[" E "," S "]`, axe `["EE ","ES "," S "]`, longsword/tachi `[" E ","NEN"," S "]`, greatsword `["EEE"," N "," S "]`, spear `["  E"," S ","S  "]`. Override with `obtain.recipe: {pattern, key}` (<= 3x3, every character keyed, ids `ns:path`).
3. Hidden tier: the sigil chest table `data/<mod>/loot_tables/chests/<boss>_sigil.json`.

Weapon only: `bossforge weapon weapon.json --project DIR` takes a standalone spec (`{"schema":"epicfight-boss-forge/weapon-1","mod_id":...,<weapon object>,"essence_item":"ns:id"}`), stores it as `bossforge/weapons/<id>.weapon.json`, registers the item in `BossRegistry.java`, writes resources and lang. The craft recipe's ingredient is `essence_item` (default `minecraft:diamond`). No loot table is generated.
