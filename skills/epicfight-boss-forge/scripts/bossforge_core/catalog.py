"""Epic Fight animation catalog: build (maintainer) and query."""
from __future__ import annotations

import json
import re
from typing import Dict, List

from . import DATA_DIR

_CATALOG_FILE = DATA_DIR / "ef-animations-20.14.17.json"
_RE_FULL = re.compile(r'nextAccessor\("([^"]+)",\s*\(\w+\)\s*->\s*new\s+(\w+)\s*(?:<[^>]*>)?\(')
_RE_KEY = re.compile(r'nextAccessor\("([^"]+)"')
_cache = None


def parse_animations_java(text: str) -> Dict[str, str]:
    keys: Dict[str, str] = {}
    for m in _RE_FULL.finditer(text):
        keys[m.group(1)] = m.group(2)
    for m in _RE_KEY.finditer(text):
        keys.setdefault(m.group(1), "unknown")
    return keys


def build(ref: str, out_path, http_get) -> dict:
    url = ("https://raw.githubusercontent.com/Epic-Fight/epicfight/%s/src/main/java/yesman/epicfight/"
           "gameasset/Animations.java" % ref)
    text = http_get(url).decode("utf-8")
    data = {"ef_version": "20.14.17", "ref": ref, "keys": dict(sorted(parse_animations_java(text).items()))}
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2) + "\n")
    return data


def load() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(_CATALOG_FILE.read_text(encoding="utf-8"))
    return _cache


def keys() -> Dict[str, str]:
    return load()["keys"]


def resolve(animation: str) -> bool:
    """True when `animation` is `epicfight:<key>` with <key> in the catalog."""
    if not isinstance(animation, str) or not animation.startswith("epicfight:"):
        return False
    return animation[len("epicfight:"):] in keys()


def kind(key: str) -> str:
    cls = keys().get(key, "unknown")
    if "Attack" in cls:
        return "attack"
    if key.startswith("biped/living/") or re.search(r"/(idle|walk|death)$", key):
        return "living"
    if re.search(r"hit_|knockdown", key):
        return "hit"
    return "other"


def list_keys(prefix: str = "") -> List[dict]:
    return [{"key": k, "class": c, "kind": kind(k)} for k, c in keys().items() if k.startswith(prefix)]
