"""subkg-to-skill — compile a fault-diagnosis knowledge subgraph into an agent skill.

Input: ``node.json`` / ``edge.json`` exports of a fault-diagnosis knowledge
graph.  Output: one skill per fault entry — a template-conformant ``SKILL.md``
(入参列表 / 前置检查 / 排查步骤 / 根因对照表) plus ``reference/`` evidence and a
``scripts/`` query tool.
"""

from subkg2skill.graph import Edge, Graph, Node, ValidationReport
from subkg2skill.lint import LintResult, lint_path, lint_text
from subkg2skill.loader import RawBundle, SubgraphLoadError, load
from subkg2skill.playbook import Playbook, build_playbook, build_playbooks
from subkg2skill.render import BuildOptions, RenderError, SkillPackage, build_package
from subkg2skill.template import SkillDoc, build_doc, render_doc

__version__ = "0.2.0"

__all__ = [
    "BuildOptions",
    "Edge",
    "Graph",
    "LintResult",
    "Node",
    "Playbook",
    "RawBundle",
    "RenderError",
    "SkillDoc",
    "SkillPackage",
    "SubgraphLoadError",
    "ValidationReport",
    "build_doc",
    "build_package",
    "build_playbook",
    "build_playbooks",
    "lint_path",
    "lint_text",
    "load",
    "render_doc",
    "__version__",
]
