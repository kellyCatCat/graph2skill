#!/usr/bin/env python3
"""免安装入口：`python run_graph2skill.py <命令> ...`

给装不了包的环境用（离线、代理需要鉴权、没有写权限）。核心功能零依赖，
只有 `steps --llm` 需要 requests、`--provider anthropic` 需要 anthropic。

    python run_graph2skill.py skillset init D:\\skills
    python run_graph2skill.py merge new.json --into isis -s D:\\skills\\skillset.json --dry-run
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from graph2skill.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
