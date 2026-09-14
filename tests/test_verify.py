"""后校验：文档里的每条内容都必须能在原图里找到来源。

`lint` 查形状，`verify` 查出处——命令、根因、判据、入参逐条回查，
凡是图里没有的就是幻觉，必须删掉或改回来源写法。
"""

import pytest

from subkg2skill.cli import main
from subkg2skill.playbook import build_playbook
from subkg2skill.render import BuildOptions, build_package
from subkg2skill.verify import verify_path, verify_text

from tests.conftest import make_edge, make_node

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


@pytest.fixture()
def document(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    package = build_package(example_graph, playbook, BuildOptions(name="isis-neighbor-down"))
    return package.files["SKILL.md"]


def tamper(document: str, old: str, new: str) -> str:
    assert old in document, f"测试数据里找不到 {old!r}"
    return document.replace(old, new, 1)


# -- 生成物本身 -----------------------------------------------------------
def test_generated_document_is_fully_grounded(document, example_graph):
    result = verify_text(document, example_graph)
    assert result.ok, [finding.render() for finding in result.errors]
    assert not result.warnings, [finding.render() for finding in result.warnings]
    # 四类内容都真的查过，不是空跑
    assert {"command", "cause", "criterion", "param"} <= set(result.checked)


def test_scaffolding_wording_is_not_reported(document, example_graph):
    assert "未找到根因" in document and "复用前置检查" in document
    assert verify_text(document, example_graph).ok


@pytest.mark.parametrize(
    "dataset, symptom, unit",
    [
        ("messy", "symptom_isis", "28.21.3"),
        ("multisource", "symptom_manual", ""),
    ],
)
def test_regression_datasets_are_grounded(dataset, symptom, unit):
    """脏图、跨来源合并图生成出来的文档，同样每条都能回查。"""
    from subkg2skill.graph import Graph
    from subkg2skill.loader import load

    from tests.conftest import ROOT

    bundle, _ = load([str(ROOT / "tests" / "data" / dataset)])
    graph, _report = Graph.from_bundle(bundle)
    for scoped in [graph.scope_to_unit(unit) if unit else graph]:
        playbook = build_playbook(scoped, scoped.nodes[symptom])
        package = build_package(scoped, playbook, BuildOptions(name="x"))
        result = verify_text(package.files["SKILL.md"], scoped)
        assert result.ok, [finding.render() for finding in result.errors]


# -- 幻觉 -----------------------------------------------------------------
def test_an_invented_command_is_an_error(document, example_graph):
    tampered = tamper(
        document, "`display isis peer`", "`display isis peer statistics slot <slot-id>`"
    )
    result = verify_text(tampered, example_graph)
    assert not result.ok
    finding = result.errors[0]
    assert finding.kind == "命令" and "statistics" in finding.text
    assert "command_templates" in finding.hint


def test_a_command_lifted_from_an_observation_is_an_error():
    """回显里出现的配置片段不是命令来源——observation 是屏幕输出，不是模板。"""
    from subkg2skill.graph import Graph
    from subkg2skill.loader import RawBundle

    nodes, edges = _bundle_with_command("display current-configuration interface <if>")
    # 回显里带着那台设备当时的配置片段
    nodes[3]["attrs"]["normalized_expression"] = "配置里存在 undo shutdown"
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["x"]))
    result = verify_text(_minimal_document(command="`undo shutdown`"), graph)
    assert [finding.kind for finding in result.errors] == ["命令"]


def test_an_invented_root_cause_is_an_error(document, example_graph):
    tampered = tamper(
        document,
        "| 未找到根因 |",
        "| 对端设备 CPU 占用过高 | `CPU 使用率 高` | 重启主控板 | - |\n| 未找到根因 |",
    )
    result = verify_text(tampered, example_graph)
    kinds = [finding.kind for finding in result.errors]
    assert "根因" in kinds and "判据" in kinds


def test_a_reworded_root_cause_names_the_source_wording(document, example_graph):
    tampered = document.replace("两端接口 MTU 配置不一致", "两端接口MTU配置不一致")
    result = verify_text(tampered, example_graph)
    assert not result.ok
    assert any("两端接口 MTU 配置不一致" in finding.hint for finding in result.errors)


def test_an_invented_parameter_is_an_error(document, example_graph):
    tampered = tamper(document, "| neName | 是 |", "| slot id | 是 |")
    result = verify_text(tampered, example_graph)
    assert [finding.kind for finding in result.errors] == ["入参"]


def test_added_repair_prose_is_reported_as_a_warning(document, example_graph):
    tampered = tamper(
        document,
        "影响：重启接口会导致该接口业务短暂中断",
        "影响：重启接口会导致该接口业务短暂中断<br>建议在凌晨割接窗口执行",
    )
    result = verify_text(tampered, example_graph)
    assert result.ok  # 自由文本不算硬错误，但必须报出来
    assert any("凌晨割接窗口" in finding.text for finding in result.warnings)


def test_findings_say_where_they_are(document, example_graph):
    tampered = tamper(document, "`display isis peer`", "`display isis nonsense`")
    finding = verify_text(tampered, example_graph).errors[0]
    assert "前置检查" in finding.where


# -- 命令来源的边界 --------------------------------------------------------
def _bundle_with_command(command: str):
    nodes = [
        make_node("symptom_a", "symptom", "端口不通", attrs={"required_slots": ["neName"]}),
        make_node("cause_b", "cause", "配置错误"),
        make_node(
            "check_c",
            "check",
            "查看配置",
            attrs={"command_templates": [command], "intent": "确认配置"},
        ),
        make_node("observation_d", "observation", "配置缺失", attrs={"field": "配置项"}),
    ]
    edges = [
        make_edge("edge_1", "has_cause", "symptom_a", "cause_b"),
        make_edge("edge_2", "diagnosed_by", "symptom_a", "check_c"),
        make_edge("edge_3", "observes", "check_c", "observation_d"),
        make_edge("edge_4", "confirms", "observation_d", "cause_b"),
    ]
    return nodes, edges


def _minimal_document(*, command: str) -> str:
    return "\n".join(
        [
            "---",
            "name: x",
            "description: d",
            "---",
            "",
            "# 入参列表",
            "",
            "| 信息 | 是否必填 | 说明 |",
            "| --- | --- | --- |",
            "| neName | 是 | 现场提供 |",
            "",
            "# 前置检查",
            "",
            "1. **查看配置**",
            f"   - CLI 命令：{command}",
            "   - 采集内容：按命令回显记录结果",
            "",
            "# 排查步骤",
            "",
            "## 步骤1：检查配置错误",
            "",
            "1. **步骤名称**：检查配置错误",
            "2. **CLI 命令**：复用前置检查步骤 1 回显",
            "3. **跳转信息**：",
            "   - 以上判据均不命中：判定“未找到根因”，结束排查。",
            "4. **根因定位**：",
            "   - 配置错误",
            "",
            "# 根因对照表",
            "",
            "| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |",
            "| --- | --- | --- | --- |",
            "| 配置错误 | `配置项` | 无直接修复CLI | - |",
            "| 未找到根因 | 全部步骤走完仍未命中任何故障特征 | 输出已执行的全部检查步骤及结果摘要 | - |",
            "",
        ]
    )


def test_placeholder_spelling_does_not_break_grounding():
    """来源写 ``{x}``、文档写 ``<x>``：同一条命令，不该报成没有来源。"""
    from subkg2skill.graph import Graph
    from subkg2skill.loader import RawBundle

    nodes, edges = _bundle_with_command("display interface {interface-name}")
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["x"]))
    document = _minimal_document(command="`display interface <interface-name>`")
    result = verify_text(document, graph)
    assert result.ok, [finding.render() for finding in result.errors]
    # 命令带进来的参数也算已声明
    assert not [finding for finding in result.findings if finding.kind == "入参"]


def test_whitespace_differences_are_tolerated():
    from subkg2skill.graph import Graph
    from subkg2skill.loader import RawBundle

    nodes, edges = _bundle_with_command("display isis peer")
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["x"]))
    assert verify_text(_minimal_document(command="`display  isis peer`"), graph).ok


# -- 路径与 CLI -----------------------------------------------------------
def test_verify_path_reads_the_original_export(tmp_path, example_dir):
    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--entry", ISIS, "--name", "x", "--out", str(out)]) == 0
    result = verify_path(out, [str(example_dir)])
    assert result.ok and result.total_checked() > 10


def test_verify_path_falls_back_to_a_dumped_slice(tmp_path, example_dir):
    out = tmp_path / "skill"
    assert (
        main(
            [
                "build", str(example_dir), "--entry", ISIS, "--name", "x",
                "--out", str(out), "--with-subgraph",
            ]
        )
        == 0
    )
    assert verify_path(out).ok


def test_verify_path_without_any_graph_explains_itself(tmp_path, example_dir):
    from subkg2skill.loader import SubgraphLoadError

    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--entry", ISIS, "--name", "x", "--out", str(out)]) == 0
    with pytest.raises(SubgraphLoadError) as excinfo:
        verify_path(out)
    assert "--graph" in str(excinfo.value)


def test_build_fails_when_its_own_output_is_not_grounded(monkeypatch, tmp_path, example_dir):
    """后校验是构建的一部分：产物不可回查就不算生成成功。"""
    import subkg2skill.cli as cli

    original = cli.verify_text

    def poisoned(text, graph):
        return original(text.replace("`display isis peer`", "`display invented command`"), graph)

    monkeypatch.setattr(cli, "verify_text", poisoned)
    out = tmp_path / "skill"
    code = main(["build", str(example_dir), "--entry", ISIS, "--name", "x", "--out", str(out)])
    assert code == 1
