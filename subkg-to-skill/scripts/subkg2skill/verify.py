"""Post-generation grounding check: every claim must be findable in the graph.

The generator never invents anything — but a skill is a text file.  It gets
hand-polished, stitched together by an agent, or written from scratch by
someone who only read the template, and that is where fabricated commands,
invented root causes and made-up criteria enter.  ``lint`` checks *shape*
(sections, numbering, cross references); this module checks *provenance*: take
the finished ``SKILL.md`` apart and look every command, root cause, criterion
and parameter back up in the subgraph it claims to come from.

Anything that cannot be found there is not knowledge — it is something the
document made up, and it has to be deleted or restored to the source wording.

What counts as grounded:

* **命令** — a usable command template of a check / repair / escalation node,
  read through the same hygiene the renderer used (``{x}`` re-bracketed, prompt
  stripped, abbreviated spellings folded).  An observation is a screen dump,
  never a command source.
* **根因** — the name of a ``cause`` node.
* **判据** — an observation's expression, field or value.
* **入参** — a symptom's ``required_slots``, or a parameter of a grounded command.
* **自由文本** — a substring of some string the export actually carried.

Fixed wording the template itself contributes (``未找到根因``, ``复用前置检查
步骤 N 回显``, the 场景跳转表 scaffolding …) is generator scaffolding, not a
claim about the network, so it is recognised rather than reported.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from subkg2skill.describe import observation_expression
from subkg2skill.graph import Graph, Node
from subkg2skill.lint import CODE_RE, SCENARIO_RE, STEP_RE, _split_sections, _table_rows
from subkg2skill.loader import SubgraphLoadError, load
from subkg2skill.playbook import fault_key
from subkg2skill.template import (
    NOT_FOUND,
    command_templates,
    command_variants,
    normalise_command,
    param_key,
    parameters_in,
)

#: Nodes whose ``command_templates`` may be quoted as a CLI command.
COMMAND_NODES = ("check", "repair", "escalation")

#: Edge-type tokens the template prints in code spans; they name the graph's own
#: vocabulary, not an observation.
VOCABULARY = {"supports", "confirms", "excludes"}

#: Wording the renderer contributes itself — scaffolding, not sourced content.
SCAFFOLDING = (
    NOT_FOUND,
    "无直接修复CLI",
    "来源未给出修复命令，只能定位",
    "输出已执行的全部检查步骤及结果摘要",
    "全部步骤走完仍未命中任何故障特征",
    "按命令回显记录结果",
    "本子图未给出判定观测",
    "本子图未给出该原因的判定观测",
    "本子图未给出可用的检查动作，只能依据现象判断",
    "来源未给出命令模板",
    "来源未给出可判定的回显判据",
    "按现象与告警匹配",
    "复用前置检查",
    "仅支持性证据",
    "需人工确认",
    "关注字段",
    "结合前置检查回显人工判断",
    "顺序执行步骤",
    "以上判据均不命中",
    "结束排查",
    "现场提供",
    "命令参数",
    "从前置检查回显中提取，无需人工输入",
    "从排查步骤回显中提取",
    "修复动作参数，按现场规划或回显确定",
    "无 `confirms` / `supports` 关系",
    "按设备版本确认",
    "按现场实际替换",
    "执行前替换为现场对象",
    "复用本场景采集",
    "本场景采集",
    "公共前置之外",
    "适用场景",
    "其他场景可跳过",
    "读数用于分流判断",
    "全部场景",
)

#: Prefixes the renderer puts in front of a sourced fragment.
TEXT_PREFIXES = ("影响：", "回退：", "关注字段：", "采集内容：", "说明：")

_WS_RE = re.compile(r"\s+")


def _norm(text: str) -> str:
    """Whitespace-insensitive, case-insensitive form used for every comparison."""
    return _WS_RE.sub(" ", (text or "").replace("　", " ")).strip().lower()


def _strings_in(value: object) -> Iterable[str]:
    """Every string leaf of a raw record — what the export actually carried."""
    if isinstance(value, str):
        if value.strip():
            yield value.strip()
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings_in(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings_in(item)


@dataclass
class Finding:
    """One claim that could not be traced back to the subgraph."""

    level: str  # error | warning
    kind: str  # 命令 | 根因 | 判据 | 入参 | 说法
    text: str
    where: str
    hint: str = ""

    def render(self) -> str:
        tail = f"；{self.hint}" if self.hint else ""
        return f"{self.level.upper()}: [{self.where}] {self.kind}「{self.text}」在子图里没有来源{tail}"


@dataclass
class VerifyResult:
    findings: List[Finding] = field(default_factory=list)
    #: How many claims of each kind were checked — the denominator of the report.
    checked: Dict[str, int] = field(default_factory=dict)

    @property
    def errors(self) -> List[Finding]:
        return [item for item in self.findings if item.level == "error"]

    @property
    def warnings(self) -> List[Finding]:
        return [item for item in self.findings if item.level == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def total_checked(self) -> int:
        return sum(self.checked.values())


class Ground:
    """Everything the subgraph licenses the document to say."""

    def __init__(self, graph: Graph) -> None:
        self.commands: Dict[str, List[str]] = {}
        self.causes: Dict[str, str] = {}
        self.cause_keys: Dict[str, str] = {}
        self.criteria: List[str] = []
        self.params: Set[str] = set()
        self.texts: List[str] = []

        for node in graph.iter_nodes():
            self.texts.extend(_norm(text) for text in _strings_in(node.raw))
            if node.node_type in COMMAND_NODES:
                # Read through the renderer's own hygiene, so a command the
                # document shows in its spelled-out form still matches the
                # source that abbreviated it.
                for command in command_templates(node):
                    self.commands.setdefault(_norm(command), []).append(node.node_id)
                for _primary, variants in command_variants(node):
                    for variant in variants:
                        self.commands.setdefault(_norm(variant), []).append(node.node_id)
            if node.node_type == "cause":
                self.causes[_norm(node.name)] = node.name
                self.cause_keys[fault_key(node.name)] = node.name
            if node.node_type == "observation":
                self.criteria.extend(_norm(text) for text in _observation_terms(node))
            if node.node_type == "symptom":
                for slot in node.attrs.get("required_slots") or []:
                    key = param_key(str(slot))
                    if key:
                        self.params.add(key)
        for edge in graph.edges:
            self.texts.extend(_norm(text) for text in _strings_in(edge.raw))

        for command in self.commands:
            for token in parameters_in([command]):
                key = param_key(token)
                if key:
                    self.params.add(key)
        self.texts = [text for text in dict.fromkeys(self.texts) if text]
        self.criteria = [text for text in dict.fromkeys(self.criteria) if text]

    # -- membership ------------------------------------------------------
    def has_command(self, command: str) -> bool:
        return _norm(normalise_command(command)) in self.commands

    def cause_match(self, name: str) -> Tuple[bool, str]:
        """``(grounded, source wording)`` — the wording differs only on a near miss."""
        key = _norm(name)
        if key in self.causes:
            return True, self.causes[key]
        loose = self.cause_keys.get(fault_key(name))
        return (False, loose) if loose else (False, "")

    def has_criterion(self, span: str) -> bool:
        needle = _norm(span)
        if not needle or needle in VOCABULARY:
            return True
        if any(needle == text or needle in text for text in self.criteria):
            return True
        # A hand-written criterion may quote a value or field the observation
        # only carries in its raw record.
        return any(needle in text for text in self.texts)

    def has_param(self, name: str) -> bool:
        return param_key(name) in self.params

    def has_text(self, fragment: str) -> bool:
        needle = _norm(fragment)
        if not needle:
            return True
        if any(mark in fragment for mark in SCAFFOLDING):
            return True
        if any(needle == text or needle in text for text in self.texts):
            return True
        # cell() truncates long source prose at 300 characters.
        if needle.endswith("…"):
            head = needle[:-1].strip()
            return bool(head) and any(head in text for text in self.texts)
        return False


def _observation_terms(node: Node) -> List[str]:
    """Every wording an observation licenses a criterion to use."""
    attrs = node.attrs
    terms = [observation_expression(node), node.name, node.description]
    for key in ("field", "normalized_expression", "object_type", "unit", "source_operator"):
        value = attrs.get(key)
        if isinstance(value, str):
            terms.append(value)
    value = attrs.get("value")
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        terms.append(str(value))
    for key in ("predicate", "knowledge_predicate"):
        predicate = attrs.get(key)
        if isinstance(predicate, dict):
            terms.append(str(predicate.get("expression") or ""))
    terms.extend(node.match_phrases)
    return [term for term in terms if term and term.strip()]


# ------------------------------------------------------------------ claims
@dataclass
class Claim:
    kind: str  # command | cause | criterion | param | text
    text: str
    where: str


_CAUSE_QUOTE_RE = re.compile(r"(?:判定根因为|定位根因|排除根因)\s*[“\"]([^”\"]+)[”\"]")
#: Lines whose code spans name a field to read, not a command to run.
_REUSE_MARKS = (
    "复用前置检查",
    "复用本场景采集",
    "来源未给出命令模板",
    "本子图未给出",
    "按来源步骤说明",
)
#: Notes the renderer attaches under a command; their spans are literals, not claims.
_NOTE_MARKS = ("注意：命令含案例字面量", "注意：命令含示例取值")


def _spans(line: str) -> List[str]:
    return [span.strip() for span in CODE_RE.findall(line) if span.strip()]


def _plain(line: str) -> str:
    """The line with code spans and markdown decoration removed."""
    without_code = CODE_RE.sub(" ", line)
    return without_code.replace("**", "").strip(" -*").strip()


#: A fragment carries a claim only if it has words in it — what is left after
#: code spans are removed is often just the punctuation that joined them.
_WORDS_RE = re.compile(r"[0-9A-Za-z\u4e00-\u9fff]")


def _fragments(text: str) -> List[str]:
    """Split a rendered cell / collection line back into its sourced pieces."""
    parts: List[str] = []
    for chunk in re.split(r"<br>|；|;", text):
        chunk = chunk.strip(" 　·-")
        for prefix in TEXT_PREFIXES:
            if chunk.startswith(prefix):
                chunk = chunk[len(prefix) :].strip()
        if chunk and _WORDS_RE.search(chunk):
            parts.append(chunk)
    return parts


def _routing_claims(row: Sequence[str], where: str) -> List[Claim]:
    """One 场景跳转表 row: a precheck command, a criterion, a scenario label."""
    claims: List[Claim] = []
    if row and row[0]:
        claims += [Claim("command", span, where) for span in _spans(row[0])]
    if len(row) > 1:
        claims += [Claim("criterion", span, where) for span in _spans(row[1])]
    # 第三列是文档自己的场景编号（“→ **场景A：…**”），不是图里的内容。
    return claims


def _precheck_claims(lines: Sequence[str]) -> List[Claim]:
    claims: List[Claim] = []
    where = "前置检查"
    collecting = False
    routing = False
    for line in lines:
        if line.startswith("##"):
            # 场景跳转表 sits inside 前置检查 as an H2.
            routing = "场景跳转表" in line
            collecting = False
            continue
        heading = re.match(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*\s*$", line)
        if heading:
            where = f"前置检查 步骤{heading.group(1)}"
            collecting = False
            continue
        if routing and line.strip().startswith("|"):
            rows = _table_rows([line])
            if rows and rows[0] and rows[0][0].strip() != "前置检查步骤":
                claims += _routing_claims(rows[0], "场景跳转表")
            continue
        if "CLI 命令" in line or "来源另有写法" in line:
            spans = _spans(line)
            if any(mark in line for mark in _REUSE_MARKS):
                collecting = False
                continue
            if spans:
                claims += [Claim("command", span, where) for span in spans]
                collecting = False
            else:
                collecting = "CLI 命令" in line
            continue
        if "采集内容" in line:
            collecting = False
            claims += [Claim("criterion", span, where) for span in _spans(line)]
            claims += [Claim("text", fragment, where) for fragment in _fragments(_plain(line))]
            continue
        if "根因定位" in line:
            collecting = False
            claims += [Claim("criterion", span, where) for span in _spans(line)]
            claims += [Claim("cause", match, where) for match in _CAUSE_QUOTE_RE.findall(line)]
            continue
        if any(mark in line for mark in _NOTE_MARKS):
            collecting = False
            continue
        if collecting and line.strip().startswith("-"):
            claims += [Claim("command", span, where) for span in _spans(line)]
    return claims


def _step_claims(lines: Sequence[str]) -> List[Claim]:
    claims: List[Claim] = []
    scenario = ""
    where = "排查步骤"
    mode = ""
    for line in lines:
        grouping = SCENARIO_RE.match(line)
        if grouping:
            scenario = f"场景{grouping.group(1)}"
            where = f"排查步骤 {scenario}"
            mode = ""
            continue
        heading = STEP_RE.match(line)
        if heading:
            where = " ".join(part for part in ("排查步骤", scenario, f"步骤{heading.group(2)}") if part)
            mode = ""
            continue
        if "**CLI 命令**" in line:
            spans = _spans(line)
            if any(mark in line for mark in _REUSE_MARKS):
                # The step reads another step's output: the spans name fields.
                claims += [Claim("criterion", span, where) for span in spans]
                mode = ""
                continue
            mode = "command"
            claims += [Claim("command", span, where) for span in spans]
            continue
        if "**跳转信息**" in line:
            mode = "branch"
            continue
        if "**根因定位**" in line:
            mode = "cause"
            continue
        if "**步骤名称**" in line:
            mode = ""
            continue
        if "本场景采集" in line and "复用" not in line:
            mode = "collection"
            continue
        stripped = line.strip()
        if mode == "collection":
            # A scenario's own collection phase: same shape as 前置检查.
            if "CLI 命令" in line:
                if not any(mark in line for mark in _REUSE_MARKS):
                    claims += [Claim("command", span, where) for span in _spans(line)]
                continue
            if "采集内容" in line:
                claims += [Claim("criterion", span, where) for span in _spans(line)]
                claims += [Claim("text", fragment, where) for fragment in _fragments(_plain(line))]
                continue
            if "根因定位" in line:
                claims += [Claim("criterion", span, where) for span in _spans(line)]
                claims += [Claim("cause", match, where) for match in _CAUSE_QUOTE_RE.findall(line)]
                continue
            if stripped.startswith("- `") or stripped.startswith("  - `"):
                claims += [Claim("command", span, where) for span in _spans(line)]
                continue
        if mode == "command" and stripped.startswith("-"):
            if any(mark in stripped for mark in _NOTE_MARKS):
                continue
            claims += [Claim("command", span, where) for span in _spans(line)]
        elif mode == "branch" and stripped.startswith("-"):
            claims += [Claim("criterion", span, where) for span in _spans(line)]
            claims += [Claim("cause", match, where) for match in _CAUSE_QUOTE_RE.findall(line)]
        elif mode == "cause" and stripped.startswith("-"):
            name = stripped.lstrip("-").strip()
            if name and name != "无":
                claims.append(Claim("cause", name, where))
    return claims


def _table_claims(lines: Sequence[str]) -> List[Claim]:
    """Root-cause rows — one table per scenario when the document has several."""
    claims: List[Claim] = []
    scenario = ""
    index = 0
    for line in lines:
        grouping = SCENARIO_RE.match(line)
        if grouping:
            scenario = f"场景{grouping.group(1)}"
            index = 0
            continue
        if not line.strip().startswith("|"):
            continue
        rows = _table_rows([line])
        if not rows or not rows[0]:
            continue
        row = rows[0]
        if row[0].strip() in ("根因", ""):  # 表头
            continue
        index += 1
        where = " ".join(part for part in ("根因对照表", scenario, f"第{index}行") if part)
        name = row[0].strip()
        if name != NOT_FOUND:
            claims.append(Claim("cause", name, where))
        if len(row) > 1:
            claims += [Claim("criterion", span, where) for span in _spans(row[1])]
        for column in row[2:4]:
            claims += [Claim("command", span, where) for span in _spans(column)]
            claims += [
                Claim("text", fragment, where)
                for fragment in _fragments(_plain(column))
                if fragment != "-"
            ]
    return claims


def _param_claims(lines: Sequence[str]) -> List[Claim]:
    claims: List[Claim] = []
    for row in _table_rows(lines):
        name = row[0].strip() if row else ""
        if name and name != "信息":
            claims.append(Claim("param", name, "入参列表"))
    return claims


def claims_of(text: str) -> List[Claim]:
    """Every checkable assertion a SKILL.md makes, with where it sits."""
    sections, _order = _split_sections(text)
    claims: List[Claim] = []
    claims += _param_claims(sections.get("入参列表", []))
    claims += _precheck_claims(sections.get("前置检查", []))
    claims += _step_claims(sections.get("排查步骤", []))
    claims += _table_claims(sections.get("根因对照表", []))
    return claims


# ----------------------------------------------------------------- verify
def verify_text(text: str, graph: Graph) -> VerifyResult:
    """Check every claim in *text* against *graph*."""
    ground = Ground(graph)
    result = VerifyResult()
    seen: Set[Tuple[str, str, str]] = set()
    for claim in claims_of(text):
        key = (claim.kind, _norm(claim.text), claim.where)
        if key in seen:
            continue
        seen.add(key)
        result.checked[claim.kind] = result.checked.get(claim.kind, 0) + 1
        if claim.kind == "command":
            if not ground.has_command(claim.text):
                result.findings.append(
                    Finding(
                        "error",
                        "命令",
                        claim.text,
                        claim.where,
                        "只能引用 check / repair / escalation 节点的 command_templates；"
                        "删掉它，或改回来源原样的命令",
                    )
                )
        elif claim.kind == "cause":
            grounded, wording = ground.cause_match(claim.text)
            if grounded:
                continue
            if wording:
                result.findings.append(
                    Finding(
                        "error",
                        "根因",
                        claim.text,
                        claim.where,
                        f"来源里写作“{wording}”，改回来源写法（根因名要逐字可查）",
                    )
                )
            else:
                result.findings.append(
                    Finding(
                        "error", "根因", claim.text, claim.where, "子图里没有同名 cause 节点，应删除"
                    )
                )
        elif claim.kind == "criterion":
            if not ground.has_criterion(claim.text):
                result.findings.append(
                    Finding(
                        "error",
                        "判据",
                        claim.text,
                        claim.where,
                        "判据只能来自 observation 的表达式、字段或取值",
                    )
                )
        elif claim.kind == "param":
            if not ground.has_param(claim.text):
                result.findings.append(
                    Finding(
                        "error",
                        "入参",
                        claim.text,
                        claim.where,
                        "入参必须是症状的 required_slots，或正文命令里真实出现的 <参数>",
                    )
                )
        elif claim.kind == "text":
            if not ground.has_text(claim.text):
                result.findings.append(
                    Finding(
                        "warning",
                        "说法",
                        claim.text,
                        claim.where,
                        "来源文本里找不到这句话；确认不是补写的说法，是就删掉或改回原文",
                    )
                )
    return result


def load_graph(paths: Sequence[str]) -> Graph:
    """Build the reference graph the document is checked against."""
    bundle, _sources = load(list(paths))
    graph, _report = Graph.from_bundle(bundle)
    return graph


def verify_path(path: Path, graph_inputs: Sequence[str] = ()) -> VerifyResult:
    """Verify a skill directory (or ``SKILL.md``) against the graph it came from.

    The graph is not shipped with the skill, so normally ``graph_inputs`` names
    the original export — that is the point of the check: the document is held
    against the source of truth, not against a copy that travelled with it.  A
    slice dumped at build time (``<skill>.internal/subgraph.json``) is used when
    no graph is given, which is the shortest path right after a build.
    """
    path = Path(path)
    document = path / "SKILL.md" if path.is_dir() else path
    if not document.exists():
        raise SubgraphLoadError(f"{document}: 文件不存在")
    if graph_inputs:
        return verify_text(document.read_text(encoding="utf-8"), load_graph(graph_inputs))
    skill_dir = document.parent
    dumped = skill_dir.parent / (skill_dir.name + ".internal") / "subgraph.json"
    if not dumped.exists():
        raise SubgraphLoadError(
            f"没有可校验的参照图：用 --graph 指定原始 node/edge 文件"
            f"（或先用 build --with-subgraph 导出 {dumped}）"
        )
    return verify_text(document.read_text(encoding="utf-8"), load_graph([str(dumped)]))
