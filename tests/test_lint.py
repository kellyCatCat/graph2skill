"""The linter has to catch the mistakes the template forbids."""

from subkg2skill.lint import lint_path, lint_text

GOOD = """---
name: demo-fault
description: 现象。出现该现象时使用。
---

# 入参列表

| 信息 | 是否必填 | 说明 |
| --- | --- | --- |
| endpoint IPv6 | 是 | 前置检查步骤 1 命令参数 |
| segment list id | 否 | 从前置检查回显中提取 |

# 前置检查

1. **查询状态**
   - CLI 命令：`display policy endpoint <endpoint-ipv6>`
   - 采集内容：`Policy State`

# 排查步骤

## 步骤1：检查是否被 shutdown

1. **步骤名称**：检查是否被 shutdown
2. **CLI 命令**：复用前置检查步骤 1 回显
3. **跳转信息**：
   - `Policy State` 为 `Down (Shutdown)`：定位根因“被 shutdown”，结束排查。
   - 以上判据均不命中：顺序执行步骤 2。
4. **根因定位**：
   - 被 shutdown

## 步骤2：检查 BFD

1. **步骤名称**：检查 BFD
2. **CLI 命令**：`display bfd session srv6-segment-list <segment-list-id>`
3. **跳转信息**：
   - `BFD State` 为 `Down`：定位根因“BFD 检测 Down”，结束排查。
   - 以上判据均不命中：判定“未找到根因”，输出已执行的全部检查步骤及结果摘要，结束排查。
4. **根因定位**：
   - BFD 检测 Down

# 根因对照表

| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |
| --- | --- | --- | --- |
| 被 shutdown | `Policy State` 为 `Down (Shutdown)` | 在该 Policy 视图下执行 `undo shutdown` | - |
| BFD 检测 Down | `BFD State` 为 `Down` | 无直接修复CLI（来源未给出修复命令，只能定位） | - |
| 未找到根因 | 全部步骤走完仍未命中任何故障特征 | 输出已执行的全部检查步骤及结果摘要 | - |
"""


def messages(text):
    return "\n".join(issue.message for issue in lint_text(text).issues)


def test_a_conforming_document_passes():
    result = lint_text(GOOD)
    assert result.ok
    # 「无直接修复CLI」只是提醒，不是错误
    assert [issue.level for issue in result.issues] == ["warning"]


def test_missing_frontmatter():
    assert "frontmatter" in messages(GOOD.split("---\n", 2)[2])


def test_chinese_name_is_rejected():
    assert "英文 slug" in messages(GOOD.replace("name: demo-fault", "name: 邻居震荡"))


def test_missing_section():
    broken = GOOD.replace("# 前置检查", "## 前置检查")
    assert "缺少章节" in messages(broken) or "一级标题必须依次为" in messages(broken)


def test_out_of_order_sections():
    text = GOOD.replace("# 入参列表", "# 排查步骤", 1).replace("# 排查步骤\n\n默认", "# 入参列表\n\n默认", 1)
    assert "一级标题必须依次为" in messages(text)


def test_discontinuous_step_numbers():
    assert "步骤编号必须从 1 起连续" in messages(GOOD.replace("## 步骤2：", "## 步骤3："))


def test_jump_to_a_missing_step():
    assert "跳转到不存在的步骤" in messages(GOOD.replace("顺序执行步骤 2。", "顺序执行步骤 7。"))


def test_precheck_back_reference_is_not_a_jump():
    # 「复用前置检查步骤 1 回显」引用的是采集步骤，不是跳转目标
    assert "跳转到不存在的步骤" not in messages(GOOD)
    text = GOOD.replace(
        "2. **CLI 命令**：复用前置检查步骤 1 回显",
        "2. **CLI 命令**：复用前置检查步骤 9 回显",
    )
    assert "跳转到不存在的步骤" not in messages(text)


def test_last_step_must_handle_no_match():
    text = GOOD.replace(
        "   - 以上判据均不命中：判定“未找到根因”，输出已执行的全部检查步骤及结果摘要，结束排查。\n", ""
    )
    assert "最后一步必须写清" in messages(text)


def test_root_cause_must_appear_verbatim_in_the_table():
    assert "没有在根因对照表里逐字出现" in messages(GOOD.replace("| 被 shutdown |", "| 被关闭 |"))


def test_table_must_carry_the_not_found_row():
    text = GOOD.replace(
        "| 未找到根因 | 全部步骤走完仍未命中任何故障特征 | 输出已执行的全部检查步骤及结果摘要 | - |\n", ""
    )
    assert "缺少「未找到根因」行" in messages(text)


def test_undeclared_parameter_in_a_step():
    assert "未在入参列表声明的参数 <color-id>" in messages(
        GOOD.replace("<segment-list-id>", "<color-id>")
    )


def test_precheck_parameter_must_be_required():
    text = GOOD.replace("| endpoint IPv6 | 是 |", "| endpoint IPv6 | 否 |")
    assert "必须是必填" in messages(text)


def test_precheck_may_not_jump():
    text = GOOD.replace("   - 采集内容：`Policy State`", "   - 采集内容：跳转步骤 2")
    assert "前置检查不允许跳转" in messages(text)


def test_brace_placeholders_are_rejected():
    assert "占位符必须用 <>" in messages(GOOD.replace("<segment-list-id>", "{segment-list-id}"))


def test_abbreviated_interface_name_is_a_warning():
    result = lint_text(GOOD.replace("display bfd session srv6-segment-list <segment-list-id>",
                                    "display interface GE0/1/0 <segment-list-id>"))
    assert any("接口名疑似缩写" in issue.message for issue in result.warnings)


def test_step_missing_a_required_item():
    text = GOOD.replace("4. **根因定位**：\n   - 被 shutdown\n", "")
    assert "缺少「根因定位」" in messages(text)


def test_lint_path_reads_a_directory(tmp_path):
    (tmp_path / "SKILL.md").write_text(GOOD, encoding="utf-8")
    assert lint_path(tmp_path).ok


def test_lint_path_reports_a_missing_file(tmp_path):
    assert "文件不存在" in lint_path(tmp_path / "nope").issues[0].message
