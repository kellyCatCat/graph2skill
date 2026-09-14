"""The package around SKILL.md: frontmatter, layout, and the internal material."""

import json

import pytest

from subkg2skill.lint import lint_text
from subkg2skill.playbook import build_playbook
from subkg2skill.render import (
    MAX_DESCRIPTION,
    BuildOptions,
    RenderError,
    build_package,
    default_description,
    normalise_name,
    suggested_slug,
)

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


@pytest.fixture()
def package(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    options = BuildOptions(name="isis-neighbor-down", sources=["examples/subgraph"])
    return build_package(example_graph, playbook, options)


@pytest.fixture()
def internal_package(example_graph):
    """The same build, with the build-time material asked for."""
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    options = BuildOptions(
        name="isis-neighbor-down",
        sources=["examples/subgraph"],
        emit_evidence=True,
        emit_subgraph=True,
        emit_script=True,
    )
    return build_package(example_graph, playbook, options)


def test_only_skill_md_ships(package):
    # 子图不对外暴露：交付物就是一个 SKILL.md。
    assert set(package.files) == {"SKILL.md"}
    assert package.internal == {}


def test_generated_document_passes_its_own_linter(package):
    result = lint_text(package.files["SKILL.md"])
    assert result.ok, [issue.render() for issue in result.errors]


def test_description_is_phenomenon_plus_when_to_use(example_graph):
    description = default_description(example_graph.nodes[ISIS])
    assert description.startswith("IS-IS邻居无法建立：")
    assert "ISIS邻居Down" in description and "时使用" in description
    assert len(description) <= MAX_DESCRIPTION


def test_custom_description_wins(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    package = build_package(
        example_graph, playbook, BuildOptions(name="x", description="自定义描述。")
    )
    assert "description: 自定义描述。" in package.files["SKILL.md"]


def test_document_does_not_point_at_files_it_does_not_ship(package):
    document = package.files["SKILL.md"]
    for gone in ("reference/evidence.md", "reference/subgraph.json", "scripts/kg_query.py"):
        assert gone not in document


def test_evidence_file_carries_sources_and_caveats(internal_package):
    evidence = internal_package.internal["evidence.md"]
    assert "《NE40E 维护宝典.pdf》" in evidence
    assert "候选知识" in evidence and "人工复核=否" in evidence
    assert "`symptom_7f1c02aa93be4d61b0c5e210`" in evidence
    assert "未求值" in evidence


def test_evidence_records_verdict_strength(internal_package):
    evidence = internal_package.internal["evidence.md"]
    assert "**支持**" in evidence and "**确认**" in evidence and "**排除**" in evidence


def test_evidence_flags_example_specific_content(internal_package):
    assert "案例特定" in internal_package.internal["evidence.md"]


def test_data_file_is_the_faults_own_slice(internal_package, example_graph):
    data = json.loads(internal_package.internal["subgraph.json"])
    ids = {node["node_id"] for node in data["nodes"]}
    assert ISIS in ids
    # 切片只含这个故障走得到的节点
    assert ids <= set(example_graph.nodes)
    assert {node["node_type"] for node in data["nodes"]} >= {"cause", "check", "observation", "repair"}
    assert data["meta"]["data_mode"] == "full"
    assert data["nodes"][0]["provenance"]


def test_slim_data_drops_bookkeeping_fields(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    package = build_package(
        example_graph, playbook, BuildOptions(name="x", emit_subgraph=True, data_mode="slim")
    )
    data = json.loads(package.internal["subgraph.json"])
    assert "canonical_key" not in data["nodes"][0]
    assert "semantic_review" not in data["nodes"][0]


def test_script_without_data_says_so(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    package = build_package(example_graph, playbook, BuildOptions(name="x", emit_script=True))
    assert "subgraph.json" not in package.internal
    assert any("查询脚本" in note for note in package.notes)


def test_internal_material_is_written_beside_the_skill(tmp_path, internal_package):
    target = tmp_path / "out" / "isis-neighbor-down"
    internal_package.write(target)
    assert sorted(path.name for path in target.iterdir()) == ["SKILL.md"]
    beside = tmp_path / "out" / "isis-neighbor-down.internal"
    assert sorted(path.name for path in beside.iterdir()) == [
        "evidence.md",
        "kg_query.py",
        "subgraph.json",
    ]


def test_notes_report_an_empty_section(example_graph):
    other = example_graph.nodes["symptom_2ad4471b8c0f4e2ab7d31f55"]
    playbook = build_playbook(example_graph, other)
    package = build_package(example_graph, playbook, BuildOptions(name="x"))
    assert any("没有可展开的候选原因" in note for note in package.notes)


def test_names_are_normalised_or_rejected():
    assert normalise_name("ISIS Neighbor Down") == "isis-neighbor-down"
    assert normalise_name("isis_neighbor") == "isis-neighbor"
    with pytest.raises(RenderError) as excinfo:
        normalise_name("邻居震荡")
    assert "英文" in str(excinfo.value)


def test_suggested_slug_is_valid_but_not_a_translation(example_graph):
    slug = suggested_slug(example_graph.nodes["symptom_2ad4471b8c0f4e2ab7d31f55"])
    assert slug.startswith("fault-") and normalise_name(slug) == slug


def test_write_refuses_to_clobber(tmp_path, package):
    target = tmp_path / "skill"
    package.write(target)
    assert (target / "SKILL.md").exists()
    with pytest.raises(RenderError):
        package.write(target)
    package.write(target, force=True)


def test_rendering_is_deterministic(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    options = BuildOptions(name="x")
    first = build_package(example_graph, playbook, options).files["SKILL.md"]
    second = build_package(example_graph, playbook, options).files["SKILL.md"]
    assert first == second
