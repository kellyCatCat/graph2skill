"""subkg2skill — compile a fault-diagnosis knowledge subgraph into an agent skill.

Input: ``node.json`` / ``edge.json`` exports of an IP-RAN fault-diagnosis
knowledge graph.  Output: a directory holding a ``SKILL.md`` plus reference
documents, the subgraph itself and a query script — usable as-is by Claude Code,
opencode or any agent that reads skill directories.
"""

from subkg2skill.graph import Edge, Graph, Node, ValidationReport
from subkg2skill.loader import RawBundle, SubgraphLoadError, load
from subkg2skill.playbook import Playbook, build_playbook, build_playbooks
from subkg2skill.render import BuildOptions, RenderError, SkillPackage, build_package

__version__ = "0.1.0"

__all__ = [
    "BuildOptions",
    "Edge",
    "Graph",
    "Node",
    "Playbook",
    "RawBundle",
    "RenderError",
    "SkillPackage",
    "SubgraphLoadError",
    "ValidationReport",
    "build_package",
    "build_playbook",
    "build_playbooks",
    "load",
    "__version__",
]
