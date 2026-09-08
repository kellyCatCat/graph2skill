from graph2skill.loader import load_graph
from graph2skill.merge import merge_graphs


def test_merge_unions_nodes_and_records_conflicts(data_dir):
    left = load_graph(data_dir / "minimal.json")
    right = load_graph(data_dir / "messy.jsonc")
    merged, report = merge_graphs([left, right], graph_id="MERGED")

    assert merged.graph_id == "MERGED"
    assert merged.domain == "DEMO"
    assert set(merged.nodes) == {"scenario:demo:1", "cause:demo:1", "case:demo:1"}

    scenario = merged.nodes["scenario:demo:1"]
    assert scenario.sources == ["doc_a", "doc_b"]
    assert scenario.name == "示例场景"  # first document wins
    assert scenario.data["nameAliases"] == ["示例场景（别名）"]
    assert "另一个来源的现象" in scenario.observations
    assert any(conflict.field == "name" for conflict in report.conflicts)


def test_relations_are_deduplicated_by_semantic_key(data_dir):
    graph = load_graph(data_dir / "minimal.json")
    merged, report = merge_graphs([graph, graph])
    assert len(merged.relations) == len(graph.relations)
    assert report.merged_relation_keys == ["HAS_CAUSE|scenario:demo:1|cause:demo:1"]
    assert report.node_total_in == 2 * len(graph.nodes)


def test_entry_nodes_and_sources_are_unioned(data_dir):
    left = load_graph(data_dir / "minimal.json")
    right = load_graph(data_dir / "messy.jsonc")
    merged, _ = merge_graphs([left, right])
    assert merged.entry_node_ids == ["scenario:demo:1", "scenario:demo:missing"]


def test_domain_override_wins(data_dir):
    graph = load_graph(data_dir / "minimal.json")
    merged, report = merge_graphs([graph], domain="OTHER")
    assert merged.domain == "OTHER"
    assert not report.conflicts
