"""The package around SKILL.md: frontmatter and layout."""

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
    options = BuildOptions(name="isis-neighbor-down")
    return build_package(example_graph, playbook, options)


def test_only_skill_md_ships(package):
    # 子图不对外暴露：交付物就是一个 SKILL.md。
    assert set(package.files) == {"SKILL.md"}


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


def test_nothing_is_written_beside_the_skill(tmp_path, package):
    target = tmp_path / "out" / "isis-neighbor-down"
    package.write(target)
    assert sorted(path.name for path in target.iterdir()) == ["SKILL.md"]
    assert sorted(path.name for path in target.parent.iterdir()) == ["isis-neighbor-down"]


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
