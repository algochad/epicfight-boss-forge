import _path  # noqa
import copy
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

from bossforge_core import javagen, manifest, scaffold, spec
from bossforge_core.spec import BossForgeError, make_context

BUILD_GRADLE = """plugins {
    id 'java'
}

repositories {
    // Put repositories for dependencies here
}

dependencies {
    minecraft "net.minecraftforge:forge:${minecraft_version}-${forge_version}"
}

publishing {
    repositories {
        maven { url "file://${project.projectDir}/mcmodsrepo" }
    }
}
"""
GRADLE_PROPERTIES = (
    "# header comment\n"
    "minecraft_version=1.20.1\n"
    "forge_version=47.4.10\n"
    "mod_id=examplemod\n"
    "mod_name=Example Mod\n"
    "mod_license=All Rights Reserved\n"
    "mod_version=1.0.0\n"
    "mod_group_id=com.example.examplemod\n"
    "mod_authors=YourNameHere\n"
    "mod_description=Example mod description."
)
MODS_TOML = (
    'modLoader="javafml"\n'
    '[[mods]]\n'
    'modId="${mod_id}"\n'
    '[[dependencies.${mod_id}]]\n'
    '    modId="forge"\n'
    '    mandatory=true\n'
)
MAIN_CLASS = """package com.example.demo;

import net.minecraftforge.eventbus.api.IEventBus;
import net.minecraftforge.fml.common.Mod;
import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;

@Mod(DemoMod.MODID)
public class DemoMod {
    public static final String MODID = "demo";

    public DemoMod() {
        IEventBus modEventBus = FMLJavaModLoadingContext.get().getModEventBus();
        modEventBus.addListener(this::setup);
    }
}
"""


def make_mdk(path, build_gradle=BUILD_GRADLE, prefix="", with_pack=False, skip=()):
    members = {
        "build.gradle": build_gradle,
        "gradlew": "#!/bin/sh\n",
        "gradle/wrapper/gradle-wrapper.jar": b"jar-stub",
        "gradle.properties": GRADLE_PROPERTIES,
        "src/main/resources/META-INF/mods.toml": MODS_TOML,
        "src/main/java/com/example/examplemod/ExampleMod.java": "package com.example.examplemod;\npublic class ExampleMod {}\n",
    }
    if with_pack:
        members["src/main/resources/pack.mcmeta"] = '{"pack":{"description":"x","pack_format":15}}'
    with zipfile.ZipFile(str(path), "w") as z:
        for name, content in members.items():
            if name not in skip:
                z.writestr(prefix + name, content)
    return Path(path)


def read_props(path):
    props = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            props[k] = v
    return props


class NewModTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.cache = self.tmp / "cache"

    def tearDown(self):
        self._tmp.cleanup()

    def run_new_mod(self, zip_path, out=None, **kw):
        kw.setdefault("author", "Test Author")
        return scaffold.new_mod("roundtable", "Round Table Bosses", out or self.tmp / "proj", mdk_zip=str(zip_path),
                                cache_dir=self.cache, offline=True, **kw)

    def test_patches_mdk_and_writes_project(self):
        out = self.tmp / "proj"
        result = self.run_new_mod(make_mdk(self.tmp / "mdk.zip"), out, description="Boss content.")
        self.assertTrue(result["ok"])
        self.assertEqual(result["package"], "com.bossforge.roundtable")

        gradle = (out / "build.gradle").read_text(encoding="utf-8").splitlines()
        i = gradle.index("repositories {")
        self.assertEqual(gradle[i + 1:i + 3], ["    exclusiveContent {", "        forRepository {"])
        self.assertIn('                url = "https://api.modrinth.com/maven"', gradle)
        self.assertIn("        forRepositories(fg.repository)", gradle)
        self.assertIn('            includeGroup "maven.modrinth"', gradle)
        j = gradle.index("dependencies {")
        self.assertEqual(gradle[j + 1], '    implementation fg.deobf("maven.modrinth:epic-fight:${epicfight_version}")')
        self.assertEqual(sum(1 for ln in gradle if "exclusiveContent" in ln), 1, "publishing repositories must stay untouched")

        props = read_props(out / "gradle.properties")
        self.assertEqual(props["mod_id"], "roundtable")
        self.assertEqual(props["mod_name"], "Round Table Bosses")
        self.assertEqual(props["mod_license"], "MIT")
        self.assertEqual(props["mod_version"], "0.1.0")
        self.assertEqual(props["mod_group_id"], "com.bossforge.roundtable")
        self.assertEqual(props["mod_authors"], "Test Author")
        self.assertEqual(props["mod_description"], "Boss content.")
        self.assertEqual(props["epicfight_version"], "20.14.17-mc1.20.1-forge")
        self.assertEqual(props["forge_version"], "47.4.10")
        self.assertEqual(props["minecraft_version"], "1.20.1")

        toml = (out / "src/main/resources/META-INF/mods.toml").read_text(encoding="utf-8")
        self.assertEqual(toml.count("[[dependencies.${mod_id}]]"), 2)
        self.assertIn('modId="forge"', toml)
        tail = toml[toml.index('modId="epicfight"') - len("    "):]
        self.assertEqual(tail, '    modId="epicfight"\n    mandatory=true\n    versionRange="[20.14.17,)"\n    ordering="AFTER"\n    side="BOTH"\n')

        main = out / "src/main/java/com/bossforge/roundtable/RoundtableMod.java"
        main_text = main.read_text(encoding="utf-8")
        self.assertIn("@Mod(RoundtableMod.MOD_ID)", main_text)
        self.assertIn("BossRegistry.register(modEventBus);", main_text)
        self.assertFalse((out / "src/main/java/com/example").exists(), "template mod sources must be removed")
        for name in javagen.CORE_CLASSES:
            self.assertTrue((out / "src/main/java/com/bossforge/roundtable/boss/core" / (name + ".java")).is_file(), name)
        registry = (out / "src/main/java/com/bossforge/roundtable/boss/BossRegistry.java").read_text(encoding="utf-8")
        self.assertIn("List.of()", registry)
        self.assertTrue(os.access(str(out / "gradlew"), os.X_OK))
        self.assertEqual(json.loads((out / "src/main/resources/pack.mcmeta").read_text(encoding="utf-8"))["pack"]["pack_format"], 15)

        man = manifest.load(out)
        self.assertEqual((man["mod_id"], man["package"], man["bosses"], man["weapons"]), ("roundtable", "com.bossforge.roundtable", {}, {}))
        rel = "src/main/java/com/bossforge/roundtable/boss/BossRegistry.java"
        self.assertEqual(man["generated"][rel], manifest.sha256((out / rel).read_bytes()))
        self.assertNotIn("src/main/java/com/bossforge/roundtable/RoundtableMod.java", man["generated"])

    def test_existing_pack_mcmeta_is_kept(self):
        out = self.tmp / "proj"
        self.run_new_mod(make_mdk(self.tmp / "mdk.zip", with_pack=True), out)
        self.assertEqual((out / "src/main/resources/pack.mcmeta").read_text(encoding="utf-8"),
                         '{"pack":{"description":"x","pack_format":15}}')

    def test_strips_single_top_level_folder(self):
        out = self.tmp / "proj"
        self.run_new_mod(make_mdk(self.tmp / "mdk.zip", prefix="forge-1.20.1-47.4.10-mdk/"), out)
        self.assertTrue((out / "build.gradle").is_file())
        self.assertFalse((out / "forge-1.20.1-47.4.10-mdk").exists())

    def test_rejects_build_gradle_without_anchors(self):
        for label, text in (("no dependencies", BUILD_GRADLE.replace("dependencies {", "deps {")),
                            ("indented repositories", BUILD_GRADLE.replace("\nrepositories {", "\n    repositories {", 1))):
            out = self.tmp / ("proj-" + label.replace(" ", "-"))
            with self.assertRaises(BossForgeError, msg=label) as cm:
                self.run_new_mod(make_mdk(self.tmp / "bad.zip", build_gradle=text), out)
            self.assertEqual(cm.exception.err.code, "mdk_layout_unexpected")
            self.assertIn("first 40 lines", cm.exception.err.message)
            self.assertFalse(out.exists(), "a rejected MDK must not leave a half-written project")

    def test_rejects_zip_missing_required_members(self):
        with self.assertRaises(BossForgeError) as cm:
            self.run_new_mod(make_mdk(self.tmp / "bad.zip", skip=("gradlew",)))
        self.assertEqual(cm.exception.err.code, "mdk_layout_unexpected")
        self.assertIn("gradlew", cm.exception.err.message)

    def test_requires_empty_output_directory(self):
        out = self.tmp / "proj"
        out.mkdir()
        (out / "keep.txt").write_text("x", encoding="utf-8")
        with self.assertRaises(BossForgeError) as cm:
            self.run_new_mod(make_mdk(self.tmp / "mdk.zip"), out)
        self.assertEqual(cm.exception.err.code, "out_not_empty")
        self.assertEqual((out / "keep.txt").read_text(encoding="utf-8"), "x")

    def test_empty_existing_directory_is_accepted(self):
        out = self.tmp / "proj"
        out.mkdir()
        self.run_new_mod(make_mdk(self.tmp / "mdk.zip"), out)
        self.assertTrue((out / "build.gradle").is_file())

    def test_download_retries_fallback_version_on_404_and_caches_it(self):
        blob = make_mdk(self.tmp / "src.zip").read_bytes()
        calls = []

        def fake_get(url, timeout=60):
            calls.append(url)
            if "47.4.10" in url:
                raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(b""))
            return blob

        with mock.patch.object(scaffold.efassets, "http_get", fake_get):
            first = scaffold.new_mod("roundtable", "Round Table Bosses", self.tmp / "one", author="A", cache_dir=self.cache)
            second = scaffold.new_mod("roundtable", "Round Table Bosses", self.tmp / "two", author="A", cache_dir=self.cache)
        self.assertEqual(len(calls), 3, calls)
        self.assertTrue(calls[0].endswith("forge-1.20.1-47.4.10-mdk.zip"))
        self.assertTrue(calls[1].endswith("forge-1.20.1-47.4.4-mdk.zip"))
        self.assertTrue(calls[2].endswith("forge-1.20.1-47.4.10-mdk.zip"), "second run only re-probes the primary version")
        self.assertEqual(Path(first["mdk"]), self.cache / "mdk" / "forge-1.20.1-47.4.4-mdk.zip")
        self.assertEqual(first["mdk"], second["mdk"])

    def test_download_failure_is_reported(self):
        def fake_get(url, timeout=60):
            raise urllib.error.URLError("no route")

        with mock.patch.object(scaffold.efassets, "http_get", fake_get):
            with self.assertRaises(BossForgeError) as cm:
                scaffold.new_mod("roundtable", "Round Table Bosses", self.tmp / "one", author="A", cache_dir=self.cache)
        self.assertEqual(cm.exception.err.code, "download_failed")
        self.assertFalse((self.tmp / "one").exists())

    def test_offline_without_cached_mdk_names_the_url(self):
        with self.assertRaises(BossForgeError) as cm:
            scaffold.new_mod("roundtable", "Round Table Bosses", self.tmp / "proj", cache_dir=self.cache, offline=True)
        self.assertEqual(cm.exception.err.code, "offline_missing")
        self.assertIn("forge-1.20.1-47.4.10-mdk.zip", cm.exception.err.message)

    def test_rejects_unusable_names(self):
        zip_path = make_mdk(self.tmp / "mdk.zip")
        cases = (("do", "Name", "com.bossforge", "reserved_java_word"),
                 ("roundtable", "Name", "com.new.stuff", "reserved_java_word"),
                 ("Round", "Name", "com.bossforge", "bad_mod_id"),
                 ("roundtable", 'Bad "Name"', "com.bossforge", "bad_mod_name"),
                 ("roundtable", "Name", "Com.BossForge", "bad_group"))
        for mod_id, name, group, code in cases:
            with self.assertRaises(BossForgeError, msg=(mod_id, name, group)) as cm:
                scaffold.new_mod(mod_id, name, self.tmp / "p", group=group, mdk_zip=str(zip_path), cache_dir=self.cache, offline=True)
            self.assertEqual(cm.exception.err.code, code)

    def test_non_ascii_and_multiline_values_survive_properties_encoding(self):
        out = self.tmp / "proj"
        self.run_new_mod(make_mdk(self.tmp / "mdk.zip"), out, description="Line one\nL\u00e9on")
        raw = (out / "gradle.properties").read_text(encoding="utf-8")
        self.assertTrue(raw.isascii(), "gradle reads .properties as ISO-8859-1, so values must be ASCII")
        self.assertIn("mod_description=Line one\\nL\\u00e9on", raw)

    def test_build_gradle_patch_is_idempotent(self):
        once = scaffold.patch_build_gradle(BUILD_GRADLE)
        self.assertEqual(scaffold.patch_build_gradle(once), once)
        crlf = scaffold.patch_build_gradle(BUILD_GRADLE.replace("\n", "\r\n"))
        self.assertNotIn("\r\r", crlf)
        self.assertEqual(crlf.count("\n"), crlf.count("\r\n"))

    def test_dependency_warnings_are_clean_after_new_mod(self):
        out = self.tmp / "proj"
        self.run_new_mod(make_mdk(self.tmp / "mdk.zip"), out)
        self.assertEqual(scaffold.dependency_warnings(out), [])


class ExistingProjectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def project(self, mod_id="demo", main=MAIN_CLASS, relpath="src/main/java/com/example/demo/DemoMod.java"):
        (self.root / "gradle.properties").write_text("mod_id=%s\nmod_name=Demo\n" % mod_id, encoding="utf-8")
        p = self.root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(main, encoding="utf-8")
        return p

    def test_detects_package_and_main_class_through_constant(self):
        p = self.project()
        pkg, path = scaffold.detect_project(self.root, "demo")
        self.assertEqual((pkg, path), ("com.example.demo", p))

    def test_detects_literal_mod_annotation(self):
        p = self.project(main=MAIN_CLASS.replace("@Mod(DemoMod.MODID)", '@Mod("demo")'))
        self.assertEqual(scaffold.detect_project(self.root, "demo")[1], p)

    def test_mod_id_mismatch(self):
        self.project(mod_id="other")
        with self.assertRaises(BossForgeError) as cm:
            scaffold.detect_project(self.root, "demo")
        self.assertEqual(cm.exception.err.code, "mod_id_mismatch")
        self.assertIn("other", cm.exception.err.message)

    def test_missing_gradle_properties_is_a_mismatch(self):
        with self.assertRaises(BossForgeError) as cm:
            scaffold.detect_project(self.root, "demo")
        self.assertEqual(cm.exception.err.code, "mod_id_mismatch")

    def test_no_main_class(self):
        self.project(main=MAIN_CLASS.replace("@Mod(DemoMod.MODID)", "@Mod.EventBusSubscriber(modid = \"demo\")"))
        with self.assertRaises(BossForgeError) as cm:
            scaffold.detect_project(self.root, "demo")
        self.assertEqual(cm.exception.err.code, "no_main_class")

    def test_main_class_for_a_different_mod_id_is_not_picked(self):
        self.project(main=MAIN_CLASS.replace('MODID = "demo"', 'MODID = "elsewhere"'))
        with self.assertRaises(BossForgeError) as cm:
            scaffold.detect_project(self.root, "demo")
        self.assertEqual(cm.exception.err.code, "no_main_class")

    def test_dependency_warnings_for_a_mod_without_epic_fight(self):
        (self.root / "build.gradle").write_text("repositories {\n}\ndependencies {\n}\n", encoding="utf-8")
        toml = self.root / "src/main/resources/META-INF/mods.toml"
        toml.parent.mkdir(parents=True)
        toml.write_text('[[mods]]\nmodId="demo"\n', encoding="utf-8")
        warns = scaffold.dependency_warnings(self.root)
        self.assertEqual([w.code for w in warns], ["epicfight_dependency_missing"] * 2)
        text = "\n".join(w.message for w in warns)
        for needle in ('includeGroup "maven.modrinth"', 'implementation fg.deobf("maven.modrinth:epic-fight:${epicfight_version}")',
                       "epicfight_version=20.14.17-mc1.20.1-forge", 'modId="epicfight"', 'versionRange="[20.14.17,)"'):
            self.assertIn(needle, text)

    def test_dependency_warnings_clear_when_declared(self):
        (self.root / "build.gradle").write_text('dependencies {\n  implementation fg.deobf("maven.modrinth:epic-fight:1")\n}\n', encoding="utf-8")
        toml = self.root / "src/main/resources/META-INF/mods.toml"
        toml.parent.mkdir(parents=True)
        toml.write_text('[[dependencies.demo]]\n    modId = "epicfight"\n', encoding="utf-8")
        self.assertEqual(scaffold.dependency_warnings(self.root), [])


class WireMainClassTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "DemoMod.java"

    def tearDown(self):
        self._tmp.cleanup()

    def wire(self, text, **kw):
        self.path.write_text(text, encoding="utf-8", newline="")
        return scaffold.wire_main_class(self.path, "com.example.demo", **kw)

    def test_inserts_call_and_import_exactly_once(self):
        result = self.wire(MAIN_CLASS)
        self.assertTrue(result["changed"])
        self.assertFalse(result["wiring_required"])
        text = self.path.read_text(encoding="utf-8")
        lines = text.splitlines()
        i = lines.index("        IEventBus modEventBus = FMLJavaModLoadingContext.get().getModEventBus();")
        self.assertEqual(lines[i + 1], "        BossRegistry.register(modEventBus);")
        self.assertEqual(text.count("BossRegistry.register("), 1)
        self.assertEqual(text.count("import com.example.demo.boss.BossRegistry;"), 1)
        last_import = max(k for k, ln in enumerate(lines) if ln.startswith("import "))
        self.assertEqual(lines[last_import], "import com.example.demo.boss.BossRegistry;")

        again = self.wire(text)
        self.assertFalse(again["changed"])
        self.assertFalse(again["wiring_required"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_uses_the_variable_name_of_the_file(self):
        self.wire(MAIN_CLASS.replace("modEventBus", "bus"))
        self.assertIn("        BossRegistry.register(bus);", self.path.read_text(encoding="utf-8"))

    def test_dry_run_does_not_write(self):
        result = self.wire(MAIN_CLASS, dry_run=True)
        self.assertTrue(result["changed"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), MAIN_CLASS)

    def test_preserves_crlf_line_endings(self):
        self.wire(MAIN_CLASS.replace("\n", "\r\n"))
        raw = self.path.read_bytes().decode("utf-8")
        self.assertIn("BossRegistry.register(modEventBus);", raw)
        self.assertEqual(raw.count("\n"), raw.count("\r\n"))

    def test_reports_wiring_required_when_bus_pattern_is_absent(self):
        text = MAIN_CLASS.replace("FMLJavaModLoadingContext.get().getModEventBus()", "makeBus()")
        result = self.wire(text)
        self.assertTrue(result["wiring_required"])
        self.assertFalse(result["changed"])
        self.assertIn("BossRegistry.register(", result["line"])
        self.assertIn("import com.example.demo.boss.BossRegistry;", result["imports"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_static_field_bus_is_not_wired_at_class_level(self):
        text = MAIN_CLASS.replace("        IEventBus modEventBus = FMLJavaModLoadingContext.get().getModEventBus();\n", "").replace(
            "    public DemoMod() {",
            "    private static final IEventBus BUS = FMLJavaModLoadingContext.get().getModEventBus();\n\n    public DemoMod() {")
        result = self.wire(text)
        self.assertTrue(result["wiring_required"])
        self.assertEqual(self.path.read_text(encoding="utf-8"), text)

    def test_commented_out_bus_statement_is_ignored(self):
        text = MAIN_CLASS.replace("        IEventBus modEventBus = FMLJavaModLoadingContext.get().getModEventBus();",
                                  "        // IEventBus modEventBus = FMLJavaModLoadingContext.get().getModEventBus();")
        self.assertTrue(self.wire(text)["wiring_required"])


# --------------------------------------------------------------------------- generated Java
def strip_java(text):
    """Blank comments, string and char literals so bracket counting only sees code."""
    out = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def king_arthur():
    return spec.load(_path.EXAMPLE)


class JavaGenTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.ctx = make_context(self.root, "roundtable", "com.bossforge.roundtable", self.root / "cache", offline=True)
        self.n = spec.normalize(king_arthur())
        self.man = manifest.new_manifest("roundtable", "com.bossforge.roundtable")

    def tearDown(self):
        self._tmp.cleanup()

    def all_files(self, n=None):
        n = n or self.n
        files = javagen.core_files(self.ctx)
        f2, lang = javagen.generate(n, self.ctx)
        files += f2
        files.append(javagen.generate_registry(self.root, self.ctx, self.man, bosses={n["id"]: n}))
        return files, lang

    def by_path(self, files):
        return {f.relpath: f.data.decode("utf-8") for f in files}

    def java(self, files):
        return {rel: text for rel, text in self.by_path(files).items() if rel.endswith(".java")}

    # ---- structure -------------------------------------------------------------------------------------------
    def test_no_unreplaced_placeholders_and_ascii_only(self):
        files, _ = self.all_files()
        for rel, text in self.by_path(files).items():
            self.assertNotIn("{{", text, rel)
            self.assertTrue(text.isascii(), rel)

    def test_file_names_match_public_classes_and_packages(self):
        files, _ = self.all_files()
        sources = self.java(files)
        self.assertEqual(len(sources), len(javagen.CORE_CLASSES) + len(javagen.MOD_CLASSES) + 2)
        for rel, text in sources.items():
            m = re.search(r"public\s+(?:final\s+|abstract\s+)*(?:class|record|interface|enum)\s+(\w+)", text)
            self.assertIsNotNone(m, rel)
            self.assertEqual(m.group(1), Path(rel).stem, rel)
            pkg = re.search(r"^package\s+([\w.]+);", text, re.M).group(1)
            self.assertEqual(pkg, str(Path(rel).parent).replace("src/main/java/", "").replace("/", "."), rel)

    def test_brackets_are_balanced(self):
        files, _ = self.all_files()
        for rel, text in self.java(files).items():
            code = strip_java(text)
            for open_c, close_c in ("{}", "()", "[]"):
                self.assertEqual(code.count(open_c), code.count(close_c), "%s: %s%s" % (rel, open_c, close_c))

    def test_paths_are_project_relative_with_forward_slashes(self):
        files, _ = self.all_files()
        for f in files:
            self.assertFalse(f.relpath.startswith("/"))
            self.assertNotIn("\\", f.relpath)
            self.assertTrue(f.relpath.startswith(("src/main/java/", "src/main/resources/")), f.relpath)

    def test_generation_is_deterministic(self):
        a, lang_a = self.all_files()
        b, lang_b = self.all_files()
        self.assertEqual([(f.relpath, f.data) for f in a], [(f.relpath, f.data) for f in b])
        self.assertEqual(lang_a, lang_b)

    # ---- epic fight attributes -------------------------------------------------------------------------------
    def test_mod_events_register_epic_fight_attributes(self):
        files, _ = self.all_files()
        events = self.by_path(files)["src/main/java/com/bossforge/roundtable/boss/BossModEvents.java"]
        for attr in ("WEIGHT", "ARMOR_NEGATION", "IMPACT", "MAX_STRIKES", "STUN_ARMOR"):
            self.assertIn("EpicFightAttributes.%s.get()" % attr, events)
        humanoid_block = events[events.index("if (def.humanoid())"):]
        for attr in ("OFFHAND_ATTACK_SPEED", "OFFHAND_MAX_STRIKES", "OFFHAND_ARMOR_NEGATION", "OFFHAND_IMPACT"):
            self.assertIn("EpicFightAttributes.%s.get()" % attr, humanoid_block)
            self.assertNotIn("EpicFightAttributes.%s.get()" % attr, events[:events.index("if (def.humanoid())")])
        self.assertIn('modid = "roundtable"', events)

    # ---- entity ----------------------------------------------------------------------------------------------
    def test_entity_profile_carries_the_spec(self):
        files, _ = self.all_files()
        entity = self.by_path(files)["src/main/java/com/bossforge/roundtable/boss/king_arthur/KingArthurEntity.java"]
        for needle in ("package com.bossforge.roundtable.boss.king_arthur;", "public class KingArthurEntity extends AbstractBossEntity",
                       '"king_arthur", "roundtable"', "400.0D, 12.0D, 6.0D, 10.0D, 0.26D, 0.8D, 48.0D", "500,",
                       '"roundtable:excalibur"', "true, BossEvent.BossBarColor.PURPLE, BossEvent.BossBarOverlay.NOTCHED_10",
                       "new float[] {1.0F, 0.66F, 0.33F}", "new boolean[] {false, true, true}",
                       'AbilitySpec.summon("minecraft:vindicator", 2, 2, 3.0F)', "AbilitySpec.regen(2.0F, 3)",
                       "AbilitySpec.enrage(3, 15.0F, 15.0F)", '"minecraft:entity.piglin_brute.hurt", "minecraft:entity.piglin_brute.death", null'):
            self.assertIn(needle, entity)

    def test_strings_with_quotes_backslashes_and_non_ascii_are_escaped(self):
        raw = king_arthur()
        raw["sounds"] = {"hurt": 'x:"hurt"', "death": "x:caf\u00e9"}
        raw["abilities"] = [{"type": "on_hit_effect", "effect": 'x:a\\b"c\u00e9'}]
        n = spec.normalize(raw)
        files, _ = self.all_files(n)
        entity = self.by_path(files)["src/main/java/com/bossforge/roundtable/boss/king_arthur/KingArthurEntity.java"]
        self.assertIn(r'"x:\"hurt\""', entity)
        self.assertIn(r'"x:caf\u00e9"', entity)
        self.assertIn(r'AbilitySpec.onHitEffect("x:a\\b\"c\u00e9", 100, 0, 1.0F)', entity)

    def test_boss_id_that_is_a_java_keyword_is_rejected(self):
        raw = king_arthur()
        raw["id"] = "int"
        with self.assertRaises(BossForgeError) as cm:
            javagen.generate(spec.normalize(raw), self.ctx)
        self.assertEqual(cm.exception.err.code, "reserved_java_word")

    def test_boss_id_clashing_with_a_core_class_is_rejected(self):
        raw = king_arthur()
        raw["id"] = "abstract_boss"
        with self.assertRaises(BossForgeError) as cm:
            javagen.generate(spec.normalize(raw), self.ctx)
        self.assertEqual(cm.exception.err.code, "reserved_class_name")

    def test_spec_mod_id_must_match_the_project(self):
        raw = king_arthur()
        raw["mod_id"] = "elsewhere"
        with self.assertRaises(BossForgeError) as cm:
            javagen.generate(spec.normalize(raw), self.ctx)
        self.assertEqual(cm.exception.err.code, "mod_id_mismatch")

    # ---- registry --------------------------------------------------------------------------------------------
    def test_registry_registers_boss_items_and_standalone_weapons(self):
        weapons_dir = self.root / "bossforge" / "weapons"
        weapons_dir.mkdir(parents=True)
        (weapons_dir / "moonblade.weapon.json").write_text(json.dumps(
            {"id": "moonblade", "display_name": "Moon Blade", "category": "dagger", "mod_id": "roundtable", "lore": ["Cold."]}), encoding="utf-8")
        self.man["weapons"]["moonblade"] = {"spec": "bossforge/weapons/moonblade.weapon.json"}
        files, _ = self.all_files()
        registry = self.by_path(files)["src/main/java/com/bossforge/roundtable/boss/BossRegistry.java"]
        for needle in ('ENTITIES.register("king_arthur"', ".sized(1.0F, 3.0F)", "KingArthurEntity::new", "MobCategory.MONSTER",
                       'ITEMS.register("excalibur"', "new BossWeaponItem(new BossTier(2031), 8, -2.4F, new Item.Properties().rarity(Rarity.EPIC)",
                       'List.of(new EffectSpec("minecraft:slowness", 60, 0, 0.3F)), 2, MOD_ID, "excalibur")',
                       'ITEMS.register("king_arthur_essence"',
                       'ITEMS.register("king_arthur_sigil"', 'new BossSigilItem(new Item.Properties().stacksTo(16).rarity(Rarity.RARE), ENTITY_KING_ARTHUR, "minecraft:enchanting_table", null, true, MOD_ID)',
                       'ITEMS.register("king_arthur_spawn_egg"', "new ForgeSpawnEggItem(ENTITY_KING_ARTHUR, 0x9AA5B8, 0x8C1A20,",
                       'ITEMS.register("moonblade"', "new BossWeaponItem(new BossTier(900), 3, -1.6F", "List.of(), 1, MOD_ID, \"moonblade\")",
                       'new BossDef("king_arthur", ENTITY_KING_ARTHUR, KingArthurEntity.PROFILE, true, false)',
                       "import com.bossforge.roundtable.boss.king_arthur.KingArthurEntity;",
                       "output.accept(WEAPON_EXCALIBUR.get());", "output.accept(WEAPON_MOONBLADE.get());",
                       "output.accept(EGG_KING_ARTHUR.get());", ".icon(() -> new ItemStack(WEAPON_EXCALIBUR.get()))"):
            self.assertIn(needle, registry)

    def test_registry_reads_specs_listed_in_the_manifest(self):
        (self.root / "bossforge" / "bosses").mkdir(parents=True)
        (self.root / "bossforge" / "bosses" / "king_arthur.boss.json").write_text(json.dumps(king_arthur()), encoding="utf-8")
        self.man["bosses"]["king_arthur"] = {"spec": "bossforge/bosses/king_arthur.boss.json", "class": "KingArthur"}
        from_manifest = javagen.generate_registry(self.root, self.ctx, self.man)
        from_memory = javagen.generate_registry(self.root, self.ctx, self.man, bosses={"king_arthur": self.n})
        self.assertEqual(from_manifest.data, from_memory.data)
        self.assertIn(b"KingArthurEntity", from_manifest.data)

    def test_registry_with_missing_spec_file_fails(self):
        self.man["bosses"]["ghost"] = {"spec": "bossforge/bosses/ghost.boss.json"}
        with self.assertRaises(BossForgeError) as cm:
            javagen.generate_registry(self.root, self.ctx, self.man)
        self.assertEqual(cm.exception.err.code, "spec_missing")

    def test_empty_registry_compiles_without_icon_item(self):
        text = javagen.generate_registry(self.root, self.ctx, self.man).data.decode("utf-8")
        self.assertIn("List.of();", text)
        self.assertIn("new ItemStack(Items.NETHER_STAR)", text)
        self.assertNotIn("RegistryObject<EntityType<", text)
        self.assertNotIn("output.accept", text)

    def test_registry_lists_every_boss_sorted_by_id(self):
        other = copy.deepcopy(king_arthur())
        other.update({"id": "black_knight", "weapons": [], "main_weapon": None})
        other["display_name"] = "Black Knight"
        other["encounter"] = {"trigger": "command"}
        other["tier"] = "quest"
        m = spec.normalize(other)
        self.man["bosses"]["black_knight"] = {"spec": "x"}
        text = javagen.generate_registry(self.root, self.ctx, self.man, bosses={"king_arthur": self.n, "black_knight": m}).data.decode("utf-8")
        self.assertLess(text.index("ENTITY_BLACK_KNIGHT ="), text.index("ENTITY_KING_ARTHUR ="))
        self.assertNotIn("SIGIL_BLACK_KNIGHT", text)
        self.assertIn("SIGIL_KING_ARTHUR", text)
        self.assertIn('new BossDef("black_knight"', text)

    def test_golem_boss_is_not_humanoid_and_natural_spawn_is_flagged(self):
        raw = king_arthur()
        raw.update({"body": {"template": "iron_golem"}, "weapons": [], "main_weapon": None, "tier": "mini", "encounter": {}})
        raw["size"] = {"height": 2.6, "width": 1.6}
        raw.pop("abilities")
        raw.pop("phases")
        n = spec.normalize(raw)
        self.assertEqual(n["derived"]["template"], "iron_golem")
        text = javagen.generate_registry(self.root, self.ctx, self.man, bosses={"king_arthur": n}).data.decode("utf-8")
        self.assertIn("KingArthurEntity.PROFILE, false, true)", text)
        self.assertNotIn("SIGIL_KING_ARTHUR", text)

    def test_duplicate_item_ids_are_rejected(self):
        clash = copy.deepcopy(king_arthur())
        clash["id"] = "mordred"
        clash["main_weapon"] = None
        with self.assertRaises(BossForgeError) as cm:
            javagen.generate_registry(self.root, self.ctx, self.man, bosses={"king_arthur": self.n, "mordred": spec.normalize(clash)})
        self.assertEqual(cm.exception.err.code, "duplicate_item_id")
        self.assertIn("excalibur", cm.exception.err.message)

    def test_standalone_weapon_clashing_with_boss_item_is_rejected(self):
        weapon = spec.normalize_weapon_spec({"id": "king_arthur_essence", "category": "sword", "mod_id": "roundtable"})
        with self.assertRaises(BossForgeError) as cm:
            javagen.generate_registry(self.root, self.ctx, self.man, bosses={"king_arthur": self.n},
                                      weapons={"king_arthur_essence": weapon})
        self.assertEqual(cm.exception.err.code, "duplicate_item_id")

    # ---- encounter + progression resources -------------------------------------------------------------------
    def test_summon_item_encounter_resources(self):
        files, _ = self.all_files()
        by = self.by_path(files)
        recipe = json.loads(by["src/main/resources/data/roundtable/recipes/king_arthur_sigil.json"])
        self.assertEqual(recipe["type"], "minecraft:crafting_shaped")
        self.assertEqual(recipe["pattern"], [" N ", "NSN", " N "])
        self.assertEqual(recipe["key"], {"N": {"item": "minecraft:netherite_ingot"}, "S": {"item": "minecraft:nether_star"}})
        self.assertEqual(recipe["result"], {"item": "roundtable:king_arthur_sigil", "count": 1})
        self.assertNotIn("src/main/resources/data/roundtable/functions/summon_king_arthur.mcfunction", by)
        self.assertFalse([p for p in by if "biome_modifier" in p])

    def test_custom_and_null_sigil_recipes(self):
        raw = king_arthur()
        raw["encounter"]["summon_item"]["recipe"] = {"pattern": ["AA"], "key": {"A": "#minecraft:logs"}}
        files, _ = self.all_files(spec.normalize(raw))
        recipe = json.loads(self.by_path(files)["src/main/resources/data/roundtable/recipes/king_arthur_sigil.json"])
        self.assertEqual(recipe["pattern"], ["AA"])
        self.assertEqual(recipe["key"], {"A": {"tag": "minecraft:logs"}})
        raw["encounter"]["summon_item"]["recipe"] = None
        files, _ = self.all_files(spec.normalize(raw))
        self.assertNotIn("src/main/resources/data/roundtable/recipes/king_arthur_sigil.json", self.by_path(files))

    def test_hidden_tier_has_no_sigil_recipe_and_a_hidden_advancement(self):
        raw = king_arthur()
        raw["tier"] = "hidden"
        files, _ = self.all_files(spec.normalize(raw))
        by = self.by_path(files)
        self.assertNotIn("src/main/resources/data/roundtable/recipes/king_arthur_sigil.json", by)
        adv = json.loads(by["src/main/resources/data/roundtable/advancements/bosses/king_arthur.json"])
        self.assertTrue(adv["display"]["hidden"])
        self.assertEqual(adv["display"]["frame"], "challenge")
        self.assertNotIn("king_arthur_spawn_egg", by["src/main/java/com/bossforge/roundtable/boss/BossRegistry.java"])
        self.assertIn("king_arthur_sigil", by["src/main/java/com/bossforge/roundtable/boss/BossRegistry.java"])

    def test_command_trigger_writes_a_summon_function(self):
        raw = king_arthur()
        raw["tier"] = "quest"
        raw["encounter"] = {}
        raw["phases"] = [{"name": "One", "health_below": 1.0}, {"name": "Two", "health_below": 0.5}]
        raw["abilities"] = []
        files, _ = self.all_files(spec.normalize(raw))
        by = self.by_path(files)
        self.assertEqual(by["src/main/resources/data/roundtable/functions/summon_king_arthur.mcfunction"], "summon roundtable:king_arthur ~ ~ ~\n")
        self.assertFalse([p for p in by if p.endswith("_sigil.json")])
        adv = json.loads(by["src/main/resources/data/roundtable/advancements/bosses/king_arthur.json"])
        self.assertEqual(adv["display"]["frame"], "goal")
        self.assertFalse(adv["display"]["hidden"])
        self.assertEqual([f.kind for f in files if f.relpath.endswith(".mcfunction")], ["mcfunction"])

    def test_natural_spawn_trigger_writes_a_biome_modifier(self):
        raw = king_arthur()
        raw["tier"] = "mini"
        raw["encounter"] = {"natural_spawn": {"biomes": "#minecraft:is_forest", "weight": 4}}
        raw["abilities"] = []
        raw["phases"] = [{"name": "Hostile", "health_below": 1.0}]
        files, _ = self.all_files(spec.normalize(raw))
        by = self.by_path(files)
        mod = json.loads(by["src/main/resources/data/roundtable/forge/biome_modifier/king_arthur_spawn.json"])
        self.assertEqual(mod, {"type": "forge:add_spawns", "biomes": "#minecraft:is_forest",
                               "spawners": {"type": "roundtable:king_arthur", "weight": 4, "minCount": 1, "maxCount": 1}})
        adv = json.loads(by["src/main/resources/data/roundtable/advancements/bosses/king_arthur.json"])
        self.assertEqual(adv["display"]["frame"], "task")
        self.assertIn("KingArthurEntity.PROFILE, true, true)", by["src/main/java/com/bossforge/roundtable/boss/BossRegistry.java"])

    def test_default_natural_spawn_biomes(self):
        raw = king_arthur()
        raw["tier"] = "mini"
        raw["encounter"] = {}
        files, _ = self.all_files(spec.normalize(raw))
        mod = json.loads(self.by_path(files)["src/main/resources/data/roundtable/forge/biome_modifier/king_arthur_spawn.json"])
        self.assertEqual(mod["biomes"], "#minecraft:is_overworld")
        self.assertEqual(mod["spawners"]["weight"], 1)

    def test_advancement_shape(self):
        files, _ = self.all_files()
        adv = json.loads(self.by_path(files)["src/main/resources/data/roundtable/advancements/bosses/king_arthur.json"])
        self.assertEqual(adv["display"]["icon"], {"item": "roundtable:excalibur"})
        self.assertEqual(adv["display"]["title"], {"translate": "advancements.roundtable.king_arthur.title"})
        self.assertEqual(adv["criteria"]["kill"], {"trigger": "minecraft:player_killed_entity",
                                                    "conditions": {"entity": {"type": "roundtable:king_arthur"}}})
        self.assertEqual(adv["requirements"], [["kill"]])

    def test_advancement_icon_falls_back_to_essence_without_weapons(self):
        raw = king_arthur()
        raw["weapons"] = []
        raw["main_weapon"] = None
        files, _ = self.all_files(spec.normalize(raw))
        adv = json.loads(self.by_path(files)["src/main/resources/data/roundtable/advancements/bosses/king_arthur.json"])
        self.assertEqual(adv["display"]["icon"], {"item": "roundtable:king_arthur_essence"})

    def test_lang_keys(self):
        _, lang = self.all_files()
        self.assertEqual(lang["entity.roundtable.king_arthur"], "King Arthur")
        self.assertEqual(lang["advancements.roundtable.king_arthur.title"], "King Arthur Vanquished")
        self.assertEqual(lang["advancements.roundtable.king_arthur.description"], "Defeat King Arthur, The Once and Future King.")
        self.assertEqual(lang["itemGroup.roundtable.bosses"], "Round Table Bosses")
        self.assertEqual(lang["bossforge.roundtable.king_arthur.phase.2"], "The king staggers, but his oath holds.")
        self.assertEqual(lang["bossforge.roundtable.king_arthur.phase.3"], "Arthur raises Excalibur for the final time.")
        self.assertNotIn("bossforge.roundtable.king_arthur.phase.1", lang)
        self.assertIn("message.roundtable.sigil.wrong_block", lang)
        self.assertIn("message.roundtable.sigil.wrong_dimension", lang)

    def test_tab_title_gets_bosses_suffix_when_missing(self):
        raw = king_arthur()
        raw["mod_name"] = "Round Table"
        _, lang = self.all_files(spec.normalize(raw))
        self.assertEqual(lang["itemGroup.roundtable.bosses"], "Round Table Bosses")

    def test_generate_leaves_item_assets_to_the_weapon_generator(self):
        files, lang = self.all_files()
        for f in files:
            self.assertNotIn("models/item", f.relpath)
            self.assertNotIn("loot_tables", f.relpath)
            self.assertNotIn("textures", f.relpath)
            self.assertNotIn("recipes/excalibur", f.relpath)
        self.assertFalse([k for k in lang if k.startswith("item.")])


if __name__ == "__main__":
    unittest.main()
