"""Everything the renderers need, computed once."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from graph2skill.analyze import GraphIndex, GraphStats, Issue, ResolvedTree, compute_stats, resolve_trees, validate
from graph2skill.merge import MergeReport
from graph2skill.model import Graph
from graph2skill.ontology import ZH
from graph2skill.util import unique_slug


@dataclass
class SkillOptions:
    """Knobs for the conversion."""

    name: Optional[str] = None
    description: Optional[str] = None
    title: Optional[str] = None
    lang: str = ZH
    max_depth: int = 12
    include_inferred_entries: bool = True
    respect_declared_scope: bool = False
    diagrams: bool = True
    max_diagram_nodes: int = 40
    excerpt_limit: int = 220
    include_graph_asset: bool = True
    include_query_script: bool = True
    include_report: bool = True
    generator: str = "graph2skill"


@dataclass
class SkillBundle:
    """The analysed graph plus naming decisions, ready for rendering."""

    graph: Graph
    index: GraphIndex
    trees: List[ResolvedTree]
    stats: GraphStats
    issues: List[Issue]
    options: SkillOptions
    merge_report: Optional[MergeReport] = None
    tree_slugs: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        graph: Graph,
        options: Optional[SkillOptions] = None,
        merge_report: Optional[MergeReport] = None,
    ) -> "SkillBundle":
        options = options or SkillOptions()
        index = GraphIndex(graph)
        trees = resolve_trees(
            graph,
            index,
            include_inferred=options.include_inferred_entries,
            respect_declared_scope=options.respect_declared_scope,
            max_depth=options.max_depth,
        )
        issues = validate(graph, index)
        for tree in trees:
            issues.extend(tree.issues)
        stats = compute_stats(graph, index, trees)
        bundle = cls(
            graph=graph,
            index=index,
            trees=trees,
            stats=stats,
            issues=issues,
            options=options,
            merge_report=merge_report,
        )
        taken: set = set()
        for tree in trees:
            bundle.tree_slugs[tree.tree_id] = unique_slug(
                tree.entry_node_id or tree.tree_id, taken, fallback="playbook"
            )
        return bundle

    # -- naming ---------------------------------------------------------------
    @property
    def domain(self) -> str:
        return self.graph.domain or self.graph.graph_id or "domain"

    def playbook_path(self, tree: ResolvedTree) -> str:
        return f"references/playbooks/{self.tree_slugs[tree.tree_id]}.md"

    def tree_for_node(self, node_id: str) -> Optional[ResolvedTree]:
        for tree in self.trees:
            if node_id in tree.node_ids:
                return tree
        return None

    def errors(self) -> List[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]

    def warnings(self) -> List[Issue]:
        return [issue for issue in self.issues if issue.severity == "warning"]

    def uncovered_node_ids(self) -> List[str]:
        covered = set()
        for tree in self.trees:
            covered.update(tree.node_ids)
        return sorted(node_id for node_id in self.graph.nodes if node_id not in covered)
