---
name: epicfight-boss-forge
description: "Design and generate Epic Fight-native boss content for Forge 1.20.1 mods: boss spec (size in blocks, lore, stats, tiers major/quest/mini/hidden), procedural boss skin/texture generator, boss weapon generator (item sprites, Epic Fight weapon capability, loot and recipes so players can obtain it), Epic Fight mobpatch movesets with phases, and Forge Java/resource scaffolding for a new mod or an existing one. Use when the user wants to create a boss, mini boss, quest boss or hidden boss, a boss skin or texture, a boss weapon, an Epic Fight mobpatch or combat pattern, a summon item or boss encounter, or to add a boss or weapon to a Forge 1.20.1 Epic Fight mod."
license: MIT
compatibility: "Python 3.9+ standard library only. Needs network once to fetch Epic Fight meshes and the Forge MDK (or pass --ef-jar/--mdk-zip). JDK 17 only to compile generated mods. Targets Forge 1.20.1 with Epic Fight 20.14.x."
metadata:
  version: "0.1.0"
  target: "forge-1.20.1 epicfight-20.14.17"
---

# Epic Fight Boss Forge

Generates a complete Epic Fight-native boss for a Forge 1.20.1 mod from one JSON spec: entity texture painted onto the real Epic Fight mesh, weapon items (sprites, EF capability, loot, recipes), the EF `mobpatch` moveset with health phases, and the Java/resources that register it. Commands are run from the skill root as `python3 scripts/bossforge.py <command>` (written `bossforge` below). All commands are non-interactive and print one JSON object (`{"ok":true,...}` or `{"ok":false,"errors":[{path,code,message}]}`); exit 0 ok, 1 failure, 2 usage.

## Use when / do not use

- Use for: bosses, mini/quest/hidden bosses, boss skins/textures, boss weapons, EF mobpatches, summon items/encounters, adding any of these to a Forge 1.20.1 mod that depends on Epic Fight.
- Do NOT use for NeoForge, Fabric, or other Minecraft versions: stop and say it is unsupported.
- Do NOT use for GeckoLib or custom-armature bosses. Custom meshes/armatures need a Blender rig plus the EF exporter (https://github.com/Epic-Fight/blender-json-exporter); see `references/epicfight-patch.md` "custom armatures". This skill uses the two EF meshes `biped` and `iron_golem` only.

## Prerequisites (check, do not assume)

1. `python3 --version` is 3.9 or newer.
2. Network once to fetch EF meshes (`bossforge fetch-ef-assets`; offline alternative `--ef-jar path/to/epicfight.jar`) and, for `new-mod`, the Forge MDK (alternative `--mdk-zip`).
3. JDK 17 only to compile the generated mod (`java -version`; with mise: `mise x java@temurin-17 -- ./gradlew ...`).

## Pick the path

| Goal | Commands |
|---|---|
| New mod + boss | `new-mod`, then `add-boss` |
| Existing mod | confirm `gradle.properties` has `mod_id` and a `@Mod("<mod_id>")` class exists, then `add-boss` |
| Weapon only (new item for a mod) | `weapon SPEC --project DIR` (adds an item, no boss) |
| Texture only | `skin SPEC --out DIR` |

## Intake

Ask only what is missing, in ONE batch, with the host's question tool (omp `ask`, Claude Code AskUserQuestion). Never ask what the user already said. Detailed mappings from natural language to spec fields are in `references/intake.md`.

| User gives | Spec field | Default if absent |
|---|---|---|
| name, title | `display_name`, `title` | name required |
| tier (major / quest / mini / hidden) | `tier` | required |
| "N blocks tall, M wide" | `size.height`, `size.width` | tier default size (3.0x1.0 major, 2.4x0.9 quest, 2.0x0.7 mini, 3.2x1.1 hidden) |
| backstory / description | `lore.*`: expand 1-2 sentences into summary, backstory, description, personality, death_message and show them for approval | at least one sentence required |
| appearance text | `appearance.archetype` + `palette` + `regions` + `overlay` + `features` | archetype `knight` |
| fighting style / skills | `moves`, `phases`, `abilities` | tier defaults |
| main weapon (name, category, look, how players get it) | `weapons[]`, `main_weapon`, `obtain` | none (fist moves) |
| how it appears | `encounter.trigger` | tier default |
| mod target | new (`mod_id`, `mod_name`, `--group`) or existing project path | required |

Size note: Minecraft hitboxes are square, so `size.width` is both width and depth. The boss body is one of two EF meshes (`biped` for humanoids and any weapon wielder, `iron_golem` for wide brutes); `body.template: auto` picks by proportions. The height in blocks sets the mesh `scale`, the hitbox comes from the entity type.

## Workflow (copy this checklist and tick it)

1. [ ] Write `boss.json`: `bossforge init --id ID --name NAME --mod-id MOD --tier TIER [--height H --width W --archetype A --weapon-category C --weapon-name N] --out boss.json`, then edit lore/appearance/weapons/moves.
2. [ ] `bossforge validate boss.json` until `ok:true`. Errors list the exact JSON pointer and allowed values.
3. [ ] `bossforge skin boss.json --out /tmp/bf-out`, then READ `/tmp/bf-out/bossforge/previews/<id>_preview.png` with the image reader.
4. [ ] Review gate (below). Iterate on `palette`/`regions`/`overlay`/`features` only.
5. [ ] New mod: `bossforge new-mod --mod-id MOD --mod-name "Name" --out DIR`. Then `bossforge add-boss boss.json --project DIR`.
6. [ ] Compile gate when a JDK exists: `cd DIR && ./gradlew --no-daemon compileJava processResources` (needs JDK 17).
7. [ ] Report: files written, warnings, `wiring_required`, what is unverified in game (see `references/qa.md`).

`add-boss` is idempotent. Re-running reports `unchanged`; files you edited by hand are listed under `conflicts` and kept unless `--force`. Specs are copied to `<project>/bossforge/bosses/<id>.boss.json`; edit that and re-run to regenerate. Lang files are merged: existing keys are never overwritten.

## Review gate

You cannot judge your own visual work. After `skin`: open the preview image, describe what is visible (face on the FRONT panel, palette, emblem, overlay armor, glowing parts, empty areas), then ask the user to approve or request changes BEFORE `add-boss`. A missing answer is not approval. Never hand-edit PNGs; change the spec and regenerate. Weapons: `bossforge weapon boss.json --out /tmp/bf-out` and read `<id>_weapons.png` the same way.

## Gotchas (verified from Epic Fight 1.20.1 / Forge 1.20.1 source)

- EF reads attribute numbers by NBT type: `stun_armor 15.0`, `impact 3.0`, `armor_negation 15.0`, `attack_damage 14.0`, `scale 1.5` MUST be floats and `max_strikes 3` an int, or the value is silently ignored (generator guarantees this; keep it when hand-editing).
- Humanoid `combat_behavior.weapon_categories` must be a JSON list.
- A modded boss MUST have EF attributes added in `EntityAttributeModificationEvent` or EF throws an NPE (generated `BossModEvents` does it).
- The entity must extend `PathfinderMob` and must not add melee goals; EF adds `AnimatedAttackGoal` and `TargetChasingGoal`.
- `renderer: "player"` for the biped template, `"minecraft:iron_golem"` for the golem; never `epicfight:custom` (it reads a `humanoid` key the parser never writes).
- `scale` resizes the mesh only; the hitbox comes from `EntityType.Builder.sized`.
- One unresolvable animation key makes EF skip the whole patch with only a log warning (`Can't deserialize mob capability`). `qa` checks every key against the catalog.
- A stun type with no bound animation means the mob is immune; stun time reduction is `stun_armor/(stun_armor+7.5)`.
- The weapon needs `data/<mod>/capabilities/weapons/<id>.json` or EF treats it as fists/one-hand tools.
- Phases are `health` predicates, never `branch_N` (resolved once at patch creation).
- 1.20.1 data paths: `loot_tables/`, `recipes/`, `advancements/`, `functions/`, `forge/biome_modifier/`.
- The MDK must be the 1.20.1 one (Java 17, ForgeGradle 6), not the `1.20.x` branch (Java 21).
- EF meshes are fetched from GitHub into the cache (GPL): never commit them to a project.
- Overlay-layer armor and the golem renderer path cannot be verified offline: say so in the report. If the overlay does not render in game set `appearance.overlay_enabled: false`.

## Command reference

| Command | Purpose |
|---|---|
| `list tiers\|archetypes\|templates\|materials\|features\|abilities\|weapon-categories\|series\|animations [--prefix P]` | valid values |
| `init --id --name --mod-id --tier ... --out FILE` | valid starter spec |
| `validate SPEC` | spec check (boss or weapon spec) |
| `fetch-ef-assets [--ef-jar JAR] [--ref SHA]` | cache EF meshes |
| `mesh-info --template biped\|iron_golem` | mesh diagnostics |
| `skin SPEC --out DIR [--mesh-file P]` | texture + preview |
| `weapon SPEC (--out DIR \| --project DIR) [--essence-item ns:id] [--force] [--dry-run]` | weapon files, or add a weapon to a mod |
| `patch SPEC --out DIR` | EF mobpatch JSON only |
| `new-mod --mod-id --mod-name --out DIR [--group G --author A --description D --mdk-zip Z --forge-version V]` | Forge 1.20.1 + EF project |
| `add-boss SPEC --project DIR [--force] [--dry-run] [--ef-jar JAR]` (alias `build`) | everything for one boss |
| `lore SPEC --out DIR` | design doc |
| `qa --project DIR` | project checks |
| `catalog --ef-ref SHA --out FILE` | maintainer: rebuild animation catalog |

Global flags: `--cache-dir DIR` (default `$BOSSFORGE_CACHE` or `~/.cache/epicfight-boss-forge`), `--offline`.

## Read more only when needed

| File | Read when |
|---|---|
| `references/intake.md` | turning the user's words into spec fields (crowns, glowing eyes, heavy/slow, heals, minions) |
| `references/boss-spec.md` | field table, defaults, validation codes, examples |
| `references/tiers.md` | choosing/tuning a tier |
| `references/epicfight-patch.md` | moveset, phases, stun math, presets, custom armatures |
| `references/skin-forge.md` | materials, features, regions, overlay, mesh measurements, reading the preview |
| `references/weapon-forge.md` | weapon categories, sprites, EF capability, obtainability |
| `references/forge-1.20.1.md` | generated classes, wiring, build, MDK/JDK notes |
| `references/encounters.md` | summon item, command, natural spawn, hidden-boss design |
| `references/qa.md` | verification checklist and in-game checks |
| `references/sources.md` | source URLs |

Worked example: `assets/examples/king-arthur/boss.json` (major tier, longsword, three phases).
