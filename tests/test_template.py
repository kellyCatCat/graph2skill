"""The four-section document: what goes in each section, and what must not."""

import pytest

from subkg2skill.graph import Graph
from subkg2skill.loader import RawBundle
from subkg2skill.playbook import build_playbook
from subkg2skill.template import (
    NOT_FOUND,
    NO_FIX,
    build_doc,
    normalise_command,
    param_display,
    param_key,
    parameters_in,
    render_doc,
)
from tests.conftest import make_edge, make_node

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


@pytest.fixture()
def doc(example_graph):
    return build_doc(example_graph, build_playbook(example_graph, example_graph.nodes[ISIS]))


@pytest.fixture()
def text(doc):
    return render_doc(doc, name="isis-neighbor-down", description="IS-IS邻居无法建立。")


# -- 入参列表 ------------------------------------------------------------
def test_required_slots_are_required(doc):
    slots = {param.display: param for param in doc.params}
    assert slots["neName"].required and slots["neName"].note == "现场提供"


def test_precheck_parameters_are_required(doc):
    param = next(p for p in doc.params if p.token == "interface-type")
    assert param.required and "前置检查步骤" in param.note


def test_repair_parameters_are_optional(doc):
    param = next(p for p in doc.params if p.token == "mtu-value")
    assert not param.required and "修复动作参数" in param.note


def test_display_name_still_matches_the_cli_name(doc):
    for param in doc.params:
        assert param_key(param.display) == param_key(param.token)


def test_param_display_keeps_non_ascii_names():
    assert param_display("端口") == "端口"
    assert param_display("interface-type") == "interface type"


def test_parameters_are_extracted_from_commands():
    assert parameters_in(["display interface <if-type><if-num>", "display x"]) == ["if-type", "if-num"]


def test_source_braces_are_rebracketed_without_renaming():
    assert normalise_command("mtu {mtu-value}") == "mtu <mtu-value>"
    assert normalise_command("interface [if-name]") == "interface <if-name>"


# -- 前置检查 ------------------------------------------------------------
def test_prechecks_are_the_entry_checks(doc):
    assert [p.title for p in doc.prechecks] == ["查看 IS-IS 邻居状态", "比对两端 IS-IS 接口配置"]
    assert doc.prechecks[0].commands == ["display isis peer", "display isis peer verbose"]


def test_precheck_collects_intent_and_fields(doc):
    assert "确认邻居当前状态" in doc.prechecks[0].collect
    assert "`邻居状态`" in doc.prechecks[0].collect


def test_precheck_does_not_judge_a_cause_that_has_its_own_step(doc):
    # 三个 has_cause 原因都在排查步骤里判定，前置检查不重复判定
    assert all(not precheck.verdicts for precheck in doc.prechecks)


def test_precheck_judges_causes_that_have_no_step(example_graph):
    # 承载业务中断没有 has_cause，共用的检查所判定的原因只能在前置检查里给出
    other = example_graph.nodes["symptom_2ad4471b8c0f4e2ab7d31f55"]
    doc = build_doc(example_graph, build_playbook(example_graph, other))
    verdicts = [line for precheck in doc.prechecks for line in precheck.verdicts]
    assert any("判定根因为" in line for line in verdicts)


# -- 排查步骤 ------------------------------------------------------------
def test_one_step_per_cause_in_rank_order(doc):
    assert [step.name for step in doc.steps] == [
        "检查两端接口 MTU 配置不一致",
        "检查两端区域地址（Area ID）配置不一致",
        "检查物理链路或光模块异常",
    ]
    assert [step.index for step in doc.steps] == [1, 2, 3]


def test_step_points_at_the_precheck_that_produced_its_criterion(doc):
    assert doc.steps[0].reuse_note.startswith("复用前置检查步骤 1")
    assert "查看 IS-IS 邻居状态" in doc.steps[0].reuse_note
    assert doc.steps[1].reuse_note.startswith("复用前置检查步骤 2")


def test_step_issues_its_own_command_when_not_collected_yet(doc):
    assert doc.steps[2].commands == [
        "display interface <interface-type><interface-number>",
        "display transceiver interface <interface-type><interface-number> verbose",
    ]


def test_supports_only_causes_are_labelled_not_promoted(doc):
    mtu = doc.steps[0].branches[0]
    assert "定位根因“两端接口 MTU 配置不一致”" in mtu.outcome
    assert "仅支持性证据" in mtu.outcome


def test_confirms_causes_carry_no_hedge(doc):
    area = doc.steps[1].branches[0]
    assert "定位根因“两端区域地址（Area ID）配置不一致”" in area.outcome
    assert "仅支持性证据" not in area.outcome


def test_excludes_branch_moves_on_instead_of_locating(doc):
    exclude = next(b for b in doc.steps[2].branches if "排除根因" in b.outcome)
    assert "定位根因" not in exclude.outcome


def test_last_step_falls_through_to_not_found(doc):
    assert NOT_FOUND in doc.steps[-1].branches[-1].outcome
    assert "顺序执行步骤" in doc.steps[0].branches[-1].outcome


def test_jump_targets_all_exist(text):
    import re

    steps = {int(n) for n in re.findall(r"^## 步骤(\d+)：", text, re.M)}
    for target in re.findall(r"顺序执行步骤 (\d+)", text):
        assert int(target) in steps


def test_cause_without_a_criterion_says_so():
    nodes = [
        make_node("symptom_a", "symptom", "现象"),
        make_node("cause_b", "cause", "无判据的原因"),
    ]
    edges = [make_edge("edge_1", "has_cause", "symptom_a", "cause_b")]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    doc = build_doc(graph, build_playbook(graph, graph.nodes["symptom_a"]))
    assert "本子图未给出该原因的判定观测" in doc.steps[0].branches[0].criterion
    assert doc.steps[0].causes == ["无判据的原因"]
    assert any("未给出判定观测" in cause.evidence for cause in doc.root_causes)


# -- 根因对照表 ----------------------------------------------------------
def test_every_located_cause_appears_verbatim(doc):
    listed = {cause.name for cause in doc.root_causes}
    for step in doc.steps:
        for cause in step.causes:
            assert cause in listed
    assert NOT_FOUND in listed


def test_fix_copies_source_commands(doc):
    mtu = next(c for c in doc.root_causes if c.name == "两端接口 MTU 配置不一致")
    assert "`mtu <mtu-value>`" in mtu.fix
    assert "影响：重启接口会导致该接口业务短暂中断" in mtu.fix
    assert "回退：恢复原 MTU 取值并重启接口" in mtu.fix


def test_fix_copies_prose_when_the_source_gave_no_command(doc):
    optical = next(c for c in doc.root_causes if c.name == "物理链路或光模块异常")
    assert "清洁光纤接头并重新插拔" in optical.fix
    assert "`" not in optical.fix.split("影响")[0]  # 没有凭空生成命令


def test_missing_repair_is_reported_not_invented():
    nodes = [
        make_node("symptom_a", "symptom", "现象"),
        make_node("cause_b", "cause", "原因"),
        make_node("check_c", "check", "检查", attrs={"command_templates": ["display x"]}),
        make_node(
            "observation_d", "observation", "异常", attrs={"normalized_expression": "x == 1"}
        ),
    ]
    edges = [
        make_edge("edge_1", "has_cause", "symptom_a", "cause_b"),
        make_edge("edge_2", "diagnosed_by", "cause_b", "check_c"),
        make_edge("edge_3", "observes", "check_c", "observation_d"),
        make_edge("edge_4", "confirms", "observation_d", "cause_b"),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    doc = build_doc(graph, build_playbook(graph, graph.nodes["symptom_a"]))
    assert next(c for c in doc.root_causes if c.name == "原因").fix == NO_FIX


def test_recheck_only_when_the_source_links_a_verification():
    nodes = [
        make_node("symptom_a", "symptom", "现象"),
        make_node("cause_b", "cause", "原因"),
        make_node("repair_c", "repair", "修复", attrs={"command_templates": ["undo shutdown"]}),
        make_node("check_d", "check", "复检", attrs={"command_templates": ["display verify"]}),
    ]
    edges = [
        make_edge("edge_1", "has_cause", "symptom_a", "cause_b"),
        make_edge("edge_2", "repaired_by", "cause_b", "repair_c"),
        make_edge("edge_3", "next_step", "repair_c", "check_d"),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    doc = build_doc(graph, build_playbook(graph, graph.nodes["symptom_a"]))
    assert next(c for c in doc.root_causes if c.name == "原因").recheck == "`display verify`"


def test_no_recheck_reads_as_a_dash(doc):
    assert all(cause.recheck == "-" for cause in doc.root_causes)


# -- 渲染 ----------------------------------------------------------------
def test_sections_are_present_and_ordered(text):
    positions = [text.index(f"# {name}") for name in ("入参列表", "前置检查", "排查步骤", "根因对照表")]
    assert positions == sorted(positions)


def test_frontmatter_is_name_and_description_only(text):
    block = text.split("---\n")[1]
    assert {line.split(":")[0] for line in block.splitlines() if line.strip()} == {
        "name",
        "description",
    }


def test_observation_expressions_never_become_commands(text):
    # 回显不是命令来源：观测里的表达式不应出现在 CLI 命令行上
    for line in text.splitlines():
        if line.strip().startswith("- CLI 命令") or "**CLI 命令**" in line:
            assert "==" not in line
