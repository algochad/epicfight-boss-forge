# Sources

Epic Fight (branch 1.20.1, https://github.com/Epic-Fight/epicfight/tree/1.20.1; pinned ref in `assets/data/versions.json`):
- `src/main/java/yesman/epicfight/api/data/reloader/MobPatchReloadListener.java` (mobpatch parser)
- `.../gameasset/MobCombatBehaviors.java` (presets)
- `.../gameasset/Animations.java` (animation catalog)
- `.../world/entity/ai/attribute/EpicFightAttributes.java` (attributes added per entity type)
- `.../client/events/engine/RenderEngine.java` (renderer keys)
- `.../world/capabilities/entitypatch/mob/CustomMobPatch.java`, `HumanoidMobPatch.java`
- `.../world/entity/ai/attribute` and `HealthPoint.java` (health predicate)
- Mesh assets: `src/main/resources/assets/epicfight/animmodels/entity/<name>.json`; mesh format read from `yesman.epicfight.api.asset.JsonAssetLoader`, `Meshes` and `HumanoidMesh` in the 20.14.17 jar (custom meshes are validated by loading them with the real `JsonAssetLoader`)
- Docs repo https://github.com/Epic-Fight/epic-fight.github.io: `docs/Guides/Entities/page1.en.md`, `docs/Guides/Weapons/page1.en.md`, `docs/API/Starting.en.md`
- Blender exporter https://github.com/Epic-Fight/blender-json-exporter
- Gradle dependency: Modrinth Maven `maven.modrinth:epic-fight:20.14.17-mc1.20.1-forge`

Forge 1.20.1: MDK https://github.com/MinecraftForge/MinecraftForge/tree/1.20.1/mdk, `ForgeBiomeModifiers.java`, javadoc https://lexxie.dev/forge/1.20.1/.

Packaging: https://agentskills.io/specification, https://github.com/vercel-labs/skills, https://code.claude.com/docs/en/plugins/marketplace-reference.

Prior art (ideas only): https://github.com/sosadly/blockbench-mcp (layered modeling doctrine, review gate), https://github.com/chouzz/minecraft-mod-dev (Claude plugin packaging), https://github.com/AIKUSAN/minecraft-agent-skills-bundle (router + dual layout). Mod references (read-only, licenses differ, never copy code): Mowzie's Mobs, Bosses of Mass Destruction.
