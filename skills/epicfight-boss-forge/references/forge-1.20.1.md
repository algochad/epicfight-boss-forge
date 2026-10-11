# Forge 1.20.1 project and generated code

Target: Minecraft 1.20.1, Forge 47.4.10 (fallback 47.4.4 if the MDK for 47.4.10 returns 404), Java 17, ForgeGradle 6, official mappings, Epic Fight 20.14.17. The `1.20.x` MDK branch is Java 21 / FG 7 and MUST NOT be used.

## new-mod

`bossforge new-mod --mod-id M --mod-name N [--group com.bossforge] --out DIR` downloads `https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-<ver>/forge-1.20.1-<ver>-mdk.zip` into the cache (or use `--mdk-zip`), then:

1. `gradle.properties`: sets `mod_id mod_name mod_version=0.1.0 mod_group_id=<group>.<M> mod_authors mod_description mod_license=MIT`, appends `epicfight_version=20.14.17-mc1.20.1-forge`.
2. `build.gradle`, located by regex anchors (never line numbers): after the column-0 `repositories {` inserts the Modrinth `exclusiveContent` block (group `maven.modrinth`), after `dependencies {` inserts `implementation fg.deobf("maven.modrinth:epic-fight:${epicfight_version}")`. A missing anchor fails with `mdk_layout_unexpected` and the first 40 lines. (In the real 47.4.10 MDK: `repositories {` line 114 and `dependencies {` line 125, both at column 0; the indented `repositories` inside `publishing` is left alone.)
3. `META-INF/mods.toml`: appends a `[[dependencies.${mod_id}]]` block for `epicfight` (`mandatory=true`, `versionRange="[20.14.17,)"`, `ordering="AFTER"`, `side="BOTH"`).
4. Deletes the template mod sources, writes `<Pascal>Mod.java` (calls `BossRegistry.register(modEventBus)`), the shared Java core, an empty `BossRegistry`, and `bossforge/manifest.json`.

## Existing mod

`add-boss` needs `gradle.properties` `mod_id` equal to the spec `mod_id` (`mod_id_mismatch`) and a class annotated `@Mod("<mod_id>")` (also via a `MOD_ID` constant; `no_main_class` otherwise). The Java package is that class's package. A mod without `bossforge/manifest.json` is adopted: the manifest is created and the shared core is written only for files that do not yet exist.

Wiring: the main class must contain `BossRegistry.register(<bus>);`. The generator finds `<var> = FMLJavaModLoadingContext.get().getModEventBus();` inside a constructor/method and inserts the call plus `import <pkg>.boss.BossRegistry;`. If the pattern is absent nothing is edited and the result has `wiring_required: true` with the exact line and imports.

Dependency check (warn only, never auto-edit): `epicfight_dependency_missing` prints the Gradle repository block, dependency line and mods.toml block to add.

## Generated layout

Under `<pkg>/boss/`: `BossRegistry` (regenerated every run from `bossforge/bosses/*.boss.json` and `bossforge/weapons/*.weapon.json`: entity types, items, spawn eggs, creative tab `bosses`), `BossModEvents` (attributes, EF attributes, spawn placement), `BossClientEvents` (renderers), `BossForgeEvents` (on-hit effects, weapon effects). `<pkg>/boss/core/`: `BossProfile AbilitySpec EffectSpec AbstractBossEntity BossAbilities BossTier BossWeaponItem BossSigilItem BossRenderer`. Per boss: `<pkg>/boss/<id>/<Cls>Entity.java`.

Behavior:
- `AbstractBossEntity extends Monster` (a `PathfinderMob`, required by EF's `CustomMobPatch`); only float/stroll/look/target goals, no melee goal (EF adds `AnimatedAttackGoal` and `TargetChasingGoal`); equips the main weapon in `finalizeSpawn` with 0 drop chance; boss bar if `show`; phase index stored in NBT `BossPhase`, phase change plays a growl, messages players within 64 blocks if `announce` is set, and triggers abilities.
- Abilities: `on_hit_effect` (applied in `LivingHurtEvent`), `aura` (every `interval` ticks to players in `radius`), `summon` (once per life on entering `phase`; skipped with a log line when the entity id is unregistered), `regen` (heal once per 20 ticks in listed phases), `enrage` (transient MULTIPLY_BASE modifiers on speed and damage).
- `BossModEvents` adds `EpicFightAttributes.WEIGHT ARMOR_NEGATION IMPACT MAX_STRIKES STUN_ARMOR` (plus offhand ones for humanoids) to each boss type. REQUIRED: EF registers them only on a hardcoded list of vanilla types and dereferences them without a null check.
- `BossRenderer` extends `HumanoidMobRenderer` with the vanilla zombie model layer; EF's patched renderer reads the texture from it and draws the held item. Armor layers are not added (bosses are painted).

## Ownership and regeneration

`bossforge/manifest.json` records the SHA-256 of every generated file. Rerun: identical = `unchanged`, untouched generated file = `updated`, hand-edited file = `conflicts` (nothing is written, exit 1) unless `--force`. Lang files are merged additively and never overwrite existing keys. The main class `<Pascal>Mod.java` is yours.

## Build

`cd DIR && ./gradlew --no-daemon compileJava processResources` with JDK 17 (`mise x java@temurin-17 -- ./gradlew ...`). First run downloads Minecraft, Forge, mappings. Run in game with `./gradlew runClient` (EF is on the dev classpath via the Modrinth Maven).

## API corrections found while writing the templates (Forge 1.20.1 javadoc)

- `HurtByTargetGoal`/`NearestAttackableTargetGoal` live in `net.minecraft.world.entity.ai.goal.target`.
- `SpawnPlacementRegisterEvent#register` is generic over `Entity`, so the generated code uses an unchecked `EntityType<Monster>` cast.
- `EntityType#spawn(level, null, null, ...)` is ambiguous; minions use `spawn(ServerLevel, BlockPos, MobSpawnType.MOB_SUMMONED)`.
- `Mob#finalizeSpawn` is deprecated (`@OverrideOnly`) in Forge 47; the override carries `@SuppressWarnings("deprecation")`.
- `ForgeRegistries.ENTITY_TYPES.getValue` returns a pig for unknown ids; lookups are guarded with `containsKey`.
- Generated Java uses `javax.annotation.Nullable` (jsr305 is on the Minecraft compile classpath).
