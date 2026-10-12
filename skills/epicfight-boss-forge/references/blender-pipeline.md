# Blender pipeline: model and skeleton in Blender, texture from the generator

A boss body can be BOXES (the `shape` block, `shape-forge.md`) or a real MESH modelled in Blender and skinned smoothly to the Epic Fight (EF) biped rig. Blender is used for the 3D model, the skeleton, the weights and the UV layout, and for inspecting the result textured and posed. The texture is still produced by the bossforge painter (`skin`, optionally `reference.bake`); the generator stays the texture source and the verifier (`fit`, `qa`, the render gate). Everything in this document was run against Blender 5.2.1 LTS (Python 3.14 inside Blender); the Blender-side code needs the Blender 4.1+ mesh API (custom normals as an attribute, `Mesh.corner_normals`, `normals_split_custom_set`).

Commands are run from the skill root as `python3 scripts/bossforge.py blender <command>` (written `bossforge blender ...`). All print one JSON object; exit 0 ok, 1 failure, 2 usage.

| Command | What it does |
|---|---|
| `blender doctor` | find Blender, headless smoke test (rig, mesh API, 64x64 Workbench render), engines, informational MCP port probe |
| `blender rig --out FILE.blend [--name EF_Biped]` | new .blend containing only the EF biped armature |
| `blender import SRC --out FILE.blend [--texture PNG] [--name N] [--id ID] [--no-custom-normals]` | EF mesh bundle / EF mesh JSON -> .blend (rig, weights, UVs, normals, material slots, texture) |
| `blender export BLEND --out DIR [--object N] [--rig N] [--id ID] [--max-influences N] [--strict]` | .blend -> bundle (`<id>.efmesh.json` + `<id>.meshmeta.json`), validated |
| `blender view ...` | render the textured model from several angles, posed (section "Viewing and posing") |

Global option: `--blender PATH` (otherwise `$BLENDER`, `PATH`, `/usr/bin/blender`, flatpak/snap/macOS/Windows install locations).

## 1. Install and doctor

1. Install Blender 4.2 or newer (Arch: `pacman -S blender`; elsewhere blender.org; flatpak/snap work). No add-ons are needed and nothing is installed into Blender.
2. `bossforge blender doctor`. Expected: `"ok": true`, `checks.rig/mesh_api/bossforge_core/render` all `ok`, `engines` containing `BLENDER_WORKBENCH` (read from the TypeError Blender raises for an invalid engine, never hardcoded: names differ between versions; `BLENDER_EEVEE_NEXT` does not exist in 5.x), `path`, `blender_version`. `mcp` reports whether a live Blender with the blender-mcp add-on listens on 127.0.0.1:9876. That is informational: the pipeline never needs it.
3. If doctor fails: `blender_missing` (install or pass `--blender`), `blender_failed` (the message carries the tail of Blender's output), `blender_timeout`, `blender_no_result`.

Every Blender-side task is its own headless process: `blender -b --factory-startup [BLEND] --python scripts/blender/_bootstrap.py` with the task script in env `BFK_SCRIPT` and its arguments as JSON in `BFK_ARGS`; the script prints one `BFK_RESULT {json}` line (`bossforge_core.blender.run`). Processes share nothing, so any number can run in parallel. All paths handed to a task must be absolute (the process runs in a temp directory).

## 2. When to use `shape` boxes and when a Blender mesh

| Need | Use |
|---|---|
| Blocky silhouette, quick iteration, many bosses, no Blender available | `shape` boxes (`shape-forge.md`): presets, attachments, relief, rigid weights, one `skin` call |
| Round forms, a face that has to read, cloth that bends with the legs, hair/ribbons/wings that follow the body smoothly, a model that must match a concept sheet closely | `mesh` from Blender (this document) |
| A model an artist will refine by hand later | `mesh`: open the .blend, edit, `blender export`, done |
| Stock vanilla biped or iron golem | neither (`shape`/`mesh` omitted) |

The two are mutually exclusive in a spec (`mesh_and_shape`), and `mesh` cannot be combined with `body.template: iron_golem`. Both end in the same thing: an EF mesh JSON skinned to the EF biped, so all EF animations and movesets work.

A box model can be moved into Blender and refined: `bossforge shape SPEC --out DIR`, then `blender import DIR/.../<id>.json --out m.blend` (rigid weights, one `skin` slot), edit, export.

## 3. Coordinates and conventions

Two spaces exist. Mixing them is the usual bug.

| | y-up model space | storage space = Blender world space |
|---|---|---|
| used by | `rigs.json`, `shape` boxes, spec sizes, the preview renderer | the EF mesh JSON, the .blend, `bfk.rig`, every Blender view |
| up | +Y | +Z |
| character faces (front) | -Z | +Y |
| character's right hand | +X | +X |
| feet / floor | y = 0 | z = 0 |
| unit | 1 block | 1 block (Blender unit) |
| conversion | `(x, y, z) -> (x, -z, y)` (`efmodel.to_blender`), inverse `(x, y, z) -> (x, z, -y)` | the game rotates stored positions by -90 degrees about X |

```
Blender, seen from above (Z toward you)        Blender, camera at +Y (the character's front view)

              +Y  = FRONT, the face looks here          Z
               ^                                        ^   head       z 1.514 .. 2.0
               |                                        |   chest      z 1.114
  -X  <--------+-------->  +X                           |   shoulders  z 1.394 (arms at x +-0.375)
  (character's left)  (character's right)               |   hip        z 0.764
               |                                        +------> +X   feet z = 0
              -Y  = BACK                          (the character's right hand is on the LEFT of the image)
```

Blender's numpad "front" view (camera at -Y) therefore shows the BACK of an EF model. Use `bfk.rig.view_front_camera()` (camera at +Y looking along -Y) or the `blender view` command. `azimuth` +90 puts the camera at -X (the character's left side), -90 at +X, 180 behind.

UV: the JSON measures `v` from the TOP row of the image (`v_ef = 1 - v_blender`); Blender's `v` is bottom-up. Import and export do the flip; do not flip anything by hand. The texture is a `size x size` PNG (power of two, default 512) that the UVs cover in 0..1.

Rest pose: the EF biped bind pose (T-pose with the arms down along the body, `rigs.json`). The model height (`meshmeta.height`) = max z - min z in blocks; the spec's `size.height` divides by it to get the entity scale, exactly as for shapes.

## 4. The rig: `EF_Biped`

`bossforge blender rig --out rig.blend` (or `bfk.rig.build_rig()`) creates the armature object `EF_Biped` at the origin (identity transform, custom property `bossforge_rig = "epicfight:entity/biped"`). 20 bones named exactly like the EF joints, in joint order (the vertex group index is the joint index), parents as in EF's `biped.json` hierarchy, bone HEADS exactly on the joint positions of `assets/data/rigs.json` converted to storage space. Bone tails point to the first child joint (`Root` points to `Torso`); leaf joints get a 0.05 block bone continuing the parent's direction; no bone is connected and bone roll is Blender's default 0.

| idx | joint | parent | rest position (storage x, y, z) | role |
|---|---|---|---|---|
| 0 | `Root` | - | (0.000, 0.001, 0.764) | pelvis; moves everything (bob, lean) |
| 1 | `Thigh_R` | `Root` | (0.125, 0.001, 0.761) | right hip: thigh |
| 2 | `Leg_R` | `Thigh_R` | (0.125, 0.001, 0.386) | right knee: shin, foot |
| 3 | `Knee_R` | `Thigh_R` | (0.125, 0.001, 0.386) | helper at the knee |
| 4-6 | `Thigh_L`, `Leg_L`, `Knee_L` | | mirrored (x = -0.125) | left leg |
| 7 | `Torso` | `Root` | (0.000, 0.001, 0.814) | waist: belly, lower back |
| 8 | `Chest` | `Torso` | (0.000, 0.001, 1.114) | chest, upper back |
| 9 | `Head` | `Chest` | (0.000, 0.001, 1.514) | neck pivot: head, hair, crown |
| 10 | `Shoulder_R` | `Chest` | (0.000, 0.001, 1.514) | root of the arm chain, at the neck |
| 11 | `Arm_R` | `Shoulder_R` | (0.375, 0.001, 1.394) | right shoulder: upper arm |
| 12 | `Hand_R` | `Arm_R` | (0.375, 0.001, 1.094) | right ELBOW: forearm and hand (bossforge bone `forearm_r`) |
| 13 | `Tool_R` | `Hand_R` | (0.375, -0.029, 0.822) | right WRIST: hand, held item (bossforge bone `hand_r`) |
| 14 | `Elbow_R` | `Arm_R` | (0.375, 0.001, 1.094) | helper at the elbow |
| 15-19 | `Shoulder_L`, `Arm_L`, `Hand_L`, `Tool_L`, `Elbow_L` | | mirrored (x = -0.375) | left arm |

Rules for the rig:
- NEVER move, rename, add or delete bones. EF binds the mesh to exactly this armature; `export` fails with `extra_bones`/`missing_bones` and warns `rig_moved` when heads deviate. Edit the mesh, not the skeleton.
- Bone-local axes are Blender's, not EF's joint frames (EF joint rotations are expressed in model-aligned axes). To pose a joint by an angle use `bfk.rig.pose_bone_world_rotate(arm_obj, "Arm_R", "X", 60)` (world axes about the joint, composes on top of the current pose, children follow; `reset_pose(arm_obj)` clears). Verified numerically (section 9).
- Helpers: `joint_names()`, `joint_index(name)`, `joint_name(i)`, `bone_for_bossforge_bone("arm_r") -> "Arm_R"`, `joint_head(name)`, `find_rig(name=None)`, `rig_report(arm_obj)`, `evaluated_world_positions(mesh_obj)`, `view_front_camera(...)`.

## 5. Building the model

- Model in storage space on the rig: the character faces +Y, stands on z = 0, arms down at the sides (arms at x = +-0.375 are the stock bind positions: model them around those, a 0.25-0.5 block thick arm is typical), legs at x = +-0.125. Match the EF proportions (hip 0.76, shoulders 1.39, neck 1.51, head top about 2.0) or scale the whole model; `size.height` then fixes the final size.
- Apply all transforms (`Ctrl+A`): the exporter bakes object transforms relative to the rig (warning `transform_baked`) but modifiers that depend on scale misbehave. Model only the visible hull (no hidden inner faces), keep faces facing outward (Mesh > Normals > Recalculate Outside); the exporter warns `flipped_triangles`.
- Quads are fine (triangulated on export: warning `triangulated` is informational). All modifiers except Armature are applied on export, in the rest pose, also when the object is hidden (tested with Array; Mirror, Subdivision, Solidify and Bevel go through the same evaluated-mesh path). Loose vertices/edges are dropped (`loose_geometry`).
- Smooth shading and custom normals are exported as the vertex normals EF lights with. Hard edges (sharp edges, flat faces) become split normals, so a faceted look costs no extra vertices in the file. Normals survive Blender's storage as int16 pairs (round trip error <= 2.5e-4, measured).
- One mesh object is exported per `export` call (`--object NAME`, otherwise the one that has an Armature modifier on the rig; several candidates give `ambiguous_object`). Join parts first with `Ctrl+J` (or `bpy.ops.object.join()`); materials and vertex groups merge by name. Model only what is visible: every triangle is drawn.
- Keep the count honest. The stock biped is 472 triangles. The exporter handled 20 480 triangles in 0.8 s; EF has no hard limit but every triangle is skinned every frame for every boss in view; stay in the low thousands and judge in the render gate.
- Empty parts: EF requires 12 non-empty parts. The exporter fills the unused ones with one degenerate triangle on the anchor joint (as the generator does). They are invisible and are not imported back.
- EF part (`head`, `torso`, `rightArm`, ... `hat`, `jacket`): each triangle goes to the part of its dominant joint (Head -> `head`, Root/Torso/Chest -> `torso`, Shoulder/Arm/Hand/Tool/Elbow R/L -> `rightArm`/`leftArm`, Thigh/Leg/Knee -> legs); overlay parts stay empty. The face attribute `bf_part` (1-based index into `efmodel.EF_PARTS`, 0 = decide by joint) keeps the original part of imported meshes so a round trip is lossless; faces you add default to 0. Part choice does not affect how a boss renders (no armor layers); the overlay parts are not used for bosses.

## 6. Weights

Vertex groups named exactly like EF joints carry the weights (groups with other names are ignored with warning `ignored_groups`). `import` creates all 20 groups. Weights are normalised per vertex on export.

Limits and rules:
- At most `--max-influences` joints per vertex (default 4; the smallest are dropped with warning `influences_trimmed`; weights below 0.001 are dropped; the rest are renormalised and rounded to 4 decimals). EF itself has NO cap: the loader reads `vcounts` of any size and both skinning paths (CPU `SkinnedMesh.getVertexPosition`, the compute shader `vanilla_mesh_transformer.comp`) loop over all influences (checked in the 20.14.17 jar). The stock biped uses at most 3 (measured: it blends `Torso`/`Chest` on 60 of its 260 vertices, 4 more vertices blend `Head`/`Shoulder_*` or `Thigh_L`/`Leg_L`, everything else is rigid), the golem at most 2.
- Every vertex needs a weight. Unweighted vertices (warning `unweighted`, error with `--strict`) are bound to the average of their weighted neighbours (isolated ones to `Root`): fix them in Blender instead.
- The deformation is `pose[joint] * inverseBind[joint] * vertex` per influence (linear blend skinning, no dual quaternion, no corrective shapes). Big angle differences between two joints that share a vertex produce the usual candy-wrapper collapse; keep blend zones short and wide enough to cover the bend.

What EF cannot do (do not design for it):
- No extra bones: the skeleton is the 20 joints above. Hair, ribbons, capes, skirts, wings, tails, ears have NO bones of their own.
- No physics, cloth, jiggle, secondary motion. Anything that must move beyond following a joint is impossible; model it in a pose that reads well at rest and under the stock walk/attack animations.
- Wings do NOT flap: nothing animates a wing joint because there is none. Wings follow `Chest`/`Torso` (lean, twist, bob) only.
- No shape keys, no per-part visibility animation, one texture, no animated UVs.

What a good weight map looks like on this rig. These are skinning guidelines for the EF biped (linear blend, 20 joints), not measurements; the section "Building the Nightingale example" records what was actually built and measured. All values are joint weights; "ramp" means a smooth gradient over about 0.2-0.3 blocks, painted in Weight Paint mode or computed from a vertex coordinate:

| Part | Weights |
|---|---|
| Torso / robe bodice | `Torso` below the waist (z < 1.1), `Chest` above; ramp between z = 0.9 and 1.3 (like the stock mesh); arms start at the shoulder seam |
| Arm | `Arm_*` for the upper arm, `Hand_*` (the elbow joint) for the forearm, `Tool_*` for the hand; ramp +-0.12 blocks around the elbow (z 1.094) and the shoulder (z 1.394), never a hard step; the armpit side of the shoulder ramps into `Chest` |
| Leg | `Thigh_*` above the knee, `Leg_*` below; ramp +-0.12 around z = 0.386; the crotch area ramps into `Root` |
| Skirt / long robe / tabard | no skirt bones: the skirt must follow the legs. Waistline rings (z about 0.8-0.9): `Torso`/`Root`. Below the hip, weight every skirt vertex to the THIGH on its own side of the centre line (x < 0 -> `Thigh_L`, x > 0 -> `Thigh_R`), and near the centre plane (|x| < 0.1) split 50/50 between both thighs so front and back panels split cleanly when a leg lifts. Hem vertices below the knee keep the same thigh weights (not `Leg_*`, the skirt hangs from the hip) or ramp into `Leg_*` only if the hem is tight. Slit skirts: independent panels each fully on one thigh. Fade from `Torso` at the waist to the thighs over 0.15 blocks |
| Cape / cloak | `Chest` at the shoulder line, ramp to `Torso`/`Root` down the back (z 1.4 -> 0.9), then `Root` plus a bit of `Thigh_*` toward the hem. Keep it out of the arms' swing volume or it intersects when the arms rise |
| Wings (angel/bat/butterfly) | the root edge at the shoulder blades fully `Chest` (or 70/30 `Chest`/`Torso`); the whole wing stays `Chest` 1.0 (rigid) or ramps to `Torso` at the lowest feathers. They do not flap; keep them from the arms' swing volume |
| Long hair | cap and top locks `Head` 1.0; locks hanging past the neck ramp `Head` -> `Chest` over the first 0.3 blocks below the neck (z 1.5 -> 1.2); the ends fully `Chest` (or `Chest`/`Torso`) so the hair hangs with the body instead of turning with the head. No hair bones: no swing |
| Ribbons / sash / trailing cloth | attach point carries the weight of the body part it hangs from (hip ribbon: `Torso`/`Root`; shoulder ribbon: `Chest`); the free ends keep the same weights (a rigid streamer that follows that joint) |
| Head details (face, horns, crown, ears, hat) | `Head` 1.0. The neck is the only blend zone: ramp the lowest ring `Head` -> `Chest` |
| Belt, pouch, tassets | `Torso` (or `Thigh_*` for tassets hanging from the hip like a skirt) |
| Held props that are part of the mesh | weight to the hand joint `Tool_*` 1.0; real weapons are not part of the body mesh (the weapon capability attaches them to the hand) |

## 7. UVs and the atlas

- The texture is ONE image shared by all groups, `size x size`, a power of two between 64 and 2048 (default 512). The UVs of the whole mesh must lie in 0..1 (warning `uv_out_of_range`; Minecraft does not tile entity textures). Missing UVs are an error (`no_uv`).
- Uniform texel density: one texels-per-block value for the whole body. The box generator uses 32 texels per block (`shape.DEFAULT_DENSITY`; 48 gives a crisp face; range 8-64): the head face (0.5 x 0.5 blocks) is then 16 x 16 texels (24 x 24 at 48). Do not give the back of the skirt a bigger share than the face because it is bigger. Recommendation (not a measured rule): a face that has to read wants 1.5-2x the density of the body, and a visible density jump is acceptable only there.
- Margin: the painter extends every island outward by `mesh.texture.padding` texels (default 4, range 0-16) so mipmaps stay clean; leave at least that distance between islands and to the image border. It paints per UV triangle, so geometry that has no UV area stays unpainted (`mesh_uv_degenerate`).
- No overlapping islands, mirrored ones included: the painter colours a texel from the last triangle that covers it, so the other copies get wrong shading and features (`mesh_uv_overlap` warns above 2% shared texels). Unwrap every part into its own space; mirrored halves are modelled twice in UV space. Straight, axis-aligned islands for faces and flat cloth give the painter clean grids for patterns, trim and text; unwrap cylinders (arms, legs, hair strands) as straight strips.
- Pick the atlas size by what must read: 512 for a normal boss, 1024 for a face with detail and filigree. `mesh.texture.size` in the spec must equal the real texture size.
- Mark seams where the texture is allowed to change (under the arms, along the back of the head), not across the face.

## 8. Material slots = groups, and the export bundle

A **group** is a material slot name. Artists and agents name slots by purpose. The painter and the spec target groups: `appearance` features use `region: "box:<glob>"` / `"part:<glob>"` exactly as for box ids of the `shape` system, and the spec's `mesh.groups.<name>` gives each group a default material/colour/accent.

Naming rules (the exporter normalises the name: Blender's `.001` suffix is dropped, upper case lower-cased, any other character becomes `_`; warning `group_renamed` when something else changed):
- `[a-z][a-z0-9_]{0,63}`, one purpose per name: `skin`, `face`, `hair`, `robe`, `robe_trim`, `cloth`, `cape`, `wing_f` (feathers), `wing_b` (wing bones/membrane), `boots`, `gloves`, `metal`, `gem`, `ribbon`, `belt`. Prefer a prefix family (`wing_*`, `robe_*`) so a feature can address them with one glob.
- One group per material/colour region (a different colour or texture treatment = another slot); faces without a slot or with a slot without material become `skin`. Groups exist only if at least one triangle uses them, and keep the material slot order.
- The group decides the painter's colours, NOT the geometry: do not split groups to control weights.

The bundle `export` writes (both files, same stem `<id>`):
- `<id>.efmesh.json`: the exact EF mesh JSON (`efmodel.check_mesh_json` clean; `weights[0] == 1.0`; one position per Blender vertex with its weights; UVs and normals deduplicated at 5 decimals; 12 non-empty parts).
- `<id>.meshmeta.json`: `{"schema":"bossforge-mesh/1","id","height","units":"blocks","joint_names":[20],"groups":[...],"tri_groups":{part:[group index per triangle of that part's array]},"group_bones":{group: dominant joint},"source":"model.blend","generator":"bfk/1"}`. `tri_groups[part]` has one entry per triangle of the part, an empty part's single anchor triangle included (entry 0).

Export report (printed JSON): `ok`, `files`, `errors`, `warnings[{code,message}]`, `positions/uvs/normals/triangles`, `triangles_by_part`, `groups`, `group_bones`, `max_influences`, `influence_histogram`, `height`, `bounds`, `facing`, `check_mesh_json`. Nothing is written when `ok` is false.

| code | meaning | action |
|---|---|---|
| `no_uv` (error) | the mesh has no UV map | unwrap |
| `no_weights` (error) | no vertex has a weight on a joint-named group | paint/import weights |
| `extra_bones`, `missing_bones` (error) | the armature is not the EF biped | use `blender rig` |
| `invalid_mesh` (error) | `efmodel.check_mesh_json` message (winding vs normals, ranges, ...) | fix the mesh |
| `unweighted` | vertices without joint weights, bound to their neighbours (error with `--strict`) | paint them |
| `influences_trimmed`, `ignored_groups` | more than N influences; vertex groups that are not EF joints | informational |
| `triangulated` | quads/ngons were triangulated | informational |
| `uv_out_of_range` | UVs outside 0..1 | fit the UVs into the square |
| `loose_geometry` | loose vertices/edges dropped | clean up |
| `zero_area`, `flipped_triangles` | degenerate or inside-out triangles | recalculate normals, delete |
| `not_on_floor` | lowest vertex is not at z = 0 | move the model: the rig's feet are at z = 0 |
| `facing_backward` | the soles of the feet point to -Y | the model must face +Y (heuristic: only fires when the feet clearly point backwards; symmetric feet give `unknown`) |
| `transform_baked`, `rig_moved`, `rig_hierarchy`, `group_renamed`, `no_rig` | see the text above | informational |

Spec block (owned by `meshsrc.py`; authoritative field list in `boss-spec.md`):

```json
"mesh": {"file": "models/nightingale.efmesh.json",
         "groups": {"skin": {"material": "skin_soft", "color": "skin", "region": "head"},
                    "robe": {"material": "silk", "color": "primary"}},
         "texture": {"size": 512, "density": 32}}
```

`file` is relative to the spec directory (or absolute); the sidecar `models/nightingale.meshmeta.json` sits next to it. `region` (`head torso arm_l arm_r leg_l leg_r`, or `private`) says which body region frame features such as the face, emblem and trim attach to; it defaults to the region of the group's dominant bone, and `private` gives a group its own frame (wings, capes, hair, crowns, staffs: otherwise a wide group distorts the shared region frame, `mesh_region_frame_wide`). `texture.size` must equal the texture the painter writes; `texture.padding` (default 4) is the painter's island dilation. `meshsrc.py` is the source of truth for the keys; `mesh` is mutually exclusive with `shape` (`mesh_and_shape`).

## 9. Round trip and numbers (measured)

`tests/test_blender.py` runs all of this (`python3 -m unittest discover -s tests -p "test_blender.py"`; Blender tests skip when no Blender exists). Numbers below come from those tests on this machine:

| Check | Result |
|---|---|
| King Arthur shape mesh (`shape` -> import -> export), 828 triangles, 552 positions | triangles per part identical in all 12 parts; max position error 0.0 (JSON has 5 decimals); max UV error 0.0; max normal error 2.5e-4 (int16 storage); joint assignment identical for every triangle corner; weights identical |
| EF stock `biped.json` (smooth weights, vcounts up to 3), 472 triangles | triangles per part identical; max position error 5e-6; UV 7e-6; weights identical; 196 / 63 / 1 vertices with 1 / 2 / 3 influences before and after |
| Pose convention: `pose_bone_world_rotate` on the imported biped vs linear blend skinning of `biped.json` with EF's joint matrices in pure Python (Arm_R 60 degrees about X, about Y, about -X; Thigh+Leg; a 5 joint chain) | max vertex error 4.5e-5 .. 8.5e-5 blocks over all poses (limit 1e-3), mesh displacement up to 0.81 blocks. The residual is the 4-decimal rounding of `rigs.json` against the product of EF's joint matrices (their max joint position difference is 4.8e-5) |
| Export is independent of the pose | the export of a posed rig equals the rest export byte for byte |

## 10. Painting, viewing, the render gate

1. Model, weight, unwrap in Blender, `blender export` -> bundle; set `mesh.file` in the spec.
2. `bossforge skin SPEC --out DIR` paints the texture onto the mesh's UV triangles from the spec's `appearance` (palette, materials per group, features) and renders its five-panel preview. `reference.bake` can bake colours from a pasted concept sheet.
3. `blender import BUNDLE --texture DIR/texture.png --out m.blend` (Closest interpolation, the image is packed) or `blender view` to inspect the textured model from front/3q/side/back and posed. READ the images.
4. Fix in Blender or in the spec, repeat. `bossforge qa` and `fit` stay the verifiers.
5. The render gate (`bossforge render-test`, `render-pipeline.md`) is still mandatory before shipping: Blender shows the mesh, the texture and the pose maths, not Minecraft's lighting, the part visibility or EF's renderer. Report "render not verified" when it could not run.

## 11. Driving the live Blender through the MCP (optional)

The pipeline itself uses headless processes only (parallel safe, no shared scene). A live Blender with the `blender` MCP is useful for an artist-in-the-loop session; it is ONE shared scene, so one agent at a time and never in parallel with other agents that touch it.

- `get_addon_status`, `get_scene_info` first. `execute_blender_code` runs Python in the live session and returns what it prints. Do not paste long scripts: write the file and run it:
  `import sys; sys.path[:0] = ["<skill>/scripts/blender"]; exec(open("<abs path>/task.py").read())` where the task script uses `import bfk` (this also puts `<skill>/scripts` on `sys.path` for `bossforge_core`). A task script reads its arguments with `bfk.get_args(default_dict)` (`BFK_ARGS` env, else the default) and prints with `bfk.emit(result)` (`BFK_RESULT {json}`). `bfk.reset_scene()` wipes the scene and is for headless runs only.
- `look` returns a picture of the live viewport; `max_size` keeps long sessions cheap. For counts, sizes and positions use `get_scene_info` or print them from `execute_blender_code`.
- Look shader nodes up by TYPE, never by name (names are localised): `next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")`, image nodes `n.type == "TEX_IMAGE"`. Colours go on node inputs; `material.diffuse_color` only affects the viewport.
- Never hardcode enum identifiers: read them (`[i.identifier for i in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items]`); `scene.render.engine` under-reports, so assign inside `try/except TypeError` and read the valid names from the message (this is what `blender doctor` does).
- `bfk` modules are plain functions: `efio.import_efmesh(path, texture=...)`, `efio.export_efmesh(obj, arm, out_dir, id)`, `rig.build_rig()`, `rig.pose_bone_world_rotate(arm, "Arm_R", "X", 60)`, `rig.view_front_camera(azimuth="3q")`. They work on the live session's current .blend; `export_efmesh` leaves the scene unchanged (it evaluates a temporary copy; modifier and visibility state are restored).
- Generated or downloaded models (`generate_3d`, `import_asset`) arrive at arbitrary scale and orientation, with arbitrary topology and no EF weights; they are references at best, not drop-in bodies. Fit them to the rig first.

## 12. Limits and what is still unverified

- Tested here: Blender 5.2.1 LTS only, Linux. The Blender-side code uses the 4.1+ mesh API (custom normals, `corner_normals`); 4.2 LTS is the documented floor, but it was not run.
- Only the EF biped armature is supported; the iron golem armature (other joint set) is not importable (`wrong_armature`).
- Custom normals are quantised by Blender (int16 pairs, 2.5e-4). The `facing_backward` check is a heuristic on the foot soles and stays silent for symmetric feet (`unknown`). The exporter does not measure UV island overlap, texel density or margins; `bossforge validate`/`qa` do that when the bundle is used (`meshsrc`: `mesh_uv_overlap`, `mesh_low_density` below 8 texels per block, `mesh_uv_outside`, `mesh_not_on_ground`, `mesh_height_mismatch`, `mesh_left_right_swapped`, `mesh_region_frame_wide`).
- A mesh that exports `ok` can still look wrong in game: part visibility, lighting, double-sided cloth, texture filtering and the EF compute shader are only judged by the render gate (`render-test`).
- Blender previews are in the rest pose or in poses made with `pose_bone_world_rotate`; what the real EF animations (walk, attacks) look like is covered by the section "Viewing and posing" (BlenderView), not by the exporter.

## Viewing and posing

`bossforge blender view` renders the textured model headlessly (one Blender subprocess, about 3-10 s; a spec also pays for `skin`) from several angles, in poses and in real Epic Fight animation frames, and writes PNGs an agent reads. Use it before `render-test` to catch geometry, weight, clipping and texture problems cheaply. READ the images; the JSON only carries paths and measurements.

```
bossforge blender view SOURCE --out DIR [--texture PNG] [--views front,3q,side,back,top,closeup_head]
    [--pose arm_r=60,thigh_l=-35,head=z:40] [--pose-sheet] [--sheet-views front,3q,side]
    [--anim epicfight:biped/living/walk --at 0,0.2,0.4 [--verify-anim]] [--engine workbench|eevee] [--light studio|flat]
    [--projection ortho|persp] [--size 640] [--sheet-size 360] [--bg #1f2430] [--cols 3]
    [--reference-views [--reference views.json]] [--no-grid] [--object NAME --rig NAME] [--ef-jar JAR]
```

`SOURCE` is any of: a boss spec (`shape` boxes: the mesh is built with `shape` and the texture painted with `skin`; `mesh` block: its bundle plus the painted texture; no body block: the stock EF biped mesh), a bundle directory or `<id>.efmesh.json` (texture: `--texture`, else `<id>.png` next to it, else the importer's flat group tints), any EF mesh JSON (a generated `animmodels/entity/<id>.json`, EF's `biped.json`), or a `.blend` made by `blender import` (its own materials unless `--texture`). The iron golem armature is not supported.

### What you get

| File | Content |
|---|---|
| `views/<view>.png`, `montage.png` | the rest (or `--pose`) pose, one square image per view, labelled; the montage tiles them (`--cols`) under a title line with source, engine, texture and pose |
| `pose_sheet.png`, `views/ps_<pose>_<view>.png` | `--pose-sheet`: one row per pose, one column per sheet view, same scale in every cell |
| `anim_sheet.png`, `views/an_<i>_<view>.png` | `--anim ... --at t1,t2,...`: one row per time |
| `reference_compare.png`, `views/ref_<view>.png` | `--reference-views`: per reference view `[reference crop \| render \| silhouette difference]` |
| `raw/<frame>__<camera>.png` (+ `.rgba`) | the transparent renders the composites are made from (alpha = silhouette) |
| `view.json` | the printed JSON: image paths, per frame bounding boxes (blocks), cameras, mesh / rig / texture facts, reference IoU, verification, warnings |

Every composite has the background (`--bg`), a 1-block ground grid on z = 0 (a ground line in the side-on views), a 1-block scale bar (vertical, horizontal in the top view, ticks every 0.25) and a label. All views of one run share one scale (the union of the posed bounding boxes), so poses compare directly; only `closeup_head*` (framed on the vertices weighted >= 0.5 to `Head`) is zoomed. Views: `front` (camera at +Y, the face), `back`, `side` = `left` (camera at -X, the character faces left), `right` (camera at +X, faces right: the reference-view convention), `3q` (35 degrees from the front towards the left side, 14 degrees up), `3q_right`, `3q_back`, `top` (front is up in the picture), `closeup_head`, `closeup_head_3q`.

The texture goes on every material with Closest interpolation and the game's alpha cutout (alpha above 0.1 opaque, else discarded; `texture.semi_transparent_texels_clipped` counts what was clipped). `workbench` (default) shows the exact texels (`--light flat`: unshaded colours; `studio`: form shading); `eevee` lights a key and a fill sun parented to the camera (the first render compiles shaders, a few seconds). Engines are looked up by name from the engine list of the running Blender, never hardcoded.

### Posing

`--pose` rotates joints about their own position in WORLD (storage) axes, in the order given, children follow, angles in degrees: `arm_r=60` swings the right arm 60 degrees forward (a bare number is about +X, the character's side axis: positive moves limbs towards +Y, the front), `head=z:40` turns the head 40 degrees counter-clockwise seen from above, `x:`, `y:`, `z:` pick the axis. Names are bossforge bones (`arm_r`, `forearm_r`, `hand_r`, `thigh_l`, `shin_l`, `torso`, `chest`, `head`, `root`) or EF joints (`Arm_R`, `Hand_R`, `Knee_L`, any case); an unknown name lists the valid ones. It uses `rig.pose_bone_world_rotate`, so the bone roll of the armature does not matter.

`--pose-sheet` renders these poses (EF joint names, world X unless noted) in `--sheet-views` (default `front,3q,side`) at `--sheet-size`: neutral; arms forward 60 (`Arm_R/L` +60); arms back 30 (-30); walk legs (`Thigh_R` +35, `Thigh_L` -35); head turn 40 (`Head` z +40); torso bend 25 (`Torso` +25). A `--pose` given as well is appended as `custom`. What to look for: robe/skirt/cape boxes that stay rigid while legs swing and pierce them (walk, side view), arms or sleeves that cut into wings and capes (arms forward/back), hair and crown tearing off the head (head turn), a gap or stretch at the waist (torso bend), box undersides that face the camera and render dark under studio lighting (the torso bend does this to the pauldrons and forearms; it is lighting, not a texture hole).

### Real Epic Fight animations

`--anim epicfight:biped/living/walk --at 0.0,0.2,0.4` evaluates EF's own animation JSON (`assets/epicfight/animmodels/animations/<key>.json`, about 500 files in 20.14.17; `bossforge list animations --prefix biped/` has the keys, with or without the `epicfight:` namespace) at those times (seconds, clamped to the clip; at most 12) and poses the rig with it. The file is read straight from the Epic Fight jar (`--ef-jar`, `$EPICFIGHT_JAR`, then launcher mod folders; no extraction) or, without a jar, downloaded from the pinned EF commit, and cached under `<cache>/ef/<ref8>/animations/`. The armature is the cached stock `biped.json`.

The conversion (module `bossforge_core/efanim.py`, transcribed from the 20.14.17 bytecode of `JsonAssetLoader.getTransformSheet / loadClipForAnimation / getJoint`, `JointTransform`, `Joint.initOriginTransform`, `Armature.getPoseTransform`, `TransformSheet.getInterpolationInfo`): a keyframe matrix `M` (row-major, the joint's FULL local transform in Blender bone space) becomes `JointTransform.fromMatrix(L_j^-1 * [CORR *] M)` where `L_j` is the joint's local rest matrix from `biped.json` (the root's is premultiplied by `CORR` = rotation -90 degrees about X, Blender Z-up to Minecraft Y-up), `CORR` is also applied to `M` of the first file entry that exists in the armature unless a `Coord` entry came before it (those files already store the Root in Minecraft space), negative times are dropped, `format: attributes` keyframes (`loc/rot/sca`) are used directly (the quaternion is the textbook rotation of `(rot[1], rot[2], rot[3], rot[0])`), frames are blended with lerp / normalized shortest-path lerp / lerp. The pose is `P_j = P_parent * L_j * T(t) R(q) S(s)`, skinning `v' = sum_i w_i (P_i W_i^-1) CORR v` with the bind world matrices `W_j`, weights as stored (not renormalized). Blender gets the rig-independent world-space matrix `D_i = CORR^-1 (P_i W_i^-1) CORR` per joint and sets each pose bone so that its deformation equals `D_i` (`bfk.view.apply_world_deltas`: target pose `D_i * rest_i`, `matrix_basis` from the parent's target), which works for any bone roll and any rest positions. Not applied: root motion / `Coord` displacement, EF's runtime layering and joint masks, IK. The rig's rest pose must be EF's stock biped rest pose (it is, when the mesh came from `shape` or `blender import`).

`--verify-anim` (bundle or spec sources) re-does the skinning of the same JSON in pure Python with EF's matrices and compares it with the vertices Blender deformed; the result is `verification` in the JSON (`max_error` in blocks, per frame `max_vertex_displacement`, `weight_sum_min/max`) and the command fails when it exceeds 1e-3 or the weights do not sum to 1.

Measured (Blender 5.2.1, EF 20.14.17):

| Check | Result |
|---|---|
| Blender deformation through `D_i` vs pure-Python EF skinning of the same JSON (`--verify-anim`), stock `biped.json` (260 vertices, up to 3 influences) and the King Arthur box mesh (552 vertices), 5 animations each (`living/walk`, `living/run`, `combat/dagger_auto1` with a `Coord` entry, `combat/greatsword_auto1`, `living/pov/eat` in `attributes` format), 44 frames, vertices displaced by up to 2.4 blocks | max error 9.0e-7 blocks on the stock mesh; 1.8e-4 on King Arthur, where all of it is on `Tool_R`/`Tool_L`: the file's keyframes there are very slightly non-orthogonal (column norms 0.9997 / 1.0006, dot 6e-4) and Blender pose bones are translate/rotate/scale, no shear; limit 1e-3 |
| `efanim.pose_matrices` (the Python port) vs the REAL Epic Fight classes (`JsonAssetLoader.getJoint/getTransformSheet`, `Joint`, `TransformSheet`, `JointTransform` loaded from the mod jar through `tests/tools/efanim_java/Harness.java`, Minecraft classes stubbed), `python3 tests/tools/efanim_java_check.py`: 59 animations (plain matrix, `Coord`-first and `attributes` files), 354 frames incl. the clip ends | max matrix element difference 1.2e-4 (Java works in float32, EF's `toQuaternion` normalises scaled columns, one keyframe of `fist_auto1` has a 2% non-uniform scale); an earlier run over 69 animations / 414 frames gave 1.03e-4 |
| Blender world rotation (`--pose arm_r=55`) vs the equivalent `D_i` matrices vs pure-Python skinning (test `test_world_rotation_equals_deltas_equals_python_skinning`) | below 1e-5 blocks |

What the real check found while it was written: first-person (`pov/`) files use `format: attributes` whose quaternion convention is easy to get backwards (EF negates the vector part and its quaternion matrix is the transpose), and a few files have unsorted keyframe times (`living/pov/drink`, joint `Shoulder_L`): `efanim` copies EF's binary search so both behave like the game.

### Reference comparison

`--reference-views` (a spec with `reference.views`, or `--reference views.json`) renders each reference view orthographically at the reference's own calibration (`px_per_block`, `ground_y`, `center_x`, crop) so the pixels line up, and writes `[reference | render | difference]` per view with the silhouette IoU (`reference` in the JSON: `iou`, `render_px`, `reference_px`, `render_only_px`, `reference_only_px`). Red = only in the reference, cyan = only in the render. The alignment itself is tested against an independent rasterisation with `reference.View.project` (IoU 0.9995-0.9998 on three calibrations including a cropped one; the rest is edge pixels). The model is shown at model scale (before the entity `scale`), like the calibrations.

### From Python

```python
from bossforge_core import blenderview
res = blenderview.render(bundle_dir_or_file, texture_png, out_dir, views=["front", "side"], pose="arm_r=60", pose_sheet=False, size=480)
res["views"]["front"]; res["frames"][0]["bbox"]; res["cameras"]["front"]  # paths, blocks, camera matrices
```

`render` takes the CLI options as keywords (`anim`, `times`, `engine`, `light`, `projection`, `sheet_size`, `bg`, `reference_views`, ...) and returns the dict the CLI prints. Inside Blender (or the live session) `bfk.view.run(args)` is the same renderer: args `source` (bundle) or an open .blend, `texture`, `out`, `frames` (`ops` or `deltas`, `views`, `size`, `dump_positions`), optional explicit `cameras`. `bfk.view.apply_world_deltas(arm, {joint: 16 floats})` poses a rig from world-space matrices; `efanim.blender_deltas(armature, clip.sample(t))` makes them.

### Findings on the shipped box examples (`--pose-sheet`, measured pictures, not game renders)

- King Arthur (`assets/examples/king-arthur/boss.json`, 69 rigid boxes). A one-off sampling of the posed boxes (6x6x6 points per moving box against the boxes of other bones, small `rotate` angles ignored; this script is not part of the tool) gave: arms forward 60 is clean (one pair, 1.9%); arms back 30 pushes the left kite shield 11% into the abdomen and 5.6% into the chest and the fists and cuffs 4.6-6.5% into the cape; legs at +-35 degrees push the left (rear) shin 11% and its greave 8% into the lowest cape segment, which is bound to `Root` and stays vertical, the rear skirt panel of the left thigh 19% into the same segment, and the belt tassets 22-25% into the root-bound skirt band. The skirt panels are bound to their own legs, so they follow them and do not pierce the opposite leg. In the pictures the head, crown and beard turn as one block, and the rest-pose face, crown, emblem and kite shield read clearly. The texture is crisp here (nearest filtering): the blur seen in game is therefore not in the texture, it comes from the game's filtering, which Blender does not reproduce.
- Nightingale box version (`assets/examples/nightingale/boss.json`, 235 boxes): the wings span 3.0 blocks and the model is 2.47 high, larger than the reference calibration (203 px/block): `--reference-views --reference /tmp/nightingale-ref/views.json` gives silhouette IoU 0.63 front, 0.61 back, 0.62 right, with far more render-only than reference-only pixels (front: 49,070 vs 9,885). Wings, hair bun and ribbons are rigid blocks on the chest and head, so a torso bend or head turn moves them as one piece; the ribbons swing as straight rods with the arms.

### Limits

- Blender previews are not the game: Workbench / EEVEE lighting differs from Minecraft's, entity textures are not mip-mapped or filtered here, part visibility and EF's renderer are only judged by `render-test`.
- Interpenetration and tearing are read from the pictures; nothing measures them automatically. Use `--pose` with a larger angle and `--views` closeups to look at a suspected spot.
- Animation previews use one animation at a time with EF's keyframe maths only (see above); blend trees, layers, root translation and IK are not evaluated. Weights that do not sum to 1 deform differently in Blender (normalised) and EF (not): `--verify-anim` reports `weight_sum_min/max`.
- A `.blend` source must contain one skinned mesh (or pass `--object` / `--rig`); the rest-pose rig must be the EF biped rig.

## Building the Nightingale example

Example: `assets/examples/nightingale-blender/` is the Nightingale (winged celestial mage, see `assets/examples/nightingale/` for the box version) modelled in code with the kit `scripts/blender/bfk/modeling.py`, skinned to the EF biped, exported as a bundle and painted by the generator. Everything below was measured on this machine (Blender 5.2.1); the texture is procedural (no `reference.bake`, no reference pixels in the repo).

### Run it

```
cd skills/epicfight-boss-forge
# 1. build: scene in memory (EF_Biped rig + mesh), nightingale.blend, the EF bundle, build_report.json (0.1-0.3 s of geometry, ~2 s with Blender start-up)
blender -b --factory-startup --python assets/examples/nightingale-blender/build_nightingale.py -- \
        --out "$PWD/assets/examples/nightingale-blender/models" [--checks] [--set wing_symmetric=true --set skirt_droop=0.05]
#    the same through the runner: bossforge_core.blender.run(Path(".../build_nightingale.py"), {"out": DIR, "id": "nightingale", "checks": true, "params": {...}})
# 2. paint (13 s at 1024x1024) and look at it
python3 scripts/bossforge.py skin assets/examples/nightingale-blender/boss.json --out /tmp/ng
python3 scripts/bossforge.py blender view assets/examples/nightingale-blender/models --texture /tmp/ng/src/main/resources/assets/roundtable/textures/entity/nightingale.png \
        --out /tmp/ng_view --views front,3q,side,back,closeup_head --pose-sheet --anim epicfight:biped/living/walk --at 0.15,0.45
# 3. measure against the sheets (copy the sheets to assets/examples/nightingale-blender/refs/, they are not shipped)
python3 scripts/bossforge.py fit assets/examples/nightingale-blender/boss.json --out /tmp/ng_fit
```

`--checks` adds `bundle_weights` (sum of the exported weights per position) and `poke_through` (below) to `build_report.json`. `nightingale.blend` is the editable scene (mesh + `EF_Biped`): edit it in Blender and re-export with `bossforge blender export`. A private copy can bake the sheet's colours by adding `reference.bake` to its spec (not done here). The build is deterministic: two runs give byte-identical `.efmesh.json`, `.meshmeta.json` and `build_report.json`. `models/` holds the committed result (`nightingale.efmesh.json`, `nightingale.meshmeta.json`, `nightingale.blend`, `build_report.json`), `texture/nightingale.png` the painted atlas; `tests/test_nightingale_blender.py` rebuilds the model in pure Python and fails when the committed bundle is not exactly that build.

### Files

| file | what |
|---|---|
| `scripts/blender/bfk/modeling.py` | the kit (pure Python except the last section): primitives, UV islands + atlas packing, weight rules, Blender conversion, numeric checks |
| `build_nightingale.py` | entry: `build_model(params)` (pure Python), `build_scene(params)` (rig + mesh in the current .blend), `run(args)` (build + export + save + report) |
| `nt_common.py` | the sheet calibration `REF`, group list with texel densities, joint heights, weight rules of arm / leg / torso / skirt, parameter defaults |
| `nt_body.py` `nt_extras.py` | head, robe shell, sleeves, hands, legs, boots, skirt, tabard; pauldrons, belt pouch + vials |
| `nt_hair.py` `nt_ribbons.py` `nt_wings.py` | hair, bun, bangs, locks, pins, ribbon loops; the long ribbons; both wings (feather tables fitted to the sheet) |
| `nt_checks.py` | `weights_total`, `bundle_weight_check`, `pose_report` (poke-through at sample poses) |
| `boss.json` | spec: the old lore / stats / staff, a `mesh` block instead of `shape`, features per group, the `reference` block |

### Scale: the model is 0.84 of the drawing

The sheets are orthographic at 203 px per block and the drawn character is 2.18 blocks tall, but the EF biped is 2.0 tall with its neck at 1.514, shoulder 1.394, elbow 1.094, knee 0.386. Building the drawing 1:1 would put the chin 0.22 above the neck joint and the shoulder joint 0.3 below the shoulder line. The model is therefore built at `REF.S = 0.84` of the sheet (chin 1.45, shoulder line 1.42, cuff 0.89, hem 0.45, knee cuff 0.35): each joint sits within 0.07 block of the matching landmark of the drawing (neck vs chin 0.06, shoulder vs shoulder line 0.03, knee vs boot cuff 0.03, wrist vs cuff 0.07), and the fit calibration of the spec is `px_per_block = 203 / 0.84 = 241.667`. `size.height` (3.0) still decides the in-game size: the mesh is 1.827 tall, so the entity scale is 1.642. All geometry code states sheet pixels (`REF.x(px)`, `REF.z(py)`, `REF.y(px_side)`, `REF.d(len_px)`), so a number can be checked against the sheet directly. Widths are the drawing's (torso 0.31 wide x 0.25 deep, sleeves 0.21 wide, legs 0.12 wide, head cube 0.26 x 0.23 x 0.27), not the stock biped's: the character is slimmer than the rig, arms hang 0.09 inside the shoulder joints.

### The kit (`bfk.modeling`)

Build in storage space (Z up, faces +Y, right = +X). A `Model` collects vertices (each with a weight spec) and face records; `build()` normalises weights, develops every record into a UV island, packs the atlas and returns plain lists; `to_blender()` makes the object. Winding is always outward (signed-volume test), so callers never think about it.

| function | use |
|---|---|
| `Model(groups, atlas, margin, density)`, `.mark()`, `.mirror(mark)`, `.build()` | container; `mirror` appends the x-mirrored copy (joints `_R` <-> `_L` swapped, UVs rebuilt upright, not mirrored) |
| `block(m, lo, hi, group, w, bevel=, top_scale=, top_shift=, rot=, segments=, skip=)` | axis-aligned (optionally chamfered, tapered, rotated, sectioned) box; the four sides are ONE wrap-around UV strip |
| `stacked_rects(m, [(z, cx, cy, hx, hy), ...], group, w)`, `frustum(...)` | rectangular shells with a free centre and size per section: torso, sleeves, tabard |
| `loft(m, rings, group, w, caps=, loop=, flat_cols=)` | any closed tube through equal-length point rings: the C-shaped skirt wall (concave caps are ear-clipped), subdivided faces |
| `sweep(m, path, widths, thick, group, w, side=, offsets=, twist=, bevel=)` | rectangular section along a polyline: feather cards, ribbons, hair locks, loop rings (`loop=True`) |
| `bezier`, `path_with_bulge`, `ring_path` | path helpers |
| `rule_chain(axis, [(coord, spec), ...])`, `rule_blend`, `rule_mix`, `blend_weights`, `smoothstep` | weight rules: gradients along an axis or any scalar field; a spec is a joint name, a dict or a rule |
| `normalize_weights(w, max_influences=3)`, `swap_joint` | prune to <= 3 influences, sum 1; `Thigh_R` <-> `Thigh_L` |
| `pack_rects`, `island_texels`, `fit_scale` | shelf packing with margins, whole-texel island sizes, the largest uniform density factor that fits the atlas |
| `to_blender(built, name, groups, rig=, colors=, image=, tags=)`, `bind_rig`, `make_material` | object with one material slot per group, vertex groups named like EF joints, UV map `UVMap`, Armature modifier; Closest-filtered image texture when `image` is given |
| `evaluated_points`, `points_inside`, `weight_report` | posed vertex positions, nearest-surface inside test (closed outward meshes), skinning totals |
| `joint_positions()` | EF joint rest positions in storage space (pure Python, from `rigs.json`) |

UV rules the kit enforces (what the painter needs): one island per planar face or per loft strip, unrolled with true lengths; image-up = world up (for horizontal faces image-up = +Y), no mirrored islands (a face is mirrored only by geometry, never by UV); uniform texel density per group (`texels per block` = group density x the call's `density` x one global fit factor, 1.0 when it fits), island sizes snapped to whole texels, `margin` texels between islands (4: the painter dilates island borders by `texture.padding` 4); every polygon with more than four corners is triangulated by the kit (ear clipping, collinear points dropped) so the exporter's tessellation never produces zero-area triangles.

### What is built and how it is skinned

| part | geometry | weights (smooth, `rule_chain` along z unless noted) |
|---|---|---|
| head (`skin`) | 0.26 x 0.23 x 0.27 cube, face density x2.4 (230 texels/block: the 8x8 pixel-art face is 60 texels wide) | `Head` |
| robe shell, belt (`robe`, `leather`) | shoulders y=172 .. hip y=335 sheet px | Root -> Torso -> Chest -> Head over the neck (`torso_rule`) |
| sleeves, cuffs, fists (`robe`, `trim`, `skin`) | 0.21 wide, flared; fists 0.12 | Chest -> `Arm` over the shoulder, `Arm` -> `Hand` (forearm joint) ramp +-0.07 around the elbow, `Hand` -> `Tool` around the wrist (`arm_rule`) |
| legs, boot cuffs, boots (`skin`, `trim`, `boots`) | 0.12 x 0.13 legs, toe step | `Thigh` -> `Leg` ramp +-0.07 around the knee (`leg_rule`) |
| skirt wall (`skirt`) | two C-shaped halves (right half + mirror), front opening +-34 px, split at the back centre, 4 sections x 7 columns, hem steps down 0.08 towards the slit | Root above the hip, own-side `Thigh` 0.14 below it (`skirt_rule`) |
| tabard (`tabard`) | one panel, front face has vertices at x = +-0.015 | x < 0 `Thigh_L`, x > 0 `Thigh_R`, only that centre strip blends (`centre_rule`) |
| pauldrons, pouch + 3 vials | slanted slabs; box with lid | Chest -> Arm; Torso -> Root |
| hair, bun, bangs, locks, pins, 2 ribbon loops (`hair`, `ornament`, `ribbon`) | stair-stepped shell tiers, 12-block bun, 4-block parted fringe with eye windows, 19 pin tiles per side, loops as stair-stepped plates | `Head`; locks below the cube Head -> Chest |
| 2 long ribbons per side (`ribbon`) | 30-42 sample splines, twisted, forked tips; left ribbons fitted separately (the sheet's sides differ) | upper end Head -> Chest, free ends Arm / Hand resp. Root / Thigh |
| wings (`wing_a..d`) | 4 rows of feather cards (A leading-edge slab 5 strips + cap, B 6 white inner coverts, C 6 lavender secondaries, D 11 long primaries) + a thin rear covert layer; every tip a flat step; roof-tile depth layering; each wing fitted to its own front-sheet outline | `Chest` 100% (`wing_weight=shoulder` adds an Arm blend at the root) |

Totals: 9732 triangles (limit 40k; 9738 in the EF parts: six empty parts keep one anchor triangle), 5408 vertices, 18 groups, 14 of the 20 joints used, influences per vertex 1 / 2 / 3 = 4568 / 656 / 184. EF parts: head 1595, torso 7219 (wings and ribbons are Chest-dominant), arms 260 + 260, legs 199 + 199. Atlas 1024 x 1024, UV coverage 15.4 %, texel density per group 64-80 (cloth, wings) / 94-125 (ornaments, vials) / 161 (hair) / 157 mean, 231 max (skin, the face); the painter's patterns use about 1/32 block cells, i.e. 2 texels, which is the sheet's art-pixel size (0.03 block).

### Measured

Skinning (`build_report.json`, `--checks`): every position of the exported EF mesh has weight total 1.0 (min 0.9999999999999999, max 1.0), no vertex without weights, max 3 influences. Poke-through = cloth, ribbon and wing faces (vertices, edge midpoints, face centres) inside the posed leg / arm volume deeper than 2 mm, pose by `rig.pose_bone_world_rotate`:

| pose | cloth inside legs | cloth + ribbons + wings inside arms (samples / deepest) |
|---|---|---|
| rest | 0 | 3 / 4 mm (feather root under the pauldron) |
| one leg forward 60 / back 60 / both split +-60 / out 30 / knee bend 60 | 0 in all five | 3 / 4 mm |
| arms forward 60 | 0 | 16 / 3.5 mm |
| arms out sideways 60 | 0 | 23 / 11 mm |
| head turn 40, torso twist 30 | 0 | 22 / 5 mm, 147 / 27 mm |
| **arms back 20 / back 45** | 0 | **1801 / 49 mm, 3786 / 83 mm: the wings** (see below) |

Silhouette fit against the sheets (calibration 241.667 px/block, same masks as `reference.views`): `bossforge fit` on the committed spec (with the sheets copied to `refs/`) gives front 0.9366, back 0.5715, right 0.8470. The back figure is clipped by the sheet (the right wing lies beyond the crop, the sheet draws only the left wing: `bossforge fit` counts those pixels as extra; clipped to the crop the same model scores 0.797, the half that the sheet draws, the character's left, 0.853, the left wing alone 0.851). Inside the front view: wing zones 0.936 (character's right) / 0.922 (left), body 0.952 (my own Blender-silhouette tool and `bossforge blender view --reference-views` agree with `fit` to 0.001-0.003). Baselines with the same masks: the shipped box example `assets/examples/nightingale/boss.json` 0.568 / 0.335 / 0.458 (front / back / right), a box version with relief wings extruded from the sheet silhouettes (217 boxes) 0.9185 / 0.5791 / 0.7337. So the mesh is better in front (+0.018 over the relief boxes, +0.37 over the shipped boxes) and in the side view (+0.11), slightly worse in the back view (-0.008 official; the relief boxes were extruded from the same masks, our left wing is fitted mostly to the front sheet).

### Pitfalls found while building it

- **Groups decide the painter's frame.** Every non-private group of a body region shares one bounding box for eyes / emblem / trim; wings, hair, ribbons, skirt, tabard, pauldrons, pouch must be `region: "private"` in `mesh.groups` or they stretch the torso / head frame. A group named in the spec but missing in the bundle is `mesh_bad_group`: the spec's groups are exactly the groups of the bundle (a test checks it).
- **Sheet drift.** The two wings, the two ribbons and the front / back wings of the sheet disagree by 5-9 px; each wing is fitted to the front sheet (the primary view) at a small cost in the back view. The back sheet draws one wing, so no model can score high there.
- **Skirts and legs.** 50/50 thigh weights at the centre line of a skirt or tabard push a swung leg through the cloth (measured: 8-35 inside samples, up to 5 cm at +-60 degrees); a skirt that follows the Root until the knee does the same. What works: each half follows its OWN thigh completely, the Root -> thigh change happens within 0.14 block of the hip, the halves part at the back centre like a split skirt. With that all ten poses are clean.
- **Open meshes break inside tests.** A sleeve built without caps made `points_inside` report the wings above its open top as inside (2331 false hits); close every tube you test against.
- **n-gons.** Concave caps with collinear points (C-shaped skirt wall, subdivided tabard) are tessellated into zero-area triangles by the exporter (`mesh_uv_degenerate`); the kit triangulates caps itself.
- **Wings and arms.** Wings are rigid on `Chest` and sit 7 mm behind the sleeves, so an arm swung back 20 degrees already passes through them (EF has no wing bones, `Shoulder_*` rotates nothing). Weighting the covert roots to the arm (`wing_weight=shoulder`) does not help measurably; a boss that must swing its arms backwards needs the wings further back (`wing_back`, `wing_sweep`) or accepted clipping.
- **The painter works on trapezoid islands.** Patterns on strongly tapered strips (the skirt) come out skewed; keep ornaments on flat panels (tabard, torso, pauldrons) and keep panel motifs >= 5 cells wide (`cell: 2` at 72 texels/block).
- **Hair eyes.** The fringe has two eye windows (y 135-141 sheet px, 0.6 of the head height): `eyes.y` 0.6 and no `brow` feature (the bangs are the brows).
- **Texture density is a budget, not a goal.** 1024 atlas, 15 % covered at 64-230 texels/block; do not raise `density` past what `built.scale == 1.0` allows (a smaller factor shrinks every island uniformly, never the face alone).

### What is still wrong / not verified

- **Not seen in game.** The Epic Fight render gate (`bossforge render-test`) was not run for this example; everything here is Blender, `bossforge skin/fit/qa/add-boss` (add-boss + qa pass without warnings) and `blender view` including the real EF walk and spear animations (`--anim`), where cloth, hair, ribbons and wings follow plausibly and nothing tears.
- **Texture is plainer than the sheet.** The painter gives palette, smooth silk, a glowing cross, belt crest, pauldron zig-zag, hem lace, a tabard with blue edges and a bottom band, feather gradients and sparkles; the sheet's dense cream filigree (collar wings, crest, side-panel tiers, tabard diamonds, glossy violet wing gradient) is not reproduced, and ornament features on skirt islands come out skewed or partial. The wing colour fade (white top, violet middle, pale tips) is approximated by four group colours plus a tip gradient and sparkles.
- **Geometry gaps.** Fists have no staff hole; the boot toe cap is a plain step; the left / right ribbons of the sheet differ, ours are mirrored; ribbon ends are two-pronged, not ragged; ribbon loops overlap the lowest bun tier by <= 3 mm; the hem tabs are one droop, not three stairs; the side view's inner flap region (x 495-506, y 400-470 sheet px) is only partly filled; the front-view ribbon boxes score 0.77 (right) / 0.77 (left) against the sheet's.
- **Numbers.** Front 0.9366 meets the 0.93 target, the side view 0.847 the 0.80 target; the back is capped by the sheet (above) and the left wing is a compromise (0.922 in the front zone, 0.851 in the back zone: the sheet's two drawings of it disagree by ~5 px).

### Parameters that matter

`--set key=value` (JSON values) or `params` in the runner args; all have defaults in `nt_common.DEFAULTS` and the part modules. `atlas` (1024; 512 shrinks every density), `face_density` (2.4), `skirt_segments` (4), `skirt_columns` (7), `skirt_droop` (0.08, hem steps down towards the slit), `skirt_drop` (0.14, hip -> thigh weight distance), `tabard_lr` (0.015), `wing_symmetric` (false: each wing fitted to the front sheet; true: one mirrored consensus wing), `wing_rear` (true: rear covert layer for the back view), `wing_spread`, `wing_curl`, `wing_density`, `wing_weight` (`chest` | `shoulder`), `wing_back`, `wing_sweep`, `ribbon_symmetric` (false: the left ribbons have their own control points), `ribbon_swing`, `ribbon_twist`, `ribbon_width`, `hair_density`, `loop_density`, `seed`, and `parts` (list of part module names after `nt_body`, e.g. `["nt_extras"]` builds the reduced model the Blender test uses).

