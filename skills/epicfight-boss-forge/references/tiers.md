# Tiers

Tier sets every default (stats, phases, stun animations, boss bar, trigger, rewards). Override any value in the spec.

| Key | major | quest | mini | hidden |
|---|---|---|---|---|
| health | 400 | 200 | 80 | 600 |
| armor | 12 | 8 | 4 | 14 |
| armor_toughness | 6 | 3 | 0 | 8 |
| attack_damage | 10 | 7 | 4 | 14 |
| movement_speed | 0.26 | 0.25 | 0.25 | 0.28 |
| knockback_resistance | 0.8 | 0.5 | 0.2 | 1.0 |
| follow_range | 48 | 40 | 28 | 56 |
| xp_reward | 500 | 200 | 50 | 800 |
| impact | 3.0 | 2.0 | 1.0 | 3.5 |
| armor_negation | 15.0 | 8.0 | 0.0 | 20.0 |
| max_strikes | 3 | 2 | 1 | 3 |
| stun_armor | 15.0 | 7.5 | 3.0 | 22.5 |
| chasing_speed | 1.15 | 1.1 | 1.0 | 1.2 |
| phase thresholds | [1.0, 0.66, 0.33] | [1.0, 0.5] | [1.0] | [1.0, 0.66, 0.33] |
| phase names | Sworn, Wounded, Last Stand | Guarded, Desperate | Hostile | Veiled, Revealed, Unbound |
| stun animations (biped) | long, knockdown | short, long, knockdown | short, long, knockdown, fall | knockdown |
| boss bar | purple notched_10 | yellow progress | hidden | white notched_20 |
| trigger | summon_item | command | natural_spawn | summon_item |
| essence per kill | 3-4 | 2-3 | 1-1 | 4-5 |
| weapon drop / craftable | 0.25 / yes | 1.0 / no | 0.05 / yes | 1.0 / no |
| default size (h x w) | 3.0 x 1.0 | 2.4 x 0.9 | 2.0 x 0.7 | 3.2 x 1.1 |

Stat meanings: `impact` is the stun power of the boss's hits, `armor_negation` the % of armor ignored, `max_strikes` the targets hit per swing, `stun_armor` resists being staggered (stun time is reduced by `stun_armor/(stun_armor+7.5)`), `chasing_speed` the speed multiplier while chasing. These numbers are design defaults to tune in play.

## Design guidance

- **major**: arena fight. Summoned with a sigil item at a chosen block. Three phases, boss bar, long/knockdown stuns only (hard to chain-stun). Reward: weapon drop 25% plus 3-4 essence (craft more weapons). Put it in a built arena; add `summon` in phase 2 and `enrage` or `regen` in phase 3.
- **quest**: part of a chain. Spawned by `/function <mod>:summon_<id>` from a quest mod, command block or advancement reward. Two phases, yellow bar. Weapon drop 100%, not craftable (the quest hands it out).
- **mini**: roaming elite. Spawns naturally (biome modifier, weight 1), no boss bar, one phase, 5% weapon drop, craftable from essence. Keep health low so it can show up in normal exploration.
- **hidden**: secret. Sigil exists only as loot (chest table `<id>_sigil` is generated: point a structure or chest at it); no recipe, no spawn egg. The advancement is hidden. Highest stats, white notched bar, 100% weapon drop, not craftable. Secret conditions (locations, riddles) are NOT automated; see `encounters.md`.
