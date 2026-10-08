"""What does not reach the document is reported, and what does reach it says where it comes from.

A symptom with no cause, a check that decides nothing, a cause with no check:
each is either rendered honestly or listed with a reason.  None of them may
vanish without a word, and none may be papered over with a pointer to a
reading the document does not have.
"""

from subkg2skill.cli import main
from subkg2skill.compose import build_doc
from subkg2skill.doc import NO_CHECK
from subkg2skill.graph import Graph
from subkg2skill.loader import RawBundle
from subkg2skill.markdown import render_doc
from subkg2skill.playbook import build_playbook

from tests.conftest import make_edge, make_node


def _nodes():
    return [
        make_node("symptom_port", "symptom", "端口不通"),
        make_node("symptom_reboot", "symptom", "设备异常重启"),
        make_node("check_if", "check", "查看接口状态", attrs={"command_templates": ["display interface brief"]}),
        make_node("obs_if", "observation", "接口为 Up", attrs={"normalized_expression": "接口状态 == 'Up'"}),
        make_node("check_log", "check", "查看告警日志", attrs={"command_templates": ["display alarm active"]}),
        make_node("obs_los", "observation", "有 LOS 告警", attrs={"normalized_expression": "存在 LOS 告警"}),
        make_node("check_cpu", "check", "查看CPU", attrs={"command_templates": ["display cpu-usage"]}),
        make_node("cause_optic", "cause", "光模块故障"),
        make_node("cause_cfg", "cause", "接口配置错误"),
        make_node("check_cfg", "check", "查看接口配置", attrs={"command_templates": ["display current-configuration"]}),
        make_node("obs_cfg", "observation", "配置已加载", attrs={"normalized_expression": "配置项存在"}),
        make_node("cause_congest", "cause", "链路拥塞"),
        make_node("repair_congest", "repair", "扩容链路", attrs={"procedure": ["调整流量或扩容"]}),
        make_node("repair_cfg", "repair", "修正配置", attrs={"command_templates": ["undo shutdown"]}),
        make_node("cause_island", "cause", "电源模块故障"),
        make_node("check_power", "check", "查看电源", attrs={"command_templates": ["display power"]}),
    ]


def _edges():
    return [
        make_edge("e1", "diagnosed_by", "symptom_port", "check_if"),
        make_edge("e2", "observes", "check_if", "obs_if"),
        make_edge("e3", "diagnosed_by", "symptom_port", "check_log"),
        make_edge("e4", "observes", "check_log", "obs_los"),
        make_edge("e5", "supports", "obs_los", "cause_optic"),
        make_edge("e6", "has_cause", "symptom_port", "cause_optic", rank=1),
        make_edge("e7", "has_cause", "symptom_port", "cause_cfg", rank=2),
        make_edge("e8", "diagnosed_by", "cause_cfg", "check_cfg"),
        make_edge("e9", "observes", "check_cfg", "obs_cfg"),
        make_edge("e10", "repaired_by", "cause_cfg", "repair_cfg"),
        make_edge("e11", "has_cause", "symptom_port", "cause_congest", rank=3),
        make_edge("e12", "repaired_by", "cause_congest", "repair_congest"),
        make_edge("e13", "diagnosed_by", "symptom_reboot", "check_cpu"),
        make_edge("e14", "diagnosed_by", "cause_island", "check_power"),
    ]


def _graph():
    graph, _ = Graph.from_bundle(RawBundle(nodes=_nodes(), edges=_edges()))
    return graph


def _step(text, number):
    return text.split(f"## 步骤{number}：", 1)[1].split("## 步骤", 1)[0]


def test_a_collection_step_that_decides_nothing_is_listed_when_it_is_dropped():
    graph = _graph()
    doc = build_doc(graph, build_playbook(graph, graph.nodes["symptom_port"]))
    assert "查看接口状态" not in [precheck.title for precheck in doc.prechecks]
    reasons = dict(doc.omitted)
    assert "不判定任何原因" in reasons["查看接口状态"]


def test_a_step_without_a_criterion_points_at_the_reading_it_actually_has():
    graph = _graph()
    text = render_doc(build_doc(graph, build_playbook(graph, graph.nodes["symptom_port"])), name="x", description="d")
    own_check = _step(text, 2)  # 接口配置错误：有自己的检查，但它不判定
    assert "`display current-configuration`" in own_check
    assert "结合本步骤回显人工判断" in own_check
    no_check = _step(text, 3)  # 链路拥塞：没有检查、没有判据，只有修复
    assert f"**CLI 命令**：{NO_CHECK}" in no_check
    assert "复用前置检查回显" not in no_check
    assert "结合现象人工判断" in no_check


def test_a_cause_decided_by_a_check_the_collection_does_not_run_runs_that_check():
    nodes = [
        make_node("symptom_a", "symptom", "端口不通"),
        make_node("cause_b", "cause", "配置错误"),
        make_node("cause_c", "cause", "协议未使能"),
        make_node("check_d", "check", "查看配置", attrs={"command_templates": ["display config"]}),
        make_node("obs_e", "observation", "配置缺失", attrs={"normalized_expression": "配置项不存在"}),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b", rank=1),
        make_edge("e2", "has_cause", "symptom_a", "cause_c", rank=2),
        make_edge("e3", "diagnosed_by", "cause_b", "check_d"),
        make_edge("e4", "observes", "check_d", "obs_e"),
        make_edge("e5", "supports", "obs_e", "cause_b"),
        # cause_c 没有自己的检查，判定它的观测来自 cause_b 的检查
        make_edge("e6", "supports", "obs_e", "cause_c"),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges))
    text = render_doc(build_doc(graph, build_playbook(graph, graph.nodes["symptom_a"])), name="x", description="d")
    step = _step(text, 2)
    assert "协议未使能" in step
    assert "复用前置检查回显" not in step.split("**跳转信息**")[0]
    assert "display config" in step


def test_the_default_build_names_the_faults_it_leaves_out(tmp_path, write_bundle, capsys):
    directory = write_bundle(_nodes(), _edges())
    assert main(["build", str(directory), "--name", "x", "--out", str(tmp_path / "out"), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "没有进入这份 skill：设备异常重启" in output
    assert "查看接口状态：前置检查的观测不判定任何原因" in output


def test_inspect_reports_what_no_symptom_leads_to(write_bundle, capsys):
    directory = write_bundle(_nodes(), _edges())
    assert main(["inspect", str(directory)]) == 0
    output = capsys.readouterr().out
    assert "症状走不到的节点：2 个" in output
    assert "电源模块故障 (cause_island，cause)" in output and "check_power" in output
    # 和 list 同一个口径
    assert "故障：1 个" in output and "另有 1 个场景没有候选原因" in output


def test_list_and_build_say_when_records_were_dropped_at_load(tmp_path, write_bundle, capsys):
    edges = _edges() + [make_edge("e99", "has_cause", "symptom_port", "cause_ghost")]
    directory = write_bundle(_nodes(), edges)
    assert main(["list", str(directory)]) == 0
    assert "载入时有 1 条告警/丢弃" in capsys.readouterr().out
    assert main(["build", str(directory), "--name", "x", "--out", str(tmp_path / "o"), "--dry-run"]) == 0
    assert "载入时有 1 条告警/丢弃" in capsys.readouterr().out
