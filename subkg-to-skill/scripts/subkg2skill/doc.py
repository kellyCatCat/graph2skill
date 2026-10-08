"""The document model: what a skill contains before it is written out.

Also the fixed wording the generator itself contributes (``未找到根因``,
``无直接修复CLI``, the hand-off labels).  ``lint`` and ``verify`` import it from
here so they can recognise it as scaffolding rather than a claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

from subkg2skill.graph import Node


NO_FIX = "无直接修复CLI（来源未给出修复命令，只能定位）"


NOT_FOUND = "未找到根因"


@dataclass
class Param:
    token: str
    display: str
    required: bool
    note: str


#: Share of a document's scenarios that must read a collection step for it to
#: stay in the shared phase.  Two scenarios means both; ten means eight.
SHARED_COVERAGE = 0.8


@dataclass
class BuildPolicy:
    """What to leave out, so the document stays about one transferable fault."""

    #: Keep nodes marked ``example_specific`` (incident addresses, device names).
    include_example_specific: bool = False
    #: Keep causes that have neither a deciding observation nor a repair.
    keep_undecidable: bool = False
    #: Hard cap on 排查步骤 (0 = no cap); the overflow is reported, not silently cut.
    max_steps: int = 0
    #: How much of the document a collection step must serve to stay shared.
    shared_coverage: float = SHARED_COVERAGE
    #: ``node_id -> slug`` of the skills this batch produced.  A hand-off names
    #: the fault either way; the slug only turns it into something the reader
    #: can open.
    skill_index: Dict[str, str] = field(default_factory=dict)


#: Edges whose target is a *different* fault: the source says so itself, which
#: is why selection never walks them.  Dropping them entirely, though, leaves
#: the reader at "结束排查" where the source went on to name somewhere to go —
#: these are the only sourced seam between two generated skills.
HANDOFF_EDGES = ("refers_to", "leads_to")


#: Why the reader is being sent on, per edge type.
HANDOFF_NOTES = {
    "leads_to": "该根因会引发此故障，处置后复查",
    "refers_to": "来源把这里转向该处",
}


#: Rendered markers.  Never a code span: the verifier reads those as commands.
HANDOFF_LABELS = {"fault": "转向故障", "escalation": "转交"}


NO_SKILL = "本批次未生成对应 skill"


@dataclass
class Handoff:
    """Somewhere the source sends the reader once this document runs out."""

    kind: str  # fault | escalation
    name: str  # the target node's own name, verbatim
    condition: str = ""  # the edge's condition, unevaluated
    slug: str = ""  # the skill covering it, when this batch knows of one
    note: str = ""
    #: Whether a batch was in play at all.  Without one the generator has no
    #: idea whether a skill covers this fault, and saying "none was generated"
    #: would be a claim about a batch that does not exist.
    batch: bool = False

    def render(self) -> str:
        text = f"{HANDOFF_LABELS.get(self.kind, self.kind)}：「{self.name}」"
        # ``，`` not ``；``: the verifier splits fragments on the latter, and a
        # hand-off cut in half stops being checkable as one.
        detail: List[str] = []
        if self.kind == "fault" and (self.slug or self.batch):
            detail.append(f"skill: {self.slug}" if self.slug else NO_SKILL)
        if self.condition:
            detail.append(f"条件：{self.condition}")
        if detail:
            text += "（" + "，".join(detail) + "）"
        return f"{text}——{self.note}" if self.note else text


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
    #: Other spellings sources gave for these commands — registered, never chosen between.
    variants: List[str] = field(default_factory=list)
    #: Example values (``slot 3``) that break on the next device.
    hardcoded: List[str] = field(default_factory=list)
    #: Scenario labels that read this collection step — one label means it is
    #: that scenario's own work, not something every reader should run.
    owners: List[str] = field(default_factory=list)
    #: True when a routing row is decided by this reading, which is why a
    #: single-scenario check can still belong to the shared phase.
    routes: bool = False

    def add_owner(self, label: str) -> None:
        if label and label not in self.owners:
            self.owners.append(label)


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
    #: The check whose command this step issues, while it issues one of its own.
    check_id: str = ""


@dataclass
class RootCause:
    name: str
    evidence: str
    fix: str
    recheck: str


@dataclass
class RoutingRow:
    """One line of the 场景跳转表 after the shared collection phase."""

    precheck: str  # “步骤 N（`command`）”, resolved once the precheck list is final
    criterion: str
    scenario: str


@dataclass
class DocScenario:
    """One fault inside a document that covers several."""

    label: str  # A / B / C …
    name: str
    symptom: Node
    #: File name of this scenario under ``reference/``.  Like the skill's own
    #: name it is English and cannot be derived from a Chinese scenario name —
    #: the caller supplies it, and a placeholder is reported until they do.
    slug: str = ""
    #: Collection only this scenario needs — not part of the shared phase.
    collection: List[Precheck] = field(default_factory=list)
    steps: List[Step] = field(default_factory=list)
    root_causes: List[RootCause] = field(default_factory=list)
    routing: List[RoutingRow] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"场景{self.label}：{self.name}"


@dataclass
class SkillDoc:
    symptom: Node
    params: List[Param]
    prechecks: List[Precheck]
    scenarios: List[DocScenario] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    #: Causes left out of the steps, with the reason — reported, never silently dropped.
    omitted: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def multi(self) -> bool:
        return len(self.scenarios) > 1

    @property
    def steps(self) -> List[Step]:
        return [step for scenario in self.scenarios for step in scenario.steps]

    @property
    def root_causes(self) -> List[RootCause]:
        return [cause for scenario in self.scenarios for cause in scenario.root_causes]


SCENARIO_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def scenario_label(index: int) -> str:
    if index < len(SCENARIO_LABELS):
        return SCENARIO_LABELS[index]
    return f"{SCENARIO_LABELS[index // len(SCENARIO_LABELS) - 1]}{index % len(SCENARIO_LABELS) + 1}"
