"""Check a generated skill against the house template.

The generator already writes conforming documents; the linter exists so a
hand-edited skill (or one produced another way) can be checked before it ships,
and so ``build`` can verify its own output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from subkg2skill.template import NOT_FOUND, PARAM_RE, case_literals, command_signature, param_key

SECTIONS = ("入参列表", "前置检查", "排查步骤", "根因对照表")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
STEP_RE = re.compile(r"^##\s*步骤\s*(\d+)\s*[：:]\s*(.+?)\s*$")
#: “步骤 N” as a jump target — “前置检查步骤 N” is a back-reference, not a jump.
JUMP_RE = re.compile(r"(?<!前置检查)步骤\s*(\d+)")
CODE_RE = re.compile(r"`([^`]+)`")
#: Only ``{}`` is a stray placeholder; ``[ ... ]`` is CLI optional-argument syntax.
BAD_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z0-9_\-一-鿿]+\}")
#: Beyond this many steps a document stops being followable; split by diagnostic unit.
MAX_REASONABLE_STEPS = 25
#: The same command collected this many times means the prechecks were not merged.
MAX_COMMAND_REPEATS = 2
SHORT_INTERFACE_RE = re.compile(r"\b(?:\d+)?(?:GE|XGE|FE|Eth)\d+/\d+", re.I)
STEP_ITEMS = ("步骤名称", "CLI 命令", "跳转信息", "根因定位")


@dataclass
class LintIssue:
    level: str  # error | warning
    message: str

    def render(self) -> str:
        return f"{self.level.upper()}: {self.message}"


@dataclass
class LintResult:
    issues: List[LintIssue]

    @property
    def errors(self) -> List[LintIssue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> List[LintIssue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors


def _split_sections(text: str) -> Tuple[Dict[str, List[str]], List[str]]:
    """Return the H1 sections keyed by title, plus their order of appearance."""
    sections: Dict[str, List[str]] = {}
    order: List[str] = []
    current: Optional[str] = None
    for line in text.splitlines():
        match = re.match(r"^#\s+(.*\S)\s*$", line)
        if match:
            current = match.group(1).strip()
            order.append(current)
            sections[current] = []
            continue
        if current is not None:
            sections[current].append(line)
    return sections, order


def _frontmatter(text: str) -> Tuple[Dict[str, str], List[LintIssue]]:
    issues: List[LintIssue] = []
    if not text.startswith("---\n"):
        return {}, [LintIssue("error", "缺少 YAML frontmatter（文件必须以 `---` 开头）")]
    parts = text.split("---\n", 2)
    if len(parts) < 3:
        return {}, [LintIssue("error", "frontmatter 没有闭合")]
    fields: Dict[str, str] = {}
    for line in parts[1].splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip().strip('"')
    name = fields.get("name", "")
    if not name:
        issues.append(LintIssue("error", "frontmatter 缺少 name"))
    elif not NAME_RE.match(name):
        issues.append(LintIssue("error", f"name 必须是英文 slug（^[a-z0-9-]+$），当前为 {name!r}"))
    if not fields.get("description"):
        issues.append(LintIssue("error", "frontmatter 缺少 description"))
    elif len(fields["description"]) > 1024:
        issues.append(LintIssue("error", "description 超过 1024 字符"))
    return fields, issues


def _commands_in(lines: Sequence[str]) -> List[str]:
    """Inline code spans that look like a command rather than a field name."""
    commands: List[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(("-", "*", "1.", "2.", "3.", "4.")) and "CLI" not in stripped:
            continue
        if "CLI" not in stripped and "命令" not in stripped and not stripped.startswith(("-", "*")):
            continue
        for span in CODE_RE.findall(line):
            if " " in span or "<" in span:
                commands.append(span)
    return commands


def _jump_lines(body: Sequence[str]) -> List[str]:
    """Just the 跳转信息 block — a CLI line may legitimately cite a precheck step."""
    lines: List[str] = []
    inside = False
    for line in body:
        if "跳转信息" in line:
            inside = True
            continue
        if "根因定位" in line:
            inside = False
        if inside:
            lines.append(line)
    return lines


def _table_rows(lines: Sequence[str]) -> List[List[str]]:
    rows: List[List[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def lint_text(text: str) -> LintResult:
    """Check one SKILL.md body against the template."""
    issues: List[LintIssue] = []
    _, frontmatter_issues = _frontmatter(text)
    issues += frontmatter_issues

    sections, order = _split_sections(text)
    if order != list(SECTIONS):
        issues.append(
            LintIssue(
                "error",
                "一级标题必须依次为 " + " → ".join(f"# {s}" for s in SECTIONS) + f"，当前为 {order}",
            )
        )
    missing = [name for name in SECTIONS if name not in sections]
    if missing:
        issues.append(LintIssue("error", "缺少章节：" + "、".join(missing)))
        return LintResult(issues)

    # -- 入参列表 -------------------------------------------------------
    param_rows = _table_rows(sections["入参列表"])[1:]  # drop the header row
    declared: Dict[str, bool] = {}
    for row in param_rows:
        if len(row) < 3:
            issues.append(LintIssue("error", f"入参列表行格式不对：{row}"))
            continue
        declared[param_key(row[0])] = row[1].strip() in ("是", "必填", "Y", "yes")

    # -- 前置检查 -------------------------------------------------------
    precheck_lines = sections["前置检查"]
    if any(JUMP_RE.search(line) and "顺序" not in line for line in precheck_lines):
        issues.append(LintIssue("error", "前置检查不允许跳转到其他步骤"))
    precheck_params: Set[str] = set()
    for command in _commands_in(precheck_lines):
        for token in PARAM_RE.findall(command):
            key = param_key(token)
            precheck_params.add(key)
            if key not in declared:
                issues.append(LintIssue("error", f"前置检查命令用了未在入参列表声明的参数 <{token}>"))
            elif not declared[key]:
                issues.append(
                    LintIssue("error", f"前置检查命令的参数 <{token}> 在入参列表里被标为“否”，必须是必填")
                )

    # -- 排查步骤 -------------------------------------------------------
    step_lines = sections["排查步骤"]
    step_numbers: List[int] = []
    step_bodies: Dict[int, List[str]] = {}
    current: Optional[int] = None
    for line in step_lines:
        match = STEP_RE.match(line)
        if match:
            current = int(match.group(1))
            step_numbers.append(current)
            step_bodies[current] = []
            continue
        if current is not None:
            step_bodies[current].append(line)
    if step_numbers and step_numbers != list(range(1, len(step_numbers) + 1)):
        issues.append(LintIssue("error", f"步骤编号必须从 1 起连续，当前为 {step_numbers}"))

    declared_causes: Set[str] = set()
    for number, body in step_bodies.items():
        joined = "\n".join(body)
        for item in STEP_ITEMS:
            if item not in joined:
                issues.append(LintIssue("error", f"步骤{number} 缺少「{item}」"))
        for target in JUMP_RE.findall("\n".join(_jump_lines(body))):
            if int(target) not in step_bodies:
                issues.append(LintIssue("error", f"步骤{number} 跳转到不存在的步骤 {target}"))
        for command in _commands_in(body):
            for token in PARAM_RE.findall(command):
                if param_key(token) not in declared:
                    issues.append(LintIssue("error", f"步骤{number} 用了未在入参列表声明的参数 <{token}>"))
        in_causes = False
        for line in body:
            if "根因定位" in line:
                in_causes = True
                continue
            if in_causes:
                stripped = line.strip()
                if stripped.startswith("-"):
                    cause = stripped.lstrip("-").strip()
                    if cause and cause != "无":
                        declared_causes.add(cause)
                elif stripped:
                    in_causes = False
    if step_bodies:
        last = "\n".join(step_bodies[max(step_bodies)])
        if NOT_FOUND not in last:
            issues.append(LintIssue("error", f"最后一步必须写清全部判据不命中时判定“{NOT_FOUND}”"))

    # 前置检查里判定的根因也要进对照表
    for line in precheck_lines:
        for match in re.finditer(r"判定根因为[“\"]([^”\"]+)[”\"]", line):
            declared_causes.add(match.group(1))

    # -- 根因对照表 -----------------------------------------------------
    table_rows = _table_rows(sections["根因对照表"])[1:]
    listed = {row[0].strip() for row in table_rows if row}
    for row in table_rows:
        if len(row) < 4:
            issues.append(LintIssue("error", f"根因对照表行格式不对（需要 4 列）：{row}"))
    for cause in sorted(declared_causes):
        if cause not in listed:
            issues.append(LintIssue("error", f"根因“{cause}”没有在根因对照表里逐字出现"))
    if NOT_FOUND not in listed:
        issues.append(LintIssue("error", f"根因对照表缺少「{NOT_FOUND}」行"))

    # -- 全文格式 -------------------------------------------------------
    for command in _commands_in(text.splitlines()):
        if BAD_PLACEHOLDER_RE.search(command):
            issues.append(LintIssue("error", f"命令里的占位符必须用 <>：`{command}`"))
        if re.search(r"\bXXX+\b", command):
            issues.append(LintIssue("warning", f"命令里保留了大写占位符：`{command}`"))
        literals = case_literals(command)
        if literals:
            issues.append(
                LintIssue(
                    "warning",
                    f"含案例字面量（{'、'.join(literals[:3])}）：`{command}`；"
                    "换一张网就不成立，应替换为入参或剔除该案例条目",
                )
            )
        if SHORT_INTERFACE_RE.search(command):
            issues.append(LintIssue("warning", f"接口名疑似缩写，应使用全称：`{command}`"))
    if len(step_bodies) > MAX_REASONABLE_STEPS:
        issues.append(
            LintIssue(
                "warning",
                f"共 {len(step_bodies)} 个排查步骤，超出可读范围（>{MAX_REASONABLE_STEPS}）；"
                "多半是把多个故障场景合成了一份，建议按诊断单元拆分（build --unit）",
            )
        )

    repeats: Dict[str, int] = {}
    for command in _commands_in(precheck_lines):
        signature = command_signature([command])
        repeats[signature] = repeats.get(signature, 0) + 1
    for signature, count in repeats.items():
        if count > MAX_COMMAND_REPEATS:
            issues.append(
                LintIssue("warning", f"前置检查里 `{signature}` 重复了 {count} 次，应合并为一条采集步骤")
            )

    for row in table_rows:
        if len(row) >= 3 and "无直接修复CLI" in row[2]:
            issues.append(LintIssue("warning", f"根因“{row[0]}”来源未给出修复命令，只能定位"))
        for cell in row[2:4] if len(row) >= 4 else []:
            for command in CODE_RE.findall(cell):
                for token in PARAM_RE.findall(command):
                    if param_key(token) not in declared:
                        issues.append(
                            LintIssue("error", f"根因对照表用了未在入参列表声明的参数 <{token}>")
                        )
    return LintResult(issues)


def lint_path(path: Path) -> LintResult:
    """Lint a ``SKILL.md`` or a skill directory containing one."""
    path = Path(path)
    target = path / "SKILL.md" if path.is_dir() else path
    if not target.exists():
        return LintResult([LintIssue("error", f"{target}: 文件不存在")])
    return lint_text(target.read_text(encoding="utf-8"))
