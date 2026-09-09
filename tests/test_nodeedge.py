import json

import pytest

from graph2skill.loader import GraphLoadError, expand_inputs, load_graph, load_graphs
from graph2skill.merge import merge_graphs
from graph2skill.nodeedge import (
    classify_records,
    find_pair,
    graph_from_records,
    join_pdf_lines,
    looks_like_cli,
    node_from_record,
    relation_from_record,
)


@pytest.fixture()
def node_edge_dir(examples_dir):
    return examples_dir / "nodeedge"


def test_join_pdf_lines_repairs_wrapped_text():
    assert join_pdf_lines("display isis last-peer-\nchange") == "display isis last-peer-change"
    assert join_pdf_lines("IS-IS 1引入IS-IS 2路由\n时未配置路由策略。") == "IS-IS 1引入IS-IS 2路由时未配置路由策略。"
    assert join_pdf_lines("display ospf peer last-\nnbr-down") == "display ospf peer last-nbr-down"


def test_looks_like_cli():
    assert looks_like_cli("display isis peer verbose")
    assert not looks_like_cli("确认IGP邻居在6小时内是否存在变化")
    assert not looks_like_cli("Policy State")


def test_classify_records():
    assert classify_records([{"node_id": "n1", "node_type": "cause"}]) == "nodes"
    assert classify_records([{"edge_id": "e1", "edge_type": "caused_by"}]) == "edges"
    assert classify_records([{"source": "a", "target": "b"}]) == "edges"
    assert classify_records([{"whatever": 1}]) == ""


def test_node_record_maps_the_documented_fields():
    node = node_from_record(
        {
            "node_id": "cause_1", "node_type": "cause", "name": "示例原因",
            "description": "第一行\n第二行。", "aliases": ["别名"],
            "attrs": {"cause_kind": "configuration", "fault_mechanism": "机理说明"},
            "scope": {"vendor": "Huawei", "product_family": "NetEngine40E"},
            "diagnostic_contexts": [{"section": "28.21.4", "title": "28.21.4 场景分析"}],
            "provenance": [{"document": "宝典.pdf", "page": 1157, "section": "28.21.4",
                            "block_id": "p1157_b002", "quote": "原文\n摘录"}],
            "quality_flags": ["verify"],
        }
    )
    assert node.type == "CAUSE"
    assert node.description == "第一行第二行。"
    assert node.data["nameAliases"] == ["别名"]
    assert node.data["section"] == "28.21.4"
    assert node.observations == ["机理说明"]
    assert node.data["quality_flags"] == ["verify"]  # 未映射的字段原样保留
    prov = node.provenance[0]
    assert prov.source_path == "宝典.pdf"
    assert prov.anchor == "p1157_b002"
    assert prov.excerpt == "原文摘录"
    assert "28.21.4" in prov.locator and "p1157" in prov.locator


def test_commands_come_from_explicit_fields_only():
    with_attr = node_from_record(
        {"node_id": "check_1", "node_type": "check", "name": "确认邻居变化",
         "attrs": {"command": ["display isis last-peer-change"]},
         "provenance": [{"document": "d.pdf", "quote": "display ospf peer"}]}
    )
    assert with_attr.commands == ["display isis last-peer-change"]  # 证据里的 quote 不算命令

    named = node_from_record({"node_id": "check_2", "node_type": "check", "name": "display isis peer verbose"})
    assert named.commands == ["display isis peer verbose"]  # 检查项名称本身是 CLI

    cause = node_from_record({"node_id": "cause_2", "node_type": "cause", "name": "display something"})
    assert cause.commands == []  # 只有检查项才按名称推断


def test_relation_record_maps_condition_and_evidence():
    relation = relation_from_record(
        {
            "edge_id": "edge_1", "edge_type": "diagnosed_by",
            "source": "cause_1", "target": "check_1",
            "condition": "邻居状态有变化", "condition_status": "conditional",
            "diagnostic_context": {"section": "4.3.3", "title": "4.3.3 采集"},
            "evidence": [{"document": "宝典.pdf", "page": 64, "block_id": "b1", "quote": "display isis\npeer"}],
        }
    )
    assert relation.type == "DIAGNOSED_BY"
    assert relation.condition == "邻居状态有变化"
    assert relation.data["condition_status"] == "conditional"
    assert relation.provenance[0].excerpt == "display isis peer"


def test_records_without_ids_are_skipped_with_warnings():
    graph = graph_from_records([{"node_type": "cause"}], [{"edge_type": "x", "source": "a"}])
    assert not graph.nodes and not graph.relations
    assert len(graph.warnings) == 2


def test_graph_infers_domain_and_entry_nodes(node_edge_dir):
    graph = load_graph(node_edge_dir)
    assert graph.domain == "NetEngine40E"
    assert graph.entry_node_ids == ["symptom_9a1b2c3d4e5f60718293a4b5"]
    assert len(graph.nodes) == 6 and len(graph.relations) == 5


def test_directory_is_loaded_as_one_graph(node_edge_dir):
    assert expand_inputs([node_edge_dir]) == [node_edge_dir]
    graphs = load_graphs([node_edge_dir])
    assert len(graphs) == 1


def test_find_pair(node_edge_dir, tmp_path):
    node_path, edge_path = find_pair(node_edge_dir)
    assert node_path.name == "node.json" and edge_path.name == "edge.json"
    assert find_pair(tmp_path) == (None, None)


def test_a_bare_array_file_is_accepted(tmp_path):
    path = tmp_path / "nodes.json"
    path.write_text(json.dumps([{"node_id": "n1", "node_type": "cause", "name": "甲"}]), encoding="utf-8")
    graph = load_graph(path)
    assert graph.nodes["n1"].name == "甲"


def test_an_unrecognisable_array_is_rejected(tmp_path):
    path = tmp_path / "weird.json"
    path.write_text(json.dumps([{"foo": 1}]), encoding="utf-8")
    with pytest.raises(GraphLoadError):
        load_graph(path)


def test_both_formats_merge_together(node_edge_dir, examples_dir):
    graphs = load_graphs([node_edge_dir, examples_dir / "isis" / "isis_ne40_manual.json"])
    merged, report = merge_graphs(graphs)
    assert len(merged.nodes) == 20
    assert {"CAUSE", "CHECK", "SYMPTOM", "SCENARIO"} <= {node.type for node in merged.nodes.values()}
    # 两份图声明的 domain 不同，如实记一笔冲突而不是悄悄挑一个
    assert [conflict.field for conflict in report.conflicts] == ["domain"]
    merged, report = merge_graphs(graphs, domain="IP网络")
    assert merged.domain == "IP网络" and not report.conflicts


def test_edge_condition_becomes_the_step_criterion(node_edge_dir):
    from graph2skill.stepskill import contexts_for_graph

    graph = load_graph(node_edge_dir)
    _, context = contexts_for_graph(graph)[0]
    assert context.steps[0].condition == "邻居状态在 6 小时内有多次变化"
    assert context.causes[0].symptom == "邻居状态在 6 小时内有多次变化"


def test_generated_skill_name_avoids_the_hash_id(node_edge_dir):
    from graph2skill.stepskill import contexts_for_graph, lint, render_markdown

    graph = load_graph(node_edge_dir)
    _, context = contexts_for_graph(graph)[0]
    assert context.name == "netengine40e-symptom-5"
    assert lint(render_markdown(context), context.allowed_commands) == []
