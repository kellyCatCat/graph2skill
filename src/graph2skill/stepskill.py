"""The 排查型 skill template: extract, render and lint.

The template has four fixed sections (入参列表 / 前置检查 / 排查步骤 / 根因对照表)
and a long list of hard rules.  Rules that a machine can check live in
:func:`lint`; the renderer produces a draft that satisfies them by construction,
so an LLM pass (see :mod:`graph2skill.llm`) only has to improve the prose.

The single most important rule — *only CLIs that appear in the source data may
be used* — is enforced from the graph's command inventory, both when rendering
and when linting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from graph2skill.analyze import GraphIndex, Issue
from graph2skill.faultmodel import CauseView, CommonIndex, FaultView, extract_faults
from graph2skill.mdsection import MarkdownDoc, Table
from graph2skill.model import Graph, Node, first_str
from graph2skill.util import escape_table_cell, one_line, slugify

SPEC_PATH = Path(__file__).resolve().parent / "resources" / "step_skill_spec.md"

SECTIONS = ("入参列表", "前置检查", "排查步骤", "根因对照表")
PARAM_RE = re.compile(r"<([^<>\s]{1,60})>")
CODE_RE = re.compile(r"`([^`\n]+)`")
STEP_HEADING_RE = re.compile(r"^##\s*步骤\s*(\d+)\s*[：:]\s*(.*)$")
STEP_REF_RE = re.compile(r"步骤\s*(\d+)")
BAD_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z_一-鿿][^}]*\}|\[[A-Z][A-Z_\- ]{2,}\]|\bXXX+\b")
SHORT_INTERFACE_RE = re.compile(r"\b(GE|XGE|Gi|Te|Eth)\d+(/\d+)+\b")
NO_ROOT_CAUSE = "未找到根因"
NO_ROOT_CAUSE_FIX = "输出已执行的全部检查步骤及结果摘要"
NO_FIX_CLI = "无直接修复CLI"

PARAM_TABLE_HEADER = ["信息", "是否必填", "说明"]
CAUSE_TABLE_HEADER = ["根因", "现象", "修复CLI和方法", "复检命令（可选）"]


# ---------------------------------------------------------------------------
# context extracted from the graph
# ---------------------------------------------------------------------------

@dataclass
class Param:
    cli: str  # "<endpoint-ipv6>"
    label: str  # "endpoint ipv6"
    required: bool = True
    note: str = ""

    def row(self) -> List[str]:
        return [escape_table_cell(self.label), "是" if self.required else "否", escape_table_cell(self.note)]


@dataclass
class PrecheckItem:
    title: str
    commands: List[str] = field(default_factory=list)
    collect: List[str] = field(default_factory=list)
    root_cause: str = ""


@dataclass
class StepItem:
    index: int
    title: str
    commands: List[Tuple[str, str]] = field(default_factory=list)  # (command, note)
    reuse_note: str = ""
    condition: str = ""
    cause: str = ""


@dataclass
class CauseRow:
    name: str
    symptom: str = ""
    fix: str = ""
    verify: str = ""

    def row(self) -> List[str]:
        return [
            escape_table_cell(self.name),
            escape_table_cell(self.symptom or "-"),
            escape_table_cell(self.fix or f"{NO_FIX_CLI}，仅能定位"),
            escape_table_cell(self.verify or "-"),
        ]


@dataclass
class StepSkillContext:
    """Everything the template needs, taken only from the graph."""

    name: str
    description: str
    fault_name: str
    params: List[Param] = field(default_factory=list)
    prechecks: List[PrecheckItem] = field(default_factory=list)
    steps: List[StepItem] = field(default_factory=list)
    causes: List[CauseRow] = field(default_factory=list)
    allowed_commands: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)


def normalize_command(command: str) -> str:
    """Compare commands ignoring whitespace and the concrete parameter names."""
    text = one_line(command).lower()
    text = PARAM_RE.sub("<>", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_param(text: str) -> str:
    return re.sub(r"[\s\-_]+", "", one_line(text)).lower().strip("<>")


def command_inventory(graph: Graph) -> List[str]:
    """Every CLI that the source data actually contains."""
    commands: List[str] = []
    for node in graph.nodes.values():
        commands.extend(node.commands)
        for key in ("fixCommands", "verifyCommands", "repairCommands"):
            value = node.data.get(key)
            if isinstance(value, list):
                commands.extend(str(item) for item in value if item)
            elif isinstance(value, str) and value.strip():
                commands.append(value.strip())
    seen: List[str] = []
    for command in commands:
        text = one_line(command)
        if text and text not in seen:
            seen.append(text)
    return seen


def _params_from_commands(commands: Iterable[str]) -> List[str]:
    found: List[str] = []
    for command in commands:
        for token in PARAM_RE.findall(command):
            placeholder = f"<{token}>"
            if placeholder not in found:
                found.append(placeholder)
    return found


def _declared_params(node: Node) -> List[Param]:
    params: List[Param] = []
    raw = node.data.get("parameters")
    if not isinstance(raw, list):
        return params
    for item in raw:
        if isinstance(item, str):
            params.append(Param(cli=f"<{item.strip('<>')}>", label=item.strip("<>").replace("-", " ")))
        elif isinstance(item, dict):
            cli = first_str(item, ("cli", "placeholder", "token")) or f"<{first_str(item, ('name',))}>"
            label = first_str(item, ("label", "name")) or cli.strip("<>").replace("-", " ")
            required = item.get("required", True)
            params.append(
                Param(
                    cli=cli if cli.startswith("<") else f"<{cli}>",
                    label=label,
                    required=bool(required),
                    note=first_str(item, ("note", "description", "source")),
                )
            )
    return params


def _fix_text(cause: CauseView) -> str:
    """The repair column, copied from the source rather than invented."""
    declared = first_str(cause.node.data, ("fix", "repair", "configFix", "修复"))
    if declared:
        return declared
    parts: List[str] = []
    for action in cause.actions:
        commands = action.commands
        if commands:
            joined = "<br>".join(f"`{one_line(command)}`" for command in commands)
            parts.append(f"{one_line(action.label)}：{joined}")
        else:
            description = one_line(action.description) or one_line(action.label)
            if description:
                parts.append(description)
    return "；".join(parts)


def _verify_text(cause: CauseView) -> str:
    declared = first_str(cause.node.data, ("verify", "verification", "recheck", "复检"))
    if declared:
        return f"`{declared}`" if not declared.startswith("`") else declared
    for action in cause.actions:
        verify = first_str(action.data, ("verify", "verification", "recheck"))
        if verify:
            return f"`{verify}`"
    return ""


def build_context(
    graph: Graph,
    fault: FaultView,
    index: Optional[GraphIndex] = None,
    name: Optional[str] = None,
    description: Optional[str] = None,
) -> StepSkillContext:
    """Turn one fault sub-graph into template input."""
    index = index or GraphIndex(graph)
    allowed = command_inventory(graph)

    prechecks: List[PrecheckItem] = []
    for node in fault.checks:
        prechecks.append(
            PrecheckItem(
                title=one_line(node.label),
                commands=[one_line(command) for command in node.commands],
                collect=[one_line(item) for item in node.observations]
                or [one_line(first_str(node.data, ("collect", "采集内容")))] if node.observations or first_str(node.data, ("collect", "采集内容")) else [],
                root_cause=first_str(node.data, ("rootCause", "根因定位")),
            )
        )
    if not prechecks and fault.node.commands:
        prechecks.append(
            PrecheckItem(
                title=f"采集{fault.name}的基础回显",
                commands=[one_line(command) for command in fault.node.commands],
                collect=[one_line(item) for item in fault.node.observations],
            )
        )

    precheck_commands = {normalize_command(command) for item in prechecks for command in item.commands}
    precheck_index = {}
    for position, item in enumerate(prechecks, start=1):
        for command in item.commands:
            precheck_index.setdefault(normalize_command(command), position)

    steps: List[StepItem] = []
    causes: List[CauseRow] = []
    for position, cause in enumerate(fault.causes, start=1):
        commands: List[Tuple[str, str]] = []
        reuse_note = ""
        candidates: List[str] = []
        for check in cause.checks:
            candidates.extend(check.commands)
        candidates.extend(cause.node.commands)
        for command in candidates:
            text = one_line(command)
            key = normalize_command(text)
            if key in precheck_commands:
                if not reuse_note:
                    reuse_note = f"复用前置检查步骤{precheck_index[key]}回显"
                continue
            note = one_line(first_str(cause.node.data, ("commandNote", "命令说明")))
            if all(text != existing for existing, _ in commands):
                commands.append((text, note))
        steps.append(
            StepItem(
                index=position,
                title=f"检查是否为{cause.name}",
                commands=commands,
                reuse_note=reuse_note,
                condition=cause.raw_trigger,
                cause=cause.name,
            )
        )
        causes.append(
            CauseRow(
                name=cause.name,
                symptom=one_line(cause.raw_trigger),
                fix=_fix_text(cause),
                verify=_verify_text(cause),
            )
        )
    causes.append(CauseRow(name=NO_ROOT_CAUSE, symptom="全部步骤走完仍未命中任何故障特征", fix=NO_ROOT_CAUSE_FIX, verify="-"))

    used_commands = [command for item in prechecks for command in item.commands]
    used_commands += [command for step in steps for command, _ in step.commands]
    declared = _declared_params(fault.node)
    params: List[Param] = list(declared)
    known = {normalize_param(param.cli) for param in params}
    for placeholder in _params_from_commands(used_commands):
        if normalize_param(placeholder) in known:
            continue
        params.append(Param(cli=placeholder, label=placeholder.strip("<>").replace("-", " "), required=True))
        known.add(normalize_param(placeholder))

    evidence: List[str] = []
    for node in [fault.node] + [cause.node for cause in fault.causes]:
        for prov in node.provenance:
            location = " · ".join(part for part in (prov.source_path, prov.locator) if part)
            if location and location not in evidence:
                evidence.append(location)

    return StepSkillContext(
        name=name or default_skill_name(fault, graph.domain),
        description=description or _default_description(fault),
        fault_name=fault.name,
        params=params,
        prechecks=prechecks,
        steps=steps,
        causes=causes,
        allowed_commands=allowed,
        evidence=evidence,
    )


GENERIC_NAME_PARTS = {"symptom", "fault", "cause", "scenario", "case", "node", "issue", "problem"}
HASH_SUFFIX_RE = re.compile(r"[-_][0-9a-f]{8,}$", re.IGNORECASE)


def default_skill_name(fault: FaultView, domain: str = "") -> str:
    """技能名：优先用标识；退化时去掉哈希后缀，太笼统就冠上领域名。"""
    if fault.identifier and not HASH_SUFFIX_RE.search(fault.identifier):
        slug = slugify(fault.identifier, fallback="")
        if slug:
            return slug
    base = HASH_SUFFIX_RE.sub("", fault.node.id.split(":")[-1])
    slug = slugify(base, fallback="")
    prefix = slugify(domain, fallback="")
    if not slug or slug in GENERIC_NAME_PARTS:
        parts = [part for part in (prefix, slug or "fault", str(fault.fault_id or "")) if part]
        return "-".join(parts)
    return f"{prefix}-{slug}" if prefix and slug in GENERIC_NAME_PARTS else slug


def _default_description(fault: FaultView) -> str:
    symptom = one_line(fault.node.description) or fault.name
    triggers = "、".join(one_line(item) for item in fault.node.observations[:3])
    when = f"出现{triggers}时使用。" if triggers else f"排查{fault.name}时使用。"
    return f"{symptom}{'' if symptom.endswith('。') else '。'}{when}"


# ---------------------------------------------------------------------------
# deterministic renderer
# ---------------------------------------------------------------------------

def render_markdown(context: StepSkillContext) -> str:
    lines = ["---", f"name: {context.name}", f"description: {one_line(context.description)}", "---", ""]

    lines += ["# 入参列表", ""]
    table = Table(start=0, end=0, header=list(PARAM_TABLE_HEADER), rows=[param.row() for param in context.params])
    lines += table.render() if context.params else ["| 信息 | 是否必填 | 说明 |", "| --- | --- | --- |", "| 网元ID | 是 | 网元的resId |"]
    lines.append("")

    lines += ["# 前置检查", "", "前置检查按顺序线性执行，仅用于采集后续排查所需的回显信息，不做跳转。", ""]
    if not context.prechecks:
        lines += ["1. **无前置采集要求**", "   - 采集内容：源数据未给出前置采集命令。", ""]
    for position, item in enumerate(context.prechecks, start=1):
        lines.append(f"{position}. **{item.title}**")
        for command in item.commands:
            lines.append(f"   - CLI 命令：`{command}`")
        if item.collect:
            lines.append(f"   - 采集内容：{'、'.join(item.collect)}。")
        if item.root_cause:
            lines.append(f"   - 根因定位：{item.root_cause}")
        lines.append("")

    lines += ["# 排查步骤", "", "默认按顺序执行，判据来自前置检查已采集的回显时不重复下发命令。", ""]
    total = len(context.steps)
    for step in context.steps:
        lines += [f"## 步骤{step.index}：{step.title}", ""]
        lines.append(f"1. **步骤名称**：{step.title}")
        if step.commands:
            if len(step.commands) == 1 and not step.commands[0][1]:
                lines.append(f"2. **CLI 命令**：`{step.commands[0][0]}`")
            else:
                lines.append("2. **CLI 命令**：")
                for command, note in step.commands:
                    suffix = f"（{note}）" if note else ""
                    lines.append(f"   - `{command}`{suffix}")
        else:
            lines.append(f"2. **CLI 命令**：{step.reuse_note or '复用前置检查回显'}，不重复下发命令")
        lines.append("3. **跳转信息**：")
        condition = one_line(step.condition) or f"回显特征符合「{step.cause}」"
        if step.index < total:
            lines.append(f"   - 不满足「{condition}」：顺序执行步骤{step.index + 1}。")
        else:
            lines.append(
                f"   - 不满足「{condition}」：判定「{NO_ROOT_CAUSE}」，{NO_ROOT_CAUSE_FIX}，结束排查。"
            )
        lines.append(f"   - 满足「{condition}」：定位根因，结束排查。")
        lines += ["4. **根因定位**：", f"   - {step.cause}", ""]
    if not context.steps:
        lines += [
            "## 步骤1：确认故障现象",
            "",
            "1. **步骤名称**：确认故障现象",
            "2. **CLI 命令**：复用前置检查回显，不重复下发命令",
            "3. **跳转信息**：",
            f"   - 无法确认：判定「{NO_ROOT_CAUSE}」，{NO_ROOT_CAUSE_FIX}，结束排查。",
            "4. **根因定位**：",
            "   - 无",
            "",
        ]

    lines += ["# 根因对照表", ""]
    cause_table = Table(start=0, end=0, header=list(CAUSE_TABLE_HEADER), rows=[row.row() for row in context.causes])
    lines += cause_table.render()
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# linter
# ---------------------------------------------------------------------------

def _front_matter(text: str) -> Tuple[Dict[str, str], int]:
    if not text.startswith("---"):
        return {}, 0
    end = text.find("\n---", 3)
    if end == -1:
        return {}, 0
    body = text[3:end]
    fields: Dict[str, str] = {}
    for line in body.splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields, text[: end + 4].count("\n") + 1


def _code_spans(text: str) -> List[str]:
    return [one_line(match) for match in CODE_RE.findall(text)]


def _is_command_like(span: str) -> bool:
    if not span or " " not in span:
        return False
    return span[0].islower()


def lint(text: str, allowed_commands: Optional[Sequence[str]] = None) -> List[Issue]:
    """Check a rendered skill against the template rules."""
    issues: List[Issue] = []
    doc = MarkdownDoc(text)
    fields, _ = _front_matter(text)

    # -- front matter ---------------------------------------------------------
    if not fields:
        issues.append(Issue("error", "front-matter-missing", "文件开头缺少 --- 包裹的 front matter"))
    else:
        name = fields.get("name", "")
        if not name:
            issues.append(Issue("error", "name-missing", "front matter 缺少 name"))
        elif not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name):
            issues.append(Issue("error", "name-format", "name 必须是小写英文与连字符", name))
        if not fields.get("description"):
            issues.append(Issue("error", "description-missing", "front matter 缺少 description（故障现象 + 适用时机）"))

    # -- the four sections ----------------------------------------------------
    top = [section for section in doc.sections() if section.level == 1]
    titles = [one_line(section.title) for section in top]
    if titles != list(SECTIONS):
        issues.append(
            Issue(
                "error",
                "sections",
                f"一级标题必须依次为 {' → '.join(SECTIONS)}，当前为 {' → '.join(titles) or '(空)'}",
            )
        )
    by_title = {one_line(section.title): section for section in top}

    # -- 入参列表 -------------------------------------------------------------
    declared_params: Dict[str, Tuple[str, bool]] = {}
    param_section = by_title.get("入参列表")
    if param_section is not None:
        tables = doc.tables(param_section)
        if not tables:
            issues.append(Issue("error", "param-table-missing", "入参列表缺少表格"))
        else:
            table = tables[0]
            if [cell.strip() for cell in table.header] != PARAM_TABLE_HEADER:
                issues.append(
                    Issue("error", "param-table-header", f"入参列表表头必须是 {' | '.join(PARAM_TABLE_HEADER)}")
                )
            for row in table.rows:
                if len(row) < 2:
                    continue
                label = one_line(row[0])
                required = one_line(row[1]) in ("是", "Y", "yes", "必填")
                key = normalize_param(label)
                if key in declared_params:
                    issues.append(Issue("warning", "param-duplicate", f"入参「{label}」重复声明", label))
                declared_params[key] = (label, required)
                if not required and len(row) > 2 and not one_line(row[2]):
                    issues.append(
                        Issue("warning", "param-source-missing", f"选填入参「{label}」未说明来自哪一步", label)
                    )

    # -- 前置检查 -------------------------------------------------------------
    precheck_section = by_title.get("前置检查")
    precheck_text = ""
    if precheck_section is not None:
        precheck_text = "\n".join(doc.lines[precheck_section.start : precheck_section.end])
        for match in STEP_REF_RE.finditer(precheck_text):
            if "前置检查" not in precheck_text[max(0, match.start() - 12) : match.start()]:
                issues.append(
                    Issue("warning", "precheck-jump", "前置检查必须线性执行，不应出现跳转到排查步骤的说明")
                )
                break
        for span in _code_spans(precheck_text):
            for placeholder in PARAM_RE.findall(span):
                key = normalize_param(placeholder)
                if key not in declared_params:
                    issues.append(
                        Issue("error", "precheck-param-undeclared", f"前置检查用到未声明的入参 <{placeholder}>", placeholder)
                    )
                elif not declared_params[key][1]:
                    issues.append(
                        Issue(
                            "error",
                            "precheck-param-optional",
                            f"前置检查只能使用必填入参，<{placeholder}> 在入参列表中为选填",
                            placeholder,
                        )
                    )

    # -- 排查步骤 -------------------------------------------------------------
    step_numbers: List[int] = []
    step_causes: List[str] = []
    steps_section = by_title.get("排查步骤")
    if steps_section is None:
        issues.append(Issue("error", "steps-missing", "缺少「排查步骤」章节"))
    else:
        mask = doc.code_fence_mask()
        step_bounds: List[Tuple[int, int, int, str]] = []
        for index in range(steps_section.start, steps_section.end):
            if mask[index]:
                continue
            match = STEP_HEADING_RE.match(doc.lines[index])
            if match:
                step_bounds.append((int(match.group(1)), index, 0, one_line(match.group(2))))
        for position, (number, start, _, title) in enumerate(step_bounds):
            end = step_bounds[position + 1][1] if position + 1 < len(step_bounds) else steps_section.end
            body = "\n".join(doc.lines[start:end])
            step_numbers.append(number)
            for label in ("步骤名称", "CLI 命令", "跳转信息", "根因定位"):
                if label not in body and label.replace(" ", "") not in body:
                    issues.append(Issue("error", "step-field-missing", f"步骤{number} 缺少「{label}」", str(number)))
            declared_title = re.search(r"\*\*步骤名称\*\*\s*[：:]\s*(.+)", body)
            if declared_title and one_line(declared_title.group(1)) != title:
                issues.append(
                    Issue("warning", "step-title-mismatch", f"步骤{number} 标题与「步骤名称」不一致", str(number))
                )
            jump_block = _labelled_block(body, "跳转信息")
            for referenced in STEP_REF_RE.findall(jump_block):
                if int(referenced) not in [item[0] for item in step_bounds]:
                    issues.append(
                        Issue("error", "step-jump-unknown", f"步骤{number} 跳转到不存在的步骤{referenced}", str(number))
                    )
            cause_block = _labelled_block(body, "根因定位")
            for line in cause_block.splitlines():
                cleaned = one_line(line).lstrip("-* ").strip()
                if not cleaned or cleaned in ("无",):
                    continue
                if CODE_RE.search(cleaned):
                    issues.append(
                        Issue("warning", "step-cause-has-command", f"步骤{number} 的根因定位不应包含命令", str(number))
                    )
                step_causes.append(cleaned)
            if position + 1 == len(step_bounds) and NO_ROOT_CAUSE not in jump_block:
                issues.append(
                    Issue("error", "last-step-outcome", f"最后一步必须写清全部判据不命中时判定「{NO_ROOT_CAUSE}」", str(number))
                )
        if step_numbers != list(range(1, len(step_numbers) + 1)):
            issues.append(Issue("error", "step-numbering", f"步骤编号必须从 1 连续递增，当前为 {step_numbers}"))

    # -- 根因对照表 -----------------------------------------------------------
    table_causes: List[str] = []
    cause_section = by_title.get("根因对照表")
    if cause_section is not None:
        tables = doc.tables(cause_section)
        if not tables:
            issues.append(Issue("error", "cause-table-missing", "根因对照表缺少表格"))
        else:
            table = tables[0]
            if [cell.strip() for cell in table.header] != CAUSE_TABLE_HEADER:
                issues.append(
                    Issue("error", "cause-table-header", f"根因对照表表头必须是 {' | '.join(CAUSE_TABLE_HEADER)}")
                )
            for row in table.rows:
                if not row:
                    continue
                name = one_line(row[0])
                table_causes.append(name)
                if name == NO_ROOT_CAUSE:
                    if len(row) < 3 or NO_ROOT_CAUSE_FIX not in one_line(row[2]):
                        issues.append(
                            Issue("error", "no-cause-row-fix", f"「{NO_ROOT_CAUSE}」行的修复列必须写「{NO_ROOT_CAUSE_FIX}」")
                        )
                elif len(row) > 2 and not one_line(row[2]):
                    issues.append(
                        Issue("error", "cause-fix-empty", f"根因「{name}」的修复列为空，应写「{NO_FIX_CLI}」并说明只能定位", name)
                    )
                if len(row) > 3 and not one_line(row[3]):
                    issues.append(Issue("warning", "cause-verify-empty", f"根因「{name}」的复检列为空，应写 -", name))
            if NO_ROOT_CAUSE not in table_causes:
                issues.append(Issue("error", "no-cause-row-missing", f"根因对照表缺少「{NO_ROOT_CAUSE}」行"))

    for cause in step_causes:
        if cause not in table_causes:
            issues.append(Issue("error", "cause-not-in-table", f"步骤里的根因「{cause}」未出现在根因对照表", cause))
    for cause in table_causes:
        if cause != NO_ROOT_CAUSE and cause not in step_causes:
            issues.append(Issue("warning", "cause-not-in-steps", f"根因对照表的「{cause}」没有任何步骤定位到它", cause))

    # -- commands and placeholders -------------------------------------------
    allowed = {normalize_command(command) for command in (allowed_commands or [])}
    seen_params: Dict[str, str] = {}
    for span in _code_spans(text):
        for placeholder in PARAM_RE.findall(span):
            key = normalize_param(placeholder)
            if key in seen_params and seen_params[key] != placeholder:
                issues.append(
                    Issue(
                        "error",
                        "param-inconsistent",
                        f"同一入参出现两种写法：<{seen_params[key]}> 与 <{placeholder}>",
                        placeholder,
                    )
                )
            seen_params.setdefault(key, placeholder)
            if key not in declared_params:
                issues.append(Issue("error", "param-undeclared", f"命令里的 <{placeholder}> 未出现在入参列表", placeholder))
        if not _is_command_like(span):
            continue
        if SHORT_INTERFACE_RE.search(span):
            issues.append(Issue("warning", "interface-abbreviated", f"接口名应使用全称：`{span}`", span))
        if allowed and normalize_command(span) not in allowed:
            issues.append(Issue("error", "command-not-in-source", f"命令不在源数据中，不能自行生成：`{span}`", span))
    for match in BAD_PLACEHOLDER_RE.finditer(re.sub(r"```.*?```", "", text, flags=re.DOTALL)):
        issues.append(Issue("warning", "placeholder-style", f"可变参数必须用尖括号：{match.group(0)}", match.group(0)))

    issues.sort(key=lambda issue: ({"error": 0, "warning": 1, "info": 2}.get(issue.severity, 9), issue.code))
    return issues


def _labelled_block(body: str, label: str) -> str:
    """The lines belonging to one numbered item of a step."""
    pattern = re.compile(rf"\*\*{re.escape(label)}\*\*\s*[：:]?(.*)", re.DOTALL)
    match = pattern.search(body)
    if not match:
        return ""
    rest = match.group(1)
    stop = re.search(r"\n\s*\d+\.\s+\*\*", rest)
    return rest[: stop.start()] if stop else rest


def contexts_for_graph(
    graph: Graph,
    common: Optional[CommonIndex] = None,
    fault_id: Optional[str] = None,
) -> List[Tuple[FaultView, StepSkillContext]]:
    """One context per fault in *graph* (optionally a single fault)."""
    index = GraphIndex(graph)
    faults = extract_faults(graph, index, common)
    if fault_id:
        wanted = str(fault_id)
        faults = [
            fault
            for fault in faults
            if str(fault.fault_id) == wanted or fault.identifier == wanted or fault.node.id == wanted
        ]
    return [(fault, build_context(graph, fault, index)) for fault in faults]


def read_spec() -> str:
    return SPEC_PATH.read_text(encoding="utf-8")
