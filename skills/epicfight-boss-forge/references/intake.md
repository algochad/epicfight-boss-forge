# Intake: from the user's words to spec fields

Ask only for what is missing, in one batch. Offer 2-4 concrete options per question (host question tool). After collecting, write `boss.json`, show the lore you expanded and the interpreted appearance for approval.

## Question list (skip anything already answered)

1. Name and optional title.
2. Tier: major (arena fight, summon item), quest (command-triggered, mid-power), mini (roams, spawns naturally), hidden (secret, loot-only sigil).
3. Size in blocks ("3 tall, 1 wide"). Limits: height 1.0-8.0, width 0.4-4.0 (width is also depth). Without an answer use the tier default.
4. Backstory or a one-line concept. At least one sentence is required.
5. Appearance: colors, armor, face, emblem, glow. Without an answer use an archetype.
6. Fighting style: fast/slow, heavy hits, what weapon.
7. Main weapon: name, category (sword, axe, dagger, longsword, greatsword, tachi, spear), look, how players get it (boss drop, craft, both).
8. Target mod: new (`mod_id`, display name, group) or existing project path.

## Size

| User says | Spec |
|---|---|
| "3 blocks tall, 1 wide" | `"size": {"height": 3.0, "width": 1.0}` |
| "2 wide" only | `width: 2.0`, height from the tier default |
| "huge", "giant" | height 5-6, width 1.6-2.2 |
| "small", "quick" | height 1.8-2.2 |
| very wide + no weapon | auto picks `iron_golem` |

Wielding a weapon forces the humanoid `biped` mesh. A wide hitbox on the biped mesh warns `wide_hitbox_for_template` (mesh looks narrow inside the hitbox).

## Appearance

Pick the closest archetype, then override. Archetypes: `knight undead beast demon elemental golem wraith cultist` (`bossforge list archetypes`).

| User says | Spec |
|---|---|
| "crown" | `overlay` `{"region":"head","material":"plate_gold","color":"accent","y":[0.86,1.0]}` |
| "glowing eyes" | feature `{"type":"eyes","glow":true,"color":"eyes"}` |
| "scar over the left eye" | `{"type":"scar","side":"left"}` |
| "long grey beard" | `{"type":"beard","length":"long","color":"#c8c8d0"}` |
| "plate armor" | overlay entries with `plate_steel` on torso/arms/legs plus `plates` feature |
| "hooded" | `{"type":"hair","style":"hood","color":"primary"}` |
| "sun/cross/skull crest" | `{"type":"emblem","shape":"sun","region":"torso","face":"front","color":"accent"}` |
| "cracked, magma" | `cracks` feature with accent color, `ember` material region |
| "runes" | `{"type":"runes","color":"accent"}` |
| "bone/skeletal" | archetype `undead` |
| "furry/beast" | archetype `beast` |

Palette keys: `primary secondary accent skin hair eyes`. Colors are palette keys or `#rrggbb`.

Capes, horns, wings, extra limbs, floating parts CANNOT exist: the EF mesh is fixed, only the painted skin changes. Honest alternatives: paint the cape colors as a tabard with `emblem`, paint horns on a hood, use the `golem` template for bulk, or tell the user a custom Blender mesh is outside this skill (see `epicfight-patch.md`).

## Fighting style, skills

| User says | Spec |
|---|---|
| "fast and light" | `dagger` or `sword` weapon, higher `movement_speed` in `stats`, `chasing_speed` 1.2 |
| "heavy, slow" | `greatsword`, `moves: [{"series":"heavy","weight":100}]` plus `combo` |
| "reach, thrusts" | `spear` |
| "lunges / big finisher" | `tachi` (has a `special` series) |
| "gets angrier" | extra `phases` and `{"type":"enrage","phase":N,"speed_pct":15,"damage_pct":15}` |
| "heals" | `{"type":"regen","per_second":2.0,"phases":[3]}` |
| "summons minions" | `{"type":"summon","entity":"minecraft:vindicator","count":2,"phase":2}` |
| "poison/wither touch" | `{"type":"on_hit_effect","effect":"minecraft:wither","duration":100}` |
| "damaging aura" | `{"type":"aura","effect":"minecraft:poison","radius":6.0,"interval":40}` |
| "knockback/stun resistant" | raise `stats.stun_armor`, `knockback_resistance` |

Custom attack chains: `{"series":"custom","animations":[{"animation":"epicfight:biped/combat/mob_longsword1","max_range":2.5}]}`; find keys with `bossforge list animations --prefix biped/combat`. Mobs only play animations whose class is an attack; the validator warns otherwise.

## Weapon

| User says | Spec |
|---|---|
| "sword" | `category: sword` (one hand) |
| "big sword", "claymore" | `greatsword` |
| "katana" | `tachi` |
| "knightly sword" | `longsword` (default for kings and knights) |
| "players can use it" | always true: weapons are real items with EF capabilities |
| "drops from boss" | `obtain.drop` (0-1; tier default) |
| "craftable" | `obtain.craft: true`; default recipe uses `<boss>_essence` |
| "poisons on hit" | weapon `effects: [{"on":"hit","effect":"minecraft:poison","duration":100,"chance":0.3}]` |

## Encounter

| User says | `encounter.trigger` |
|---|---|
| "summon item", "altar" | `summon_item` (+ `required_block`, `dimension`) |
| "quest boss", "spawned by command / quest mod" | `command` |
| "roams the world" | `natural_spawn` (+ `biomes`, `weight`) |
| "secret", "hidden" | tier `hidden`: sigil exists only as loot, no recipe |

## Lore expansion rule

From a one-line concept write: `summary` (1 sentence), `backstory` (2-3 sentences), `description` (what the player sees), `personality`, `death_message`. Keep each under 1200 characters. Show them to the user and wait for approval before generating.
