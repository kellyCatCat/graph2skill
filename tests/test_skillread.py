import pytest

from graph2skill.loader import load_graph
from graph2skill.merge import merge_graphs
from graph2skill.skillread import enrich_context, read_skill_doc
from graph2skill.stepskill import contexts_for_graph, lint, render_markdown


@pytest.fixture()
def facts(examples_dir):
    return read_skill_doc(examples_dir / "skillset" / "SKILL-bgp.md")


@pytest.fixture()
def context(examples_dir):
    merged, _ = merge_graphs([load_graph(examples_dir / "skillset" / "graphs" / "bgp.json")])
    return contexts_for_graph(merged, fault_id="10")[0][1]


def test_reads_one_fault_not_its_parent_sections(facts):
    assert len(facts.faults) == 1
    fault = facts.faults[0]
    assert (fault.fault_id, fault.identifier, fault.category) == ("10", "bgp-neighbor-abnormal", "路由协议类")
    assert fault.name == "BGP邻居状态异常"
    assert fault.reference_file == "reference/fault-10-bgp-neighbor.md"


def test_reads_the_cause_table(facts):
    names = [cause.name for cause in facts.faults[0].causes]
    assert names[:2] == ["BGP邻居数量超限", "路由故障"]
    assert facts.faults[0].cause("路由故障").name_en == "Routing Failure"


def test_reads_commands_from_the_reference_decision_tree(facts):
    assert "display bgp peer" in facts.commands
    assert "display bgp all summary" in facts.commands
    assert facts.faults[0].cause("BGP邻居数量超限").commands


def test_reads_the_common_jump_table(facts):
    directions = {direction for direction, _, _ in facts.jumps}
    assert "接口DOWN/物理层异常" in directions
    assert facts.jump_for("链路异常") == ["§3.6 链路故障"]


def test_reads_the_trigger_text(facts):
    assert "BGP邻居状态异常" in facts.trigger_text


def test_match_prefers_fault_id_then_identifier_then_name(facts):
    assert facts.match(fault_id="10") is facts.faults[0]
    assert facts.match(identifier="bgp-neighbor-abnormal") is facts.faults[0]
    assert facts.match(name="BGP邻居状态异常") is facts.faults[0]
    assert facts.match(fault_id="99", identifier="nope", name="不存在") is None


def test_missing_reference_file_is_a_warning_not_a_crash(tmp_path):
    doc = tmp_path / "SKILL-x.md"
    doc.write_text(
        "# X\n\n## 2. 故障\n\n### 2.1 故障序号1：甲\n\n标识: x | 分类: y | 根因: 1类\n\n"
        "| 编号 | 根因名称 | 英文标识 | 触发特征 |\n| --- | --- | --- | --- |\n| 1 | 原因甲 | A | 特征甲 |\n\n"
        "→ 诊断决策树与详细根因分析详见：reference/missing.md\n",
        encoding="utf-8",
    )
    facts = read_skill_doc(doc)
    assert facts.faults[0].causes[0].trigger == "特征甲"
    assert any("决策树文件不存在" in warning for warning in facts.warnings)


def test_enrichment_extends_the_command_whitelist(facts, context):
    before = len(context.allowed_commands)
    notes = enrich_context(context, facts, facts.match(fault_id="10"))
    assert len(context.allowed_commands) > before
    assert any("命令白名单" in note for note in notes)


def test_enrichment_adds_doc_only_causes_and_keeps_the_output_valid(facts, context):
    facts.faults[0].causes.append(
        type(facts.faults[0].causes[0])(
            name="仅文档记录的根因", trigger="现场特征描述", commands=["display bgp peer"],
            drilldown=["common.md §3.1 物理端口故障"],
        )
    )
    enrich_context(context, facts, facts.match(fault_id="10"))
    text = render_markdown(context)
    assert "仅文档记录的根因" in text
    assert "common.md §3.1 物理端口故障" in text
    assert lint(text, context.allowed_commands) == []


def test_enrichment_recomputes_parameters(facts, context):
    cause_type = type(facts.faults[0].causes[0])
    facts.faults[0].causes.append(
        cause_type(name="带参数的根因", trigger="x", commands=["display bgp peer <peer-ip>"])
    )
    enrich_context(context, facts, facts.match(fault_id="10"))
    assert any(param.cli == "<peer-ip>" for param in context.params)
    assert lint(render_markdown(context), context.allowed_commands) == []


def test_no_match_leaves_the_context_untouched(facts, context):
    before = render_markdown(context)
    assert enrich_context(context, facts, None) == []
    assert render_markdown(context) == before
