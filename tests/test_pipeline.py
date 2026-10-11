import _path  # noqa
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from bossforge_core import efassets, manifest, scaffold, spec
from bossforge_core.cli import main as cli_main
from _path import EXAMPLE

REF = spec.data("versions")["epicfight"]["assets_ref"]
CACHE = efassets.default_cache_dir()
HAVE_MESHES = all((CACHE / "ef" / REF[:8] / (n + ".json")).is_file() for n in ("biped", "iron_golem"))

MAIN = """package org.example.fake;

import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;

@Mod(FakeMod.MOD_ID)
public class FakeMod {
    public static final String MOD_ID = "roundtable";

    public FakeMod() {
        IEventBus modBus = FMLJavaModLoadingContext.get().getModEventBus();
    }
}
"""


def make_project(root: Path, mod_id="roundtable"):
    (root / "src/main/java/org/example/fake").mkdir(parents=True)
    (root / "src/main/java/org/example/fake/FakeMod.java").write_text(MAIN.replace("roundtable", mod_id))
    (root / "gradle.properties").write_text("mod_id=%s\n" % mod_id)
    return root


def run_cli(*argv):
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli_main(list(argv))
    return code, json.loads(buf.getvalue())


def golem_spec(tmp: Path) -> Path:
    p = tmp / "golem.json"
    code, out = run_cli("init", "--id", "stone_warden", "--name", "Stone Warden", "--mod-id", "roundtable", "--tier", "mini",
                        "--height", "2.6", "--width", "1.6", "--archetype", "golem", "--template", "iron_golem", "--out", str(p))
    assert code == 0, out
    return p


@unittest.skipUnless(HAVE_MESHES, "EF meshes not cached (run fetch-ef-assets)")
class AddBossTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.project = make_project(cls.tmp / "proj")
        cls.first = scaffold.add_boss(EXAMPLE, cls.project, CACHE, offline=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def res(self, rel):
        return self.project / "src/main/resources" / rel

    def test_first_run_succeeds_and_wires_existing_main_class(self):
        self.assertTrue(self.first["ok"], self.first)
        self.assertFalse(self.first["wiring_required"])
        main = (self.project / "src/main/java/org/example/fake/FakeMod.java").read_text()
        self.assertEqual(main.count("BossRegistry.register(modBus);"), 1)
        self.assertEqual(main.count("import org.example.fake.boss.BossRegistry;"), 1)
        self.assertTrue(any(w["code"] == "epicfight_dependency_missing" for w in self.first["warnings"]))

    def test_expected_resources(self):
        for rel in ("assets/roundtable/textures/entity/king_arthur.png", "assets/roundtable/textures/item/excalibur.png",
                    "assets/roundtable/textures/item/excalibur_gui.png", "data/roundtable/epicfight_mobpatch/king_arthur.json",
                    "data/roundtable/capabilities/weapons/excalibur.json", "data/roundtable/loot_tables/entities/king_arthur.json",
                    "data/roundtable/recipes/excalibur.json", "data/roundtable/recipes/king_arthur_sigil.json"):
            self.assertTrue(self.res(rel).is_file(), rel)
        patch = json.loads(self.res("data/roundtable/epicfight_mobpatch/king_arthur.json").read_text())
        self.assertEqual(patch["attributes"]["scale"], round(3.0 / spec.data("templates")["biped"]["model_height"], 3))
        self.assertTrue((self.project / "bossforge/previews/king_arthur_preview.png").is_file())
        self.assertTrue((self.project / "docs/bosses/king_arthur.md").is_file())

    def test_generated_java_is_consistent(self):
        for f in (self.project / "src/main/java").rglob("*.java"):
            text = f.read_text()
            self.assertNotIn("{{", text, f.name)
            self.assertRegex(text, r"public\s+(?:final\s+|abstract\s+)*(?:class|interface|record|enum)\s+%s\b" % f.stem)

    def test_rerun_is_all_unchanged_and_qa_clean(self):
        again = scaffold.add_boss(EXAMPLE, self.project, CACHE, offline=True)
        self.assertEqual(again["files"]["written"], [])
        self.assertEqual(again["files"]["updated"], [])
        self.assertEqual(again["files"]["conflicts"], [])
        self.assertTrue(again["qa"]["ok"], again["qa"])

    def test_hand_edit_conflicts_until_forced(self):
        proj = make_project(self.tmp / "conflict")
        scaffold.add_boss(EXAMPLE, proj, CACHE, offline=True)
        target = proj / "src/main/resources/data/roundtable/epicfight_mobpatch/king_arthur.json"
        original = target.read_bytes()
        target.write_bytes(original + b" ")
        res = scaffold.add_boss(EXAMPLE, proj, CACHE, offline=True)
        self.assertFalse(res["ok"])
        self.assertIn("src/main/resources/data/roundtable/epicfight_mobpatch/king_arthur.json", res["files"]["conflicts"])
        self.assertEqual(target.read_bytes(), original + b" ")
        res = scaffold.add_boss(EXAMPLE, proj, CACHE, offline=True, force=True)
        self.assertTrue(res["ok"])
        self.assertEqual(target.read_bytes(), original)

    def test_lang_never_overwrites_existing_keys_and_second_boss_joins_registry(self):
        proj = make_project(self.tmp / "second")
        lang = proj / "src/main/resources/assets/roundtable/lang/en_us.json"
        lang.parent.mkdir(parents=True)
        lang.write_text(json.dumps({"item.roundtable.excalibur": "My Own Name"}))
        scaffold.add_boss(EXAMPLE, proj, CACHE, offline=True)
        res = scaffold.add_boss(golem_spec(self.tmp), proj, CACHE, offline=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["template"], "iron_golem")
        data = json.loads(lang.read_text())
        self.assertEqual(data["item.roundtable.excalibur"], "My Own Name")
        self.assertIn("entity.roundtable.stone_warden", data)
        reg = (proj / "src/main/java/org/example/fake/boss/BossRegistry.java").read_text()
        self.assertIn("king_arthur", reg)
        self.assertIn("stone_warden", reg)
        man = manifest.load(proj)
        self.assertEqual(sorted(man["bosses"]), ["king_arthur", "stone_warden"])
        mp = json.loads((proj / "src/main/resources/data/roundtable/epicfight_mobpatch/stone_warden.json").read_text())
        self.assertFalse(mp["isHumanoid"])
        self.assertEqual(mp["stun_animations"], {})

    def test_mod_id_mismatch_is_rejected(self):
        proj = make_project(self.tmp / "other", mod_id="othermod")
        with self.assertRaises(spec.BossForgeError) as cm:
            scaffold.add_boss(EXAMPLE, proj, CACHE, offline=True)
        self.assertEqual(cm.exception.err.code, "mod_id_mismatch")

    def test_dry_run_writes_nothing(self):
        proj = make_project(self.tmp / "dry")
        res = scaffold.add_boss(EXAMPLE, proj, CACHE, offline=True, dry_run=True)
        self.assertTrue(res["files"]["written"])
        self.assertFalse((proj / "src/main/resources").exists())
        self.assertFalse((proj / "bossforge").exists())

    def test_invalid_spec_reports_errors_without_writing(self):
        proj = make_project(self.tmp / "invalid")
        bad = json.loads(EXAMPLE.read_text())
        bad["size"]["height"] = 99
        p = self.tmp / "bad.json"
        p.write_text(json.dumps(bad))
        res = scaffold.add_boss(p, proj, CACHE, offline=True)
        self.assertFalse(res["ok"])
        self.assertIn("/size/height", [e["path"] for e in res["errors"]])
        self.assertFalse((proj / "bossforge").exists())


class AddWeaponTests(unittest.TestCase):
    def test_weapon_only_project_registers_item_and_resources(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            proj = make_project(tmp / "proj")
            wspec = tmp / "w.json"
            wspec.write_text(json.dumps({"schema": spec.WEAPON_SCHEMA, "mod_id": "roundtable", "id": "oath_blade", "display_name": "Oath Blade",
                                         "category": "sword", "obtain": {"craft": True}}))
            res = scaffold.add_weapon(wspec, proj, CACHE, offline=True)
            self.assertTrue(res["ok"], res)
            self.assertTrue((proj / "src/main/resources/data/roundtable/capabilities/weapons/oath_blade.json").is_file())
            recipe = json.loads((proj / "src/main/resources/data/roundtable/recipes/oath_blade.json").read_text())
            self.assertIn("minecraft:diamond", json.dumps(recipe))
            self.assertIn("oath_blade", (proj / "src/main/java/org/example/fake/boss/BossRegistry.java").read_text())
            self.assertEqual(sorted(manifest.load(proj)["weapons"]), ["oath_blade"])
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_MESHES, "EF meshes not cached (run fetch-ef-assets)")
class CustomShapePipelineTests(unittest.TestCase):
    def test_shipped_example_generates_a_custom_mesh_wired_into_the_mobpatch(self):
        from _path import SHIPPED_EXAMPLE
        from bossforge_core import efmodel
        tmp = Path(tempfile.mkdtemp())
        try:
            proj = make_project(tmp / "proj")
            res = scaffold.add_boss(SHIPPED_EXAMPLE, proj, CACHE, offline=True)
            self.assertTrue(res["ok"], res)
            res_dir = proj / "src/main/resources"
            mesh = json.loads((res_dir / "assets/roundtable/animmodels/entity/king_arthur.json").read_text())
            self.assertEqual(efmodel.check_mesh_json(mesh), [])
            patch = json.loads((res_dir / "data/roundtable/epicfight_mobpatch/king_arthur.json").read_text())
            self.assertEqual(patch["model"], "roundtable:entity/king_arthur")
            self.assertEqual(patch["armature"], "epicfight:entity/biped")
            n = spec.normalize(spec.load(SHIPPED_EXAMPLE))
            self.assertEqual(patch["attributes"]["scale"], n["derived"]["scale"])
            self.assertTrue(res["qa"]["ok"], res["qa"])
            again = scaffold.add_boss(SHIPPED_EXAMPLE, proj, CACHE, offline=True)
            self.assertEqual(again["files"]["written"] + again["files"]["updated"] + again["files"]["conflicts"], [])
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_nightingale_example_is_a_complete_complex_character(self):
        """The reference-sheet example (wings, hairdo, ribbons, filigree, crystal staff) builds, has no stale
        `box:` globs (spec warnings) and ships the 64px staff sprite."""
        from _path import SKILL
        from bossforge_core import pngio
        ex = SKILL / "assets" / "examples" / "nightingale" / "boss.json"
        n, errs, warns = spec.check(json.loads(ex.read_text(encoding="utf-8")))
        self.assertEqual((errs, warns), ([], []))
        tmp = Path(tempfile.mkdtemp())
        try:
            proj = make_project(tmp / "proj")
            res = scaffold.add_boss(ex, proj, tmp / "empty-cache", offline=True)
            self.assertTrue(res["ok"], res)
            self.assertTrue(res["qa"]["ok"], res["qa"])
            tex = proj / "src/main/resources/assets/roundtable/textures/item/lullaby_staff.png"
            self.assertEqual(pngio.png_size(tex), (64, 64))
            self.assertEqual(pngio.png_size(tex.with_name("lullaby_staff_gui.png")), (32, 32))
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)

    def test_custom_shape_needs_no_epic_fight_mesh_download(self):
        from _path import SHIPPED_EXAMPLE
        tmp = Path(tempfile.mkdtemp())
        try:
            proj = make_project(tmp / "proj")
            res = scaffold.add_boss(SHIPPED_EXAMPLE, proj, tmp / "empty-cache", offline=True)
            self.assertTrue(res["ok"], res)
        finally:
            shutil.rmtree(str(tmp), ignore_errors=True)
