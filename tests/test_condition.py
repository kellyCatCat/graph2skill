"""Condition rendering, including the source-side spellings."""

from subkg2skill.condition import (
    condition_is_binding,
    describe_edge_condition,
    format_condition,
    format_value,
)
from subkg2skill.graph import Edge


def test_atomic_condition_reads_as_a_sentence():
    rendered = format_condition(
        {"type": "atomic", "object": "设备", "field": "资源状态", "operator": "eq", "value": "不足"}
    )
    assert rendered == "设备.资源状态 等于 不足"


def test_exists_operator_drops_the_value():
    rendered = format_condition({"type": "atomic", "field": "日志", "operator": "exists", "value": None})
    assert rendered == "日志 存在"


def test_and_or_not_nest():
    rendered = format_condition(
        {
            "type": "or",
            "conditions": [
                {"type": "atomic", "field": "A", "operator": "eq", "value": 1},
                {"type": "not", "condition": {"type": "text", "expression": "已倒换"}},
            ],
        }
    )
    assert rendered == "（A 等于 1 或 非（文本「已倒换」））"


def test_top_level_text_is_not_double_labelled():
    assert format_condition({"type": "text", "expression": "邻居类型为 Level-1"}) == "邻居类型为 Level-1"


def test_single_child_group_is_not_wrapped():
    rendered = format_condition(
        {"type": "and", "conditions": [{"type": "atomic", "field": "A", "operator": "eq", "value": 1}]}
    )
    assert rendered == "A 等于 1"


def test_sampling_constraints_are_kept():
    rendered = format_condition(
        {
            "type": "atomic",
            "field": "丢包率",
            "operator": "gt",
            "value": 1,
            "unit": "%",
            "aggregation": "all",
            "window_seconds": 60,
        }
    )
    assert "(%)" in rendered and "聚合=all" in rendered and "窗口=60s" in rendered


def test_unknown_operator_is_flagged_not_dropped():
    rendered = format_condition({"type": "atomic", "field": "X", "operator": "≈", "value": 1})
    assert "≈" in rendered and "未知操作符" in rendered


def test_source_side_spellings_survive():
    rendered = format_condition(
        {
            "type": "atomic",
            "var": "optical_module_certification",
            "comparison": ">",
            "value": 0,
            "other": "path_cost_via_CSG=2000",
            "negate": True,
        }
    )
    assert rendered.startswith("非（")
    assert "来源写法" in rendered and "path_cost_via_CSG=2000" in rendered


def test_deep_nesting_is_bounded():
    condition = {"type": "text", "expression": "底"}
    for _ in range(30):
        condition = {"type": "not", "condition": condition}
    assert "嵌套过深" in format_condition(condition)


def test_format_value_keeps_types_distinct():
    assert format_value(True) == "true"
    assert format_value(None) == "(未给出)"
    assert format_value([1, "a"]) == "[1, a]"


def test_edge_condition_states_it_is_unevaluated():
    edge = Edge(
        {
            "edge_id": "edge_1",
            "edge_type": "supports",
            "source": "observation_a",
            "target": "cause_b",
            "condition": {"type": "text", "expression": "对端未收到 Hello"},
            "condition_status": "text_only",
        }
    )
    rendered = describe_edge_condition(edge)
    assert "未求值" in rendered and "对端未收到 Hello" in rendered
    assert condition_is_binding(edge)


def test_unconditional_edge_has_no_condition_line():
    edge = Edge({"edge_type": "supports", "condition": None, "condition_status": "unconditional"})
    assert describe_edge_condition(edge) == ""
    assert not condition_is_binding(edge)


def test_original_condition_is_shown_when_condition_is_empty():
    edge = Edge(
        {
            "edge_type": "supports",
            "condition": None,
            "condition_status": "preserved_unparsed",
            "original_condition": {"type": "atomic", "expression": "原文条件"},
        }
    )
    rendered = describe_edge_condition(edge)
    assert "原文条件" in rendered and "未规范化" in rendered
