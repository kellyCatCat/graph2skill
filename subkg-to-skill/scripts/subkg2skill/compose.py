"""Build the four-section skill document out of playbooks.

``# 入参列表`` → ``# 前置检查`` → ``# 排查步骤`` → ``# 根因对照表``, one scenario
per fault.  Rules that drive everything here:

* Commands may only come from ``attrs.command_templates`` / ``attrs.procedure``
  of check, repair and escalation nodes — never from an observation, never
  invented.
* Evidence strength is carried through: a cause decided only by ``supports``
  says so, instead of being promoted to a confirmed root cause.
* One command, one collection step (see :mod:`subkg2skill.collection`).
* Case-specific material (``example_specific``) is left out by default. It
  carries the addresses, device names and topology of one incident, which do
  not transfer to the reader's network.

The result is a :class:`~subkg2skill.doc.SkillDoc`; :mod:`subkg2skill.markdown`
writes it out.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Set, Tuple

from subkg2skill import hygiene, schema
from subkg2skill.collection import (
    REUSE_RE,
    SharedCollection,
    collect_line,
    equivalent_precheck,
    merge_collect,
    register_variants,
    resolve_references,
    share_across_steps,
    split_collection,
)
from subkg2skill.commands import (
    case_literals,
    command_templates,
    command_templates_with_rejections,
    command_variants,
    param_display,
    param_key,
    parameters_in,
    same_command_set,
)
from subkg2skill.condition import format_condition, observation_expression
from subkg2skill.doc import (
    NO_FIX,
    NOT_FOUND,
    HANDOFF_NOTES,
    Branch,
    BuildPolicy,
    DocScenario,
    Handoff,
    Param,
    Precheck,
    RootCause,
    RoutingRow,
    SkillDoc,
    Step,
    scenario_label,
)
from subkg2skill.graph import Graph, Node, _string_list, _text
from subkg2skill.playbook import CauseBranch, CheckStep, Link, Playbook, Verdict, fault_key


def build_doc(graph: Graph, playbook: Playbook, policy: Optional[BuildPolicy] = None) -> SkillDoc:
    """Turn one symptom playbook into the template's four sections."""
    return build_multi_doc(graph, [(playbook.symptom.name, playbook)], policy)


def build_multi_doc(
    graph: Graph,
    scenarios: Sequence[Tuple[str, Playbook]],
    policy: Optional[BuildPolicy] = None,
) -> SkillDoc:
    """Build one document covering several faults off a shared collection phase.

    The prechecks are pooled and deduplicated across scenarios — the same
    ``display isis peer`` serves all of them — and a routing table after them
    says which scenario each reading leads to.  Steps are numbered inside their
    own scenario.
    """
    if not scenarios:
        raise ValueError("至少需要一个场景")
    policy = policy or BuildPolicy()
    shared = SharedCollection()
    for node in graph.iter_nodes():
        if node.node_type == "check":
            for observation, _edge in graph.targets(node.node_id, "observes"):
                shared.producing_check.setdefault(observation.node_id, node.node_id)

    omitted: List[Tuple[str, str]] = []
    repair_commands: List[str] = []
    built: List[DocScenario] = []
    for index, (name, playbook) in enumerate(scenarios):
        built.append(
            _build_scenario(
                graph,
                playbook,
                policy,
                shared,
                omitted,
                repair_commands,
                label=scenario_label(index),
                name=name or playbook.symptom.name,
                single=len(scenarios) == 1,
            )
        )

    # A precheck nobody reads from and that decides nothing is just noise.
    referenced: Set[str] = set()
    any_steps = False
    for scenario in built:
        any_steps = any_steps or bool(scenario.steps)
        for step in scenario.steps:
            referenced.update(REUSE_RE.findall(step.reuse_note))
        for row in scenario.routing:
            referenced.update(REUSE_RE.findall(row.precheck))
    shared.prechecks = [
        precheck
        for precheck in shared.prechecks
        if precheck.verdicts
        or not any_steps
        or any(node_id in referenced for node_id in precheck.node_ids)
    ]
    if len(built) > 1:
        shared.prechecks = split_collection(
            shared.prechecks, built, coverage=policy.shared_coverage
        )
    else:
        shared.prechecks = share_across_steps(
            graph, built[0], shared, coverage=policy.shared_coverage
        )
    resolve_references(shared.prechecks, built)

    primary = scenarios[0][1]
    params, dropped_params = _build_params(
        primary,
        shared.prechecks,
        doc_steps(built),
        repair_commands,
        [
            (f"{scenario.title} 采集 {index}", precheck)
            for scenario in built
            for index, precheck in enumerate(scenario.collection, start=1)
        ],
    )
    omitted.extend(dropped_params)
    doc = SkillDoc(
        symptom=primary.symptom,
        params=params,
        prechecks=shared.prechecks,
        scenarios=built,
        omitted=omitted,
    )
    if not shared.prechecks:
        doc.notes.append("子图中没有可用的入口检查，前置检查为空。")
    if not any_steps:
        doc.notes.append("子图中没有可展开的候选原因，排查步骤为空。")
    if omitted:
        doc.notes.append(f"{len(omitted)} 个原因/检查未进入正文，（构建输出逐条列出原因）。")
    return doc


def doc_steps(scenarios: Sequence[DocScenario]) -> List[Step]:
    return [step for scenario in scenarios for step in scenario.steps]


def _build_scenario(
    graph: Graph,
    playbook: Playbook,
    policy: BuildPolicy,
    shared: SharedCollection,
    omitted: List[Tuple[str, str]],
    repair_commands: List[str],
    *,
    label: str,
    name: str,
    single: bool,
) -> DocScenario:
    scenario = DocScenario(label=label, name=name, symptom=playbook.symptom)
    branch_causes = {branch.cause.node_id for branch in playbook.causes}
    branch_keys = {fault_key(branch.cause.name) for branch in playbook.causes}
    precheck_causes: Dict[str, Tuple[List[str], str]] = {}
    routing_seen: Set[str] = set()

    for step in playbook.entry_checks:
        check = step.check
        if not _usable(check, policy):
            omitted.append((check.name, "检查动作带案例特定背景（example_specific）"))
            continue
        commands, rejected = command_templates_with_rejections(check)
        for rejection in rejected:
            omitted.append((f"{check.name}：`{rejection.text}`", rejection.reason))
        # One command, one collection step: a graph holds many check nodes that
        # run the same thing, and repeating it is what bloats the document.
        # Abbreviated and spelled-out forms are the same thing.
        index = equivalent_precheck(shared.prechecks, commands) if commands else None
        if index is not None:
            precheck = shared.prechecks[index - 1]
            precheck.collect = merge_collect(
                precheck.collect, collect_line(check, _observed(step))
            )
            precheck.node_ids.append(check.node_id)
            register_variants(precheck, commands)
        else:
            precheck = Precheck(
                title=check.name,
                commands=commands,
                collect=collect_line(check, _observed(step)),
                node_id=check.node_id,
                node_ids=[check.node_id],
                literals=case_literals(" ".join(commands)),
                hardcoded=hygiene.hardcoded_literals(" ".join(commands)),
            )
            for _command, variants in command_variants(check):
                precheck.variants.extend(variants)
            shared.prechecks.append(precheck)
            index = len(shared.prechecks)
        precheck.add_owner(label)
        shared.of_check[check.node_id] = index

        # What this reading tells the reader to open next.
        if not single:
            for outcome in step.outcomes:
                expression = observation_expression(outcome.observation)
                if expression and expression not in routing_seen and len(scenario.routing) < 3:
                    routing_seen.add(expression)
                    precheck.routes = True
                    scenario.routing.append(
                        RoutingRow(
                            precheck=f"@@{precheck.node_ids[0]}@@"
                            + (f"（`{commands[0]}`）" if commands else ""),
                            criterion=f"`{expression}`",
                            scenario=f"场景{label}：{name}",
                        )
                    )

        for observation, verdict in _decisive(step):
            # Causes that get their own step are judged there, once, not twice.
            if verdict.kind == "excludes" or verdict.cause.node_id in branch_causes:
                continue
            if fault_key(verdict.cause.name) in branch_keys:
                continue
            if not (_usable(verdict.cause, policy) and _usable(observation, policy)):
                continue
            strength = _verdict_strength(verdict.kind)
            precheck.verdicts.append(
                f"若 {_criterion(observation)}，判定根因为“{verdict.cause.name}”{strength}"
                + (f"（{scenario.title}）" if not single else "")
                + "，结束排查。"
            )
            entry = precheck_causes.setdefault(verdict.cause.name, ([], verdict.cause.node_id))
            criterion = f"{_criterion(observation)}{strength}"
            if criterion not in entry[0]:
                entry[0].append(criterion)

    # Indexed by fault identity, not node id: when sources are merged the cause
    # that a verdict points at may be another source's node for the same fault,
    # while the repairs hang off the one we kept.
    branch_by_cause: Dict[str, CauseBranch] = {
        fault_key(b.cause.name): b for b in playbook.causes
    }
    step_causes: Dict[str, Tuple[List[str], str]] = {}

    for branch in playbook.causes:
        if not _usable(branch.cause, policy):
            omitted.append((branch.cause.name, "原因带案例特定背景（example_specific）"))
            continue
        noise = hygiene.looks_like_cause_name(branch.cause.name)
        if noise is not None:
            # A sentence out of the surrounding text, or a row of screen output.
            omitted.append((branch.cause.name, noise.reason))
            continue
        decisive = [
            pair
            for pair in _branch_decisive(branch)
            if _usable(pair[0], policy) and _usable(pair[1].cause, policy)
        ]
        if not decisive and not branch.repairs and not policy.keep_undecidable:
            # A step with no criterion and no fix tells the reader nothing.
            omitted.append((branch.cause.name, "既无判定观测也无修复动作"))
            continue
        if policy.max_steps and len(scenario.steps) >= policy.max_steps:
            omitted.append((branch.cause.name, f"超出 --max-steps {policy.max_steps} 限制"))
            continue

        commands, reuse_note = _step_commands(
            branch, decisive, shared.producing_check, shared.of_check, shared.prechecks
        )
        issued_by = ""
        for own in branch.checks if commands else ():
            if same_command_set(commands, command_templates(own.check)):
                issued_by = own.check.node_id
                break
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
            entry = step_causes.setdefault(verdict.cause.name, ([], verdict.cause.node_id))
            if f"{criterion}{strength}" not in entry[0]:
                entry[0].append(f"{criterion}{strength}")
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
                (["本子图未给出判定观测（无 `confirms` / `supports` 关系）"], branch.cause.node_id),
            )

        scenario.steps.append(
            Step(
                index=len(scenario.steps) + 1,
                name=f"检查{branch.cause.name}",
                commands=commands,
                reuse_note=reuse_note,
                branches=branches,
                causes=causes,
                literals=case_literals(" ".join(commands)),
                check_id=issued_by,
            )
        )

    # Fall-through wording: every step but the last hands over to the next one.
    for step in scenario.steps:
        if step.index < len(scenario.steps):
            following = f"顺序执行步骤 {step.index + 1}。"
        else:
            following = f"判定“{NOT_FOUND}”，输出已执行的全部检查步骤及结果摘要，结束排查。"
        for branch in step.branches:
            branch.outcome = branch.outcome.replace("{next}", following)
        step.branches.append(Branch("以上判据均不命中", following))

    for cause_name, (criteria, node_id) in {**precheck_causes, **step_causes}.items():
        cause_node = graph.get(node_id)
        branch = branch_by_cause.get(fault_key(cause_name))
        fix, recheck, commands = _repair_fix(graph, cause_node, branch)
        repair_commands.extend(commands)
        # This cause is where the source hands over to another fault; a reader
        # who lands on this row is the one who needs to know.
        handoffs = handoffs_of(branch.refers_to + branch.leads_to, policy) if branch else []
        scenario.root_causes.append(
            RootCause(
                name=cause_name,
                evidence=cell("；".join(criteria)),
                fix=_with_handoffs(cell(fix), handoffs),
                recheck=cell(recheck),
            )
        )
    # Nothing matched and the document is out of steps — if the source said
    # where to go next, this is the row that has to say so, instead of
    # telling the reader to stop where the source did not.
    scenario.root_causes.append(
        RootCause(
            name=NOT_FOUND,
            evidence="全部步骤走完仍未命中任何故障特征",
            fix=_with_handoffs(
                "输出已执行的全部检查步骤及结果摘要",
                handoffs_of(playbook.referrals, policy, note=False),
            ),
            recheck="-",
        )
    )

    if not single and not scenario.routing:
        # No reading in the graph separates this scenario from the others.
        triggers = "、".join(playbook.trigger_terms()[:3])
        scenario.routing.append(
            RoutingRow(
                precheck="（来源未给出可判定的回显判据）",
                criterion=f"按现象与告警匹配：{triggers}" if triggers else "按现象与告警匹配",
                scenario=f"场景{label}：{name}",
            )
        )
    return scenario


def _usable(node: Node, policy: BuildPolicy) -> bool:
    """False for material that only describes the incident a case was written about."""
    return policy.include_example_specific or not node.example_specific


def handoffs_of(
    links: Sequence[Link], policy: BuildPolicy, *, note: bool = True
) -> List[Handoff]:
    """Cross-fault links of one node, as hand-offs the document can render.

    ``leads_to`` between two causes stays inside this document's own subject,
    so only a symptom (another fault) or an escalation (a person) is a hand-off.
    """
    found: List[Handoff] = []
    seen: Set[str] = set()
    for link in links:
        node = link.node
        if node.node_type not in ("symptom", "escalation") or node.node_id in seen:
            continue
        if not _usable(node, policy):
            continue
        seen.add(node.node_id)
        found.append(
            Handoff(
                kind="fault" if node.node_type == "symptom" else "escalation",
                name=node.name,
                condition=format_condition(link.edge.condition),
                slug=policy.skill_index.get(node.node_id, ""),
                note=HANDOFF_NOTES.get(link.edge.edge_type, "") if note else "",
                batch=bool(policy.skill_index),
            )
        )
    return found


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


def _with_handoffs(text: str, handoffs: Sequence["Handoff"]) -> str:
    """Append hand-offs to a cell, each one whole.

    They go through :func:`cell` separately so that a long repair cannot push
    a hand-off past the truncation limit — where the reader is sent next is
    not the part to lose.
    """
    if not handoffs:
        return text
    return "<br>".join([text] + [cell(handoff.render()) for handoff in handoffs])


def _observed(step: CheckStep) -> List[Node]:
    return [outcome.observation for outcome in step.outcomes]


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

    def cite(index: int) -> Tuple[List[str], str]:
        precheck = prechecks[index - 1]
        # Which phase this points at — shared or the scenario's own — is only
        # known once every scenario is built, so cite by identity for now.
        return [], f"复用@@{precheck.node_ids[0]}@@ 回显（{precheck.title}{field_note}）"

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
        # The same command may already be collected under another check node,
        # possibly spelled out where this one abbreviates it.
        same = equivalent_precheck(prechecks, commands) if commands else None
        if same:
            return cite(same)
        if commands:
            return commands, ""
        return [], f"{step.check.name}：来源未给出命令模板，按来源步骤说明人工执行"
    return [], "复用前置检查回显" if prechecks else "本子图未给出可用的检查动作，只能依据现象判断"


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


def _build_params(
    playbook: Playbook,
    prechecks: Sequence[Precheck],
    steps: Sequence[Step],
    repair_commands: Sequence[str] = (),
    scenario_collection: Sequence[Tuple[str, Precheck]] = (),
) -> Tuple[List[Param], List[Tuple[str, str]]]:
    """Required slots plus every ``<token>`` the document's commands actually use.

    Returns the parameters and the slots that were dropped with the reason —
    a slot the document never references is a barrier to entry, not an input.
    """
    params: Dict[str, Param] = {}
    dropped: List[Tuple[str, str]] = []

    for slot in playbook.required_slots():
        key = param_key(slot)
        if not key:
            if slot.strip():
                # Nothing but the extractor's hash: there is no question to ask.
                dropped.append((slot, "伪参数：只剩抽取哈希，没有可填的名字"))
            continue
        if hygiene.is_topology_label(slot):
            # A letter off the source's topology diagram; nobody can fill it in.
            dropped.append((slot, "伪参数：来源示意图的设备编号，现场没有这个名字"))
            continue
        # ``peer ip c8be5e6454`` is one input, however many sources hashed it.
        general = hygiene.generalise_slot(slot)
        params[key] = Param(general, param_display(general), True, "现场提供")

    precheck_tokens: Set[str] = set()
    collection = [(f"前置检查步骤 {index}", precheck) for index, precheck in enumerate(prechecks, start=1)]
    # A scenario's own collection asks the field for its parameters just the
    # same; only where it is run differs.
    collection += [(where, precheck) for where, precheck in scenario_collection]
    for where, precheck in collection:
        for token in parameters_in(precheck.commands):
            key = param_key(token)
            precheck_tokens.add(key)
            existing = params.get(key)
            note = f"{where} 命令参数"
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
    return list(params.values()), dropped
