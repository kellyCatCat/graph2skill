#!/usr/bin/env python3
"""Install (or update) the subkg-to-skill skill into Claude Code / opencode.

    python3 install.py                     # 装到 Claude Code 用户目录（默认）
    python3 install.py --link              # 软链到仓库，git pull 即更新
    python3 install.py --target opencode   # 装到 opencode
    python3 install.py --target all --project
    python3 install.py --uninstall

Re-running it is the update: the destination is replaced wholesale, so a file
this repository no longer ships stops existing at the destination too.  Only a
directory that looks like a previous install of this skill is ever replaced.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

SKILL_NAME = "subkg-to-skill"
SOURCE = Path(__file__).resolve().parent / SKILL_NAME
IGNORE = shutil.ignore_patterns("__pycache__", "*.py[cod]", ".DS_Store", ".pytest_cache")

TARGETS = {
    # target -> (用户级路径, 项目级路径)
    "claude": (Path.home() / ".claude" / "skills", Path(".claude") / "skills"),
    "opencode": (Path.home() / ".config" / "opencode" / "skill", Path(".opencode") / "skill"),
}


def destination(target: str, project: bool) -> Path:
    user_dir, project_dir = TARGETS[target]
    base = Path.cwd() / project_dir if project else user_dir
    return base / SKILL_NAME


def looks_like_our_skill(path: Path) -> bool:
    """True when *path* is a previous install (or a link to the source)."""
    if path.is_symlink():
        return True
    skill_md = path / "SKILL.md"
    if not skill_md.exists():
        return False
    try:
        head = skill_md.read_text(encoding="utf-8")[:400]
    except OSError:
        return False
    return f"name: {SKILL_NAME}" in head


def remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def install(target: str, *, project: bool, link: bool, force: bool, dry_run: bool) -> Tuple[bool, str]:
    dest = destination(target, project)
    if dest.exists() or dest.is_symlink():
        if not (looks_like_our_skill(dest) or force):
            return False, f"{dest} 已存在且不像本 skill 的安装；确认后加 --force 覆盖"
        if dry_run:
            return True, f"将替换 {dest}"
        remove(dest)
    if dry_run:
        return True, f"将{'软链' if link else '安装'}到 {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if link:
        os.symlink(SOURCE, dest, target_is_directory=True)
        return True, f"已软链 {dest} → {SOURCE}"
    shutil.copytree(SOURCE, dest, ignore=IGNORE)
    return True, f"已安装到 {dest}"


def uninstall(target: str, *, project: bool, force: bool, dry_run: bool) -> Tuple[bool, str]:
    dest = destination(target, project)
    if not (dest.exists() or dest.is_symlink()):
        return True, f"{dest} 未安装，跳过"
    if not (looks_like_our_skill(dest) or force):
        return False, f"{dest} 不像本 skill 的安装；确认后加 --force 删除"
    if dry_run:
        return True, f"将删除 {dest}"
    remove(dest)
    return True, f"已删除 {dest}"


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"安装/更新 {SKILL_NAME} skill")
    parser.add_argument(
        "--target",
        action="append",
        choices=("claude", "opencode", "all"),
        help="安装目标，可重复；默认 claude",
    )
    parser.add_argument("--project", action="store_true", help="装到当前项目而不是用户目录")
    parser.add_argument("--link", action="store_true", help="建软链而不是复制（改仓库即时生效）")
    parser.add_argument("--uninstall", action="store_true", help="卸载")
    parser.add_argument("--force", action="store_true", help="目标目录不像本 skill 时也覆盖")
    parser.add_argument("--dry-run", action="store_true", help="只打印会做什么")
    args = parser.parse_args(argv)

    if not (SOURCE / "SKILL.md").exists():
        print(f"错误：找不到 {SOURCE / 'SKILL.md'}，请在仓库根目录运行", file=sys.stderr)
        return 2

    targets = args.target or ["claude"]
    if "all" in targets:
        targets = ["claude", "opencode"]

    failed = 0
    for target in dict.fromkeys(targets):
        if args.uninstall:
            ok, message = uninstall(
                target, project=args.project, force=args.force, dry_run=args.dry_run
            )
        else:
            ok, message = install(
                target,
                project=args.project,
                link=args.link,
                force=args.force,
                dry_run=args.dry_run,
            )
        print(("  " if ok else "错误：") + message)
        failed += 0 if ok else 1

    if not failed and not args.uninstall and not args.dry_run:
        print("\n更新方式：git pull 后重跑本命令" + ("（软链安装无需重跑）" if args.link else ""))
        print("Claude Code 会话中改动生效：/reload-plugins")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
