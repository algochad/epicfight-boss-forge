# QA

`bossforge qa --project DIR` checks (hard errors fail the exit code): generated JSON parses; mobpatch passes the parser mirror (`validate_mobpatch`: required keys, faction, attribute types, living motions, stun keys, predicates, weapon categories, styles, every animation in the catalog); texture and sprite sizes match the plan (entity = authored UV x scale, weapons 32/16, `_gui` 16); every registered item has model, texture, lang key; every referenced lang key exists; no unreplaced `{{` placeholders in Java; Java file name equals the public class; braces/parens balanced; manifest hashes consistent; tier contract complete (lore, stats, phases, trigger resources, weapon obtainable by drop or craft); entity texture coverage >= 0.90; for `shape` bosses the custom mesh JSON (`animmodels/entity/<id>.json`) passes the structural validator (12 parts, index ranges, joint ids, winding), the mobpatch `model` equals `<mod>:entity/<id>`, and the texture size equals the generated atlas.

Compile gate (proves the Java templates): `cd DIR && ./gradlew --no-daemon compileJava processResources` with JDK 17. First run downloads Minecraft, Forge and mappings (minutes).

## In-game checklist (cannot be automated here)

1. Log has NO `Can't deserialize mob capability` line for the boss patch.
2. The held weapon is rendered in the right hand with the weapon hold pose and the EF moveset (longsword/tachi/greatsword poses).
3. Attacks play EF animations and respect reach; a heavy hit staggers the player.
4. Boss is staggered by strong hits per tier stun table (not immune unless intended).
5. Phase transitions: bar progress, growl sound, announce message, phase-gated moves (heavy from phase 2), abilities (summon once, regen, enrage).
6. Custom shape: the model renders (not invisible, not stretched), pieces stay attached while walking, attacking and dying; cape/wings/horns do not poke through the body badly; the texture is not mirrored or swapped (face on the front). For stock meshes: overlay layer visible; if not set `appearance.overlay_enabled: false`.
7. Golem template: renderer path works (`"minecraft:iron_golem"`).
8. Weapon: obtainable (drop/recipe), on-hit effect applies, and the boss spawns without an NPE (EF attributes registered).
9. Spawn: sigil on the required block, command, or natural spawn.

## Render test in a dev client

`bossforge render-test --project DIR` is the render gate: it runs the mod in a Forge dev client, photographs the boss from five angles and checks which mesh Epic Fight drew. The full process, mesh rules, symptom table and debug recipes are in `render-pipeline.md`.
