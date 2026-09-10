"""Assemble the skill package around one fault entry.

    <skill>/
      SKILL.md                 the four-section template document
      reference/evidence.md    where every claim came from, and how far it is verified
      reference/subgraph.json  this fault's slice of the graph, verbatim
      scripts/kg_query.py      stdlib query tool over that slice

``SKILL.md`` is written by :mod:`subkg2skill.template`; this module supplies the
frontmatter, the supporting files and the on-disk layout.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Set

from subkg2skill import describe, schema
from subkg2skill.condition import describe_edge_condition
from subkg2skill.graph import Graph, Node, _text
from subkg2skill.playbook import Playbook
from subkg2skill.template import build_doc, render_doc

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
LEAD = "> 出处与证据强度见 `reference/evidence.md`；子图原始数据见 `reference/subgraph.json`（可用 `scripts/kg_query.py` 查询）。"


class RenderError(RuntimeError):
    """Raised when the requested package cannot be produced."""


@dataclass
class BuildOptions:
    name: str = ""
    description: str = ""
    evidence_limit: int = 3
    data_mode: str = "full"  # full | slim | none
    include_script: bool = True
    include_lead: bool = True
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
        return written


def normalise_name(name: str) -> str:
    """Coerce *name* into the ``^[a-z0-9-]+$`` slug the template requires."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise RenderError(
            f"技能名 {name!r} 无法转换为合法 slug；模板要求英文名（^[a-z0-9-]+$），请给一个英文 slug"
        )
    return slug[:64].strip("-")


def suggested_slug(symptom: Node) -> str:
    """A valid fallback slug — ASCII fragments of the name plus the id tail.

    It is deliberately not a translation: Chinese symptom names cannot be turned
    into meaningful English mechanically, so the caller is expected to supply a
    real name and this only keeps the build unblocked.
    """
    hint = re.sub(r"[^a-z0-9]+", "-", symptom.name.lower()).strip("-")
    tail = symptom.node_id.split("_", 1)[-1][:8]
    return normalise_name(f"{hint}-{tail}" if hint else f"fault-{tail}")


def default_description(symptom: Node) -> str:
    """``故障现象 + 适用时机``, as the template's example does it."""
    phenomenon = symptom.name
    abnormal = _text(symptom.attr("abnormal_behavior"))
    if abnormal:
        phenomenon = f"{symptom.name}：{abnormal}"
    triggers: List[str] = []
    for term in symptom.aliases + symptom.match_phrases:
        term = term.strip()
        if term and term != symptom.name and term not in triggers:
            triggers.append(term)
    when = "、".join(triggers[:4])
    trigger_context = _text(symptom.attr("trigger_context"))
    tail = f"（常见于{trigger_context}）" if trigger_context else ""
    if when:
        description = f"{phenomenon}。出现 {when} 等现象或告警时使用{tail}。"
    else:
        description = f"{phenomenon}。出现该现象或相关告警时使用{tail}。"
    return description[: MAX_DESCRIPTION - 1]


# ---------------------------------------------------------------- evidence
def _node_evidence_block(node: Node, limit: int) -> List[str]:
    lines = [f"### {node.name}（`{node.node_id}`）", ""]
    lines.append(f"- 知识状态：{describe.trust_line(node)}")
    lines.append(f"- 适用范围：{describe.scope_line(node.scope)}")
    context = describe.context_line(node.diagnostic_contexts)
    if context:
        lines.append(f"- 诊断单元：{context}")
    body = describe.node_block(node)
    lines += body
    for flag in node.quality_flags:
        lines.append(f"- 质量标记 `{flag}` — {schema.quality_flag_note(flag)}")
    evidence = describe.evidence_lines(node.provenance, limit)
    if evidence:
        lines.append("- 出处：")
        lines += [f"  - {line}" for line in evidence]
    else:
        lines.append("- 出处：来源未记录")
    lines.append("")
    return lines


def render_evidence(graph: Graph, playbook: Playbook, options: BuildOptions) -> str:
    """Everything the four sections deliberately leave out: sources and caveats."""
    symptom = playbook.symptom
    lines = [
        "# 证据与出处",
        "",
        "本 skill 由故障诊断知识图谱子图展开而成。**全部内容都是候选知识**"
        "（`status=candidate`），`machine_checked` 只表示机器复核，不等于人工确认；",
        "边上的 `condition` 均未在现场求值。给用户结论时请附上这里的 `node_id` 与来源。",
        "",
        f"- 生成时间（UTC）：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 输入：{'、'.join(options.sources) or '(未记录)'}",
        f"- 本切片：节点 {len(graph)}、关系 {len(graph.edges)}",
        "",
        "## 症状",
        "",
    ]
    lines += _node_evidence_block(symptom, options.evidence_limit)

    checks: List[Node] = []
    seen: Set[str] = set()
    for step in playbook.entry_checks + [s for b in playbook.causes for s in b.checks]:
        if step.check.node_id not in seen:
            seen.add(step.check.node_id)
            checks.append(step.check)
    if checks:
        lines += ["## 检查动作", ""]
        for check in checks:
            lines += _node_evidence_block(check, options.evidence_limit)

    verdict_lines: List[str] = []
    for branch in playbook.causes:
        for verdict in branch.verdicts:
            condition = describe_edge_condition(verdict.edge)
            verdict_lines.append(
                f"- **{verdict.label}**：{describe.observation_expression(verdict.observation)} "
                f"（`{verdict.observation.node_id}`）→ 「{verdict.cause.name}」"
                f"（`{verdict.edge.edge_type}`）" + (f"；{condition}" if condition else "")
            )
    if verdict_lines:
        lines += ["## 判据（观测 → 原因）", ""] + verdict_lines + [""]

    if playbook.causes:
        lines += ["## 候选原因", ""]
        for branch in playbook.causes:
            lines += _node_evidence_block(branch.cause, options.evidence_limit)
            condition = describe_edge_condition(branch.edge)
            if condition:
                lines += [f"- 症状→原因关系：{condition}", ""]

    repairs: List[Node] = []
    seen = set()
    for branch in playbook.causes:
        for link in branch.repairs:
            if link.node.node_id not in seen:
                seen.add(link.node.node_id)
                repairs.append(link.node)
    if repairs:
        lines += ["## 修复动作", ""]
        for repair in repairs:
            lines += _node_evidence_block(repair, options.evidence_limit)

    escalations = [link.node for link in playbook.referrals if link.node.node_type == "escalation"]
    escalations += [link.node for link in playbook.entry_actions if link.node.node_type == "escalation"]
    if escalations:
        lines += ["## 转交 / 升级", ""]
        for escalation in {node.node_id: node for node in escalations}.values():
            lines += _node_evidence_block(escalation, options.evidence_limit)

    lines += [
        "## 读法提醒",
        "",
        "- `confirms` 强于 `supports`；只有 `supports` 时不要宣布根因已确认。",
        "- `excludes` 只排除它指向的那个原因，不能外推成排除全部。",
        "- `condition` 是待判断的分支条件，需绑定现场对象、字段与采样数据后再判断。",
        "- `command_templates` 含待绑定参数，执行前先补全参数；变更类操作先与用户确认。",
        "- `service_impact` / `rollback` / `preconditions` 为空只表示来源没写，不代表无影响、无需回退、无前置条件。",
        "- 标了 `example_specific=true` 的条目带案例特定背景，不能推广到其他设备。",
        "",
    ]
    return "\n".join(lines)


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
            "generator": "subkg-to-skill",
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
def build_package(graph: Graph, playbook: Playbook, options: BuildOptions) -> SkillPackage:
    """Render one fault entry into a template-conformant skill package."""
    name = normalise_name(options.name or suggested_slug(playbook.symptom))
    description = options.description or default_description(playbook.symptom)
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1]

    slice_graph = graph.subgraph(playbook.covered)
    doc = build_doc(slice_graph, playbook)
    package = SkillPackage(notes=list(doc.notes))
    package.files["SKILL.md"] = render_doc(
        doc, name=name, description=description, lead=LEAD if options.include_lead else ""
    )
    package.files["reference/evidence.md"] = render_evidence(slice_graph, playbook, options)
    if options.data_mode != "none":
        package.files["reference/subgraph.json"] = render_data(slice_graph, options)
    elif options.include_script:
        package.notes.append("--data none 时不生成 reference/subgraph.json，查询脚本将无数据可读。")
    if options.include_script:
        package.files["scripts/kg_query.py"] = _query_script()
    return package
