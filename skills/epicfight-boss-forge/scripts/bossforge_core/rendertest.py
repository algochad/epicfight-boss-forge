"""`bossforge render-test`: render a boss in a real Forge dev client and photograph it.

Offline checks cannot prove a boss renders: Epic Fight picks the mesh from the mobpatch `renderer`, so a wrong
renderer silently draws the stock mesh with the custom texture scrambled across it. This runs the project in a
throwaway copy (`./gradlew runClient`) with a generated test class that joins a template world, summons the boss
in open air, takes screenshots from five angles and prints which mesh Epic Fight used (`RT_MESH`).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import List, Optional

from . import TEMPLATES_DIR, manifest as mf
from . import javagen
from .spec import BossForgeError, load, normalize

WORLD_NAME = "rendertest"
RUN_PROPERTIES = (
    "args '--quickPlaySingleplayer', '%s', '--width', '640', '--height', '360'" % WORLD_NAME,
    "jvmArgs '-Xmx2G'",
    # EF's mixins ship an SRG refmap; a dev (official-mapped) environment must remap it or EF fails to apply them.
    "property 'mixin.env.remapRefMap', 'true'",
    "property 'mixin.env.refMapRemappingFile', \"${projectDir}/build/createSrgToMcp/output.srg\"",
)
_CLIENT_RUN = re.compile(r"^([ \t]*)client\s*\{[ \t]*$", re.M)
_IGNORE = shutil.ignore_patterns("build", ".gradle", "run", ".git", ".idea", "*.iml")


def patch_build_gradle(text: str) -> str:
    """Add the test run settings to the `client` run configuration (anchor: the `client {` line, never a line number)."""
    if "mixin.env.remapRefMap" in text:
        return text
    m = _CLIENT_RUN.search(text)
    if m is None:
        raise BossForgeError("/build.gradle", "runs_layout_unexpected",
                             "no `client {` run configuration found in build.gradle (expected inside `minecraft { runs { ... } }`)")
    indent = m.group(1) + "    "
    block = "".join(indent + line + "\n" for line in RUN_PROPERTIES)
    return text[:m.end() + 1] + block + text[m.end() + 1:]


def java_prefix() -> List[str]:
    """Command prefix that runs Gradle on JDK 17."""
    home = os.environ.get("JAVA_HOME")
    candidates = [Path(home) / "bin" / "java"] if home else []
    which = shutil.which("java")
    if which:
        candidates.append(Path(which))
    for exe in candidates:
        try:
            out = subprocess.run([str(exe), "-version"], capture_output=True, text=True, timeout=30).stderr
        except (OSError, subprocess.SubprocessError):
            continue
        if re.search(r'version "17[."]', out):
            return []
    if shutil.which("mise"):
        return ["mise", "x", "java@temurin-17", "--"]
    raise BossForgeError("/java", "no_jdk17", "JDK 17 not found (set JAVA_HOME or install it, e.g. `mise use java@temurin-17`)")


def has_display() -> bool:
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def expected_mesh_vertices(project: Path, mod_id: str, boss_id: str, derived: dict) -> Optional[int]:
    """Position count Epic Fight must report for this boss (custom shapes only; stock meshes are not asserted)."""
    if not derived.get("custom"):
        return None
    f = project / "src" / "main" / "resources" / "assets" / mod_id / "animmodels" / "entity" / (boss_id + ".json")
    if not f.is_file():
        raise BossForgeError("/model", "model_missing", "%s missing; run add-boss first" % f)
    return len(json.loads(f.read_text(encoding="utf-8"))["vertices"]["positions"]["array"]) // 3


def run(project, boss_id: Optional[str] = None, out_dir=None, timeout: int = 900, keep: bool = False) -> dict:
    root = Path(project)
    man = mf.load(root)
    if man is None:
        raise BossForgeError("/project", "not_a_project", "%s has no bossforge/manifest.json; run add-boss first" % root)
    if not man.get("bosses"):
        raise BossForgeError("/project", "no_bosses", "the project has no boss to render")
    boss_id = boss_id or sorted(man["bosses"])[0]
    if boss_id not in man["bosses"]:
        raise BossForgeError("/boss", "unknown_boss", "boss %r not in %s" % (boss_id, ", ".join(sorted(man["bosses"]))))
    mod_id, package = man["mod_id"], man["package"]
    n = normalize(load(root / man["bosses"][boss_id]["spec"]))
    expected = expected_mesh_vertices(root, mod_id, boss_id, n["derived"])
    out = Path(out_dir) if out_dir else root / "bossforge" / "render-test"
    if not has_display():
        return {"ok": True, "skipped": "no display (set DISPLAY or WAYLAND_DISPLAY): the render test opens a Minecraft window", "boss": boss_id}
    prefix = java_prefix()

    work = Path(tempfile.mkdtemp(prefix="bossforge-rendertest-"))
    copy = work / "project"
    try:
        shutil.copytree(str(root), str(copy), ignore=_IGNORE, symlinks=True)
        gradle = copy / "build.gradle"
        gradle.write_text(patch_build_gradle(gradle.read_text(encoding="utf-8")), encoding="utf-8")
        pkg_dir = copy / "src" / "main" / "java" / Path(*package.split("."))
        test_dir = pkg_dir / "rendertest"
        test_dir.mkdir(parents=True, exist_ok=True)
        src = javagen.render(javagen.load_template("rendertest", "RenderTest.java.tpl"),
                             {"PACKAGE": package, "MOD_ID": mod_id, "BOSS_ID": boss_id}, "RenderTest")
        (test_dir / "RenderTest.java").write_text(src, encoding="utf-8")
        saves = copy / "run" / "saves" / WORLD_NAME
        saves.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(str(TEMPLATES_DIR / "rendertest" / "level.dat"), str(saves / "level.dat"))
        (copy / "run" / "options.txt").write_text("onboardAccessibility:false\nskipMultiplayerWarning:true\ntutorialStep:none\nlang:en_us\npauseOnLostFocus:false\n", encoding="utf-8")

        log = work / "client.log"
        started = time.time()
        with open(str(log), "w", encoding="utf-8") as fh:
            proc = subprocess.Popen(prefix + ["./gradlew", "--no-daemon", "runClient"], cwd=str(copy), stdout=fh, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            timed_out = False
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
        text = log.read_text(encoding="utf-8", errors="replace")
        shots = sorted((copy / "run" / "screenshots").glob("*.png"))
        out.mkdir(parents=True, exist_ok=True)
        for old in out.glob("*.png"):
            old.unlink()
        names = ["front", "front-right", "front-left", "back", "close-up"]
        saved = []
        for i, s in enumerate(shots):
            dest = out / ("%s_%s.png" % (boss_id, names[i] if i < len(names) else str(i + 1)))
            shutil.copyfile(str(s), str(dest))
            saved.append(str(dest))
        game_log = copy / "run" / "logs" / "latest.log"
        game_text = game_log.read_text(encoding="utf-8", errors="replace") if game_log.is_file() else ""
        problems = []
        m = re.search(r"^RT_MESH (\d+)", text, re.M)
        seen = int(m.group(1)) if m else None
        counts = [int(x) for x in re.findall(r"^RT_ENTITY view=\d+ count=(\d+)", text, re.M)]
        if counts and min(counts) == 0:
            problems.append("the boss entity was missing from the client at %d of %d screenshots (it was not summoned, not tracked or died)"
                            % (sum(1 for c in counts if c == 0), len(counts)))
        held = re.findall(r"^RT_HELD view=\d+ item=(\S+)", text, re.M)
        want = ("%s:%s" % (mod_id, n["main_weapon"])) if n.get("main_weapon") else None
        if want and held and any(h != want for h in held):
            problems.append("the boss is not holding its main weapon %s (main hand: %s): the weapon item is not registered or finalizeSpawn did not equip it"
                            % (want, held[-1]))
        if timed_out:
            problems.append("timed out after %ds" % timeout)
        if seen is None:
            problems.append("Epic Fight never drew the boss (no RT_MESH): the entity did not render, the world did not load, or the patch was rejected")
        elif expected is not None and seen != expected:
            problems.append("Epic Fight drew a mesh with %d positions but the custom mesh has %d: the mobpatch renderer ignores `model` "
                            "(a custom shape needs renderer `epicfight:custom` + `humanoid: true`)" % (seen, expected))
        if "Can't deserialize mob capability" in game_text or "Can't deserialize mob capability" in text:
            problems.append("Epic Fight rejected the mobpatch (log: `Can't deserialize mob capability`)")
        if re.search(r"^RT_DONE", text, re.M) is None and not timed_out:
            problems.append("the client exited before finishing the scripted run (see the Gradle output)")
        if len(saved) < 5:
            problems.append("only %d of 5 screenshots were taken" % len(saved))
        tail = "\n".join(text.splitlines()[-40:])
        result = {"ok": not problems, "boss": boss_id, "screenshots": saved, "mesh_positions_seen": seen,
                  "mesh_positions_expected": expected, "seconds": round(time.time() - started),
                  "entity_counts": counts, "held_items": held, "problems": problems,
                  "next": ["READ every screenshot and judge it against the boss description before approving",
                           "if `problems` is non-empty fix the cause; see references/render-pipeline.md (symptom table)"]}
        if problems:
            result["log_tail"] = tail
            (out / "client.log").write_text(text, encoding="utf-8")
        return result
    finally:
        if not keep:
            shutil.rmtree(str(work), ignore_errors=True)
