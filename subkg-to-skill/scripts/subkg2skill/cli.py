"""Command line interface.

    build_skill.py list     <子图>                       # 有哪些故障入口，各自建议的 slug
    build_skill.py build    <子图> --entry <症状> --name <slug> --out <目录>
    build_skill.py build-all <子图> --out <目录> [--names names.json]
    build_skill.py inspect  <子图>                       # 规模与分布
    build_skill.py validate <子图>                       # 只做结构校验
    build_skill.py lint     <skill 目录或 SKILL.md>      # 模板符合性检查

一个 skill 对应一个故障入口（一个 symptom），文档遵循四章节模板。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from subkg2skill import __version__, schema
from subkg2skill.graph import Graph, Node, ValidationReport
from subkg2skill.lint import lint_path, lint_text
from subkg2skill.loader import SubgraphLoadError, load
from subkg2skill.playbook import build_playbook, build_playbooks, entry_symptoms
from subkg2skill.render import (
    BuildOptions,
    RenderError,
    build_package,
    normalise_name,
    suggested_slug,
)


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "inputs",
        nargs="*",
        help="节点/边文件或目录（.json/.jsonc/.jsonl）；目录会自动识别 node*/edge* 文件",
    )
    parser.add_argument("--nodes", action="append", default=[], help="显式指定节点文件，可重复")
    parser.add_argument("--edges", action="append", default=[], help="显式指定边文件，可重复")
    parser.add_argument("--strict", action="store_true", help="把校验告警也当作错误")


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("子图选择（都不给时使用全部输入）")
    group.add_argument("--root", action="append", default=[], help="起点：node_id、id 前缀或名称关键词")
    group.add_argument("--depth", type=int, default=0, help="从起点向前展开的层数（0=不限）")
    group.add_argument(
        "--node-type", action="append", default=[], choices=list(schema.NODE_TYPES), help="按节点类型筛选种子"
    )
    group.add_argument("--section", action="append", default=[], help="按诊断单元/章节号筛选种子")
    group.add_argument("--vendor", action="append", default=[], help="按 scope.vendor 筛选种子")
    group.add_argument("--query", default="", help="按关键词筛选种子")


def _add_output_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument("--evidence", type=int, default=3, help="每个条目展示的来源条数")
    parser.add_argument(
        "--data", choices=("full", "slim", "none"), default="full", help="随包数据的详细程度"
    )
    parser.add_argument("--no-script", action="store_true", help="不生成查询脚本")
    parser.add_argument("--no-lead", action="store_true", help="不在 frontmatter 后加参考文件提示行")
    parser.add_argument("--force", action="store_true", help="覆盖已有目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印将生成的内容")


def _load_graph(args):
    bundle, sources = load(args.inputs, node_files=args.nodes, edge_files=args.edges)
    graph, report = Graph.from_bundle(bundle, strict=bool(args.strict))
    return graph, report, sources


def _resolve_roots(graph: Graph, roots: Sequence[str]) -> Set[str]:
    resolved: Set[str] = set()
    for root in roots:
        if root in graph:
            resolved.add(root)
            continue
        prefixed = [nid for nid in graph.nodes if nid.startswith(root)]
        if prefixed:
            resolved.update(prefixed)
            continue
        hits = graph.search(root)
        if not hits:
            raise RenderError(f"起点 {root!r} 既不是 node_id，也没有匹配到任何节点")
        resolved.update(node.node_id for node in hits)
    return resolved


def _select(graph: Graph, args) -> Graph:
    seeds: Set[str] = set()
    if getattr(args, "root", None):
        seeds |= _resolve_roots(graph, args.root)
    if args.node_type or args.section or args.vendor or args.query:
        filtered = graph.filter_nodes(
            node_types=args.node_type,
            sections=args.section,
            vendors=args.vendor,
            query=args.query,
        )
        seeds = (seeds & filtered) if args.root else filtered
    if not seeds:
        if args.root or args.node_type or args.section or args.vendor or args.query:
            raise RenderError("筛选条件没有选中任何节点")
        return graph
    depth = args.depth if args.depth > 0 else None
    return graph.subgraph(graph.reachable(seeds, depth=depth))


def _resolve_entry(graph: Graph, entry: str) -> Node:
    """Find the one symptom a skill will document."""
    symptoms = entry_symptoms(graph)
    if not symptoms:
        raise RenderError("子图里没有 symptom 节点，无法生成 skill")
    if not entry:
        if len(symptoms) == 1:
            return symptoms[0]
        raise RenderError(
            f"子图里有 {len(symptoms)} 个故障入口，请用 --entry 指定一个"
            "（先跑 `list` 看清单），或用 build-all 批量生成"
        )
    if entry in graph and graph.nodes[entry].node_type == "symptom":
        return graph.nodes[entry]
    candidates = [node for node in symptoms if node.node_id.startswith(entry)]
    if not candidates:
        candidates = [node for node in graph.search(entry, node_types=["symptom"])]
    if not candidates:
        raise RenderError(f"入口 {entry!r} 没有匹配到任何 symptom 节点")
    if len(candidates) > 1:
        listed = "、".join(f"{node.name}({node.node_id})" for node in candidates[:5])
        raise RenderError(f"入口 {entry!r} 匹配到多个症状：{listed}…；请给出确切的 node_id")
    return candidates[0]


def _print_report(report: ValidationReport, *, limit: int = 20) -> None:
    counts = report.counts()
    if not counts:
        print("校验：结构完整，没有丢弃任何记录。")
        return
    print("校验发现：")
    for kind, count in counts.items():
        print(f"  {kind}: {count}")
    for issue in report.issues[:limit]:
        print(f"  {issue.render()}")
    if len(report.issues) > limit:
        print(f"  …另有 {len(report.issues) - limit} 条")


def _print_lint(result, *, prefix: str = "  ") -> None:
    if result.ok and not result.warnings:
        print(f"{prefix}模板检查：通过")
        return
    print(f"{prefix}模板检查：{len(result.errors)} 错误 / {len(result.warnings)} 警告")
    for issue in result.issues[:20]:
        print(f"{prefix}  {issue.render()}")
    if len(result.issues) > 20:
        print(f"{prefix}  …另有 {len(result.issues) - 20} 条")


def _install_hint(out_dir: Path, name: str) -> None:
    print("\n装进框架：")
    print(f"  cp -r {out_dir} .claude/skills/{name}          # Claude Code（项目级）")
    print(f"  cp -r {out_dir} ~/.claude/skills/{name}        # Claude Code（全局）")
    print(f"  cp -r {out_dir} .opencode/skill/{name}         # opencode（项目级）")
    print(f"  cp -r {out_dir} ~/.config/opencode/skill/{name}")


# ------------------------------------------------------------- commands
def cmd_list(args) -> int:
    graph, _report, sources = _load_graph(args)
    graph = _select(graph, args)
    symptoms = entry_symptoms(graph)
    print(f"输入：{'、'.join(sources)}")
    print(f"故障入口（symptom）共 {len(symptoms)} 个：\n")
    for symptom in symptoms[: args.limit]:
        playbook = build_playbook(graph, symptom)
        triggers = " / ".join(playbook.trigger_terms()[1:4])
        print(f"  {symptom.name}")
        print(f"    node_id : {symptom.node_id}")
        print(f"    规模    : 原因 {len(playbook.causes)}、检查 {playbook.check_count}、修复 {playbook.repair_count}")
        if triggers:
            print(f"    触发说法: {triggers}")
        print(f"    建议 slug: {suggested_slug(symptom)}（模板要求英文名，请按语义改写）")
        print()
    if len(symptoms) > args.limit:
        print(f"  …另有 {len(symptoms) - args.limit} 个（--limit 调整）")
    return 0


def cmd_inspect(args) -> int:
    graph, report, sources = _load_graph(args)
    graph = _select(graph, args)
    print(f"输入：{'、'.join(sources)}")
    print(f"节点 {len(graph)}；关系 {len(graph.edges)}")
    print("\n节点类型：")
    for node_type, count in graph.node_counts().items():
        print(f"  {node_type} ({schema.NODE_LABELS[node_type]}): {count}")
    print("\n关系类型：")
    for edge_type, count in graph.edge_counts().items():
        print(f"  {edge_type} ({schema.edge_label(edge_type)}): {count}")
    print("\n适用范围（厂商）：")
    for vendor, count in graph.vendors().items():
        print(f"  {vendor}: {count}")
    sections = graph.sections()
    if sections:
        print("\n诊断单元（前 10）：")
        for section, count in list(sections.items())[:10]:
            print(f"  {section}: {count}")
    flags = graph.quality_flag_counts()
    if flags:
        print("\n质量标记：")
        for flag, count in list(flags.items())[:15]:
            print(f"  {flag}: {count}")
    orphans = graph.orphan_nodes()
    if orphans:
        print(f"\n孤立节点：{len(orphans)}（前 5）")
        for node in orphans[:5]:
            print(f"  {node.name} ({node.node_id})")
    playbooks = build_playbooks(graph)
    print(f"\n可生成 skill：{len(playbooks)} 份（一个故障入口一份，用 list 看清单）")
    print()
    _print_report(report)
    return 0


def cmd_validate(args) -> int:
    graph, report, sources = _load_graph(args)
    print(f"输入：{'、'.join(sources)}")
    print(f"通过校验的节点 {len(graph)}；关系 {len(graph.edges)}")
    _print_report(report, limit=args.limit)
    orphans = graph.orphan_nodes()
    if orphans:
        print(f"\n提示：{len(orphans)} 个节点没有任何关系。")
    if report.errors:
        print(f"\n发现 {len(report.errors)} 条错误。")
        return 1
    return 0


def _build_one(graph: Graph, symptom: Node, name: str, out_dir: Path, args, sources) -> int:
    playbook = build_playbook(graph, symptom)
    options = BuildOptions(
        name=name,
        description=args.description,
        evidence_limit=args.evidence,
        data_mode=args.data,
        include_script=not args.no_script,
        include_lead=not args.no_lead,
        sources=sources,
    )
    package = build_package(graph, playbook, options)
    result = lint_text(package.files["SKILL.md"])

    if args.dry_run:
        print(f"技能名：{normalise_name(name)}｜入口：{symptom.name}（{symptom.node_id}）")
        print("将写出：")
        for relative in sorted(package.files):
            print(f"  {relative}  ({len(package.files[relative])} 字符)")
        for note in package.notes:
            print(f"提示：{note}")
        _print_lint(result)
        return 0 if result.ok else 1

    written = package.write(out_dir, force=args.force)
    print(f"技能已生成：{out_dir}")
    print(f"  技能名：{normalise_name(name)}")
    print(f"  入口症状：{symptom.name}（{symptom.node_id}）")
    print(f"  前置检查 {len(playbook.entry_checks)} 条；排查步骤 {len(playbook.causes)} 步；文件 {len(written)} 个")
    for note in package.notes:
        print(f"  提示：{note}")
    _print_lint(result)
    return 0 if result.ok else 1


def cmd_build(args) -> int:
    graph, report, sources = _load_graph(args)
    if args.strict and report.errors:
        _print_report(report)
        print("\n--strict 模式下存在校验错误，已中止。", file=sys.stderr)
        return 1
    graph = _select(graph, args)
    symptom = _resolve_entry(graph, args.entry)
    if not args.name:
        raise RenderError(
            "模板要求英文技能名，请用 --name 指定（`list` 会给出建议 slug，但请按语义改写）"
        )
    out_dir = Path(args.out)
    code = _build_one(graph, symptom, args.name, out_dir, args, sources)
    if not args.dry_run:
        _install_hint(out_dir, normalise_name(args.name))
    return code


def cmd_build_all(args) -> int:
    graph, report, sources = _load_graph(args)
    if args.strict and report.errors:
        _print_report(report)
        print("\n--strict 模式下存在校验错误，已中止。", file=sys.stderr)
        return 1
    graph = _select(graph, args)
    names: Dict[str, str] = {}
    if args.names:
        path = Path(args.names)
        if not path.exists():
            raise RenderError(f"{path}: 命名映射文件不存在")
        try:
            names = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RenderError(f"{path}: 不是合法 JSON（{exc}）") from exc
        if not isinstance(names, dict):
            raise RenderError(f"{path}: 应为 {{node_id: slug}} 的对象")

    symptoms = entry_symptoms(graph)
    if args.limit:
        symptoms = symptoms[: args.limit]
    if not symptoms:
        raise RenderError("子图里没有 symptom 节点，无法生成 skill")

    root = Path(args.out)
    failures = 0
    unnamed: List[str] = []
    for symptom in symptoms:
        name = names.get(symptom.node_id) or names.get(symptom.name) or ""
        if not name:
            name = suggested_slug(symptom)
            unnamed.append(f"{symptom.node_id}  {symptom.name}  →  {name}")
        slug = normalise_name(name)
        code = _build_one(graph, symptom, name, root / slug, args, sources)
        failures += 1 if code else 0
        print()
    print(f"共生成 {len(symptoms)} 份 skill，{failures} 份未通过模板检查。")
    if unnamed:
        print(
            f"\n以下 {len(unnamed)} 份用了机械生成的 slug（模板要求英文名，请按语义改写后重跑，"
            "或用 --names 提供 {node_id: slug} 映射）："
        )
        for line in unnamed[:20]:
            print(f"  {line}")
        if len(unnamed) > 20:
            print(f"  …另有 {len(unnamed) - 20} 条")
    return 1 if failures else 0


def cmd_lint(args) -> int:
    failures = 0
    for target in args.targets:
        result = lint_path(Path(target))
        print(f"{target}:")
        _print_lint(result)
        failures += 1 if not result.ok else 0
    return 1 if failures else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build_skill.py",
        description="把 JSON 知识图谱子图编译成符合模板的排障 skill（一个故障入口一份）",
    )
    parser.add_argument("--version", action="version", version=f"subkg-to-skill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="列出故障入口（symptom）与建议 slug")
    _add_input_arguments(listing)
    _add_selection_arguments(listing)
    listing.add_argument("--limit", type=int, default=30, help="最多列出多少个")
    listing.set_defaults(func=cmd_list)

    build = sub.add_parser("build", help="为一个故障入口生成 skill")
    _add_input_arguments(build)
    _add_selection_arguments(build)
    _add_output_arguments(build)
    build.add_argument("--entry", default="", help="入口症状：node_id、id 前缀或名称关键词")
    build.add_argument("--name", default="", help="技能名（英文 slug，模板硬性要求）")
    build.add_argument("--description", default="", help="frontmatter 描述（不给则由症状自动生成）")
    build.set_defaults(func=cmd_build)

    build_all = sub.add_parser("build-all", help="给每个故障入口各生成一份 skill")
    _add_input_arguments(build_all)
    _add_selection_arguments(build_all)
    _add_output_arguments(build_all)
    build_all.add_argument("--names", default="", help="{node_id: slug} 的 JSON 映射文件")
    build_all.add_argument("--limit", type=int, default=0, help="最多生成多少份（0=不限）")
    build_all.add_argument("--description", default="", help="统一的 frontmatter 描述（一般不用）")
    build_all.set_defaults(func=cmd_build_all)

    inspect = sub.add_parser("inspect", help="查看子图规模与分布")
    _add_input_arguments(inspect)
    _add_selection_arguments(inspect)
    inspect.set_defaults(func=cmd_inspect)

    validate = sub.add_parser("validate", help="只做结构校验")
    _add_input_arguments(validate)
    validate.add_argument("--limit", type=int, default=50, help="最多打印多少条明细")
    validate.set_defaults(func=cmd_validate)

    lint = sub.add_parser("lint", help="检查已生成的 skill 是否符合模板")
    lint.add_argument("targets", nargs="+", help="skill 目录或 SKILL.md 路径")
    lint.set_defaults(func=cmd_lint)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "inputs", None) is not None and not (args.inputs or args.nodes or args.edges):
        parser.error("请至少给出一个输入文件或目录（或用 --nodes/--edges 指定）")
    try:
        return args.func(args)
    except (SubgraphLoadError, RenderError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    except BrokenPipeError:  # output piped into `head` and friends
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
