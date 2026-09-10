#!/usr/bin/env python3
"""Entry point for the subkg-to-skill generator.

    python3 scripts/build_skill.py inspect  <子图输入>
    python3 scripts/build_skill.py validate <子图输入>
    python3 scripts/build_skill.py build    <子图输入> --out <目录> --name <slug>

Standard library only — no install step, run it straight from the skill directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from subkg2skill.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
