import pytest

from graph2skill.loader import load_graph
from graph2skill.merge import merge_graphs
from graph2skill.stepskill import (
    CAUSE_TABLE_HEADER,
    NO_ROOT_CAUSE,
    command_inventory,
    contexts_for_graph,
    lint,
    normalize_command,
    normalize_param,
    render_markdown,
)


@pytest.fixture()
def bgp_graph(examples_dir):
    merged, _ = merge_graphs(
        [
            load_graph(examples_dir / "skillset" / "graphs" / "bgp.json"),
            load_graph(examples_dir / "incoming" / "bgp-route-flap.json"),
        ]
    )
    return merged


@pytest.fixture()
def flap_context(bgp_graph):
    pairs = contexts_for_graph(bgp_graph, fault_id="11")
    assert pairs, "fault 11 should be extractable"
    return pairs[0][1]


def test_context_only_carries_commands_from_the_graph(bgp_graph, flap_context):
    inventory = {normalize_command(command) for command in command_inventory(bgp_graph)}
    used = [command for item in flap_context.prechecks for command in item.commands]
    used += [command for step in flap_context.steps for command, _ in step.commands]
    assert used
    assert all(normalize_command(command) in inventory for command in used)


def test_context_maps_causes_to_steps_and_rows(flap_context):
    assert [step.cause for step in flap_context.steps] == [
        "承载链路震荡",
        "路由衰减配置不当",
        "LDP会话震荡引发标签重分配",
    ]
    names = [row.name for row in flap_context.causes]
    assert names[-1] == NO_ROOT_CAUSE
    assert names[:-1] == [step.cause for step in flap_context.steps]


def test_fix_column_is_copied_not_invented(flap_context):
    dampening = next(row for row in flap_context.causes if row.name == "路由衰减配置不当")
    assert "`dampening 15 750 2000 60`" in dampening.fix
    link = next(row for row in flap_context.causes if row.name == "承载链路震荡")
    assert link.fix == ""  # no action in the graph -> renderer writes 无直接修复CLI


def test_rendered_draft_passes_its_own_lint(bgp_graph, flap_context):
    text = render_markdown(flap_context)
    assert lint(text, command_inventory(bgp_graph)) == []


def test_rendered_draft_has_the_four_sections_in_order(flap_context):
    text = render_markdown(flap_context)
    positions = [text.index(f"# {title}") for title in ("入参列表", "前置检查", "排查步骤", "根因对照表")]
    assert positions == sorted(positions)
    assert text.startswith("---\nname: bgp-route-flap\n")


def test_last_step_declares_the_no_root_cause_outcome(flap_context):
    text = render_markdown(flap_context)
    last = text.split("## 步骤3")[1]
    assert NO_ROOT_CAUSE in last
    assert "输出已执行的全部检查步骤及结果摘要" in text


def test_cause_table_has_the_required_columns_and_fallback_row(flap_context):
    text = render_markdown(flap_context)
    assert "| " + " | ".join(CAUSE_TABLE_HEADER) + " |" in text
    assert f"| {NO_ROOT_CAUSE} |" in text
    assert "无直接修复CLI，仅能定位" in text


def test_normalizers():
    assert normalize_command("display  bgp peer") == "display bgp peer"
    assert normalize_command("display x <a-b>") == normalize_command("display x <other>")
    assert normalize_param("endpoint IPv6") == normalize_param("<endpoint-ipv6>")


# --- linter rules ---------------------------------------------------------------

GOOD = """---
name: demo-fault
description: 现象。出现现象时使用。
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| 网元ID | 是 | 网元的resId |

# 前置检查

1. **采集**
   - CLI 命令：`display bgp peer`

# 排查步骤

## 步骤1：检查甲

1. **步骤名称**：检查甲
2. **CLI 命令**：`display bgp peer`
3. **跳转信息**：
   - 不满足：判定「未找到根因」，输出已执行的全部检查步骤及结果摘要，结束排查。
4. **根因定位**：
   - 根因甲

# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| 根因甲 | `State` 为 `Down` | 无直接修复CLI，仅能定位 | - |
| 未找到根因 | 全部步骤走完仍未命中 | 输出已执行的全部检查步骤及结果摘要 | - |
"""


def _codes(text, allowed=None):
    return {issue.code for issue in lint(text, allowed)}


def test_reference_document_is_clean():
    assert lint(GOOD, ["display bgp peer"]) == []


def test_invented_command_is_rejected():
    text = GOOD.replace("`display bgp peer`", "`display bgp peer detail`", 1)
    assert "command-not-in-source" in _codes(text, ["display bgp peer"])


def test_missing_section_is_rejected():
    text = GOOD.replace("# 入参列表", "# 参数")
    assert "sections" in _codes(text)


def test_step_numbering_must_be_continuous():
    text = GOOD.replace("## 步骤1：检查甲", "## 步骤2：检查甲")
    assert "step-numbering" in _codes(text)


def test_undeclared_parameter_is_rejected():
    text = GOOD.replace("`display bgp peer`", "`display bgp peer <vpn-name>`")
    assert "param-undeclared" in _codes(text)


def test_inconsistent_parameter_spelling_is_rejected():
    text = GOOD.replace(
        "| 网元ID | 是 | 网元的resId |",
        "| 网元ID | 是 | 网元的resId |\n| vpn name | 是 | 实例名 |",
    ).replace("`display bgp peer`", "`display bgp peer <vpn-name>`", 1)
    text = text.replace("2. **CLI 命令**：`display bgp peer`", "2. **CLI 命令**：`display bgp peer <vpnname>`")
    assert "param-inconsistent" in _codes(text)


def test_optional_parameter_cannot_be_used_by_prechecks():
    text = GOOD.replace(
        "| 网元ID | 是 | 网元的resId |",
        "| 网元ID | 是 | 网元的resId |\n| color | 否 | 来自前置检查步骤1 |",
    ).replace("   - CLI 命令：`display bgp peer`", "   - CLI 命令：`display bgp peer <color>`")
    assert "precheck-param-optional" in _codes(text)


def test_cause_names_must_match_between_steps_and_table():
    text = GOOD.replace("| 根因甲 |", "| 根因乙 |")
    codes = _codes(text)
    assert "cause-not-in-table" in codes
    assert "cause-not-in-steps" in codes


def test_missing_no_root_cause_row_is_rejected():
    text = GOOD.replace("| 未找到根因 | 全部步骤走完仍未命中 | 输出已执行的全部检查步骤及结果摘要 | - |\n", "")
    assert "no-cause-row-missing" in _codes(text)


def test_empty_fix_column_is_rejected():
    text = GOOD.replace("| 根因甲 | `State` 为 `Down` | 无直接修复CLI，仅能定位 | - |",
                        "| 根因甲 | `State` 为 `Down` |  | - |")
    assert "cause-fix-empty" in _codes(text)


def test_jump_to_a_missing_step_is_rejected():
    text = GOOD.replace("- 不满足：判定「未找到根因」，输出已执行的全部检查步骤及结果摘要，结束排查。",
                        "- 不满足：跳转步骤9。\n   - 全不命中：判定「未找到根因」，结束排查。")
    assert "step-jump-unknown" in _codes(text)


def test_bad_front_matter_is_rejected():
    assert "front-matter-missing" in _codes(GOOD.split("---\n", 2)[2])
    assert "name-format" in _codes(GOOD.replace("name: demo-fault", "name: Demo Fault"))


def test_placeholder_style_and_interface_abbreviation_are_flagged():
    text = GOOD.replace("`display bgp peer`", "`display interface GE0/1/0`")
    codes = _codes(text)
    assert "interface-abbreviated" in codes
    text2 = GOOD.replace("网元的resId", "网元的resId，形如 {resId}")
    assert "placeholder-style" in _codes(text2)


def test_fix_commands_may_not_appear_in_step_root_cause():
    text = GOOD.replace("   - 根因甲", "   - 根因甲，执行 `undo shutdown`")
    assert "step-cause-has-command" in _codes(text)
