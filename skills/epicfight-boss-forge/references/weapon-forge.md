# Weapon forge

Every boss weapon is a real Forge item the player can obtain, with an Epic Fight capability file so it behaves as an EF weapon (moveset, stun, strikes) in the player's hands and in the boss's.

## Weapon object

`{id, display_name, category, rarity (common|uncommon|rare|epic; rare), damage (1-40), attack_speed (-4.0..0.0), durability (100-5000), ef{impact, armor_negation, max_strikes, damage_bonus?, speed_bonus?}, appearance{blade, guard, grip, accent, gem, glow, motif plain|fuller|runes|serrated, shape, guard_shape, pommel_shape, length 0.8-1.15, width 0.7-1.4, resolution 32|64, rings 0-4, spikes 2-5}, visual_scale 0.4-1.5, effects[{on:"hit", effect, duration:100, amplifier:0, chance:1.0}], obtain{drop, craft, recipe?}, lore[<=4 lines <=90 chars]}`. Spears take `ef: {one_hand:{...}, two_hand:{...}}`. `resolution`, `rings` and `spikes` are optional and only matter for the staff shapes (below).

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

A mage staff uses the spear category on purpose: it gets EF's spear moveset, `epicfight:spear` capability and the `epicfight:item/spear` model (hold pose and held transform). Only the sprite differs. Colours reuse the normal keys: `blade` = crystal, `guard` = gold, `grip` = shaft bands (crystal_staff) or wood (orb_staff), `gem` = orb, `accent` = glow halo (`glow: true`). `pommel_shape`/`guard_shape`/`motif` do not apply.

| Shape | Look (foot to tip) |
|---|---|
| `crystal_staff` | crystal point, gold foot band, shaft in alternating `grip` blue and aqua bands over pale crystal, gold collar, flared crystal, then a see-through gold ring with silver arcs and bars around a faceted glowing `gem` orb (bright core, highlight pixel, gold struts), and a stepped crystal spire on top |
| `orb_staff` | crystal ferrule, gold band, plain `grip` shaft, gold collar, orb in a gold ring, a fan of crystal shards on top |

- `spikes` (2-5, default 3): spire steps of `crystal_staff`, shards in the fan of `orb_staff`.
- `rings` (0-4, default 2): gold rings on the shaft. 0 = none (collar and foot band turn silver), 1 = collar, 2 = collar + foot band (the sheet look), 3 and 4 add one or two thin rings mid-shaft.
- `length` stretches the shaft and spire, `width` slims or widens every part.
- `resolution` (32 or 64): staffs default to **64**, because the orb, ring and spire are too fine for 32 px (a staff at 32 is still valid and complete, just coarser). Any other shape only offers its category's native size; asking for another value is a validation error (`unknown_value` at `/appearance/resolution`).

Resolution never changes the in-game size: `epicfight:item/spear` and the Forge `separate_transforms` perspectives parent vanilla `item/generated`, which maps the texture onto a fixed 16-unit square whatever its pixel size (EF itself ships a 32 px spear texture next to a 16 px `_gui` one). A 64 px staff gets a 32 px `_gui` texture, so the pair keeps the 2:1 ratio of the other long weapons. Held scale is controlled by `visual_scale` only.

Example (the Celestial Mage staff):

```json
{"id": "glowing_staff", "display_name": "Glowing Staff", "category": "spear", "rarity": "epic",
 "appearance": {"shape": "crystal_staff", "blade": "#bfe9ff", "guard": "#f0c850", "grip": "#4aa0e8",
                "accent": "#7fd6ff", "gem": "#2ea8ff", "glow": true, "resolution": 64, "rings": 2, "spikes": 3}}
```

## Held size (`visual_scale`)

EF's weapon parent models display held items at 1.5x, and EF multiplies by the entity `scale` again, so weapons on big bosses look enormous. `visual_scale` (0.4-1.5) scales the weapon in the third/first person HAND perspectives only (gui, ground and frame are unchanged; the grip stays at the hand). If omitted, the boss's `main_weapon` is shrunk automatically when the mesh scale is above 1.2 (`clamp(1/sqrt(scale), 0.7, 1.0)`). The same item model is used when a player holds it.

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
- `assets/<mod>/models/item/<id>.json`: long categories use Forge `forge:separate_transforms` over `epicfight:item/<cat>` (the same shape EF ships); dagger parents `epicfight:item/dagger`; sword/axe use `minecraft:item/handheld`.
- `data/<mod>/capabilities/weapons/<id>.json`: `{"type":"epicfight:<cat>","attributes":{"common"|"one_hand"/"two_hand":{...}}}`. Floats for impact/armor_negation, int `max_strikes`. The item must be a registered Forge item or EF skips it; no Java capability code is needed.
- Lang keys `item.<mod>.<id>`, `item.<mod>.<id>.desc.<n>`.
- Item registration is generated into `BossRegistry.java` (class `BossWeaponItem` applies on-hit effects and lore).

Sprites are drawn procedurally (axis frame diagonal, parts: pommel, grip, guard with gem, blade with taper/curve/fuller/runes/serration, 1px outline, optional glow halo). Preview `bossforge/previews/<boss>_weapons.png` shows each sprite at 8x (a 64 px staff gives a 512 px panel); read it before approving.

## Obtainability

1. Boss loot table `data/<mod>/loot_tables/entities/<boss>.json`: one pool per weapon with `random_chance` = `obtain.drop`, plus the essence pool (`rewards.essence`).
2. Craft recipe `data/<mod>/recipes/<weapon>.json` when `obtain.craft`: default shaped patterns using `<boss>_essence` (E), stick (S), netherite ingot (N): sword `[" E "," E "," S "]`, dagger `[" E "," S "]`, axe `["EE ","ES "," S "]`, longsword/tachi `[" E ","NEN"," S "]`, greatsword `["EEE"," N "," S "]`, spear `["  E"," S ","S  "]`. Override with `obtain.recipe: {pattern, key}` (<= 3x3, every character keyed, ids `ns:path`).
3. Hidden tier: the sigil chest table `data/<mod>/loot_tables/chests/<boss>_sigil.json`.

Weapon only: `bossforge weapon weapon.json --project DIR` takes a standalone spec (`{"schema":"epicfight-boss-forge/weapon-1","mod_id":...,<weapon object>,"essence_item":"ns:id"}`), stores it as `bossforge/weapons/<id>.weapon.json`, registers the item in `BossRegistry.java`, writes resources and lang. The craft recipe's ingredient is `essence_item` (default `minecraft:diamond`). No loot table is generated.
