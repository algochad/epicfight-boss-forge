"""Make the skill's scripts importable from tests."""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKILL = REPO / "skills" / "epicfight-boss-forge"
SCRIPTS = SKILL / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
EXAMPLE = SKILL / "assets" / "examples" / "king-arthur" / "boss.json"
