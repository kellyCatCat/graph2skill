"""Render a subgraph into a portable skill package.

Layout (identical for Claude Code, opencode and anything else that reads a
``SKILL.md``)::

    <skill>/
      SKILL.md                     entry document, kept short on purpose
      INSTALL.md                   where to drop the directory per framework
      references/index.md          symptom -> playbook routing table
      references/reading-guide.md  how to read the bundled data honestly
      references/coverage.md       what the build did and did not document
      references/playbooks/*.md    one troubleshooting document per symptom
      data/subgraph.json           the selected nodes and edges, verbatim
      scripts/kg_query.py          stdlib query tool over that JSON

Progressive disclosure is the point: ``SKILL.md`` routes, the playbooks carry
the detail, and the script answers anything the documents left out.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from subkg2skill import describe, schema
from subkg2skill.condition import describe_edge_condition
from subkg2skill.graph import Edge, Graph, Node, _text
from subkg2skill.playbook import CauseBranch, CheckStep, Link, Playbook, coverage

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_DESCRIPTION = 1024
SLIM_DROP_KEYS = (
    "canonical_key",
    "semantic_review",
    "normalizations",
    "extraction_edge_ids",
    "automatic_resolution",
    "concept_alignment_ids",
    "source_contexts",
)


class RenderError(RuntimeError):
    """Raised when the requested package cannot be produced."""


@dataclass
class BuildOptions:
    name: str = "kg-fault-diagnosis"
    title: str = ""
    description: str = ""
    evidence_limit: int = 3
    data_mode: str = "full"  # full | slim | none
    allowed_tools: str = ""
    include_script: bool = True
    sources: Sequence[str] = ()


@dataclass
class SkillPackage:
    files: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def write(self, out_dir: Path, *, force: bool = False) -> List[Path]:
        """Write every file under *out_dir*; refuse to clobber without ``force``."""
        out_dir = Path(out_dir)
        if out_dir.exists() and any(out_dir.iterdir()) and not force:
            what = "已有技能目录" if (out_dir / "SKILL.md").exists() else "非空目录"
            raise RenderError(f"{out_dir} 是{what}；确认后加 --force 覆盖")
        written: List[Path] = []
        for relative, content in sorted(self.files.items()):
            path = out_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
        self._prune_stale_playbooks(out_dir, written)
        return written

    def _prune_stale_playbooks(self, out_dir: Path, written: List[Path]) -> None:
        """Drop playbooks left over from an earlier build of a different subgraph."""
        playbooks = out_dir / "references" / "playbooks"
        if not playbooks.is_dir():
            return
        keep = {path.resolve() for path in written}
        removed = 0
        for path in playbooks.glob("*.md"):
            if path.resolve() not in keep:
                path.unlink()
                removed += 1
        if removed:
            self.notes.append(f"清理了 {removed} 份上一次构建遗留的手册。")


# ---------------------------------------------------------------- helpers
def normalise_name(name: str) -> str:
    """Coerce *name* into the ``^[a-z0-9-]+$`` form skill loaders expect."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise RenderError(f"技能名 {name!r} 无法转换为合法 slug（只允许小写字母、数字和连字符）")
    return slug[:64].strip("-")


def _ascii_hint(text: str, limit: int = 16) -> str:
    hint = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return hint[:limit].strip("-")


def playbook_filename(playbook: Playbook, taken: Dict[str, str]) -> str:
    """Stable, greppable filename: optional ASCII hint plus a node-id stem."""
    node_id = playbook.node_id
    stem = node_id if len(node_id) <= 28 else node_id[:28]
    hint = _ascii_hint(playbook.symptom.name)
    base = f"{hint}-{stem}" if hint else stem
    candidate = f"{base}.md"
    suffix = 2
    while candidate in taken and taken[candidate] != node_id:
        candidate = f"{base}-{suffix}.md"
        suffix += 1
    taken[candidate] = node_id
    return candidate


def _yaml_string(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = re.sub(r"\s+", " ", escaped).strip()
    return f'"{escaped}"'


def _section(title: str, lines: Sequence[str]) -> List[str]:
    if not lines:
        return []
    return [title, ""] + list(lines) + [""]


def _indent(lines: Iterable[str], prefix: str) -> List[str]:
    return [f"{prefix}{line}" if line else "" for line in lines]


def _edge_note(edge: Optional[Edge]) -> List[str]:
    """Condition, ordering and provenance caveats attached to one relation."""
    if edge is None:
        return []
    notes: List[str] = []
    condition = describe_edge_condition(edge)
    if condition:
        notes.append(condition)
    if edge.rank is not None:
        notes.append(f"来源顺序号 rank={edge.rank}（仅排序提示，不是概率或置信度）")
    if edge.review_status and edge.review_status != "machine_checked":
        notes.append(f"关系审核状态：{edge.review_status}")
    for flag in edge.quality_flags:
        notes.append(f"关系标记 `{flag}` — {schema.quality_flag_note(flag)}")
    return notes


def _node_caveats(node: Node) -> List[str]:
    notes: List[str] = []
    if node.example_specific:
        notes.append("**案例特定（example_specific=true）**：勿直接套用到其他设备或场景。")
    for flag in node.quality_flags:
        notes.append(f"标记 `{flag}` — {schema.quality_flag_note(flag)}")
    return notes


# ------------------------------------------------------------- playbooks
def render_playbook(graph: Graph, playbook: Playbook, options: BuildOptions) -> str:
    symptom = playbook.symptom
    lines: List[str] = [f"# {symptom.name}", ""]
    lines.append(f"- 节点：`{symptom.node_id}`（{schema.NODE_LABELS['symptom']}）")
    triggers = playbook.trigger_terms()
    if triggers:
        lines.append("- 触发说法：" + " / ".join(triggers))
    lines.append("- 适用范围：" + describe.scope_line(symptom.scope))
    context = describe.context_line(symptom.diagnostic_contexts)
    if context:
        lines.append("- 诊断单元：" + context)
    lines.append("- 知识状态：" + describe.trust_line(symptom))
    if symptom.description:
        lines.append("- 描述：" + symptom.description)
    lines.append("")

    detail = describe.symptom_block(symptom)
    if detail:
        lines += ["## 0. 现象与需补齐的信息", ""] + detail + [""]
    caveats = _node_caveats(symptom)
    if caveats:
        lines += ["> 注意：", ""] + [f"> - {note}" for note in caveats] + [""]

    if playbook.entry_checks:
        lines += ["## 1. 入口检查（不区分具体原因）", ""]
        for index, step in enumerate(playbook.entry_checks, start=1):
            lines += _render_check(step, f"1.{index}", options)
    if playbook.entry_actions:
        lines += ["## 1x. 症状直接给出的后续动作", ""]
        for link in playbook.entry_actions:
            lines += _render_link_action(link, options)

    if playbook.causes:
        lines += ["## 2. 候选原因对照表", ""]
        lines += _render_cause_table(playbook)
        lines += [""]
        for index, branch in enumerate(playbook.causes, start=1):
            lines += _render_cause(branch, f"2.{index}", options)
    else:
        lines += [
            "## 2. 候选原因",
            "",
            "该症状在本子图中没有 `has_cause` 关系；只能按上面的检查动作收集信息，"
            "或按下面的转向条目进入其他入口。",
            "",
        ]

    if playbook.referrals:
        lines += ["## 3. 转向其他入口 / 升级处理", ""]
        for link in playbook.referrals:
            lines += _render_link_action(link, options)

    evidence = describe.evidence_lines(symptom.provenance, options.evidence_limit)
    if evidence:
        lines += ["## 4. 该症状的来源证据", ""] + [f"- {line}" for line in evidence] + [""]

    lines += [
        "---",
        "",
        "回查更多字段：`python3 scripts/kg_query.py show <node_id>`；"
        "查看邻接关系：`python3 scripts/kg_query.py neighbors <node_id>`。",
        "",
    ]
    return "\n".join(lines)


def _render_cause_table(playbook: Playbook) -> List[str]:
    header = [
        "| # | 可能原因 | 分类 | 确认观测 | 排除观测 | 修复动作 | 附加条件 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    rows: List[str] = []
    for index, branch in enumerate(playbook.causes, start=1):
        confirms = branch.verdicts_of("confirms")
        excludes = branch.verdicts_of("excludes")
        supports = branch.verdicts_of("supports")
        confirm_cell = "；".join(v.observation.name for v in confirms[:2]) or (
            "（仅支持性证据：" + "；".join(v.observation.name for v in supports[:2]) + "）"
            if supports
            else "—"
        )
        exclude_cell = "；".join(v.observation.name for v in excludes[:2]) or "—"
        repair_cell = "；".join(link.node.name for link in branch.repairs[:2]) or "—"
        condition = describe_edge_condition(branch.edge)
        condition_cell = "有（见分支说明）" if condition else "无"
        marks = " ⚠案例特定" if branch.cause.example_specific else ""
        rows.append(
            f"| {index} | {branch.cause.name}{marks} | {_text(branch.cause.attr('cause_kind')) or '—'} "
            f"| {confirm_cell} | {exclude_cell} | {repair_cell} | {condition_cell} |"
        )
    return header + rows


def _render_check(
    step: CheckStep, number: str, options: BuildOptions, *, level: int = 3
) -> List[str]:
    check = step.check
    heading = "#" * level
    lines = [f"{heading} {number} 检查：{check.name}", "", f"- 节点：`{check.node_id}`"]
    if step.edge is not None:
        lines.append(
            f"- 到达方式：{schema.edge_label(step.edge.edge_type)}"
            f"（`{step.edge.edge_type}`，来自 `{step.edge.source}`）"
        )
    lines += [f"- {note}" for note in _edge_note(step.edge)]
    if step.repeated:
        lines += ["- 该检查已在本手册前文展开，此处不重复。", ""]
        return lines
    lines += describe.check_block(check)
    lines += [f"- {note}" for note in _node_caveats(check)]
    if check.provenance:
        lines.append("- 出处：" + describe.evidence_lines(check.provenance, 1)[0])
    lines.append("")

    if step.outcomes:
        lines += ["**可能的观测结果与判读：**", ""]
        for outcome in step.outcomes:
            observation = outcome.observation
            lines.append(
                f"- **{describe.observation_expression(observation)}**（`{observation.node_id}`）"
            )
            for note in describe.observation_details(observation):
                lines.append(f"  - {note}")
            for note in _edge_note(outcome.edge):
                lines.append(f"  - {note}")
            for verdict in outcome.verdicts:
                verdict_notes = _edge_note(verdict.edge)
                tail = ("；" + "；".join(verdict_notes)) if verdict_notes else ""
                lines.append(
                    f"  - → **{verdict.label}** 原因「{verdict.cause.name}」"
                    f"（`{verdict.edge.edge_type}` → `{verdict.cause.node_id}`）{tail}"
                )
            for link in outcome.next_steps:
                lines.append(
                    f"  - → 下一步：{describe.node_headline(link.node)}"
                    + (f"；{describe_edge_condition(link.edge)}" if describe_edge_condition(link.edge) else "")
                )
            for note in _node_caveats(observation):
                lines.append(f"  - {note}")
        lines.append("")
    else:
        lines += ["本子图未给出该检查的观测结果模板，按现场实际结果判断并记录。", ""]

    non_check_steps = [link for link in step.next_steps if link.node.node_type != "check"]
    if non_check_steps:
        lines += ["**检查之后的动作：**", ""]
        for link in non_check_steps:
            lines += _render_link_action(link, options)
    return lines


def _render_link_action(link: Link, options: BuildOptions) -> List[str]:
    node = link.node
    lines = [f"- {describe.node_headline(node)}"]
    condition = describe_edge_condition(link.edge)
    if condition:
        lines.append(f"  - {condition}")
    body = describe.node_block(node)
    lines += _indent(body, "  ")
    for note in _node_caveats(node):
        lines.append(f"  - {note}")
    if node.provenance:
        lines.append("  - 出处：" + describe.evidence_lines(node.provenance, 1)[0])
    lines.append("")
    return lines


def _render_cause(branch: CauseBranch, number: str, options: BuildOptions) -> List[str]:
    cause = branch.cause
    lines = [f"### {number} 原因：{cause.name}", "", f"- 节点：`{cause.node_id}`"]
    lines += describe.cause_block(cause)
    lines.append("- 适用范围：" + describe.scope_line(cause.scope))
    lines.append("- 知识状态：" + describe.trust_line(cause))
    relation_notes = _edge_note(branch.edge)
    if relation_notes:
        lines.append("- 关系（症状→原因）：")
        lines += [f"  - {note}" for note in relation_notes]
    for note in _node_caveats(cause):
        lines.append(f"- {note}")
    lines.append("")

    if branch.verdicts:
        lines += ["**判据（观测 → 结论）：**", ""]
        for verdict in branch.verdicts:
            notes = _edge_note(verdict.edge)
            tail = ("；" + "；".join(notes)) if notes else ""
            lines.append(
                f"- **{verdict.label}**：{describe.observation_expression(verdict.observation)} "
                f"（`{verdict.observation.node_id}`）{tail}"
            )
        lines.append("")
        if not branch.verdicts_of("confirms"):
            lines += [
                "> 本原因只有支持性/排除性证据，没有 `confirms` 关系；不要据此宣布根因已确认。",
                "",
            ]

    if branch.checks:
        lines += ["**定位检查：**", ""]
        for index, step in enumerate(branch.checks, start=1):
            lines += _render_check(step, f"{number}.{index}", options, level=4)

    if branch.repairs:
        lines += ["**修复动作：**", ""]
        for link in branch.repairs:
            lines += _render_link_action(link, options)

    related = [
        ("细化为", branch.refines),
        ("可能导致", branch.leads_to),
        ("转向", branch.refers_to),
    ]
    related_lines: List[str] = []
    for label, links in related:
        for link in links:
            condition = describe_edge_condition(link.edge)
            related_lines.append(
                f"- {label}：{describe.node_headline(link.node)}"
                + (f"；{condition}" if condition else "")
            )
    if related_lines:
        lines += ["**相关条目：**", ""] + related_lines + [""]

    evidence = describe.evidence_lines(cause.provenance, options.evidence_limit)
    if evidence:
        lines += ["**出处：**", ""] + [f"- {line}" for line in evidence] + [""]
    return lines


# ------------------------------------------------------------ index page
#: Above this many symptoms the index is split so no single file has to be read whole.
INDEX_SHARD_LIMIT = 150
#: Above this many diagnostic units the directory stops being a directory; chunk instead.
MAX_INDEX_GROUPS = 40


def _index_row(playbook: Playbook, filename: str, prefix: str) -> str:
    triggers = " / ".join(playbook.trigger_terms()[1:4]) or "—"
    context = describe.context_line(playbook.symptom.diagnostic_contexts) or "—"
    if len(context) > 40:
        context = context[:40] + "…"
    return (
        f"| {playbook.symptom.name} | {triggers} | {context} | {len(playbook.causes)} "
        f"| {playbook.check_count} | {playbook.repair_count} | [{filename}]({prefix}{filename}) |"
    )


INDEX_TABLE_HEAD = [
    "| 症状 | 触发说法 | 诊断单元 | 候选原因 | 检查 | 修复 | 手册 |",
    "| --- | --- | --- | --- | --- | --- | --- |",
]

INDEX_INTRO = [
    "按用户描述的现象在“触发说法”列里匹配，然后只打开对应的那一份手册。",
    "匹配不到时用脚本检索：`python3 scripts/kg_query.py search \"关键词\"`。",
]


def _index_group(playbook: Playbook) -> str:
    """Group symptoms by the top level of their diagnostic unit."""
    sections = playbook.symptom.sections
    if not sections:
        return "未标注诊断单元"
    head = sections[0].split(":")[0].split(".")[0].strip()
    return head or "未标注诊断单元"


def render_index(playbooks: Sequence[Playbook], filenames: Dict[str, str]) -> Dict[str, str]:
    """Return ``{relative path: content}`` — one table, or a directory plus shards."""
    if len(playbooks) <= INDEX_SHARD_LIMIT:
        lines = ["# 症状索引（排查手册路由表）", ""] + INDEX_INTRO + [""] + INDEX_TABLE_HEAD
        lines += [
            _index_row(playbook, filenames[playbook.node_id], "playbooks/")
            for playbook in playbooks
        ]
        return {"references/index.md": "\n".join(lines + [""])}

    grouped: Dict[str, List[Playbook]] = {}
    for playbook in playbooks:
        grouped.setdefault(_index_group(playbook), []).append(playbook)

    # (label, shard path, members) — ordered as the directory should read.
    shards: List[Tuple[str, str, List[Playbook]]] = []
    if len(grouped) <= MAX_INDEX_GROUPS:
        for ordinal, group in enumerate(sorted(grouped), start=1):
            shard = f"index/{ordinal:03d}-{_ascii_hint(group) or 'unit'}.md"
            shards.append((group, shard, grouped[group]))
    else:
        # Too many diagnostic units to make a useful directory — chunk instead.
        for start in range(0, len(playbooks), INDEX_SHARD_LIMIT):
            chunk = list(playbooks[start : start + INDEX_SHARD_LIMIT])
            ordinal = start // INDEX_SHARD_LIMIT + 1
            label = f"分片 {ordinal}（{chunk[0].symptom.name} … {chunk[-1].symptom.name}）"
            shards.append((label, f"index/part-{ordinal:03d}.md", chunk))

    files: Dict[str, str] = {}
    directory = [
        "# 症状索引（分片路由表）",
        "",
        f"共 {len(playbooks)} 个症状入口，按诊断单元分片。**先按关键词检索，再打开对应分片**：",
        "",
        "```bash",
        'python3 scripts/kg_query.py search "关键词" --type symptom',
        "```",
        "",
        "检索命中后，用返回的 `node_id` 在分片里找到对应手册文件。",
        "",
        "| 诊断单元 | 症状数 | 示例症状 | 分片 |",
        "| --- | --- | --- | --- |",
    ]
    for group, shard, members in shards:
        examples = "；".join(playbook.symptom.name for playbook in members[:3])
        if len(members) > 3:
            examples += " …"
        directory.append(f"| {group} | {len(members)} | {examples} | [{shard}]({shard}) |")
        shard_lines = [f"# 症状索引 · {group}", ""] + INDEX_INTRO + [""] + INDEX_TABLE_HEAD
        shard_lines += [
            _index_row(playbook, filenames[playbook.node_id], "../playbooks/")
            for playbook in members
        ]
        files[f"references/{shard}"] = "\n".join(shard_lines + [""])
    files["references/index.md"] = "\n".join(directory + [""])
    return files


def render_reading_guide(graph: Graph) -> str:
    lines = [
        "# 数据读法（本技能自带子图）",
        "",
        "手册里的每一条判断都来自下面这套节点/边语义。改写或外推之前先看这一页。",
        "",
        "## 节点角色",
        "",
        "| node_type | 含义 | 本子图数量 |",
        "| --- | --- | --- |",
    ]
    counts = graph.node_counts()
    for node_type in schema.NODE_TYPES:
        if node_type in counts:
            lines.append(
                f"| `{node_type}` | {schema.NODE_LABELS[node_type]} | {counts[node_type]} |"
            )
    lines += ["", "## 关系语义与方向", "", "| edge_type | 含义 | 本子图数量 |", "| --- | --- | --- |"]
    edge_counts = graph.edge_counts()
    for edge_type in schema.EDGE_TYPES:
        if edge_type in edge_counts:
            lines.append(
                f"| `{edge_type}` | {schema.edge_meaning(edge_type)} | {edge_counts[edge_type]} |"
            )
    lines += [
        "",
        "## 判据强弱",
        "",
        "- `confirms` 强于 `supports`：前者在**给定条件与范围内**被表述为确认该原因，后者只是支持证据。",
        "- `excludes` 只排除**指定的那个原因**，不能外推成排除全部原因。",
        "- `has_cause` 是“症状指向可能原因”，不是原因传播；原因传播用 `leads_to`。",
        "- `refines` 是细化描述，`refers_to` 是转向其他入口，都不是因果推断。",
        "- `next_step` 是流程上的下一步，可能受 condition 限制。",
        "",
        "## 条件（condition）",
        "",
        "| condition_status | 含义 |",
        "| --- | --- |",
    ]
    for status, label in schema.CONDITION_STATUS_LABELS.items():
        lines.append(f"| `{status}` | {label} |")
    lines += [
        "",
        "手册里所有条件都标了“未求值”：它们是**待判断的分支条件**，需要绑定现场对象、字段和采样数据后再判断。",
        "",
        "## 操作符",
        "",
        "| 操作符 | 含义 |",
        "| --- | --- |",
    ]
    for operator, label in schema.OPERATOR_LABELS.items():
        lines.append(f"| `{operator}` | {label} |")
    lines += [
        "",
        "## 质量标记",
        "",
        "本子图出现的标记及含义：",
        "",
    ]
    flags = graph.quality_flag_counts()
    if flags:
        lines += ["| flag | 含义 | 次数 |", "| --- | --- | --- |"]
        for flag, count in flags.items():
            lines.append(f"| `{flag}` | {schema.quality_flag_note(flag)} | {count} |")
    else:
        lines.append("（本子图未出现质量标记。）")
    lines += [
        "",
        "## 来源定位",
        "",
        "证据条目按 `document` + `page`/`printed_page` + `section` + `block_id` + `quote` 定位；",
        "Excel 来源用 `sheet`/`cell`，案例来源用 `case_uuid`/`json_path`。",
        "`image_ocr` 只说明有 OCR 文字依据，并不证明流程图箭头方向。",
        "引文可定位只说明文字依据可查，不自动证明关系方向或诊断结论正确。",
        "",
    ]
    return "\n".join(lines)


def render_coverage(graph: Graph, playbooks: Sequence[Playbook], report, options: BuildOptions) -> str:
    stats = coverage(graph, playbooks)
    lines = [
        "# 构建报告与覆盖情况",
        "",
        f"- 生成时间（UTC）：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 输入：{'、'.join(options.sources) or '(未记录)'}",
        f"- 节点：{len(graph)}；关系：{len(graph.edges)}",
        f"- 排查手册：{len(playbooks)} 份",
        f"- 被手册覆盖的节点：{len(stats['documented'])}；未覆盖：{len(stats['uncovered'])}",
        "",
        "## 节点与关系分布",
        "",
    ]
    lines += [f"- {k}：{v}" for k, v in graph.node_counts().items()]
    lines += [f"- {k}：{v}" for k, v in graph.edge_counts().items()]
    lines += ["", "## 适用范围分布", ""]
    lines += [f"- 厂商 {k}：{v}" for k, v in graph.vendors().items()]

    issues = report.counts() if report is not None else {}
    lines += ["", "## 载入时丢弃/告警", ""]
    if issues:
        lines += [f"- {kind}：{count}" for kind, count in issues.items()]
        lines.append("")
        lines.append("前 20 条明细：")
        lines += [f"- {issue.render()}" for issue in report.issues[:20]]
    else:
        lines.append("- 无：所有节点与关系都通过了结构校验。")

    uncovered = stats["uncovered"]
    lines += ["", "## 未进入手册的节点", ""]
    if uncovered:
        lines.append(
            "这些节点仍在 `data/subgraph.json` 中，可用 `scripts/kg_query.py` 查询；"
            "它们没有从任何症状出发被走到（通常是孤立节点或只被反向引用）。"
        )
        lines.append("")
        for node in uncovered[:50]:
            lines.append(f"- {describe.node_headline(node)}")
        if len(uncovered) > 50:
            lines.append(f"- （另有 {len(uncovered) - 50} 个未列出）")
    else:
        lines.append("- 无：所有节点都被至少一份手册引用。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- SKILL.md
def default_description(graph: Graph, playbooks: Sequence[Playbook], options: BuildOptions) -> str:
    counts = graph.node_counts()
    vendors = [v for v in graph.vendors() if v != "(未标注)"][:2]
    top = [playbook.symptom.name for playbook in playbooks[:6]]
    vendor_part = "、".join(vendors) or "多厂商"
    parts = [
        f"基于 {vendor_part} 承载网/路由器故障诊断知识图谱子图（"
        f"{counts.get('symptom', 0)} 症状 / {counts.get('cause', 0)} 原因 / "
        f"{counts.get('check', 0)} 检查 / {counts.get('observation', 0)} 观测 / "
        f"{counts.get('repair', 0)} 修复）给出可回溯的排查路径：检查动作 → 观测判据 → "
        f"候选原因 → 修复或升级，并附文档页码与原文引文。",
        "适用场景：用户报告网络设备/承载网故障现象（如 " + "、".join(top) + " 等），",
        "或需要按知识库确认根因、给出检查命令、查证结论出处时使用。",
        "Use for IP-RAN / carrier-network fault triage grounded in a curated knowledge graph.",
    ]
    text = " ".join(parts)
    return text[: MAX_DESCRIPTION - 1]


def render_skill_md(graph: Graph, playbooks: Sequence[Playbook], options: BuildOptions) -> str:
    name = normalise_name(options.name)
    description = options.description or default_description(graph, playbooks, options)
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1]
    title = options.title or "IP-RAN 故障诊断（知识图谱子图）"
    counts = graph.node_counts()

    front = ["---", f"name: {name}", f"description: {_yaml_string(description)}"]
    if options.allowed_tools:
        front.append(f"allowed-tools: {options.allowed_tools}")
    front += ["---", ""]

    top_triggers: List[str] = []
    for playbook in playbooks[:8]:
        top_triggers.extend(playbook.trigger_terms()[:2])
    top_triggers = list(dict.fromkeys(top_triggers))[:12]

    body = [
        f"# {title}",
        "",
        f"内置一份故障诊断知识图谱子图（节点 {len(graph)}、关系 {len(graph.edges)}："
        + "，".join(f"{schema.NODE_LABELS[k]} {v}" for k, v in counts.items())
        + f"），并展开成 {len(playbooks)} 份按症状组织的排查手册。",
        "",
        "**所有内容都是从产品文档与案例中抽取的候选知识（`status=candidate`），"
        "机器复核不等于人工确认。手册给的是待验证的排查路径，不是已确认的结论。**",
        "",
        "## 何时使用",
        "",
        "- 用户描述了承载网/路由器侧的故障现象，需要一条可执行、可回溯的排查路径；",
        "- 需要「检查动作 → 观测判据 → 候选原因 → 修复/升级」的结构化排障建议；",
        "- 需要为某条结论找到文档出处（文档名、页码、章节、原文引文）。",
    ]
    if top_triggers:
        body += ["", "覆盖的典型现象：" + "、".join(top_triggers) + " 等。"]
    body += [
        "",
        "## 使用流程",
        "",
        "1. **定位入口**：读 `references/index.md`（症状 → 手册路由表），按“触发说法”匹配用户描述。",
        "   匹配不到就检索：`python3 scripts/kg_query.py search \"关键词\"`。",
        "2. **只打开需要的那份手册**：`references/playbooks/<文件>.md`。不要一次读入全部手册。",
        "3. **先补齐信息**：手册第 0 节列出必须向用户确认的槽位（设备名、端口名、版本等）和适用范围；"
        "缺失就先问，不要替用户假设。",
        "4. **按证据推进**：对每个候选原因，执行“定位检查”，把现场结果对照“可能的观测结果与判读”，"
        "再根据 `confirms` / `supports` / `excludes` 收敛。",
        "5. **给出动作**：只使用手册中该原因下列出的修复动作；命令模板必须先绑定现场参数，"
        "涉及配置变更或业务影响的操作先向用户确认。",
        "6. **回查原文**：`python3 scripts/kg_query.py show <node_id>` 看完整字段与来源；"
        "`neighbors` / `expand` 看邻接关系；全量数据在 `data/subgraph.json`。",
        "",
        "## 证据纪律（必须遵守）",
        "",
        "1. **候选知识**：全部节点/关系都是 `candidate`；`machine_checked` 只表示机器复核，"
        "`human_reviewed=false` 表示没有人工确认。表述时说“知识库提示”，不要说“已确认”。",
        "2. **条件未求值**：手册里的 `condition` 是待判断的分支条件，未在现场求值。"
        "先绑定对象、字段和采样数据，再判断成立与否；判断不了就明确说“未验证”。",
        "3. **核对适用范围**：`scope`（厂商/产品/版本/组件/场景）不匹配现场就不要套用；"
        "`software_version=null` 表示未明确，不表示适用所有版本。",
        "4. **案例特定内容**：标了 `example_specific=true` 的条目带案例参数，不能推广到其他设备。",
        "5. **命令是模板**：`command_templates` 含待绑定参数或案例常量，"
        "`non_executable_knowledge` / `execution_policy` 标记的更需要人工解释；不要直接下发。",
        "6. **判据强弱**：`confirms` 也只在给定条件与范围内成立；`excludes` 只排除它指向的那个原因；"
        "只有 `supports` 时不要宣布根因。",
        "7. **空值不是承诺**：`service_impact` / `rollback` / `preconditions` 为空只表示来源没写，"
        "不表示无影响、无需回退、无前置条件。",
        "8. **引用可回溯**：给结论时附 `node_id` 和来源（文档/页码/章节/引文）；无法回查就直说。",
        "",
        "## 输出格式建议",
        "",
        "```",
        "现象：<用户描述> → 匹配症状：<名称>（node_id）",
        "已确认信息：<设备/端口/版本/时间…>；仍缺：<槽位>",
        "排查步骤：",
        "  1) <检查名>：<绑定参数后的命令>  预期判读：<观测→结论>",
        "候选原因（按当前证据）：",
        "  - <原因>：<支持/排除的观测>，证据强度 confirms|supports|尚无",
        "建议动作：<修复动作>（前置条件 / 业务影响 / 回退）",
        "出处：<文档 页码 §章节 “引文”>（node_id）",
        "未验证项：<未求值条件、未核对范围>",
        "```",
        "",
        "## 目录",
        "",
        "| 文件 | 用途 |",
        "| --- | --- |",
        "| `references/index.md` | 症状 → 手册路由表，先读这个 |",
        "| `references/playbooks/*.md` | 每个症状一份排查手册 |",
        "| `references/reading-guide.md` | 节点/关系语义、条件与操作符、质量标记的读法 |",
        "| `references/coverage.md` | 构建报告：数据分布、丢弃项、未覆盖节点 |",
        "| `data/subgraph.json` | 子图原始数据（节点/关系全字段） |",
        "| `scripts/kg_query.py` | 零依赖查询脚本（search / show / neighbors / expand / path / stats） |",
        "",
    ]
    return "\n".join(front + body)


def render_install(options: BuildOptions, name: str) -> str:
    return "\n".join(
        [
            f"# 安装 `{name}`",
            "",
            "这是一份**框架无关**的技能目录：一个带 YAML frontmatter 的 `SKILL.md`，"
            "加上按需加载的 `references/`、`data/`、`scripts/`。把整个目录复制到对应位置即可。",
            "",
            "## Claude Code",
            "",
            "```bash",
            f"cp -r {name} .claude/skills/{name}          # 仅当前项目",
            f"cp -r {name} ~/.claude/skills/{name}        # 全局可用",
            "```",
            "",
            "重启（或新开）会话后，`/skills` 或自动触发都能看到它。",
            "",
            "## opencode",
            "",
            "```bash",
            f"cp -r {name} .opencode/skill/{name}             # 仅当前项目",
            f"cp -r {name} ~/.config/opencode/skill/{name}    # 全局可用",
            "```",
            "",
            "## 其他框架 / 直接使用",
            "",
            "任何能读 Markdown 的智能体都可以用：把 `SKILL.md` 作为系统提示的一部分，",
            "并允许模型按需读取 `references/` 下的文件。`scripts/kg_query.py` 只依赖 Python 3.9+ 标准库：",
            "",
            "```bash",
            f"python3 {name}/scripts/kg_query.py stats",
            f"python3 {name}/scripts/kg_query.py search \"邻居震荡\"",
            f"python3 {name}/scripts/kg_query.py show <node_id>",
            f"python3 {name}/scripts/kg_query.py expand <node_id> --depth 2",
            "```",
            "",
            "## 更新",
            "",
            "重新运行 `subkg2skill build --out <目录> --force` 覆盖即可；",
            "文件名按 `node_id` 生成，同一子图重复构建结果稳定。",
            "",
        ]
    )


# ------------------------------------------------------------------ data
def _slim(record: Dict[str, Any], *, evidence_keys: Sequence[str], limit: int = 2) -> Dict[str, Any]:
    trimmed = {k: v for k, v in record.items() if k not in SLIM_DROP_KEYS}
    for key in evidence_keys:
        value = trimmed.get(key)
        if isinstance(value, list) and len(value) > limit:
            trimmed[key] = value[:limit]
    return trimmed


def render_data(graph: Graph, options: BuildOptions) -> str:
    nodes = [node.raw for node in graph.iter_nodes()]
    edges = [edge.raw for edge in graph.edges]
    if options.data_mode == "slim":
        nodes = [_slim(raw, evidence_keys=("provenance",)) for raw in nodes]
        edges = [_slim(raw, evidence_keys=("evidence",)) for raw in edges]
    payload = {
        "meta": {
            "generator": "subkg2skill",
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "sources": list(options.sources),
            "data_mode": options.data_mode,
            "node_counts": graph.node_counts(),
            "edge_counts": graph.edge_counts(),
            "notice": "候选知识（status=candidate）；condition 未在现场求值；引用请回查 provenance/evidence。",
        },
        "nodes": nodes,
        "edges": edges,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _query_script() -> str:
    return (Path(__file__).parent / "resources" / "kg_query.py").read_text(encoding="utf-8")


# ----------------------------------------------------------------- build
def build_package(graph: Graph, playbooks: Sequence[Playbook], report, options: BuildOptions) -> SkillPackage:
    """Render every file of the skill package into memory."""
    name = normalise_name(options.name)
    package = SkillPackage()
    filenames: Dict[str, str] = {}
    taken: Dict[str, str] = {}
    for playbook in playbooks:
        filename = playbook_filename(playbook, taken)
        filenames[playbook.node_id] = filename
        package.files[f"references/playbooks/{filename}"] = render_playbook(
            graph, playbook, options
        )
    package.files["SKILL.md"] = render_skill_md(graph, playbooks, options)
    package.files["INSTALL.md"] = render_install(options, name)
    package.files.update(render_index(playbooks, filenames))
    package.files["references/reading-guide.md"] = render_reading_guide(graph)
    package.files["references/coverage.md"] = render_coverage(graph, playbooks, report, options)
    if options.data_mode != "none":
        package.files["data/subgraph.json"] = render_data(graph, options)
    elif options.include_script:
        package.notes.append("--data none 时不生成 data/subgraph.json，查询脚本将无数据可读。")
    if options.include_script:
        package.files["scripts/kg_query.py"] = _query_script()
    if not playbooks:
        package.notes.append("子图中没有 symptom 节点，未生成任何排查手册。")
    return package
