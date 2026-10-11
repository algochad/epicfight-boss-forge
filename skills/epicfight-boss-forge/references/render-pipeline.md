# Render pipeline: how a boss gets drawn, and the process to get it right

Read this before generating, changing or debugging how a boss looks. The rule: **a boss is not done until it has been rendered in a real client** (`bossforge render-test`). Specs, JSON validators and previews prove only that files are well formed; the first version of the custom shapes passed every offline check and still rendered as scrambled fragments in game.

## 1. What decides what is on screen

Five inputs must agree. Epic Fight (EF) draws the mesh chosen by the mobpatch `renderer`, with the pose from the armature, and the texture comes from the entity's own Java renderer.

| Input | Where | Must be |
|---|---|---|
| mobpatch `renderer` | `data/<mod>/epicfight_mobpatch/<id>.json` | stock biped: `"player"`; stock golem: `"minecraft:iron_golem"`; **custom mesh: `"epicfight:custom"` plus `"humanoid": true`** |
| mobpatch `model` | same file | only honoured by `epicfight:custom`: `<mod>:entity/<id>` -> `assets/<mod>/animmodels/entity/<id>.json`. `player` and the preset renderers IGNORE it |
| mobpatch `armature` | same file | `epicfight:entity/biped` (custom shapes are skinned to it) |
| `isHumanoid` and `humanoid` | same file | both true for the biped rig. EF's mobpatch parser reads `isHumanoid`; `epicfight:custom` reads `humanoid` to choose `PCustomHumanoidEntityRenderer` (item/armor/head layers, `HumanoidMesh`) over `PCustomEntityRenderer` |
| mesh JSON | `assets/<mod>/animmodels/entity/<id>.json` | see section 3 |
| texture | `assets/<mod>/textures/entity/<id>.png`, returned by the Java `BossRenderer#getTextureLocation` | UVs in the mesh index THIS image; any size |
| `attributes.scale` | mobpatch | `size.height / mesh height`; scales the mesh only, the hitbox comes from `EntityType.Builder.sized` |
| EF attributes | Java `BossModEvents` | added to the entity type or EF throws an NPE |

## 2. The process (follow in order)

1. **Write the spec** with a `shape` (silhouette, proportions, attachments, parts; `shape-forge.md`) and `appearance`.
2. `bossforge validate`, then `bossforge shape` (box count, bounds, atlas, warnings) and `bossforge skin` -> READ the five-panel preview. This judges the DESIGN (silhouette, palette, face), not the rendering.
3. `bossforge add-boss`. It writes the mesh JSON, texture, mobpatch (with the correct renderer), Java, resources, and runs `qa`. `qa` fails when a custom `model` is paired with a preset renderer, when the mesh JSON is structurally invalid, or when the texture size does not match the atlas.
4. **Compile gate**: `./gradlew --no-daemon compileJava processResources` (JDK 17).
5. **Render gate** (mandatory when a JDK 17 and a display exist): `bossforge render-test --project DIR [--boss ID]`. It copies the project, adds a throwaway test class, runs `./gradlew runClient`, joins a template world, summons the boss in open air and saves five screenshots (front, front-right, front-left, back, close-up) to `<project>/bossforge/render-test/`. It also prints `mesh_positions_seen` vs `mesh_positions_expected`: if they differ EF drew the wrong mesh. READ all five screenshots and judge them against the user's description. A render-test that did not run (`skipped`, no display or JDK) must be reported as "render not verified".
6. Only then ship the jar and tell the user what was verified: renders (screenshots), and what was NOT (attack/stun behavior, long-term animation, balance).

`render-test` needs a display (it opens a 640x360 Minecraft window for about two minutes), JDK 17, network the first time (Gradle downloads Minecraft/Forge/EF), about 2 GB of free RAM. It never touches the real project: it works in a temporary copy and the test class is not shipped.

## 3. Mesh JSON rules (custom shapes)

- `{"vertices": {positions, normals, uvs, vcounts, weights, vindices, parts}}`, each of the first six `{"array": [...]}`; `parts` = `{"<EF part>": {"array": [ints]}}`; no `armature` section.
- Positions and normals are stored in Blender Z-up space; EF rotates them -90 deg about X, so model space `(x, y, z)` (y up, feet at 0) is stored `(x, -z, y)`. The character faces -z; its right is +x.
- Each int triple in a part array is one vertex `(position index, uv index, normal index)`; each 3 vertices form a CCW triangle seen from outside. UVs: `u = px/W`, `v = row/H`, row 0 at the TOP of the image.
- `vcounts` has one entry per POSITION; `vindices` holds `(joint index, weight index)` pairs; `weights` is a flat list of unique weights. Joint indices are positions in the armature `joints` list (`assets/data/rigs.json`): Root 0, Thigh_R 1, Leg_R 2, Knee_R 3, Thigh_L 4, Leg_L 5, Knee_L 6, Torso 7, Chest 8, Head 9, Shoulder_R 10, Arm_R 11, Hand_R 12 (forearm), Tool_R 13, Elbow_R 14, Shoulder_L 15, Arm_L 16, Hand_L 17, Tool_L 18, Elbow_L 19.
- The skinning formula EF uses is `pose[joint] * inverseBind[joint] * vertex`; vertices must therefore be in the BIND pose space of the stock biped (the generator guarantees it).
- `HumanoidMesh` looks up exactly these 12 part names: `head hat torso jacket rightArm leftArm rightLeg leftLeg rightSleeve leftSleeve rightPants leftPants`. All 12 must exist and be non-empty (a missing one only logs an error, then breaks armor/overlay handling).
- Rigid weights (one joint, weight 1.0) are fine; limbs crease at bends instead of blending.

## 4. Verification ladder (what each level catches)

| Level | Tool | Catches | Does NOT catch |
|---|---|---|---|
| 1 | `qa`, `efmodel.check_mesh_json` | malformed JSON, bad indices, missing parts, winding, texture size, renderer/model mismatch | anything about how EF treats the data |
| 2 | load the mesh with EF's own `JsonAssetLoader` (maintainer harness: a `main` in a copy of the project calling `new JsonAssetLoader(InputStream, rl).loadSkinnedMesh((a,b,c,d) -> new HumanoidMesh(a,b,c,d))`) | parse errors, missing part keys, counts | which mesh the renderer picks |
| 3 | CPU skinning simulation with EF's `Armature.setPose`, `getPoseMatrices()`, `Joint.getToOrigin()` and `SkinnedMesh.getVertexPosition` | wrong joint indices, wrong bind space, exploded or collapsed bodies | renderer choice, GPU path |
| 4 | `bossforge render-test` | everything visible: wrong renderer, wrong texture path, UV errors, scale, attachment placement, held item size | in-fight behavior |

## 5. Symptom -> cause

| What you see | Cause | Fix |
|---|---|---|
| Stock humanoid shape, or a handful of floating textured fragments/slabs, texture looks magnified and mixed (steel next to chainmail next to cloth in one block) | the mobpatch `renderer` is `player` (or a preset), so EF draws the stock biped mesh and ignores `model`; the stock UVs index your custom atlas, most of which is transparent | `renderer: "epicfight:custom"` + `"humanoid": true` (the generator does this; `render-test` reports `mesh_positions_seen` = 260 instead of the custom count) |
| Boss invisible | texture path wrong (`getTextureLocation`), texture fully transparent, entity type not registered in `EntityAttributeCreationEvent`, or mobpatch rejected (log `Can't deserialize mob capability`) | check the log, run `qa` |
| Body collapsed into a small clump near the feet | poses are identity: skinning was applied without a pose (animator not running) or the mesh is not in bind-pose space | see level 3; confirm joint indices |
| Wrong colors / texture swapped or mirrored | UV origin or atlas mismatch (row 0 must be the top) | regenerate; compare with the skin preview |
| Boss far bigger or smaller than the hitbox | `attributes.scale` != `size.height / mesh height` | regenerate; `qa` checks it |
| `held_items` shows `minecraft:air` / render-test problem "not holding its main weapon" | the weapon item is not registered, or `finalizeSpawn` did not run. NOTE `/summon <id> x y z {NBT}` with any NBT SKIPS `finalizeSpawn` (vanilla only calls it for a bare summon), so test and quest commands must summon without NBT | summon without NBT (render-test does), check `BossRegistry` |
| Held weapon enormous | EF displays held items at 1.5x then multiplies by the entity scale | weapon `visual_scale` (`weapon-forge.md`) |
| Everything renders in the GUI but nothing in game | the resource pack path for the mesh is wrong: the file must be `assets/<mod>/animmodels/entity/<id>.json` for model `<mod>:entity/<id>` | regenerate |

## 6. Debug recipes (maintainers)

- Which mesh is EF using? Subscribe to `yesman.epicfight.api.client.forgeevent.PrepareModelEvent` (client Forge bus) and print `event.getMesh().positions().length / 3`: stock biped 260, stock iron golem 318, a custom shape its own count. This is exactly what `render-test` reports.
- Dev client mixin remap: EF ships an SRG refmap, so the `client` run needs `property 'mixin.env.remapRefMap', 'true'` and `property 'mixin.env.refMapRemappingFile', "${projectDir}/build/createSrgToMcp/output.srg"`, otherwise EF's mixins fail to apply and the client crashes.
- Screenshots: `net.minecraft.client.Screenshot.grab(mc.gameDirectory, mc.getMainRenderTarget(), consumer)` writes to `run/screenshots/`. Hide the GUI (`mc.options.hideGui = true`) and fly the camera with `/tp @s x y z yaw pitch` (creative flying) around a `NoAI` `NoGravity` boss summoned in open air.
- The compute-shader skinning path (GPU) and the CPU path use the same pose matrices; the shader multiplies `poses[joint]` by a per-part transform that is identity for these meshes.
