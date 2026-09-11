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
from typing import Dict, List, Optional, Sequence, Set, Tuple

from subkg2skill import __version__, schema
from subkg2skill.graph import Graph, Node, ValidationReport
from subkg2skill.lint import lint_path, lint_text
from subkg2skill.loader import SubgraphLoadError, load
from subkg2skill.playbook import (
    build_merged_playbook,
    build_playbook,
    build_playbooks,
    entry_scenarios,
    entry_symptoms,
    fault_groups,
    fault_key,
    suggest_merges,
)
from subkg2skill.render import (
    BuildOptions,
    RenderError,
    build_package,
    normalise_name,
    suggested_slug,
)
from subkg2skill.plan import metrics, plan_document, render_metrics, render_plan
from subkg2skill.template import build_multi_doc, scenario_label


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
        "--exclude",
        action="append",
        default=[],
        help="剔除与本场景无关的节点：node_id 或名称关键词（如 MPLS），可重复",
    )


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


def _units_of(graph: Graph, symptom: Node) -> Dict[str, int]:
    return graph.edge_units(symptom.node_id, "has_cause", "diagnosed_by", "next_step")


def _resolve_units(
    graph: Graph, symptoms: Sequence[Node], units: Sequence[str], all_units: bool
) -> List[str]:
    """Pick the diagnostic units this skill covers, or explain why one is needed."""
    if all_units:
        return []
    available: Dict[str, int] = {}
    for symptom in symptoms:
        for name, count in _units_of(graph, symptom).items():
            if name != "(未标注)":
                available[name] = available.get(name, 0) + count
    if units:
        resolved: List[str] = []
        for unit in units:
            if not any(Graph.unit_matches(name, unit) for name in available):
                listed = "、".join(list(available)[:8]) or "（无）"
                raise RenderError(f"诊断单元 {unit!r} 在这些症状下没有关系；现有单元：{listed}")
            resolved.append(unit)
        return resolved
    if len(available) <= 1:
        return [next(iter(available))] if available else []
    if len(symptoms) > 1:
        # Merging sources is the point of this build; their units all belong.
        return list(available)
    listed = "\n".join(f"    {name}（{count} 条关系）" for name, count in list(available.items())[:10])
    raise RenderError(
        f"“{symptoms[0].name}”的关系分布在 {len(available)} 个诊断单元里，合成一份 skill 会把多个"
        f"故障场景混在一起。请用 --unit 指定其中一个（可重复；或 --all-units 明确要合并）：\n{listed}"
    )


def _same_fault_symptoms(graph: Graph, symptom: Node) -> List[Node]:
    """Symptom nodes from other sources describing the same fault."""
    key = fault_key(symptom.name)
    same = [
        node
        for node in graph.of_type("symptom")
        if node.node_id != symptom.node_id and fault_key(node.name) == key
    ]
    return [symptom] + sorted(same, key=lambda node: node.node_id)


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


def _print_metrics(doc, *, prefix: str = "  ") -> None:
    """Say which delivery numbers came out of range, so they get reported on."""
    if doc is None:
        return
    off = [metric for metric in metrics(doc) if not metric.ok]
    if not off:
        print(f"{prefix}交付统计：全部指标在健康值内")
        return
    print(f"{prefix}交付统计：{len(off)} 项超出健康值，交付时要说明")
    for metric in off:
        print(f"{prefix}  {metric.name}：{metric.value}（期望 {metric.healthy}）")


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
    print(f"输入：{'、'.join(sources)}")
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
            print(f"    诊断单元: {len(_units_of(graph, symptom))} 个（不拆分会把多个场景混在一起）")
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


def _build_one(
    graph: Graph,
    symptoms: Sequence[Node],
    name: str,
    out_dir: Path,
    args,
    sources,
    units: Sequence[str] = (),
    cache: Optional[Dict[str, Graph]] = None,
) -> int:
    symptoms = list(symptoms)
    units = [unit for unit in units if unit]
    excluded: List[Tuple[str, str]] = []
    excluded_ids: Set[str] = set()
    unit_key = "|".join(sorted(units))
    if cache is None or unit_key not in cache:
        scoped = graph.scope_to_units(units) if units else graph
        if cache is not None:
            cache[unit_key] = scoped
    else:
        scoped = cache[unit_key]
    scoped = _apply_exclusions(scoped, getattr(args, "exclude", []), excluded, excluded_ids)
    playbook = build_merged_playbook(scoped, symptoms, units)
    symptom = playbook.symptom
    options = BuildOptions(
        name=name,
        description=args.description,
        evidence_limit=args.evidence,
        data_mode=args.data,
        include_script=not args.no_script,
        include_lead=not args.no_lead,
        sources=sources,
        unit="、".join(units),
        include_example_specific=args.include_example_specific,
        keep_undecidable=args.keep_undecidable,
        max_steps=args.max_steps,
        excluded=excluded,
    )
    package = build_package(scoped, playbook, options)
    result = lint_text(package.files["SKILL.md"])

    if args.dry_run:
        print(
            f"技能名：{normalise_name(name)}｜入口：{symptom.name}（{symptom.node_id}）"
            + (f"｜合并来源 {len(symptoms)} 个" if len(symptoms) > 1 else "")
            + (f"｜诊断单元：{'、'.join(units)}" if units else "")
        )
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
    print(
        f"  入口症状：{symptom.name}（{symptom.node_id}）"
        + (f"｜诊断单元：{'、'.join(units)}" if units else "")
    )
    if len(symptoms) > 1:
        print("  合并来源：" + "；".join(f"{node.name}（{node.node_id}）" for node in symptoms))
    stats = package.stats
    print(
        f"  前置检查 {stats.get('prechecks', 0)} 条；排查步骤 {stats.get('steps', 0)} 步；"
        f"根因 {stats.get('root_causes', 0)} 个；文件 {len(written)} 个"
    )
    for note in package.notes:
        print(f"  提示：{note}")
    _print_lint(result)
    _print_metrics(package.doc)
    return 0 if result.ok else 1


def _load_manifest(path: Path) -> Dict:
    if not path.exists():
        raise RenderError(f"{path}: 场景清单文件不存在")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"{path}: 不是合法 JSON（{exc}）") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("scenarios"), list):
        raise RenderError(f"{path}: 应为 {{name, description, scenarios: [...]}} 的对象")
    if not manifest["scenarios"]:
        raise RenderError(f"{path}: scenarios 是空的")
    return manifest


def _apply_exclusions(
    graph: Graph,
    specs: Sequence[str],
    removed: List[Tuple[str, str]],
    removed_ids: Set[str],
) -> Graph:
    """Drop nodes a person judged unrelated, recording what went.

    An exclusion that matches nothing is an error, not a no-op: a typo would
    otherwise silently leave the unrelated material in the document.
    """
    specs = [spec for spec in specs if spec]
    if not specs:
        return graph
    ids, matched = graph.resolve_exclusions(specs)
    unmatched = set(specs) - {spec for spec, _node in matched}
    if unmatched:
        raise RenderError(
            "以下 --exclude / exclude 没有匹配到任何节点（写错了会静默漏掉内容）："
            + "、".join(sorted(unmatched))
        )
    for spec, node in matched:
        if node.node_id not in removed_ids:
            removed_ids.add(node.node_id)
            removed.append((spec, node.name))
    return graph.without(ids)


def _scenarios_from_manifest(
    graph: Graph, path: Path, extra_exclude: Sequence[str] = (), removed_ids: Optional[Set[str]] = None
) -> Tuple[str, str, List[Tuple[str, object]], List[str], List[Tuple[str, str]]]:
    """Read a scenario manifest into (技能名, 描述, [(场景名, playbook)], 单元, 剔除记录)."""
    manifest = _load_manifest(path)
    units: List[str] = []
    for entry in manifest["scenarios"]:
        units += [unit for unit in entry.get("units") or [] if unit]
    units = list(dict.fromkeys(units))
    scoped = graph.scope_to_units(units) if units else graph

    removed: List[Tuple[str, str]] = []
    removed_ids = removed_ids if removed_ids is not None else set()
    named: List[Tuple[str, object]] = []
    for index, entry in enumerate(manifest["scenarios"]):
        ids = entry.get("entries") or []
        if not ids:
            raise RenderError(f"第 {index + 1} 个场景没有 entries")
        symptoms = [_resolve_entry(scoped, node_id) for node_id in ids]
        unit_scope = [unit for unit in entry.get("units") or [] if unit]
        base = scoped.scope_to_units(unit_scope) if unit_scope else scoped
        # 全局 --exclude 对所有场景生效，清单里的 exclude 只作用于本场景
        base = _apply_exclusions(
            base, list(extra_exclude) + list(entry.get("exclude") or []), removed, removed_ids
        )
        playbook = build_merged_playbook(base, symptoms, unit_scope)
        named.append((entry.get("name") or symptoms[0].name, playbook))
    return (
        manifest.get("name", ""),
        manifest.get("description", ""),
        named,
        units,
        removed,
    )


def _scenarios_from_groups(
    graph: Graph, args, removed_ids: Optional[Set[str]] = None
) -> Tuple[List[Tuple[str, object]], List[str], List[Tuple[str, str]]]:
    """Fall back to the automatic grouping when no manifest is given."""
    groups = fault_groups(graph, min_causes=args.min_causes, merge=not args.no_merge)
    if args.limit:
        groups = groups[: args.limit]
    if not groups:
        raise RenderError("子图里没有可生成的故障场景")
    units = list(dict.fromkeys(unit for group in groups for unit in group.units))
    scoped = graph.scope_to_units(units) if units else graph
    removed: List[Tuple[str, str]] = []
    removed_ids = removed_ids if removed_ids is not None else set()
    scoped = _apply_exclusions(scoped, getattr(args, "exclude", []), removed, removed_ids)
    named = [
        (
            group.name,
            build_merged_playbook(
                scoped.scope_to_units(group.units) if group.units else scoped,
                group.symptoms,
                group.units,
            ),
        )
        for group in groups
    ]
    return named, units, removed


def cmd_plan(args) -> int:
    """Phase 3: what would actually be written, and what could still be folded."""
    graph, report, sources = _load_graph(args)
    graph = _select(graph, args)
    removed_ids: Set[str] = set()
    if args.scenarios:
        name, _description, named, units, removed = _scenarios_from_manifest(
            graph, Path(args.scenarios), args.exclude, removed_ids
        )
        source = f"场景清单 {args.scenarios}"
    else:
        named, units, removed = _scenarios_from_groups(graph, args, removed_ids)
        name = ""
        source = "自动分组（未用场景清单）"
    scoped = graph.scope_to_units(units) if units else graph
    scoped = scoped.without(removed_ids)

    policy = BuildOptions(
        include_example_specific=args.include_example_specific,
        keep_undecidable=args.keep_undecidable,
        max_steps=args.max_steps,
    ).policy()
    doc = build_multi_doc(scoped, named, policy)
    for spec, node_name in removed:
        doc.omitted.append((node_name, f"按 exclude 剔除（匹配 {spec!r}）"))
    plan = plan_document(doc)

    print(f"输入：{'、'.join(sources)}")
    print(f"编排来源：{source}" + (f"；技能名 {name}" if name and not name.startswith("<") else ""))
    print()
    for line in render_plan(plan, limit=args.limit or 20):
        print(line)
    for line in render_metrics(metrics(doc)):
        print(line)
    if report.issues:
        print(f"载入时有 {len(report.issues)} 条告警/丢弃，生成后见 reference/evidence.md")
    if not args.scenarios:
        print(
            "把编排固化下来：list --export-scenarios scenarios.json（改名/调整分组）"
            "→ plan --scenarios scenarios.json → build --scenarios scenarios.json"
        )
    return 0


def cmd_build_scenarios(args, graph: Graph, sources) -> int:
    """One document covering several faults off a shared collection phase."""
    removed_ids: Set[str] = set()
    manifest_name, manifest_description, named, units, removed = _scenarios_from_manifest(
        graph, Path(args.scenarios), args.exclude, removed_ids
    )
    name = args.name or manifest_name or ""
    if not name or name.startswith("<"):
        raise RenderError("场景清单里的 name 还是占位符；模板要求英文技能名，用 --name 指定或改清单")
    scoped = (graph.scope_to_units(units) if units else graph).without(removed_ids)

    options = BuildOptions(
        name=name,
        description=args.description or manifest_description,
        evidence_limit=args.evidence,
        data_mode=args.data,
        include_script=not args.no_script,
        include_lead=not args.no_lead,
        sources=sources,
        unit="、".join(dict.fromkeys(units)),
        include_example_specific=args.include_example_specific,
        keep_undecidable=args.keep_undecidable,
        max_steps=args.max_steps,
        excluded=removed,
    )
    package = build_package(scoped, named, options)
    result = lint_text(package.files["SKILL.md"])
    out_dir = Path(args.out)

    if args.dry_run:
        print(f"技能名：{normalise_name(name)}｜场景 {len(named)} 个")
        for index, (scenario_name, playbook) in enumerate(named):
            print(f"  场景{scenario_label(index)}：{scenario_name}（{len(playbook.causes)} 个原因）")
        print("将写出：")
        for relative in sorted(package.files):
            print(f"  {relative}  ({len(package.files[relative])} 字符)")
        _print_lint(result)
        return 0 if result.ok else 1

    written = package.write(out_dir, force=args.force)
    print(f"技能已生成：{out_dir}")
    print(f"  技能名：{normalise_name(name)}")
    stats = package.stats
    print(
        f"  场景 {len(named)} 个；公共前置检查 {stats.get('prechecks', 0)} 条；"
        f"排查步骤 {stats.get('steps', 0)} 步；根因 {stats.get('root_causes', 0)} 个；"
        f"文件 {len(written)} 个"
    )
    for index, (scenario_name, playbook) in enumerate(named):
        print(f"    场景{scenario_label(index)}：{scenario_name}")
    for note in package.notes:
        print(f"  提示：{note}")
    _print_lint(result)
    _print_metrics(package.doc)
    _install_hint(out_dir, normalise_name(name))
    return 0 if result.ok else 1


def cmd_build(args) -> int:
    graph, report, sources = _load_graph(args)
    if args.strict and report.errors:
        _print_report(report)
        print("\n--strict 模式下存在校验错误，已中止。", file=sys.stderr)
        return 1
    graph = _select(graph, args)
    if args.scenarios:
        return cmd_build_scenarios(args, graph, sources)
    entries = args.entry or [""]
    symptoms = [_resolve_entry(graph, entry) for entry in entries]
    if args.merge_same_name and len(symptoms) == 1:
        symptoms = _same_fault_symptoms(graph, symptoms[0])
    elif len(symptoms) == 1:
        others = _same_fault_symptoms(graph, symptoms[0])[1:]
        if others:
            print(
                f"提示：另有 {len(others)} 个同名症状（其他来源）描述同一故障，"
                "加 --merge-same-name 可合并成一份更完整的 skill："
            )
            for node in others[:5]:
                print(f"  {node.name}（{node.node_id}）")
    units = _resolve_units(graph, symptoms, args.unit, args.all_units)
    if not args.name:
        raise RenderError(
            "模板要求英文技能名，请用 --name 指定（`list` 会给出建议 slug，但请按语义改写）"
        )
    out_dir = Path(args.out)
    code = _build_one(graph, symptoms, args.name, out_dir, args, sources, units)
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

    skipped = 0
    if args.all_units:
        scenarios = [([symptom], []) for symptom in entry_symptoms(graph)]
    else:
        groups = fault_groups(graph, min_causes=args.min_causes, merge=not args.no_merge)
        skipped = len(entry_scenarios(graph, min_causes=0)) - sum(
            len(group.symptoms) for group in groups
        )
        scenarios = [(group.symptoms, group.units) for group in groups]
    if args.limit:
        scenarios = scenarios[: args.limit]
    if not scenarios:
        raise RenderError("子图里没有可生成的故障场景（symptom × 诊断单元）")

    root = Path(args.out)
    failures = 0
    unnamed: List[str] = []
    cache: Dict[str, Graph] = {}
    for symptoms, units in scenarios:
        symptom = symptoms[0]
        unit = units[0] if units else ""
        key = f"{symptom.node_id}@{unit}" if unit else symptom.node_id
        name = (
            names.get(fault_key(symptom.name))
            or names.get(key)
            or names.get(symptom.node_id)
            or names.get(symptom.name)
            or ""
        )
        if not name:
            name = suggested_slug(symptom, unit)
            unnamed.append(f"{key}  {symptom.name}  →  {name}")
        slug = normalise_name(name)
        code = _build_one(graph, symptoms, name, root / slug, args, sources, units, cache)
        failures += 1 if code else 0
        print()
    print(f"共生成 {len(scenarios)} 份 skill，{failures} 份未通过模板检查。")
    if skipped:
        print(
            f"另有 {skipped} 个场景候选原因少于 {args.min_causes} 个未生成"
            "（排查步骤会是空的；--min-causes 0 可强制生成）"
        )
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

    build = sub.add_parser("build", help="为一个故障入口生成 skill")
    _add_input_arguments(build)
    _add_selection_arguments(build)
    _add_output_arguments(build)
    build.add_argument(
        "--entry", action="append", default=[], help="入口症状：node_id、id 前缀或名称关键词，可重复（多个即合并）"
    )
    build.add_argument(
        "--unit", action="append", default=[], help="诊断单元（章节号/案例 ID），可重复"
    )
    build.add_argument(
        "--scenarios",
        default="",
        help="场景清单 JSON（list --export-scenarios 生成）：一份 skill 含公共前置检查 + 多个场景",
    )
    build.add_argument(
        "--merge-same-name",
        action="store_true",
        help="把其他来源里同名的症状一并合并进来（手册 + 作战树 + 案例库）",
    )
    build.add_argument(
        "--all-units", action="store_true", help="合并该症状的全部诊断单元（会混合多个故障场景）"
    )
    build.add_argument("--name", default="", help="技能名（英文 slug，模板硬性要求）")
    build.add_argument("--description", default="", help="frontmatter 描述（不给则由症状自动生成）")
    build.set_defaults(func=cmd_build)

    build_all = sub.add_parser("build-all", help="给每个故障入口各生成一份 skill")
    _add_input_arguments(build_all)
    _add_selection_arguments(build_all)
    _add_output_arguments(build_all)
    build_all.add_argument("--names", default="", help="{node_id: slug} 的 JSON 映射文件")
    build_all.add_argument("--limit", type=int, default=0, help="最多生成多少份（0=不限）")
    build_all.add_argument("--all-units", action="store_true", help="每个症状一份，不按诊断单元拆分")
    build_all.add_argument(
        "--no-merge", action="store_true", help="不按故障归并同名症状，一个场景一份"
    )
    build_all.add_argument("--min-causes", type=int, default=1, help="至少几个候选原因才生成")
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
    plan.add_argument("--max-steps", type=int, default=0, help="排查步骤上限（0=不限）")
    plan.add_argument(
        "--exclude", action="append", default=[], help="剔除无关节点：node_id 或名称关键词，可重复"
    )
    plan.set_defaults(func=cmd_plan)

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
