"""Edge conditions reach the document, marked unevaluated, and are checked against the graph.

A relation the source only asserts under a condition is narrower than the same
relation without it.  Dropping the condition widens a criterion past what the
source claims, so every rendered relation carries its condition along.
"""

from subkg2skill.compose import build_doc, cell
from subkg2skill.doc import CONDITION_RE
from subkg2skill.graph import Graph
from subkg2skill.lint import lint_text
from subkg2skill.loader import RawBundle
from subkg2skill.markdown import render_doc
from subkg2skill.playbook import build_playbook
from subkg2skill.verify import verify_text

from tests.conftest import make_edge, make_node

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


def document(graph, symptom=ISIS):
    doc = build_doc(graph, build_playbook(graph, graph.nodes[symptom]))
    return render_doc(doc, name="isis", description="IS-IS邻居无法建立。")


def section(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


def test_a_verdict_condition_stays_with_its_criterion(example_graph):
    text = document(example_graph)
    marker = "〔条件：对端接口未收到本端Hello（未求值）〕"
    # 跳转信息里：条件写在冒号之后，判据本身不变
    step = section(text, "## 步骤1", "## 步骤2")
    assert f"`邻居状态 == 'Init' 且持续 60s 无变化`：{marker}定位根因" in step
    # 根因对照表的现象列里也带着
    assert marker in section(text, "# 根因对照表", "未找到根因")


def test_cause_and_check_conditions_are_rendered_where_they_apply(example_graph):
    text = document(example_graph)
    # symptom -has_cause-> cause 的条件：这个原因什么时候才是候选
    assert "适用条件：〔条件：互联接口.MTU 不等于 对端MTU（未求值）〕" in text
    # check -next_step-> check 的条件：什么时候才跑这条采集
    assert "执行条件：〔条件：邻居状态非 Up（未求值）〕" in section(text, "# 前置检查", "## 步骤跳转表")


def test_a_handoff_condition_uses_the_same_marker(example_graph):
    assert "转交：「收集信息并转技术支持」〔条件：上述检查均无异常（未求值）〕" in document(example_graph)


def test_rendered_conditions_are_grounded_and_lint_clean(example_graph):
    text = document(example_graph)
    assert len(CONDITION_RE.findall(text)) >= 5
    result = verify_text(text, example_graph)
    assert result.ok, [finding.render() for finding in result.errors]
    assert result.checked.get("condition", 0) >= 5
    assert lint_text(text).ok


def test_an_invented_condition_is_an_error(example_graph):
    text = document(example_graph).replace("对端接口未收到本端Hello", "对端接口光功率过低")
    result = verify_text(text, example_graph)
    assert any(finding.kind == "条件" and "光功率过低" in finding.text for finding in result.errors)


def test_a_step_reference_inside_a_condition_is_not_a_jump():
    nodes = [
        make_node("symptom_a", "symptom", "端口不通"),
        make_node("cause_b", "cause", "配置错误"),
        make_node("observation_c", "observation", "配置缺失", attrs={"normalized_expression": "配置项不存在"}),
        make_node("check_d", "check", "查看配置", attrs={"command_templates": ["display config"]}),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge("e2", "diagnosed_by", "cause_b", "check_d"),
        make_edge("e3", "observes", "check_d", "observation_c"),
        make_edge(
            "e4", "supports", "observation_c", "cause_b",
            condition={"type": "text", "expression": "已完成步骤 7 的割接"},
            condition_status="text_only",
        ),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges))
    text = document(graph, "symptom_a")
    assert "〔条件：已完成步骤 7 的割接（未求值）〕" in text
    assert not [issue for issue in lint_text(text).errors if "不存在的步骤" in issue.message]


def test_truncation_never_cuts_a_condition_in_half():
    text = "`判据`" + "x" * 280 + "〔条件：一段很长的来源条件文本（未求值）〕"
    cut = cell(text)
    assert cut.endswith("…")
    assert "〔" not in cut


# -- 修复动作没写的字段：说出来，而不是不说 -----------------------------------
def _repair_graph(repair_attrs):
    nodes = [
        make_node("symptom_a", "symptom", "端口不通"),
        make_node("cause_b", "cause", "配置错误"),
        make_node("repair_c", "repair", "补齐配置", attrs=repair_attrs),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge("e2", "repaired_by", "cause_b", "repair_c"),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges))
    return graph


def _fix_cell(graph):
    text = document(graph, "symptom_a")
    row = next(line for line in text.splitlines() if line.startswith("| 配置错误 |"))
    return row.split(" | ")[2]


def test_a_repair_that_says_nothing_about_risk_is_reported_as_silent():
    fix = _fix_cell(_repair_graph({"command_templates": ["undo shutdown"]}))
    assert "来源未给出：前置条件、业务影响、回退方法" in fix


def test_what_a_repair_does_say_is_shown_and_only_the_rest_is_missing():
    graph = _repair_graph(
        {
            "command_templates": ["undo shutdown"],
            "preconditions": ["已获得变更窗口"],
            "service_impact": "",
            "rollback": "shutdown",
        }
    )
    fix = _fix_cell(graph)
    assert "前置条件：已获得变更窗口" in fix
    assert "回退：shutdown" in fix
    assert "来源未给出：业务影响" in fix and "前置条件、" not in fix
    text = document(graph, "symptom_a")
    assert verify_text(text, graph).ok


def test_a_repaired_by_condition_follows_its_repair(example_graph):
    nodes = [
        make_node("symptom_a", "symptom", "端口不通"),
        make_node("cause_b", "cause", "配置错误"),
        make_node("repair_c", "repair", "补齐配置", attrs={"command_templates": ["undo shutdown"]}),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge(
            "e2", "repaired_by", "cause_b", "repair_c",
            condition={"type": "text", "expression": "业务低峰期"}, condition_status="text_only",
        ),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges))
    assert "`undo shutdown`〔条件：业务低峰期（未求值）〕" in _fix_cell(graph)
