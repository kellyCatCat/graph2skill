"""跨 skill 的转向：一份 skill 走到头，该把读者交给谁。

图里 `refers_to` / `leads_to` 明说“这已经是另一个故障了”，选图不跨越它们——
但读者走到兜底行时需要知道下一步去哪。这些边是两份 skill 之间**唯一有来源**
的接缝，所以转向只能写来源自己指过去的地方，`verify` 逐条回查。
"""

import json

from subkg2skill.cli import main
from subkg2skill.lint import lint_text
from subkg2skill.playbook import build_playbook
from subkg2skill.render import BuildOptions, build_package
from subkg2skill.template import NO_SKILL
from subkg2skill.verify import verify_text

from tests.conftest import make_edge, make_node

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"
#: 示例图里 `leads_to` 指过去的那个故障
DOWNSTREAM = "symptom_2ad4471b8c0f4e2ab7d31f55"


def build(example_graph, **options) -> str:
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    package = build_package(
        example_graph, playbook, BuildOptions(name="isis-neighbor-down", **options)
    )
    return package.files["SKILL.md"]


# -- 转向本身 -------------------------------------------------------------
def test_a_cause_that_leads_to_another_fault_says_so(example_graph):
    # 「物理链路或光模块异常」-leads_to-> 「承载业务中断」
    document = build(example_graph)
    assert "转向故障：「承载业务中断」" in document
    # 写在它自己那一行，不是塞给所有根因
    rows = [line for line in document.splitlines() if line.startswith("| 物理链路")]
    assert rows and "转向故障" in rows[0]


def test_the_fallthrough_row_carries_the_escalation_the_source_gave(example_graph):
    # 症状 -refers_to-> 「收集信息并转技术支持」，条件「上述检查均无异常」。
    # 来源给了下一步，兜底行就不该只写“结束排查”。
    document = build(example_graph)
    row = next(line for line in document.splitlines() if line.startswith("| 未找到根因"))
    assert "转交：「收集信息并转技术支持」" in row
    assert "条件：上述检查均无异常" in row


def handoff_fragments(document: str) -> list:
    """文档里的每一条转向，按它在单元格里的写法切出来。"""
    return [
        fragment
        for line in document.splitlines()
        for cell in line.split("|")
        for fragment in cell.split("<br>")
        if fragment.strip().startswith(("转向故障", "转交："))
    ]


def test_a_handoff_never_uses_a_code_span(example_graph):
    # 反引号里的东西 verify 当命令回查，转向写成代码就成了“编造的命令”
    fragments = handoff_fragments(build(example_graph, skill_index={DOWNSTREAM: "bearer"}))
    assert len(fragments) == 2
    assert all("`" not in fragment for fragment in fragments)


def test_a_handoff_is_one_fragment_the_verifier_can_read_whole(example_graph):
    # verify 按 `；` 切片段；转向里用了它，目标名和条件就会被切成两半，
    # 整条转向也就没法作为一条来回查了
    for fragment in handoff_fragments(build(example_graph, skill_index={DOWNSTREAM: "bearer"})):
        assert "；" not in fragment


# -- slug 从哪来 ----------------------------------------------------------
def test_without_a_batch_the_handoff_names_the_fault_and_claims_nothing_else(example_graph):
    # 单独 build 时生成器并不知道有没有别的 skill，就不要替它下结论
    document = build(example_graph)
    assert "转向故障：「承载业务中断」——" in document
    assert NO_SKILL not in document
    assert lint_text(document).ok and not lint_text(document).warnings


def test_an_index_turns_the_handoff_into_something_the_reader_can_open(example_graph):
    document = build(example_graph, skill_index={DOWNSTREAM: "bearer-service-interrupted"})
    assert "转向故障：「承载业务中断」（skill: bearer-service-interrupted）" in document
    assert lint_text(document).ok


def test_a_batch_that_misses_the_target_says_the_line_stops_here(example_graph):
    # 批次存在、却没覆盖到目标：这是真缺口，如实报出来
    document = build(example_graph, skill_index={"symptom_other": "something-else"})
    assert f"转向故障：「承载业务中断」（{NO_SKILL}）" in document
    warnings = " ".join(issue.message for issue in lint_text(document).warnings)
    assert "本批次没有对应 skill" in warnings and "承载业务中断" in warnings


def test_build_each_links_the_skills_it_is_about_to_write(tmp_path, example_dir, capsys):
    # 批量时 slug 要先全定下来，否则先生成的那份不知道后生成的那份叫什么
    out = tmp_path / "batch"
    assert main(["build", str(example_dir), "--each", "--out", str(out), "--min-causes", "0"]) == 0
    written = {path.name for path in out.iterdir()}
    assert len(written) == 2
    isis = next(path for path in out.iterdir() if path.name.startswith("is-is"))
    other = next(path for path in out.iterdir() if path != isis)
    assert f"（skill: {other.name}）" in (isis / "SKILL.md").read_text(encoding="utf-8")


def test_skill_index_file_links_skills_built_one_run_at_a_time(
    tmp_path, example_dir, capsys
):
    index = tmp_path / "index.json"
    index.write_text(
        json.dumps({DOWNSTREAM: "bearer-service-interrupted"}), encoding="utf-8"
    )
    out = tmp_path / "skill"
    code = main(
        [
            "build", str(example_dir),
            "--entry", ISIS, "--unit", "28.21.3",
            "--name", "isis-neighbor-down",
            "--skill-index", str(index),
            "--out", str(out),
        ]
    )
    assert code == 0
    assert "（skill: bearer-service-interrupted）" in (out / "SKILL.md").read_text(encoding="utf-8")


# -- 后校验 ---------------------------------------------------------------
def test_the_generated_handoffs_are_grounded(example_graph):
    result = verify_text(build(example_graph), example_graph)
    assert result.ok, [finding.render() for finding in result.errors]
    assert not result.warnings, [finding.render() for finding in result.warnings]
    assert result.checked.get("link") == 2


def test_an_invented_destination_is_an_error(example_graph):
    document = build(example_graph).replace("「承载业务中断」", "「BGP邻居震荡」")
    result = verify_text(document, example_graph)
    assert [finding.kind for finding in result.errors] == ["转向"]
    assert "refers_to / leads_to" in result.errors[0].hint


def test_a_real_fault_the_source_never_pointed_at_is_still_an_error(example_graph):
    # 图里确实有这个症状——但没有任何边把读者引过去，就不能引过去
    document = build(example_graph).replace("「承载业务中断」", "「IS-IS邻居无法建立」")
    result = verify_text(document, example_graph)
    assert [finding.kind for finding in result.errors] == ["转向"]


def test_a_reworded_destination_is_pointed_back_at_the_source_wording(example_graph):
    document = build(example_graph).replace("「承载业务中断」", "「承载 业务中断!」")
    result = verify_text(document, example_graph)
    assert result.errors and "承载业务中断" in result.errors[0].hint


def test_an_invented_handoff_condition_is_reported(example_graph):
    document = build(example_graph).replace("上述检查均无异常", "确认光功率低于门限")
    result = verify_text(document, example_graph)
    assert any(finding.kind == "说法" for finding in result.warnings)


def test_a_slug_that_is_not_a_slug_is_an_error(example_graph):
    document = build(example_graph, skill_index={DOWNSTREAM: "bearer-service-interrupted"})
    issues = lint_text(document.replace("bearer-service-interrupted", "承载业务中断 skill"))
    assert any("不是合法 slug" in issue.message for issue in issues.errors)


# -- 什么不该成为转向 -----------------------------------------------------
def test_a_cause_leading_to_another_cause_is_not_a_handoff(write_bundle, tmp_path):
    """`leads_to` 落在 cause 上是本图内的传播，不是另一份 skill。"""
    from subkg2skill.graph import Graph
    from subkg2skill.loader import RawBundle

    nodes = [
        make_node("symptom_a", "symptom", "端口不通"),
        make_node("cause_b", "cause", "配置错误"),
        make_node("cause_c", "cause", "转发表项缺失"),
        make_node("check_d", "check", "查看配置", attrs={"command_templates": ["display config"]}),
        make_node(
            "observation_e", "observation", "配置缺失",
            attrs={"field": "配置项", "operator": "not_exists"},
        ),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge("e2", "diagnosed_by", "cause_b", "check_d"),
        make_edge("e3", "observes", "check_d", "observation_e"),
        make_edge("e4", "confirms", "observation_e", "cause_b"),
        make_edge("e5", "leads_to", "cause_b", "cause_c"),
    ]
    graph, report = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    assert not report.errors
    playbook = build_playbook(graph, graph.nodes["symptom_a"])
    document = build_package(graph, playbook, BuildOptions(name="port-down")).files["SKILL.md"]
    assert "转向故障" not in document
