"""从已有的 house style skill 文档里读回事实。

`steps` 默认只认 JSON 子图，但真正的知识有一部分只存在于人写的 `SKILL-*.md`
和 `reference/fault-*.md` 里：英文标识、触发特征、只在文档里出现的根因、
决策树里的命令、以及「根因迭代到底层」的跳转去向。这里把它们读出来，
补进 :class:`~graph2skill.stepskill.StepSkillContext`，让生成的模板 skill
同时覆盖「图里有的」和「文档里有的」。

只读，不改文档。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from graph2skill.mdsection import MarkdownDoc, Section, Table
from graph2skill.util import one_line

FAULT_NO_RE = re.compile(r"故障序号\s*(\d+)")
META_RE = re.compile(r"(标识|分类|根因)\s*[：:]\s*([^|]+)")
REFERENCE_RE = re.compile(r"详见[：:]\s*([^\s，,。]+\.md)")
CAUSE_HEADING_RE = re.compile(r"^#{2,4}\s*根因\s*(\d+)?\s*[：:]?\s*(.+)$")
DRILLDOWN_RE = re.compile(r"(?:底层下钻|跳转)[：:]\s*(.+)")
SECTION_TOKEN_RE = re.compile(r"§\s*(\d+(?:\.\d+)*)")
CODE_RE = re.compile(r"`([^`\n]+)`")
ITERATE_HINTS = ("根因迭代", "迭代到底层", "跳转")
TRIGGER_HINTS = ("触发条件", "适用", "现象")


def _is_command_like(text: str) -> bool:
    text = one_line(text)
    return bool(text) and " " in text and text[0].islower()


@dataclass
class DocCause:
    """文档里的一条根因。"""

    name: str
    name_en: str = ""
    trigger: str = ""
    ordinal: int = 0
    description: str = ""
    commands: List[str] = field(default_factory=list)
    drilldown: List[str] = field(default_factory=list)


@dataclass
class DocFault:
    """文档里的一个故障小节（含它的 reference 决策树）。"""

    fault_id: str = ""
    name: str = ""
    identifier: str = ""
    category: str = ""
    reference_file: str = ""
    causes: List[DocCause] = field(default_factory=list)

    def cause(self, name: str) -> Optional[DocCause]:
        target = one_line(name)
        for item in self.causes:
            if item.name == target:
                return item
        return None


@dataclass
class SkillDocFacts:
    """一份 skill 文档读出来的全部事实。"""

    path: str = ""
    faults: List[DocFault] = field(default_factory=list)
    jumps: List[Tuple[str, str, str]] = field(default_factory=list)  # (根因方向, §章节, 故障类型)
    trigger_text: str = ""
    commands: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def match(self, fault_id: str = "", identifier: str = "", name: str = "") -> Optional[DocFault]:
        """按「故障序号 → 标识 → 名称」依次匹配，和 merge 用的口径一致。"""
        for fault in self.faults:
            if fault_id and fault.fault_id and str(fault.fault_id) == str(fault_id):
                return fault
        for fault in self.faults:
            if identifier and fault.identifier and fault.identifier == identifier:
                return fault
        target = one_line(name)
        for fault in self.faults:
            if target and (target == fault.name or target in fault.name or fault.name in target):
                return fault
        return None

    def jump_for(self, cause_name: str) -> List[str]:
        """跳转表里与该根因方向沾边的条目，渲染成「§3.6 链路故障」。"""
        target = one_line(cause_name)
        hits: List[str] = []
        for direction, section, fault_type in self.jumps:
            if not direction or not section:
                continue
            if direction in target or target in direction:
                hits.append(f"{section} {fault_type}".strip())
        return hits


def _meta_fields(lines: Sequence[str]) -> Dict[str, str]:
    for line in lines:
        if "标识" in line and ("|" in line or "：" in line or ":" in line):
            found = {key: one_line(value) for key, value in META_RE.findall(line)}
            if found:
                return found
    return {}


def _cause_table(doc: MarkdownDoc, section: Section) -> Optional[Table]:
    for table in doc.tables(section):
        if any("根因" in cell or "原因" in cell for cell in table.header):
            return table
    return None


def _column(table: Table, *names: str) -> Optional[int]:
    for name in names:
        index = table.column_index(name)
        if index is not None:
            return index
    return None


def _read_reference(path: Path, fault: DocFault, facts: SkillDocFacts) -> None:
    """把 reference 决策树里的说明、命令、下钻方向补到对应根因上。"""
    if not path.is_file():
        facts.warnings.append(f"决策树文件不存在，已跳过：{path}")
        return
    doc = MarkdownDoc(path.read_text(encoding="utf-8"), str(path))
    mask = doc.code_fence_mask()
    starts: List[Tuple[int, str, int]] = []
    for index, line in enumerate(doc.lines):
        if mask[index]:
            continue
        match = CAUSE_HEADING_RE.match(line)
        if match:
            starts.append((index, one_line(match.group(2)), int(match.group(1)) if match.group(1) else 0))
    for position, (start, name, ordinal) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(doc.lines)
        cause = fault.cause(name)
        if cause is None:
            cause = DocCause(name=name, ordinal=ordinal)
            fault.causes.append(cause)
        body = doc.lines[start + 1 : end]
        in_fence = False
        for line in body:
            stripped = line.strip()
            if stripped.startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                if stripped and stripped not in cause.commands:
                    cause.commands.append(stripped)
                continue
            drill = DRILLDOWN_RE.search(stripped)
            if drill:
                text = one_line(drill.group(1))
                if text not in cause.drilldown:
                    cause.drilldown.append(text)
                continue
            for span in CODE_RE.findall(stripped):
                if _is_command_like(span) and span not in cause.commands:
                    cause.commands.append(one_line(span))
            if stripped and not stripped.startswith(("#", ">", "-", "*", "|")) and not cause.description:
                if "：" not in stripped[:6]:
                    cause.description = one_line(stripped)
        for command in cause.commands:
            if command not in facts.commands:
                facts.commands.append(command)


def _fault_sections(doc: MarkdownDoc) -> List[Section]:
    """承载故障的最内层小节。

    父章节的行范围包含子章节，所以「标识:」这类特征会在父子两级都命中；
    只保留不包含其它候选的那一层。
    """
    candidates: List[Section] = []
    for section in doc.sections():
        lines = doc.lines[section.start : section.end]
        if not _meta_fields(lines) and not FAULT_NO_RE.search(section.title):
            continue
        if _cause_table(doc, section) is None and not _meta_fields(lines):
            continue
        candidates.append(section)
    return [
        section
        for section in candidates
        if not any(
            other is not section and section.start <= other.start and other.end <= section.end
            for other in candidates
        )
    ]


def read_skill_doc(path: str | Path, reference_root: Optional[Path] = None) -> SkillDocFacts:
    """读一份 house style skill 文档（连同它引用的决策树文件）。"""
    doc_path = Path(path)
    facts = SkillDocFacts(path=str(doc_path))
    if not doc_path.is_file():
        raise FileNotFoundError(f"{doc_path}: 文档不存在")
    doc = MarkdownDoc(doc_path.read_text(encoding="utf-8"), str(doc_path))
    root = Path(reference_root) if reference_root else doc_path.parent

    for section in _fault_sections(doc):
        lines = doc.lines[section.start : section.end]
        meta = _meta_fields(lines)
        title_match = FAULT_NO_RE.search(section.title)
        table = _cause_table(doc, section)
        fault = DocFault(
            fault_id=title_match.group(1) if title_match else "",
            name=one_line(FAULT_NO_RE.sub("", section.title).lstrip("：: ")),
            identifier=meta.get("标识", ""),
            category=meta.get("分类", ""),
        )
        for line in lines:
            pointer = REFERENCE_RE.search(line)
            if pointer:
                fault.reference_file = pointer.group(1)
                break
        if table is not None:
            name_col = _column(table, "根因名称", "根因", "原因")
            en_col = _column(table, "英文标识", "英文名称", "English")
            trigger_col = _column(table, "触发特征", "现象", "判据")
            for ordinal, row in enumerate(table.rows, start=1):
                if name_col is None or name_col >= len(row):
                    continue
                name = one_line(row[name_col])
                if not name:
                    continue
                fault.causes.append(
                    DocCause(
                        name=name,
                        name_en=one_line(row[en_col]) if en_col is not None and en_col < len(row) else "",
                        trigger=one_line(row[trigger_col]) if trigger_col is not None and trigger_col < len(row) else "",
                        ordinal=ordinal,
                    )
                )
        if fault.reference_file:
            _read_reference(root / fault.reference_file, fault, facts)
        facts.faults.append(fault)

    for section in doc.sections():
        if any(hint in section.title for hint in ITERATE_HINTS):
            for table in doc.tables(section):
                for row in table.rows:
                    sections = []
                    for cell in row:
                        sections.extend(SECTION_TOKEN_RE.findall(cell))
                    if not sections:
                        continue
                    facts.jumps.append(
                        (one_line(row[0]), ", ".join(f"§{item}" for item in sections), one_line(row[-1]))
                    )
            break

    for section in doc.sections():
        if any(hint in section.title for hint in TRIGGER_HINTS) and section.level <= 2:
            prose = [
                one_line(line)
                for line in doc.lines[section.start + 1 : section.end]
                if line.strip() and not line.strip().startswith(("#", "-", "*", "|", ">", "["))
            ]
            if prose:
                facts.trigger_text = prose[0]
            break

    return facts


# ---------------------------------------------------------------------------
# 把文档事实补进模板 skill 的上下文
# ---------------------------------------------------------------------------

def enrich_context(context, facts: SkillDocFacts, doc_fault: Optional[DocFault] = None) -> List[str]:
    """用文档里的事实补齐上下文；返回补了哪些内容的说明。

    图和文档各有各的知识：图里有命令与结构，文档里有英文标识、触发特征、
    以及只写在 reference 决策树里的根因。两边合起来才是完整的一份 skill。
    """
    from graph2skill.stepskill import (
        NO_ROOT_CAUSE,
        CauseRow,
        Param,
        StepItem,
        normalize_command,
        normalize_param,
    )
    from graph2skill.stepskill import PARAM_RE

    notes: List[str] = []
    if doc_fault is None:
        return notes

    if doc_fault.identifier and doc_fault.identifier != context.name:
        notes.append(f"name 取文档标识：{context.name} → {doc_fault.identifier}")
        context.name = doc_fault.identifier
    if facts.trigger_text and facts.trigger_text not in context.description:
        context.description = f"{context.description.rstrip('。')}。{facts.trigger_text}"
        notes.append("description 补入文档的触发条件")

    known = {normalize_command(command) for command in context.allowed_commands}
    added_commands = 0
    for command in facts.commands:
        if normalize_command(command) not in known:
            context.allowed_commands.append(command)
            known.add(normalize_command(command))
            added_commands += 1
    if added_commands:
        notes.append(f"命令白名单 +{added_commands} 条（来自决策树文件）")

    rows = {row.name: row for row in context.causes}
    filled = 0
    for doc_cause in doc_fault.causes:
        row = rows.get(doc_cause.name)
        if row is None:
            continue
        if not row.symptom and doc_cause.trigger and "reference" not in doc_cause.trigger.lower():
            row.symptom = doc_cause.trigger
            filled += 1
        if not row.fix and doc_cause.drilldown:
            row.fix = f"无直接修复CLI，需按 {'、'.join(doc_cause.drilldown)} 继续下钻定位"
            filled += 1
    if filled:
        notes.append(f"根因对照表补齐 {filled} 处现象/下钻说明")

    graph_causes = {row.name for row in context.causes}
    appended = 0
    for doc_cause in doc_fault.causes:
        if doc_cause.name in graph_causes:
            continue
        # 这条根因的命令同样来自文档，必须一并进白名单，否则会被自己的校验器拒掉
        for command in doc_cause.commands:
            if normalize_command(command) not in known:
                context.allowed_commands.append(command)
                known.add(normalize_command(command))
        index = len(context.steps) + 1
        condition = doc_cause.trigger if doc_cause.trigger and "reference" not in doc_cause.trigger.lower() else ""
        context.steps.append(
            StepItem(
                index=index,
                title=f"检查是否为{doc_cause.name}",
                commands=[(command, "") for command in doc_cause.commands],
                condition=condition or one_line(doc_cause.description),
                cause=doc_cause.name,
            )
        )
        drill = doc_cause.drilldown or facts.jump_for(doc_cause.name)
        context.causes.insert(
            len(context.causes) - 1 if context.causes and context.causes[-1].name == NO_ROOT_CAUSE else len(context.causes),
            CauseRow(
                name=doc_cause.name,
                symptom=condition or one_line(doc_cause.description),
                fix=f"无直接修复CLI，需按 {'、'.join(drill)} 继续下钻定位" if drill else "",
                verify="",
            ),
        )
        graph_causes.add(doc_cause.name)
        appended += 1
    if appended:
        notes.append(f"补入文档独有的根因 {appended} 条")

    # 步骤里新加了命令，入参必须跟着重算，否则过不了校验
    used = [command for item in context.prechecks for command in item.commands]
    used += [command for step in context.steps for command, _ in step.commands]
    known_params = {normalize_param(param.cli) for param in context.params}
    for command in used:
        for token in PARAM_RE.findall(command):
            placeholder = f"<{token}>"
            if normalize_param(placeholder) in known_params:
                continue
            context.params.append(Param(cli=placeholder, label=placeholder.strip("<>").replace("-", " ")))
            known_params.add(normalize_param(placeholder))
    return notes
