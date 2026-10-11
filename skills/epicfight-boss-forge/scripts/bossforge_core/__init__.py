"""bossforge_core: Epic Fight boss generator library (stdlib only)."""
from __future__ import annotations

from pathlib import Path

PKG_DIR = Path(__file__).resolve().parent
SKILL_ROOT = PKG_DIR.parent.parent
DATA_DIR = SKILL_ROOT / "assets" / "data"
TEMPLATES_DIR = SKILL_ROOT / "assets" / "templates"
