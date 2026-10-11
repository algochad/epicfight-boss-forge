"""Project ownership manifest + safe file writer + lang merging."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Dict, List, Optional

from .spec import GenFile

MANIFEST_REL = "bossforge/manifest.json"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load(project_root) -> Optional[dict]:
    p = Path(project_root) / MANIFEST_REL
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def new_manifest(mod_id: str, package: str) -> dict:
    return {"schema": 1, "mod_id": mod_id, "package": package, "bosses": {}, "weapons": {}, "generated": {}}


def save(project_root, manifest: dict) -> None:
    p = Path(project_root) / MANIFEST_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    manifest["generated"] = dict(sorted(manifest["generated"].items()))
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_files(project_root, files: List[GenFile], manifest: dict, force: bool = False, dry_run: bool = False) -> Dict[str, List[str]]:
    """Write GenFiles honouring ownership. Returns {written, unchanged, updated, conflicts}.

    Absent -> written. Identical bytes -> unchanged. Differs but current hash equals the recorded one -> updated.
    Otherwise conflict (not written) unless force (then updated). Manifest `generated` is updated for written files.
    """
    root = Path(project_root)
    res: Dict[str, List[str]] = {"written": [], "unchanged": [], "updated": [], "conflicts": []}
    gen = manifest.setdefault("generated", {})
    for f in files:
        target = root / f.relpath
        digest = sha256(f.data)
        if not target.exists():
            res["written"].append(f.relpath)
        else:
            cur = target.read_bytes()
            if cur == f.data:
                res["unchanged"].append(f.relpath)
                gen[f.relpath] = digest
                continue
            if gen.get(f.relpath) == sha256(cur) or force:
                res["updated"].append(f.relpath)
            else:
                res["conflicts"].append(f.relpath)
                continue
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(f.data)
            gen[f.relpath] = digest
    return res


def merge_lang(project_root, mod_id: str, lang: Dict[str, str], dry_run: bool = False) -> List[str]:
    """Add absent keys to en_us.json, never overwrite. Returns the keys added."""
    p = Path(project_root) / "src" / "main" / "resources" / "assets" / mod_id / "lang" / "en_us.json"
    cur = {}
    if p.is_file():
        cur = json.loads(p.read_text(encoding="utf-8"))
    added = []
    for k, v in lang.items():
        if k not in cur:
            cur[k] = v
            added.append(k)
    if added and not dry_run:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    elif not p.is_file() and not dry_run:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cur, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return added
