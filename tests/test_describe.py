"""Attribute rendering: nothing invented, nothing silently dropped."""

from subkg2skill import describe
from subkg2skill.graph import Node
from tests.conftest import make_node


def node(node_type, name, attrs, **overrides):
    return Node(make_node(f"{node_type}_x", node_type, name, attrs=attrs, **overrides))


def test_scope_line_names_what_is_unconstrained():
    line = describe.scope_line(
        {"vendor": "Huawei", "product_family": None, "software_version": None,
         "component_type": None, "scenario": None, "scope_basis": "document_product"}
    )
    assert "厂商=Huawei" in line and "版本未限定" in line and "来源文档覆盖的产品" in line


def test_scope_line_handles_missing_scope():
    assert describe.scope_line({}) == "适用范围未标注"


def test_observation_prefers_the_normalized_expression():
    observation = node("observation", "邻居 Init", {"normalized_expression": "状态 == 'Init'"})
    assert describe.observation_expression(observation) == "状态 == 'Init'"


def test_observation_falls_back_to_field_operator_value():
    observation = node(
        "observation",
        "收光低",
        {"object_type": "光模块", "field": "收光功率", "operator": "lt", "value": -18, "unit": "dBm"},
    )
    rendered = describe.observation_expression(observation)
    assert "光模块.收光功率" in rendered and "小于" in rendered and "-18" in rendered and "(dBm)" in rendered


def test_observation_details_surface_predicates_and_sampling():
    observation = node(
        "observation",
        "抖动",
        {
            "aggregation": "all",
            "window_seconds": 60,
            "sample_count": 3,
            "knowledge_predicate": {"type": "text", "expression": "需人工解释"},
            "source_operator": "!=",
            "knowledge_mode": "possible_check_result",
        },
    )
    details = "\n".join(describe.observation_details(observation))
    assert "窗口=60s" in details or "时间窗口=60s" in details
    assert "需人工解释" in details and "!=" in details and "不代表现场已经出现" in details


def test_check_block_marks_templates_as_unbound():
    check = node(
        "check",
        "查看接口",
        {
            "check_kind": "cli",
            "command_templates": ["display interface {if}"],
            "parameters": ["if"],
            "procedure": ["登录设备", "执行命令"],
            "execution_policy": "knowledge_template_requires_context_binding",
        },
    )
    block = "\n".join(describe.check_block(check))
    assert "参数需绑定" in block and "display interface {if}" in block and "`if`" in block
    assert "模板需绑定现场上下文后使用" in block


def test_repair_block_says_when_the_source_gave_nothing():
    repair = node(
        "repair",
        "换板",
        {"repair_kind": "replace", "service_impact": None, "rollback": None, "expected_effect": None,
         "preconditions": []},
    )
    block = "\n".join(describe.repair_block(repair))
    assert "空值不表示无影响" in block
    assert "空数组不证明无需前置条件" in block


def test_escalation_block_lists_handoff_material():
    escalation = node(
        "escalation",
        "转交",
        {"destination_role": "技术支持", "collection_requirements": ["日志", "配置"],
         "handoff_template": "模板", "instructions": "说明"},
    )
    block = "\n".join(describe.escalation_block(escalation))
    assert "技术支持" in block and "日志" in block and "模板" in block and "说明" in block


def test_evidence_line_locates_pdf_and_excel_sources():
    pdf = describe.evidence_line(
        {"document": "手册.pdf", "document_version": "07", "page": 64, "printed_page": 24,
         "section": "4.3.3", "block_id": "p0064_b010", "quote": "引文", "source_kind": "pdf_text"}
    )
    assert "《手册.pdf》" in pdf and "物理页 64" in pdf and "印刷页 24" in pdf and "引文" in pdf
    excel = describe.evidence_line({"document": "作战树.xlsx", "sheet": "Sheet1", "cell": "D5", "quote": "格子"})
    assert "Sheet1 D5" in excel


def test_evidence_lines_report_what_was_not_shown():
    items = [{"document": f"d{i}.pdf", "quote": "q"} for i in range(5)]
    lines = describe.evidence_lines(items, limit=2)
    assert len(lines) == 3 and "另有 3 条" in lines[-1]


def test_trust_line_never_claims_human_review():
    symptom = node("symptom", "现象", {"example_specific": True})
    line = describe.trust_line(symptom)
    assert "人工复核=否" in line and "案例特定" in line


def test_flag_lines_explain_prefixed_flags():
    lines = describe.flag_lines(["unmapped_cause_kind:license", "ocr_only"])
    assert "license" in lines[0] and "OCR" in lines[1]


def test_node_headline_carries_role_and_id():
    assert "故障原因" in describe.node_headline(node("cause", "原因", {}))
