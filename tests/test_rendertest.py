import _path  # noqa
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bossforge_core import javagen, manifest, rendertest
from bossforge_core.spec import BossForgeError

MDK_RUNS = """minecraft {
    runs {
        configureEach {
            workingDirectory project.file('run')
        }

        client {
            property 'forge.enabledGameTestNamespaces', mod_id
        }

        server {
            args '--nogui'
        }
    }
}
"""


class RunConfigPatchTests(unittest.TestCase):
    def test_settings_go_into_the_client_run_only_and_are_indented(self):
        out = rendertest.patch_build_gradle(MDK_RUNS)
        client, server = out.split("        server {")
        self.assertIn("            property 'mixin.env.remapRefMap', 'true'", client)
        self.assertIn("--quickPlaySingleplayer', 'rendertest'", client)
        self.assertNotIn("mixin.env", server)
        self.assertIn("property 'forge.enabledGameTestNamespaces', mod_id", client)  # existing lines kept

    def test_patch_is_idempotent(self):
        once = rendertest.patch_build_gradle(MDK_RUNS)
        self.assertEqual(rendertest.patch_build_gradle(once), once)

    def test_missing_client_run_is_reported_not_guessed(self):
        with self.assertRaises(BossForgeError) as cm:
            rendertest.patch_build_gradle("minecraft { runs { server { } } }")
        self.assertEqual(cm.exception.err.code, "runs_layout_unexpected")


class HarnessTemplateTests(unittest.TestCase):
    VALUES = {"PACKAGE": "com.example.mod", "MOD_ID": "mymod", "BOSS_ID": "iron_lord", "WEAPON_ID": "iron_blade"}

    def source(self, **over):
        return javagen.render(javagen.load_template("rendertest", "RenderTest.java.tpl"), dict(self.VALUES, **over), "RenderTest")

    def test_test_class_has_no_placeholders_and_names_the_boss(self):
        src = self.source()
        self.assertNotIn("{{", src)
        self.assertIn("package com.example.mod.rendertest;", src)
        self.assertIn('"mymod:iron_lord"', src)
        self.assertIn('"iron_blade".isEmpty() ? "" : "mymod:iron_blade"', src)
        # the harness must report which mesh Epic Fight drew and whether the boss existed at each shot
        self.assertIn("RT_MESH", src)
        self.assertIn("RT_ENTITY", src)

    def test_a_boss_without_a_weapon_renders_without_the_player_shots(self):
        src = self.source(WEAPON_ID="")
        self.assertIn('"".isEmpty() ? "" : "mymod:"', src)
        self.assertIn("WEAPON.isEmpty() ? 0 : PLAYER_NAMES.length", src)

    def test_harness_reports_what_the_held_item_and_outline_checks_need(self):
        src = self.source()
        for key in ("RT_VIEW", "RT_HELD", "RT_GLOW", "RT_ANIM", "RT_POSE", "RT_PLAYER", "RT_BODY"):
            self.assertIn(key, src)
        # Epic Fight's own joint matrices, the target highlight verdict and the three guide switches
        for token in ("getPoseMatrices", "shouldHighlightTarget", "enableTargetEntityGuide", "showTargetIndicator", "healthBarVisibility",
                      "isCurrentlyGlowing", "hasGlowingTag", "getTeam"):
            self.assertIn(token, src)
        # numbers go out with a fixed locale: `tp` rejects Java's 1.0E-4 notation
        self.assertIn("Locale.ROOT", src)

    def test_view_names_match_between_the_template_and_the_runner(self):
        import re
        src = self.source()
        names = re.search(r"String\[\] NAMES = \{([^}]*)\}", src).group(1)
        player = re.search(r"String\[\] PLAYER_NAMES = \{([^}]*)\}", src).group(1)
        listed = re.findall(r'"([^"]+)"', names) + re.findall(r'"([^"]+)"', player)
        self.assertEqual(tuple(listed), rendertest.DEFAULT_VIEW_NAMES)
        views = re.search(r"double\[\]\[\] VIEWS = \{(.*?)\n    \};", src, re.S).group(1)
        rows = [r for r in views.splitlines() if r.strip().startswith("{")]
        self.assertEqual(len(rows), len(re.findall(r'"([^"]+)"', names)))
        for r in rows:
            self.assertEqual(len(r.split("//")[0].strip().strip("{},").split(",")), 6, r)  # eye xyz + target xyz
        # the diagnostics come first (guides on, after damage, guides off), then every clean model shot has the guides off
        off = int(re.search(r"GUIDES_OFF_FROM = (\d+)", src).group(1))
        hurt = int(re.search(r"HURT_AT = (\d+)", src).group(1))
        self.assertEqual(rendertest.DEFAULT_VIEW_NAMES[:3], ("outline-on", "outline-hurt", "outline-off"))
        self.assertEqual((hurt, off), (1, 2))
        self.assertEqual(rendertest.DEFAULT_VIEW_NAMES[3:8], ("front", "front-right", "front-left", "back", "close-up"))
        self.assertIn("mc.options.hideGui = view > GUIDES_OFF_FROM;", src)


class GlowParseTests(unittest.TestCase):
    def test_glow_lines_are_parsed_per_view(self):
        text = ("RT_GLOW view=1 glowing=false glowTag=false team=null highlight=false focus=null guideCfg=true indicatorCfg=true healthBarCfg=HURT\n"
                "RT_GLOW view=5 glowing=false glowTag=false team=null highlight=true focus=NightingaleEntity['N'/5] guideCfg=true\n"
                "RT_GLOW view=6 glowing=true glowTag=false team=red highlight=false focus=null guideCfg=false\n")
        got = rendertest.parse_glow(text)
        self.assertEqual([(g["view"], g["highlight"], g["glowing"], g["team"]) for g in got],
                         [(1, False, False, "null"), (5, True, False, "null"), (6, False, True, "red")])
        self.assertEqual(rendertest.parse_glow("nothing here"), [])


class ExpectationTests(unittest.TestCase):
    def test_custom_shape_expects_the_generated_vertex_count(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            f = root / "src/main/resources/assets/m/animmodels/entity/b.json"
            f.parent.mkdir(parents=True)
            f.write_text(json.dumps({"vertices": {"positions": {"array": [0.0] * 30}}}))
            self.assertEqual(rendertest.expected_mesh_vertices(root, "m", "b", {"custom": True}), 10)
            self.assertIsNone(rendertest.expected_mesh_vertices(root, "m", "b", {"custom": False}))

    def test_missing_model_is_an_error(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(BossForgeError) as cm:
                rendertest.expected_mesh_vertices(Path(d), "m", "b", {"custom": True})
            self.assertEqual(cm.exception.err.code, "model_missing")


class SkipTests(unittest.TestCase):
    def test_without_a_display_the_render_test_is_skipped_not_failed(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            raw = json.loads(Path(_path.SHIPPED_EXAMPLE).read_text())
            (root / "bossforge/bosses").mkdir(parents=True)
            (root / "bossforge/bosses/king_arthur.boss.json").write_text(json.dumps(raw))
            man = manifest.new_manifest("roundtable", "com.example.roundtable")
            man["bosses"]["king_arthur"] = {"spec": "bossforge/bosses/king_arthur.boss.json", "class": "KingArthur"}
            manifest.save(root, man)
            model = root / "src/main/resources/assets/roundtable/animmodels/entity/king_arthur.json"
            model.parent.mkdir(parents=True)
            model.write_text(json.dumps({"vertices": {"positions": {"array": [0.0] * 6}}}))
            env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
            with mock.patch.dict(os.environ, env, clear=True):
                res = rendertest.run(root)
            self.assertTrue(res["ok"])
            self.assertIn("no display", res["skipped"])


if __name__ == "__main__":
    unittest.main()
