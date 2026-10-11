"""Fetch / cache Epic Fight entity mesh JSON files."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .spec import BossForgeError

RAW_URL = ("https://raw.githubusercontent.com/Epic-Fight/epicfight/%s/src/main/resources/assets/epicfight/"
           "animmodels/entity/%s.json")
JAR_MEMBER = "assets/epicfight/animmodels/entity/%s.json"
USER_AGENT = "bossforge/0.1"


def default_cache_dir() -> Path:
    env = os.environ.get("BOSSFORGE_CACHE")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(xdg) / "epicfight-boss-forge"


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def check_mesh_json(data: bytes, where: str) -> None:
    try:
        d = json.loads(data.decode("utf-8"))
        ok = "positions" in d["vertices"] and "array" in d["vertices"]["positions"] and "parts" in d["vertices"]
    except Exception:
        ok = False
    if not ok:
        raise BossForgeError("/mesh", "bad_mesh", "%s is not an Epic Fight mesh (needs vertices.positions.array and vertices.parts)" % where)


def mesh_file(name: str, cache_dir, ref: str, ef_jar: Optional[str] = None, offline: bool = False) -> Path:
    cache_dir = Path(cache_dir)
    path = cache_dir / "ef" / ref[:8] / (name + ".json")
    if path.is_file():
        return path
    data = None
    if ef_jar:
        try:
            with zipfile.ZipFile(ef_jar) as z:
                data = z.read(JAR_MEMBER % name)
        except (KeyError, OSError, zipfile.BadZipFile) as e:
            raise BossForgeError("/mesh", "ef_jar_missing", "cannot read %s from %s: %s" % (JAR_MEMBER % name, ef_jar, e))
    else:
        url = RAW_URL % (ref, name)
        if offline:
            raise BossForgeError("/mesh", "offline_missing", "mesh %s not cached and --offline set; download %s into %s" % (name, url, path))
        try:
            data = http_get(url)
        except (urllib.error.URLError, OSError) as e:
            raise BossForgeError("/mesh", "download_failed", "GET %s failed: %s" % (url, e))
    check_mesh_json(data, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path
