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
    def test_test_class_has_no_placeholders_and_names_the_boss(self):
        src = javagen.render(javagen.load_template("rendertest", "RenderTest.java.tpl"),
                             {"PACKAGE": "com.example.mod", "MOD_ID": "mymod", "BOSS_ID": "iron_lord"}, "RenderTest")
        self.assertNotIn("{{", src)
        self.assertIn("package com.example.mod.rendertest;", src)
        self.assertIn('"mymod:iron_lord"', src)
        # the harness must report which mesh Epic Fight drew and whether the boss existed at each shot
        self.assertIn("RT_MESH", src)
        self.assertIn("RT_ENTITY", src)


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
