"""graph2skill - turn domain decision subgraphs into a Claude Skill package."""

from graph2skill.bundle import SkillBundle, SkillOptions
from graph2skill.loader import GraphLoadError, load_graph, load_graphs
from graph2skill.merge import merge_graphs
from graph2skill.model import Graph, Node, Relation
from graph2skill.skill import BuildResult, build_bundle, build_skill, render_files, write_skill

__version__ = "0.1.0"

__all__ = [
    "BuildResult",
    "Graph",
    "GraphLoadError",
    "Node",
    "Relation",
    "SkillBundle",
    "SkillOptions",
    "build_bundle",
    "build_skill",
    "load_graph",
    "load_graphs",
    "merge_graphs",
    "render_files",
    "write_skill",
    "__version__",
]
