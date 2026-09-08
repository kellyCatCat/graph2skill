import re

from graph2skill.bundle import SkillBundle, SkillOptions
from graph2skill.loader import load_graph
from graph2skill.merge import merge_graphs
from graph2skill.ontology import EN
from graph2skill.render import (
    default_description,
    default_skill_name,
    render_catalog,
    render_commands,
    render_playbook,
    render_report,
    render_skill_md,
    render_sources,
)
from graph2skill.util import anchor


def test_front_matter_is_parseable_and_bounded(example_bundle):
    text = render_skill_md(example_bundle)
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "SKILL.md must start with YAML front matter"
    front = match.group(1)
    assert front.splitlines()[0] == "name: isis-playbook"
    description = front.split("description:", 1)[1].strip()
    assert 0 < len(description) <= 1024
    assert "ISIS" in description


def test_skill_md_links_every_playbook(example_bundle):
    text = render_skill_md(example_bundle)
    for tree in example_bundle.trees:
        assert example_bundle.playbook_path(tree) in text


def test_playbook_anchors_resolve_within_the_file(example_bundle):
    tree = example_bundle.trees[0]
    text = render_playbook(example_bundle, tree)
    targets = set(re.findall(r'<a id="([^"]+)"></a>', text))
    links = set(re.findall(r"\]\(#([^)]+)\)", text))
    assert links, "the decision path should link to node details"
    assert links <= targets


def test_playbook_contains_commands_and_provenance(example_bundle):
    tree = next(t for t in example_bundle.trees if t.entry_node_id == "scenario:isis:IS-IS-2")
    text = render_playbook(example_bundle, tree)
    assert "display mpls ldp session" in text
    assert "故障处理：IP路由/IS-IS故障案例" in text
    assert "```mermaid" in text


def test_diagram_can_be_disabled(example_bundle):
    example_bundle.options.diagrams = False
    text = render_playbook(example_bundle, example_bundle.trees[0])
    assert "```mermaid" not in text


def test_mermaid_labels_are_escaped(example_bundle):
    text = render_playbook(example_bundle, example_bundle.trees[0])
    diagram = text.split("```mermaid", 1)[1].split("```", 1)[0]
    for line in diagram.splitlines():
        if "[" in line and "]" in line:
            assert line.count('"') % 2 == 0


def test_catalog_lists_every_node(example_bundle):
    text = render_catalog(example_bundle)
    for node_id in example_bundle.graph.nodes:
        assert node_id in text


def test_uncovered_nodes_are_rendered_in_full(data_dir):
    graph = load_graph(data_dir / "cyclic.json")
    merged, _ = merge_graphs([graph])
    bundle = SkillBundle.build(merged, SkillOptions(include_inferred_entries=False))
    text = render_catalog(bundle)
    assert "孤立节点" in text
    assert anchor("lonely") in text


def test_commands_index_covers_all_samples(example_bundle):
    text = render_commands(example_bundle)
    assert text.count("|") > 20
    assert "undo isis circuit-type" in text


def test_sources_index_groups_by_document(example_bundle):
    text = render_sources(example_bundle)
    assert "## `特性描述：MPLS/LDP与IGP联动.md`" in text
    assert "cot_IPRAN故障场景" in text


def test_report_mentions_merge_and_conflicts(example_bundle):
    text = render_report(example_bundle)
    assert "输入文档数: 2" in text
    assert "按 id 合并的节点: 1" in text
    assert "字段冲突" in text
    assert "isis_ne40_manual.json" in text


def test_english_output(example_bundle):
    example_bundle.options.lang = EN
    text = render_skill_md(example_bundle)
    assert "When to use" in text
    assert "Decision playbooks for the ISIS domain" in default_description(example_bundle)


def test_name_and_description_overrides(example_bundle):
    example_bundle.options.name = "custom-name"
    example_bundle.options.description = "custom description"
    assert default_skill_name(example_bundle) == "custom-name"
    assert default_description(example_bundle) == "custom description"


def test_table_cells_escape_pipes():
    graph = {
        "graphId": "P",
        "domain": "D",
        "entryNodeIds": ["a"],
        "nodes": [
            {"id": "a", "type": "SCENARIO", "name": "带 | 竖线 的名字",
             "data": {"parameterExamples": [{"command": "display x | include y"}]}}
        ],
        "relations": [],
    }
    from graph2skill.model import Graph

    bundle = SkillBundle.build(Graph.from_raw(graph))
    for row in render_commands(bundle).splitlines():
        if row.startswith("| `display"):
            assert row.count("|") - row.count("\\|") == 4


def test_skill_name_is_slugified_and_bounded(example_bundle):
    example_bundle.options.name = "Some Weird Name!! 中文"
    assert default_skill_name(example_bundle) == "some-weird-name"
    example_bundle.options.name = "x" * 120
    assert len(default_skill_name(example_bundle)) <= 64


def test_description_is_capped(example_bundle):
    example_bundle.options.description = "详" * 2000
    assert len(default_description(example_bundle)) <= 1024


def test_cjk_only_domain_still_yields_ascii_name():
    from graph2skill.model import Graph

    bundle = SkillBundle.build(Graph.from_raw({"graphId": "图谱", "domain": "路由协议", "nodes": [{"id": "a"}]}))
    name = default_skill_name(bundle)
    assert name.isascii() and name.endswith("-playbook")


def test_links_from_reference_files_are_relative_to_their_directory(example_bundle):
    from graph2skill.render import render_catalog, render_commands, render_overview

    for text in (render_catalog(example_bundle), render_commands(example_bundle), render_overview(example_bundle)):
        assert "](references/playbooks/" not in text
        assert "](playbooks/" in text


def test_links_from_skill_md_are_package_relative(example_bundle):
    assert "](references/playbooks/" in render_skill_md(example_bundle)
