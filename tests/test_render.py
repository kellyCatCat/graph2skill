"""The generated package: frontmatter, routing, wording and file layout."""

import json
import re

import pytest

from subkg2skill.graph import Graph
from subkg2skill.loader import RawBundle
from subkg2skill.playbook import build_playbook, build_playbooks
from subkg2skill.render import (
    BuildOptions,
    MAX_DESCRIPTION,
    RenderError,
    build_package,
    normalise_name,
    render_index,
    render_playbook,
)
from tests.conftest import make_edge, make_node

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


@pytest.fixture()
def package(example_graph):
    playbooks = build_playbooks(example_graph)
    options = BuildOptions(name="ipran-fault-diagnosis", sources=["examples/subgraph"])
    return build_package(example_graph, playbooks, None, options)


def frontmatter(text: str) -> dict:
    assert text.startswith("---\n")
    block = text.split("---\n", 2)[1]
    fields = {}
    for line in block.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_package_contains_the_expected_layout(package):
    names = set(package.files)
    assert "SKILL.md" in names
    assert "INSTALL.md" in names
    assert "references/index.md" in names
    assert "references/reading-guide.md" in names
    assert "data/subgraph.json" in names
    assert "scripts/kg_query.py" in names
    assert sum(name.startswith("references/playbooks/") for name in names) == 2


def test_frontmatter_is_parseable_and_bounded(package):
    fields = frontmatter(package.files["SKILL.md"])
    assert fields["name"] == "ipran-fault-diagnosis"
    assert re.match(r"^[a-z0-9][a-z0-9-]*$", fields["name"])
    description = fields["description"]
    assert description.startswith('"') and description.endswith('"')
    assert len(description) <= MAX_DESCRIPTION + 2
    assert "\n" not in description


def test_description_mentions_triggering_symptoms(package):
    description = frontmatter(package.files["SKILL.md"])["description"]
    assert "IS-IS邻居无法建立" in description


def test_custom_description_is_escaped_not_dropped(example_graph):
    options = BuildOptions(name="x", description='含"引号"的描述\n第二行')
    package = build_package(example_graph, build_playbooks(example_graph), None, options)
    line = [l for l in package.files["SKILL.md"].splitlines() if l.startswith("description:")][0]
    assert '\\"引号\\"' in line and "\n" not in line[len("description:") :]


def test_skill_md_states_the_evidence_discipline(package):
    text = package.files["SKILL.md"]
    for expected in ("candidate", "未求值", "scope", "example_specific", "command_templates", "node_id"):
        assert expected in text
    assert "references/index.md" in text


def test_skill_md_stays_short(package):
    assert len(package.files["SKILL.md"].splitlines()) < 120


def test_allowed_tools_only_appears_when_asked(example_graph):
    playbooks = build_playbooks(example_graph)
    plain = build_package(example_graph, playbooks, None, BuildOptions(name="x"))
    assert "allowed-tools" not in plain.files["SKILL.md"]
    scoped = build_package(
        example_graph, playbooks, None, BuildOptions(name="x", allowed_tools="Read, Bash")
    )
    assert "allowed-tools: Read, Bash" in scoped.files["SKILL.md"]


def test_index_routes_every_playbook(package):
    index = package.files["references/index.md"]
    for name in package.files:
        if name.startswith("references/playbooks/"):
            assert name.split("/")[-1] in index


def test_playbook_carries_causes_checks_verdicts_and_repairs(package):
    text = next(v for k, v in package.files.items() if "is-is" in k)
    assert "## 2. 候选原因对照表" in text
    assert "**确认** 原因「两端区域地址（Area ID）配置不一致」" in text
    assert "display isis peer" in text
    assert "统一两端接口 MTU" in text
    assert "《NE40E 维护宝典.pdf》" in text


def test_playbook_warns_when_only_supporting_evidence_exists(package):
    text = next(v for k, v in package.files.items() if "is-is" in k)
    assert "没有 `confirms` 关系" in text


def test_playbook_marks_example_specific_observations(package):
    text = next(v for k, v in package.files.items() if "is-is" in k)
    assert "案例特定（example_specific=true）" in text


def test_playbook_headings_nest_under_their_cause(package):
    text = next(v for k, v in package.files.items() if "is-is" in k)
    assert "### 2.1 原因：" in text and "#### 2.1.1 检查：" in text


def test_reading_guide_only_documents_present_relations(package):
    guide = package.files["references/reading-guide.md"]
    assert "`has_cause`" in guide and "`confirms`" in guide


def test_data_file_keeps_provenance_by_default(package):
    data = json.loads(package.files["data/subgraph.json"])
    assert data["meta"]["data_mode"] == "full"
    assert len(data["nodes"]) == 17 and len(data["edges"]) == 26
    assert data["nodes"][0]["provenance"]


def test_slim_data_drops_bookkeeping_fields(example_graph):
    options = BuildOptions(name="x", data_mode="slim")
    package = build_package(example_graph, build_playbooks(example_graph), None, options)
    data = json.loads(package.files["data/subgraph.json"])
    assert "canonical_key" not in data["nodes"][0]
    assert "semantic_review" not in data["nodes"][0]


def test_data_none_skips_the_bundle_and_says_so(example_graph):
    options = BuildOptions(name="x", data_mode="none")
    package = build_package(example_graph, build_playbooks(example_graph), None, options)
    assert "data/subgraph.json" not in package.files
    assert any("查询脚本" in note for note in package.notes)


def test_script_can_be_omitted(example_graph):
    options = BuildOptions(name="x", include_script=False)
    package = build_package(example_graph, build_playbooks(example_graph), None, options)
    assert "scripts/kg_query.py" not in package.files


def test_names_are_normalised_or_rejected():
    assert normalise_name("IP-RAN Fault Diagnosis") == "ip-ran-fault-diagnosis"
    assert normalise_name("ipran_kg") == "ipran-kg"
    with pytest.raises(RenderError):
        normalise_name("知识图谱")


def test_playbook_filenames_are_stable_and_unique(example_graph):
    playbooks = build_playbooks(example_graph)
    first = build_package(example_graph, playbooks, None, BuildOptions(name="x"))
    second = build_package(example_graph, playbooks, None, BuildOptions(name="x"))
    assert set(first.files) == set(second.files)


def _many_symptoms(count: int, section_of):
    nodes, edges = [], []
    for i in range(count):
        nodes.append(
            make_node(
                f"symptom_{i:04d}",
                "symptom",
                f"现象{i}",
                diagnostic_contexts=[{"section": section_of(i), "title": "标题"}],
            )
        )
        nodes.append(make_node(f"cause_{i:04d}", "cause", f"原因{i}"))
        edges.append(make_edge(f"edge_{i:04d}", "has_cause", f"symptom_{i:04d}", f"cause_{i:04d}"))
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    return graph


def test_small_index_stays_one_file(example_graph):
    files = render_index(build_playbooks(example_graph), {ISIS: "a.md", "symptom_2ad4471b8c0f4e2ab7d31f55": "b.md"})
    assert list(files) == ["references/index.md"]


def test_large_index_shards_by_diagnostic_unit():
    graph = _many_symptoms(200, lambda i: f"{i % 6 + 1}.{i}.1")
    playbooks = build_playbooks(graph)
    files = render_index(playbooks, {p.node_id: f"{p.node_id}.md" for p in playbooks})
    shards = [name for name in files if name.startswith("references/index/")]
    assert len(shards) == 6
    directory = files["references/index.md"]
    assert len(directory) < 4000
    assert all(shard.split("references/")[1] in directory for shard in shards)


def test_too_many_units_fall_back_to_fixed_chunks():
    graph = _many_symptoms(400, lambda i: f"{i}.1.1")
    playbooks = build_playbooks(graph)
    files = render_index(playbooks, {p.node_id: f"{p.node_id}.md" for p in playbooks})
    shards = sorted(name for name in files if name.startswith("references/index/"))
    assert shards == [f"references/index/part-{i:03d}.md" for i in range(1, 4)]


def test_empty_symptom_set_is_reported(tiny_graph):
    graph = tiny_graph.subgraph(["cause_b", "repair_e"])
    package = build_package(graph, [], None, BuildOptions(name="x"))
    assert any("没有 symptom" in note for note in package.notes)


def test_write_refuses_to_clobber_then_prunes(tmp_path, package):
    target = tmp_path / "skill"
    package.write(target)
    assert (target / "SKILL.md").exists()
    with pytest.raises(RenderError):
        package.write(target)
    stale = target / "references" / "playbooks" / "stale.md"
    stale.write_text("旧手册", encoding="utf-8")
    package.write(target, force=True)
    assert not stale.exists()


def test_render_playbook_is_deterministic(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    options = BuildOptions(name="x")
    assert render_playbook(example_graph, playbook, options) == render_playbook(
        example_graph, playbook, options
    )
