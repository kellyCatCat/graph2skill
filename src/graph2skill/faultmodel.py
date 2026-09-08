"""Read a subgraph the way the IP-network house style thinks about it.

A scenario/fault node becomes a numbered 故障 with a cause table; causes that
point at nodes owned by ``common`` become 「根因迭代到底层」 references instead of
duplicated content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from graph2skill.analyze import GraphIndex
from graph2skill.model import Graph, Node, first_str
from graph2skill.util import one_line, slugify

FAULT_TYPES = ("FAULT", "SCENARIO", "SYMPTOM", "PHENOMENON")
CAUSE_TYPES = ("CAUSE", "ROOT_CAUSE")
FAULT_NO_RE = re.compile(r"故障序号\s*(\d+)")


@dataclass(frozen=True)
class CommonRef:
    """A node that lives in an included (common) skill."""

    node_id: str
    name: str
    section: str  # e.g. "3.6" ("" when it could not be located)
    doc: str  # e.g. "common.md"

    @property
    def section_label(self) -> str:
        return f"§{self.section}" if self.section else ""


class CommonIndex:
    """Which node ids belong to the included skills, and where they are documented."""

    def __init__(self) -> None:
        self._by_id: Dict[str, CommonRef] = {}
        self._by_name: Dict[str, CommonRef] = {}

    @classmethod
    def build(cls, graph: Optional[Graph], doc_name: str, doc_sections: Sequence = ()) -> "CommonIndex":
        index = cls()
        titles = {one_line(section.title): section.number for section in doc_sections if section.number}
        if graph is not None:
            for node in graph.nodes.values():
                section = first_str(node.data, ("section", "commonSection", "chapter"))
                section = section.lstrip("§") if section else titles.get(one_line(node.label), "")
                ref = CommonRef(node_id=node.id, name=one_line(node.label), section=section, doc=doc_name)
                index._by_id[node.id] = ref
                index._by_name.setdefault(one_line(node.label), ref)
        for title, number in titles.items():
            index._by_name.setdefault(title, CommonRef(node_id="", name=title, section=number, doc=doc_name))
        return index

    def merge(self, other: "CommonIndex") -> "CommonIndex":
        """Fold another index in; the first skill that claims a node wins."""
        for node_id, ref in other._by_id.items():
            self._by_id.setdefault(node_id, ref)
        for name, ref in other._by_name.items():
            self._by_name.setdefault(name, ref)
        return self

    def __contains__(self, node_id: str) -> bool:
        return node_id in self._by_id

    def by_id(self, node_id: str) -> Optional[CommonRef]:
        return self._by_id.get(node_id)

    def by_name(self, name: str) -> Optional[CommonRef]:
        return self._by_name.get(one_line(name))

    def resolve(self, node: Node) -> Optional[CommonRef]:
        return self.by_id(node.id) or self.by_name(node.label)

    @property
    def node_ids(self) -> Set[str]:
        return set(self._by_id)


@dataclass
class CauseView:
    """One row of a 故障 cause table."""

    node: Node
    ordinal: int = 0
    common_refs: List[CommonRef] = field(default_factory=list)
    checks: List[Node] = field(default_factory=list)
    actions: List[Node] = field(default_factory=list)

    @property
    def name(self) -> str:
        return one_line(self.node.label)

    @property
    def name_en(self) -> str:
        return first_str(self.node.data, ("nameEn", "englishName", "name_en", "englishIdentifier"))

    @property
    def trigger(self) -> str:
        return first_str(self.node.data, ("trigger", "triggerFeature", "condition")) or "见reference决策树"

    def row(self) -> List[str]:
        return [str(self.ordinal), self.name, self.name_en, self.trigger]


@dataclass
class FaultView:
    """A numbered 故障 as the house style presents it."""

    node: Node
    fault_id: str = ""
    identifier: str = ""
    category: str = ""
    causes: List[CauseView] = field(default_factory=list)
    checks: List[Node] = field(default_factory=list)
    reference_file: str = ""

    @property
    def name(self) -> str:
        return one_line(self.node.label)

    @property
    def title(self) -> str:
        return f"故障序号{self.fault_id}：{self.name}" if self.fault_id else self.name

    @property
    def meta_line(self) -> str:
        return f"标识: {self.identifier} | 分类: {self.category or '-'} | 根因: {len(self.causes)}类"

    def common_refs(self) -> List[CommonRef]:
        seen: Dict[str, CommonRef] = {}
        for cause in self.causes:
            for ref in cause.common_refs:
                seen.setdefault(ref.node_id or ref.name, ref)
        return list(seen.values())


def default_reference_file(fault: FaultView, reference_dir: str = "reference") -> str:
    slug = fault.identifier or slugify(fault.node.id, fallback="fault")
    prefix = f"fault-{fault.fault_id}-" if fault.fault_id else "fault-"
    return f"{reference_dir.rstrip('/')}/{prefix}{slug}.md"


def _collect_common_refs(
    index: GraphIndex, node: Node, common: CommonIndex, depth: int = 3
) -> List[CommonRef]:
    """Follow a cause downwards, collecting the common-owned nodes it reaches."""
    refs: Dict[str, CommonRef] = {}
    seen: Set[str] = set()

    def walk(node_id: str, level: int) -> None:
        if level > depth or node_id in seen:
            return
        seen.add(node_id)
        for relation in index.successors(node_id):
            target = index.node(relation.to_id)
            ref = common.by_id(relation.to_id) or (common.resolve(target) if target else None)
            if ref is not None:
                refs.setdefault(ref.node_id or ref.name, ref)
                continue
            walk(relation.to_id, level + 1)

    walk(node.id, 0)
    return list(refs.values())


def extract_faults(
    graph: Graph,
    index: Optional[GraphIndex] = None,
    common: Optional[CommonIndex] = None,
    reference_dir: str = "reference",
) -> List[FaultView]:
    """Turn fault/scenario entry nodes into :class:`FaultView` objects."""
    index = index or GraphIndex(graph)
    common = common or CommonIndex()
    faults: List[FaultView] = []
    for node in graph.nodes.values():
        if node.type not in FAULT_TYPES or node.id in common:
            continue
        if not any(index.node(rel.to_id) and index.node(rel.to_id).type in CAUSE_TYPES for rel in index.successors(node.id)):
            continue
        fault = FaultView(
            node=node,
            fault_id=_fault_id(node),
            identifier=first_str(node.data, ("identifier", "slug", "faultKey")) or slugify(node.id, fallback="fault"),
            category=first_str(node.data, ("category", "faultCategory", "classification")),
        )
        for relation in index.successors(node.id):
            target = index.node(relation.to_id)
            if target is None or target.id in common:
                continue
            if target.type in CAUSE_TYPES:
                cause = CauseView(node=target, common_refs=_collect_common_refs(index, target, common))
                for child_relation in index.successors(target.id):
                    child = index.node(child_relation.to_id)
                    if child is None or child.id in common:
                        continue
                    if child.type in ("CHECK", "DIAGNOSIS", "COMMAND", "STEP"):
                        cause.checks.append(child)
                    elif child.type in ("ACTION", "SOLUTION", "FIX"):
                        cause.actions.append(child)
                fault.causes.append(cause)
            elif target.type in ("CHECK", "DIAGNOSIS", "COMMAND", "STEP"):
                fault.checks.append(target)
        for ordinal, cause in enumerate(fault.causes, start=1):
            cause.ordinal = ordinal
        fault.reference_file = first_str(node.data, ("referenceFile", "reference")) or default_reference_file(
            fault, reference_dir
        )
        faults.append(fault)
    faults.sort(key=lambda item: (_sort_key(item.fault_id), item.node.id))
    return faults


def _fault_id(node: Node) -> str:
    value = first_str(node.data, ("faultId", "faultNo", "faultNumber", "序号"))
    if value:
        return value.strip()
    for raw in (node.data.get("faultId"), node.data.get("faultNo")):
        if isinstance(raw, int):
            return str(raw)
    match = FAULT_NO_RE.search(node.label)
    if match:
        return match.group(1)
    match = re.search(r"(\d+)$", node.id)
    return match.group(1) if match else ""


def _sort_key(fault_id: str):
    return (0, int(fault_id)) if fault_id.isdigit() else (1, 0)
