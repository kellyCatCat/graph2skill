"""Command line interface.

    build_skill.py inspect <子图>                        # 规模、分布与结构校验
    build_skill.py list    <子图>                        # 故障分组、根因构成、合并建议
    build_skill.py plan    <子图> [--scenarios s.json]   # 生成前预览规模与交付统计
    build_skill.py build   <子图> --name <slug> --out <目录>                # 一个子图一份
    build_skill.py build   <子图> --entry <症状> --name <slug> --out <目录>  # 只做一个故障
    build_skill.py build   <子图> --each --out <目录>                       # 每个故障各一份
    build_skill.py check   <skill 目录> --graph <原图>   # 模板检查 + 后校验

默认粒度是**一个子图一份 skill**：图里的每个故障成为它的一个场景，公共前置共用，
步骤按场景写进 `reference/`。文档遵循四章节模板。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from subkg2skill import __version__, scenarios, schema
from subkg2skill.compose import build_multi_doc
from subkg2skill.doc import SHARED_COVERAGE, scenario_label
from subkg2skill.graph import Graph, ValidationReport
from subkg2skill.lint import lint_files, lint_path
from subkg2skill.loader import SubgraphLoadError, load
from subkg2skill.markdown import render_package
from subkg2skill.plan import metrics, plan_document, render_metrics, render_plan
from subkg2skill.playbook import (
    build_merged_playbook,
    build_playbook,
    entry_scenarios,
    entry_symptoms,
    fault_groups,
    suggest_merges,
)
from subkg2skill.render import (
    BuildOptions,
    RenderError,
    _assign_scenario_slugs,
    build_package,
    normalise_name,
    suggested_slug,
)
from subkg2skill.scenarios import ScenarioSet
from subkg2skill.verify import VerifyResult, verify_files, verify_path


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
    parser.add_argument("--force", action="store_true", help="覆盖已有目录")
    parser.add_argument("--dry-run", action="store_true", help="只打印将生成的内容")
    parser.add_argument(
        "--include-example-specific",
        action="store_true",
        help="保留 example_specific 条目（含案例地址、设备名与组网，默认剔除）",
    )
    parser.add_argument(
        "--keep-undecidable", action="store_true", help="保留既无判据也无修复动作的原因"
    )
    parser.add_argument("--max-steps", type=int, default=0, help="排查步骤上限（0=不限）")
    parser.add_argument(
        "--shared-coverage",
        type=float,
        default=SHARED_COVERAGE,
        help=(
            f"多场景时公共前置的门槛：一条采集要被这个比例的场景读到才留在公共层"
            f"（默认 {SHARED_COVERAGE:g}；分流判据与采集期判根因不受此限）"
        ),
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="剔除与本场景无关的节点：node_id 或名称关键词（如 MPLS），可重复",
    )
    parser.add_argument(
        "--skill-index",
        default="",
        help=(
            "{node_id: slug} 的 JSON：本批次别的 skill 覆盖了哪些故障入口。"
            "图里跨故障的边（refers_to / leads_to）据此写成可打开的 skill 引用；"
            "不给也会写出转向，只是没有 slug。--each 自己算，不用给"
        ),
    )


def _load_graph(args):
    bundle, sources = load(args.inputs, node_files=args.nodes, edge_files=args.edges)
    graph, report = Graph.from_bundle(bundle, strict=bool(args.strict))
    return graph, report, sources


def _select(graph: Graph, args) -> Graph:
    return scenarios.select(
        graph,
        roots=args.root,
        node_types=args.node_type,
        sections=args.section,
        vendors=args.vendor,
        query=args.query,
        depth=args.depth,
    )


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


def _print_verify(result: VerifyResult, *, prefix: str = "  ") -> None:
    """Report the grounding check: what was traced back, and what was not."""
    checked = "、".join(f"{kind} {count}" for kind, count in result.checked.items())
    if result.ok and not result.warnings:
        detail = f"：{checked}" if checked else ""
        print(f"{prefix}后校验：通过（逐条回查 {result.total_checked()} 项{detail}）")
        return
    print(
        f"{prefix}后校验：{len(result.errors)} 错误 / {len(result.warnings)} 警告"
        f"（共查 {result.total_checked()} 项）"
    )
    for finding in result.findings[:20]:
        print(f"{prefix}  {finding.render()}")
    if len(result.findings) > 20:
        print(f"{prefix}  …另有 {len(result.findings) - 20} 条")
    if result.errors:
        print(f"{prefix}  以上内容在子图里没有来源，属于幻觉：删掉，或改回来源原样的写法，再重跑。")


def _print_omitted(omitted: Sequence, *, prefix: str = "  ", limit: int = 10) -> None:
    """List what was left out, with the reason for each."""
    if not omitted:
        return
    print(f"{prefix}未进入正文的条目（{len(omitted)} 条）：")
    for name, reason in list(omitted)[:limit]:
        print(f"{prefix}  {name}：{reason}")
    if len(omitted) > limit:
        print(f"{prefix}  …另有 {len(omitted) - limit} 条")


def _print_metrics(doc, *, prefix: str = "  ", files=None) -> None:
    """Say which delivery numbers came out of range, so they get reported on."""
    if doc is None:
        return
    off = [metric for metric in metrics(doc, files) if not metric.ok]
    if not off:
        print(f"{prefix}交付统计：全部指标在健康值内")
        return
    print(f"{prefix}交付统计：{len(off)} 项超出健康值，交付时要说明")
    for metric in off:
        print(f"{prefix}  {metric.name}：{metric.value}（期望 {metric.healthy}）")


def _install_hint(out_dir: Path, name: str) -> None:
    print("\n装进框架（整个目录一起拷，reference/ 是 skill 的一部分）：")
    print(f"  cp -r {out_dir} .claude/skills/{name}          # Claude Code（项目级）")
    print(f"  cp -r {out_dir} ~/.claude/skills/{name}        # Claude Code（全局）")
    print(f"  cp -r {out_dir} .opencode/skill/{name}         # opencode（项目级）")
    print(f"  cp -r {out_dir} ~/.config/opencode/skill/{name}")


# ------------------------------------------------------------- commands
def cmd_list(args) -> int:
    graph, report, sources = _load_graph(args)
    graph = _select(graph, args)
    print(f"输入：{'、'.join(sources)}")
    _print_load_issues(report)
    if args.all_units:
        symptoms = entry_symptoms(graph)
        print(f"故障入口（symptom）共 {len(symptoms)} 个（未按诊断单元拆分）：\n")
        for symptom in symptoms[: args.limit]:
            playbook = build_playbook(graph, symptom)
            print(f"  {symptom.name}")
            print(f"    node_id : {symptom.node_id}")
            print(
                f"    规模    : 原因 {len(playbook.causes)}、检查 {playbook.check_count}、"
                f"修复 {playbook.repair_count}"
            )
            print(f"    诊断单元: {len(scenarios.units_of(graph, symptom))} 个（不拆分会把多个场景混在一起）")
            print()
        if len(symptoms) > args.limit:
            print(f"  …另有 {len(symptoms) - args.limit} 个（--limit 调整）")
        return 0

    groups = fault_groups(graph, min_causes=args.min_causes, merge=not args.no_merge)
    total_scenarios = len(entry_scenarios(graph, min_causes=args.min_causes))
    skipped = len(entry_scenarios(graph, min_causes=0)) - total_scenarios
    if args.no_merge:
        print(f"故障场景（症状 × 诊断单元）共 {len(groups)} 个，一个场景一份 skill：\n")
    else:
        print(
            f"故障（跨来源合并后）共 {len(groups)} 个，来自 {total_scenarios} 个场景；"
            "一个故障一份 skill：\n"
        )
    cache: Dict[str, Graph] = {}
    for group in groups[: args.limit]:
        unit_key = "|".join(sorted(group.units))
        scoped = cache.setdefault(unit_key, graph.scope_to_units(group.units))
        playbook = build_merged_playbook(scoped, group.symptoms, group.units)
        triggers = " / ".join(playbook.trigger_terms()[1:5])
        print(f"  {group.name}")
        if group.sources > 1:
            print(f"    合并来源 : {group.sources} 个 — " + "；".join(
                f"{node.name}({node.node_id})" for node in group.symptoms))
        else:
            print(f"    node_id  : {group.primary.node_id}")
        print(f"    诊断单元 : {'、'.join(group.units) or '(未标注)'}")
        print(
            f"    规模     : 原因 {len(playbook.causes)}、检查 {playbook.check_count}、"
            f"修复 {playbook.repair_count}"
        )
        if len(playbook.causes) > 25:
            print("    ⚠ 合并后原因过多，可能仍是多个场景混在一起，考虑只取其中一两个 --unit")
        if triggers:
            print(f"    触发说法 : {triggers}")
        if args.show_causes:
            print("    根因构成 :")
            scoped_group = graph.scope_to_units(group.units) if group.units else graph
            for symptom in group.symptoms:
                units = "、".join(
                    unit for unit in scoped_group.edge_units(symptom.node_id, "has_cause")
                ) or "(未标注)"
                causes = [
                    node.name for node, _edge in scoped_group.targets(symptom.node_id, "has_cause")
                ]
                print(f"      [{units}] {symptom.name}")
                for cause in causes:
                    print(f"          - {cause}")
                if not causes:
                    print("          （该来源没有 has_cause 关系）")
        print(f"    建议 slug: {suggested_slug(group.primary)}（模板要求英文名，请按语义改写）")
        command = "build " + " ".join(f"--entry {node.node_id}" for node in group.symptoms)
        command += "".join(f" --unit {unit}" for unit in group.units)
        print(f"    生成命令 : {command} --name <english-slug>")
        print()
    if len(groups) > args.limit:
        print(f"  …另有 {len(groups) - args.limit} 个（--limit 调整）")
    if skipped:
        print(f"  另有 {skipped} 个场景候选原因少于 {args.min_causes} 个，已跳过（--min-causes 0 可包含）")

    if args.export_scenarios:
        manifest = {
            "name": "<english-slug>",
            "description": "",
            "scenarios": [
                {
                    "name": group.name,
                    # 该场景在 reference/ 下的文件名（英文 slug）。中文场景名没法
                    # 机械翻译，和技能名一样由调用方按语义给出；留空则用 scenario-a
                    # 这类占位名，并在生成时列出来提醒改。
                    "slug": "",
                    "entries": [node.node_id for node in group.symptoms],
                    "units": list(group.units),
                    # 与本场景无关的节点：写 node_id 或名称关键词，生成时整条剔除
                    "exclude": [],
                }
                for group in groups
            ],
        }
        path = Path(args.export_scenarios)
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            f"\n已导出 {len(groups)} 个场景到 {path}；改好场景名与技能名后："
            f"\n  build <图> --scenarios {path} --out <目录>"
        )

    if args.suggest_merge:
        suggestions = suggest_merges(graph, groups)
        print()
        if not suggestions:
            print("没有发现名字不同但内容高度重叠的故障。")
            return 0
        print(
            f"以下 {len(suggestions)} 组故障名字不同，但根因大量重叠，可能是同一故障的两种写法。"
            "**要不要合并由你判断，判断标准是修复动作是否相同，不是名字像不像**——"
            "下面按修复列给出对比：\n"
        )
        for suggestion in suggestions[: args.limit]:
            print(f"  {suggestion.left.name}  ＋  {suggestion.right.name}")
            print(
                f"    根因重叠 : {len(suggestion.shared_causes)} 个"
                f"（重叠度 {suggestion.overlap:.0%}）— " + "、".join(suggestion.shared_causes[:4])
            )
            print(f"    结论     : {suggestion.verdict}")
            if suggestion.same_fix:
                print(
                    f"    修复相同 : {len(suggestion.same_fix)} 个 — "
                    + "、".join(suggestion.same_fix[:4])
                )
            if suggestion.one_sided_fix:
                print(
                    f"    单边有CLI: {len(suggestion.one_sided_fix)} 个（合并时取有命令的那份）— "
                    + "、".join(suggestion.one_sided_fix[:4])
                )
            if suggestion.different_fix:
                print(
                    f"    修复不同 : {len(suggestion.different_fix)} 个（两个动作都要保留，"
                    "或根本是两个故障）— " + "、".join(suggestion.different_fix[:4])
                )
            if suggestion.shared_commands:
                print(
                    f"    共用命令 : {len(suggestion.shared_commands)} 条"
                    "（仅供参考，命令是手段不是故障，别按它合并）— "
                    + "、".join(f"`{command}`" for command in suggestion.shared_commands[:3])
                )
            command = "build " + " ".join(f"--entry {node.node_id}" for node in suggestion.entries)
            command += "".join(f" --unit {unit}" for unit in suggestion.units)
            print(f"    合并命令 : {command} --name <english-slug>")
            print()
    return 0


def _unreachable(graph: Graph) -> List:
    """Connected nodes no symptom leads to — they cannot end up in any skill."""
    symptoms = [node.node_id for node in graph.of_type("symptom")]
    reached = graph.reachable(symptoms, edge_types=schema.FORWARD_EDGES)
    connected = {edge.source for edge in graph.edges} | {edge.target for edge in graph.edges}
    return [
        node
        for node in graph.iter_nodes()
        if node.node_id in connected and node.node_id not in reached
    ]


def _print_load_issues(report: ValidationReport) -> None:
    """Records dropped at load time are absent from everything below; say so."""
    if report.issues:
        print(
            f"提示：载入时有 {len(report.issues)} 条告警/丢弃，"
            "这些记录不参与下面的结果（inspect 看明细）"
        )


def cmd_inspect(args) -> int:
    """Phase 1: what the export holds, and whether it loads cleanly."""
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
    # Faults are split by the unit on each relation, not on each node: a node is
    # reused across units, so counting nodes would not match what list shows.
    units = graph.edge_units()
    if units:
        print(f"\n诊断单元（按关系计；共 {len(units)} 个，前 10）：")
        for unit, count in list(units.items())[:10]:
            print(f"  {unit}: {count}")
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
    unreachable = _unreachable(graph)
    if unreachable:
        print(f"\n症状走不到的节点：{len(unreachable)} 个（从任何症状出发都到不了，不会进入任何 skill；前 5）")
        for node in unreachable[:5]:
            print(f"  {node.name} ({node.node_id}，{node.node_type})")
    groups = fault_groups(graph)
    scenarios_used = len(entry_scenarios(graph, min_causes=1))
    short = len(entry_scenarios(graph, min_causes=0)) - scenarios_used
    print(
        f"\n故障：{len(groups)} 个（跨来源合并后，来自 {scenarios_used} 个“症状 × 诊断单元”场景）。"
        "默认一个子图生成一份 skill，它们是其中的场景；用 list 看清单"
    )
    if short:
        print(f"另有 {short} 个场景没有候选原因，默认不进入 skill")
    print()
    _print_report(report, limit=args.limit)
    if report.errors:
        print(f"\n发现 {len(report.errors)} 条错误。")
        return 1
    return 0


def _scenario_set(graph: Graph, args) -> ScenarioSet:
    """This subgraph's scenarios: from the manifest if there is one, else as grouped."""
    if args.scenarios:
        found = scenarios.from_manifest(graph, Path(args.scenarios), args.exclude)
    else:
        names_file = getattr(args, "names", "")
        found = scenarios.from_groups(
            graph,
            min_causes=args.min_causes,
            merge=not args.no_merge,
            limit=args.limit,
            exclude=args.exclude,
            names=scenarios.load_slug_map(Path(names_file), "命名映射") if names_file else None,
        )
    for note in found.notes:
        print(f"提示：{note}")
    return found


def _skill_index(args) -> Dict[str, str]:
    """The batch's ``node_id -> slug`` map, when the caller supplied one.

    Needed only when the skills are built one run at a time: a single
    ``build --each`` already knows every slug it is about to write.
    """
    path = getattr(args, "skill_index", "")
    return scenarios.load_slug_map(Path(path), "skill 索引") if path else {}


def cmd_plan(args) -> int:
    """Phase 3: what would actually be written, and what could still be folded."""
    graph, report, sources = _load_graph(args)
    graph = _select(graph, args)
    found = _scenario_set(graph, args)
    source = f"场景清单 {args.scenarios}" if args.scenarios else "自动分组（未用场景清单）"

    policy = BuildOptions(
        include_example_specific=args.include_example_specific,
        keep_undecidable=args.keep_undecidable,
        max_steps=args.max_steps,
        shared_coverage=args.shared_coverage,
    ).policy()
    doc = build_multi_doc(found.graph, found.named, policy)
    for spec, node_name in found.removed:
        doc.omitted.append((node_name, f"按 exclude 剔除（匹配 {spec!r}）"))
    plan = plan_document(doc)
    unnamed = _assign_scenario_slugs(doc, found.slugs)
    # 文档规模要看渲染后的正文，而且要按交付时的拆分来看——多场景时读者读的是
    # 入口 + 他那一个场景，不是全部场景之和。技能名此时可能还是占位符，不影响行数。
    preview = render_package(doc, name="preview", description="预览")

    name = found.name
    print(f"输入：{'、'.join(sources)}")
    print(f"编排来源：{source}" + (f"；技能名 {name}" if name and not name.startswith("<") else ""))
    print()
    for line in render_plan(plan, limit=args.limit or 20):
        print(line)
    for line in render_metrics(metrics(doc, preview)):
        print(line)
    if unnamed:
        # build 会直接拒绝，在这里先说清楚要补哪几个
        print(f"以下 {len(unnamed)} 个场景还没有英文文件名，`build` 会拒绝生成：")
        for scenario_name, _slug in unnamed:
            print(f"  {scenario_name}")
        print("  按语义拟英文名：自动分组时 --names 给 {node_id: slug}，用清单时填每个场景的 slug\n")
    if report.issues:
        print(f"载入时有 {len(report.issues)} 条告警/丢弃（inspect 可看明细）")
    if not args.scenarios:
        print(
            "把编排固化下来：list --export-scenarios scenarios.json（改名/调整分组）"
            "→ plan --scenarios scenarios.json → build --scenarios scenarios.json"
        )
    return 0


def _deliver(
    found: ScenarioSet,
    name: str,
    out_dir: Path,
    args,
    *,
    skill_index: Dict[str, str],
    install_hint: bool = True,
) -> int:
    """Build one skill, run both checks on it, then write it (or say what would be written)."""
    options = BuildOptions(
        name=name,
        description=args.description or found.description,
        include_example_specific=args.include_example_specific,
        keep_undecidable=args.keep_undecidable,
        max_steps=args.max_steps,
        shared_coverage=args.shared_coverage,
        excluded=found.removed,
        skill_index=dict(skill_index),
        scenario_slugs=found.slugs,
    )
    package = build_package(found.graph, found.named, options)
    result = lint_files(package.files)
    # Post-verification: every command, root cause, criterion and parameter in
    # the finished document has to be findable in the subgraph it came from.
    grounding = verify_files(package.files, found.reference.subgraph(found.covered))
    ok = result.ok and grounding.ok
    slug = normalise_name(name)
    stats = package.stats
    units = f"｜诊断单元：{'、'.join(found.units)}" if found.units else ""

    if len(found.named) == 1:
        playbook = found.named[0][1]
        symptom, merged = playbook.symptom, playbook.symptoms
        entry = f"{symptom.name}（{symptom.node_id}）"
        brief = f"入口：{entry}" + (f"｜合并来源 {len(merged)} 个" if len(merged) > 1 else "") + units
        summary = [f"入口症状：{entry}{units}"]
        if len(merged) > 1:
            summary.append("合并来源：" + "；".join(f"{node.name}（{node.node_id}）" for node in merged))
        summary.append(
            f"前置检查 {stats.get('prechecks', 0)} 条；排查步骤 {stats.get('steps', 0)} 步；"
            f"根因 {stats.get('root_causes', 0)} 个；交付文件 {len(package.files)} 个"
        )
        listing: List[str] = []
    else:
        brief = f"场景 {len(found.named)} 个"
        summary = [
            f"场景 {len(found.named)} 个；公共前置检查 {stats.get('prechecks', 0)} 条；"
            f"排查步骤 {stats.get('steps', 0)} 步；根因 {stats.get('root_causes', 0)} 个；"
            f"交付文件 {len(package.files)} 个"
        ]
        # 实际规模（剔除、折叠之后）看 plan；这里只列场景
        listing = [
            f"场景{scenario_label(index)}：{scenario_name}"
            for index, (scenario_name, _book) in enumerate(found.named)
        ]

    if args.dry_run:
        print(f"技能名：{slug}｜{brief}")
        for line in listing:
            print(f"  {line}")
        print("将写出：")
        for relative in sorted(package.files):
            print(f"  {relative}  ({len(package.files[relative])} 字符)")
        for note in package.notes:
            print(f"提示：{note}")
        _print_omitted(package.omitted, prefix="")
        _print_lint(result, prefix="")
        _print_verify(grounding, prefix="")
        return 0 if ok else 1

    package.write(out_dir, force=args.force)
    print(f"技能已生成：{out_dir}")
    print(f"  技能名：{slug}")
    for line in summary:
        print(f"  {line}")
    for line in listing:
        print(f"    {line}")
    for note in package.notes:
        print(f"  提示：{note}")
    _print_omitted(package.omitted)
    _print_lint(result)
    _print_verify(grounding)
    _print_metrics(package.doc, files=package.files)
    if install_hint:
        _install_hint(out_dir, slug)
    return 0 if ok else 1


def cmd_build(args) -> int:
    graph, report, sources = _load_graph(args)
    if args.strict and report.errors:
        _print_report(report)
        print("\n--strict 模式下存在校验错误，已中止。", file=sys.stderr)
        return 1
    _print_load_issues(report)
    graph = _select(graph, args)
    if args.each:
        if args.entry or args.scenarios or args.name:
            raise RenderError("--each 按故障逐个命名输出，不能与 --entry / --scenarios / --name 同用")
        return cmd_build_each(args, graph)
    # 默认粒度是**一个子图一份 skill**：不指定入口时，把这张图里的每个故障编成
    # 一个场景，公共前置共用，步骤各进各的 reference/ 文件。指定了 --entry 才是
    # 只做那一个故障（--each 则是每个故障各自独立成一份）。
    if args.scenarios or not (args.entry or args.unit or args.all_units):
        found = _scenario_set(graph, args)
        name = args.name or found.name
        if not name or name.startswith("<"):
            raise RenderError(
                "场景清单里的 name 还是占位符；模板要求英文技能名，用 --name 指定或改清单"
                if args.scenarios
                else "模板要求英文技能名，请用 --name 指定这份 skill 的名字"
            )
        return _deliver(found, name, Path(args.out), args, skill_index=_skill_index(args))

    symptoms = [scenarios.resolve_entry(graph, entry) for entry in args.entry or [""]]
    if len(symptoms) == 1:
        same = scenarios.same_fault_symptoms(graph, symptoms[0])
        if args.merge_same_name:
            symptoms = same
        elif len(same) > 1:
            print(
                f"提示：另有 {len(same) - 1} 个同名症状（其他来源）描述同一故障，"
                "加 --merge-same-name 可合并成一份更完整的 skill："
            )
            for node in same[1:6]:
                print(f"  {node.name}（{node.node_id}）")
    units = scenarios.resolve_units(graph, symptoms, args.unit, args.all_units)
    if not args.name:
        raise RenderError(
            "模板要求英文技能名，请用 --name 指定（`list` 会给出建议 slug，但请按语义改写）"
        )
    found = scenarios.from_entries(graph, symptoms, units, args.exclude)
    return _deliver(found, args.name, Path(args.out), args, skill_index=_skill_index(args))


def cmd_build_each(args, graph: Graph) -> int:
    """Every fault in the subgraph as a skill of its own."""
    plan = scenarios.plan_each(
        graph,
        names=scenarios.load_slug_map(Path(args.names), "命名映射") if args.names else None,
        all_units=args.all_units,
        min_causes=args.min_causes,
        merge=not args.no_merge,
        limit=args.limit,
    )
    index = plan.index(_skill_index(args))
    root = Path(args.out)
    cache: Dict[str, Graph] = {}
    failures = 0
    for symptoms, units, name in plan.skills:
        found = scenarios.from_entries(graph, symptoms, units, args.exclude, cache)
        code = _deliver(
            found, name, root / normalise_name(name), args, skill_index=index, install_hint=False
        )
        failures += 1 if code else 0
        print()
    print(f"共生成 {len(plan.skills)} 份 skill，{failures} 份未通过模板检查。")
    if plan.skipped:
        print(
            f"另有 {plan.skipped} 个场景候选原因少于 {args.min_causes} 个未生成"
            "（排查步骤会是空的；--min-causes 0 可强制生成）"
        )
    if plan.unnamed:
        print(
            f"\n以下 {len(plan.unnamed)} 份用了机械生成的 slug（模板要求英文名，请按语义改写后重跑，"
            "或用 --names 提供 {node_id: slug} 映射）："
        )
        for line in plan.unnamed[:20]:
            print(f"  {line}")
        if len(plan.unnamed) > 20:
            print(f"  …另有 {len(plan.unnamed) - 20} 条")
    return 1 if failures else 0


def cmd_check(args) -> int:
    """Shape against the template, then provenance against the original graph."""
    failures = 0
    for target in args.targets:
        print(f"{target}:")
        result = lint_path(Path(target))
        _print_lint(result)
        ok = result.ok
        if args.graph:
            grounding = verify_path(Path(target), list(args.graph))
            _print_verify(grounding)
            ok = ok and grounding.ok
        else:
            print("  后校验：未给 --graph，已跳过（交付前必须对原图补跑）")
        failures += 0 if ok else 1
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
    listing.add_argument("--all-units", action="store_true", help="不按诊断单元拆分")
    listing.add_argument(
        "--no-merge", action="store_true", help="不按故障归并同名症状，逐个场景列出"
    )
    listing.add_argument(
        "--export-scenarios",
        default="",
        help="把当前分组导出成场景清单 JSON，编辑后交给 build --scenarios 生成一份多场景 skill",
    )
    listing.add_argument(
        "--show-causes",
        action="store_true",
        help="按来源列出每组的根因清单，用来判断一个组是不是混了两类故障",
    )
    listing.add_argument(
        "--suggest-merge",
        action="store_true",
        help="额外报告名字不同但根因高度重叠的故障，供人工判断是否合并",
    )
    listing.add_argument("--min-causes", type=int, default=1, help="至少几个候选原因才算一个场景")
    listing.set_defaults(func=cmd_list)

    build = sub.add_parser(
        "build", help="把一个子图编成一份 skill（--entry 则只做其中一个故障）"
    )
    _add_input_arguments(build)
    _add_selection_arguments(build)
    _add_output_arguments(build)
    build.add_argument(
        "--entry",
        action="append",
        default=[],
        help=(
            "只做这一个故障：node_id、id 前缀或名称关键词，可重复（多个即合并）。"
            "不给则把整个子图编成一份多场景 skill"
        ),
    )
    build.add_argument(
        "--unit", action="append", default=[], help="诊断单元（章节号/案例 ID），可重复"
    )
    build.add_argument(
        "--scenarios",
        default="",
        help="场景清单 JSON（list --export-scenarios 生成）：一份 skill 含公共前置检查 + 多个场景",
    )
    build.add_argument("--min-causes", type=int, default=1, help="自动分组时，至少几个候选原因才算一个场景")
    build.add_argument(
        "--no-merge", action="store_true", help="自动分组时不跨来源归并同名症状"
    )
    build.add_argument(
        "--limit", type=int, default=0, help="自动分组时最多收几个场景 / --each 时最多生成几份（0=不限）"
    )
    build.add_argument(
        "--names",
        default="",
        help=(
            "{node_id: slug} 的 JSON：自动分组时每个场景的参考文件名（reference/<slug>.md），"
            "--each 时每份 skill 的名字。用 --scenarios 时改在清单里填 slug"
        ),
    )
    build.add_argument(
        "--merge-same-name",
        action="store_true",
        help="把其他来源里同名的症状一并合并进来（手册 + 作战树 + 案例库）",
    )
    build.add_argument(
        "--all-units",
        action="store_true",
        help="合并该症状的全部诊断单元（会混合多个故障场景）；--each 时每个症状一份、不按单元拆",
    )
    build.add_argument(
        "--each", action="store_true", help="每个故障各自独立成一份 skill，写到 <输出目录>/<slug>/"
    )
    build.add_argument("--name", default="", help="技能名（英文 slug，模板硬性要求）")
    build.add_argument("--description", default="", help="frontmatter 描述（不给则由症状自动生成）")
    build.set_defaults(func=cmd_build)

    inspect = sub.add_parser("inspect", help="查看子图规模与分布，并做结构校验（有错误退出码为 1）")
    _add_input_arguments(inspect)
    _add_selection_arguments(inspect)
    inspect.add_argument("--limit", type=int, default=20, help="校验明细最多打印多少条")
    inspect.set_defaults(func=cmd_inspect)

    plan = sub.add_parser(
        "plan", help="生成前预览：每个场景的步骤/根因/修复命令数，以及还能合并什么"
    )
    _add_input_arguments(plan)
    _add_selection_arguments(plan)
    plan.add_argument("--scenarios", default="", help="场景清单 JSON；不给则用自动分组")
    plan.add_argument("--limit", type=int, default=20, help="提示最多列出多少条")
    plan.add_argument("--min-causes", type=int, default=1, help="自动分组时的最小候选原因数")
    plan.add_argument("--no-merge", action="store_true", help="自动分组时不跨来源归并")
    plan.add_argument(
        "--include-example-specific", action="store_true", help="保留 example_specific 条目"
    )
    plan.add_argument("--keep-undecidable", action="store_true", help="保留既无判据也无修复的原因")
    plan.add_argument(
        "--shared-coverage",
        type=float,
        default=SHARED_COVERAGE,
        help="公共前置的门槛，与 build 同义",
    )
    plan.add_argument("--max-steps", type=int, default=0, help="排查步骤上限（0=不限）")
    plan.add_argument(
        "--exclude", action="append", default=[], help="剔除无关节点：node_id 或名称关键词，可重复"
    )
    plan.set_defaults(func=cmd_plan)

    check = sub.add_parser(
        "check", help="交付前检查：模板形状（lint）+ 逐条回查原图（verify，需 --graph）"
    )
    check.add_argument("targets", nargs="+", help="skill 目录或 SKILL.md 路径")
    check.add_argument(
        "--graph", action="append", default=[], help="原图（node/edge 文件或目录），可重复"
    )
    check.set_defaults(func=cmd_check)
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
