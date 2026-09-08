import json

import pytest

from graph2skill.bundle import SkillOptions
from graph2skill.skill import build_skill, render_files


def test_build_writes_the_expected_layout(tmp_path, examples_dir):
    result = build_skill([examples_dir / "isis"], tmp_path / "skill", options=SkillOptions())
    files = set(result.relative_files)
    assert "SKILL.md" in files
    assert "references/overview.md" in files
    assert "references/node-catalog.md" in files
    assert "references/conversion-report.md" in files
    assert "assets/graph.json" in files
    assert "scripts/graph_query.py" in files
    assert any(name.startswith("references/playbooks/") for name in files)
    assert result.skill_name == "isis-playbook"


def test_graph_asset_is_valid_json_and_round_trips(tmp_path, examples_dir):
    result = build_skill([examples_dir / "isis"], tmp_path / "skill")
    payload = json.loads((tmp_path / "skill" / "assets" / "graph.json").read_text(encoding="utf-8"))
    assert payload["schemaVersion"] == "cograg.domain-decision-subgraph.v1"
    assert len(payload["nodes"]) == result.bundle.stats.node_count
    assert {relation["id"] for relation in payload["relations"]} == set(result.bundle.graph.relations)


def test_query_script_runs_against_the_generated_asset(tmp_path, examples_dir):
    import subprocess
    import sys

    build_skill([examples_dir / "isis"], tmp_path / "skill")
    proc = subprocess.run(
        [sys.executable, str(tmp_path / "skill" / "scripts" / "graph_query.py"), "search", "LDP"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "cause:isis:isis-75700908-1" in proc.stdout


def test_non_empty_output_dir_needs_force(tmp_path, examples_dir):
    out = tmp_path / "skill"
    build_skill([examples_dir / "isis"], out)
    with pytest.raises(FileExistsError):
        build_skill([examples_dir / "isis"], out)
    build_skill([examples_dir / "isis"], out, overwrite=True)


def test_rendering_is_deterministic(examples_dir):
    first = render_files(_bundle(examples_dir))
    second = render_files(_bundle(examples_dir))
    assert first == second


def test_optional_parts_can_be_dropped(tmp_path, examples_dir):
    options = SkillOptions(include_graph_asset=False, include_query_script=False, include_report=False)
    result = build_skill([examples_dir / "isis"], tmp_path / "skill", options=options)
    files = set(result.relative_files)
    assert not {name for name in files if name.startswith(("assets/", "scripts/"))}
    assert "references/conversion-report.md" not in files


def test_every_generated_file_is_non_empty_utf8(tmp_path, examples_dir):
    result = build_skill([examples_dir / "isis"], tmp_path / "skill")
    for path in result.files:
        assert path.read_text(encoding="utf-8").strip()


def _bundle(examples_dir):
    from graph2skill.skill import build_bundle

    return build_bundle([examples_dir / "isis"])


def test_output_path_that_is_a_file_is_rejected(tmp_path, examples_dir):
    target = tmp_path / "not-a-dir"
    target.write_text("x", encoding="utf-8")
    with pytest.raises(FileExistsError):
        build_skill([examples_dir / "isis"], target)


def test_every_markdown_link_and_anchor_resolves(tmp_path, examples_dir):
    import re

    root = tmp_path / "skill"
    build_skill([examples_dir / "isis"], root)
    texts = {path: path.read_text(encoding="utf-8") for path in root.rglob("*.md")}
    checked = 0
    for path, text in texts.items():
        for target in re.findall(r"\]\(([^)\s]+)\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            file_part, _, anchor_part = target.partition("#")
            if file_part:
                resolved = (path.parent / file_part).resolve()
                assert resolved.exists(), f"{path.name} links to missing {file_part}"
            else:
                resolved = path
            if anchor_part:
                target_text = texts.get(resolved, "")
                assert f'<a id="{anchor_part}"></a>' in target_text, f"{path.name}: dead anchor #{anchor_part}"
            checked += 1
    assert checked > 50
