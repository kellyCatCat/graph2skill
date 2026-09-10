"""Build the four-section skill document the house template requires.

One skill documents one fault entry (a single ``symptom``):
``# 入参列表`` → ``# 前置检查`` → ``# 排查步骤`` → ``# 根因对照表``.

Rules that drive everything here:

* Commands may only come from ``attrs.command_templates`` / ``attrs.procedure``
  of check, repair and escalation nodes — never from an observation, never
  invented.
* Evidence strength is carried through: a cause decided only by ``supports``
  says so, instead of being promoted to a confirmed root cause.
* One command, one collection step. Knowledge graphs hold many check nodes that
  run the same command, and repeating it a dozen times makes a document nobody
  follows, so prechecks are merged by command and steps point back at them.
* Case-specific material (``example_specific``) is left out by default. It
  carries the addresses, device names and topology of one incident, which do
  not transfer to the reader's network.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from subkg2skill import schema
from subkg2skill.describe import observation_expression
from subkg2skill.graph import Graph, Node, _string_list, _text
from subkg2skill.playbook import CauseBranch, CheckStep, Playbook, Verdict

#: ``{x}`` in source templates is re-bracketed to ``<x>``; the name never changes.
#: ``[ ... ]`` is left alone — in CLI reference syntax it marks an optional
#: argument, not a placeholder, and rewriting it would corrupt the command.
PLACEHOLDER_RE = re.compile(r"\{([^{}]+)\}")
PARAM_RE = re.compile(r"<([^<>]+)>")
NO_FIX = "无直接修复CLI（来源未给出修复命令，只能定位）"
NOT_FOUND = "未找到根因"

#: Literals that belong to the incident a case was written about, not to the reader.
CASE_LITERAL_RES = (
    ("IP 地址", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")),
    ("IPv6 地址", re.compile(r"\b[0-9a-fA-F]{1,4}(?::[0-9a-fA-F]{0,4}){3,}")),
    ("设备名", re.compile(r"\b(?:Device|Router|Switch|CE|PE|ASG|CSG|AGG|MER|MAR)[A-Z0-9]\b")),
    ("具体接口号", re.compile(r"\b\d+/\d+/\d+\b")),
    ("System ID", re.compile(r"\b[0-9a-fA-F]{4}(?:\.[0-9a-fA-F]{4}){2}\b")),
)


def case_literals(text: str) -> List[str]:
    """Case-specific literals found in *text*, as ``类型 值`` labels."""
    found: List[str] = []
    for label, pattern in CASE_LITERAL_RES:
        for match in pattern.findall(text or ""):
            value = match if isinstance(match, str) else match[0]
            item = f"{label} {value}"
            if item not in found:
                found.append(item)
    return found


def normalise_command(command: str) -> str:
    """Re-bracket ``{}`` placeholders to ``<>`` without renaming anything."""
    text = _text(command)
    if not text:
        return ""
    return PLACEHOLDER_RE.sub(lambda m: f"<{m.group(1).strip()}>", text)


def command_signature(commands: Sequence[str]) -> str:
    """Identity used to merge collection steps that run the same thing."""
    return "\n".join(sorted(re.sub(r"\s+", " ", command).strip() for command in commands))


def cell(text: str, *, limit: int = 300) -> str:
    """Make *text* safe for a markdown table cell.

    Source prose arrives with hard line breaks and pipes in it; both break the
    table silently, which is how a root-cause table stops being readable.
    """
    flattened = re.sub(r"[\r\n]+", "<br>", _text(text)).replace("|", "\\|")
    flattened = re.sub(r"[ \t]{2,}", " ", flattened).strip()
    if len(flattened) > limit:
        flattened = flattened[:limit].rstrip() + "…"
    return flattened


def command_templates(node: Node) -> List[str]:
    return [normalise_command(cmd) for cmd in _string_list(node.attrs.get("command_templates")) if _text(cmd)]


def parameters_in(commands: Iterable[str]) -> List[str]:
    """Parameter tokens actually appearing in *commands*, in order of appearance."""
    found: List[str] = []
    for command in commands:
        for token in PARAM_RE.findall(command):
            token = token.strip()
            if token and token not in found:
                found.append(token)
    return found


def param_key(token: str) -> str:
    """Identity used to merge a slot name with a CLI parameter name."""
    return re.sub(r"[\s_\-]+", "", token).lower()


def param_display(token: str) -> str:
    """Readable label that still matches the CLI name once spaces/hyphens are dropped."""
    if re.search(r"[A-Za-z0-9]", token) and not re.search(r"[^\x00-\x7f]", token):
        return token.replace("-", " ").replace("_", " ").strip()
    return token.strip()


@dataclass
class Param:
    token: str
    display: str
    required: bool
    note: str


@dataclass
class BuildPolicy:
    """What to leave out, so the document stays about one transferable fault."""

    #: Keep nodes marked ``example_specific`` (incident addresses, device names).
    include_example_specific: bool = False
    #: Keep causes that have neither a deciding observation nor a repair.
    keep_undecidable: bool = False
    #: Hard cap on 排查步骤 (0 = no cap); the overflow is reported, not silently cut.
    max_steps: int = 0


@dataclass
class Precheck:
    """One line of the linear collection phase (one command, not one node)."""

    title: str
    commands: List[str]
    collect: str
    verdicts: List[str] = field(default_factory=list)
    node_id: str = ""
    node_ids: List[str] = field(default_factory=list)
    literals: List[str] = field(default_factory=list)


@dataclass
class Branch:
    """One row of a step's 跳转信息.

    ``outcome`` may contain ``{next}``; the fall-through pass fills it in once
    the total number of steps is known.
    """

    criterion: str
    outcome: str


@dataclass
class Step:
    index: int
    name: str
    commands: List[str]
    reuse_note: str
    branches: List[Branch]
    causes: List[str]
    literals: List[str] = field(default_factory=list)


@dataclass
class RootCause:
    name: str
    evidence: str
    fix: str
    recheck: str


@dataclass
class SkillDoc:
    symptom: Node
    params: List[Param]
    prechecks: List[Precheck]
    steps: List[Step]
    root_causes: List[RootCause]
    notes: List[str] = field(default_factory=list)
    #: Causes left out of the steps, with the reason — reported, never silently dropped.
    omitted: List[Tuple[str, str]] = field(default_factory=list)
    unit: str = ""


def _collect_line(check: Node, outcomes: Sequence) -> str:
    """What this check is being run to read off the screen."""
    parts: List[str] = []
    intent = _text(check.attr("intent"))
    if intent:
        parts.append(intent)
    fields: List[str] = []
    for outcome in outcomes:
        field_name = _text(outcome.observation.attr("field"))
        if field_name and field_name not in fields:
            fields.append(field_name)
    if fields:
        parts.append("关注字段：" + "、".join(f"`{name}`" for name in fields))
    if not parts:
        procedure = _string_list(check.attrs.get("procedure"))
        if procedure:
            parts.append("；".join(procedure))
    return "；".join(parts) or "按命令回显记录结果"


def _verdict_strength(kind: str) -> str:
    return {"confirms": "", "supports": "（仅支持性证据 `supports`，需人工确认）"}.get(kind, "")


def _criterion(observation: Node) -> str:
    return f"`{observation_expression(observation)}`"


def _decisive(step: CheckStep) -> List[Tuple[Node, Verdict]]:
    """Observations of *step* that decide a cause, strongest first."""
    decisive: List[Tuple[Node, Verdict]] = []
    for outcome in step.outcomes:
        for verdict in outcome.verdicts:
            decisive.append((outcome.observation, verdict))
    decisive.sort(key=lambda pair: schema.VERDICT_EDGES.index(pair[1].kind))
    return decisive


def _repair_fix(
    graph: Graph, cause_node: Optional[Node], branch: Optional[CauseBranch]
) -> Tuple[str, str, List[str]]:
    """Return (修复CLI和方法, 复检命令, 用到的命令) — copied from the source, never expanded."""
    repairs = [link.node for link in branch.repairs] if branch else []
    if not repairs:
        return NO_FIX, "-", []
    fixes: List[str] = []
    rechecks: List[str] = []
    used: List[str] = []
    for repair in repairs:
        commands = command_templates(repair)
        used.extend(commands)
        if commands:
            fixes.append("<br>".join(f"`{cmd}`" for cmd in commands))
        else:
            steps = _string_list(repair.attrs.get("procedure"))
            fixes.append("；".join(steps) if steps else f"{repair.name}（{NO_FIX}）")
        impact = _text(repair.attr("service_impact"))
        if impact:
            fixes.append(f"影响：{impact}")
        rollback = _text(repair.attr("rollback"))
        if rollback:
            fixes.append(f"回退：{rollback}")
        # A verification command only counts when the source links one.
        for node, _edge in graph.targets(repair.node_id, "next_step"):
            if node.node_type == "check":
                verification = command_templates(node)
                used.extend(verification)
                rechecks.extend(f"`{cmd}`" for cmd in verification)
    fix = "<br>".join(fixes) if fixes else NO_FIX
    recheck = "<br>".join(dict.fromkeys(rechecks)) if rechecks else "-"
    return fix, recheck, used


def _usable(node: Node, policy: BuildPolicy) -> bool:
    """False for material that only describes the incident a case was written about."""
    return policy.include_example_specific or not node.example_specific


def build_doc(graph: Graph, playbook: Playbook, policy: Optional[BuildPolicy] = None) -> SkillDoc:
    """Turn one symptom playbook into the template's four sections."""
    policy = policy or BuildPolicy()
    producing_check: Dict[str, str] = {}
    for node in graph.iter_nodes():
        if node.node_type == "check":
            for observation, _edge in graph.targets(node.node_id, "observes"):
                producing_check.setdefault(observation.node_id, node.node_id)

    branch_causes = {branch.cause.node_id for branch in playbook.causes}
    prechecks: List[Precheck] = []
    precheck_causes: Dict[str, Tuple[str, str]] = {}  # 根因名 -> (现象, cause node_id)
    by_signature: Dict[str, int] = {}  # 命令签名 -> 前置检查序号
    precheck_of_check: Dict[str, int] = {}
    omitted: List[Tuple[str, str]] = []

    for step in playbook.entry_checks:
        check = step.check
        if not _usable(check, policy):
            omitted.append((check.name, "检查动作带案例特定背景（example_specific）"))
            continue
        commands = command_templates(check)
        signature = command_signature(commands)
        # One command, one collection step: a graph holds many check nodes that
        # run the same thing, and repeating it is what bloats the document.
        index = by_signature.get(signature) if signature else None
        if index is not None:
            precheck = prechecks[index - 1]
            precheck.collect = _merge_collect(precheck.collect, _collect_line(check, step.outcomes))
            precheck.node_ids.append(check.node_id)
        else:
            precheck = Precheck(
                title=check.name,
                commands=commands,
                collect=_collect_line(check, step.outcomes),
                node_id=check.node_id,
                node_ids=[check.node_id],
                literals=case_literals(" ".join(commands)),
            )
            prechecks.append(precheck)
            index = len(prechecks)
            if signature:
                by_signature[signature] = index
        precheck_of_check[check.node_id] = index

        for observation, verdict in _decisive(step):
            # Causes that get their own step are judged there, once, not twice.
            if verdict.kind == "excludes" or verdict.cause.node_id in branch_causes:
                continue
            if not (_usable(verdict.cause, policy) and _usable(observation, policy)):
                continue
            strength = _verdict_strength(verdict.kind)
            precheck.verdicts.append(
                f"若 {_criterion(observation)}，判定根因为“{verdict.cause.name}”{strength}，结束排查。"
            )
            precheck_causes.setdefault(
                verdict.cause.name, (f"{_criterion(observation)}{strength}", verdict.cause.node_id)
            )

    steps: List[Step] = []
    branch_by_cause: Dict[str, CauseBranch] = {b.cause.node_id: b for b in playbook.causes}
    step_causes: Dict[str, Tuple[str, str]] = {}

    for branch in playbook.causes:
        if not _usable(branch.cause, policy):
            omitted.append((branch.cause.name, "原因带案例特定背景（example_specific）"))
            continue
        decisive = [
            pair
            for pair in _branch_decisive(branch)
            if _usable(pair[0], policy) and _usable(pair[1].cause, policy)
        ]
        has_repair = bool(branch.repairs)
        if not decisive and not has_repair and not policy.keep_undecidable:
            # A step with no criterion and no fix tells the reader nothing.
            omitted.append((branch.cause.name, "既无判定观测也无修复动作"))
            continue
        if policy.max_steps and len(steps) >= policy.max_steps:
            omitted.append((branch.cause.name, f"超出 --max-steps {policy.max_steps} 限制"))
            continue

        commands, reuse_note = _step_commands(
            branch, decisive, producing_check, precheck_of_check, by_signature, prechecks
        )
        branches: List[Branch] = []
        causes: List[str] = []
        for observation, verdict in decisive:
            criterion = _criterion(observation)
            if verdict.kind == "excludes":
                branches.append(Branch(criterion, f"排除根因“{verdict.cause.name}”，{{next}}"))
                continue
            strength = _verdict_strength(verdict.kind)
            branches.append(Branch(criterion, f"定位根因“{verdict.cause.name}”{strength}，结束排查。"))
            if verdict.cause.name not in causes:
                causes.append(verdict.cause.name)
            step_causes.setdefault(
                verdict.cause.name, (f"{criterion}{strength}", verdict.cause.node_id)
            )
        if not any(verdict.kind != "excludes" for _obs, verdict in decisive):
            # No observation decides this cause: say so instead of inventing a criterion.
            branches.append(
                Branch(
                    "本子图未给出该原因的判定观测",
                    "结合前置检查回显人工判断；无法判定则{next}",
                )
            )
            causes.append(branch.cause.name)
            step_causes.setdefault(
                branch.cause.name,
                ("本子图未给出判定观测（无 `confirms` / `supports` 关系）", branch.cause.node_id),
            )

        steps.append(
            Step(
                index=len(steps) + 1,
                name=f"检查{branch.cause.name}",
                commands=commands,
                reuse_note=reuse_note,
                branches=branches,
                causes=causes,
                literals=case_literals(" ".join(commands)),
            )
        )

    # Fall-through wording: every step but the last hands over to the next one.
    for step in steps:
        if step.index < len(steps):
            following = f"顺序执行步骤 {step.index + 1}。"
        else:
            following = f"判定“{NOT_FOUND}”，输出已执行的全部检查步骤及结果摘要，结束排查。"
        for branch in step.branches:
            branch.outcome = branch.outcome.replace("{next}", following)
        step.branches.append(Branch("以上判据均不命中", following))

    root_causes: List[RootCause] = []
    repair_commands: List[str] = []
    for name, (evidence, node_id) in {**precheck_causes, **step_causes}.items():
        cause_node = graph.get(node_id)
        branch = branch_by_cause.get(node_id)
        fix, recheck, commands = _repair_fix(graph, cause_node, branch)
        repair_commands.extend(commands)
        root_causes.append(
            RootCause(name=name, evidence=cell(evidence), fix=cell(fix), recheck=cell(recheck))
        )
    root_causes.append(
        RootCause(
            name=NOT_FOUND,
            evidence="全部步骤走完仍未命中任何故障特征",
            fix="输出已执行的全部检查步骤及结果摘要",
            recheck="-",
        )
    )

    # A precheck nobody reads from and that decides nothing is just noise.
    referenced = {ref for step in steps for ref in REUSE_RE.findall(step.reuse_note)}
    prechecks = [
        precheck
        for precheck in prechecks
        if precheck.verdicts
        or not steps
        or any(node_id in referenced for node_id in precheck.node_ids)
    ]
    _resolve_references(prechecks, steps)

    doc = SkillDoc(
        symptom=playbook.symptom,
        params=_build_params(playbook.symptom, prechecks, steps, repair_commands),
        prechecks=prechecks,
        steps=steps,
        root_causes=root_causes,
        omitted=omitted,
    )
    if not prechecks:
        doc.notes.append("该症状在子图中没有可用的入口检查，前置检查为空。")
    if not steps:
        doc.notes.append("该症状在子图中没有可展开的候选原因，排查步骤为空。")
    if omitted:
        doc.notes.append(f"{len(omitted)} 个原因/检查未进入正文，明细见 reference/evidence.md。")
    return doc


#: Steps cite the precheck they read from by node id until the list is final.
REUSE_RE = re.compile(r"@@([^@]+)@@")


def _resolve_references(prechecks: List[Precheck], steps: List[Step]) -> None:
    """Turn the ``@@node_id@@`` placeholders into final precheck numbers.

    Steps are built before it is known which prechecks survive pruning, so they
    cite the collection step by identity and get their number here.
    """
    number: Dict[str, int] = {}
    for index, precheck in enumerate(prechecks, start=1):
        for node_id in precheck.node_ids:
            number[node_id] = index
    for step in steps:
        step.reuse_note = REUSE_RE.sub(
            lambda match: str(number.get(match.group(1), "?")), step.reuse_note
        )
        if "步骤 ?" in step.reuse_note:  # the precheck it cited did not survive
            step.reuse_note = "复用前置检查回显"


def _merge_collect(existing: str, addition: str) -> str:
    """Union of two collection descriptions, without repeating a phrase."""
    parts: List[str] = []
    for chunk in (existing or "").split("；") + (addition or "").split("；"):
        chunk = chunk.strip()
        if chunk and chunk != "按命令回显记录结果" and chunk not in parts:
            parts.append(chunk)
    return "；".join(parts) or "按命令回显记录结果"


def _branch_decisive(branch: CauseBranch) -> List[Tuple[Node, "Verdict"]]:
    """Every observation that decides this cause, strongest first, deduplicated."""
    decisive: List[Tuple[Node, Verdict]] = []
    seen: Set[Tuple[str, str]] = set()
    for verdict in branch.verdicts:
        key = (verdict.observation.node_id, verdict.kind)
        if key not in seen:
            seen.add(key)
            decisive.append((verdict.observation, verdict))
    for step in branch.checks:
        for observation, verdict in _decisive(step):
            key = (observation.node_id, verdict.kind)
            if key in seen:
                continue
            if verdict.cause.node_id != branch.cause.node_id and verdict.kind != "excludes":
                continue
            seen.add(key)
            decisive.append((observation, verdict))
    decisive.sort(key=lambda pair: schema.VERDICT_EDGES.index(pair[1].kind))
    return decisive


def _step_commands(
    branch: CauseBranch,
    decisive: Sequence[Tuple[Node, "Verdict"]],
    producing_check: Dict[str, str],
    precheck_of_check: Dict[str, int],
    by_signature: Dict[str, int],
    prechecks: Sequence[Precheck],
) -> Tuple[List[str], str]:
    """Commands for a step — or a pointer to the precheck whose output decides it."""
    fields: List[str] = []
    for observation, _verdict in decisive:
        name = _text(observation.attr("field"))
        if name and name not in fields:
            fields.append(name)
    field_note = ("，查看 " + "、".join(f"`{name}`" for name in fields) + " 字段") if fields else ""

    def cite(index: int) -> Tuple[List[str], str]:
        precheck = prechecks[index - 1]
        return [], f"复用前置检查步骤 @@{precheck.node_ids[0]}@@ 回显（{precheck.title}{field_note}）"

    # Prefer the precheck that actually produced the deciding observation.
    for observation, _verdict in decisive:
        check_id = producing_check.get(observation.node_id)
        index = precheck_of_check.get(check_id or "")
        if index:
            return cite(index)

    for step in branch.checks:
        index = precheck_of_check.get(step.check.node_id)
        if index:
            return cite(index)
        commands = command_templates(step.check)
        # The same command may already be collected under another check node.
        same = by_signature.get(command_signature(commands)) if commands else None
        if same:
            return cite(same)
        if commands:
            return commands, ""
        return [], f"{step.check.name}：来源未给出命令模板，按来源步骤说明人工执行"
    return [], "复用前置检查回显" if prechecks else "本子图未给出可用的检查动作，只能依据现象判断"


def _build_params(
    symptom: Node,
    prechecks: Sequence[Precheck],
    steps: Sequence[Step],
    repair_commands: Sequence[str] = (),
) -> List[Param]:
    """Required slots plus every ``<token>`` the document's commands actually use."""
    params: Dict[str, Param] = {}

    for slot in _string_list(symptom.attrs.get("required_slots")):
        key = param_key(slot)
        if key:
            params[key] = Param(slot, param_display(slot), True, "现场提供")

    precheck_tokens: Set[str] = set()
    for index, precheck in enumerate(prechecks, start=1):
        for token in parameters_in(precheck.commands):
            key = param_key(token)
            precheck_tokens.add(key)
            existing = params.get(key)
            note = f"前置检查步骤 {index} 命令参数"
            if existing is None:
                params[key] = Param(token, param_display(token), True, note)
            elif not existing.required:
                params[key] = Param(existing.token, existing.display, True, note)

    for step in steps:
        for token in parameters_in(step.commands):
            key = param_key(token)
            if key in params:
                continue
            params[key] = Param(
                token,
                param_display(token),
                False,
                "从前置检查回显中提取，无需人工输入" if precheck_tokens else "从排查步骤回显中提取",
            )

    for token in parameters_in(repair_commands):
        key = param_key(token)
        if key not in params:
            params[key] = Param(token, param_display(token), False, "修复动作参数，按现场规划或回显确定")
    return list(params.values())


# ------------------------------------------------------------------ render
def render_doc(doc: SkillDoc, *, name: str, description: str, lead: str = "") -> str:
    lines = ["---", f"name: {name}", f"description: {description}", "---", ""]
    if lead:
        lines += [lead, ""]

    lines += ["# 入参列表", ""]
    if doc.params:
        lines += ["| 信息 | 是否必填 | 说明 |", "| --- | --- | --- |"]
        for param in doc.params:
            lines.append(f"| {param.display} | {'是' if param.required else '否'} | {param.note} |")
    else:
        lines.append("本排查流程不需要额外入参（子图未给出必填槽位与命令参数）。")
    lines.append("")

    lines += ["# 前置检查", ""]
    if doc.prechecks:
        lines.append("前置检查按顺序线性执行，仅用于采集后续排查所需的回显信息，不做跳转。")
        lines.append("")
        for index, precheck in enumerate(doc.prechecks, start=1):
            lines.append(f"{index}. **{precheck.title}**")
            if precheck.commands:
                if len(precheck.commands) == 1:
                    lines.append(f"   - CLI 命令：`{precheck.commands[0]}`")
                else:
                    lines.append("   - CLI 命令：")
                    lines += [f"     - `{command}`" for command in precheck.commands]
            else:
                lines.append("   - CLI 命令：来源未给出命令模板，按来源步骤说明人工采集")
            if precheck.literals:
                lines.append(
                    "   - 注意：命令含案例字面量（" + "、".join(precheck.literals[:4]) + "），执行前替换为现场对象"
                )
            lines.append(f"   - 采集内容：{precheck.collect}")
            for verdict in precheck.verdicts:
                lines.append(f"   - 根因定位：{verdict}")
            lines.append("")
    else:
        lines += ["本子图未给出该症状的入口检查动作，直接进入排查步骤。", ""]

    lines += ["# 排查步骤", ""]
    if doc.steps:
        lines.append("默认按顺序执行；判据来自前置检查回显的步骤不重复下发命令。")
        lines.append("")
        for step in doc.steps:
            lines.append(f"## 步骤{step.index}：{step.name}")
            lines.append("")
            lines.append(f"1. **步骤名称**：{step.name}")
            if step.commands:
                if len(step.commands) == 1:
                    lines.append(f"2. **CLI 命令**：`{step.commands[0]}`")
                else:
                    lines.append("2. **CLI 命令**：")
                    lines += [f"   - `{command}`" for command in step.commands]
            else:
                lines.append(f"2. **CLI 命令**：{step.reuse_note or '复用前置检查回显'}")
            if step.literals:
                lines.append(
                    "   - 注意：命令含案例字面量（" + "、".join(step.literals[:4]) + "），执行前替换为现场对象"
                )
            lines.append("3. **跳转信息**：")
            for branch in step.branches:
                lines.append(f"   - {branch.criterion}：{branch.outcome}")
            lines.append("4. **根因定位**：")
            if step.causes:
                lines += [f"   - {cause}" for cause in step.causes]
            else:
                lines.append("   - 无")
            lines.append("")
    else:
        lines += ["本子图未给出该症状的候选原因，无法展开排查步骤。", ""]

    lines += ["# 根因对照表", "", "| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |", "| --- | --- | --- | --- |"]
    for cause in doc.root_causes:
        lines.append(f"| {cause.name} | {cause.evidence} | {cause.fix} | {cause.recheck} |")
    lines.append("")
    return "\n".join(lines)
