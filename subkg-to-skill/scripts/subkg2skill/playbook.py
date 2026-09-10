"""Assemble per-symptom diagnostic playbooks out of the raw subgraph.

One playbook per ``symptom`` node: entry checks, candidate causes, the checks
that discriminate between them, what each check can observe, which observation
confirms / supports / excludes which cause, and the repairs or escalations the
source allows.  This module only walks the graph — the wording lives in
:mod:`subkg2skill.render`.
"""

from __future__ import annotations

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


@dataclass
class Playbook:
    """Everything needed to render one symptom's troubleshooting document."""

    symptom: Node
    entry_checks: List[CheckStep] = field(default_factory=list)
    entry_actions: List[Link] = field(default_factory=list)
    causes: List[CauseBranch] = field(default_factory=list)
    referrals: List[Link] = field(default_factory=list)
    covered: Set[str] = field(default_factory=set)

    @property
    def title(self) -> str:
        return self.symptom.name

    @property
    def node_id(self) -> str:
        return self.symptom.node_id

    def trigger_terms(self) -> List[str]:
        terms = [self.symptom.name] + self.symptom.aliases + self.symptom.match_phrases
        seen: List[str] = []
        for term in terms:
            term = term.strip()
            if term and term not in seen:
                seen.append(term)
        return seen

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
