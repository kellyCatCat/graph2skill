"""公共前置的门槛在单故障文档里按排查步骤算。

一份 skill 覆盖几个故障时，公共前置服务的是各个场景；只覆盖一个故障时，读者同样
不知道自己会走到哪一步，所以子场景就是各个排查步骤——同样的两个问题：这条回显
说得出该进哪一步吗，够不够多的步骤要读它。
"""

import math
import re

import pytest

from subkg2skill.graph import Graph
from subkg2skill.lint import lint_text
from subkg2skill.loader import RawBundle
from subkg2skill.playbook import build_playbook
from subkg2skill.template import BuildPolicy, build_doc, render_doc
from tests.conftest import make_edge, make_node

SHARED = "display current-configuration configuration isis"


def build(nodes, edges, policy=None):
    graph, report = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    assert not report.errors
    return build_doc(graph, build_playbook(graph, graph.nodes["symptom_x"]), policy)


def records(count: int, shared: int, *, entry: str = "display isis peer"):
    """One symptom, *count* causes; the first *shared* of them run one command."""
    nodes = [make_node("symptom_x", "symptom", "邻居异常", attrs={"required_slots": ["neName"]})]
    edges = []
    if entry:
        nodes.append(
            make_node(
                "check_entry", "check", "看邻居",
                attrs={"command_templates": [entry], "intent": "看邻居状态"},
            )
        )
        edges.append(make_edge("e_entry", "diagnosed_by", "symptom_x", "check_entry", rank=0))
    for index in range(count):
        command = SHARED if index < shared else f"display cold-{index}"
        nodes += [
            make_node(f"cause_{index}", "cause", f"原因{index}"),
            make_node(
                f"check_{index}", "check", f"查{index}",
                attrs={"command_templates": [command], "intent": f"查{index}"},
            ),
            make_node(
                f"obs_{index}", "observation", f"观测{index}",
                attrs={"field": f"字段{index}", "operator": "eq", "value": f"v{index}"},
            ),
        ]
        edges += [
            make_edge(f"hc{index}", "has_cause", "symptom_x", f"cause_{index}", rank=index),
            make_edge(f"db{index}", "diagnosed_by", f"cause_{index}", f"check_{index}"),
            make_edge(f"ob{index}", "observes", f"check_{index}", f"obs_{index}"),
            make_edge(f"cf{index}", "confirms", f"obs_{index}", f"cause_{index}"),
        ]
    return nodes, edges


# -- 公共前置 ------------------------------------------------------------
def test_a_command_every_step_needs_is_collected_once():
    doc = build(*records(4, shared=4, entry=""))
    assert [precheck.commands for precheck in doc.prechecks] == [[SHARED]]
    assert all(not step.commands for step in doc.steps)
    assert all("复用@@" in step.reuse_note or "复用前置检查" in step.reuse_note
               for step in doc.steps)


def test_a_command_below_the_bar_stays_in_its_steps():
    # 4 个步骤里只有 2 个读它，50% < 80%：不进公共前置
    doc = build(*records(4, shared=2, entry=""))
    assert not any(SHARED in " ".join(p.commands) for p in doc.prechecks)
    assert doc.steps[0].commands == [SHARED]


def test_a_command_below_the_bar_is_still_issued_only_once():
    # 不够格进公共前置，也不该在两个步骤里各下发一遍
    doc = build(*records(4, shared=3, entry=""))
    assert [step.commands for step in doc.steps].count([SHARED]) == 1
    assert "复用步骤 1" in doc.steps[1].reuse_note
    assert "复用步骤 1" in doc.steps[2].reuse_note


def test_the_bar_is_adjustable():
    doc = build(*records(4, shared=2, entry=""), policy=BuildPolicy(shared_coverage=0.5))
    assert [precheck.commands for precheck in doc.prechecks] == [[SHARED]]


def test_the_bar_matches_the_documented_arithmetic():
    # 「两个步骤要两个都读，十个要八个」
    for total in (2, 5, 10):
        assert max(2, math.ceil(0.8 * total)) == {2: 2, 5: 4, 10: 8}[total]


def test_one_step_shares_nothing():
    doc = build(*records(1, shared=1, entry=""))
    assert not doc.prechecks
    assert doc.steps[0].commands == [SHARED]


def test_a_collection_step_only_one_of_several_steps_reads_goes_back_to_it():
    # 入口检查不产生任何判据，只有「原因3」拿它当检查手段：留在公共前置
    # 等于让走前三步的读者白敲一条命令
    nodes, edges = records(4, shared=4)
    nodes = [node for node in nodes if node["node_id"] not in ("check_3", "obs_3")]
    edges = [edge for edge in edges if edge["edge_id"] not in ("db3", "ob3", "cf3")]
    nodes.append(make_node("repair_3", "repair", "改配置3", attrs={"command_templates": ["undo x"]}))
    edges += [
        make_edge("e_own", "diagnosed_by", "cause_3", "check_entry"),
        make_edge("e_fix3", "repaired_by", "cause_3", "repair_3"),
    ]
    doc = build(nodes, edges)
    assert "看邻居" not in [precheck.title for precheck in doc.prechecks]
    assert doc.steps[3].commands == ["display isis peer"]


def test_a_collection_step_that_routes_stays_however_few_read_it():
    nodes, edges = records(4, shared=3)
    nodes.append(make_node("obs_entry", "observation", "邻居 Init", attrs={"field": "邻居状态"}))
    edges += [
        make_edge("e_obs", "observes", "check_entry", "obs_entry"),
        make_edge("e_cf", "confirms", "obs_entry", "cause_3"),
        make_edge("e_own", "diagnosed_by", "cause_3", "check_entry"),
    ]
    doc = build(nodes, edges)
    entry = next(p for p in doc.prechecks if p.title == "看邻居")
    assert entry.routes


# -- 步骤跳转表 ----------------------------------------------------------
def test_a_reading_that_picks_out_one_step_routes_to_it():
    doc = build(*records(4, shared=4, entry=""))
    routing = doc.scenarios[0].routing
    assert [row.scenario for row in routing] == [
        f"步骤 {index}：检查原因{index - 1}" for index in range(1, 5)
    ]


def test_a_reading_that_points_at_every_step_routes_nowhere():
    # 同一条判据指向四个步骤，分不了流
    nodes, edges = records(4, shared=0)
    nodes.append(make_node("obs_entry", "observation", "邻居非 Up", attrs={"field": "邻居状态"}))
    edges.append(make_edge("e_obs", "observes", "check_entry", "obs_entry"))
    edges += [
        make_edge(f"e_sp{index}", "supports", "obs_entry", f"cause_{index}")
        for index in range(4)
    ]
    doc = build(nodes, edges)
    assert not doc.scenarios[0].routing


def test_the_routing_table_is_rendered_and_passes_lint():
    doc = build(*records(4, shared=4, entry=""))
    text = render_doc(doc, name="demo", description="现象。出现时使用。")
    assert "## 步骤跳转表" in text
    assert "| 前置检查步骤 | 判据 | 跳转步骤 |" in text
    assert "→ **步骤 1：检查原因0**" in text
    assert lint_text(text).ok, [issue.render() for issue in lint_text(text).errors]


def test_the_routing_heading_is_not_counted_as_a_step():
    doc = build(*records(4, shared=4, entry=""))
    text = render_doc(doc, name="demo", description="现象。出现时使用。")
    assert len(re.findall(r"^## 步骤\d", text, re.M)) == 4


def test_lint_rejects_a_row_pointing_at_a_step_that_does_not_exist():
    doc = build(*records(4, shared=4, entry=""))
    text = render_doc(doc, name="demo", description="现象。出现时使用。")
    broken = text.replace("→ **步骤 1：检查原因0**", "→ **步骤 9：检查原因0**")
    messages = [issue.message for issue in lint_text(broken).issues]
    assert any("步骤跳转表指向了不存在的步骤 9" in message for message in messages)


# -- 适用步骤 ------------------------------------------------------------
def test_a_collection_step_every_step_reads_says_so():
    doc = build(*records(4, shared=4, entry=""))
    text = render_doc(doc, name="demo", description="现象。出现时使用。")
    assert "适用步骤：全部步骤" in text


def test_a_narrow_collection_step_names_the_step_it_serves():
    nodes, edges = records(4, shared=4)
    nodes.append(make_node("obs_entry", "observation", "邻居 Init", attrs={"field": "邻居状态"}))
    edges += [
        make_edge("e_obs", "observes", "check_entry", "obs_entry"),
        make_edge("e_cf", "confirms", "obs_entry", "cause_2"),
    ]
    doc = build(nodes, edges)
    text = render_doc(doc, name="demo", description="现象。出现时使用。")
    assert "适用步骤：步骤 3：检查原因2（读数用于分流判断，其他步骤可跳过）" in text


def test_the_audience_line_is_not_read_as_a_jump():
    # 「适用步骤：步骤 3：…」说的是谁读这条回显，不是跳转
    doc = build(*records(4, shared=4, entry=""))
    text = render_doc(doc, name="demo", description="现象。出现时使用。")
    assert "前置检查不允许跳转" not in " ".join(
        issue.message for issue in lint_text(text).issues
    )
