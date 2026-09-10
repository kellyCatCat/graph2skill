"""Merging what several sources say about the same fault.

``tests/data/multisource`` holds one fault written up three times — a manual
section, a battle-tree row and a case entry — with a cause (MTU) that appears in
two of them as separate nodes.
"""

import pytest

from subkg2skill.cli import main
from subkg2skill.graph import Graph
from subkg2skill.loader import load
from subkg2skill.playbook import (
    build_merged_playbook,
    build_playbook,
    fault_groups,
    fault_key,
)
from subkg2skill.render import BuildOptions, build_package, default_description
from subkg2skill.template import build_doc
from tests.conftest import ROOT

MULTI = ROOT / "tests" / "data" / "multisource"
MESSY = ROOT / "tests" / "data" / "messy"


def graph_of(path):
    bundle, _ = load([str(path)])
    graph, report = Graph.from_bundle(bundle)
    assert not report.errors
    return graph


@pytest.fixture()
def multi_graph():
    return graph_of(MULTI)


@pytest.fixture()
def group(multi_graph):
    """The three-source 邻居无法建立 fault (the fixture also holds an overlapping one)."""
    groups = fault_groups(multi_graph)
    return next(group for group in groups if group.sources == 3)


@pytest.fixture()
def merged(multi_graph, group):
    scoped = multi_graph.scope_to_units(group.units)
    return build_merged_playbook(scoped, group.symptoms, group.units)


# -- 故障身份 ------------------------------------------------------------
def test_spelling_differences_are_one_fault():
    assert fault_key("IS-IS邻居无法建立") == fault_key("ISIS邻居无法建立")
    assert fault_key("IS-IS 邻居无法建立") == fault_key("isis邻居无法建立")


def test_different_faults_stay_apart():
    assert fault_key("IS-IS邻居无法建立") != fault_key("IS-IS邻居震荡")


def test_three_sources_become_one_group(group):
    assert group.sources == 3
    assert set(group.units) == {"17.4.1", "ipran_battle_tree:s0:r159", "ipran_icase"}


def test_differently_named_faults_are_not_merged_automatically(multi_graph):
    # 「协议邻居关系无法建立」和「IS-IS邻居无法建立」根因高度重叠，但名字不同：
    # 只作为建议，不自动合并
    names = {group.name for group in fault_groups(multi_graph)}
    assert names == {"IS-IS邻居无法建立", "协议邻居关系无法建立"}


def test_units_of_one_node_are_not_merged_back():
    # 同一个症状节点跨三个单元，是三个不同故障，不能因为同名就并回去
    groups = fault_groups(graph_of(MESSY))
    assert len(groups) == 3
    assert all(len(group.symptoms) == 1 for group in groups)


def test_no_merge_lists_every_scenario(multi_graph):
    assert len(fault_groups(multi_graph, merge=False)) == 4
    assert len(fault_groups(multi_graph)) == 2


# -- 合并后的 playbook ---------------------------------------------------
def test_causes_are_folded_by_name(merged):
    names = [branch.cause.name for branch in merged.causes]
    assert names.count("两端接口MTU不一致") == 1
    assert set(names) == {"两端接口MTU不一致", "Level不匹配", "System ID冲突", "两端认证方式不匹配"}


def test_a_folded_cause_keeps_every_sources_evidence(merged):
    mtu = next(b for b in merged.causes if b.cause.name == "两端接口MTU不一致")
    assert {v.kind for v in mtu.verdicts} == {"confirms", "supports"}
    # 修复来自手册，判据来自作战树——两边都要在
    assert [link.node.name for link in mtu.repairs] == ["统一两端MTU"]
    assert any(step.check.name == "比对两端接口MTU" for step in mtu.checks)


def test_trigger_terms_span_all_sources(merged):
    terms = merged.trigger_terms()
    assert "IS-IS邻居无法建立" in terms and "ISIS邻居无法建立" in terms
    assert "ISIS邻居Down" in terms and "邻居无法Up" in terms


def test_required_slots_are_the_union(merged):
    assert set(merged.required_slots()) == {"neName", "interfaceName", "peerName"}


def test_unmerged_playbook_sees_only_its_own_source(multi_graph):
    single = build_playbook(multi_graph, multi_graph.nodes["symptom_manual"])
    assert [b.cause.name for b in single.causes] == ["两端接口MTU不一致", "Level不匹配"]


# -- 合并后的文档 --------------------------------------------------------
def test_document_has_one_step_per_folded_cause(multi_graph, merged):
    doc = build_doc(multi_graph.scope_to_units(merged.units), merged)
    assert len(doc.steps) == 4
    assert len({step.name for step in doc.steps}) == 4


def test_the_folded_step_lists_both_criteria(multi_graph, merged):
    doc = build_doc(multi_graph.scope_to_units(merged.units), merged)
    mtu = next(step for step in doc.steps if "MTU" in step.name)
    criteria = " ".join(branch.criterion for branch in mtu.branches)
    assert "本端 MTU != 对端 MTU" in criteria and "邻居状态 == 'Init'" in criteria


def test_the_repair_survives_the_fold(multi_graph, merged):
    """The verdict points at one source's cause node, the repair hangs off another."""
    doc = build_doc(multi_graph.scope_to_units(merged.units), merged)
    mtu = next(cause for cause in doc.root_causes if cause.name == "两端接口MTU不一致")
    assert "`mtu <mtu-value>`" in mtu.fix
    assert "无直接修复CLI" not in mtu.fix
    assert "接口重启会中断业务" in mtu.fix


def test_description_carries_every_sources_wording(multi_graph, merged):
    description = default_description(merged.symptom, merged)
    assert "ISIS邻居Down" in description or "邻居无法Up" in description


def test_merged_document_passes_the_linter(multi_graph, merged):
    from subkg2skill.lint import lint_text

    scoped = multi_graph.scope_to_units(merged.units)
    package = build_package(scoped, merged, BuildOptions(name="isis-neighbor-down"))
    result = lint_text(package.files["SKILL.md"])
    assert result.ok, [issue.render() for issue in result.errors]


def test_evidence_names_every_merged_source(multi_graph, merged):
    scoped = multi_graph.scope_to_units(merged.units)
    package = build_package(scoped, merged, BuildOptions(name="isis"))
    evidence = package.files["reference/evidence.md"]
    assert "合并了 3 个来源" in evidence
    for node_id in ("symptom_manual", "symptom_tree", "symptom_case"):
        assert node_id in evidence


# -- CLI -----------------------------------------------------------------
def test_merge_same_name_pulls_in_the_other_sources(tmp_path, capsys):
    out = tmp_path / "skill"
    code = main(
        ["build", str(MULTI), "--entry", "symptom_manual", "--merge-same-name",
         "--name", "isis-neighbor-down", "--out", str(out)]
    )
    assert code == 0
    assert "合并来源：" in capsys.readouterr().out
    text = (out / "SKILL.md").read_text(encoding="utf-8")
    assert text.count("## 步骤") == 4


def test_building_one_source_alone_says_what_it_is_missing(tmp_path, capsys):
    out = tmp_path / "skill"
    code = main(
        ["build", str(MULTI), "--entry", "symptom_manual", "--name", "isis", "--out", str(out)]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "同名症状" in output and "--merge-same-name" in output
    assert (out / "SKILL.md").read_text(encoding="utf-8").count("## 步骤") == 2


def test_entry_can_be_repeated_for_unrelated_names(tmp_path):
    out = tmp_path / "skill"
    code = main(
        ["build", str(MULTI), "--entry", "symptom_manual", "--entry", "symptom_tree",
         "--name", "isis", "--out", str(out)]
    )
    assert code == 0
    assert (out / "SKILL.md").read_text(encoding="utf-8").count("## 步骤") == 3


def test_build_all_emits_one_skill_for_the_merged_fault(tmp_path):
    out = tmp_path / "all"
    assert main(["build-all", str(MULTI), "--out", str(out)]) == 0
    # 三来源的那个故障合成一份，另一个重叠但不同名的故障单独一份
    assert len(list(out.iterdir())) == 2


def test_build_all_no_merge_emits_one_per_source(tmp_path):
    out = tmp_path / "all"
    assert main(["build-all", str(MULTI), "--out", str(out), "--no-merge"]) == 0
    assert len(list(out.iterdir())) == 4


def test_list_shows_the_merge_and_its_build_command(capsys):
    assert main(["list", str(MULTI)]) == 0
    output = capsys.readouterr().out
    assert "合并来源 : 3 个" in output
    assert "--entry symptom_manual --entry symptom_tree --entry symptom_case" in output


# -- 合并建议（按根因/命令重叠发现，不自动合并） --------------------------
def test_overlapping_faults_are_suggested_not_merged(multi_graph):
    from subkg2skill.playbook import suggest_merges

    groups = fault_groups(multi_graph)
    suggestions = suggest_merges(multi_graph, groups)
    assert len(suggestions) == 1
    suggestion = suggestions[0]
    assert {suggestion.left.name, suggestion.right.name} == {
        "IS-IS邻居无法建立",
        "协议邻居关系无法建立",
    }
    assert len(suggestion.shared_causes) == 3
    assert suggestion.overlap >= 0.5
    assert "display isis peer" in suggestion.shared_commands
    # 建议里带上双方全部来源，可以直接执行
    assert len(suggestion.entries) == 4


def test_unrelated_faults_are_not_suggested():
    from subkg2skill.playbook import suggest_merges

    graph = graph_of(MESSY)
    assert suggest_merges(graph, fault_groups(graph)) == []


def test_a_single_shared_cause_is_not_enough(multi_graph):
    from subkg2skill.playbook import suggest_merges

    groups = fault_groups(multi_graph)
    assert suggest_merges(multi_graph, groups, min_shared_causes=4) == []


def test_list_reports_suggestions_with_a_runnable_command(capsys):
    assert main(["list", str(MULTI), "--suggest-merge"]) == 0
    output = capsys.readouterr().out
    assert "根因大量重叠" in output and "要不要合并由你判断" in output
    assert "--entry symptom_generic" in output


def test_lint_flags_near_duplicate_root_causes():
    from subkg2skill.lint import lint_text

    document = (
        "---\nname: dup\ndescription: 现象。出现时使用。\n---\n\n"
        "# 入参列表\n\n| 信息 | 是否必填 | 说明 |\n| --- | --- | --- |\n\n"
        "# 前置检查\n\n1. **采集**\n   - CLI 命令：`display isis peer`\n   - 采集内容：`状态`\n\n"
        "# 排查步骤\n\n## 步骤1：检查MTU\n\n1. **步骤名称**：检查MTU\n"
        "2. **CLI 命令**：复用前置检查步骤 1 回显\n3. **跳转信息**：\n"
        "   - `MTU` 不一致：定位根因“两端接口MTU不一致”，结束排查。\n"
        "   - 以上判据均不命中：判定“未找到根因”，输出摘要，结束排查。\n"
        "4. **根因定位**：\n   - 两端接口MTU不一致\n\n"
        "# 根因对照表\n\n| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |\n| --- | --- | --- | --- |\n"
        "| 两端接口MTU不一致 | `MTU` 不一致 | 无直接修复CLI | - |\n"
        "| 两端MTU不一致 | `MTU` 不一致 | 无直接修复CLI | - |\n"
        "| 未找到根因 | 全部步骤走完仍未命中任何故障特征 | 输出摘要 | - |\n"
    )
    result = lint_text(document)
    assert result.ok  # 只是提醒，不是错误
    warnings = " ".join(issue.message for issue in result.warnings)
    assert "写法高度相似" in warnings and "修复动作不同就别合" in warnings


def test_lint_does_not_flag_genuinely_different_causes():
    from subkg2skill.lint import lint_text

    document = (
        "---\nname: ok\ndescription: 现象。出现时使用。\n---\n\n"
        "# 入参列表\n\n| 信息 | 是否必填 | 说明 |\n| --- | --- | --- |\n\n"
        "# 前置检查\n\n1. **采集**\n   - CLI 命令：`display isis peer`\n   - 采集内容：`状态`\n\n"
        "# 排查步骤\n\n## 步骤1：检查A\n\n1. **步骤名称**：检查A\n"
        "2. **CLI 命令**：复用前置检查步骤 1 回显\n3. **跳转信息**：\n"
        "   - `状态` 为 `Down`：定位根因“两端接口MTU不一致”，结束排查。\n"
        "   - 以上判据均不命中：判定“未找到根因”，输出摘要，结束排查。\n"
        "4. **根因定位**：\n   - 两端接口MTU不一致\n\n"
        "# 根因对照表\n\n| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |\n| --- | --- | --- | --- |\n"
        "| 两端接口MTU不一致 | `状态` 为 `Down` | 无直接修复CLI | - |\n"
        "| System ID冲突 | `状态` 为 `Down` | 无直接修复CLI | - |\n"
        "| 未找到根因 | 全部步骤走完仍未命中任何故障特征 | 输出摘要 | - |\n"
    )
    warnings = " ".join(issue.message for issue in lint_text(document).warnings)
    assert "写法高度相似" not in warnings


def test_list_show_causes_breaks_a_group_down_by_source(capsys):
    assert main(["list", str(MULTI), "--show-causes"]) == 0
    output = capsys.readouterr().out
    assert "根因构成 :" in output
    # 每个来源各自贡献了哪些根因，才看得出一个组是不是混了两类故障
    assert "[17.4.1] IS-IS邻居无法建立" in output
    assert "[ipran_icase] ISIS邻居无法建立" in output
    assert "- 两端认证方式不匹配" in output
