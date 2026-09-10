"""Assemble per-symptom diagnostic playbooks out of the raw subgraph.

One playbook per ``symptom`` node: entry checks, candidate causes, the checks
that discriminate between them, what each check can observe, which observation
confirms / supports / excludes which cause, and the repairs or escalations the
source allows.  This module only walks the graph — the wording lives in
:mod:`subkg2skill.render`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

from subkg2skill import schema
from subkg2skill.graph import Edge, Graph, Node

#: How far ``next_step`` chains are followed before the playbook stops unrolling.
MAX_CHAIN = 6


@dataclass
class Link:
    """A neighbour plus the edge that reached it."""

    node: Node
    edge: Edge


@dataclass
class Verdict:
    """An observation's stance on one cause."""

    observation: Node
    cause: Node
    edge: Edge  # confirms / supports / excludes

    @property
    def kind(self) -> str:
        return self.edge.edge_type

    @property
    def label(self) -> str:
        return schema.VERDICT_LABELS.get(self.kind, self.kind)


@dataclass
class ObservationOutcome:
    """One possible result of a check, with what it decides and what follows."""

    observation: Node
    edge: Edge  # observes
    verdicts: List[Verdict] = field(default_factory=list)
    next_steps: List[Link] = field(default_factory=list)


@dataclass
class CheckStep:
    """A check action, how it was reached, and its possible outcomes."""

    check: Node
    edge: Optional[Edge] = None
    outcomes: List[ObservationOutcome] = field(default_factory=list)
    next_steps: List[Link] = field(default_factory=list)
    repeated: bool = False  # already expanded elsewhere in this playbook


@dataclass
class CauseBranch:
    """A candidate cause hanging off the symptom, with its discriminators."""

    cause: Node
    edge: Edge  # has_cause
    checks: List[CheckStep] = field(default_factory=list)
    repairs: List[Link] = field(default_factory=list)
    verdicts: List[Verdict] = field(default_factory=list)
    refines: List[Link] = field(default_factory=list)
    leads_to: List[Link] = field(default_factory=list)
    refers_to: List[Link] = field(default_factory=list)

    def verdicts_of(self, kind: str) -> List[Verdict]:
        return [verdict for verdict in self.verdicts if verdict.kind == kind]


#: Punctuation and spacing that spelling differences hide behind (IS-IS vs ISIS).
_KEY_STRIP_RE = re.compile(r"[\s\-_·/\\（）()\[\]【】：:，,。.、~!！?？\"'“”‘’]+")


def fault_key(name: str) -> str:
    """Identity of a fault across sources.

    The same fault is written differently in a manual, a battle tree and a case
    library — ``IS-IS邻居无法建立`` and ``ISIS邻居无法建立`` are one thing, and
    keeping them apart produces two thin skills instead of one complete one.
    """
    folded = unicodedata.normalize("NFKC", name or "").lower()
    return _KEY_STRIP_RE.sub("", folded)


@dataclass
class Playbook:
    """Everything needed to render one symptom's troubleshooting document."""

    symptom: Node
    entry_checks: List[CheckStep] = field(default_factory=list)
    entry_actions: List[Link] = field(default_factory=list)
    causes: List[CauseBranch] = field(default_factory=list)
    referrals: List[Link] = field(default_factory=list)
    covered: Set[str] = field(default_factory=set)
    #: Every symptom this document covers — more than one when sources were merged.
    merged: List[Node] = field(default_factory=list)
    #: Diagnostic units the merged sources came from.
    units: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.symptom.name

    @property
    def node_id(self) -> str:
        return self.symptom.node_id

    @property
    def symptoms(self) -> List[Node]:
        return self.merged or [self.symptom]

    def trigger_terms(self) -> List[str]:
        terms: List[str] = []
        for symptom in self.symptoms:
            terms += [symptom.name] + symptom.aliases + symptom.match_phrases
        seen: List[str] = []
        for term in terms:
            term = term.strip()
            if term and term not in seen:
                seen.append(term)
        return seen

    def required_slots(self) -> List[str]:
        """Slots every merged source asks the field for."""
        slots: List[str] = []
        for symptom in self.symptoms:
            for slot in symptom.attrs.get("required_slots") or []:
                slot = str(slot).strip()
                if slot and slot not in slots:
                    slots.append(slot)
        return slots

    @property
    def check_count(self) -> int:
        return len({step.check.node_id for step in self._all_checks()})

    def _all_checks(self) -> List[CheckStep]:
        steps = list(self.entry_checks)
        for branch in self.causes:
            steps.extend(branch.checks)
        return steps

    @property
    def repair_count(self) -> int:
        return len({link.node.node_id for branch in self.causes for link in branch.repairs})


def _links(graph: Graph, node_id: str, *edge_types: str) -> List[Link]:
    return [Link(node, edge) for node, edge in graph.targets(node_id, *edge_types)]


def _build_check_step(
    graph: Graph,
    check: Node,
    edge: Optional[Edge],
    *,
    expanded: Set[str],
    causes_in_scope: Set[str],
) -> CheckStep:
    """Expand a check into its observations; repeats are marked, not re-expanded."""
    if check.node_id in expanded:
        return CheckStep(check=check, edge=edge, repeated=True)
    expanded.add(check.node_id)
    step = CheckStep(check=check, edge=edge)
    for observation, observes_edge in graph.targets(check.node_id, "observes"):
        outcome = ObservationOutcome(observation=observation, edge=observes_edge)
        for cause, verdict_edge in graph.targets(observation.node_id, *schema.VERDICT_EDGES):
            if causes_in_scope and cause.node_id not in causes_in_scope:
                continue
            outcome.verdicts.append(
                Verdict(observation=observation, cause=cause, edge=verdict_edge)
            )
        outcome.verdicts.sort(
            key=lambda v: (schema.VERDICT_EDGES.index(v.kind), v.cause.name)
        )
        outcome.next_steps = _links(graph, observation.node_id, "next_step")
        step.outcomes.append(outcome)
    step.next_steps = _links(graph, check.node_id, "next_step")
    return step


def _chain(graph: Graph, start: Node, *, expanded: Set[str], causes_in_scope: Set[str]) -> List[CheckStep]:
    """Follow a ``next_step`` chain of checks from *start*, bounded by MAX_CHAIN."""
    steps: List[CheckStep] = []
    current: Optional[Node] = start
    incoming: Optional[Edge] = None
    seen: Set[str] = set()
    while current is not None and len(steps) < MAX_CHAIN:
        if current.node_id in seen:
            break
        seen.add(current.node_id)
        step = _build_check_step(
            graph, current, incoming, expanded=expanded, causes_in_scope=causes_in_scope
        )
        steps.append(step)
        following = [
            link for link in step.next_steps if link.node.node_type == "check"
        ]
        if step.repeated or len(following) != 1:
            break
        current, incoming = following[0].node, following[0].edge
    return steps


def build_playbook(graph: Graph, symptom: Node) -> Playbook:
    """Walk the subgraph outward from *symptom* into a playbook structure."""
    playbook = Playbook(symptom=symptom)
    playbook.covered.add(symptom.node_id)
    expanded: Set[str] = set()

    cause_links = sorted(
        _links(graph, symptom.node_id, "has_cause"),
        key=lambda link: (link.edge.sort_key, link.node.name),
    )
    causes_in_scope = {link.node.node_id for link in cause_links}

    for node, edge in graph.targets(symptom.node_id, "diagnosed_by"):
        playbook.entry_checks.extend(
            _chain(graph, node, expanded=expanded, causes_in_scope=causes_in_scope)
        )
    for link in _links(graph, symptom.node_id, "next_step"):
        if link.node.node_type == "check":
            playbook.entry_checks.extend(
                _chain(graph, link.node, expanded=expanded, causes_in_scope=causes_in_scope)
            )
        else:
            playbook.entry_actions.append(link)
    playbook.referrals = _links(graph, symptom.node_id, "refers_to")

    for link in cause_links:
        branch = CauseBranch(cause=link.node, edge=link.edge)
        for node, edge in graph.targets(link.node.node_id, "diagnosed_by"):
            branch.checks.extend(
                _chain(graph, node, expanded=expanded, causes_in_scope=causes_in_scope)
            )
        branch.repairs = _links(graph, link.node.node_id, "repaired_by")
        for observation, verdict_edge in graph.sources(link.node.node_id, *schema.VERDICT_EDGES):
            branch.verdicts.append(
                Verdict(observation=observation, cause=link.node, edge=verdict_edge)
            )
        branch.verdicts.sort(
            key=lambda v: (schema.VERDICT_EDGES.index(v.kind), v.observation.name)
        )
        branch.refines = _links(graph, link.node.node_id, "refines")
        branch.leads_to = _links(graph, link.node.node_id, "leads_to")
        branch.refers_to = _links(graph, link.node.node_id, "refers_to")
        playbook.causes.append(branch)

    playbook.covered |= _covered_ids(playbook)
    return playbook


def _covered_ids(playbook: Playbook) -> Set[str]:
    covered: Set[str] = {playbook.symptom.node_id}
    for link in playbook.entry_actions + playbook.referrals:
        covered.add(link.node.node_id)
    steps = list(playbook.entry_checks)
    for branch in playbook.causes:
        covered.add(branch.cause.node_id)
        steps.extend(branch.checks)
        covered.update(link.node.node_id for link in branch.repairs)
        covered.update(link.node.node_id for link in branch.refines)
        covered.update(link.node.node_id for link in branch.leads_to)
        covered.update(link.node.node_id for link in branch.refers_to)
        covered.update(verdict.observation.node_id for verdict in branch.verdicts)
    for step in steps:
        covered.add(step.check.node_id)
        for outcome in step.outcomes:
            covered.add(outcome.observation.node_id)
            covered.update(link.node.node_id for link in outcome.next_steps)
            covered.update(verdict.cause.node_id for verdict in outcome.verdicts)
        covered.update(link.node.node_id for link in step.next_steps)
    return covered


def entry_symptoms(graph: Graph) -> List[Node]:
    """Symptoms worth a document, best entry points first.

    A symptom that nothing refers to and that owns causes or checks is a real
    diagnostic entry; one that only exists as the target of ``refers_to`` /
    ``refines`` is usually a cross reference, so it sorts last.
    """

    def weight(node: Node) -> Tuple[int, int, str]:
        outgoing = len(graph.out_edges(node.node_id, "has_cause", "diagnosed_by", "next_step"))
        referenced = len(graph.in_edges(node.node_id, "refers_to", "refines", "leads_to"))
        rank = 0 if outgoing else 1
        if not outgoing and referenced:
            rank = 2
        return (rank, -outgoing, node.name)

    return sorted(graph.of_type("symptom"), key=weight)


def _merge_branches(target: CauseBranch, extra: CauseBranch) -> None:
    """Fold *extra* into *target*: same cause, documented by another source."""
    seen_checks = {step.check.node_id for step in target.checks}
    for step in extra.checks:
        if step.check.node_id not in seen_checks:
            seen_checks.add(step.check.node_id)
            target.checks.append(step)
    seen_repairs = {link.node.node_id for link in target.repairs}
    for link in extra.repairs:
        if link.node.node_id not in seen_repairs:
            seen_repairs.add(link.node.node_id)
            target.repairs.append(link)
    seen_verdicts = {(v.observation.node_id, v.kind) for v in target.verdicts}
    for verdict in extra.verdicts:
        key = (verdict.observation.node_id, verdict.kind)
        if key not in seen_verdicts:
            seen_verdicts.add(key)
            target.verdicts.append(verdict)
    target.verdicts.sort(key=lambda v: (schema.VERDICT_EDGES.index(v.kind), v.observation.name))
    for attribute in ("refines", "leads_to", "refers_to"):
        current = getattr(target, attribute)
        seen = {link.node.node_id for link in current}
        for link in getattr(extra, attribute):
            if link.node.node_id not in seen:
                seen.add(link.node.node_id)
                current.append(link)


def build_merged_playbook(
    graph: Graph, symptoms: Sequence[Node], units: Sequence[str] = ()
) -> Playbook:
    """One document covering the same fault as several sources describe it.

    Causes are folded together by name, so a cause the manual and the case
    library both mention becomes one step carrying both sources' checks,
    observations and repairs — rather than two steps with the same title.
    """
    if not symptoms:
        raise ValueError("至少需要一个症状节点")
    parts = [build_playbook(graph, symptom) for symptom in symptoms]
    merged = parts[0]
    merged.merged = list(symptoms)
    merged.units = [unit for unit in units if unit]
    by_cause: Dict[str, CauseBranch] = {fault_key(b.cause.name): b for b in merged.causes}
    seen_checks = {step.check.node_id for step in merged.entry_checks}
    seen_links = {link.node.node_id for link in merged.referrals}

    for part in parts[1:]:
        for step in part.entry_checks:
            if step.check.node_id not in seen_checks:
                seen_checks.add(step.check.node_id)
                merged.entry_checks.append(step)
        for branch in part.causes:
            key = fault_key(branch.cause.name)
            existing = by_cause.get(key)
            if existing is None:
                by_cause[key] = branch
                merged.causes.append(branch)
            else:
                _merge_branches(existing, branch)
        for link in part.referrals:
            if link.node.node_id not in seen_links:
                seen_links.add(link.node.node_id)
                merged.referrals.append(link)
        for link in part.entry_actions:
            merged.entry_actions.append(link)
        merged.covered |= part.covered
    return merged


@dataclass
class FaultGroup:
    """Scenarios that describe the same fault, across sources and units."""

    key: str
    symptoms: List[Node]
    units: List[str]
    causes: int
    checks: int

    @property
    def primary(self) -> Node:
        return self.symptoms[0]

    @property
    def name(self) -> str:
        return self.primary.name

    @property
    def sources(self) -> int:
        return len(self.symptoms)


def fault_groups(graph: Graph, *, min_causes: int = 1, merge: bool = True) -> List[FaultGroup]:
    """Group scenarios that describe the same fault in different sources.

    Only scenarios from **different symptom nodes** are merged — a manual, a
    battle tree and a case library each writing up ``IS-IS邻居无法建立``.  The
    several diagnostic units of one node stay apart: those are different faults
    that a merged graph happened to hang off the same node, and folding them
    together is what produced ninety-step documents.  A node contributing
    several units joins the merge with its richest unit; the rest stand alone.
    """
    scenarios = entry_scenarios(graph, min_causes=min_causes)
    if not merge:
        return [
            FaultGroup(
                key=scenario.key,
                symptoms=[scenario.symptom],
                units=[scenario.unit] if scenario.unit else [],
                causes=scenario.causes,
                checks=scenario.checks,
            )
            for scenario in scenarios
        ]

    by_node: Dict[str, List[Scenario]] = defaultdict(list)
    for scenario in scenarios:
        by_node[scenario.symptom.node_id].append(scenario)

    mergeable: List[Scenario] = []
    standalone: List[Scenario] = []
    for items in by_node.values():
        items.sort(key=lambda s: (-s.causes, s.unit))
        mergeable.append(items[0])
        standalone.extend(items[1:])

    grouped: Dict[str, FaultGroup] = {}
    order: List[str] = []
    for scenario in mergeable:
        key = fault_key(scenario.symptom.name)
        group = grouped.get(key)
        if group is None:
            grouped[key] = FaultGroup(
                key=key,
                symptoms=[scenario.symptom],
                units=[scenario.unit] if scenario.unit else [],
                causes=scenario.causes,
                checks=scenario.checks,
            )
            order.append(key)
            continue
        group.symptoms.append(scenario.symptom)
        if scenario.unit and scenario.unit not in group.units:
            group.units.append(scenario.unit)
        group.causes += scenario.causes
        group.checks += scenario.checks

    groups = [grouped[key] for key in order]
    groups += [
        FaultGroup(
            key=scenario.key,
            symptoms=[scenario.symptom],
            units=[scenario.unit] if scenario.unit else [],
            causes=scenario.causes,
            checks=scenario.checks,
        )
        for scenario in standalone
    ]
    groups.sort(key=lambda g: (-g.causes, g.name, "|".join(g.units)))
    return groups


@dataclass
class MergeSuggestion:
    """Two faults that look like one thing under two names."""

    left: FaultGroup
    right: FaultGroup
    shared_causes: List[str]
    shared_commands: List[str]
    overlap: float

    @property
    def entries(self) -> List[Node]:
        return self.left.symptoms + self.right.symptoms

    @property
    def units(self) -> List[str]:
        return list(dict.fromkeys(self.left.units + self.right.units))


def _group_signature(graph: Graph, group: "FaultGroup") -> Tuple[Dict[str, str], Set[str]]:
    """Cause names and check commands this fault is described by."""
    scoped = graph.scope_to_units(group.units) if group.units else graph
    causes: Dict[str, str] = {}
    commands: Set[str] = set()
    for symptom in group.symptoms:
        for cause, _edge in scoped.targets(symptom.node_id, "has_cause"):
            causes[fault_key(cause.name)] = cause.name
            for check, _e in scoped.targets(cause.node_id, "diagnosed_by"):
                commands.update(_commands_of(check))
        for check, _edge in scoped.targets(symptom.node_id, "diagnosed_by"):
            commands.update(_commands_of(check))
    return causes, commands


def _commands_of(check: Node) -> Set[str]:
    raw = check.attrs.get("command_templates") or []
    return {re.sub(r"\s+", " ", str(command)).strip() for command in raw if str(command).strip()}


def suggest_merges(
    graph: Graph,
    groups: Sequence["FaultGroup"],
    *,
    min_shared_causes: int = 2,
    min_overlap: float = 0.34,
) -> List[MergeSuggestion]:
    """Faults worth a human look before they are generated as separate skills.

    Names differ, but the causes (and the commands used to tell them apart)
    largely coincide — usually one fault written from two angles.  This only
    ever *suggests*: two causes named alike can still need different fixes,
    so the decision stays with a person.
    """
    signatures = {id(group): _group_signature(graph, group) for group in groups}
    by_cause: Dict[str, List["FaultGroup"]] = defaultdict(list)
    for group in groups:
        for key in signatures[id(group)][0]:
            by_cause[key].append(group)

    seen: Set[Tuple[int, int]] = set()
    suggestions: List[MergeSuggestion] = []
    for candidates in by_cause.values():
        for index, left in enumerate(candidates):
            for right in candidates[index + 1 :]:
                pair = tuple(sorted((id(left), id(right))))
                if pair in seen or fault_key(left.name) == fault_key(right.name):
                    continue
                seen.add(pair)
                left_causes, left_commands = signatures[id(left)]
                right_causes, right_commands = signatures[id(right)]
                shared = set(left_causes) & set(right_causes)
                union = set(left_causes) | set(right_causes)
                if len(shared) < min_shared_causes or not union:
                    continue
                overlap = len(shared) / len(union)
                if overlap < min_overlap:
                    continue
                suggestions.append(
                    MergeSuggestion(
                        left=left,
                        right=right,
                        shared_causes=sorted(left_causes[key] for key in shared),
                        shared_commands=sorted(left_commands & right_commands),
                        overlap=overlap,
                    )
                )
    suggestions.sort(key=lambda s: (-s.overlap, -len(s.shared_causes), s.left.name))
    return suggestions


@dataclass
class Scenario:
    """One fault entry inside one diagnostic unit — the unit of a skill."""

    symptom: Node
    unit: str
    causes: int
    checks: int

    @property
    def key(self) -> str:
        return f"{self.symptom.node_id}@{self.unit}" if self.unit else self.symptom.node_id


def entry_scenarios(graph: Graph, *, min_causes: int = 0) -> List[Scenario]:
    """Split every entry symptom by the diagnostic units its relations live in.

    One symptom in a merged graph can carry causes from dozens of chapters and
    cases; each of those is a separate troubleshooting scenario, and turning
    them into one document is what produces hundred-step skills.
    """
    scenarios: List[Scenario] = []
    for symptom in entry_symptoms(graph):
        # Count straight off the symptom's own edges — scoping the whole graph
        # once per scenario turns a 17k-edge export into an O(n²) build.
        causes: Dict[str, int] = {}
        checks: Dict[str, int] = {}
        unscoped_causes = 0
        unscoped_checks = 0
        for edge in graph.out_edges(symptom.node_id, "has_cause", "diagnosed_by"):
            bucket = causes if edge.edge_type == "has_cause" else checks
            if edge.section:
                bucket[edge.section] = bucket.get(edge.section, 0) + 1
            elif edge.edge_type == "has_cause":
                unscoped_causes += 1
            else:
                unscoped_checks += 1
        units = set(causes) | set(checks)
        if not units:
            units = {""}
        for unit in units:
            # Relations with no unit of their own stay in every scenario.
            count = causes.get(unit, 0) + unscoped_causes
            if count < min_causes:
                continue
            scenarios.append(
                Scenario(
                    symptom=symptom,
                    unit=unit,
                    causes=count,
                    checks=checks.get(unit, 0) + unscoped_checks,
                )
            )
    scenarios.sort(key=lambda s: (-s.causes, s.symptom.name, s.unit))
    return scenarios


def build_playbooks(graph: Graph, *, limit: int = 0) -> List[Playbook]:
    """Build playbooks for every symptom (or the first *limit* entry points)."""
    symptoms = entry_symptoms(graph)
    if limit:
        symptoms = symptoms[:limit]
    return [build_playbook(graph, symptom) for symptom in symptoms]


def coverage(graph: Graph, playbooks: Sequence[Playbook]) -> Dict[str, List[Node]]:
    """Split nodes into those documented by a playbook and those left out."""
    covered: Set[str] = set()
    for playbook in playbooks:
        covered |= playbook.covered
    documented = [graph.nodes[nid] for nid in covered if nid in graph.nodes]
    missing = [node for node in graph.iter_nodes() if node.node_id not in covered]
    return {"documented": documented, "uncovered": missing}
