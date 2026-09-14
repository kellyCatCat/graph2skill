"""subkg-to-skill — compile a fault-diagnosis knowledge subgraph into an agent skill.

Input: ``node.json`` / ``edge.json`` exports of a fault-diagnosis knowledge
graph.  Output: one skill per fault entry — a template-conformant ``SKILL.md``
(入参列表 / 前置检查 / 排查步骤 / 根因对照表) and nothing else; the graph stays
behind as build-time material.
"""

from subkg2skill.graph import Edge, Graph, Node, ValidationReport
from subkg2skill.lint import LintResult, lint_path, lint_text
from subkg2skill.loader import RawBundle, SubgraphLoadError, load
from subkg2skill.playbook import Playbook, build_playbook, build_playbooks
from subkg2skill.render import BuildOptions, RenderError, SkillPackage, build_package
from subkg2skill.template import SkillDoc, build_doc, render_doc
from subkg2skill.verify import Finding, Ground, VerifyResult, verify_path, verify_text

__version__ = "0.2.0"

__all__ = [
    "BuildOptions",
    "Edge",
    "Finding",
    "Ground",
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
    "VerifyResult",
    "build_doc",
    "build_package",
    "build_playbook",
    "build_playbooks",
    "lint_path",
    "lint_text",
    "load",
    "render_doc",
    "verify_path",
    "verify_text",
    "__version__",
]
