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
from subkg2skill.commands import PARAM_RE, case_literals, command_signature, param_key
from subkg2skill.doc import CONDITION_RE, HANDOFF_LABELS, NO_SKILL, NOT_FOUND
from subkg2skill.playbook import fault_key

CAUSE_TABLE = "根因对照表"
SECTIONS = ("入参列表", "前置检查", "排查步骤", CAUSE_TABLE)
#: A multi-scenario skill's entry file: the steps live under ``reference/``.
INDEX_SECTIONS = ("入参列表", "前置检查", "排查步骤")
#: ``# 场景A：BGP邻居无法建立`` — the first heading of a scenario's own file.
SCENARIO_H1_RE = re.compile(r"^场景\s*([A-Za-z0-9]+)\s*[：:]\s*(.+?)\s*$")
#: ``| 场景A：… | reference/neighbor-down.md | … |`` — the index's pointer table.
REFERENCE_PATH_RE = re.compile(r"^reference/([A-Za-z0-9][A-Za-z0-9-]*)\.md$")
#: ``scenario-a`` / ``scenario-a16`` — the scenario's label spelled out, which
#: tells a reader nothing about which fault is in the file.
PLACEHOLDER_SLUG_RE = re.compile(r"^scenario-[a-z]\d*$")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
#: ``（skill: isis-neighbor-down）`` — the skill a hand-off points at.  A name
#: that is not a slug names nothing the reader can open.
SKILL_REF_RE = re.compile(r"skill:\s*([^），]+)")
#: A hand-off whose fault no skill in this batch covers.
DANGLING_HANDOFF_RE = re.compile(
    "(?:" + "|".join(re.escape(label) for label in HANDOFF_LABELS.values()) + r")[：:]\s*「([^」]+)」"
    r"（" + re.escape(NO_SKILL)
)
#: ``## 步骤N`` in a single-fault document, ``#### 步骤N`` inside a scenario.
STEP_RE = re.compile(r"^(#{2,4})\s*步骤\s*(\d+)\s*[：:]\s*(.+?)\s*$")
SCENARIO_RE = re.compile(r"^###\s*场景\s*([A-Za-z0-9]+)\s*[：:]\s*(.+?)\s*$")
ROUTING_HEADING = "场景跳转表"
#: The same table one level down, in a document covering a single fault.
STEP_ROUTING_HEADING = "步骤跳转表"
#: ``- 适用步骤：步骤 3：…`` names who reads a collection step, it is not a jump.
AUDIENCE_RE = re.compile(r"适用(?:场景|步骤)")
#: A scenario's own collection phase, rendered inside 排查步骤.
COLLECTION_HEADING = "本场景采集"
#: “步骤 N” as a jump target — “前置检查步骤 N” is a back-reference, not a jump.
JUMP_RE = re.compile(r"(?<!前置检查)步骤\s*(\d+)")
CODE_RE = re.compile(r"`([^`]+)`")
#: Table cells are separated by unescaped pipes; ``\|`` is a literal one.
CELL_SPLIT_RE = re.compile(r"(?<!\\)\|")
#: Only ``{}`` is a stray placeholder; ``[ ... ]`` is CLI optional-argument syntax.
BAD_PLACEHOLDER_RE = re.compile(r"\{[A-Za-z0-9_\-一-鿿]+\}")
#: Beyond this many steps in one path a reader stops following; split it up.
MAX_REASONABLE_STEPS = 25
#: A scenario the frontmatter never names is one the agent will not pick this
#: skill for — the section exists, but nothing routes to it.
DESCRIPTION_NAMED_SCENARIOS = 6
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


#: Lines whose code spans are readings or source text, never commands.
_NOT_COMMAND_LINES = ("根因定位", "采集内容", "适用条件", "执行条件", "适用步骤", "适用场景")


def _commands_in(lines: Sequence[str]) -> List[str]:
    """Inline code spans that look like a command rather than a field name.

    Criteria in 跳转信息, 根因定位 and 采集内容 are code spans too, and an
    observation may read ``x <br> y``; only command lines and the repair /
    recheck columns of the root-cause table carry commands.
    """
    commands: List[str] = []
    in_jump = False
    for line in lines:
        stripped = line.strip()
        if "跳转信息" in stripped:
            in_jump = True
            continue
        if in_jump and ("根因定位" in stripped or stripped.startswith("#")):
            in_jump = False
        if in_jump:
            continue
        if stripped.startswith("|"):
            rows = _table_rows([line])
            if rows and len(rows[0]) >= 4:
                for column in rows[0][2:4]:
                    commands += [span for span in CODE_RE.findall(column) if " " in span or "<" in span]
            continue
        if any(mark in stripped for mark in _NOT_COMMAND_LINES):
            continue
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
            numbers = seen.setdefault(criterion, [])
            if number not in numbers:
                numbers.append(number)
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


#: A root cause named in a jump line, ``“…”`` — its words are not instructions.
QUOTED_RE = re.compile(r"“[^”]*”")
#: ``场景B：<症状名>`` up to the closing bracket — a scenario's name is the symptom's.
SCENARIO_REF_RE = re.compile(r"场景[A-Za-z0-9]+[：:][^）\n]*")
#: ``**title**`` — titles are node names, i.e. source text.
BOLD_RE = re.compile(r"\*\*[^*]+\*\*")


def _instructions(line: str) -> str:
    """*line* without the source text in it — what is left is the document's own wording.

    A condition, a quoted root cause or a code span can say "步骤 3" or
    ``<br>``; neither is a jump nor a parameter the document asks for.
    """
    line = CONDITION_RE.sub(" ", line)
    line = QUOTED_RE.sub(" ", line)
    line = BOLD_RE.sub(" ", line)  # a collection step's title is the check's own name
    line = SCENARIO_REF_RE.sub(" ", line)  # 「（场景B：<症状名>）」 names a scenario by its source name
    return CODE_RE.sub(" ", line)


def _routing_target(cell: str) -> str:
    """``→ **场景A：名称**`` → ``场景A：名称``; a ``*`` inside the name stays."""
    text = cell.strip().lstrip("→").strip()
    if text.startswith("**") and text.endswith("**") and len(text) >= 4:
        text = text[2:-2]
    return re.sub(r"\s", "", text)


def _table_rows(lines: Sequence[str]) -> List[List[str]]:
    """Split markdown table rows into cells, honouring escaped pipes.

    Source prose carries ``|`` characters and the renderer escapes them as
    ``\|``; splitting on every pipe would invent an extra column and shift
    every check that reads a cell by index.
    """
    rows: List[List[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        body = stripped[1:]
        if body.endswith("|") and not body.endswith("\\|"):
            body = body[:-1]
        cells = [cell.strip().replace("\\|", "|") for cell in CELL_SPLIT_RE.split(body)]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def _reference_table(step_lines: Sequence[str]) -> Tuple[List[str], List[LintIssue]]:
    """The index's 场景 → 参考文件 table: which scenarios exist and where they live.

    This table is the only thing tying the entry file to the scenario files, so
    a row pointing at a path that is not ``reference/<slug>.md`` sends the
    reader nowhere.
    """
    issues: List[LintIssue] = []
    scenarios: List[str] = []
    seen: Set[str] = set()
    for row in _table_rows(step_lines)[1:]:  # drop the header row
        if len(row) < 2 or not row[0].strip():
            continue
        title = row[0].strip()
        if not SCENARIO_H1_RE.match(title):
            issues.append(
                LintIssue("error", f"参考文件表的场景名必须写成「场景X：名称」，当前为 {title!r}")
            )
            continue
        path = row[1].strip().strip("`")
        reference = REFERENCE_PATH_RE.match(path)
        if not reference:
            issues.append(
                LintIssue(
                    "error",
                    f"{title} 的参考文件必须是 `reference/<英文名>.md`，当前为 {path!r}",
                )
            )
        elif PLACEHOLDER_SLUG_RE.match(reference.group(1)):
            # 文件名是读者和 agent 区分场景的唯一依据，编号不是名字。
            issues.append(
                LintIssue(
                    "error",
                    f"{title} 的参考文件名 {path!r} 只是场景编号，没有语义；"
                    "按该场景的故障含义改成英文名（如 reference/neighbor-down.md）",
                )
            )
        elif path in seen:
            issues.append(LintIssue("error", f"参考文件 {path} 被多个场景共用，一个场景一个文件"))
        seen.add(path)
        scenarios.append(title)
    if not scenarios and not issues:
        issues.append(
            LintIssue(
                "error",
                "多场景入口的「排查步骤」必须用一张表列出每个场景的参考文件（场景 / 参考文件 / 内容）",
            )
        )
    return scenarios, issues


def reference_paths(text: str) -> List[str]:
    """Reference files an index document points at, in the order it lists them."""
    sections, order = _split_sections(text)
    if document_kind(order) != "index":
        return []
    found: List[str] = []
    for row in _table_rows(sections.get("排查步骤", []))[1:]:
        if len(row) >= 2:
            path = row[1].strip().strip("`")
            if REFERENCE_PATH_RE.match(path) and path not in found:
                found.append(path)
    return found


def document_kind(order: Sequence[str]) -> str:
    """Which of the three shapes this file is, by its first-level headings.

    ``skill`` — one fault, all four sections in one file.
    ``index`` — the entry file of a multi-scenario skill: inputs, shared
    collection and the routing table, with the steps one file per scenario.
    ``scenario`` — one of those files: a scenario heading and its root causes.
    """
    listed = list(order)
    if listed == list(SECTIONS):
        return "skill"
    if listed == list(INDEX_SECTIONS):
        return "index"
    if listed and SCENARIO_H1_RE.match(listed[0]) and CAUSE_TABLE in listed:
        return "scenario"
    return ""


def lint_text(text: str, *, declared: Optional[Dict[str, bool]] = None) -> LintResult:
    """Check one delivered file against the template.

    ``declared`` carries the 入参列表 of the skill a scenario file belongs to:
    its commands use those parameters, but the table itself lives in
    ``SKILL.md``.  :func:`lint_path` supplies it; on its own a scenario file
    can only be checked for everything else.
    """
    issues: List[LintIssue] = []
    frontmatter, frontmatter_issues = _frontmatter(text)
    issues += frontmatter_issues

    sections, order = _split_sections(text)
    kind = document_kind(order)
    if not kind:
        issues.append(
            LintIssue(
                "error",
                "一级标题必须是以下三种之一："
                + "；".join(
                    (
                        "单故障 " + " → ".join(f"# {s}" for s in SECTIONS),
                        "多场景入口 " + " → ".join(f"# {s}" for s in INDEX_SECTIONS),
                        f"场景参考文件 `# 场景X：…` → `# {CAUSE_TABLE}`",
                    )
                )
                + f"，当前为 {order}",
            )
        )
        return LintResult(issues)

    # -- 入参列表 -------------------------------------------------------
    # 场景参考文件没有这一节：入参声明在它所属 skill 的 SKILL.md 里。
    param_rows = _table_rows(sections["入参列表"])[1:] if "入参列表" in sections else []
    declared = dict(declared or {})
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
    precheck_lines = sections.get("前置检查", [])
    # 场景跳转表就在这一节里，但它是分流表不是采集步骤，两类检查要分开
    routing_start = next(
        (
            index
            for index, line in enumerate(precheck_lines)
            if line.strip().startswith("##")
            and (ROUTING_HEADING in line or STEP_ROUTING_HEADING in line)
        ),
        len(precheck_lines),
    )
    collection_lines = precheck_lines[:routing_start]
    if any(
        JUMP_RE.search(_instructions(line)) and "顺序" not in line and not AUDIENCE_RE.search(line)
        for line in collection_lines
    ):
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
    # 场景参考文件把步骤放在它自己的 `# 场景X：…` 标题下，那就是它的排查步骤一节。
    step_lines = sections[order[0]] if kind == "scenario" else sections.get("排查步骤", [])
    scenarios: List[str] = []          # 场景标题，按出现顺序
    step_bodies: Dict[str, Dict[int, List[str]]] = {}   # 场景 -> 步骤号 -> 正文
    order: Dict[str, List[int]] = {}
    current_scenario = ""
    current_step: Optional[int] = None
    step_bodies[""] = {}
    order[""] = []
    #: 场景自己的采集块：住在排查步骤一节里，形状和前置检查一样
    collection: Dict[str, List[str]] = {}
    in_collection = False
    for line in step_lines:
        scenario_match = SCENARIO_RE.match(line)
        if scenario_match:
            current_scenario = f"场景{scenario_match.group(1)}：{scenario_match.group(2)}"
            scenarios.append(current_scenario)
            step_bodies.setdefault(current_scenario, {})
            order.setdefault(current_scenario, [])
            current_step = None
            in_collection = False
            continue
        if COLLECTION_HEADING in line and "复用" not in line:
            in_collection = True
            collection.setdefault(current_scenario, [])
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
        elif in_collection:
            collection.setdefault(current_scenario, []).append(line)

    if kind == "index":
        # 入口文件的场景来自「排查步骤」里的参考文件表——步骤在 reference/ 下，
        # 这份文件里没有 `### 场景X` 标题可数。
        scenarios, reference_issues = _reference_table(step_lines)
        issues += reference_issues

    if scenarios and step_bodies[""]:
        issues.append(LintIssue("error", "分场景时所有步骤都必须落在某个 `### 场景X：…` 下"))

    # 一个场景在 description 里没名字，agent 就不会为它选中这份 skill：
    # 正文有内容，检索却到不了，等于没覆盖。
    description = frontmatter.get("description", "")
    if scenarios and description:
        unnamed = [
            scenario
            for scenario in scenarios
            if scenario.split("：", 1)[-1].strip() not in description
        ]
        if unnamed:
            listed = "、".join(unnamed[:4])
            issues.append(
                LintIssue(
                    "warning",
                    f"{len(unnamed)} 个场景没写进 description（{listed}…）；"
                    "description 是 agent 选中这份 skill 的唯一依据，"
                    f"名字进不去的场景检索不到——通常说明场景数超过了一份 skill 能承载的量"
                    f"（约 {DESCRIPTION_NAMED_SCENARIOS} 个），该拆成多份",
                )
            )

    # 场景自己的采集，和前置检查一样：参数得是现场能提供的必填项
    for scenario, lines_in_block in collection.items():
        where = f"{scenario} " if scenario else ""
        if any(JUMP_RE.search(_instructions(line)) and "顺序" not in line for line in lines_in_block):
            issues.append(LintIssue("error", f"{where}本场景采集不允许跳转到其他步骤"))
        for command in _commands_in(lines_in_block):
            for token in PARAM_RE.findall(command):
                key = param_key(token)
                if key not in declared:
                    issues.append(
                        LintIssue("error", f"{where}本场景采集用了未在入参列表声明的参数 <{token}>")
                    )
                elif not declared[key]:
                    issues.append(
                        LintIssue(
                            "error",
                            f"{where}本场景采集的参数 <{token}> 在入参列表里被标为“否”，必须是必填",
                        )
                    )

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
            for target in JUMP_RE.findall("\n".join(_instructions(line) for line in _jump_lines(body))):
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

    # -- 步骤跳转表（单场景）---------------------------------------------
    # 分流表指向排查步骤时，目标必须真实存在，且同一条判据不能指向两个步骤
    if kind == "skill" and not scenarios and routing_start < len(precheck_lines):
        step_rows = _table_rows(precheck_lines[routing_start:])[1:]
        by_criterion: Dict[str, Set[str]] = {}
        for row in step_rows:
            if len(row) < 3:
                issues.append(LintIssue("error", f"{STEP_ROUTING_HEADING}行格式不对（需要 3 列）：{row}"))
                continue
            # "→ **步骤 N：检查<原因名>**": only the leading N is the target; the
            # name after it is source text and may say anything.
            targets = JUMP_RE.findall(row[2])[:1]
            if not targets:
                issues.append(
                    LintIssue("error", f"{STEP_ROUTING_HEADING}的跳转目标必须写成「步骤 N」：{row[2]}")
                )
            for target in targets:
                if int(target) not in step_bodies.get("", {}):
                    issues.append(
                        LintIssue("error", f"{STEP_ROUTING_HEADING}指向了不存在的步骤 {target}")
                    )
            by_criterion.setdefault(row[1].strip(), set()).update(targets)
        for criterion, targets in by_criterion.items():
            if len(targets) > 1:
                issues.append(
                    LintIssue(
                        "warning",
                        f"{STEP_ROUTING_HEADING}里 {criterion} 同时指向步骤 "
                        + "、".join(sorted(targets))
                        + "，无法据此分流",
                    )
                )

    # -- 场景跳转表 -----------------------------------------------------
    routing_rows: List[List[str]] = []
    if scenarios:
        routing_rows = _table_rows(precheck_lines[routing_start:])[1:]
        if not routing_rows:
            issues.append(
                LintIssue("error", f"分场景时前置检查后必须有「{ROUTING_HEADING}」，说明每条判据进入哪个场景")
            )
        routed = {_routing_target(row[2]) for row in routing_rows if len(row) >= 3}
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
    # 入口文件没有这一节：根因随步骤一起住在各自的场景参考文件里。
    table_lines = sections.get(CAUSE_TABLE, [])
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
    if NOT_FOUND not in listed and kind != "index":
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

    # -- 跨 skill 的转向 -------------------------------------------------
    # 转向写的是另一份 skill 的名字，得是这个框架装得进去的 slug；写成中文或
    # 带空格，读者照着找不到东西可开。
    for slug in dict.fromkeys(SKILL_REF_RE.findall(text)):
        if not NAME_RE.match(slug.strip()):
            issues.append(
                LintIssue(
                    "error",
                    f"转向引用的 skill 名 {slug.strip()!r} 不是合法 slug（^[a-z0-9-]+$）；"
                    "按目标 skill 的 frontmatter name 写",
                )
            )
    dangling = dict.fromkeys(DANGLING_HANDOFF_RE.findall(text))
    if dangling:
        issues.append(
            LintIssue(
                "warning",
                f"{len(dangling)} 处转向的故障本批次没有对应 skill（"
                + "、".join(list(dangling)[:4])
                + "）；读者走到这里就断了——补生成这些故障的 skill，"
                "或在交付时说清楚这几条线索到此为止",
            )
        )

    # -- 全文格式 -------------------------------------------------------
    for command in _commands_in(text.splitlines()):
        if BAD_PLACEHOLDER_RE.search(command):
            issues.append(LintIssue("error", f"命令里的占位符必须用 <>：`{command}`"))
        if re.search(r"\bXXX+\b", command):
            issues.append(LintIssue("warning", f"命令里保留了大写占位符：`{command}`"))
        for token in PARAM_RE.findall(command):
            hashes = [part for part in re.split(r"[\s_\-]+", token) if hygiene.is_hash_token(part)]
            if hashes:
                issues.append(
                    LintIssue(
                        "error",
                        f"参数 <{token}> 带抽取哈希（{'、'.join(hashes)}），现场敲不出这个 id；"
                        f"去掉写成 <{hygiene.generalise_slot(token) or token}>",
                    )
                )
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
    # 可读性看的是**读者实际要走的那条路**：单故障文档是全篇，多场景文档是一个场景。
    # 全篇步骤数由覆盖多少根因决定，不该用同一把尺子卡。
    for scenario, bodies in step_bodies.items():
        if len(bodies) > MAX_REASONABLE_STEPS:
            where = f"{scenario} " if scenario else ""
            issues.append(
                LintIssue(
                    "warning",
                    f"{where}共 {len(bodies)} 个排查步骤，超出可读范围（>{MAX_REASONABLE_STEPS}）；"
                    "多半是把多个故障场景合成了一步步的长队，建议按诊断单元拆分（build --unit）"
                    "或拆成多个场景",
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


def declared_params(text: str) -> Dict[str, bool]:
    """The 入参列表 of a document, as ``参数 -> 是否必填``."""
    sections, _order = _split_sections(text)
    declared: Dict[str, bool] = {}
    for row in _table_rows(sections.get("入参列表", []))[1:]:
        if len(row) >= 2 and row[0].strip():
            declared[param_key(row[0])] = row[1].strip() in ("是", "必填", "Y", "yes")
    return declared


def lint_files(files: Dict[str, str]) -> LintResult:
    """Lint a whole delivered skill: the entry file and every scenario file.

    A scenario file's commands are written against the 入参列表 in ``SKILL.md``,
    and the entry file promises a file per scenario — neither can be checked
    from one document alone, so the package is checked as a whole.
    """
    entry = files.get("SKILL.md")
    if entry is None:
        return LintResult([LintIssue("error", "交付目录里没有 SKILL.md")])
    issues = list(lint_text(entry).issues)
    declared = declared_params(entry)
    promised = reference_paths(entry)
    delivered = sorted(path for path in files if path != "SKILL.md")

    for path in promised:
        if path not in files:
            issues.append(LintIssue("error", f"SKILL.md 指向的参考文件不存在：{path}"))
    for path in delivered:
        if path not in promised:
            # 没人指向它，读者就永远到不了——比内容有错更隐蔽。
            issues.append(
                LintIssue("error", f"{path} 没有出现在 SKILL.md 的参考文件表里，读者到不了它")
            )
    for path in delivered:
        for issue in lint_text(files[path], declared=declared).issues:
            issues.append(LintIssue(issue.level, f"{path}: {issue.message}"))
    return LintResult(issues)


def lint_path(path: Path) -> LintResult:
    """Lint a ``SKILL.md``, or a skill directory with its ``reference/`` files."""
    path = Path(path)
    if not path.is_dir():
        if not path.exists():
            return LintResult([LintIssue("error", f"{path}: 文件不存在")])
        return lint_text(path.read_text(encoding="utf-8"))
    entry = path / "SKILL.md"
    if not entry.exists():
        return LintResult([LintIssue("error", f"{entry}: 文件不存在")])
    files = {"SKILL.md": entry.read_text(encoding="utf-8")}
    for child in sorted((path / "reference").glob("*.md")) if (path / "reference").is_dir() else []:
        files[f"reference/{child.name}"] = child.read_text(encoding="utf-8")
    return lint_files(files)
