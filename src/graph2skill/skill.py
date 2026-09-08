"""Assemble the on-disk skill package."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from graph2skill.bundle import SkillBundle, SkillOptions
from graph2skill.loader import load_graphs
from graph2skill.merge import merge_graphs
from graph2skill.render import (
    default_description,
    default_skill_name,
    render_catalog,
    render_commands,
    render_overview,
    render_playbook,
    render_report,
    render_skill_md,
    render_sources,
)

RESOURCE_DIR = Path(__file__).resolve().parent / "resources"


@dataclass
class BuildResult:
    """What was written where."""

    output_dir: Path
    skill_name: str
    description: str
    files: List[Path] = field(default_factory=list)
    bundle: Optional[SkillBundle] = None

    @property
    def relative_files(self) -> List[str]:
        return [str(path.relative_to(self.output_dir)) for path in self.files]


def render_files(bundle: SkillBundle) -> Dict[str, str]:
    """Map of relative path -> file content for the whole package."""
    files: Dict[str, str] = {
        "SKILL.md": render_skill_md(bundle),
        "references/overview.md": render_overview(bundle),
        "references/node-catalog.md": render_catalog(bundle),
        "references/commands.md": render_commands(bundle),
        "references/sources.md": render_sources(bundle),
    }
    for tree in bundle.trees:
        files[bundle.playbook_path(tree)] = render_playbook(bundle, tree)
    if bundle.options.include_report:
        files["references/conversion-report.md"] = render_report(bundle)
    if bundle.options.include_graph_asset:
        files["assets/graph.json"] = json.dumps(bundle.graph.to_dict(), ensure_ascii=False, indent=2, sort_keys=False) + "\n"
    return files


def write_skill(bundle: SkillBundle, output_dir: Path, overwrite: bool = False) -> BuildResult:
    """Write the rendered package to *output_dir*."""
    output_dir = Path(output_dir)
    if output_dir.exists() and not output_dir.is_dir():
        raise FileExistsError(f"{output_dir} exists and is not a directory")
    if output_dir.is_dir() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(
            f"{output_dir} is not empty; pass --force to overwrite the generated files"
        )
    files = render_files(bundle)
    written: List[Path] = []
    for relative, content in files.items():
        target = output_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(target)
    if bundle.options.include_query_script:
        target = output_dir / "scripts" / "graph_query.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(RESOURCE_DIR / "graph_query.py", target)
        target.chmod(0o755)
        written.append(target)
    return BuildResult(
        output_dir=output_dir,
        skill_name=default_skill_name(bundle),
        description=default_description(bundle),
        files=sorted(written),
        bundle=bundle,
    )


def build_bundle(
    inputs: Sequence[str],
    options: Optional[SkillOptions] = None,
    graph_id: Optional[str] = None,
    domain: Optional[str] = None,
    recursive: bool = True,
) -> SkillBundle:
    """Load, merge and analyse *inputs* into a renderable bundle."""
    graphs = load_graphs(inputs, recursive=recursive)
    merged, report = merge_graphs(graphs, graph_id=graph_id, domain=domain)
    return SkillBundle.build(merged, options=options, merge_report=report)


def build_skill(
    inputs: Sequence[str],
    output_dir: Path,
    options: Optional[SkillOptions] = None,
    graph_id: Optional[str] = None,
    domain: Optional[str] = None,
    overwrite: bool = False,
    recursive: bool = True,
) -> BuildResult:
    """End-to-end conversion: graph documents in, skill package out."""
    bundle = build_bundle(inputs, options=options, graph_id=graph_id, domain=domain, recursive=recursive)
    return write_skill(bundle, output_dir, overwrite=overwrite)
