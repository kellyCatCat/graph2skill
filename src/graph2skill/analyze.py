"""Index, validate and traverse a merged graph."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from graph2skill.model import Graph, Node, Relation, dedupe
from graph2skill.ontology import ENTRY_ROLE_TYPES, node_type_order

DEFAULT_MAX_DEPTH = 12

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True)
class Issue:
    severity: str  # error | warning | info
    code: str
    message: str
    ref: str = ""

    def format(self) -> str:
        suffix = f" [{self.ref}]" if self.ref else ""
        return f"{self.severity.upper():7} {self.code}: {self.message}{suffix}"


class GraphIndex:
    """Adjacency and lookup helpers over a :class:`~graph2skill.model.Graph`."""

    def __init__(self, graph: Graph):
        self.graph = graph
        self.out_edges: Dict[str, List[Relation]] = {}
        self.in_edges: Dict[str, List[Relation]] = {}
        self.by_type: Dict[str, List[str]] = {}
        self._relation_order: Dict[str, int] = {rid: pos for pos, rid in enumerate(graph.relations)}
        for relation in graph.relations.values():
            self.out_edges.setdefault(relation.from_id, []).append(relation)
            self.in_edges.setdefault(relation.to_id, []).append(relation)
        for node in graph.nodes.values():
            self.by_type.setdefault(node.type, []).append(node.id)
        for bucket in self.out_edges.values():
            bucket.sort(key=self._edge_sort_key)
        for bucket in self.in_edges.values():
            bucket.sort(key=self._edge_sort_key)
        for bucket in self.by_type.values():
            bucket.sort()

    def _edge_sort_key(self, relation: Relation) -> Tuple[int, int, str]:
        """Group by ontology rank, then keep the order the source graph used."""
        target = self.graph.nodes.get(relation.to_id)
        return (
            node_type_order(target.type) if target else 999,
            self._relation_order.get(relation.id, 10**6),
            relation.to_id,
        )

    def node(self, node_id: str) -> Optional[Node]:
        return self.graph.nodes.get(node_id)

    def successors(self, node_id: str) -> List[Relation]:
        return self.out_edges.get(node_id, [])

    def predecessors(self, node_id: str) -> List[Relation]:
        return self.in_edges.get(node_id, [])

    def roots(self) -> List[str]:
        """Nodes nothing points at, ordered by ontology rank then id."""
        roots = [node_id for node_id in self.graph.nodes if not self.in_edges.get(node_id)]
        return sorted(roots, key=lambda nid: (node_type_order(self.graph.nodes[nid].type), nid))

    def entry_candidates(self, include_inferred: bool = True) -> List[str]:
        """Declared entry nodes first, then declared tree roots, then inferred roots."""
        ordered: List[str] = [nid for nid in self.graph.entry_node_ids if nid in self.graph.nodes]
        ordered += [
            tree.entry_node_id
            for tree in self.graph.decision_trees.values()
            if tree.entry_node_id in self.graph.nodes
        ]
        if include_inferred:
            ordered += [
                node_id
                for node_id in self.roots()
                if self.graph.nodes[node_id].type in ENTRY_ROLE_TYPES or self.successors(node_id)
            ]
        return dedupe(ordered)


@dataclass
class TreeNodeView:
    """One node as it appears at a specific position of a decision tree."""

    node_id: str
    node: Optional[Node]
    depth: int
    via: Optional[Relation] = None
    children: List["TreeNodeView"] = field(default_factory=list)
    repeated: bool = False  # already expanded elsewhere; not expanded again
    truncated: bool = False  # hit the depth limit

    def iter_views(self) -> Iterable["TreeNodeView"]:
        yield self
        for child in self.children:
            yield from child.iter_views()


@dataclass
class ResolvedTree:
    """A decision tree ready to be rendered."""

    tree_id: str
    entry_node_id: str
    root: TreeNodeView
    declared: bool
    sources: List[str] = field(default_factory=list)
    node_ids: List[str] = field(default_factory=list)
    relation_ids: List[str] = field(default_factory=list)
    issues: List[Issue] = field(default_factory=list)

    @property
    def entry_node(self) -> Optional[Node]:
        return self.root.node

    @property
    def title(self) -> str:
        return self.root.node.label if self.root.node else self.entry_node_id

    @property
    def size(self) -> int:
        return len(self.node_ids)

    @property
    def max_depth(self) -> int:
        return max((view.depth for view in self.root.iter_views()), default=0)


def build_tree(
    index: GraphIndex,
    entry_node_id: str,
    allowed_nodes: Optional[Set[str]] = None,
    allowed_relations: Optional[Set[str]] = None,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> Tuple[TreeNodeView, List[str], List[str], List[Issue]]:
    """Depth-first expansion of the sub-graph rooted at *entry_node_id*."""
    issues: List[Issue] = []
    visited: Set[str] = set()
    node_ids: List[str] = []
    relation_ids: List[str] = []

    def expand(node_id: str, depth: int, via: Optional[Relation], path: Tuple[str, ...]) -> TreeNodeView:
        node = index.node(node_id)
        view = TreeNodeView(node_id=node_id, node=node, depth=depth, via=via)
        if node is None:
            issues.append(Issue("error", "missing-node", "relation points at an unknown node", node_id))
            return view
        node_ids.append(node_id)
        if node_id in path:
            view.repeated = True
            issues.append(Issue("warning", "cycle", "cycle detected; branch stopped here", node_id))
            return view
        if node_id in visited:
            view.repeated = True
            return view
        if depth >= max_depth:
            view.truncated = bool(index.successors(node_id))
            if view.truncated:
                issues.append(Issue("warning", "depth-limit", f"stopped at depth {max_depth}", node_id))
            return view
        visited.add(node_id)
        for relation in index.successors(node_id):
            if allowed_relations is not None and relation.id not in allowed_relations:
                continue
            if allowed_nodes is not None and relation.to_id not in allowed_nodes:
                continue
            relation_ids.append(relation.id)
            view.children.append(expand(relation.to_id, depth + 1, relation, path + (node_id,)))
        return view

    root = expand(entry_node_id, 0, None, ())
    return root, dedupe(node_ids), dedupe(relation_ids), issues


def resolve_trees(
    graph: Graph,
    index: Optional[GraphIndex] = None,
    include_inferred: bool = True,
    respect_declared_scope: bool = False,
    max_depth: int = DEFAULT_MAX_DEPTH,
) -> List[ResolvedTree]:
    """Turn declared ``decisionTrees`` + entry nodes into renderable trees.

    Declared trees always define the entry points.  Their ``nodeIds`` /
    ``relationIds`` are frequently partial, so by default the whole reachable
    sub-graph is walked; pass ``respect_declared_scope=True`` to stay inside the
    declared sets instead.
    """
    index = index or GraphIndex(graph)
    resolved: List[ResolvedTree] = []
    seen_entries: Set[str] = set()

    for tree in graph.decision_trees.values():
        entry = tree.entry_node_id
        if entry not in graph.nodes:
            continue
        allowed_nodes = None
        allowed_relations = None
        if respect_declared_scope:
            known_nodes = [nid for nid in tree.node_ids if nid in graph.nodes]
            known_relations = [rid for rid in tree.relation_ids if rid in graph.relations]
            if known_nodes:
                allowed_nodes = set(known_nodes) | {entry}
            if known_relations:
                allowed_relations = set(known_relations)
        root, node_ids, relation_ids, issues = build_tree(
            index, entry, allowed_nodes, allowed_relations, max_depth
        )
        resolved.append(
            ResolvedTree(
                tree_id=tree.tree_id,
                entry_node_id=entry,
                root=root,
                declared=True,
                sources=list(tree.sources),
                node_ids=node_ids,
                relation_ids=relation_ids,
                issues=issues,
            )
        )
        seen_entries.add(entry)

    for entry in index.entry_candidates(include_inferred=include_inferred):
        if entry in seen_entries:
            continue
        root, node_ids, relation_ids, issues = build_tree(index, entry, None, None, max_depth)
        if len(node_ids) <= 1 and not include_inferred:
            continue
        resolved.append(
            ResolvedTree(
                tree_id=f"tree:{entry}",
                entry_node_id=entry,
                root=root,
                declared=False,
                sources=list(graph.nodes[entry].sources),
                node_ids=node_ids,
                relation_ids=relation_ids,
                issues=issues,
            )
        )
        seen_entries.add(entry)

    resolved.sort(key=lambda tree: (not tree.declared, -tree.size, tree.entry_node_id))
    return resolved


def covered_node_ids(trees: Sequence[ResolvedTree]) -> Set[str]:
    covered: Set[str] = set()
    for tree in trees:
        covered.update(tree.node_ids)
    return covered


def validate(graph: Graph, index: Optional[GraphIndex] = None) -> List[Issue]:
    """Structural checks over the merged graph."""
    index = index or GraphIndex(graph)
    issues: List[Issue] = []

    if not graph.nodes:
        issues.append(Issue("error", "empty-graph", "graph has no nodes"))
    for relation in graph.relations.values():
        if relation.from_id not in graph.nodes:
            issues.append(
                Issue("error", "dangling-relation", f"relation source '{relation.from_id}' is not a node", relation.id)
            )
        if relation.to_id not in graph.nodes:
            issues.append(
                Issue("error", "dangling-relation", f"relation target '{relation.to_id}' is not a node", relation.id)
            )
    for entry in graph.entry_node_ids:
        if entry not in graph.nodes:
            issues.append(Issue("error", "missing-entry", "entryNodeIds references an unknown node", entry))
    for tree in graph.decision_trees.values():
        if tree.entry_node_id and tree.entry_node_id not in graph.nodes:
            issues.append(
                Issue("error", "missing-tree-entry", f"decision tree entry '{tree.entry_node_id}' is not a node", tree.tree_id)
            )
        for node_id in tree.node_ids:
            if node_id not in graph.nodes:
                issues.append(Issue("warning", "tree-unknown-node", f"decision tree lists unknown node '{node_id}'", tree.tree_id))
        for relation_id in tree.relation_ids:
            if relation_id not in graph.relations:
                issues.append(
                    Issue("warning", "tree-unknown-relation", f"decision tree lists unknown relation '{relation_id}'", tree.tree_id)
                )
    if not graph.entry_node_ids and not graph.decision_trees:
        issues.append(Issue("warning", "no-entry-points", "graph declares neither entryNodeIds nor decisionTrees; roots will be inferred"))
    for node_id, node in graph.nodes.items():
        if not index.successors(node_id) and not index.predecessors(node_id):
            issues.append(Issue("warning", "isolated-node", f"node '{node.label}' has no relations", node_id))
    unknown_types = sorted({node.type for node in graph.nodes.values() if node.type == "UNKNOWN"})
    for node_type in unknown_types:
        issues.append(Issue("info", "unknown-type", f"{len(index.by_type.get(node_type, []))} node(s) have no usable type", node_type))
    issues.sort(key=lambda issue: (SEVERITY_ORDER.get(issue.severity, 9), issue.code, issue.ref))
    return issues


@dataclass
class GraphStats:
    node_count: int
    relation_count: int
    tree_count: int
    node_types: Dict[str, int]
    relation_types: Dict[str, int]
    sources: Dict[str, int]
    entry_count: int
    orphan_count: int
    command_count: int
    provenance_count: int


def compute_stats(graph: Graph, index: Optional[GraphIndex] = None, trees: Optional[Sequence[ResolvedTree]] = None) -> GraphStats:
    index = index or GraphIndex(graph)
    trees = trees if trees is not None else resolve_trees(graph, index)
    covered = covered_node_ids(trees)
    node_types: Dict[str, int] = {}
    sources: Dict[str, int] = {}
    commands = 0
    provenance = 0
    for node in graph.nodes.values():
        node_types[node.type] = node_types.get(node.type, 0) + 1
        for source in node.sources or ([node.source] if node.source else []):
            sources[source] = sources.get(source, 0) + 1
        commands += len(node.commands)
        provenance += len(node.provenance)
    relation_types: Dict[str, int] = {}
    for relation in graph.relations.values():
        relation_types[relation.type] = relation_types.get(relation.type, 0) + 1
    return GraphStats(
        node_count=len(graph.nodes),
        relation_count=len(graph.relations),
        tree_count=len(trees),
        node_types=dict(sorted(node_types.items(), key=lambda kv: (-kv[1], kv[0]))),
        relation_types=dict(sorted(relation_types.items(), key=lambda kv: (-kv[1], kv[0]))),
        sources=dict(sorted(sources.items(), key=lambda kv: (-kv[1], kv[0]))),
        entry_count=len(trees),
        orphan_count=len([nid for nid in graph.nodes if nid not in covered]),
        command_count=commands,
        provenance_count=provenance,
    )
