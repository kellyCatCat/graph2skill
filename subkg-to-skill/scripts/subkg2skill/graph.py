"""In-memory subgraph: typed accessors, validation and selection.

The raw records keep every field the export carried — traceability matters more
than tidiness here — so :class:`Node` and :class:`Edge` are thin views over the
original dict rather than a re-modelled copy.
"""

from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from subkg2skill import schema

_MISSING = object()


def _text(value: Any) -> str:
    """Collapse whatever the export stored into a single trimmed line."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "；".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, dict):
        return "；".join(f"{k}={_text(v)}" for k, v in value.items() if _text(v))
    return str(value)


def _string_list(value: Any) -> List[str]:
    """Normalise the ``array | string | null`` fields into a list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    out.append(item.strip())
            elif item is not None:
                out.append(_text(item))
        return out
    return [_text(value)]


def _dict(value: Any) -> Dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value
    if value in (None, "", {}):
        return []
    return [value]


@dataclass(frozen=True)
class Node:
    """One knowledge node (symptom / cause / check / observation / repair / escalation)."""

    raw: Dict[str, Any]

    @property
    def node_id(self) -> str:
        return _text(self.raw.get("node_id"))

    @property
    def node_type(self) -> str:
        return _text(self.raw.get("node_type"))

    @property
    def name(self) -> str:
        return _text(self.raw.get("name")) or self.node_id

    @property
    def description(self) -> str:
        return _text(self.raw.get("description"))

    @property
    def aliases(self) -> List[str]:
        return _string_list(self.raw.get("aliases"))

    @property
    def attrs(self) -> Dict[str, Any]:
        return _dict(self.raw.get("attrs"))

    @property
    def scope(self) -> Dict[str, Any]:
        return _dict(self.raw.get("scope"))

    @property
    def status(self) -> str:
        return _text(self.raw.get("status"))

    @property
    def review_status(self) -> str:
        return _text(self.raw.get("review_status"))

    @property
    def quality_flags(self) -> List[str]:
        return _string_list(self.raw.get("quality_flags"))

    @property
    def provenance(self) -> List[Dict[str, Any]]:
        return [item for item in _list(self.raw.get("provenance")) if isinstance(item, dict)]

    @property
    def diagnostic_contexts(self) -> List[Dict[str, Any]]:
        return [
            item for item in _list(self.raw.get("diagnostic_contexts")) if isinstance(item, dict)
        ]

    @property
    def sections(self) -> List[str]:
        seen: List[str] = []
        for ctx in self.diagnostic_contexts:
            section = _text(ctx.get("section"))
            if section and section not in seen:
                seen.append(section)
        return seen

    @property
    def example_specific(self) -> bool:
        return bool(self.attrs.get("example_specific"))

    @property
    def match_phrases(self) -> List[str]:
        return _string_list(self.attrs.get("match_phrases"))

    @property
    def human_reviewed(self) -> bool:
        return bool(_dict(self.raw.get("semantic_review")).get("human_reviewed"))

    def attr(self, key: str, default: Any = None) -> Any:
        return self.attrs.get(key, default)

    def search_text(self) -> str:
        parts = [self.name, self.description, self.node_id]
        parts += self.aliases + self.match_phrases
        parts += [_text(self.attrs.get(key)) for key in ("intent", "fault_mechanism", "field")]
        parts += [_text(ctx.get("title")) for ctx in self.diagnostic_contexts]
        return "\n".join(part for part in parts if part)


@dataclass(frozen=True)
class Edge:
    """One directed relation between two nodes."""

    raw: Dict[str, Any]

    @property
    def edge_id(self) -> str:
        return _text(self.raw.get("edge_id"))

    @property
    def edge_type(self) -> str:
        return _text(self.raw.get("edge_type"))

    @property
    def source(self) -> str:
        return _text(self.raw.get("source"))

    @property
    def target(self) -> str:
        return _text(self.raw.get("target"))

    @property
    def condition(self) -> Any:
        return self.raw.get("condition")

    @property
    def original_condition(self) -> Any:
        return self.raw.get("original_condition")

    @property
    def condition_status(self) -> str:
        return _text(self.raw.get("condition_status"))

    @property
    def rank(self) -> Optional[int]:
        value = self.raw.get("rank")
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @property
    def scope(self) -> Dict[str, Any]:
        return _dict(self.raw.get("scope"))

    @property
    def diagnostic_context(self) -> Dict[str, Any]:
        return _dict(self.raw.get("diagnostic_context"))

    @property
    def section(self) -> str:
        return _text(self.diagnostic_context.get("section"))

    @property
    def evidence(self) -> List[Dict[str, Any]]:
        return [item for item in _list(self.raw.get("evidence")) if isinstance(item, dict)]

    @property
    def quality_flags(self) -> List[str]:
        return _string_list(self.raw.get("quality_flags"))

    @property
    def review_status(self) -> str:
        return _text(self.raw.get("review_status"))

    @property
    def sort_key(self) -> Tuple[int, int, str]:
        rank = self.rank
        return (0, rank, self.edge_id) if rank is not None else (1, 0, self.edge_id)


@dataclass
class Issue:
    """A validation finding; ``level`` is ``error`` or ``warning``."""

    level: str
    kind: str
    message: str
    ref: str = ""

    def render(self) -> str:
        where = f" [{self.ref}]" if self.ref else ""
        return f"{self.level.upper()}: {self.message}{where}"


@dataclass
class ValidationReport:
    issues: List[Issue] = field(default_factory=list)
    dropped_edges: int = 0
    dropped_nodes: int = 0
    duplicate_nodes: int = 0
    duplicate_edges: int = 0

    def add(self, level: str, kind: str, message: str, ref: str = "") -> None:
        self.issues.append(Issue(level, kind, message, ref))

    @property
    def errors(self) -> List[Issue]:
        return [issue for issue in self.issues if issue.level == "error"]

    @property
    def warnings(self) -> List[Issue]:
        return [issue for issue in self.issues if issue.level == "warning"]

    def counts(self) -> Dict[str, int]:
        return dict(Counter(issue.kind for issue in self.issues))


class Graph:
    """Indexed nodes and edges with the adjacency helpers the renderer needs."""

    def __init__(self, nodes: Iterable[Node], edges: Iterable[Edge]) -> None:
        self.nodes: Dict[str, Node] = {node.node_id: node for node in nodes}
        self.edges: List[Edge] = list(edges)
        self._out: Dict[str, List[Edge]] = defaultdict(list)
        self._in: Dict[str, List[Edge]] = defaultdict(list)
        for edge in self.edges:
            self._out[edge.source].append(edge)
            self._in[edge.target].append(edge)
        for bucket in (self._out, self._in):
            for key in bucket:
                bucket[key].sort(key=lambda e: e.sort_key)

    # -- construction ----------------------------------------------------
    @classmethod
    def from_bundle(cls, bundle, *, strict: bool = False) -> Tuple["Graph", ValidationReport]:
        """Build a graph from a :class:`~subkg2skill.loader.RawBundle`.

        Records that cannot be placed (missing id, unknown type, dangling
        endpoint, endpoint types the schema forbids) are dropped with a
        recorded issue; ``strict`` turns every drop into an error the caller
        is expected to surface.
        """
        report = ValidationReport()
        nodes: Dict[str, Node] = {}
        for index, raw in enumerate(bundle.nodes):
            node = Node(raw)
            if not node.node_id:
                report.add("error", "node_missing_id", f"第 {index} 个节点缺少 node_id")
                report.dropped_nodes += 1
                continue
            if node.node_type not in schema.NODE_TYPES:
                report.add(
                    "error",
                    "node_unknown_type",
                    f"未知 node_type={node.node_type or '(空)'}",
                    node.node_id,
                )
                report.dropped_nodes += 1
                continue
            if node.node_id in nodes:
                report.duplicate_nodes += 1
                report.add("warning", "node_duplicate", "重复 node_id，保留首次出现", node.node_id)
                continue
            nodes[node.node_id] = node

        edges: List[Edge] = []
        seen_edges: Set[str] = set()
        seen_triples: Set[Tuple[str, str, str]] = set()
        for index, raw in enumerate(bundle.edges):
            edge = Edge(raw)
            if edge.edge_type not in schema.EDGE_RULES:
                report.add(
                    "error",
                    "edge_unknown_type",
                    f"未知 edge_type={edge.edge_type or '(空)'}",
                    edge.edge_id or f"#{index}",
                )
                report.dropped_edges += 1
                continue
            if not edge.source or not edge.target:
                report.add("error", "edge_missing_endpoint", "边缺少 source 或 target", edge.edge_id)
                report.dropped_edges += 1
                continue
            missing = [end for end in (edge.source, edge.target) if end not in nodes]
            if missing:
                report.add(
                    "error",
                    "edge_dangling",
                    "边的端点不在节点集合中：" + "、".join(missing),
                    edge.edge_id or f"#{index}",
                )
                report.dropped_edges += 1
                continue
            source_type = nodes[edge.source].node_type
            target_type = nodes[edge.target].node_type
            if not schema.endpoints_allowed(edge.edge_type, source_type, target_type):
                report.add(
                    "error",
                    "edge_bad_endpoints",
                    f"{edge.edge_type} 不允许 {source_type} → {target_type}",
                    edge.edge_id or f"#{index}",
                )
                report.dropped_edges += 1
                continue
            if edge.edge_id and edge.edge_id in seen_edges:
                report.duplicate_edges += 1
                report.add("warning", "edge_duplicate", "重复 edge_id，保留首次出现", edge.edge_id)
                continue
            triple = (edge.edge_type, edge.source, edge.target)
            if triple in seen_triples:
                report.duplicate_edges += 1
                report.add(
                    "warning",
                    "edge_duplicate_relation",
                    f"重复关系 {edge.edge_type}: {edge.source} → {edge.target}",
                    edge.edge_id,
                )
                continue
            if edge.source == edge.target:
                report.add("warning", "edge_self_loop", "自环边", edge.edge_id)
            seen_edges.add(edge.edge_id)
            seen_triples.add(triple)
            edges.append(edge)

        if strict:
            for issue in report.issues:
                if issue.level == "warning":
                    issue.level = "error"
        return cls(nodes.values(), edges), report

    # -- lookups ---------------------------------------------------------
    def __contains__(self, node_id: object) -> bool:
        return node_id in self.nodes

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, node_id: str) -> Optional[Node]:
        return self.nodes.get(node_id)

    def of_type(self, *node_types: str) -> List[Node]:
        wanted = set(node_types)
        return [node for node in self.nodes.values() if node.node_type in wanted]

    def out_edges(self, node_id: str, *edge_types: str) -> List[Edge]:
        edges = self._out.get(node_id, [])
        if not edge_types:
            return list(edges)
        wanted = set(edge_types)
        return [edge for edge in edges if edge.edge_type in wanted]

    def in_edges(self, node_id: str, *edge_types: str) -> List[Edge]:
        edges = self._in.get(node_id, [])
        if not edge_types:
            return list(edges)
        wanted = set(edge_types)
        return [edge for edge in edges if edge.edge_type in wanted]

    def targets(self, node_id: str, *edge_types: str) -> List[Tuple[Node, Edge]]:
        pairs = []
        for edge in self.out_edges(node_id, *edge_types):
            node = self.nodes.get(edge.target)
            if node is not None:
                pairs.append((node, edge))
        return pairs

    def sources(self, node_id: str, *edge_types: str) -> List[Tuple[Node, Edge]]:
        pairs = []
        for edge in self.in_edges(node_id, *edge_types):
            node = self.nodes.get(edge.source)
            if node is not None:
                pairs.append((node, edge))
        return pairs

    def search(self, term: str, *, node_types: Sequence[str] = (), limit: int = 0) -> List[Node]:
        """Case-insensitive substring search over names, aliases and context."""
        needle = term.strip().lower()
        if not needle:
            return []
        wanted = set(node_types)
        hits: List[Tuple[int, Node]] = []
        for node in self.nodes.values():
            if wanted and node.node_type not in wanted:
                continue
            haystack = node.search_text().lower()
            if needle not in haystack:
                continue
            score = 0 if needle in node.name.lower() else 1
            hits.append((score, node))
        hits.sort(key=lambda pair: (pair[0], pair[1].name, pair[1].node_id))
        result = [node for _, node in hits]
        return result[:limit] if limit else result

    # -- selection -------------------------------------------------------
    def reachable(
        self,
        roots: Iterable[str],
        *,
        depth: Optional[int] = None,
        edge_types: Sequence[str] = schema.FORWARD_EDGES,
        pull_evidence: bool = True,
    ) -> Set[str]:
        """Forward closure from *roots*, optionally pulling verdict evidence back in.

        Diagnosis reads forward (symptom → cause → check → observation), but the
        observation that confirms a cause points *backwards* into it, so those
        in-edges are pulled in as well unless ``pull_evidence`` is off.
        """
        wanted = set(edge_types)
        seen: Set[str] = set()
        queue: deque = deque()
        for root in roots:
            if root in self.nodes and root not in seen:
                seen.add(root)
                queue.append((root, 0))
        while queue:
            node_id, level = queue.popleft()
            if depth is not None and level >= depth:
                continue
            neighbours = [e.target for e in self.out_edges(node_id) if e.edge_type in wanted]
            if pull_evidence:
                neighbours += [e.source for e in self.in_edges(node_id, *schema.VERDICT_EDGES)]
                neighbours += [e.source for e in self.in_edges(node_id, "observes")]
            for neighbour in neighbours:
                if neighbour not in seen:
                    seen.add(neighbour)
                    queue.append((neighbour, level + 1))
        return seen

    # -- diagnostic units ------------------------------------------------
    @staticmethod
    def unit_matches(section: str, unit: str) -> bool:
        """Section ids nest with ``.`` (28.21.3) or ``:`` (ipran_battle_tree:s0:r56)."""
        if not unit:
            return True
        if not section:
            return False
        return section == unit or section.startswith(unit + ".") or section.startswith(unit + ":")

    def edge_units(self, node_id: str = "", *edge_types: str) -> Dict[str, int]:
        """How many relations sit in each diagnostic unit (optionally from one node)."""
        edges = self.out_edges(node_id, *edge_types) if node_id else self.edges
        counter = Counter(edge.section or "(未标注)" for edge in edges)
        return dict(counter.most_common())

    def scope_to_units(self, units: Sequence[str], *, include_unscoped: bool = True) -> "Graph":
        """Keep relations belonging to any of *units* (empty = keep everything)."""
        wanted = [unit for unit in units if unit]
        if not wanted:
            return self
        kept = [
            edge
            for edge in self.edges
            if any(self.unit_matches(edge.section, unit) for unit in wanted)
            or (include_unscoped and not edge.section)
        ]
        return Graph(self.nodes.values(), kept)

    def scope_to_unit(self, unit: str, *, include_unscoped: bool = True) -> "Graph":
        """Keep only relations belonging to *unit*.

        A knowledge graph merges one symptom across many chapters and cases, so
        walking every edge out of it mixes unrelated scenarios into one
        document.  Scoping to a diagnostic unit is what keeps a generated skill
        about a single fault.  Relations with no unit of their own are kept by
        default — they are generic rather than foreign.
        """
        if not unit:
            return self
        kept = [
            edge
            for edge in self.edges
            if self.unit_matches(edge.section, unit) or (include_unscoped and not edge.section)
        ]
        return Graph(self.nodes.values(), kept)

    def subgraph(self, node_ids: Iterable[str]) -> "Graph":
        keep = {nid for nid in node_ids if nid in self.nodes}
        nodes = [self.nodes[nid] for nid in keep]
        edges = [e for e in self.edges if e.source in keep and e.target in keep]
        return Graph(nodes, edges)

    def filter_nodes(
        self,
        *,
        node_types: Sequence[str] = (),
        sections: Sequence[str] = (),
        vendors: Sequence[str] = (),
        query: str = "",
    ) -> Set[str]:
        """Node ids matching every filter given (empty filters match everything)."""
        wanted_types = set(node_types)
        wanted_sections = {s.strip() for s in sections if s.strip()}
        wanted_vendors = {v.strip().lower() for v in vendors if v.strip()}
        needle = query.strip().lower()
        picked: Set[str] = set()
        for node in self.nodes.values():
            if wanted_types and node.node_type not in wanted_types:
                continue
            if wanted_sections and not any(
                section == wanted or section.startswith(wanted + ".")
                for section in node.sections
                for wanted in wanted_sections
            ):
                continue
            if wanted_vendors and _text(node.scope.get("vendor")).lower() not in wanted_vendors:
                continue
            if needle and needle not in node.search_text().lower():
                continue
            picked.add(node.node_id)
        return picked

    # -- statistics ------------------------------------------------------
    def node_counts(self) -> Dict[str, int]:
        counter = Counter(node.node_type for node in self.nodes.values())
        return {t: counter.get(t, 0) for t in schema.NODE_TYPES if counter.get(t, 0)}

    def edge_counts(self) -> Dict[str, int]:
        counter = Counter(edge.edge_type for edge in self.edges)
        return {t: counter.get(t, 0) for t in schema.EDGE_TYPES if counter.get(t, 0)}

    def vendors(self) -> Dict[str, int]:
        counter = Counter(
            _text(node.scope.get("vendor")) or "(未标注)" for node in self.nodes.values()
        )
        return dict(counter.most_common())

    def sections(self) -> Dict[str, int]:
        counter: Counter = Counter()
        for node in self.nodes.values():
            for section in node.sections:
                counter[section] += 1
        return dict(counter.most_common())

    def quality_flag_counts(self) -> Dict[str, int]:
        counter: Counter = Counter()
        for node in self.nodes.values():
            counter.update(node.quality_flags)
        for edge in self.edges:
            counter.update(edge.quality_flags)
        return dict(counter.most_common())

    def orphan_nodes(self) -> List[Node]:
        connected = {e.source for e in self.edges} | {e.target for e in self.edges}
        return [node for node in self.nodes.values() if node.node_id not in connected]

    def iter_nodes(self) -> Iterator[Node]:
        return iter(self.nodes.values())
