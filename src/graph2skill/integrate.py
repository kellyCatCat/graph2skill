"""Merge a JSON subgraph into an existing skill document.

The flow is: load the skill's paired subgraph, merge the incoming one into it,
work out what is genuinely new, and then edit the skill document surgically —
new 故障 subsections are inserted, existing cause tables gain rows, the
「根因迭代到底层」 table gains jump targets, and the reference decision-tree files
are refreshed inside their managed blocks.

Nodes owned by an included skill (``common.md``) are never copied into the child
skill: the child keeps a reference to the common section instead.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from graph2skill.analyze import GraphIndex
from graph2skill.faultmodel import CommonIndex, CommonRef, FaultView, extract_faults
from graph2skill.houserender import (
    cause_rows,
    common_ref_rows,
    existing_cause_names,
    render_cause_block,
    render_fault_section,
    render_reference_header,
    update_meta_line,
)
from graph2skill.loader import load_graph, load_graphs
from graph2skill.mdsection import MarkdownDoc, Section, Table, upsert_rows
from graph2skill.merge import MergeReport, merge_graphs
from graph2skill.model import Graph
from graph2skill.skillset import SkillEntry, SkillSet, SkillSetError

REFERENCE_POINTER_RE = re.compile(r"详见[：:]\s*([^\s，,。]+\.md)")
FAULT_TITLE_RE = re.compile(r"故障序号\s*(\d+)")
CAUSES_BLOCK_ID = "causes"
FAULTS_SECTION_HINTS = ("故障", "场景")
ITERATE_SECTION_HINTS = ("根因迭代", "迭代到底层", "跳转")


@dataclass
class FileChange:
    """A file graph2skill wants to write."""

    path: Path
    before: Optional[str]
    after: str
    summary: List[str] = field(default_factory=list)

    @property
    def created(self) -> bool:
        return self.before is None

    @property
    def changed(self) -> bool:
        return self.before != self.after

    def diff(self, context: int = 3) -> str:
        before = (self.before or "").splitlines(keepends=True)
        after = self.after.splitlines(keepends=True)
        name = str(self.path)
        return "".join(
            difflib.unified_diff(
                before,
                after,
                fromfile=f"a/{name}" if self.before is not None else "/dev/null",
                tofile=f"b/{name}",
                n=context,
            )
        )


@dataclass
class IntegrationReport:
    """What merging produced, before anything is written."""

    skill: str
    changes: List[FileChange] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    added_nodes: List[str] = field(default_factory=list)
    updated_nodes: List[str] = field(default_factory=list)
    added_relations: List[str] = field(default_factory=list)
    common_owned: List[str] = field(default_factory=list)
    new_faults: List[str] = field(default_factory=list)
    updated_faults: List[str] = field(default_factory=list)
    merge_report: Optional[MergeReport] = None

    @property
    def pending(self) -> List[FileChange]:
        return [change for change in self.changes if change.changed]

    def apply(self) -> List[Path]:
        written: List[Path] = []
        for change in self.pending:
            change.path.parent.mkdir(parents=True, exist_ok=True)
            change.path.write_text(change.after, encoding="utf-8")
            written.append(change.path)
        return written

    def diff(self, context: int = 3) -> str:
        return "".join(change.diff(context) for change in self.pending)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def build_common_index(skillset: SkillSet, entry: SkillEntry) -> Tuple[CommonIndex, List[str]]:
    """Index every node documented by the skills *entry* includes."""
    index = CommonIndex()
    warnings: List[str] = []
    for included in skillset.included_entries(entry):
        doc_path = skillset.doc_path(included)
        sections = []
        if doc_path.is_file():
            sections = MarkdownDoc(doc_path.read_text(encoding="utf-8"), str(doc_path)).sections()
        else:
            warnings.append(f"被引用的技能 '{included.name}' 缺少文档 {included.doc}")
        graph = None
        if included.graph and (skillset.root / included.graph).is_file():
            graph = load_graph(skillset.root / included.graph)
        else:
            warnings.append(
                f"被引用的技能 '{included.name}' 没有可用的子图，只能按 {included.doc} 的章节标题匹配公共节点"
            )
        partial = CommonIndex.build(graph, Path(included.doc).name, sections)
        index.merge(partial)
    return index, warnings


def strip_common_nodes(graph: Graph, common: CommonIndex) -> List[str]:
    """Drop nodes owned by an included skill; relations pointing at them stay."""
    removed = [node_id for node_id in graph.nodes if node_id in common]
    for node_id in removed:
        del graph.nodes[node_id]
    return removed


def find_faults_section(doc: MarkdownDoc, entry: SkillEntry) -> Optional[Section]:
    declared = entry.sections.get("faults")
    if declared:
        section = doc.find_section(number=declared) or doc.find_section(title_contains=declared)
        if section:
            return section
    for section in doc.sections():
        if section.level != 2:
            continue
        subsections = doc.subsections(section)
        if any(FAULT_TITLE_RE.search(sub.title) for sub in subsections):
            return section
    for section in doc.sections():
        if section.level == 2 and any(hint in section.title for hint in FAULTS_SECTION_HINTS):
            return section
    return None


def find_iterate_section(doc: MarkdownDoc, entry: SkillEntry) -> Optional[Section]:
    declared = entry.sections.get("iterate")
    if declared:
        section = doc.find_section(number=declared) or doc.find_section(title_contains=declared)
        if section:
            return section
    for section in doc.sections():
        if any(hint in section.title for hint in ITERATE_SECTION_HINTS):
            return section
    return None


def find_fault_section(doc: MarkdownDoc, parent: Section, fault: FaultView) -> Optional[Section]:
    for section in doc.subsections(parent):
        match = FAULT_TITLE_RE.search(section.title)
        if fault.fault_id and match and match.group(1) == str(fault.fault_id):
            return section
        body = "\n".join(doc.lines[section.start : section.end])
        if fault.identifier and re.search(rf"标识:\s*{re.escape(fault.identifier)}\b", body):
            return section
        if fault.name and fault.name in section.title:
            return section
    return None


def cause_table_in(doc: MarkdownDoc, section: Section) -> Optional[Table]:
    for table in doc.tables(section):
        if any("根因" in cell or "原因" in cell for cell in table.header):
            return table
    return None


def reference_pointer(doc: MarkdownDoc, section: Section) -> str:
    for line in doc.lines[section.start : section.end]:
        match = REFERENCE_POINTER_RE.search(line)
        if match:
            return match.group(1)
    return ""


def next_subsection_number(doc: MarkdownDoc, parent: Section) -> str:
    subsections = doc.subsections(parent)
    base = parent.number or str(len([s for s in doc.sections() if s.level == 2]))
    used = []
    for section in subsections:
        if section.number and section.number.startswith(f"{base}."):
            tail = section.number[len(base) + 1 :].split(".")[0]
            if tail.isdigit():
                used.append(int(tail))
    return f"{base}.{(max(used) + 1) if used else 1}"


# ---------------------------------------------------------------------------
# the integration itself
# ---------------------------------------------------------------------------

def integrate(
    skillset: SkillSet,
    target: str,
    incoming: Sequence[str],
    update_graph: bool = True,
    update_statistics: bool = False,
) -> IntegrationReport:
    """Merge *incoming* subgraph documents into the skill named *target*.

    With an empty *incoming* the skill is simply re-synchronised against the
    subgraph it already owns.
    """
    entry = skillset.get(target)
    graph_path = skillset.graph_path(entry)
    if not graph_path.is_file():
        raise SkillSetError(f"{graph_path}: the subgraph paired with '{entry.name}' does not exist")
    doc_path = skillset.doc_path(entry)
    if not doc_path.is_file():
        raise SkillSetError(f"{doc_path}: skill document not found")

    report = IntegrationReport(skill=entry.name)
    common, common_warnings = build_common_index(skillset, entry)
    report.warnings.extend(common_warnings)

    base_graph = load_graph(graph_path)
    incoming_graphs = load_graphs(list(incoming)) if incoming else []
    merged, merge_report = merge_graphs(
        [base_graph, *incoming_graphs], graph_id=base_graph.graph_id, domain=base_graph.domain or None
    )
    report.merge_report = merge_report

    report.common_owned = strip_common_nodes(merged, common)
    for node_id in report.common_owned:
        if node_id not in base_graph.nodes:
            report.warnings.append(f"节点 {node_id} 归属 {common.by_id(node_id).doc}，未写入本技能，只保留引用")

    report.added_nodes = sorted(set(merged.nodes) - set(base_graph.nodes))
    # internal=False: graph2skill's own bookkeeping (graphIds) must not count as a change
    report.updated_nodes = sorted(
        node_id
        for node_id, node in merged.nodes.items()
        if node_id in base_graph.nodes
        and node.to_dict(internal=False) != base_graph.nodes[node_id].to_dict(internal=False)
    )
    report.added_relations = sorted(set(merged.relations) - set(base_graph.relations))

    if update_graph:
        report.changes.append(
            FileChange(
                path=graph_path,
                before=graph_path.read_text(encoding="utf-8"),
                after=json.dumps(merged.to_dict(internal=False), ensure_ascii=False, indent=2) + "\n",
                summary=[
                    f"新增节点 {len(report.added_nodes)}、更新节点 {len(report.updated_nodes)}、"
                    f"新增关系 {len(report.added_relations)}"
                ],
            )
        )

    index = GraphIndex(merged)
    faults = extract_faults(merged, index, common, reference_dir=entry.reference_dir)
    doc_before = doc_path.read_text(encoding="utf-8")
    doc = MarkdownDoc(doc_before, str(doc_path))
    doc_summary: List[str] = []

    faults_section = find_faults_section(doc, entry)
    if faults_section is None:
        report.warnings.append(
            f"{entry.doc}: 未找到承载故障小节的章节（可在清单里用 sections.faults 指定），跳过故障表更新"
        )
    else:
        _apply_faults(doc, faults_section, faults, report, doc_summary, entry)

    iterate_section = find_iterate_section(doc, entry)
    refs: List[Tuple[str, CommonRef]] = []
    for fault in faults:
        for cause in fault.causes:
            for ref in cause.common_refs:
                refs.append((cause.name, ref))
    if refs:
        if iterate_section is None:
            report.warnings.append(
                f"{entry.doc}: 未找到「根因迭代到底层」章节（可用 sections.iterate 指定），"
                f"{len(refs)} 条公共章节跳转未写入"
            )
        else:
            _apply_common_refs(doc, iterate_section, refs, report, doc_summary)

    if doc.dirty:
        report.changes.append(FileChange(path=doc_path, before=doc_before, after=doc.text, summary=doc_summary))

    for fault in faults:
        change = _reference_change(skillset, entry, fault)
        if change is not None:
            report.changes.append(change)

    if update_statistics and entry.statistics:
        change = _statistics_change(skillset, entry, faults)
        if change is not None:
            report.changes.append(change)
    elif entry.statistics and not update_statistics:
        report.warnings.append(f"统计文件 {entry.statistics} 未更新（如需同步请加 --update-stats）")

    return report


def _apply_faults(
    doc: MarkdownDoc,
    faults_section: Section,
    faults: Sequence[FaultView],
    report: IntegrationReport,
    summary: List[str],
    entry: SkillEntry,
) -> None:
    for fault in faults:
        # sections shift as we edit, so re-resolve on every iteration
        parent = doc.find_section(number=faults_section.number, level=faults_section.level) or faults_section
        section = find_fault_section(doc, parent, fault)
        if section is None:
            number = next_subsection_number(doc, parent)
            lines = list(render_fault_section(fault, number, level=parent.level + 1))
            while lines and not lines[-1].strip():
                lines.pop()
            insert_at = parent.end
            while insert_at > parent.start + 1 and not doc.lines[insert_at - 1].strip():
                insert_at -= 1
            trailing = [""] if insert_at < len(doc.lines) and doc.lines[insert_at].strip() else []
            doc.insert_lines(insert_at, ["", *lines, *trailing])
            report.new_faults.append(fault.title)
            summary.append(f"新增 {number} {fault.title}（{len(fault.causes)} 类根因）")
            continue

        pointer = reference_pointer(doc, section)
        if pointer:
            fault.reference_file = pointer
        table = cause_table_in(doc, section)
        if table is None:
            report.warnings.append(f"{entry.doc}: 「{section.title}」下没有找到根因表，未合并 {len(fault.causes)} 条根因")
            continue
        key_column = table.column_index("根因名称")
        if key_column is None:
            key_column = 1 if len(table.header) > 1 else 0
        ordinal_column = table.column_index("编号")
        added, updated = upsert_rows(table, cause_rows(fault), key_column=key_column, ordinal_column=ordinal_column)
        table_changed = doc.update_table(table)
        section = doc.find_section(number=section.number, level=section.level) or section
        meta = update_meta_line(doc.lines[section.start : section.end], len(table.rows))
        meta_changed = False
        if meta is not None:
            offset, new_line = meta
            meta_changed = doc.replace_lines(section.start + offset, section.start + offset + 1, [new_line])
        if added or updated or table_changed or meta_changed:
            report.updated_faults.append(fault.title)
            summary.append(f"{section.number} {fault.title}：新增根因 {added} 条、更新 {updated} 条")


def _apply_common_refs(
    doc: MarkdownDoc,
    iterate_section: Section,
    refs: Sequence[Tuple[str, CommonRef]],
    report: IntegrationReport,
    summary: List[str],
) -> None:
    tables = doc.tables(iterate_section)
    if not tables:
        report.warnings.append("「根因迭代到底层」章节下没有找到跳转表，公共章节引用未写入")
        return
    table = tables[0]
    rows, warnings = common_ref_rows(table, refs)
    report.warnings.extend(warnings)
    if not rows:
        return
    table.rows.extend(rows)
    if doc.update_table(table):
        summary.append(f"根因迭代表新增 {len(rows)} 条公共章节跳转")


def _reference_change(skillset: SkillSet, entry: SkillEntry, fault: FaultView) -> Optional[FileChange]:
    if not fault.reference_file:
        return None
    path = skillset.root / fault.reference_file
    before: Optional[str] = path.read_text(encoding="utf-8") if path.is_file() else None
    doc = MarkdownDoc(before if before is not None else "", str(path))
    if before is None:
        doc.lines = list(render_reference_header(fault))
        doc.ends_with_newline = True
    block = doc.find_block(CAUSES_BLOCK_ID)
    handwritten = existing_cause_names(doc, skip_range=block)
    start_ordinal = max([number for number in handwritten.values() if number] or [0])
    content: List[str] = []
    generated = 0
    for cause in fault.causes:
        if cause.name in handwritten:
            continue
        start_ordinal += 1
        generated += 1
        content.extend(render_cause_block(cause, start_ordinal))
    if not content and block is None:
        return None
    while content and not content[-1]:
        content.pop()
    doc.upsert_block(CAUSES_BLOCK_ID, content)
    after = doc.text
    if before == after:
        return None
    return FileChange(
        path=path,
        before=before,
        after=after,
        summary=[f"{'新建' if before is None else '更新'}决策树文件，托管区含 {generated} 条根因"],
    )


def _statistics_change(skillset: SkillSet, entry: SkillEntry, faults: Sequence[FaultView]) -> Optional[FileChange]:
    path = skillset.root / entry.statistics
    if not path.is_file():
        return None
    before = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(before)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    match = None
    for fault in faults:
        if payload.get("identifier") == fault.identifier or str(payload.get("faultId", "")) == str(fault.fault_id):
            match = fault
            break
    if match is None:
        return None
    updated = dict(payload)
    if "causeCount" in updated:
        updated["causeCount"] = len(match.causes)
    after = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
    if after == before:
        return None
    return FileChange(path=path, before=before, after=after, summary=[f"causeCount → {len(match.causes)}"])
