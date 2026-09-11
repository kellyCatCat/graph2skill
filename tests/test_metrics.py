"""Delivery statistics: the numbers that say whether the optimisation landed.

A document can pass every structural check and still be unusable — one command
per step means nothing was merged, and a criterion count at parity with the
step count means most steps decide on a single reading.
"""

from __future__ import annotations

from subkg2skill.cli import main
from subkg2skill.graph import Graph
from subkg2skill.loader import load
from subkg2skill.plan import metrics, render_metrics
from subkg2skill.playbook import build_merged_playbook, fault_groups
from subkg2skill.template import build_multi_doc
from tests.conftest import ROOT

MULTI = ROOT / "tests" / "data" / "multisource"


def _doc():
    bundle, _ = load([str(MULTI)])
    graph, report = Graph.from_bundle(bundle)
    assert not report.errors
    groups = fault_groups(graph)
    units = [unit for group in groups for unit in group.units]
    scoped = graph.scope_to_units(units)
    named = [
        (group.name, build_merged_playbook(scoped.scope_to_units(group.units), group.symptoms, group.units))
        for group in groups
    ]
    return build_multi_doc(scoped, named)


def _by_name(doc):
    return {metric.name: metric for metric in metrics(doc)}


def test_every_threshold_is_reported():
    found = _by_name(_doc())
    for name in (
        "前置检查 命令数 / 步骤数",
        "必填参数",
        "步骤数 : 根因数",
        "判据 / 步骤",
        "命令复用率",
        "复检覆盖率",
        "「仅定位」根因",
    ):
        assert name in found, name


def test_steps_and_causes_should_pair_up():
    """一步一根因：对不上说明某一步排了不止一个根因，或某个根因没有步骤。"""
    metric = _by_name(_doc())["步骤数 : 根因数"]
    left, right = (int(part) for part in metric.value.split(" : "))
    assert metric.ok == (left == right)


def test_command_reuse_counts_steps_that_issue_their_own_command():
    doc = _doc()
    issued = sum(1 for s in doc.scenarios for step in s.steps if step.commands)
    steps = sum(len(s.steps) for s in doc.scenarios)
    assert _by_name(doc)["命令复用率"].value == f"{1 - issued / steps:.0%}"


def test_a_locate_only_cause_is_recorded_not_failed():
    """来源没给修复命令是数据完整度问题，不是文档缺陷。"""
    metric = _by_name(_doc())["「仅定位」根因"]
    assert metric.ok is True
    assert metric.value.endswith("个")


def test_scenario_balance_only_appears_with_several_scenarios():
    doc = _doc()
    assert "场景规模均衡性" in _by_name(doc)
    single = build_multi_doc(*_single())
    assert "场景规模均衡性" not in _by_name(single)


def _single():
    bundle, _ = load([str(MULTI)])
    graph, _report = Graph.from_bundle(bundle)
    groups = fault_groups(graph)[:1]
    units = [unit for group in groups for unit in group.units]
    scoped = graph.scope_to_units(units)
    return scoped, [
        (group.name, build_merged_playbook(scoped, group.symptoms, group.units))
        for group in groups
    ]


def test_the_report_marks_what_is_out_of_range():
    lines = render_metrics(metrics(_doc()))
    body = "\n".join(lines)
    assert "| 指标 | 本次 | 健康值 |" in body
    assert "⚠" in body or "全部指标" in body


def test_plan_prints_the_delivery_table(capsys):
    assert main(["plan", str(MULTI)]) == 0
    output = capsys.readouterr().out
    assert "交付统计" in output
    assert "命令复用率" in output


def test_build_reports_metrics_that_fell_out_of_range(tmp_path, capsys):
    import json

    manifest = tmp_path / "scenarios.json"
    main(["list", str(MULTI), "--export-scenarios", str(manifest)])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["name"] = "isis-troubleshooting"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    main(["build", str(MULTI), "--scenarios", str(manifest), "--out", str(tmp_path / "s")])
    output = capsys.readouterr().out
    assert "交付统计" in output
