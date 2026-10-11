# Intake: from the user's words to spec fields

Ask only for what is missing, in one batch. Offer 2-4 concrete options per question (host question tool). After collecting, write `boss.json`, show the lore you expanded and the interpreted appearance for approval.

## Question list (skip anything already answered)

1. Name and optional title.
2. Tier: major (arena fight, summon item), quest (command-triggered, mid-power), mini (roams, spawns naturally), hidden (secret, loot-only sigil).
3. Size in blocks ("3 tall, 1 wide"). Limits: height 1.0-8.0, width 0.4-4.0 (width is also depth). Without an answer use the tier default.
4. Backstory or a one-line concept. At least one sentence is required.
5. Body shape: "What silhouette: hulking brute, armored knight, gaunt king, winged fiend, hooded caster, beast, insect? Signature pieces (crown, horns, wings, cape, tail, oversized arm or pauldron, shield)? Anything asymmetric?" Without an answer use the archetype's silhouette, but a vague answer is a reason to ask: a boss must not look generic.
6. Appearance: colors, armor, face, emblem, glow. Without an answer use an archetype.
7. Fighting style: fast/slow, heavy hits, what weapon.
8. Main weapon: name, category (sword, axe, dagger, longsword, greatsword, tachi, spear), look (blade shape, guard), how players get it (boss drop, craft, both).
9. Target mod: new (`mod_id`, display name, group) or existing project path.

## Size

| User says | Spec |
|---|---|
| "3 blocks tall, 1 wide" | `"size": {"height": 3.0, "width": 1.0}` |
| "2 wide" only | `width: 2.0`, height from the tier default |
| "huge", "giant" | height 5-6, width 1.6-2.2 |
| "small", "quick" | height 1.8-2.2 |
| very wide + no weapon | `body.template: iron_golem` (stock golem mesh) or a `brute`/`colossus` shape |

A `shape` boss is always built on the EF biped rig, so it can wield any weapon. Size is the TOTAL height including crown and horns.

## Body shape

Map the description to a silhouette first (`bossforge list silhouettes`), then proportions, attachments and free-form parts (`shape-forge.md` has every option):

| User says | Shape |
|---|---|
| "king", "crowned" | silhouette `knight` or `skeletal_king`; attachment `crown` (spiked/tall/band/broken) |
| "armored", "knight", "paladin" | `knight`; `pauldrons` layered, `gauntlets`, `greaves`, `cape`, `shield` kite |
| "warlord", "war chief" | `warlord` (asymmetric pauldron, banner) |
| "hulking", "brute", "giant fists" | `brute` or proportions `head 0.8, shoulders 1.5, arm_thickness 1.7, fist 2.2`; `jaw`, `spine_spikes` |
| "colossus", "golem-like", "construct" | `colossus` (reactor `core`, round pauldrons) |
| "demon", "winged", "devil" | `fiend`: `horns` demon, `wings` bat, `tail` spiked, `claws` |
| "skeleton", "lich", "undead king" | `skeletal_king`: `ribcage`, skull `pauldrons`, tattered `cape` |
| "mage", "sorcerer", "cultist" | `caster`: `hood`, tattered `cape`, `skirt` robe, `halo`/`orbs` |
| "ghost", "wraith", "specter" | `wraith`: long arms with talons, pointed `hood`, shards |
| "beast", "werewolf", "lion" | `beast`: `mane`, `tusks`, `claws`, `tail` |
| "insect", "mantis", "spider-like" | `insectoid`: `extra_arms`, mandibles |
| "horns", "antlers" | `horns` (curved straight ram antlers demon bull) |
| "cape", "cloak" | `cape` (short long tattered) |
| "wings" | `wings` (bat feathered angel seraph skeletal flame; `spread` 10-80); static, they do not flap. `seraph` = tall raised angel wings as one dense fan-shaped gradient sail (white top, lavender, violet tips): `spread` ~30, `color` white, `accent` lavender, `tip` violet; add a painter `gradient` feature on `box:wings_f*` for a stronger fade |
| "angel", "healer", "celestial", "feathered wings" | `caster` with `remove` hood/halo/orbs/cape, `wings` seraph `spread` 30, `skirt` robe `slit`+`hem`+`arm_clear` (centre panel + side slits + light hem layer, hands stay visible), `pouch` `arm_clear`, `pauldrons` layered `scale` 0.75, `greaves` sabaton `material` silk + `cuff` + `sole`, `hairdo`, `ribbons` all, `pouch`; raise `size.height` ~15% because the wings add height |
| "hair in a bun", "ponytail", "braid", "bangs", "side locks", "hair ribbons", "hair pins" | `hairdo` (`style` bun twin_buns ponytail braid; `bangs`, `locks`, `ribbon_loops`, `pins`); hair colour is the palette `hair` |
| "long ribbons", "streamers", "trailing sashes" | `ribbons` (`style` wrist hem back all; `length` 0.5-2.0), white `color` + pale blue `accent` |
| "potion pouch", "vials", "belt bag" | `pouch` (`side`, `vials` 0-4, `colors` list, `belt`) |
| "slit skirt", "open robe front", "tabard panel on the skirt", "layered hem" | `skirt` with `slit: true` (centre panel + side slits), `panel: true` (panel only) and/or `hem: true`; paint the panel via region `box:skirt_fp*` |
| "soft boots with a cuff" | `greaves` `style` sabaton, `material` silk, `cuff: true` (`cuff_color`), `sole: true` |
| "tail" | `tail` (lizard spiked whip club) |
| "shield" | `shield` (round kite tower) on the left forearm |
| "floating orbs / shards / halo" | `orbs`, `halo` |
| "one huge shoulder / chained lantern / spear through the arm" | free-form `parts` boxes (coordinates in `shape-forge.md`) |
| "small head, huge hands" | proportions `head`, `fist` |
| "tall and gaunt" | proportions `torso_height 1.2`, `arm_thickness 0.6`, `leg_thickness 0.6` |

Rules: 2-4 signature pieces, 2-3 proportion overrides, glow in the accent color. Check the side and back panels of the preview, not only the front.

## Appearance

Pick the closest archetype, then override. Archetypes: `knight undead beast demon elemental golem wraith cultist` (`bossforge list archetypes`).

| User says | Spec |
|---|---|
| "glowing crown" | `crown` attachment with `color: accent`; for a baked gold cap use `overlay` `{"region":"head","material":"plate_gold","color":"accent","y":[0.86,1.0]}` |
| "glowing eyes" | feature `{"type":"eyes","glow":true,"color":"eyes"}` |
| "scar over the left eye" | `{"type":"scar","side":"left"}` |
| "long grey beard" | `{"type":"beard","length":"long","color":"#c8c8d0"}` |
| "plate armor" | `regions` torso/arms `plate_steel`, `plates` feature, `pauldrons`/`gauntlets`/`greaves` attachments |
| "hooded" | `{"type":"hair","style":"hood","color":"primary"}` |
| "sun/cross/skull crest" | `{"type":"emblem","shape":"sun","region":"torso","face":"front","color":"accent"}` |
| "cracked, magma" | `cracks` feature with accent color, `ember` material region |
| "runes" | `{"type":"runes","color":"accent"}` |
| "bone/skeletal" | archetype `undead` |
| "furry/beast" | archetype `beast` |

Palette keys: `primary secondary accent skin hair eyes`. Colors are palette keys or `#rrggbb`.

Extra limbs that animate, flapping wings, cloth physics and non-humanoid skeletons cannot exist (rigid static pieces on the EF biped rig only); say so and offer the closest static alternative.

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

## Reference images

When the user pastes a character sheet or concept image: (1) read it and list every visible feature as a checklist (silhouette and proportions; head: hair style, bangs, bun, pins, face, eye colour; each garment with trim and accent colours and ornament patterns; back pieces: wings or cape with colour gradient; footwear; held item shape; small props such as pouches, vials, ribbons; glow accents); (2) map each line to a spec field; (3) render `skin` and compare the preview to the reference feature by feature, from front, side and back; (4) when a feature has no generator support, add it to the generator (new preset, feature, material or weapon shape) instead of dropping it; (5) report the checklist with what matches and what is only approximated. An exact copy of an arbitrary model is not possible: the boss is boxes skinned to the Epic Fight biped rig and painted procedurally.
