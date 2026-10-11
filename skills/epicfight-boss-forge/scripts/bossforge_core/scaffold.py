"""Forge 1.20.1 project scaffolding.

* ``new_mod``: turn the official Forge 1.20.1 MDK into a Boss Forge project (Epic Fight dependency, shared Java core).
* ``detect_project`` / ``wire_main_class``: adopt an existing mod (find its ``@Mod`` class, register ``BossRegistry``).
* ``dependency_warnings``: report a missing Epic Fight dependency in an existing mod without editing it.
* ``add_boss`` / ``add_weapon``: the generation pipelines behind ``add-boss`` and ``weapon --project``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import urllib.error
import zipfile
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple

from . import efassets, javagen, manifest as mf
from .spec import MOD_ID_RE, BossForgeError, Err, GenFile, data, make_context, pascal

MDK_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/1.20.1-%s/forge-1.20.1-%s-mdk.zip"
REQUIRED_MDK_MEMBERS = ("build.gradle", "gradlew", "gradle/wrapper/gradle-wrapper.jar", "src/main/resources/META-INF/mods.toml")
GROUP_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

_EF = data("versions")["epicfight"]
EF_GRADLE_VERSION = _EF["gradle_coordinate"].split(":")[-1]
EF_GRADLE_PROPERTY = "epicfight_version=" + EF_GRADLE_VERSION

# Modrinth's documented Maven block for ForgeGradle (inserted right after `repositories {`).
EF_BUILD_GRADLE_REPO_LINES = (
    "    exclusiveContent {",
    "        forRepository {",
    "            maven {",
    '                name = "Modrinth"',
    '                url = "https://api.modrinth.com/maven"',
    "            }",
    "        }",
    "        forRepositories(fg.repository)",
    "        filter {",
    '            includeGroup "maven.modrinth"',
    "        }",
    "    }",
)
# Inserted right after `dependencies {`; `epicfight_version` lives in gradle.properties.
EF_DEP_LINE = '    implementation fg.deobf("maven.modrinth:epic-fight:${epicfight_version}")'
EF_MODS_TOML_BLOCK = (
    "[[dependencies.${mod_id}]]\n"
    '    modId="epicfight"\n'
    "    mandatory=true\n"
    '    versionRange="%s"\n'
    '    ordering="AFTER"\n'
    '    side="BOTH"\n'
) % _EF["version_range"]

_REPOSITORIES_RE = re.compile(r"^repositories\s*\{[ \t]*(?=\r?$)", re.M)
_DEPENDENCIES_RE = re.compile(r"^dependencies\s*\{[ \t]*(?=\r?$)", re.M)
_BUS_RE = re.compile(r"(\w+)\s*=\s*FMLJavaModLoadingContext\s*\.\s*get\(\)\s*\.\s*getModEventBus\(\)\s*;")
_FML_IMPORT = "import net.minecraftforge.fml.javafmlmod.FMLJavaModLoadingContext;"


# --------------------------------------------------------------------------- small helpers
def _newline(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _ensure_trailing_newline(text: str) -> str:
    return text if not text or text.endswith("\n") else text + _newline(text)


def _mask(text: str, blank_strings: bool = True) -> str:
    """Blank comments (and, unless ``blank_strings`` is false, string/char literals) with spaces, keeping offsets."""
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                out[k] = " "
            i = j
        elif c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                if text[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and text[j] != c and text[j] != "\n":
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            if blank_strings:
                for k in range(i, j):
                    out[k] = " "
            i = j
        else:
            i += 1
    return "".join(out)


def _brace_depth(masked: str, idx: int) -> int:
    return masked.count("{", 0, idx) - masked.count("}", 0, idx)


def _git_user_name() -> Optional[str]:
    try:
        r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    name = r.stdout.strip()
    return name or None


def _prop_escape(value: str) -> str:
    """Escape a value for a Java .properties file (Gradle reads it as ISO-8859-1)."""
    out = []
    for ch in value.replace("\r\n", "\n").replace("\r", "\n"):
        o = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif o > 126 or o < 32:
            if o > 0xFFFF:
                o -= 0x10000
                out.append("\\u%04x\\u%04x" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
            else:
                out.append("\\u%04x" % o)
        else:
            out.append(ch)
    return "".join(out)


# --------------------------------------------------------------------------- MDK text patchers (pure)
def patch_gradle_properties(text: str, values: List[Tuple[str, str]]) -> str:
    """Replace the value of each key in ``values`` (keeping every other line); append keys that are absent."""
    nl = _newline(text)
    lines = text.splitlines(keepends=True)
    pending = dict(values)
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_.\-]*)[ \t]*=", line)
        if m and m.group(1) in pending:
            eol = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
            lines[i] = "%s=%s%s" % (m.group(1), pending.pop(m.group(1)), eol)
    text = "".join(lines)
    if pending:
        text = _ensure_trailing_newline(text)
        text += "".join("%s=%s%s" % (k, v, nl) for k, v in values if k in pending)
    return text


def _layout_error(name: str, text: str, missing: List[str]) -> BossForgeError:
    head = "\n".join(text.splitlines()[:40])
    return BossForgeError("/" + name, "mdk_layout_unexpected",
                          "%s lacks the expected anchor line(s) %s. This is not the Forge 1.20.1 MDK layout; first 40 lines:\n%s"
                          % (name, ", ".join(missing), head))


def patch_build_gradle(text: str) -> str:
    """Insert the Modrinth repository block and the Epic Fight dependency by regex anchors (never line numbers)."""
    rm = _REPOSITORIES_RE.search(text)
    dm = _DEPENDENCIES_RE.search(text)
    missing = []
    if rm is None:
        missing.append("a line `repositories {` at column 0")
    if dm is None:
        missing.append("a line `dependencies {` at column 0")
    if missing:
        raise _layout_error("build.gradle", text, missing)
    nl = _newline(text)
    inserts = []
    if "api.modrinth.com/maven" not in text:
        inserts.append((rm.end(), nl.join(EF_BUILD_GRADLE_REPO_LINES)))
    if "epic-fight" not in text:
        inserts.append((dm.end(), EF_DEP_LINE))
    for pos, block in sorted(inserts, reverse=True):
        text = text[:pos] + nl + block + text[pos:]
    return text


def patch_mods_toml(text: str) -> str:
    if re.search(r"modId\s*=\s*[\"']epicfight[\"']", text):
        return text
    nl = _newline(text)
    block = EF_MODS_TOML_BLOCK.replace("\n", nl)
    return _ensure_trailing_newline(text) + nl + block


# --------------------------------------------------------------------------- MDK acquisition
def _mdk_cache_path(cache_dir: Path, version: str) -> Path:
    return cache_dir / "mdk" / ("forge-1.20.1-%s-mdk.zip" % version)


def obtain_mdk(mdk_zip: Optional[str], forge_version: Optional[str], cache_dir: Path, offline: bool) -> Path:
    """Return a local MDK zip: the explicit one, the cached one, or a fresh download (404 retries the fallback version)."""
    if mdk_zip:
        p = Path(mdk_zip)
        if not p.is_file():
            raise BossForgeError("/mdk_zip", "mdk_missing", "MDK zip not found: %s" % p)
        return p
    versions = data("versions")
    primary = forge_version or versions["forge"]
    candidates = [primary] + ([versions["forge_fallback"]] if primary != versions["forge_fallback"] else [])
    tried = []
    for ver in candidates:
        cached = _mdk_cache_path(cache_dir, ver)
        if cached.is_file():
            return cached
        url = MDK_URL % (ver, ver)
        if offline:
            tried.append(url)
            continue
        try:
            blob = efassets.http_get(url, timeout=120)
        except urllib.error.HTTPError as e:
            e.close()
            tried.append("%s (HTTP %s)" % (url, e.code))
            if e.code == 404:
                continue
            raise BossForgeError("/mdk_zip", "download_failed", "GET %s failed: HTTP %s" % (url, e.code))
        except (urllib.error.URLError, OSError) as e:
            raise BossForgeError("/mdk_zip", "download_failed", "GET %s failed: %s" % (url, e))
        tmp = cached.with_name(cached.name + ".part")
        cached.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(blob)
        os.replace(str(tmp), str(cached))
        return cached
    if offline:
        raise BossForgeError("/mdk_zip", "offline_missing",
                             "Forge MDK not cached and --offline set; download %s and pass it with --mdk-zip" % tried[0])
    raise BossForgeError("/mdk_zip", "download_failed", "no Forge MDK found; tried: %s" % "; ".join(tried))


def _common_prefix(names: List[str]) -> str:
    """Single top-level folder shared by every member ('' when files sit at the zip root)."""
    firsts = set()
    for name in names:
        parts = name.split("/")
        if len(parts) == 1:
            return ""
        firsts.add(parts[0])
    return firsts.pop() + "/" if len(firsts) == 1 else ""


def _safe_rel(rel: str) -> None:
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or "\\" in rel:
        raise BossForgeError("/mdk_zip", "mdk_unsafe_path", "MDK zip member %r escapes the project directory" % rel)


# --------------------------------------------------------------------------- new-mod
def _validate_new_mod(mod_id: str, mod_name: str, group: str, description: str) -> None:
    if not isinstance(mod_id, str) or not MOD_ID_RE.match(mod_id):
        raise BossForgeError("/mod_id", "bad_mod_id", "mod id %r must match %s" % (mod_id, MOD_ID_RE.pattern))
    if not isinstance(mod_name, str) or not 1 <= len(mod_name) <= 60:
        raise BossForgeError("/mod_name", "bad_mod_name", "mod name must be 1-60 characters")
    if re.search(r'["\\$\r\n]', mod_name):
        raise BossForgeError("/mod_name", "bad_mod_name", "mod name must not contain double quotes, backslashes, $ or line breaks (it is embedded in mods.toml)")
    if not GROUP_RE.match(group):
        raise BossForgeError("/group", "bad_group", "group %r must be a lowercase dotted package such as com.bossforge" % group)
    for seg in group.split("."):
        javagen.check_package_segment(seg, "/group", "group segment")
    javagen.check_package_segment(mod_id, "/mod_id", "mod id")
    if "'''" in description:
        raise BossForgeError("/description", "bad_description", "description must not contain ''' (it is embedded in mods.toml)")


def new_mod(mod_id: str, mod_name: str, out_dir, group: str = "com.bossforge", author: Optional[str] = None,
            description: str = "", mdk_zip: Optional[str] = None, forge_version: Optional[str] = None,
            cache_dir=None, offline: bool = False) -> dict:
    """Create a Forge 1.20.1 mod project from the official MDK, wired for Epic Fight and Boss Forge."""
    _validate_new_mod(mod_id, mod_name, group, description)
    out = Path(out_dir)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise BossForgeError("/out", "out_not_empty", "%s exists and is not an empty directory" % out)
    cache = Path(cache_dir) if cache_dir else efassets.default_cache_dir()
    zpath = obtain_mdk(mdk_zip, forge_version, cache, offline)
    package = "%s.%s" % (group, mod_id)
    author = author or _git_user_name() or "Unknown"
    if re.search(r'["\\$\r\n]', author):
        raise BossForgeError("/author", "bad_author", "author must not contain double quotes, backslashes, $ or line breaks (it is embedded in mods.toml)")
    description = description or "%s: Epic Fight boss content." % mod_name

    try:
        zf = zipfile.ZipFile(str(zpath))
    except (zipfile.BadZipFile, OSError) as e:
        raise BossForgeError("/mdk_zip", "bad_mdk_zip", "%s is not a readable zip: %s" % (zpath, e))
    with zf:
        infos = zf.infolist()
        prefix = _common_prefix([i.filename for i in infos])
        members = {}
        for info in infos:
            rel = info.filename[len(prefix):]
            if rel and not rel.endswith("/"):
                _safe_rel(rel)
                members[rel] = info
        missing = [m for m in REQUIRED_MDK_MEMBERS if m not in members]
        if missing:
            sample = sorted(members)[:40]
            raise BossForgeError("/mdk_zip", "mdk_layout_unexpected",
                                 "%s lacks required member(s) %s. Expected the Forge 1.20.1 MDK; first members:\n%s"
                                 % (zpath, ", ".join(missing), "\n".join(sample)))

        def text_of(name: str) -> Optional[str]:
            return zf.read(members[name]).decode("utf-8") if name in members else None

        build_gradle = patch_build_gradle(text_of("build.gradle"))
        mods_toml = patch_mods_toml(text_of("src/main/resources/META-INF/mods.toml"))
        props_in = text_of("gradle.properties") or ""
        props = patch_gradle_properties(props_in, [
            ("mod_id", mod_id),
            ("mod_name", _prop_escape(mod_name)),
            ("mod_license", "MIT"),
            ("mod_version", "0.1.0"),
            ("mod_group_id", package),
            ("mod_authors", _prop_escape(author)),
            ("mod_description", _prop_escape(description)),
            ("epicfight_version", EF_GRADLE_VERSION),
        ])
        forge_m = re.search(r"^forge_version\s*=\s*(\S+)", props, re.M)
        overrides = {
            "build.gradle": build_gradle.encode("utf-8"),
            "src/main/resources/META-INF/mods.toml": mods_toml.encode("utf-8"),
            "gradle.properties": props.encode("utf-8"),
        }
        created = not out.exists()
        try:
            out.mkdir(parents=True, exist_ok=True)
            for rel, info in members.items():
                if rel.startswith("src/main/java/") and rel.endswith(".java"):
                    continue  # the template mod's sources are replaced by the generated main class
                target = out / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(overrides[rel] if rel in overrides else zf.read(info))
                if (info.external_attr >> 16) & 0o111 or rel == "gradlew":
                    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            if "gradle.properties" not in members:
                (out / "gradle.properties").write_bytes(overrides["gradle.properties"])
            pack = out / "src" / "main" / "resources" / "pack.mcmeta"
            if not pack.exists():
                pack.parent.mkdir(parents=True, exist_ok=True)
                pack.write_text('{\n  "pack": {\n    "description": %s,\n    "pack_format": %d\n  }\n}\n'
                                % (json.dumps("%s resources" % mod_name, ensure_ascii=False), data("versions")["pack_format"]), encoding="utf-8")
            main_rel = write_main_class(out, package, mod_id)
            ctx = make_context(out, mod_id, package, cache, offline=offline)
            man = mf.new_manifest(mod_id, package)
            files = javagen.core_files(ctx)
            files.append(javagen.generate_registry(out, ctx, man))
            result = mf.write_files(out, files, man)
            mf.save(out, man)
        except BaseException:
            _discard(out, created)
            raise
    return {
        "ok": True,
        "project": str(out),
        "mod_id": mod_id,
        "package": package,
        "main_class": main_rel,
        "forge_version": forge_m.group(1) if forge_m else None,
        "epicfight_version": EF_GRADLE_VERSION,
        "mdk": str(zpath),
        "files": result,
        "next": [
            "python3 scripts/bossforge.py add-boss <boss.json> --project %s" % out,
            "cd %s && ./gradlew compileJava processResources   (needs JDK 17)" % out,
        ],
    }


def _discard(out: Path, created: bool) -> None:
    if created:
        shutil.rmtree(str(out), ignore_errors=True)
        return
    for child in out.iterdir():
        if child.is_dir():
            shutil.rmtree(str(child), ignore_errors=True)
        else:
            child.unlink()


def write_main_class(project_dir, package: str, mod_id: str) -> str:
    """Write ``<Pascal>Mod.java`` (project-owned, not tracked in the manifest). Returns its project-relative path."""
    cls = pascal(mod_id) + "Mod"
    text = javagen.render(javagen.load_template("mod", "MainMod.java.tpl"),
                          {"PACKAGE": package, "MOD_ID": mod_id, "CLASS": cls}, "MainMod")
    rel = "src/main/java/%s/%s.java" % (package.replace(".", "/"), cls)
    target = Path(project_dir) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return rel


# --------------------------------------------------------------------------- existing projects
def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def detect_project(project_dir, mod_id: str) -> Tuple[str, Path]:
    """Return (java package, path of the ``@Mod`` class) for ``mod_id``.

    Errors: ``mod_id_mismatch`` (gradle.properties ``mod_id`` differs or is absent), ``no_main_class``.
    """
    root = Path(project_dir)
    props = _read(root / "gradle.properties")
    m = re.search(r"^\s*mod_id\s*=\s*(\S+)\s*$", props or "", re.M)
    if m is None:
        raise BossForgeError("/mod_id", "mod_id_mismatch", "%s has no mod_id entry; expected mod_id=%s" % (root / "gradle.properties", mod_id))
    if m.group(1) != mod_id:
        raise BossForgeError("/mod_id", "mod_id_mismatch",
                             "spec mod_id %r differs from gradle.properties mod_id %r; fix one of them" % (mod_id, m.group(1)))
    java_dir = root / "src" / "main" / "java"
    sources = sorted(java_dir.rglob("*.java")) if java_dir.is_dir() else []
    texts = {p: _read(p) or "" for p in sources}
    masked = {p: _mask(t, blank_strings=False) for p, t in texts.items()}
    constant = re.compile(r"String\s+(\w+)\s*=\s*\"%s\"" % re.escape(mod_id))
    constants = {mm.group(1) for t in texts.values() for mm in constant.finditer(t)}
    for p in sources:
        for am in re.finditer(r"@Mod\s*\(\s*([^)]*?)\s*\)", masked[p]):
            arg = am.group(1).strip()
            literal = re.match(r'^"([^"]*)"$', arg)
            ident = arg.split(".")[-1]
            if (literal and literal.group(1) == mod_id) or (not literal and ident in constants):
                pm = re.search(r"^\s*package\s+([\w.]+)\s*;", masked[p], re.M)
                if pm is None:
                    raise BossForgeError("/main_class", "no_main_class", "%s is in the default package; move it into a package" % p)
                return pm.group(1), p
    raise BossForgeError("/main_class", "no_main_class",
                         "no class annotated @Mod(%r) found under %s" % (mod_id, java_dir))


def wire_main_class(main_class_path, package: str, dry_run: bool = False) -> dict:
    """Make the main class call ``BossRegistry.register(<mod event bus>)`` (idempotent).

    Looks for ``<var> = FMLJavaModLoadingContext.get().getModEventBus();`` inside a method or constructor, inserts the
    call on the next line and the ``BossRegistry`` import after the last import. When no such statement exists nothing
    is edited and ``wiring_required`` is true with the exact lines to add.
    """
    path = Path(main_class_path)
    with open(str(path), "r", encoding="utf-8", newline="") as fh:
        text = fh.read()
    masked = _mask(text)
    imp = "import %s.boss.BossRegistry;" % package
    if re.search(r"\bBossRegistry\s*\.\s*register\s*\(", masked):
        return {"wired": True, "changed": False, "wiring_required": False, "bus_var": None}
    fallback = {
        "wired": False, "changed": False, "wiring_required": True, "bus_var": None,
        "line": "BossRegistry.register(FMLJavaModLoadingContext.get().getModEventBus());",
        "imports": [imp, _FML_IMPORT],
        "message": "Add the line inside your @Mod constructor and the imports at the top of %s." % path,
    }
    bm = None
    for cand in _BUS_RE.finditer(masked):
        if _brace_depth(masked, cand.start()) >= 2:
            bm = cand
            break
    if bm is None:
        return fallback
    nl = _newline(text)
    line_start = masked.rfind("\n", 0, bm.start()) + 1
    indent = re.match(r"[ \t]*", text[line_start:]).group(0)
    call = "%sBossRegistry.register(%s);" % (indent, bm.group(1))
    line_end = masked.find("\n", bm.end())
    if line_end == -1:
        new = text + nl + call + nl
    else:
        new = text[:line_end + 1] + call + nl + text[line_end + 1:]
    if imp not in text:
        last = None
        for last in re.finditer(r"^[ \t]*import\s+[^;]+;[^\r\n]*", masked, re.M):
            pass
        if last is not None:
            at = last.end()
            new = new[:at] + nl + imp + new[at:]
        else:
            pm = re.search(r"^[ \t]*package\s+[\w.]+\s*;[^\r\n]*", masked, re.M)
            at = pm.end() if pm else 0
            new = new[:at] + nl + nl + imp + (nl if pm else "") + new[at:]
    if not dry_run:
        with open(str(path), "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
    return {"wired": True, "changed": True, "wiring_required": False, "bus_var": bm.group(1)}


def dependency_warnings(project_dir) -> List[Err]:
    """Warn (never edit) when an existing mod lacks the Epic Fight Gradle dependency or mods.toml entry."""
    root = Path(project_dir)
    warns: List[Err] = []
    gradle = [t for t in (_read(root / "build.gradle"), _read(root / "build.gradle.kts")) if t]
    if not any("epic-fight" in t for t in gradle):
        warns.append(Err("/build.gradle", "epicfight_dependency_missing",
                         "build.gradle does not depend on Epic Fight. Inside `repositories {` add:\n%s\n"
                         "inside `dependencies {` add:\n%s\nand add `%s` to gradle.properties."
                         % ("\n".join(EF_BUILD_GRADLE_REPO_LINES), EF_DEP_LINE, EF_GRADLE_PROPERTY)))
    toml = _read(root / "src" / "main" / "resources" / "META-INF" / "mods.toml") or ""
    if not re.search(r"modId\s*=\s*[\"']epicfight[\"']", toml):
        warns.append(Err("/src/main/resources/META-INF/mods.toml", "epicfight_dependency_missing",
                         "mods.toml does not declare Epic Fight. Append:\n%s" % EF_MODS_TOML_BLOCK))
    return warns


# --------------------------------------------------------------------------- pipelines
def _spec_copy(rel: str, raw_bytes: bytes) -> GenFile:
    return GenFile(rel, raw_bytes, "json")


def _open_project(project_dir, mod_id: str):
    """(root, package, main_class_path, manifest, adopted)."""
    root = Path(project_dir)
    if not root.is_dir():
        raise BossForgeError("/project", "project_missing", "%s is not a directory" % root)
    package, main_path = detect_project(root, mod_id)
    man = mf.load(root)
    adopted = man is None
    if adopted:
        man = mf.new_manifest(mod_id, package)
    elif man.get("mod_id") != mod_id:
        raise BossForgeError("/mod_id", "mod_id_mismatch", "manifest mod_id %r differs from spec mod_id %r" % (man.get("mod_id"), mod_id))
    man.setdefault("weapons", {})
    man.setdefault("bosses", {})
    return root, package, main_path, man, adopted


def _commit(root: Path, man: dict, files: List[GenFile], lang: dict, mod_id: str, force: bool, dry_run: bool) -> dict:
    """Conflict pre-check, then write files + lang + manifest. Nothing is written when there are conflicts."""
    import copy
    probe = mf.write_files(root, files, copy.deepcopy(man), force=force, dry_run=True)
    if probe["conflicts"] and not force:
        return dict(probe, written=[], updated=[], unchanged=[])
    res = mf.write_files(root, files, man, force=force, dry_run=dry_run)
    res["lang_added"] = mf.merge_lang(root, mod_id, lang, dry_run=dry_run)
    if not dry_run:
        mf.save(root, man)
    return res


def _core_files(ctx, root: Path, adopted: bool) -> List[GenFile]:
    files = javagen.core_files(ctx)
    if adopted:
        # existing mod without a manifest: never claim files that are already there
        files = [f for f in files if not (root / f.relpath).exists()]
    return files


def _summary(root, res, warnings, wiring, qa_errs) -> dict:
    hard = [e for e in qa_errs if not e.code.startswith("warn_")]
    return {"files": res, "warnings": [w.to_dict() for w in warnings], "wiring": wiring,
            "wiring_required": bool(wiring and wiring.get("wiring_required")),
            "qa": {"ok": not hard, "errors": [e.to_dict() for e in hard],
                   "warnings": [e.to_dict() for e in qa_errs if e.code.startswith("warn_")]}}


def add_boss(spec_path, project_dir, cache_dir=None, offline: bool = False, force: bool = False, dry_run: bool = False,
             ef_jar: Optional[str] = None) -> dict:
    """Generate every file for one boss into a mod project (idempotent; hand-edited files are conflicts)."""
    from . import lore, patch, qa, skin, spec as S, weapon
    raw_bytes = Path(spec_path).read_bytes()
    raw = S.load(spec_path)
    if raw.get("schema") == S.WEAPON_SCHEMA:
        raise BossForgeError("/schema", "not_boss_spec", "this is a weapon spec; use `weapon SPEC --project DIR`")
    n, errs, warns = S.check(raw)
    if errs:
        return {"ok": False, "errors": [e.to_dict() for e in errs], "warnings": [w.to_dict() for w in warns]}
    root, package, main_path, man, adopted = _open_project(project_dir, n["mod_id"])
    cache = Path(cache_dir) if cache_dir else efassets.default_cache_dir()
    ctx = make_context(root, n["mod_id"], package, cache, offline=offline, force=force, dry_run=dry_run, ef_jar=ef_jar)
    files: List[GenFile] = _core_files(ctx, root, adopted)
    lang: dict = {}
    report = None
    gens = [skin.generate_with_report, weapon.generate, patch.generate, javagen.generate, lore.generate]
    if n["derived"].get("custom"):
        from . import shape
        gens.insert(1, shape.generate)
    for gen in gens:
        out = gen(n, ctx)
        files.extend(out[0])
        lang.update(out[1])
        if gen is skin.generate_with_report:
            report = out[2]
    rel_spec = "bossforge/bosses/%s.boss.json" % n["id"]
    files.append(_spec_copy(rel_spec, raw_bytes))
    man["bosses"][n["id"]] = {"spec": rel_spec, "class": javagen.boss_class(n["id"])}
    files.append(javagen.generate_registry(root, ctx, man, bosses={n["id"]: n}))
    res = _commit(root, man, files, lang, n["mod_id"], force, dry_run)
    if res["conflicts"] and not force:
        return {"ok": False, "boss": n["id"], "errors": [Err("/" + c, "conflict", "%s was modified since it was generated; rerun with --force to overwrite" % c).to_dict() for c in res["conflicts"]],
                "files": res}
    wiring = wire_main_class(main_path, package, dry_run=dry_run)
    all_warns = list(warns) + dependency_warnings(root)
    all_warns.extend(Err(**w) for w in (report or {}).get("warnings", []) if w not in [x.to_dict() for x in warns])
    qa_errs = [] if dry_run else qa.run(root, None, cache)
    d = n["derived"]
    out = {"ok": True, "boss": n["id"], "template": d["template"], "scale": d["scale"], "hitbox": d["hitbox"], "adopted_existing_mod": adopted,
           "previews": [f.relpath for f in files if f.kind == "preview"], "dry_run": dry_run, "package": package,
           "wiring_required": bool(wiring.get("wiring_required"))}
    out.update(_summary(root, res, all_warns, wiring, qa_errs))
    out["ok"] = out["qa"]["ok"]
    if wiring.get("wiring_required"):
        out["wiring_instructions"] = {"line": wiring["line"], "imports": wiring["imports"]}
    out["next"] = ["read %s/bossforge/previews/%s_preview.png" % (root, n["id"]),
                   "cd %s && ./gradlew --no-daemon compileJava processResources   (JDK 17)" % root,
                   "RENDER GATE: python3 scripts/bossforge.py render-test --project %s --boss %s  (needs a display + JDK 17), then READ the screenshots; "
                   "without it the boss is NOT verified to render (references/render-pipeline.md)" % (root, n["id"]),
                   "see references/qa.md for the in-game checklist"]
    return out


def add_weapon(spec_path, project_dir, cache_dir=None, offline: bool = False, force: bool = False, dry_run: bool = False,
               essence_item: Optional[str] = None) -> dict:
    """Add a standalone weapon (no boss) to a mod project: resources, lang, registry."""
    from . import qa, spec as S, weapon
    raw_bytes = Path(spec_path).read_bytes()
    raw = S.load(spec_path)
    errs = S.validate_weapon_spec(raw)
    if errs:
        return {"ok": False, "errors": [e.to_dict() for e in errs]}
    w = S.normalize_weapon_spec(raw)
    root, package, main_path, man, adopted = _open_project(project_dir, w["mod_id"])
    cache = Path(cache_dir) if cache_dir else efassets.default_cache_dir()
    ctx = make_context(root, w["mod_id"], package, cache, offline=offline, force=force, dry_run=dry_run)
    files: List[GenFile] = _core_files(ctx, root, adopted)
    wfiles, lang = weapon.generate_standalone(w, ctx, essence_item or w.get("essence_item"))
    files.extend(wfiles)
    rel_spec = "bossforge/weapons/%s.weapon.json" % w["id"]
    files.append(_spec_copy(rel_spec, raw_bytes))
    man["weapons"][w["id"]] = {"spec": rel_spec}
    files.append(javagen.generate_registry(root, ctx, man, weapons={w["id"]: w}))
    res = _commit(root, man, files, lang, w["mod_id"], force, dry_run)
    if res["conflicts"] and not force:
        return {"ok": False, "weapon": w["id"], "errors": [Err("/" + c, "conflict", "%s was modified since it was generated; rerun with --force to overwrite" % c).to_dict() for c in res["conflicts"]],
                "files": res}
    wiring = wire_main_class(main_path, package, dry_run=dry_run)
    qa_errs = [] if dry_run else qa.run(root, None, cache)
    out = {"ok": True, "weapon": w["id"], "category": w["category"], "dry_run": dry_run,
           "previews": [f.relpath for f in files if f.kind == "preview"]}
    out.update(_summary(root, res, dependency_warnings(root), wiring, qa_errs))
    out["ok"] = out["qa"]["ok"]
    if wiring.get("wiring_required"):
        out["wiring_instructions"] = {"line": wiring["line"], "imports": wiring["imports"]}
    return out
