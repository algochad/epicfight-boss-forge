#!/usr/bin/env python3
"""Compare `bossforge_core.efanim` with the REAL Epic Fight classes.

`tests/tools/efanim_java/Harness.java` runs EF's own `JsonAssetLoader.getJoint / getTransformSheet`, `Joint.initOriginTransform`,
`TransformSheet.getInterpolatedTransform` and `JointTransform.getAnimationBoundMatrix` (loaded from the mod jar; only the handful of
Minecraft classes they touch are stubbed in `efanim_java/`) on the stock `biped.json` armature plus an animation file and prints the
20 skinning matrices (pose * toOrigin, Minecraft space) per time. This script runs the same files through `efanim.pose_matrices` and
reports the largest matrix element difference (float32 in Java vs float64 here, so ~1e-4 at worst).

    python3 tests/tools/efanim_java_check.py [--jar EPIC_FIGHT.jar] [--classpath a.jar:b.jar] [--count 60] [--seed 7]

Needs a JDK 17 (`javac`/`java` on PATH, or `mise x java@temurin-17`), the Epic Fight 20.14.17 jar and gson, joml, guava,
datafixerupper, log4j-api, fastutil jars (found in ~/.gradle/caches/modules-2 when a Forge workspace was built, or pass --classpath).
Exit code 0 when every compared frame is within --tolerance (default 2e-3).
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "skills" / "epicfight-boss-forge" / "scripts"))

from bossforge_core import efanim, efassets  # noqa: E402
from bossforge_core.spec import data  # noqa: E402

LIBS = (("com.google.code.gson", "gson"), ("org.joml", "joml"), ("com.google.guava", "guava"), ("com.mojang", "datafixerupper"),
        ("org.apache.logging.log4j", "log4j-api"), ("it.unimi.dsi", "fastutil"))


def java_commands() -> Optional[Dict[str, List[str]]]:
    """{"javac": [...], "java": [...]} for a working JDK (PATH, else `mise x java@temurin-17`), or None."""
    candidates = []
    if shutil.which("javac") and shutil.which("java"):
        candidates.append({"javac": ["javac"], "java": ["java"]})
    if shutil.which("mise"):
        candidates.append({"javac": ["mise", "x", "java@temurin-17", "--", "javac"], "java": ["mise", "x", "java@temurin-17", "--", "java"]})
    for c in candidates:
        try:
            if subprocess.run(c["javac"] + ["-version"], capture_output=True, text=True).returncode == 0:
                return c
        except OSError:
            continue
    return None


def find_libs() -> Optional[List[str]]:
    """Newest jar of every library in the Gradle module cache; None when one is missing."""
    base = Path.home() / ".gradle" / "caches" / "modules-2" / "files-2.1"
    out = []
    for group, artifact in LIBS:
        jars = sorted(base.glob("%s/%s/*/*/%s-*.jar" % (group, artifact, artifact)))
        jars = [j for j in jars if not j.name.endswith(("-sources.jar", "-javadoc.jar"))]
        if not jars:
            return None
        out.append(str(jars[-1]))
    return out


def compile_harness(cmds: Dict[str, List[str]], classpath: str, out_dir: Path) -> None:
    sources = [str(p) for p in (HERE / "efanim_java").rglob("*.java")]
    r = subprocess.run(cmds["javac"] + ["-nowarn", "-cp", classpath, "-d", str(out_dir)] + sources, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("javac failed:\n" + r.stderr[-2000:])


def pick_animations(jar: Path, count: int, seed: int) -> List[str]:
    """A seeded sample over the three file kinds (plain matrices, Coord first, loc/rot/sca attributes) plus walk and run."""
    z = zipfile.ZipFile(jar)
    kinds: Dict[str, List[str]] = {}
    for name in z.namelist():
        if "/animations/biped/" not in name or not name.endswith(".json") or "/data/" in name:
            continue
        d = json.loads(z.read(name))
        a = d.get("animation")
        if not a:
            continue
        k = ("attributes" if isinstance(a[0]["transform"][0], dict) else "matrix") + ("_coord" if a[0]["name"] == "Coord" else "")
        kinds.setdefault(k, []).append(name.split("animations/")[1][:-5])
    rng = random.Random(seed)
    chosen = ["biped/living/walk", "biped/living/run"]
    per = max(1, count // max(1, len(kinds)))
    for k in sorted(kinds):
        lst = sorted(kinds[k])
        chosen += rng.sample(lst, min(len(lst), per))
    return list(dict.fromkeys(chosen))[:max(count, 2)]


def run(jar: Path, classpath_libs: Sequence[str], count: int, seed: int) -> Dict[str, Any]:
    cmds = java_commands()
    if cmds is None:
        raise SystemExit("no JDK (javac/java) found")
    classpath = ":".join([str(jar)] + list(classpath_libs))
    ref = data("versions")["epicfight"]["assets_ref"]
    biped = efassets.mesh_file("biped", efassets.default_cache_dir(), ref, str(jar), True)
    arm = efanim.Armature.from_mesh_json(json.loads(biped.read_text("utf-8")))
    z = zipfile.ZipFile(jar)
    worst: List[Any] = []
    frames = 0
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "classes"
        out.mkdir()
        compile_harness(cmds, classpath, out)
        for key in pick_animations(jar, count, seed):
            raw = z.read(efanim.ANIM_MEMBER % key)
            anim = Path(d) / "anim.json"
            anim.write_bytes(raw)
            clip = efanim.parse_clip(json.loads(raw), arm, key)
            times = sorted({0.0, clip.duration * 0.13, clip.duration * 0.37, clip.duration * 0.5, clip.duration * 0.81, clip.duration})
            r = subprocess.run(cmds["java"] + ["-cp", classpath + ":" + str(out), "Harness", str(biped), str(anim)] + ["%.6f" % t for t in times],
                               capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit("EF harness failed on %s:\n%s" % (key, r.stderr[-1500:]))
            for fr in json.loads(r.stdout):
                mc = efanim.pose_matrices(arm, clip.sample(fr["t"]))
                err = max(abs(a - b) for name, m in fr["m"].items() for a, b in zip(m, mc[name]))
                worst.append((err, key, fr["t"]))
                frames += 1
    worst.sort(reverse=True)
    return {"animations": len({w[1] for w in worst}), "frames": frames, "max_error": worst[0][0], "worst": [[round(e, 8), k, t] for e, k, t in worst[:5]]}


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--jar", help="Epic Fight jar (default: auto-detected)")
    p.add_argument("--classpath", help="colon separated jars: gson, joml, guava, datafixerupper, log4j-api, fastutil")
    p.add_argument("--count", type=int, default=60, help="animations to compare (default 60)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--tolerance", type=float, default=2e-3)
    a = p.parse_args(argv)
    jar = efanim.find_jar(a.jar)
    if jar is None:
        raise SystemExit("Epic Fight jar not found (pass --jar)")
    libs = a.classpath.split(":") if a.classpath else find_libs()
    if not libs:
        raise SystemExit("library jars not found in ~/.gradle (pass --classpath)")
    res = run(jar, libs, a.count, a.seed)
    res["tolerance"] = a.tolerance
    res["ok"] = res["max_error"] <= a.tolerance
    print(json.dumps(res, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
