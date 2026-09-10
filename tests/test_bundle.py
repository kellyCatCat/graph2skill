"""One skill covering several faults: shared prechecks, routing table, scenarios."""

import json

import pytest

from subkg2skill.cli import main
from subkg2skill.graph import Graph
from subkg2skill.lint import lint_text
from subkg2skill.loader import load
from subkg2skill.playbook import build_merged_playbook, fault_groups
from subkg2skill.render import BuildOptions, build_package
from subkg2skill.template import build_multi_doc, render_doc, scenario_label
from tests.conftest import ROOT

MULTI = ROOT / "tests" / "data" / "multisource"


@pytest.fixture()
def scenarios():
    bundle, _ = load([str(MULTI)])
    graph, report = Graph.from_bundle(bundle)
    assert not report.errors
    groups = fault_groups(graph)
    assert len(groups) == 2
    units = [unit for group in groups for unit in group.units]
    scoped = graph.scope_to_units(units)
    named = [
        (group.name, build_merged_playbook(scoped.scope_to_units(group.units), group.symptoms, group.units))
        for group in groups
    ]
    return scoped, named


@pytest.fixture()
def doc(scenarios):
    graph, named = scenarios
    return build_multi_doc(graph, named)


@pytest.fixture()
def text(doc):
    return render_doc(doc, name="isis-troubleshooting", description="IS-IS 排障。出现邻居异常时使用。")


def test_labels_run_a_b_c():
    assert [scenario_label(i) for i in range(3)] == ["A", "B", "C"]


def test_prechecks_are_shared_across_scenarios(doc):
    commands = [command for precheck in doc.prechecks for command in precheck.commands]
    # 两个场景都用 display isis peer，只应出现一次
    assert commands.count("display isis peer") == 1


def test_each_scenario_numbers_its_steps_from_one(doc):
    assert len(doc.scenarios) == 2
    for scenario in doc.scenarios:
        assert [step.index for step in scenario.steps] == list(
            range(1, len(scenario.steps) + 1)
        )


def test_routing_table_points_at_every_scenario(doc, text):
    assert "## 场景跳转表" in text
    for scenario in doc.scenarios:
        assert f"→ **{scenario.title}**" in text


def test_routing_rows_cite_a_real_precheck(doc):
    for scenario in doc.scenarios:
        for row in scenario.routing:
            assert "@@" not in row.precheck
            if row.precheck.startswith("步骤"):
                number = int(row.precheck.split("步骤 ")[1].split("（")[0])
                assert 1 <= number <= len(doc.prechecks)


def test_scenario_headings_and_step_levels(text):
    assert "### 场景A：" in text and "### 场景B：" in text
    assert "#### 步骤1：" in text
    assert "\n## 步骤" not in text  # 分场景时步骤是四级标题


def test_root_cause_tables_are_split_by_scenario(text):
    table = text.split("# 根因对照表")[1]
    assert table.count("### 场景") == 2
    assert table.count("| 未找到根因 |") == 2


def test_the_document_passes_the_linter(text):
    result = lint_text(text)
    assert result.ok, [issue.render() for issue in result.errors]


def test_lint_requires_a_routing_table_when_scenarios_exist(text):
    without = text.replace("## 场景跳转表", "## 其他说明")
    messages = " ".join(issue.message for issue in lint_text(without).issues)
    assert "必须有「场景跳转表」" in messages


def test_lint_catches_a_routing_row_pointing_nowhere(text):
    broken = text.replace("→ **场景B：", "→ **场景Z：")
    messages = " ".join(issue.message for issue in lint_text(broken).issues)
    assert "指向了不存在的场景" in messages or "没有出现在场景跳转表里" in messages


def test_lint_catches_wrong_step_heading_level(text):
    broken = text.replace("#### 步骤1：", "## 步骤1：", 1)
    messages = " ".join(issue.message for issue in lint_text(broken).issues)
    assert "步骤标题应为四级" in messages


def test_lint_numbers_steps_per_scenario(text):
    # 第二个场景的步骤如果接着上一个场景编号，应报错
    broken = text.replace("#### 步骤1：检查两端接口MTU不一致", "#### 步骤4：检查两端接口MTU不一致")
    messages = " ".join(issue.message for issue in lint_text(broken).issues)
    assert "步骤编号必须从 1 起连续" in messages


def test_single_scenario_output_is_unchanged(scenarios):
    graph, named = scenarios
    single = build_multi_doc(graph, named[:1])
    text = render_doc(single, name="isis", description="现象。出现时使用。")
    assert "## 场景跳转表" not in text
    assert "### 场景A：" not in text
    assert "## 步骤1：" in text
    assert lint_text(text).ok


def test_package_description_covers_every_scenario(scenarios):
    graph, named = scenarios
    package = build_package(graph, named, BuildOptions(name="isis-troubleshooting"))
    description = [
        line for line in package.files["SKILL.md"].splitlines() if line.startswith("description:")
    ][0]
    assert "2 个故障场景" in description
    assert "场景跳转表" in description


def test_evidence_lists_every_scenarios_sources(scenarios):
    graph, named = scenarios
    package = build_package(graph, named, BuildOptions(name="isis-troubleshooting"))
    evidence = package.files["reference/evidence.md"]
    assert "覆盖 2 个故障场景" in evidence
    for node_id in ("symptom_manual", "symptom_tree", "symptom_case", "symptom_generic"):
        assert node_id in evidence


# -- CLI -----------------------------------------------------------------
def test_export_then_build(tmp_path, capsys):
    manifest = tmp_path / "scenarios.json"
    assert main(["list", str(MULTI), "--export-scenarios", str(manifest)]) == 0
    assert "已导出 2 个场景" in capsys.readouterr().out

    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["name"].startswith("<")  # 占位符，必须由人填
    assert len(payload["scenarios"]) == 2
    payload["name"] = "isis-troubleshooting"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out = tmp_path / "skill"
    assert main(["build", str(MULTI), "--scenarios", str(manifest), "--out", str(out)]) == 0
    text = (out / "SKILL.md").read_text(encoding="utf-8")
    assert "## 场景跳转表" in text and "### 场景B：" in text
    assert lint_text(text).ok


def test_placeholder_name_is_refused(tmp_path, capsys):
    manifest = tmp_path / "scenarios.json"
    main(["list", str(MULTI), "--export-scenarios", str(manifest)])
    capsys.readouterr()
    out = tmp_path / "skill"
    code = main(["build", str(MULTI), "--scenarios", str(manifest), "--out", str(out)])
    assert code == 2
    assert "占位符" in capsys.readouterr().err


def test_missing_manifest_is_reported(tmp_path, capsys):
    code = main(
        ["build", str(MULTI), "--scenarios", str(tmp_path / "nope.json"), "--out", str(tmp_path / "s")]
    )
    assert code == 2
    assert "场景清单文件不存在" in capsys.readouterr().err


def test_dry_run_lists_the_scenarios(tmp_path, capsys):
    manifest = tmp_path / "scenarios.json"
    main(["list", str(MULTI), "--export-scenarios", str(manifest)])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["name"] = "isis-troubleshooting"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    out = tmp_path / "skill"
    code = main(
        ["build", str(MULTI), "--scenarios", str(manifest), "--out", str(out), "--dry-run"]
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "场景A：" in output and "场景B：" in output
    assert not out.exists()


# -- 阶段三：生成前规划 ---------------------------------------------------
def test_plan_counts_what_the_document_would_hold(scenarios):
    from subkg2skill.plan import plan_document

    graph, named = scenarios
    doc = build_multi_doc(graph, named)
    plan = plan_document(doc)
    assert [p.label for p in plan.scenarios] == ["A", "B"]
    assert plan.steps == sum(len(s.steps) for s in doc.scenarios)
    assert plan.prechecks == len(doc.prechecks)
    # 修复命令数只数真有命令的根因
    scenario_a = plan.scenarios[0]
    assert scenario_a.fix_commands >= 1
    assert plan.scenarios[1].fix_commands == 0


def test_plan_records_which_prechecks_a_scenario_reads(scenarios):
    from subkg2skill.plan import plan_document

    graph, named = scenarios
    plan = plan_document(build_multi_doc(graph, named))
    assert plan.scenarios[0].prechecks  # 场景A 复用了公共采集
    assert all(1 <= number <= plan.prechecks for s in plan.scenarios for number in s.prechecks)


def test_plan_flags_a_scenario_contained_in_another(scenarios):
    from subkg2skill.plan import plan_document

    graph, named = scenarios
    plan = plan_document(build_multi_doc(graph, named))
    messages = " ".join(hint.message for hint in plan.hints)
    assert "全部也出现在" in messages and "可考虑并入" in messages


def test_plan_flags_a_cause_investigated_twice(scenarios):
    from subkg2skill.plan import plan_document

    graph, named = scenarios
    plan = plan_document(build_multi_doc(graph, named))
    messages = " ".join(hint.message for hint in plan.hints)
    assert "同一个根因在多处各排一遍" in messages


def test_plan_of_a_single_clean_scenario_has_nothing_to_suggest(scenarios):
    from subkg2skill.plan import plan_document

    graph, named = scenarios
    plan = plan_document(build_multi_doc(graph, named[:1]))
    assert [hint for hint in plan.hints if hint.kind == "scenario"] == []


def test_plan_lists_what_was_dropped(scenarios):
    from subkg2skill.plan import plan_document, render_plan

    graph, named = scenarios
    plan = plan_document(build_multi_doc(graph, named))
    rendered = "\n".join(render_plan(plan))
    assert "生成规划（未写盘" in rendered
    assert "| **合计** |" in rendered


def test_plan_command_runs_on_the_automatic_grouping(capsys):
    assert main(["plan", str(MULTI)]) == 0
    output = capsys.readouterr().out
    assert "生成规划" in output and "自动分组" in output
    assert "还能再合并的地方" in output


def test_plan_command_reads_a_manifest(tmp_path, capsys):
    manifest = tmp_path / "scenarios.json"
    main(["list", str(MULTI), "--export-scenarios", str(manifest)])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["name"] = "isis-troubleshooting"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    assert main(["plan", str(MULTI), "--scenarios", str(manifest)]) == 0
    output = capsys.readouterr().out
    assert "场景清单" in output and "isis-troubleshooting" in output


def test_plan_writes_nothing(tmp_path):
    before = set(tmp_path.iterdir())
    main(["plan", str(MULTI)])
    assert set(tmp_path.iterdir()) == before
