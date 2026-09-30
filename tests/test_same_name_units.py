"""One symptom written up chapter by chapter.

A manual that describes 「CPU占用率高」 in twenty-odd chapters gives one symptom
node with twenty-odd diagnostic units, each holding its own cause nodes under
the same few names.  ``list`` keeps the units apart on purpose; what the reader
needs from the tool is (a) to see all of them, (b) evidence of which units are
the same fault, and (c) a merge that actually folds them into one step per cause.
"""

import pytest

from subkg2skill.cli import main
from subkg2skill.graph import Graph
from subkg2skill.loader import RawBundle
from subkg2skill.playbook import cluster_same_name, fault_groups, suggest_merges
from tests.conftest import make_edge, make_node

UNITS = [f"3.{index}" for index in range(1, 26)]
CAUSES = ["路由震荡", "用户频繁上线", "报文攻击"]


def _records():
    nodes = [make_node("sym_cpu", "symptom", "CPU占用率高")]
    edges = []
    for index, unit in enumerate(UNITS):
        context = {"diagnostic_contexts": [{"section": unit, "title": unit}]}
        edge_context = {"diagnostic_context": {"section": unit, "title": unit}}
        for name in CAUSES[: 2 + index % 2]:
            cause, repair = f"cause_{name}_{index}", f"repair_{name}_{index}"
            nodes.append(make_node(cause, "cause", name, **context))
            nodes.append(
                make_node(repair, "repair", f"处理{name}",
                          attrs={"command_templates": [f"undo {name}"]}, **context)
            )
            edges.append(make_edge(f"h_{cause}", "has_cause", "sym_cpu", cause, **edge_context))
            edges.append(make_edge(f"r_{cause}", "repaired_by", cause, repair, **edge_context))
    # 一个只挂了检查、没有任何原因的入口
    nodes.append(make_node("sym_empty", "symptom", "空入口"))
    nodes.append(make_node("chk_empty", "check", "看CPU", attrs={"command_templates": ["display cpu"]}))
    edges.append(make_edge("d_empty", "diagnosed_by", "sym_empty", "chk_empty"))
    return nodes, edges


@pytest.fixture()
def graph():
    nodes, edges = _records()
    built, report = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["cpu"]))
    assert not report.errors
    return built


@pytest.fixture()
def graph_dir(write_bundle):
    return write_bundle(*_records())


def test_list_limit_zero_lists_everything(graph_dir, capsys):
    assert main(["list", str(graph_dir), "--limit", "0"]) == 0
    output = capsys.readouterr().out
    assert output.count("生成命令") == len(UNITS)
    assert "未列出" not in output


def test_list_says_a_truncated_listing_is_not_an_exclusion(graph_dir, capsys):
    assert main(["list", str(graph_dir), "--limit", "3"]) == 0
    output = capsys.readouterr().out
    assert f"另有 {len(UNITS) - 3} 个未列出" in output and "--limit 0" in output


def test_causeless_entries_are_reported_as_nothing_to_decide(graph_dir, capsys):
    assert main(["list", str(graph_dir), "--limit", "0"]) == 0
    output = capsys.readouterr().out
    assert "一个候选原因都没有" in output and "不是需要决策的事" in output


def test_plan_covers_every_scenario_build_would_write(graph_dir, capsys):
    # plan 的 --limit 只管提示条数，不能把场景也截掉
    assert main(["plan", str(graph_dir)]) == 0
    output = capsys.readouterr().out
    assert f"| 场景数 | {len(UNITS)} 个 |" in output


def test_same_name_units_are_compared(graph):
    suggestions = suggest_merges(graph, fault_groups(graph))
    assert suggestions and all(suggestion.same_name for suggestion in suggestions)
    # 同一个症状节点的两个单元，合并命令里 --entry 只出现一次
    assert all(len(suggestion.entries) == 1 for suggestion in suggestions)


def test_agreeing_units_fold_into_one_cluster(graph):
    clusters, rest = cluster_same_name(graph, suggest_merges(graph, fault_groups(graph)))
    assert rest == []
    assert len(clusters) == 1
    cluster = clusters[0]
    assert sorted(cluster.units) == sorted(UNITS)
    assert sorted(cluster.causes) == sorted(CAUSES)
    assert [node.node_id for node in cluster.entries] == ["sym_cpu"]


def test_list_prints_one_cluster_instead_of_every_pair(graph_dir, capsys):
    assert main(["list", str(graph_dir), "--suggest-merge"]) == 0
    output = capsys.readouterr().out
    assert "在多个诊断单元各写了一遍" in output
    assert f"诊断单元 : {len(UNITS)} 个" in output
    assert output.count("合并命令") == 1


def test_merging_units_folds_same_name_causes(graph_dir, tmp_path):
    out = tmp_path / "cpu"
    args = ["build", str(graph_dir), "--entry", "sym_cpu", "--name", "cpu-high", "--out", str(out)]
    for unit in UNITS:
        args += ["--unit", unit]
    assert main(args) == 0
    text = (out / "SKILL.md").read_text(encoding="utf-8")
    assert text.count("## 步骤") == len(CAUSES)
    table = text.split("# 根因对照表", 1)[1]
    for name in CAUSES:
        assert table.count(f"`undo {name}`") == 1
