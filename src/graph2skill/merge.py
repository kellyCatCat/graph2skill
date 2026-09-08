"""Merge several graph documents into one consolidated graph.

Nodes are merged by ``id``; relations are merged by their semantic key
``(type, from, to)`` so that two pipelines emitting the same edge under
different ids do not produce a duplicated branch in the rendered skill.
Every non-trivial decision is recorded in a :class:`MergeReport` so the CLI
can show what happened instead of silently picking a winner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from graph2skill.model import DecisionTree, Graph, Node, Relation, dedupe


@dataclass
class Conflict:
    """Two documents disagreed about the same field."""

    kind: str  # "node" | "relation" | "graph"
    ref: str  # node / relation id, or the graph field name
    field: str
    kept: Any
    dropped: Any

    def describe(self) -> str:
        return f"{self.kind} {self.ref}: field '{self.field}' kept {self.kept!r}, ignored {self.dropped!r}"


@dataclass
class MergeReport:
    """What happened while merging."""

    graph_ids: List[str] = field(default_factory=list)
    origins: List[str] = field(default_factory=list)
    node_total_in: int = 0
    relation_total_in: int = 0
    merged_node_ids: List[str] = field(default_factory=list)
    merged_relation_keys: List[str] = field(default_factory=list)
    conflicts: List[Conflict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def has_findings(self) -> bool:
        return bool(self.merged_node_ids or self.merged_relation_keys or self.conflicts or self.warnings)


def _merge_value(
    left: Any, right: Any, ref: str, kind: str, path: str, conflicts: List[Conflict]
) -> Any:
    """Recursively merge two JSON-ish values, preferring *left* on conflict."""
    if left is None or left == "" or left == [] or left == {}:
        return right if right is not None else left
    if right is None or right == "" or right == [] or right == {}:
        return left
    if isinstance(left, dict) and isinstance(right, dict):
        merged = dict(left)
        for key, value in right.items():
            if key in merged:
                merged[key] = _merge_value(merged[key], value, ref, kind, f"{path}.{key}" if path else key, conflicts)
            else:
                merged[key] = value
        return merged
    if isinstance(left, list) and isinstance(right, list):
        return dedupe(list(left) + list(right))
    if isinstance(left, list) or isinstance(right, list):
        left_list = left if isinstance(left, list) else [left]
        right_list = right if isinstance(right, list) else [right]
        return dedupe(left_list + right_list)
    if left != right:
        conflicts.append(Conflict(kind=kind, ref=ref, field=path or "value", kept=left, dropped=right))
    return left


def merge_node_pair(left: Node, right: Node) -> Tuple[Node, List[Conflict]]:
    """Merge two nodes sharing an id."""
    conflicts: List[Conflict] = []
    node_type = left.type
    if left.type != right.type:
        if left.type in ("", "UNKNOWN"):
            node_type = right.type
        elif right.type not in ("", "UNKNOWN"):
            conflicts.append(Conflict("node", left.id, "type", left.type, right.type))
    name = left.name or right.name
    if left.name and right.name and left.name != right.name:
        conflicts.append(Conflict("node", left.id, "name", left.name, right.name))
    data = _merge_value(dict(left.data), dict(right.data), left.id, "node", "data", conflicts)
    if left.name and right.name and left.name != right.name:
        aliases = dedupe(list(data.get("nameAliases", [])) + [right.name])
        data["nameAliases"] = aliases
    return (
        Node(
            id=left.id,
            type=node_type,
            name=name,
            source=left.source or right.source,
            sources=dedupe(left.sources + right.sources),
            data=data if isinstance(data, dict) else dict(left.data),
            graph_ids=dedupe(left.graph_ids + right.graph_ids),
        ),
        conflicts,
    )


def merge_relation_pair(left: Relation, right: Relation) -> Tuple[Relation, List[Conflict]]:
    """Merge two relations sharing a semantic key."""
    conflicts: List[Conflict] = []
    data = _merge_value(dict(left.data), dict(right.data), left.id, "relation", "data", conflicts)
    return (
        Relation(
            id=left.id,
            type=left.type,
            from_id=left.from_id,
            to_id=left.to_id,
            source=left.source or right.source,
            sources=dedupe(left.sources + right.sources),
            data=data if isinstance(data, dict) else dict(left.data),
            graph_ids=dedupe(left.graph_ids + right.graph_ids),
        ),
        conflicts,
    )


def _merge_tree_pair(left: DecisionTree, right: DecisionTree) -> DecisionTree:
    return DecisionTree(
        tree_id=left.tree_id,
        entry_node_id=left.entry_node_id or right.entry_node_id,
        node_ids=dedupe(left.node_ids + right.node_ids),
        relation_ids=dedupe(left.relation_ids + right.relation_ids),
        sources=dedupe(left.sources + right.sources),
        graph_ids=dedupe(left.graph_ids + right.graph_ids),
    )


def relation_key(relation: Relation) -> str:
    return f"{relation.type}|{relation.from_id}|{relation.to_id}"


def merge_graphs(
    graphs: Sequence[Graph],
    graph_id: Optional[str] = None,
    domain: Optional[str] = None,
) -> Tuple[Graph, MergeReport]:
    """Merge *graphs* (in the given order) into a single :class:`Graph`."""
    if not graphs:
        raise ValueError("merge_graphs() needs at least one graph")

    report = MergeReport()
    merged = Graph(
        graph_id=graph_id or "+".join(dedupe([g.graph_id for g in graphs if g.graph_id])) or "merged-graph",
        domain=domain or "",
        schema_version=graphs[0].schema_version,
        origin=", ".join(g.origin for g in graphs if g.origin),
    )

    relation_index: Dict[str, str] = {}  # semantic key -> relation id kept
    for graph in graphs:
        report.graph_ids.append(graph.graph_id)
        if graph.origin:
            report.origins.append(graph.origin)
        report.node_total_in += len(graph.nodes)
        report.relation_total_in += len(graph.relations)
        report.warnings.extend(f"{graph.origin or graph.graph_id}: {w}" for w in graph.warnings)

        if graph.schema_version != merged.schema_version:
            report.conflicts.append(
                Conflict("graph", graph.origin or graph.graph_id, "schemaVersion", merged.schema_version, graph.schema_version)
            )
        if domain is None and graph.domain:
            if not merged.domain:
                merged.domain = graph.domain
            elif merged.domain != graph.domain:
                report.conflicts.append(
                    Conflict("graph", graph.origin or graph.graph_id, "domain", merged.domain, graph.domain)
                )
        merged.sources = dedupe(merged.sources + graph.sources)
        merged.entry_node_ids = dedupe(merged.entry_node_ids + graph.entry_node_ids)

        for node in graph.nodes.values():
            existing = merged.nodes.get(node.id)
            if existing is None:
                merged.nodes[node.id] = node
                continue
            combined, conflicts = merge_node_pair(existing, node)
            merged.nodes[node.id] = combined
            report.conflicts.extend(conflicts)
            report.merged_node_ids.append(node.id)

        for relation in graph.relations.values():
            key = relation_key(relation)
            kept_id = relation_index.get(key)
            if kept_id is None:
                if relation.id in merged.relations:
                    # Same id, different endpoints: keep both by re-keying.
                    relation.id = f"{relation.id}#{graph.graph_id}"
                merged.relations[relation.id] = relation
                relation_index[key] = relation.id
                continue
            combined, conflicts = merge_relation_pair(merged.relations[kept_id], relation)
            merged.relations[kept_id] = combined
            report.conflicts.extend(conflicts)
            report.merged_relation_keys.append(key)

        for tree in graph.decision_trees.values():
            existing_tree = merged.decision_trees.get(tree.tree_id)
            merged.decision_trees[tree.tree_id] = (
                tree if existing_tree is None else _merge_tree_pair(existing_tree, tree)
            )

    report.merged_node_ids = dedupe(report.merged_node_ids)
    report.merged_relation_keys = dedupe(report.merged_relation_keys)
    return merged, report
