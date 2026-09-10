"""Build the four-section skill document the house template requires.

One skill documents one fault entry (a single ``symptom``):
``# 入参列表`` → ``# 前置检查`` → ``# 排查步骤`` → ``# 根因对照表``.

Two rules drive everything here.  Commands may only come from
``attrs.command_templates`` / ``attrs.procedure`` of check, repair and
escalation nodes — never from an observation, never invented.  And evidence
strength is carried through: a cause decided only by ``supports`` says so in
the jump text and in the table, instead of being promoted to a confirmed root
cause.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from subkg2skill import schema
from subkg2skill.describe import observation_expression
from subkg2skill.graph import Graph, Node, _string_list, _text
from subkg2skill.playbook import CauseBranch, CheckStep, Playbook, Verdict

#: ``{x}`` / ``[x]`` in source templates are re-bracketed to ``<x>``; names never change.
PLACEHOLDER_RE = re.compile(r"[{\[]([^{}\[\]]+)[}\]]")
PARAM_RE = re.compile(r"<([^<>]+)>")
NO_FIX = "无直接修复CLI（来源未给出修复命令，只能定位）"
NOT_FOUND = "未找到根因"


def normalise_command(command: str) -> str:
    """Re-bracket source placeholders to ``<>`` without renaming anything."""
    text = _text(command)
    if not text:
        return ""
    return PLACEHOLDER_RE.sub(lambda m: f"<{m.group(1).strip()}>", text)


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
class Precheck:
    """One line of the linear collection phase."""

    title: str
    commands: List[str]
    collect: str
    verdicts: List[str] = field(default_factory=list)
    node_id: str = ""


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


def build_doc(graph: Graph, playbook: Playbook) -> SkillDoc:
    """Turn one symptom playbook into the template's four sections."""
    producing_check: Dict[str, str] = {}
    for node in graph.iter_nodes():
        if node.node_type == "check":
            for observation, _edge in graph.targets(node.node_id, "observes"):
                producing_check.setdefault(observation.node_id, node.node_id)

    branch_causes = {branch.cause.node_id for branch in playbook.causes}
    prechecks: List[Precheck] = []
    precheck_causes: Dict[str, Tuple[str, str]] = {}  # 根因名 -> (现象, cause node_id)
    precheck_of_check: Dict[str, int] = {}

    for step in playbook.entry_checks:
        precheck = Precheck(
            title=step.check.name,
            commands=command_templates(step.check),
            collect=_collect_line(step.check, step.outcomes),
            node_id=step.check.node_id,
        )
        for observation, verdict in _decisive(step):
            # Causes that get their own step are judged there, once, not twice.
            if verdict.kind == "excludes" or verdict.cause.node_id in branch_causes:
                continue
            strength = _verdict_strength(verdict.kind)
            precheck.verdicts.append(
                f"若 {_criterion(observation)}，判定根因为“{verdict.cause.name}”{strength}，结束排查。"
            )
            precheck_causes.setdefault(
                verdict.cause.name, (f"{_criterion(observation)}{strength}", verdict.cause.node_id)
            )
        prechecks.append(precheck)
        precheck_of_check[step.check.node_id] = len(prechecks)

    steps: List[Step] = []
    branch_by_cause: Dict[str, CauseBranch] = {b.cause.node_id: b for b in playbook.causes}
    step_causes: Dict[str, Tuple[str, str]] = {}

    for branch in playbook.causes:
        decisive = _branch_decisive(branch)
        commands, reuse_note = _step_commands(
            branch, decisive, producing_check, precheck_of_check, prechecks
        )
        branches: List[Branch] = []
        causes: List[str] = []
        for observation, verdict in decisive:
            criterion = _criterion(observation)
            if verdict.kind == "excludes":
                branches.append(
                    Branch(criterion, f"排除根因“{verdict.cause.name}”，{{next}}")
                )
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
        root_causes.append(RootCause(name=name, evidence=evidence, fix=fix, recheck=recheck))
    root_causes.append(
        RootCause(
            name=NOT_FOUND,
            evidence="全部步骤走完仍未命中任何故障特征",
            fix="输出已执行的全部检查步骤及结果摘要",
            recheck="-",
        )
    )

    doc = SkillDoc(
        symptom=playbook.symptom,
        params=_build_params(playbook.symptom, prechecks, steps, repair_commands),
        prechecks=prechecks,
        steps=steps,
        root_causes=root_causes,
    )
    if not prechecks:
        doc.notes.append("该症状在子图中没有入口检查（diagnosed_by/next_step），前置检查为空。")
    if not steps:
        doc.notes.append("该症状在子图中没有候选原因（has_cause），排查步骤为空。")
    return doc


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
    prechecks: Sequence[Precheck],
) -> Tuple[List[str], str]:
    """Commands for a step — or a pointer to the precheck whose output decides it."""
    fields: List[str] = []
    for observation, _verdict in decisive:
        name = _text(observation.attr("field"))
        if name and name not in fields:
            fields.append(name)
    field_note = ("，查看 " + "、".join(f"`{name}`" for name in fields) + " 字段") if fields else ""

    # Prefer the precheck that actually produced the deciding observation.
    for observation, _verdict in decisive:
        check_id = producing_check.get(observation.node_id)
        index = precheck_of_check.get(check_id or "")
        if index:
            return [], f"复用前置检查步骤 {index} 回显（{prechecks[index - 1].title}{field_note}）"

    for step in branch.checks:
        index = precheck_of_check.get(step.check.node_id)
        if index:
            return [], f"复用前置检查步骤 {index} 回显（{prechecks[index - 1].title}{field_note}）"
        commands = command_templates(step.check)
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
