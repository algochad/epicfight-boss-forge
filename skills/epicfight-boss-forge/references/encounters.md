# Encounters and progression

`encounter.trigger` (tier default in `tiers.md`):

| Trigger | Generated | Notes |
|---|---|---|
| `summon_item` | sigil item `<boss>_sigil` + sprite + lang; recipe `<boss>_sigil` (default `[" N ","NSN"," N "]`, N netherite ingot, S nether star) unless `recipe: null` or hidden tier | `summon_item: {required_block: null\|"ns:path", dimension: null\|"ns:path", consume: true, recipe: {pattern,key}\|null}` |
| `command` | `data/<mod>/functions/summon_<boss>.mcfunction` (`summon <mod>:<boss> ~ ~ ~`) | for command blocks, quest mods, datapack triggers |
| `natural_spawn` | `data/<mod>/forge/biome_modifier/<boss>_spawn.json` (`forge:add_spawns`) | `natural_spawn: {biomes: "#minecraft:is_overworld", weight: 1}`; spawn placement registered in `BossModEvents` |

Always: advancement `data/<mod>/advancements/bosses/<boss>.json` (kill trigger; frame challenge for major/hidden, goal for quest, task for mini; hidden for hidden tier), spawn egg item (not hidden tier), loot table, essence item, creative tab entries, boss bar from `encounter.boss_bar {show,color,overlay}` (colors pink blue red green yellow purple white; overlays progress notched_6 notched_10 notched_12 notched_20).

The sigil's `useOn` is server-side: wrong block sends `message.<mod>.sigil.wrong_block`, wrong dimension sends `message.<mod>.sigil.wrong_dimension`; otherwise it spawns the boss above the clicked block and consumes one sigil (not in creative).

## Hidden-boss design

Hidden tier only guarantees the sigil exists as loot (chest table `<mod>:chests/<boss>_sigil`) with no recipe, no spawn egg, a hidden advancement. Place it yourself: point a structure's chest `LootTable` NBT at the table, use it as extra loot in an existing structure (loot modifier), or give it to a quest. Secret conditions (location, time, riddle, puzzle) are NOT automated; add them with `required_block`/`dimension` on the sigil or with your own datapack logic.
