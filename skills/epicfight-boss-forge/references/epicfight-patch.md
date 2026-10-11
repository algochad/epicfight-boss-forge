# Epic Fight mobpatch

Generated file: `data/<mod>/epicfight_mobpatch/<boss>.json`, loaded by EF's `MobPatchReloadListener` (datapack folder `epicfight_mobpatch`, file name = entity id). One unresolvable animation or malformed field makes EF skip the entire patch with a log warning, so `qa` validates the generated file against the parser rules and the animation catalog (`assets/data/ef-animations-20.14.17.json`, 335 keys).

## Anatomy (biped)

Key order: `model`, `armature` (`epicfight:entity/biped`), `renderer` (`"player"`), `isHumanoid` (true), `faction`, `attributes{impact, armor_negation, max_strikes, stun_armor, attack_damage, chasing_speed, scale}`, `default_livingmotions`, `humanoid_weapon_motions`, `stun_animations`, `combat_behavior`.

- `attributes`: floats except `max_strikes` (int). `scale` = mesh scale = `size.height / model_height`.
- `default_livingmotions`: idle/walk/chase/mount/fall/death animations.
- `humanoid_weapon_motions`: per weapon style, `idle`/`walk`/`chase` from `biped/living/hold_<cat>`, `walk_<cat>`, `run_<cat>` when those keys exist.
- `stun_animations`: subset of `short long knockdown fall` per tier. A stun type without an animation makes the boss immune to that stun.
- `combat_behavior`: list of `{weapon_categories:[...], style, behavior_series:[...]}`. Entries are emitted for the weapon's natural style, the same series with `"style":"common"` (EF falls back to `common`), and a `fist` fallback.
- Golem form: `isHumanoid:false`, `renderer:"minecraft:iron_golem"`, `stun_animations:{}`, `combat_behavior` is a flat series list, no weapons.

Series object: `{"weight":F,"canBeInterrupted":B,"looping":B,"cooldown":I,"behaviors":[{"conditions":[...],"animation":"epicfight:<key>"}]}`. EF picks a series by weight among those whose FIRST behavior's conditions pass, then plays behaviors in order, aborting if a later behavior's conditions fail. `cooldown` is in ticks.

Predicates: `health {health, comparator: less_ratio|greater_ratio|less|greater}`, `random_chance {chance}`, `within_distance {min,max}`, `within_eye_height`, `within_angle {min,max}`, `within_angle_horizontal {min,max}`. `less_ratio` is true when `health > currentHealth/maxHealth`; `greater_ratio` when `health < currentHealth/maxHealth` (both strict).

## Presets (`bossforge list series`)

| Template / category | Series (animations, reach at scale 1) |
|---|---|
| biped fist (fallback) | `combo` zombie/attack1-3 (1.8) |
| biped sword, axe | `combo` mob_onehand1-2 (2.0) |
| biped dagger | `combo` mob_dagger_onehand1-3 (2.0) |
| biped spear | `combo` mob_spear_twohand1-3 (3.0); `heavy` twohand3, cooldown 100 |
| biped longsword | `combo` mob_longsword1-2 (2.5); `heavy` longsword2 at 40% chance, cooldown 60 |
| biped tachi | longsword `combo` and `heavy`; `special` mob_tachi_special (4.0), cooldown 100 |
| biped greatsword | `combo` greatsword_auto1-2 + mob_greatsword1 at 30% (3.0); `heavy` mob_greatsword1, cooldown 100 |
| iron_golem | `smash` attack2 (2.0); `combo` attack3, attack4 (eye height only), attack1 at 30% |

Reach distances are multiplied by `scale * range_scale` (min stays).

## Moves in the spec

`moves[]` (omit for defaults): `{series: "<preset id>"|"custom", weight?, cooldown?, phases?:[int], chance?, range_scale?, interruptible?, loop?, animations?}`. Default phases per series: `combo` all phases, `heavy` phases 2..P (or 1 if only one phase), `special` the last phase; golem `smash` all, `combo` 2..P.

Phase windows: phase k covers health in `(thr_{k+1}, thr_k]`. Contiguous phases merge into one series; the first behavior gets `health less_ratio hi` when `hi < 1.0` and `health greater_ratio lo` when `lo > 0.0`. Phase 1 therefore has no upper bound. Never use `branch_N` blocks: they resolve once at patch creation.

## Stun math

Stun duration reduction = `stun_armor / (stun_armor + 7.5)`; with `stun_armor 15` the boss takes 33% of normal stun time. `impact` (weapon + base) must exceed stun armor thresholds to stagger.

## Custom models on the biped rig

A boss with a `shape` gets `"model": "<mod>:entity/<id>"` (EF loads `assets/<mod>/animmodels/entity/<id>.json`), `"renderer": "epicfight:custom"` and `"humanoid": true`, and keeps `"armature": "epicfight:entity/biped"`, so every biped animation and weapon moveset still applies. The renderer key is essential: `renderer: "player"` always draws the stock biped mesh and ignores `model` (a custom model with it renders the stock mesh with the custom texture scrambled across it). `epicfight:custom` reads the boolean `humanoid` (not `isHumanoid`) to choose the humanoid renderer with item/armor layers. The mesh JSON has no armature section; vertices are rigidly weighted to the biped joints (indices from `assets/data/rigs.json`), and all 12 EF humanoid part names (`head hat torso jacket rightArm leftArm rightLeg leftLeg rightSleeve leftSleeve rightPants leftPants`) are present because `HumanoidMesh` looks them up. `qa` re-validates the mesh JSON and checks that the mobpatch `model` matches.

## Custom armatures

Not generated. A custom boss mesh needs a Blender rig exported with the EF exporter (https://github.com/Epic-Fight/blender-json-exporter) as `assets/<mod>/animmodels/entity/<name>.json` plus an armature and animation set registered through the EF API (`docs/API/Starting.en.md` in the EF docs repo). Point `model`/`armature` at the new ids; the rest of this skill (spec, weapons, loot, Java) still applies.
