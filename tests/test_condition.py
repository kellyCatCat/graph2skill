"""Condition and observation rendering, including the source-side spellings."""

from subkg2skill.condition import format_condition, format_value, observation_expression
from subkg2skill.graph import Node
from tests.conftest import make_node


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


def _observation(name, attrs):
    return Node(make_node("observation_x", "observation", name, attrs=attrs))


def test_observation_prefers_the_normalized_expression():
    observation = _observation("邻居 Init", {"normalized_expression": "状态 == 'Init'"})
    assert observation_expression(observation) == "状态 == 'Init'"


def test_observation_falls_back_to_field_operator_value():
    observation = _observation(
        "收光低",
        {"object_type": "光模块", "field": "收光功率", "operator": "lt", "value": -18, "unit": "dBm"},
    )
    rendered = observation_expression(observation)
    assert "光模块.收光功率" in rendered and "小于" in rendered and "-18" in rendered and "(dBm)" in rendered
