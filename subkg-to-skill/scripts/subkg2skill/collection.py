"""Where each collection step goes.

One command is one collection step: a graph holds many check nodes that run
the same command, so they are merged here.  Each step then has to be placed —
in the shared 前置检查 every reader runs, in one scenario's own 本场景采集, or
back inside the one 排查步骤 that reads it — and steps cite it by node id until
the placement is final, when :func:`resolve_references` numbers them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from subkg2skill import hygiene, schema
from subkg2skill.commands import command_signature, command_variants, same_command_set
from subkg2skill.condition import observation_expression
from subkg2skill.doc import DocScenario, Precheck, RoutingRow, Step
from subkg2skill.graph import Graph, Node, _string_list, _text
from subkg2skill.playbook import fault_key


@dataclass
class SharedCollection:
    """Collection phase shared by every scenario in one document."""

    prechecks: List[Precheck] = field(default_factory=list)
    of_check: Dict[str, int] = field(default_factory=dict)
    producing_check: Dict[str, str] = field(default_factory=dict)


def equivalent_precheck(prechecks: Sequence["Precheck"], commands: Sequence[str]) -> Optional[int]:
    """1-based index of the collection step already running *commands*."""
    for index, precheck in enumerate(prechecks, start=1):
        if same_command_set(commands, precheck.commands):
            return index
    return None


def _observations_of(graph: Graph, check: Node) -> List[Node]:
    return [node for node, _edge in graph.targets(check.node_id, "observes")]


def collect_line(check: Node, observations: Sequence[Node]) -> str:
    """What this check is being run to read off the screen."""
    parts: List[str] = []
    intent = _text(check.attr("intent"))
    if intent:
        parts.append(intent)
    fields: List[str] = []
    for observation in observations:
        field_name = _text(observation.attr("field"))
        if field_name and field_name not in fields:
            fields.append(field_name)
    if fields:
        parts.append("关注字段：" + "、".join(f"`{name}`" for name in fields))
    if not parts:
        procedure = _string_list(check.attrs.get("procedure"))
        if procedure:
            parts.append("；".join(procedure))
    return "；".join(parts) or "按命令回显记录结果"


def merge_collect(existing: str, addition: str) -> str:
    """Union of two collection descriptions, without repeating a phrase."""
    parts: List[str] = []
    for chunk in (existing or "").split("；") + (addition or "").split("；"):
        chunk = chunk.strip()
        if chunk and chunk != "按命令回显记录结果" and chunk not in parts:
            parts.append(chunk)
    return "；".join(parts) or "按命令回显记录结果"


def register_variants(precheck: Precheck, commands: Sequence[str]) -> None:
    """Record a second source's spelling of a command already collected.

    Two sources writing ``advertise-routes`` and ``advertised-routes`` is not a
    thing to silently pick a winner on — the reader is told to confirm against
    the device version.
    """
    for command in commands:
        if command in precheck.commands or command in precheck.variants:
            continue
        if any(hygiene.same_command(command, existing) for existing in precheck.commands):
            precheck.variants.append(command)


#: Steps cite the precheck they read from by node id until the list is final.
REUSE_RE = re.compile(r"@@([^@]+)@@")


#: What a citation resolves to when the collection step it named was pruned.
UNRESOLVED = "（已合并）"


def _readers(precheck: Precheck, scenarios: Sequence[DocScenario]) -> List[str]:
    """Scenario labels that actually read this collection step."""
    labels = list(precheck.owners)
    for scenario in scenarios:
        if scenario.label in labels:
            continue
        cited = {
            node_id
            for step in scenario.steps
            for node_id in REUSE_RE.findall(step.reuse_note)
        }
        cited.update(
            node_id for row in scenario.routing for node_id in REUSE_RE.findall(row.precheck)
        )
        if cited & set(precheck.node_ids):
            labels.append(scenario.label)
    return labels


def split_collection(
    prechecks: Sequence[Precheck], scenarios: Sequence[DocScenario], *, coverage: float
) -> List[Precheck]:
    """Keep the shared collection shared; hand the rest to the scenarios that need it.

    "公共前置" has to mean it.  The two costs are not symmetric: a step left in
    the shared phase is run by *every* reader, including the ones whose fault it
    says nothing about — a command issued on a live device for nothing — while
    sinking it copies a few lines into the scenario sections, which a reader
    never sees more than one of.  So the bar is coverage, not "more than one":
    a step stays shared when at least ``coverage`` of the document's scenarios
    read it (two scenarios means both; ten means eight).

    Two kinds of narrow collection stay shared whatever their coverage, because
    the reader runs them *before* knowing which scenario they are in: one whose
    reading decides a routing row, and one that settles a root cause during
    collection.  They are labelled with who they serve instead.
    """
    total = len(scenarios)
    needed = max(1, math.ceil(coverage * total)) if total else 1
    kept: List[Precheck] = []
    for precheck in prechecks:
        readers = _readers(precheck, scenarios)
        precheck.owners = readers
        if len(readers) >= needed or precheck.routes or precheck.verdicts:
            kept.append(precheck)
            continue
        owners = [scenario for scenario in scenarios if scenario.label in readers]
        if not owners:  # nobody reads it; the pruning pass already allowed it
            kept.append(precheck)
            continue
        # Every scenario that reads it gets its own copy — a reader follows one
        # scenario, so the step has to be there, and nowhere else.
        for scenario in owners:
            scenario.collection.append(precheck)
    return kept


def share_across_steps(
    graph: Graph, scenario: DocScenario, shared: SharedCollection, *, coverage: float
) -> List[Precheck]:
    """Decide the collection phase of a document covering one fault.

    A skill covering several faults asks two questions of every collection
    step: does its reading say which fault to open, and do enough readers need
    it to be worth running before the split.  One fault deserves the same two
    questions one level down, where the sub-scenarios are the steps — a reader
    pays for 前置检查 whichever step turns out to be theirs, and a command the
    steps issue for themselves is a command typed once per step.
    """
    steps = scenario.steps
    if not steps:
        return shared.prechecks
    # Two steps means both; ten means eight.  One step shares nothing.
    needed = max(2, math.ceil(coverage * len(steps)))

    issuing: Dict[str, List[Step]] = {}
    for step in steps:
        if step.commands and step.check_id:
            issuing.setdefault(command_signature(step.commands), []).append(step)
    for group in issuing.values():
        if len(group) < needed:
            continue
        checks = [node for node in (graph.get(step.check_id) for step in group) if node]
        if not checks:
            continue
        check = checks[0]
        # Several check nodes run this one command; the collection step stands
        # for all of them, so every reading they produce is collected here.
        precheck = Precheck(
            title=check.name,
            commands=list(group[0].commands),
            collect="",
            node_id=check.node_id,
            node_ids=[node.node_id for node in checks],
            literals=list(group[0].literals),
            hardcoded=hygiene.hardcoded_literals(" ".join(group[0].commands)),
        )
        for node in checks:
            precheck.collect = merge_collect(
                precheck.collect, collect_line(node, _observations_of(graph, node))
            )
            for _command, variants in command_variants(node):
                precheck.variants.extend(variants)
        shared.prechecks.append(precheck)
        for node in checks:
            shared.of_check[node.node_id] = len(shared.prechecks)
        for step in group:
            step.reuse_note = f"复用@@{check.node_id}@@ 回显（{check.name}）"
            step.commands = []
            step.literals = []
            step.check_id = ""

    # Below the bar it is not everyone's work, but it is still one screen: the
    # steps after the first read what the first one already put there.
    for group in issuing.values():
        if len(group) < 2 or not group[0].commands:
            continue
        first = group[0]
        for step in group[1:]:
            if not step.commands:
                continue
            step.reuse_note = f"复用步骤 {first.index}（{first.name}）的回显"
            step.commands = []
            step.literals = []
            step.check_id = ""

    _route_to_steps(graph, scenario, shared.prechecks)

    kept: List[Precheck] = []
    for precheck in shared.prechecks:
        readers = [
            step
            for step in steps
            if set(REUSE_RE.findall(step.reuse_note)) & set(precheck.node_ids)
        ]
        precheck.owners = [f"步骤 {step.index}：{step.name}" for step in readers]
        if len(readers) != 1 or len(steps) == 1 or precheck.routes or precheck.verdicts:
            kept.append(precheck)
            continue
        # One step of several reads it and the reading routes nowhere: it is
        # that step's own work, and the other readers were typing it for nothing.
        reader = readers[0]
        reader.commands = list(precheck.commands)
        reader.reuse_note = ""
        reader.literals = list(precheck.literals)
        reader.check_id = precheck.node_ids[0]
    return kept


def _route_to_steps(
    graph: Graph, scenario: DocScenario, prechecks: Sequence[Precheck]
) -> None:
    """Rows sending the reader straight to the step a reading picks out.

    Only a reading that picks out *one* step routes anywhere: an observation
    supporting every cause in the document says «若 X 转步骤 1；若 X 转步骤 2»,
    which is one criterion written three times, not a classification.
    """
    step_of_cause: Dict[str, Step] = {}
    for step in scenario.steps:
        for cause in step.causes:
            step_of_cause.setdefault(fault_key(cause), step)
    seen: Set[str] = set()
    for precheck in prechecks:
        for check_id in precheck.node_ids:
            check = graph.get(check_id)
            if check is None:
                continue
            for observation in _observations_of(graph, check):
                targets = {
                    step_of_cause[fault_key(cause.name)].index
                    for cause, edge in graph.targets(
                        observation.node_id, *schema.VERDICT_EDGES
                    )
                    if edge.edge_type != "excludes" and fault_key(cause.name) in step_of_cause
                }
                expression = observation_expression(observation)
                if len(targets) != 1 or not expression or expression in seen:
                    continue
                seen.add(expression)
                step = scenario.steps[targets.pop() - 1]
                precheck.routes = True
                scenario.routing.append(
                    RoutingRow(
                        precheck=f"@@{precheck.node_ids[0]}@@"
                        + (f"（`{precheck.commands[0]}`）" if precheck.commands else ""),
                        criterion=f"`{expression}`",
                        scenario=f"步骤 {step.index}：{step.name}",
                    )
                )
    scenario.routing.sort(key=lambda row: row.scenario)


def resolve_references(prechecks: List[Precheck], scenarios: Sequence[DocScenario]) -> None:
    """Turn the ``@@node_id@@`` placeholders into final collection-step numbers.

    Steps and routing rows are built before it is known which prechecks survive
    pruning — and before it is known whether the one they cite ended up shared
    or inside their own scenario — so they cite it by identity and get both the
    phase and the number here.
    """
    shared: Dict[str, str] = {}
    for index, precheck in enumerate(prechecks, start=1):
        for node_id in precheck.node_ids:
            shared[node_id] = f"前置检查步骤 {index}"

    for scenario in scenarios:
        local: Dict[str, str] = {}
        for index, precheck in enumerate(scenario.collection, start=1):
            for node_id in precheck.node_ids:
                local[node_id] = f"本场景采集 {index}"

        def resolve(text: str, _local: Dict[str, str] = local) -> str:
            return REUSE_RE.sub(
                lambda match: shared.get(match.group(1), _local.get(match.group(1), UNRESOLVED)),
                text,
            )

        for step in scenario.steps:
            step.reuse_note = resolve(step.reuse_note)
            if UNRESOLVED in step.reuse_note:  # the collection it cited did not survive
                step.reuse_note = "复用前置检查回显"
        for row in scenario.routing:
            row.precheck = resolve(row.precheck)
            if UNRESOLVED in row.precheck:
                row.precheck = "（对应采集步骤已合并，见前置检查）"
