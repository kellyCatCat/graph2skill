"""Tolerant data model for ``cograg.domain-decision-subgraph.v1`` graphs.

The upstream graphs are produced by several extraction pipelines, so every
field is treated as optional and every list-ish field accepts either a scalar,
a list, or a list of dicts.  Unknown keys are never dropped: they are kept in
``Node.data`` / ``Relation.data`` so that renderers can still surface them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

SCHEMA_VERSION = "cograg.domain-decision-subgraph.v1"

# Keys that are consumed by typed attributes and therefore not repeated in the
# "extra fields" section of a rendered node.
_KNOWN_NODE_DATA_KEYS = frozenset(
    {
        "description",
        "executionClass",
        "nodeType",
        "observations",
        "parameterExamples",
        "provenance",
        "scenarioId",
        "sourceVersion",
    }
)


def as_list(value: Any) -> List[Any]:
    """Return *value* as a list, treating ``None`` as empty and scalars as one item."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    if isinstance(value, set):
        return sorted(value, key=str)
    return [value]


def dedupe(items: Iterable[Any]) -> List[Any]:
    """Stable de-duplication for hashable-or-not items."""
    seen: List[str] = []
    out: List[Any] = []
    for item in items:
        key = item if isinstance(item, str) else json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.append(key)
        out.append(item)
    return out


def first_str(mapping: Dict[str, Any], keys: Iterable[str]) -> str:
    """First non-empty string value among *keys* of *mapping*."""
    if not isinstance(mapping, dict):
        return ""
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def as_text_list(value: Any) -> List[str]:
    """Coerce a scalar / list / list-of-dicts field into a list of strings."""
    out: List[str] = []
    for item in as_list(value):
        if item is None:
            continue
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = first_str(item, ("command", "text", "value", "content", "name", "description"))
            if not text:
                text = json.dumps(item, sort_keys=True, ensure_ascii=False)
        else:
            text = str(item).strip()
        if text:
            out.append(text)
    return dedupe(out)


@dataclass(frozen=True)
class Provenance:
    """Where a piece of knowledge came from."""

    anchor: str = ""
    excerpt: str = ""
    locator: str = ""
    source_path: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> "Provenance":
        if isinstance(raw, str):
            return cls(source_path=raw.strip())
        if not isinstance(raw, dict):
            return cls()
        return cls(
            anchor=first_str(raw, ("anchor", "hash", "sha256")),
            excerpt=first_str(raw, ("excerpt", "snippet", "text")),
            locator=first_str(raw, ("locator", "section", "path")),
            source_path=first_str(raw, ("sourcePath", "source_path", "file", "document")),
        )

    def to_dict(self) -> Dict[str, str]:
        return {
            "anchor": self.anchor,
            "excerpt": self.excerpt,
            "locator": self.locator,
            "sourcePath": self.source_path,
        }

    @property
    def is_empty(self) -> bool:
        return not any((self.anchor, self.excerpt, self.locator, self.source_path))


def parse_provenance(value: Any) -> List[Provenance]:
    items = [Provenance.from_raw(raw) for raw in as_list(value)]
    return [item for item in items if not item.is_empty]


@dataclass
class Node:
    """A single graph node (scenario, cause, check, case, ...)."""

    id: str
    type: str = "UNKNOWN"
    name: str = ""
    source: str = ""
    sources: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    graph_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], graph_id: str = "") -> "Node":
        node_id = first_str(raw, ("id", "nodeId", "_id"))
        if not node_id:
            raise ValueError(f"node without id: {json.dumps(raw, ensure_ascii=False)[:160]}")
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        node_type = first_str(raw, ("type", "nodeType")) or first_str(data, ("nodeType",))
        sources = as_text_list(raw.get("sources"))
        source = first_str(raw, ("source",))
        if source and source not in sources:
            sources = [source] + sources
        return cls(
            id=node_id,
            type=(node_type or "UNKNOWN").strip().upper(),
            name=first_str(raw, ("name", "title", "label")) or first_str(data, ("description",)),
            source=source,
            sources=sources,
            data=dict(data),
            graph_ids=[graph_id] if graph_id else [],
        )

    # -- typed views over ``data`` -------------------------------------------------
    @property
    def description(self) -> str:
        return first_str(self.data, ("description", "summary", "detail"))

    @property
    def execution_class(self) -> str:
        return first_str(self.data, ("executionClass",))

    @property
    def scenario_id(self) -> str:
        return first_str(self.data, ("scenarioId", "scenario_id"))

    @property
    def source_version(self) -> str:
        return first_str(self.data, ("sourceVersion", "source_version"))

    @property
    def observations(self) -> List[str]:
        return as_text_list(self.data.get("observations"))

    @property
    def commands(self) -> List[str]:
        """Executable/diagnostic snippets attached to the node."""
        values = as_text_list(self.data.get("parameterExamples"))
        values += as_text_list(self.data.get("commands"))
        return dedupe(values)

    @property
    def provenance(self) -> List[Provenance]:
        return parse_provenance(self.data.get("provenance"))

    @property
    def label(self) -> str:
        """Human readable one-liner, always non-empty."""
        return self.name or self.description or self.id

    @property
    def extra_data(self) -> Dict[str, Any]:
        """Fields of ``data`` that no typed accessor already surfaces."""
        return {k: v for k, v in sorted(self.data.items()) if k not in _KNOWN_NODE_DATA_KEYS}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "source": self.source,
            "sources": list(self.sources),
            "data": self.data,
            "graphIds": list(self.graph_ids),
        }


@dataclass
class Relation:
    """A directed edge between two nodes."""

    id: str
    type: str = "RELATED_TO"
    from_id: str = ""
    to_id: str = ""
    source: str = ""
    sources: List[str] = field(default_factory=list)
    data: Dict[str, Any] = field(default_factory=dict)
    graph_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], graph_id: str = "", index: int = 0) -> "Relation":
        data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
        from_id = first_str(raw, ("from", "source_id", "fromId", "start"))
        to_id = first_str(raw, ("to", "target_id", "toId", "end"))
        rel_type = first_str(raw, ("type", "edgeType")) or first_str(data, ("edgeType",)) or "RELATED_TO"
        rel_id = first_str(raw, ("id", "relationId")) or f"{graph_id or 'rel'}:{rel_type}:{from_id}->{to_id}#{index}"
        sources = as_text_list(raw.get("sources"))
        source = first_str(raw, ("source",))
        if source and source not in sources:
            sources = [source] + sources
        return cls(
            id=rel_id,
            type=rel_type.strip().upper(),
            from_id=from_id,
            to_id=to_id,
            source=source,
            sources=sources,
            data=dict(data),
            graph_ids=[graph_id] if graph_id else [],
        )

    @property
    def provenance(self) -> List[Provenance]:
        return parse_provenance(self.data.get("provenance"))

    @property
    def condition(self) -> str:
        """Guard/label carried by the edge, if the pipeline emitted one."""
        return first_str(self.data, ("condition", "guard", "when", "label", "description"))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "from": self.from_id,
            "to": self.to_id,
            "source": self.source,
            "sources": list(self.sources),
            "data": self.data,
            "graphIds": list(self.graph_ids),
        }


@dataclass
class DecisionTree:
    """A declared sub-graph rooted at one entry node."""

    tree_id: str
    entry_node_id: str = ""
    node_ids: List[str] = field(default_factory=list)
    relation_ids: List[str] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)
    graph_ids: List[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], graph_id: str = "", index: int = 0) -> "DecisionTree":
        entry = first_str(raw, ("entryNodeId", "entry", "rootNodeId"))
        tree_id = first_str(raw, ("treeId", "id")) or (f"tree:{entry}" if entry else f"tree:{graph_id}:{index}")
        return cls(
            tree_id=tree_id,
            entry_node_id=entry,
            node_ids=as_text_list(raw.get("nodeIds")),
            relation_ids=as_text_list(raw.get("relationIds")),
            sources=as_text_list(raw.get("sources")),
            graph_ids=[graph_id] if graph_id else [],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "treeId": self.tree_id,
            "entryNodeId": self.entry_node_id,
            "nodeIds": list(self.node_ids),
            "relationIds": list(self.relation_ids),
            "sources": list(self.sources),
        }


@dataclass
class Graph:
    """One parsed graph document."""

    graph_id: str = ""
    domain: str = ""
    schema_version: str = SCHEMA_VERSION
    sources: List[str] = field(default_factory=list)
    entry_node_ids: List[str] = field(default_factory=list)
    nodes: Dict[str, Node] = field(default_factory=dict)
    relations: Dict[str, Relation] = field(default_factory=dict)
    decision_trees: Dict[str, DecisionTree] = field(default_factory=dict)
    origin: str = ""  # file path the graph was loaded from
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def from_raw(cls, raw: Dict[str, Any], origin: str = "") -> "Graph":
        if not isinstance(raw, dict):
            raise ValueError(f"{origin or 'graph'}: top level value must be an object")
        graph_id = first_str(raw, ("graphId", "id", "name")) or (origin or "graph")
        graph = cls(
            graph_id=graph_id,
            domain=first_str(raw, ("domain",)),
            schema_version=first_str(raw, ("schemaVersion", "schema_version")) or SCHEMA_VERSION,
            sources=as_text_list(raw.get("sources")),
            entry_node_ids=as_text_list(raw.get("entryNodeIds")),
            origin=origin,
        )
        for item in as_list(raw.get("nodes")):
            if not isinstance(item, dict):
                graph.warnings.append(f"skipped non-object node: {item!r}")
                continue
            try:
                node = Node.from_raw(item, graph_id)
            except ValueError as exc:
                graph.warnings.append(str(exc))
                continue
            if node.id in graph.nodes:
                graph.warnings.append(f"duplicate node id inside {graph_id}: {node.id}")
                graph.nodes[node.id] = merge_nodes(graph.nodes[node.id], node)
            else:
                graph.nodes[node.id] = node
        for index, item in enumerate(as_list(raw.get("relations"))):
            if not isinstance(item, dict):
                graph.warnings.append(f"skipped non-object relation: {item!r}")
                continue
            relation = Relation.from_raw(item, graph_id, index)
            if not relation.from_id or not relation.to_id:
                graph.warnings.append(f"relation {relation.id} misses an endpoint; skipped")
                continue
            graph.relations[relation.id] = relation
        for index, item in enumerate(as_list(raw.get("decisionTrees"))):
            if not isinstance(item, dict):
                continue
            tree = DecisionTree.from_raw(item, graph_id, index)
            graph.decision_trees[tree.tree_id] = tree
        return graph

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "graphId": self.graph_id,
            "domain": self.domain,
            "sources": list(self.sources),
            "entryNodeIds": list(self.entry_node_ids),
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "relations": [relation.to_dict() for relation in self.relations.values()],
            "decisionTrees": [tree.to_dict() for tree in self.decision_trees.values()],
        }


def merge_nodes(left: Node, right: Node) -> Node:
    """Merge two nodes that share an id (see :mod:`graph2skill.merge`)."""
    from graph2skill.merge import merge_node_pair  # local import to avoid a cycle

    merged, _ = merge_node_pair(left, right)
    return merged
