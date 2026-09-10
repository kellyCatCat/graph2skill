"""Keeping a generated skill about one transferable fault.

The fixture under ``tests/data/messy`` reproduces what a real merged graph does
to the output: one symptom spanning three diagnostic units, three check nodes
running the same command, a case whose commands carry an incident's addresses
and device names, and a cause with neither a criterion nor a fix.
"""

import subprocess
import sys

import pytest

from subkg2skill.cli import main
from subkg2skill.graph import Graph
from subkg2skill.loader import load
from subkg2skill.playbook import build_playbook, entry_scenarios
from subkg2skill.render import BuildOptions, build_package
from subkg2skill.template import BuildPolicy, build_doc
from tests.conftest import ROOT

MESSY = ROOT / "tests" / "data" / "messy"
SYMPTOM = "symptom_isis"


@pytest.fixture()
def messy_graph():
    bundle, _ = load([str(MESSY)])
    graph, report = Graph.from_bundle(bundle)
    assert not report.errors
    return graph


def doc_for(graph, unit="28.21.3", policy=None):
    scoped = graph.scope_to_unit(unit) if unit else graph
    return build_doc(scoped, build_playbook(scoped, scoped.nodes[SYMPTOM]), policy)


# -- 场景切分 ------------------------------------------------------------
def test_one_symptom_splits_into_scenarios(messy_graph):
    scenarios = entry_scenarios(messy_graph)
    assert {s.unit for s in scenarios} == {"28.21.3", "4.3.3", "case:loop-001"}
    biggest = scenarios[0]
    assert biggest.unit == "28.21.3" and biggest.causes == 3


def test_scoping_keeps_only_one_scenarios_relations(messy_graph):
    scoped = messy_graph.scope_to_unit("28.21.3")
    causes = [node.name for node, _e in scoped.targets(SYMPTOM, "has_cause")]
    assert "MED值配置不当导致路由优选错误" not in causes
    assert "ISIS路由震荡" not in causes


def test_unit_matching_handles_both_id_shapes():
    assert Graph.unit_matches("28.21.3", "28.21")
    assert Graph.unit_matches("case:loop-001", "case")
    assert not Graph.unit_matches("28.21.3", "28.2")


def test_build_refuses_to_merge_scenarios_silently(tmp_path, capsys):
    code = main(
        ["build", str(MESSY), "--entry", SYMPTOM, "--name", "isis", "--out", str(tmp_path / "s")]
    )
    assert code == 2
    error = capsys.readouterr().err
    assert "3 个诊断单元" in error and "--unit" in error


def test_all_units_is_an_explicit_opt_in(tmp_path):
    code = main(
        ["build", str(MESSY), "--entry", SYMPTOM, "--all-units", "--name", "isis",
         "--out", str(tmp_path / "s")]
    )
    assert code == 0


def test_build_all_produces_one_skill_per_scenario(tmp_path):
    out = tmp_path / "all"
    assert main(["build-all", str(MESSY), "--out", str(out)]) == 0
    assert len(list(out.iterdir())) == 3


# -- 命令去重 ------------------------------------------------------------
def test_checks_sharing_a_command_collapse_into_one_precheck(messy_graph):
    doc = doc_for(messy_graph)
    commands = [command for precheck in doc.prechecks for command in precheck.commands]
    assert commands.count("display isis peer") == 1


def test_the_merged_precheck_keeps_every_intent(messy_graph):
    doc = doc_for(messy_graph)
    peer = next(p for p in doc.prechecks if "display isis peer" in p.commands)
    for intent in ("确认邻居状态", "判断邻居是否Up", "复核邻居状态"):
        assert intent in peer.collect
    assert len(peer.node_ids) == 3


def test_steps_cite_the_collection_step_instead_of_rerunning_it(messy_graph):
    doc = doc_for(messy_graph)
    assert all(not step.commands for step in doc.steps)
    assert all("复用前置检查步骤" in step.reuse_note for step in doc.steps)


def test_precheck_numbers_stay_valid_after_pruning(messy_graph):
    doc = doc_for(messy_graph)
    cited = {int(part.split()[0]) for step in doc.steps
             for part in [step.reuse_note.split("复用前置检查步骤 ")[1]]}
    assert cited <= set(range(1, len(doc.prechecks) + 1))
    assert "?" not in " ".join(step.reuse_note for step in doc.steps)


# -- 案例特定内容 --------------------------------------------------------
def test_case_specific_material_is_left_out_by_default(messy_graph):
    doc = doc_for(messy_graph, unit="case:loop-001")
    rendered = " ".join(
        [p.title + " ".join(p.commands) for p in doc.prechecks]
        + [s.name + " ".join(s.commands) for s in doc.steps]
    )
    assert "10.10.10.10" not in rendered and "DeviceB" not in rendered
    assert any("example_specific" in reason for _name, reason in doc.omitted)


def test_case_specific_material_can_be_opted_back_in(messy_graph):
    doc = doc_for(messy_graph, unit="case:loop-001", policy=BuildPolicy(include_example_specific=True))
    commands = [command for precheck in doc.prechecks for command in precheck.commands]
    assert any("10.10.10.10" in command for command in commands)


def test_kept_case_literals_are_flagged_in_the_document(messy_graph):
    doc = doc_for(messy_graph, unit="case:loop-001", policy=BuildPolicy(include_example_specific=True))
    literals = [literal for precheck in doc.prechecks for literal in precheck.literals]
    assert any("IP 地址 10.10.10.10" in literal for literal in literals)


# -- 规模控制 ------------------------------------------------------------
def test_a_cause_with_no_criterion_and_no_fix_is_omitted(messy_graph):
    doc = doc_for(messy_graph)
    assert "疑似底层故障" not in [step.name.replace("检查", "") for step in doc.steps]
    assert ("疑似底层故障", "既无判定观测也无修复动作") in doc.omitted


def test_max_steps_caps_and_records_what_it_dropped(messy_graph):
    doc = doc_for(messy_graph, policy=BuildPolicy(max_steps=1))
    assert len(doc.steps) == 1
    assert any("max-steps" in reason for _name, reason in doc.omitted)


# -- 表格安全 ------------------------------------------------------------
def test_prose_fixes_do_not_break_the_table(messy_graph):
    doc = doc_for(messy_graph)
    level = next(cause for cause in doc.root_causes if cause.name == "Level不匹配")
    assert "\n" not in level.fix and "<br>" in level.fix
    assert "\\|" in level.fix  # 竖线被转义，不会切出新的一列


def test_generated_document_has_no_case_literal_warnings(messy_graph, tmp_path):
    from subkg2skill.lint import lint_text

    scoped = messy_graph.scope_to_unit("28.21.3")
    package = build_package(
        scoped, build_playbook(scoped, scoped.nodes[SYMPTOM]), BuildOptions(name="isis")
    )
    result = lint_text(package.files["SKILL.md"])
    assert result.ok
    assert not [issue for issue in result.warnings if "案例字面量" in issue.message]


def test_evidence_file_explains_every_omission(messy_graph):
    scoped = messy_graph.scope_to_unit("28.21.3")
    package = build_package(
        scoped, build_playbook(scoped, scoped.nodes[SYMPTOM]), BuildOptions(name="isis")
    )
    evidence = package.files["reference/evidence.md"]
    assert "未进入正文的条目" in evidence and "疑似底层故障" in evidence


def test_lint_flags_an_oversized_hand_written_document(tmp_path):
    from subkg2skill.lint import lint_text

    steps = "\n".join(
        f"## 步骤{i}：检查{i}\n\n1. **步骤名称**：检查{i}\n2. **CLI 命令**：`display x`\n"
        f"3. **跳转信息**：\n   - `f` 为 `v`：定位根因“根因{i}”，结束排查。\n"
        f"   - 以上判据均不命中：顺序执行步骤 {i + 1}。\n4. **根因定位**：\n   - 根因{i}\n"
        for i in range(1, 31)
    )
    table = "\n".join(f"| 根因{i} | `f` 为 `v` | 无直接修复CLI | - |" for i in range(1, 31))
    document = (
        "---\nname: big\ndescription: 大文档。出现时使用。\n---\n\n"
        "# 入参列表\n\n| 信息 | 是否必填 | 说明 |\n| --- | --- | --- |\n\n"
        "# 前置检查\n\n1. **采集**\n   - CLI 命令：`display x`\n   - 采集内容：`f`\n\n"
        f"# 排查步骤\n\n{steps}\n"
        "# 根因对照表\n\n| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |\n| --- | --- | --- | --- |\n"
        f"{table}\n| 未找到根因 | 全部步骤走完仍未命中任何故障特征 | 输出摘要 | - |\n"
    )
    warnings = " ".join(issue.message for issue in lint_text(document).warnings)
    assert "超出可读范围" in warnings and "按诊断单元拆分" in warnings


def test_lint_flags_a_repeated_collection_command():
    from subkg2skill.lint import lint_text

    document = (
        "---\nname: dup\ndescription: 重复。出现时使用。\n---\n\n"
        "# 入参列表\n\n| 信息 | 是否必填 | 说明 |\n| --- | --- | --- |\n\n"
        "# 前置检查\n\n"
        + "".join(f"{i}. **检查{i}**\n   - CLI 命令：`display isis peer`\n   - 采集内容：`状态`\n\n" for i in range(1, 5))
        + "# 排查步骤\n\n## 步骤1：检查A\n\n1. **步骤名称**：检查A\n2. **CLI 命令**：复用前置检查步骤 1 回显\n"
        "3. **跳转信息**：\n   - `状态` 为 `Down`：定位根因“根因A”，结束排查。\n"
        "   - 以上判据均不命中：判定“未找到根因”，输出摘要，结束排查。\n4. **根因定位**：\n   - 根因A\n\n"
        "# 根因对照表\n\n| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |\n| --- | --- | --- | --- |\n"
        "| 根因A | `状态` 为 `Down` | 无直接修复CLI | - |\n"
        "| 未找到根因 | 全部步骤走完仍未命中任何故障特征 | 输出摘要 | - |\n"
    )
    warnings = " ".join(issue.message for issue in lint_text(document).warnings)
    assert "重复了 4 次" in warnings and "合并" in warnings


def test_the_documented_scenario_workflow_runs(tmp_path):
    entry = ROOT / "subkg-to-skill" / "scripts" / "build_skill.py"
    listing = subprocess.run(
        [sys.executable, str(entry), "list", str(MESSY)], capture_output=True, text=True
    )
    assert listing.returncode == 0 and "--unit 28.21.3" in listing.stdout
    built = subprocess.run(
        [sys.executable, str(entry), "build", str(MESSY), "--entry", SYMPTOM,
         "--unit", "28.21.3", "--name", "isis-neighbor", "--out", str(tmp_path / "s")],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    assert "模板检查：通过" in built.stdout
