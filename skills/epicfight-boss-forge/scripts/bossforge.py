#!/usr/bin/env python3
"""Epic Fight Boss Forge CLI entry point."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bossforge_core.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
