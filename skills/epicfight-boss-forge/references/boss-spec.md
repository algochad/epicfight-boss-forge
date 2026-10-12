# Boss spec reference

File: `boss.json`, `"schema": "epicfight-boss-forge/1"`. Run `bossforge validate boss.json`; errors carry a JSON pointer, a code and the allowed values. `spec.normalize` applies tier, archetype and template defaults; the normalized form is what generators read.

| Field | Type / default | Rules |
|---|---|---|
| `schema` | `"epicfight-boss-forge/1"` | required, exact |
| `id` | string | `^[a-z][a-z0-9_]{2,31}$`; unique per project; Java class = PascalCase |
| `mod_id` | string | `^[a-z][a-z0-9_]{1,63}$` |
| `mod_name` | default Title(mod_id) | 1-60 chars |
| `display_name` | string | 1-48 chars |
| `title` | default `""` | <= 64 |
| `tier` | `major quest mini hidden` | required |
| `seed` | int, default crc32(id) | any int; same seed gives byte-identical output |
| `lore` | `{summary, backstory, description, personality?, death_message?}` | first three required; each <= 1200 chars |
| `size` | `{height, width, depth?}` blocks | height 1.0-8.0, width 0.4-4.0, `depth` omitted or equal width (`depth_must_equal_width`) |
| `body.template` | `auto biped iron_golem` | with a `shape` or `mesh` block always the EF biped rig; otherwise auto: humanoid only if `main_weapon` is set, else the closest proportion |
| `shape` | optional block | custom body: `{rig, silhouette, proportions, attachments, remove, parts, texture.density, texture.atlas}`; see `shape-forge.md` (`texture.atlas` 512 or 1024, default 512; the `relief` preset cuts boxes from the `reference` views). Not allowed with `iron_golem` (`shape_requires_biped`) |
| `mesh` | optional block | imported body from Blender: `{file, groups:{<group>:{material,color,accent,region}}, texture:{size,padding,density}}`; `file` = `<stem>.efmesh.json` (the EF mesh JSON) next to its `<stem>.meshmeta.json`, relative to the spec; `groups.<g>.region` is `head torso arm_l arm_r leg_l leg_r` or `private` (default: derived per triangle from the weights); `texture.size` 64-2048 power of two (default 512), `padding` 0-16 (default 4). Mutually exclusive with `shape` (`mesh_and_shape`) and `iron_golem` (`mesh_requires_biped`); see `skin-forge.md` "Painting an imported mesh". `add-boss` stages the bundle into `bossforge/models/` |
| `reference` | optional block | `{views:{front|back|right|left:{image,crop,mask,ground_y,center_x,px_per_block}}, bake:{views,min_facing,depth_tolerance,smear,palette,regions,exclude,order,blend,footprint}}`: orthographic reference views of pasted concept art, used by `fit`, the `relief` preset and texture baking; see `reference-fit.md`. Image paths are relative to the spec file; `add-boss` stages them into `bossforge/refs/` |
| `faction` | default from archetype | `neutral undead blaze enderman illager piglins wither villager zombified_piglin` |
| `stats` | overrides | health 20-4096, armor 0-30, armor_toughness 0-20, attack_damage 1-200, movement_speed 0.1-0.6, knockback_resistance 0-1, follow_range 8-128, xp_reward 0-20000, impact 0-1024, armor_negation 0-100, max_strikes 1-1024 (int), stun_armor 0-1024, chasing_speed 0.5-2.0 |
| `appearance` | see `skin-forge.md` | archetype, palette, regions, overlay, overlay_enabled, features, texture.scale (stock meshes only; custom shapes use `shape.texture.density`, imported meshes `mesh.texture.size`) |
| `weapons` | list, default `[]` | see `weapon-forge.md`; ids unique |
| `main_weapon` | weapon id or null | must exist; only on a humanoid template (`weapon_not_wieldable`) |
| `moves` | list, default from tier+weapon | see `epicfight-patch.md` |
| `phases` | `[{name, health_below, announce?}]` | 1-4, first = 1.0, strictly decreasing, each in (0, 1] |
| `abilities` | list, default `[]` | `on_hit_effect aura summon regen enrage` (`bossforge list abilities`) |
| `encounter` | default from tier | see `encounters.md` |
| `rewards.essence` | `{min, max}`, default from tier | ints 0-64 |
| `sounds` | `{hurt, death, ambient?}` | registry ids, default from archetype |

Effective stat = `stats.<k>` else the tier value. `attack_damage` is the entity base attribute (EF copies it); the held weapon's damage is added on top.

Derived: `scale = round(size.height / model_height, 3)` (allowed 0.5-5.0, else `scale_out_of_range`); hitbox = width x height; `model_height` is 2.0 (stock biped), 2.7748 (stock iron_golem) or the total height of the custom shape or imported mesh (crown and horns included; the top of the mesh with its feet on y=0; `derived.custom`, `derived.model_id`).

Validation codes: `bad_schema bad_id bad_mod_id required bad_type bad_length out_of_range unknown_value depth_must_equal_width weapon_not_wieldable unknown_weapon duplicate_weapon_id first_phase_not_one phases_not_decreasing unknown_archetype unknown_material unknown_region unknown_feature bad_color unknown_series unknown_animation unknown_ability bad_phase recipe_invalid bad_resource_id texture_too_large scale_out_of_range`; `mesh` adds `mesh_and_shape mesh_requires_biped mesh_missing_file mesh_bad_json mesh_missing_meta mesh_bad_meta mesh_bad_group unknown_key`. Warnings: `density_reduced wide_hitbox_for_template overlay_ignored non_attack_animation player_skill_animation template_unmeasured`; `mesh` adds `mesh_uv_outside mesh_low_density mesh_uv_overlap mesh_uv_degenerate mesh_not_on_ground mesh_height_mismatch mesh_left_right_swapped mesh_region_frame_wide unknown_box`.

Minimal valid example:

```json
{"schema":"epicfight-boss-forge/1","id":"ash_warden","mod_id":"embers","display_name":"Ash Warden","tier":"mini",
 "lore":{"summary":"A burnt sentinel.","backstory":"It stood watch until the fire took the keep.","description":"A scorched armored husk."},
 "size":{"height":2.2,"width":0.8},"appearance":{"archetype":"undead"}}
```

Full example: `assets/examples/king-arthur/boss.json`. Documentation-grade JSON Schema: `assets/schemas/boss.schema.json` (the validator is authoritative).
