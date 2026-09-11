"""Check a generated skill against the house template.

The generator already writes conforming documents; the linter exists so a
hand-edited skill (or one produced another way) can be checked before it ships,
and so ``build`` can verify its own output.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from subkg2skill import hygiene
from subkg2skill.playbook import fault_key
from subkg2skill.template import NOT_FOUND, PARAM_RE, case_literals, command_signature, param_key

SECTIONS = ("入参列表", "前置检查", "排查步骤", "根因对照表")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
#: ``## 步骤N`` in a single-fault document, ``#### 步骤N`` inside a scenario.
STEP_RE = re.compile(r"^(#{2,4})\s*步骤\s*(\d+)\s*[：:]\s*(.+?)\s*$")
SCENARIO_RE = re.compile(r"^###\s*场景\s*([A-Za-z0-9]+)\s*[：:]\s*(.+?)\s*$")
ROUTING_HEADING = "场景跳转表"
#: “步骤 N” as a jump target — “前置检查步骤 N” is a back-reference, not a jump.
JUMP_RE = re.compile(r"(?<!前置检查)步骤\s*(\d+)")
CODE_RE = re.compile(r"`([^`]+)`")
#: Only ``{}`` is a stray placeholder; ``[ ... ]`` is CLI optional-argument syntax.
BAD_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z0-9_\-一-鿿]+\}")
#: Beyond this many steps a document stops being followable; split by diagnostic unit.
MAX_REASONABLE_STEPS = 25
#: The same command collected this many times means the prechecks were not merged.
MAX_COMMAND_REPEATS = 2
#: Root-cause names this similar are usually one cause written twice.
NEAR_DUPLICATE_RATIO = 0.8
#: A criterion shared by more than this many steps is restating the entry condition.
MAX_CRITERION_REUSE = 2
#: ``- `判据`：定位根因“X”`` — the deciding half of a 跳转信息 row.
CRITERION_RE = re.compile(r"^\s*[-*]\s*(?P<criterion>.+?)\s*[：:]\s*(?P<outcome>.+?)\s*$")
#: A parameter the engineer brings with them, rather than reads off a screen.
FIELD_SUPPLIED_RE = re.compile(r"现场|用户提供|人工提供")
#: Wording the generator uses for a branch that decides nothing on its own.
FALLTHROUGH_CRITERIA = ("以上判据均不命中", "本子图未给出该原因的判定观测")
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


def _criteria(body: Sequence[str]) -> List[Tuple[str, str]]:
    """The (判据, 结论) pairs of one step's 跳转信息 block."""
    pairs: List[Tuple[str, str]] = []
    for line in _jump_lines(body):
        match = CRITERION_RE.match(line)
        if not match:
            continue
        criterion = match.group("criterion").strip()
        if criterion and criterion not in FALLTHROUGH_CRITERIA:
            pairs.append((criterion, match.group("outcome").strip()))
    return pairs


def _criterion_issues(step_bodies: Dict[int, List[str]], *, scenario: str = "") -> List[LintIssue]:
    """Criteria that cannot tell one root cause from another.

    Three shapes, all invisible to a section-structure check and all leaving an
    agent to guess:

    * one criterion repeated across many steps of one scenario — it is the
      condition for entering that scenario at all (``BGP邻居状态 !=
      Established``), so it contributes nothing to telling those steps apart;
    * a criterion and its negation leading to the same place, which is a row
      that can be deleted without changing anything;
    * a step whose every criterion is a restatement of its own name, with no
      field from a command's output bound to it.
    """
    issues: List[LintIssue] = []
    where = f"{scenario} " if scenario else ""
    seen: Dict[str, List[int]] = {}
    for number in sorted(step_bodies):
        pairs = _criteria(step_bodies[number])
        for criterion, outcome in pairs:
            seen.setdefault(criterion, []).append(number)
        # A criterion and its negation that land in the same place decide nothing.
        for index, (criterion, outcome) in enumerate(pairs):
            for other, other_outcome in pairs[index + 1 :]:
                if outcome != other_outcome:
                    continue
                if _is_negation(criterion, other):
                    issues.append(
                        LintIssue(
                            "error",
                            f"{where}步骤{number} 的「{criterion}」与「{other}」互为正反却跳到同一处，"
                            "这一行删掉不影响任何判断",
                        )
                    )
    for criterion, numbers in seen.items():
        if len(numbers) > MAX_CRITERION_REUSE:
            issues.append(
                LintIssue(
                    "error",
                    f"{where}判据「{criterion}」被步骤 {'、'.join(map(str, numbers))} 共用，"
                    "对区分根因没有贡献（多半是入场条件的重述）；每步应绑定回显里的一个具体字段",
                )
            )
    return issues


def _is_negation(left: str, right: str) -> bool:
    """True when two criteria are the same statement with opposite polarity."""
    negations = (("存在", "不存在"), ("有", "没有"), ("==", "!="), ("是", "不是"), ("为", "不为"))
    for positive, negative in negations:
        if negative in left and positive in right and left.replace(negative, positive) == right:
            return True
        if negative in right and positive in left and right.replace(negative, positive) == left:
            return True
    return False


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
        if hygiene.is_topology_label(row[0]):
            issues.append(
                LintIssue(
                    "error",
                    f"入参「{row[0]}」是来源示意图的设备编号，现场填不出来；"
                    "删掉它，把用到它的判据改成角色描述（如“发生错误优选的设备”）",
                )
            )

    # A parameter nobody references is a barrier to entry, not an input.
    # Repairs live in a table, so every code span counts, not just command lines.
    body_params = {
        param_key(token) for span in CODE_RE.findall(text) for token in PARAM_RE.findall(span)
    }
    for row in param_rows:
        if len(row) < 3 or param_key(row[0]) in body_params:
            continue
        if hygiene.is_topology_label(row[0]):
            continue  # already reported, with a better reason
        if FIELD_SUPPLIED_RE.search(row[2]):
            # Which device, which interface — brought by the engineer, and not
            # every source spells it the way its own commands do.
            continue
        issues.append(
            LintIssue(
                "warning",
                f"入参「{row[0]}」在正文的任何命令里都没有被引用；确认它是排查真正需要的信息，否则删掉",
            )
        )

    # -- 前置检查 -------------------------------------------------------
    precheck_lines = sections["前置检查"]
    # 场景跳转表就在这一节里，但它是分流表不是采集步骤，两类检查要分开
    routing_start = next(
        (
            index
            for index, line in enumerate(precheck_lines)
            if line.strip().startswith("##") and ROUTING_HEADING in line
        ),
        len(precheck_lines),
    )
    collection_lines = precheck_lines[:routing_start]
    if any(JUMP_RE.search(line) and "顺序" not in line for line in collection_lines):
        issues.append(LintIssue("error", "前置检查不允许跳转到其他步骤"))
    precheck_params: Set[str] = set()
    for command in _commands_in(collection_lines):
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
    scenarios: List[str] = []          # 场景标题，按出现顺序
    step_bodies: Dict[str, Dict[int, List[str]]] = {}   # 场景 -> 步骤号 -> 正文
    order: Dict[str, List[int]] = {}
    current_scenario = ""
    current_step: Optional[int] = None
    step_bodies[""] = {}
    order[""] = []
    for line in step_lines:
        scenario_match = SCENARIO_RE.match(line)
        if scenario_match:
            current_scenario = f"场景{scenario_match.group(1)}：{scenario_match.group(2)}"
            scenarios.append(current_scenario)
            step_bodies.setdefault(current_scenario, {})
            order.setdefault(current_scenario, [])
            current_step = None
            continue
        match = STEP_RE.match(line)
        if match:
            level, number = match.group(1), int(match.group(2))
            if scenarios and level != "####":
                issues.append(
                    LintIssue("error", f"分场景时步骤标题应为四级（`#### 步骤{number}：…`），当前为 `{level}`")
                )
            if not scenarios and level != "##":
                issues.append(
                    LintIssue("error", f"未分场景时步骤标题应为二级（`## 步骤{number}：…`），当前为 `{level}`")
                )
            current_step = number
            step_bodies[current_scenario][number] = []
            order[current_scenario].append(number)
            continue
        if current_step is not None:
            step_bodies[current_scenario][current_step].append(line)

    if scenarios and step_bodies[""]:
        issues.append(LintIssue("error", "分场景时所有步骤都必须落在某个 `### 场景X：…` 下"))

    declared_causes: Dict[str, Set[str]] = {}
    for scenario in scenarios or [""]:
        numbers = order.get(scenario, [])
        if numbers and numbers != list(range(1, len(numbers) + 1)):
            where = f"{scenario} " if scenario else ""
            issues.append(LintIssue("error", f"{where}步骤编号必须从 1 起连续，当前为 {numbers}"))
        bodies = step_bodies.get(scenario, {})
        found: Set[str] = set()
        for number, body in bodies.items():
            joined = "\n".join(body)
            for item in STEP_ITEMS:
                if item not in joined:
                    where = f"{scenario} " if scenario else ""
                    issues.append(LintIssue("error", f"{where}步骤{number} 缺少「{item}」"))
            for target in JUMP_RE.findall("\n".join(_jump_lines(body))):
                if int(target) not in bodies:
                    where = f"{scenario} " if scenario else ""
                    issues.append(
                        LintIssue("error", f"{where}步骤{number} 跳转到不存在的步骤 {target}")
                    )
            for command in _commands_in(body):
                for token in PARAM_RE.findall(command):
                    if param_key(token) not in declared:
                        where = f"{scenario} " if scenario else ""
                        issues.append(
                            LintIssue("error", f"{where}步骤{number} 用了未在入参列表声明的参数 <{token}>")
                        )
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
                            found.add(cause)
                    elif stripped:
                        in_causes = False
        if bodies:
            last = "\n".join(bodies[max(bodies)])
            if NOT_FOUND not in last:
                where = f"{scenario} 的" if scenario else ""
                issues.append(
                    LintIssue("error", f"{where}最后一步必须写清全部判据不命中时判定“{NOT_FOUND}”")
                )
        issues += _criterion_issues(bodies, scenario=scenario)
        declared_causes[scenario] = found

    total_steps = sum(len(bodies) for bodies in step_bodies.values())
    if total_steps > MAX_REASONABLE_STEPS and not scenarios:
        issues.append(
            LintIssue(
                "warning",
                f"共 {total_steps} 个排查步骤，超出可读范围（>{MAX_REASONABLE_STEPS}）；"
                "多半是把多个故障场景合成了一份，建议按诊断单元拆分（build --unit）或分场景（### 场景X）",
            )
        )

    repeats: Dict[str, int] = {}
    for command in _commands_in(collection_lines):
        signature = command_signature([command])
        repeats[signature] = repeats.get(signature, 0) + 1
    for signature, count in repeats.items():
        if count > MAX_COMMAND_REPEATS:
            issues.append(
                LintIssue("warning", f"前置检查里 `{signature}` 重复了 {count} 次，应合并为一条采集步骤")
            )

    # -- 场景跳转表 -----------------------------------------------------
    routing_rows: List[List[str]] = []
    if scenarios:
        routing_rows = _table_rows(precheck_lines[routing_start:])[1:]
        if not routing_rows:
            issues.append(
                LintIssue("error", f"分场景时前置检查后必须有「{ROUTING_HEADING}」，说明每条判据进入哪个场景")
            )
        routed = {
            re.sub(r"[*→\s]", "", row[2]) for row in routing_rows if len(row) >= 3
        }
        for scenario in scenarios:
            if re.sub(r"\s", "", scenario) not in routed:
                issues.append(LintIssue("error", f"{scenario} 没有出现在{ROUTING_HEADING}里"))
        known = {re.sub(r"\s", "", scenario) for scenario in scenarios}
        for target in routed:
            if target not in known:
                issues.append(
                    LintIssue("error", f"{ROUTING_HEADING}指向了不存在的场景：{target}")
                )
        by_precheck: Dict[str, List[str]] = {}
        for row in routing_rows:
            if len(row) >= 3:
                by_precheck.setdefault(row[0].strip(), []).append(row[1].strip())
        for precheck, criteria in by_precheck.items():
            if len(criteria) != len(set(criteria)):
                issues.append(
                    LintIssue(
                        "warning",
                        f"{ROUTING_HEADING}里 {precheck} 有完全相同的判据指向不同场景，无法据此分流",
                    )
                )

    # -- 根因对照表 -----------------------------------------------------
    table_lines = sections["根因对照表"]
    per_scenario: Dict[str, List[List[str]]] = {}
    if scenarios:
        current = ""
        buckets: Dict[str, List[str]] = {"": []}
        for line in table_lines:
            match = SCENARIO_RE.match(line)
            if match:
                current = f"场景{match.group(1)}：{match.group(2)}"
                buckets[current] = []
                continue
            buckets.setdefault(current, []).append(line)
        per_scenario = {name: _table_rows(lines)[1:] for name, lines in buckets.items()}
    table_rows = _table_rows(table_lines)[1:]
    listed = {row[0].strip() for row in table_rows if row}
    for row in table_rows:
        if len(row) < 4:
            issues.append(LintIssue("error", f"根因对照表行格式不对（需要 4 列）：{row}"))

    for scenario, causes in declared_causes.items():
        scope = {row[0].strip() for row in per_scenario.get(scenario, [])} if scenarios else listed
        for cause in sorted(causes):
            if cause not in (scope or listed):
                where = f"{scenario} 的" if scenario else ""
                issues.append(LintIssue("error", f"{where}根因“{cause}”没有在根因对照表里逐字出现"))
    if NOT_FOUND not in listed:
        issues.append(LintIssue("error", f"根因对照表缺少「{NOT_FOUND}」行"))

    names = [row[0].strip() for row in table_rows if row and row[0].strip() != NOT_FOUND]
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            left_key, right_key = fault_key(left), fault_key(right)
            if not left_key or not right_key or left_key == right_key:
                continue
            contained = left_key in right_key or right_key in left_key
            ratio = SequenceMatcher(None, left_key, right_key).ratio()
            if contained or ratio >= NEAR_DUPLICATE_RATIO:
                issues.append(
                    LintIssue(
                        "warning",
                        f"根因“{left}”与“{right}”写法高度相似，可能是同一根因的两种说法；"
                        "确认是否该合并（修复动作不同就别合）",
                    )
                )

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
        hardcoded = hygiene.hardcoded_literals(command)
        if hardcoded:
            issues.append(
                LintIssue(
                    "warning",
                    f"命令里留着示例取值（{'、'.join(hardcoded[:3])}）：`{command}`；"
                    "换台设备就是错的，应参数化或在采集内容里注明按实际替换",
                )
            )
    if len(step_bodies) > MAX_REASONABLE_STEPS:
        issues.append(
            LintIssue(
                "warning",
                f"共 {len(step_bodies)} 个排查步骤，超出可读范围（>{MAX_REASONABLE_STEPS}）；"
                "多半是把多个故障场景合成了一份，建议按诊断单元拆分（build --unit）",
            )
        )

    repeats: Dict[str, int] = {}
    for command in _commands_in(collection_lines):
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
