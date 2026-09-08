"""Render graph content in the existing IP-network skill house style.

Two different contracts are used on purpose:

* **Skill documents** never get marker comments.  New 故障 subsections are
  inserted once and every later run merges table rows surgically, so hand edits
  survive.
* **Reference decision-tree files** use ``<!-- graph2skill:begin/end -->``
  markers: content inside a marker pair is owned by graph2skill and regenerated.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from graph2skill.faultmodel import CauseView, CommonRef, FaultView
from graph2skill.mdsection import MarkdownDoc, Table
from graph2skill.model import Node
from graph2skill.util import escape_table_cell, one_line, truncate

SECTION_TOKEN_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)")
CAUSE_HEADING_RE = re.compile(r"^#{2,4}\s*根因\s*(\d+)?\s*[：:]?\s*(.*)$")
CAUSE_TABLE_HEADER = ["编号", "根因名称", "英文标识", "触发特征"]


# ---------------------------------------------------------------------------
# skill document fragments
# ---------------------------------------------------------------------------

def cause_rows(fault: FaultView) -> List[List[str]]:
    return [
        [str(cause.ordinal), escape_table_cell(cause.name), escape_table_cell(cause.name_en), escape_table_cell(cause.trigger)]
        for cause in fault.causes
    ]


def render_fault_section(fault: FaultView, number: str, level: int = 3) -> List[str]:
    """A complete 故障 subsection, as if it had been written by hand."""
    heading = f"{'#' * level} {number} {fault.title}".replace("  ", " ")
    lines = [heading, "", fault.meta_line, ""]
    table = Table(start=0, end=0, header=list(CAUSE_TABLE_HEADER), rows=cause_rows(fault))
    lines += table.render()
    lines.append("")
    if fault.reference_file:
        lines.append(f"→ 诊断决策树与详细根因分析详见：{fault.reference_file}")
        lines.append("")
    return lines


def update_meta_line(lines: Sequence[str], cause_count: int) -> Optional[Tuple[int, str]]:
    """Refresh ``根因: N类`` in a 故障 meta line; returns (index, new line)."""
    for offset, line in enumerate(lines):
        if "标识:" in line and "根因:" in line:
            updated = re.sub(r"根因:\s*\d+\s*类", f"根因: {cause_count}类", line)
            return (offset, updated) if updated != line else None
    return None


def sections_in_table(table: Table, key_column: int) -> set:
    found = set()
    for row in table.rows:
        if key_column < len(row):
            found.update(SECTION_TOKEN_RE.findall(row[key_column]))
    return found


def common_ref_key_column(table: Table) -> int:
    for index, cell in enumerate(table.header):
        if "章节" in cell or "common" in cell.lower():
            return index
    return min(1, len(table.header) - 1)


def common_ref_rows(
    table: Table, refs: Sequence[Tuple[str, CommonRef]]
) -> Tuple[List[List[str]], List[str]]:
    """Rows to append to the 「根因迭代到底层」 table, plus warnings.

    A reference whose common section is already mentioned anywhere in the table
    is skipped: the jump target is what matters, not how it is phrased.
    """
    key_column = common_ref_key_column(table)
    present = sections_in_table(table, key_column)
    width = max(len(table.header), 3)
    rows: List[List[str]] = []
    warnings: List[str] = []
    for direction, ref in refs:
        if not ref.section:
            warnings.append(f"根因「{direction}」指向 {ref.doc} 的「{ref.name}」，但未能定位章节号，未写入跳转表")
            continue
        if ref.section in present:
            continue
        present.add(ref.section)
        row = [""] * width
        row[0] = escape_table_cell(direction)
        row[key_column] = f"§{ref.section}"
        for index in range(width):
            if index not in (0, key_column):
                row[index] = escape_table_cell(ref.name)
                break
        rows.append(row)
    return rows, warnings


# ---------------------------------------------------------------------------
# reference decision-tree files
# ---------------------------------------------------------------------------

def _command_block(nodes: Sequence[Node]) -> List[str]:
    commands: List[str] = []
    for node in nodes:
        commands.extend(node.commands)
    if not commands:
        return []
    seen: List[str] = []
    for command in commands:
        if command not in seen:
            seen.append(command)
    return ["```", *seen, "```"]


def render_cause_block(cause: CauseView, ordinal: int, excerpt_limit: int = 200) -> List[str]:
    lines = [f"## 根因{ordinal}：{cause.name}", ""]
    meta = []
    if cause.name_en:
        meta.append(f"英文标识: {cause.name_en}")
    if cause.trigger:
        meta.append(f"触发特征: {cause.trigger}")
    if meta:
        lines += [" | ".join(meta), ""]
    description = one_line(cause.node.description)
    if description and description != cause.name:
        lines += [description, ""]
    observations = [obs for obs in cause.node.observations if one_line(obs) != cause.name]
    if observations:
        lines += ["现象："] + [f"- {one_line(item)}" for item in observations] + [""]
    checks = _command_block([cause.node, *cause.checks])
    if checks:
        lines += ["检查："] + checks + [""]
    for action in cause.actions:
        lines.append(f"处理：{one_line(action.label)}")
        commands = _command_block([action])
        if commands:
            lines += commands
        lines.append("")
    for ref in cause.common_refs:
        target = f"{ref.doc} {ref.section_label}".strip()
        lines += [f"底层下钻：见 {target} {ref.name}".rstrip(), ""]
    for prov in cause.node.provenance:
        head = " · ".join(part for part in (f"`{prov.source_path}`" if prov.source_path else "", prov.locator) if part)
        if head:
            lines.append(f"出处：{head}")
        if prov.excerpt:
            lines.append(f"> {truncate(prov.excerpt, excerpt_limit)}")
        lines.append("")
    while lines and not lines[-1]:
        lines.pop()
    lines.append("")
    return lines


def existing_cause_names(doc: MarkdownDoc, skip_range: Optional[Tuple[int, int]] = None) -> Dict[str, int]:
    """Map of 根因 headings already present outside the managed block."""
    found: Dict[str, int] = {}
    mask = doc.code_fence_mask()
    for index, line in enumerate(doc.lines):
        if mask[index]:
            continue
        if skip_range and skip_range[0] <= index < skip_range[1]:
            continue
        match = CAUSE_HEADING_RE.match(line)
        if match:
            name = one_line(match.group(2))
            found[name] = int(match.group(1)) if match.group(1) else 0
    return found


def render_reference_header(fault: FaultView) -> List[str]:
    meta = [f"标识: {fault.identifier}"]
    if fault.category:
        meta.append(f"分类: {fault.category}")
    sources = ", ".join(fault.node.sources)
    if sources:
        meta.append(f"来源: {sources}")
    lines = [f"# 故障{fault.fault_id}：{fault.name} — 诊断决策树", "", "> " + " | ".join(meta), ""]
    description = one_line(fault.node.description)
    if description and description != fault.name:
        lines += [description, ""]
    checks = _command_block(fault.checks)
    if checks:
        lines += ["前置检查："] + checks + [""]
    return lines
