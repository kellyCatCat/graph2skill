"""Command line interface: ``build``, ``inspect`` and ``validate``.

    subkg2skill build node.json edge.json --out out/ipran --name ipran-diagnosis
    subkg2skill build graph_dir --root symptom_9a1b --depth 3 --out out/one-case
    subkg2skill inspect node.json edge.json
    subkg2skill validate node.json edge.json --strict
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional, Sequence, Set

from subkg2skill import __version__, schema
from subkg2skill.graph import Graph, ValidationReport
from subkg2skill.loader import SubgraphLoadError, load
from subkg2skill.playbook import build_playbooks, coverage
from subkg2skill.render import BuildOptions, RenderError, build_package, normalise_name


def _add_input_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "inputs",
        nargs="*",
        help="节点/边文件或目录（.json/.jsonc/.jsonl）；目录会自动识别 node*/edge* 文件",
    )
    parser.add_argument("--nodes", action="append", default=[], help="显式指定节点文件，可重复")
    parser.add_argument("--edges", action="append", default=[], help="显式指定边文件，可重复")
    parser.add_argument(
        "--strict", action="store_true", help="把校验告警也当作错误（构建时直接失败）"
    )


def _add_selection_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("子图选择（都不给时使用全部输入）")
    group.add_argument(
        "--root",
        action="append",
        default=[],
        help="起点节点：node_id、id 前缀或名称关键词，可重复",
    )
    group.add_argument("--depth", type=int, default=0, help="从起点向前展开的层数（0=不限）")
    group.add_argument(
        "--node-type", action="append", default=[], choices=list(schema.NODE_TYPES), help="按节点类型筛选种子"
    )
    group.add_argument("--section", action="append", default=[], help="按诊断单元/章节号筛选种子")
    group.add_argument("--vendor", action="append", default=[], help="按 scope.vendor 筛选种子")
    group.add_argument("--query", default="", help="按关键词筛选种子")


def _load_graph(args) -> tuple:
    bundle, sources = load(args.inputs, node_files=args.nodes, edge_files=args.edges)
    graph, report = Graph.from_bundle(bundle, strict=bool(args.strict))
    return graph, report, sources


def _resolve_roots(graph: Graph, roots: Sequence[str]) -> Set[str]:
    """Roots may be node ids, id prefixes or plain search terms."""
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
    if args.root:
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
    keep = graph.reachable(seeds, depth=depth)
    return graph.subgraph(keep)


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
    print(f"\n可生成排查手册：{len(playbooks)} 份")
    for playbook in playbooks[:10]:
        print(
            f"  {playbook.symptom.name} — 原因 {len(playbook.causes)}、"
            f"检查 {playbook.check_count}、修复 {playbook.repair_count}"
        )
    if len(playbooks) > 10:
        print(f"  …另有 {len(playbooks) - 10} 份")
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


def cmd_build(args) -> int:
    graph, report, sources = _load_graph(args)
    if args.strict and report.errors:
        _print_report(report)
        print("\n--strict 模式下存在校验错误，已中止。", file=sys.stderr)
        return 1
    graph = _select(graph, args)
    playbooks = build_playbooks(graph, limit=args.max_playbooks)
    options = BuildOptions(
        name=args.name,
        title=args.title,
        description=args.description,
        evidence_limit=args.evidence,
        data_mode=args.data,
        allowed_tools=args.allowed_tools,
        include_script=not args.no_script,
        sources=sources,
    )
    package = build_package(graph, playbooks, report, options)

    if args.dry_run:
        print(f"技能名：{normalise_name(options.name)}")
        print(f"节点 {len(graph)}；关系 {len(graph.edges)}；手册 {len(playbooks)} 份")
        print("将写出：")
        for relative in sorted(package.files):
            print(f"  {relative}  ({len(package.files[relative])} 字符)")
        for note in package.notes:
            print(f"提示：{note}")
        return 0

    out_dir = Path(args.out)
    written = package.write(out_dir, force=args.force)
    stats = coverage(graph, playbooks)
    print(f"技能已生成：{out_dir}")
    print(f"  技能名：{normalise_name(options.name)}")
    print(f"  节点 {len(graph)}；关系 {len(graph.edges)}；排查手册 {len(playbooks)} 份")
    print(f"  覆盖节点 {len(stats['documented'])}；未覆盖 {len(stats['uncovered'])}")
    print(f"  文件 {len(written)} 个")
    for note in package.notes:
        print(f"  提示：{note}")
    if report.issues:
        print(f"  载入告警/丢弃 {len(report.issues)} 条，明细见 references/coverage.md")
    print("\n安装方式见 INSTALL.md（Claude Code: .claude/skills/；opencode: .opencode/skill/）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subkg2skill",
        description="把 JSON 格式的故障诊断知识图谱子图编译成可直接使用的智能体技能包",
    )
    parser.add_argument("--version", action="version", version=f"subkg2skill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="生成技能目录")
    _add_input_arguments(build)
    _add_selection_arguments(build)
    build.add_argument("--out", required=True, help="输出目录")
    build.add_argument("--name", default="kg-fault-diagnosis", help="技能名（小写字母/数字/连字符）")
    build.add_argument("--title", default="", help="SKILL.md 标题")
    build.add_argument("--description", default="", help="frontmatter 描述（不给则自动生成）")
    build.add_argument("--max-playbooks", type=int, default=0, help="最多生成多少份手册（0=不限）")
    build.add_argument("--evidence", type=int, default=3, help="每个条目展示的来源条数")
    build.add_argument(
        "--data", choices=("full", "slim", "none"), default="full", help="随包数据的详细程度"
    )
    build.add_argument("--allowed-tools", default="", help="写入 frontmatter 的 allowed-tools")
    build.add_argument("--no-script", action="store_true", help="不生成查询脚本")
    build.add_argument("--force", action="store_true", help="覆盖已有目录")
    build.add_argument("--dry-run", action="store_true", help="只打印将生成的内容")
    build.set_defaults(func=cmd_build)

    inspect = sub.add_parser("inspect", help="查看子图规模、分布与可生成的手册")
    _add_input_arguments(inspect)
    _add_selection_arguments(inspect)
    inspect.set_defaults(func=cmd_inspect)

    validate = sub.add_parser("validate", help="只做结构校验")
    _add_input_arguments(validate)
    validate.add_argument("--limit", type=int, default=50, help="最多打印多少条明细")
    validate.set_defaults(func=cmd_validate)
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
