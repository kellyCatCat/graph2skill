#!/usr/bin/env python3
"""Run subkg2skill without installing it (offline or proxy-restricted setups).

    python run_subkg2skill.py build examples/subgraph --out out/ipran
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from subkg2skill.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
