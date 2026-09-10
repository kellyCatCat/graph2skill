"""Indexing, validation and selection."""

from subkg2skill.graph import Graph
from subkg2skill.loader import RawBundle
from tests.conftest import make_edge, make_node


def build(nodes, edges, **kwargs):
    return Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["test"]), **kwargs)


def test_counts_and_labels(example_graph):
    assert example_graph.node_counts() == {
        "symptom": 2,
        "cause": 4,
        "check": 3,
        "observation": 4,
        "repair": 3,
        "escalation": 1,
    }
    assert example_graph.edge_counts()["has_cause"] == 3
    assert example_graph.vendors() == {"Huawei": 17}


def test_drops_dangling_edges():
    nodes = [make_node("symptom_a", "symptom", "现象")]
    edges = [make_edge("edge_1", "has_cause", "symptom_a", "cause_missing")]
    graph, report = build(nodes, edges)
    assert graph.edges == []
    assert report.dropped_edges == 1
    assert any(issue.kind == "edge_dangling" for issue in report.issues)


def test_rejects_endpoint_types_the_schema_forbids():
    nodes = [
        make_node("symptom_a", "symptom", "现象"),
        make_node("repair_b", "repair", "修复"),
    ]
    edges = [make_edge("edge_1", "has_cause", "symptom_a", "repair_b")]
    graph, report = build(nodes, edges)
    assert graph.edges == []
    assert any(issue.kind == "edge_bad_endpoints" for issue in report.issues)


def test_rejects_unknown_types():
    nodes = [make_node("thing_a", "thing", "未知"), make_node("symptom_b", "symptom", "现象")]
    edges = [make_edge("edge_1", "unknown_rel", "symptom_b", "symptom_b")]
    graph, report = build(nodes, edges)
    assert list(graph.nodes) == ["symptom_b"]
    kinds = {issue.kind for issue in report.issues}
    assert {"node_unknown_type", "edge_unknown_type"} <= kinds


def test_duplicate_records_keep_the_first():
    nodes = [make_node("symptom_a", "symptom", "第一次"), make_node("symptom_a", "symptom", "第二次")]
    graph, report = build(nodes, [])
    assert graph.nodes["symptom_a"].name == "第一次"
    assert report.duplicate_nodes == 1


def test_duplicate_relations_are_collapsed():
    nodes = [make_node("symptom_a", "symptom", "现象"), make_node("cause_b", "cause", "原因")]
    edges = [
        make_edge("edge_1", "has_cause", "symptom_a", "cause_b"),
        make_edge("edge_2", "has_cause", "symptom_a", "cause_b"),
    ]
    graph, report = build(nodes, edges)
    assert len(graph.edges) == 1
    assert report.duplicate_edges == 1


def test_strict_promotes_warnings_to_errors():
    nodes = [make_node("symptom_a", "symptom", "一"), make_node("symptom_a", "symptom", "二")]
    _, report = build(nodes, [], strict=True)
    assert report.errors
    assert not report.warnings


def test_missing_node_id_is_dropped():
    graph, report = build([{"node_type": "symptom", "name": "无 id"}], [])
    assert len(graph) == 0
    assert any(issue.kind == "node_missing_id" for issue in report.issues)


def test_edges_sort_by_rank_then_id(example_graph):
    causes = example_graph.out_edges("symptom_7f1c02aa93be4d61b0c5e210", "has_cause")
    assert [edge.rank for edge in causes] == [1, 2, 3]


def test_reachable_pulls_verdict_evidence_back_in(tiny_graph):
    reached = tiny_graph.reachable(["symptom_a"])
    assert reached == {"symptom_a", "cause_b", "check_c", "observation_d", "repair_e"}


def test_reachable_respects_depth(tiny_graph):
    assert tiny_graph.reachable(["symptom_a"], depth=1, pull_evidence=False) == {
        "symptom_a",
        "cause_b",
    }


def test_subgraph_keeps_only_internal_edges(tiny_graph):
    sub = tiny_graph.subgraph(["symptom_a", "cause_b"])
    assert len(sub) == 2
    assert [edge.edge_type for edge in sub.edges] == ["has_cause"]


def test_filter_nodes_by_section_and_type(example_graph):
    picked = example_graph.filter_nodes(node_types=["symptom"], sections=["4.3.3"])
    assert picked == {"symptom_2ad4471b8c0f4e2ab7d31f55"}


def test_filter_nodes_by_vendor_matches_case_insensitively(example_graph):
    assert len(example_graph.filter_nodes(vendors=["huawei"])) == 17


def test_search_prefers_name_matches(example_graph):
    hits = example_graph.search("区域地址")
    assert hits[0].name.startswith("两端区域地址")
    assert all("区域地址" in node.search_text() for node in hits)


def test_search_can_be_limited_by_type(example_graph):
    hits = example_graph.search("邻居", node_types=["symptom"])
    assert {node.node_type for node in hits} == {"symptom"}


def test_orphan_nodes_are_reported():
    nodes = [make_node("symptom_a", "symptom", "孤立")]
    graph, _ = build(nodes, [])
    assert [node.node_id for node in graph.orphan_nodes()] == ["symptom_a"]


def test_node_helpers_normalise_messy_fields():
    node = make_node(
        "escalation_a",
        "escalation",
        "转交",
        attrs={"collection_requirements": "单条字符串", "example_specific": True},
        aliases=["别名", "  "],
    )
    graph, _ = build([node], [])
    escalation = graph.nodes["escalation_a"]
    assert escalation.aliases == ["别名"]
    assert escalation.example_specific is True
    assert escalation.sections == ["1.2.3"]
