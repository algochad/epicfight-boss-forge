---
name: epicfight-boss-forge
description: "Design and generate Epic Fight-native boss content for Forge 1.20.1 mods: boss spec (size in blocks, lore, stats, tiers major/quest/mini/hidden), custom boss shapes and silhouettes (proportions, horns, wings, cape, tail, crown, armor, free-form boxes on the Epic Fight rig), procedural boss skin/texture generator, boss weapon generator (item sprites, Epic Fight weapon capability, loot and recipes so players can obtain it), Epic Fight mobpatch movesets with phases, and Forge Java/resource scaffolding for a new mod or an existing one. Use when the user wants to create a boss, mini boss, quest boss or hidden boss, a unique boss model or look, a boss skin or texture, a boss weapon, an Epic Fight mobpatch or combat pattern, a summon item or boss encounter, or to add a boss or weapon to a Forge 1.20.1 Epic Fight mod."
license: MIT
compatibility: "Python 3.9+ standard library only. Needs network once to fetch Epic Fight meshes and the Forge MDK (or pass --ef-jar/--mdk-zip). JDK 17 only to compile generated mods. Targets Forge 1.20.1 with Epic Fight 20.14.x."
metadata:
  version: "0.1.0"
  target: "forge-1.20.1 epicfight-20.14.17"
---

# Epic Fight Boss Forge

Generates a complete Epic Fight-native boss for a Forge 1.20.1 mod from one JSON spec: a UNIQUE boss shape (custom Epic Fight mesh built from proportions, presets and boxes, skinned to the EF biped rig so all EF animations work) with a painted texture, weapon items (sprites, EF capability, loot, recipes), the EF `mobpatch` moveset with health phases, and the Java/resources that register it. Commands are run from the skill root as `python3 scripts/bossforge.py <command>` (written `bossforge` below). All commands are non-interactive and print one JSON object (`{"ok":true,...}` or `{"ok":false,"errors":[{path,code,message}]}`); exit 0 ok, 1 failure, 2 usage.

## Use when / do not use

- Use for: bosses, mini/quest/hidden bosses, boss skins/textures, boss weapons, EF mobpatches, summon items/encounters, adding any of these to a Forge 1.20.1 mod that depends on Epic Fight.
- Do NOT use for NeoForge, Fabric, or other Minecraft versions: stop and say it is unsupported.
- Do NOT use for GeckoLib bosses or bosses that need their own skeleton/animations (quadrupeds, dragons, spiders, flapping wings). Those need a Blender rig plus the EF exporter (https://github.com/Epic-Fight/blender-json-exporter); see `references/epicfight-patch.md` "custom armatures". This skill builds humanoid shapes on the EF biped rig (any proportions, horns, wings, capes, tails, extra static limbs as rigid parts) and also supports the stock `iron_golem` mesh.

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
| appearance text | `appearance.archetype` + `palette` + `regions` + `features` (face, emblem, trim, wear) | archetype `knight` |
| body shape / silhouette: build, head gear, back piece, tail, arms, asymmetry | `shape.silhouette` + `shape.proportions` + `shape.attachments` + `shape.parts` (read `references/shape-forge.md`) | silhouette from the archetype |
| fighting style / skills | `moves`, `phases`, `abilities` | tier defaults |
| main weapon (name, category, look, how players get it) | `weapons[]`, `main_weapon`, `obtain` | none (fist moves) |
| how it appears | `encounter.trigger` | tier default |
| mod target | new (`mod_id`, `mod_name`, `--group`) or existing project path | required |

### The user gives a reference image or character sheet

Treat it as the acceptance test, not as inspiration. Read the image, then write a numbered checklist of every visible feature (proportions, head and hair, face, each garment and its trim colours, back pieces such as wings or capes, footwear, held items, small props, glow accents) and map each item to a spec field (`shape.attachments`, `shape.parts`, `appearance.features`, weapon `appearance`). After every `skin` preview, re-read the reference and the preview together and tick the checklist item by item; list what still differs and fix it by changing the spec or by improving the generator (a missing preset, material or feature is a generator gap to close in `scripts/bossforge_core/`, not a reason to ship a simplified boss). Say plainly which items can only be approximated (an exact imported model is not possible: the boss is boxes on the Epic Fight biped rig, painted by the generator). Only ask the user to approve once the checklist is ticked or the remaining differences are listed. See `references/intake.md` "Reference images".

Size note: Minecraft hitboxes are square, so `size.width` is both width and depth, and `size.height` is the TOTAL height including crown/horns (it sets the mesh `scale`; the hitbox comes from the entity type). With a `shape` block the body is a custom mesh on the EF biped rig; without one it is a stock EF mesh (`biped`, or `iron_golem` for wide weaponless brutes via `body.template`).

## Workflow (copy this checklist and tick it)

1. [ ] Write `boss.json`: `bossforge init --id ID --name NAME --mod-id MOD --tier TIER [--height H --width W --archetype A --silhouette S --weapon-category C --weapon-name N] --out boss.json` (adds a `shape` from the archetype's silhouette; `--silhouette none` = stock mesh), then edit lore/appearance/weapons/moves.
2. [ ] DESIGN THE SHAPE from the user's description (`references/shape-forge.md`): choose a silhouette, 2-3 proportion overrides, 2-4 signature attachments, free-form `parts` for anything unique. A generic boss is a failure: it must read as this character from the side and from behind. `bossforge list silhouettes|presets` shows the options.
3. [ ] `bossforge validate boss.json` until `ok:true`. Errors list the exact JSON pointer and allowed values.
4. [ ] `bossforge skin boss.json --out /tmp/bf-out`, then READ `/tmp/bf-out/bossforge/previews/<id>_preview.png` (front, three-quarter, side, back, texture) with the image reader. Iterate on `shape`, `palette`, `regions`, `features`, `shape.texture.density` (48 for a crisp face).
5. [ ] Review gate (below). Weapons: `bossforge weapon boss.json --out /tmp/bf-out` and read `<id>_weapons.png`.
6. [ ] New mod: `bossforge new-mod --mod-id MOD --mod-name "Name" --out DIR`. Then `bossforge add-boss boss.json --project DIR`.
7. [ ] Compile gate when a JDK exists: `cd DIR && ./gradlew --no-daemon compileJava processResources` (needs JDK 17).
8. [ ] RENDER GATE (never skip): `bossforge render-test --project DIR [--boss ID]` runs the real mod in a Forge dev client (needs a display, JDK 17, ~2 GB RAM, first run downloads Minecraft) and saves five screenshots to `DIR/bossforge/render-test/`. `ok` must be true (`mesh_positions_seen` equals `mesh_positions_expected`), then READ every screenshot and compare with the user's description. Offline checks and the skin preview do NOT prove the boss renders; a boss that was not rendered must be reported as "render not verified". Process and failure table: `references/render-pipeline.md`.
9. [ ] Report: files written, warnings, `wiring_required`, what was verified (compile, render screenshots) and what is unverified in game (see `references/qa.md`).

`add-boss` is idempotent. Re-running reports `unchanged`; files you edited by hand are listed under `conflicts` and kept unless `--force`. Specs are copied to `<project>/bossforge/bosses/<id>.boss.json`; edit that and re-run to regenerate. Lang files are merged: existing keys are never overwritten.

## Review gate

You cannot judge your own visual work. After `skin`: open the preview image, describe what is visible (face on the FRONT panel, silhouette from the side and back, palette, emblem, armor, glowing parts, empty areas), judge it honestly against the user's description ("is this a king, or a generic armored guy?"), then ask the user to approve or request changes BEFORE `add-boss`. A missing answer is not approval. Never hand-edit PNGs or mesh JSON; change the spec and regenerate. Weapons: read `<id>_weapons.png` the same way. Repeat the same judgment on the `render-test` screenshots, which show the boss as the game really draws it.

## Gotchas (verified from Epic Fight 1.20.1 / Forge 1.20.1 source)

- EF reads attribute numbers by NBT type: `stun_armor 15.0`, `impact 3.0`, `armor_negation 15.0`, `attack_damage 14.0`, `scale 1.5` MUST be floats and `max_strikes 3` an int, or the value is silently ignored (generator guarantees this; keep it when hand-editing).
- Humanoid `combat_behavior.weapon_categories` must be a JSON list.
- A modded boss MUST have EF attributes added in `EntityAttributeModificationEvent` or EF throws an NPE (generated `BossModEvents` does it).
- The entity must extend `PathfinderMob` and must not add melee goals; EF adds `AnimatedAttackGoal` and `TargetChasingGoal`.
- Renderer choice decides which mesh is drawn. `renderer: "player"` ALWAYS draws the stock biped mesh and ignores the mobpatch `model`; `"minecraft:iron_golem"` the stock golem. A custom mesh (a `shape` boss) needs `renderer: "epicfight:custom"` plus a boolean `"humanoid": true` key (EF reads `humanoid`, not `isHumanoid`, to pick `PCustomHumanoidEntityRenderer`). The generator writes this; `qa` rejects a custom `model` with a preset renderer (it would silently render the stock mesh with your texture scrambled across it).
- `scale` resizes the mesh only; the hitbox comes from `EntityType.Builder.sized`.
- One unresolvable animation key makes EF skip the whole patch with only a log warning (`Can't deserialize mob capability`). `qa` checks every key against the catalog.
- A stun type with no bound animation means the mob is immune; stun time reduction is `stun_armor/(stun_armor+7.5)`.
- The weapon needs `data/<mod>/capabilities/weapons/<id>.json` or EF treats it as fists/one-hand tools.
- Phases are `health` predicates, never `branch_N` (resolved once at patch creation).
- 1.20.1 data paths: `loot_tables/`, `recipes/`, `advancements/`, `functions/`, `forge/biome_modifier/`.
- The MDK must be the 1.20.1 one (Java 17, ForgeGradle 6), not the `1.20.x` branch (Java 21).
- EF meshes are fetched from GitHub into the cache (GPL): never commit them to a project.
- Overlay-layer armor and the golem renderer path cannot be verified offline: say so in the report. If the overlay does not render in game set `appearance.overlay_enabled: false`.
- A `shape` boss uses a custom EF mesh `assets/<mod>/animmodels/entity/<id>.json` referenced by the mobpatch `model` (`<mod>:entity/<id>`) with the stock `epicfight:entity/biped` armature; the mesh must contain the 12 EF part names (generated). Shape pieces are rigid and static (no cloth or wing animation). The generated King Arthur mesh was verified loading with EF's own `JsonAssetLoader` and rendering correctly in a Forge dev client (screenshots); attack/stun behavior still needs a play test.
- EF scales held items with the entity `scale` on top of its own 1.5x display scale: big bosses get huge weapons. The generator auto-shrinks the main weapon's hand display above scale 1.2; tune with weapon `visual_scale` (0.4-1.5).

## Command reference

| Command | Purpose |
|---|---|
| `list tiers\|archetypes\|templates\|materials\|features\|abilities\|weapon-categories\|series\|silhouettes\|presets\|animations [--prefix P]` | valid values |
| `init --id --name --mod-id --tier ... [--silhouette S] --out FILE` | valid starter spec (with a shape) |
| `validate SPEC` | spec check (boss or weapon spec) |
| `fetch-ef-assets [--ef-jar JAR] [--ref SHA]` | cache EF meshes |
| `mesh-info --template biped\|iron_golem` | mesh diagnostics |
| `skin SPEC --out DIR [--mesh-file P]` | texture + preview (builds the custom mesh for `shape` bosses) |
| `shape SPEC --out DIR` | build only the custom EF mesh JSON; prints boxes, atlas, bounds, warnings |
| `weapon SPEC (--out DIR \| --project DIR) [--essence-item ns:id] [--force] [--dry-run]` | weapon files, or add a weapon to a mod |
| `patch SPEC --out DIR` | EF mobpatch JSON only |
| `new-mod --mod-id --mod-name --out DIR [--group G --author A --description D --mdk-zip Z --forge-version V]` | Forge 1.20.1 + EF project |
| `add-boss SPEC --project DIR [--force] [--dry-run] [--ef-jar JAR]` (alias `build`) | everything for one boss |
| `render-test --project DIR [--boss ID] [--out DIR] [--timeout S] [--keep]` | render the boss in a real Forge dev client, save 5 screenshots, verify which mesh EF drew |
| `lore SPEC --out DIR` | design doc |
| `qa --project DIR` | project checks |
| `catalog --ef-ref SHA --out FILE` | maintainer: rebuild animation catalog |

Global flags: `--cache-dir DIR` (default `$BOSSFORGE_CACHE` or `~/.cache/epicfight-boss-forge`), `--offline`.

## Read more only when needed

| File | Read when |
|---|---|
| `references/shape-forge.md` | designing the boss's unique body: silhouettes, proportions, attachments (crown, horns, wings, cape, tail...), free-form boxes |
| `references/intake.md` | turning the user's words into spec fields (shape, face, glow, heavy/slow, heals, minions) |
| `references/boss-spec.md` | field table, defaults, validation codes, examples |
| `references/tiers.md` | choosing/tuning a tier |
| `references/epicfight-patch.md` | moveset, phases, stun math, presets, custom armatures |
| `references/skin-forge.md` | materials, features, regions, overlay, mesh measurements, reading the preview |
| `references/weapon-forge.md` | weapon categories, sprites, EF capability, obtainability |
| `references/forge-1.20.1.md` | generated classes, wiring, build, MDK/JDK notes |
| `references/encounters.md` | summon item, command, natural spawn, hidden-boss design |
| `references/render-pipeline.md` | how a boss is drawn (renderer/model/mesh/texture), the render gate process, symptom -> cause table, debug recipes |
| `references/qa.md` | verification checklist and in-game checks |
| `references/sources.md` | source URLs |

Worked examples: `assets/examples/king-arthur/boss.json` (major tier, custom knight shape with crown, tabard, cape and kite shield, longsword, three phases) and `assets/examples/nightingale/boss.json` (a complex character built from a reference sheet: `seraph` wings, `hairdo`, `ribbons`, `pouch`, slit robe with `filigree`/`gradient`/`repaint` painting, anime face, 64px `crystal_staff`; copy its structure for any detailed humanoid).
