"""交付形态：单故障一个文件，多场景拆成入口 + 每场景一份参考文件。

读者只走一个场景。把所有场景的步骤都堆在入口文件里，等于让每个读者都加载
别人那几份——和公共前置的取舍是同一件事，只是低了一层。
"""

import json

import pytest

from subkg2skill.cli import main
from subkg2skill.graph import Graph
from subkg2skill.lint import document_kind, lint_files, lint_path, reference_paths
from subkg2skill.loader import load
from subkg2skill.playbook import build_merged_playbook, build_playbook, fault_groups
from subkg2skill.render import BuildOptions, build_package
from subkg2skill.verify import verify_files, verify_path

from tests.conftest import ROOT

MULTI = ROOT / "tests" / "data" / "multisource"
ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


@pytest.fixture()
def multi():
    bundle, _ = load([str(MULTI)])
    graph, report = Graph.from_bundle(bundle)
    assert not report.errors
    groups = fault_groups(graph)
    units = [unit for group in groups for unit in group.units]
    scoped = graph.scope_to_units(units)
    named = [
        (
            group.name,
            build_merged_playbook(scoped.scope_to_units(group.units), group.symptoms, group.units),
        )
        for group in groups
    ]
    return scoped, named


def build(multi, **options):
    graph, named = multi
    return graph, build_package(graph, named, BuildOptions(name="isis-troubleshooting", **options))


# -- 形态 -----------------------------------------------------------------
def test_a_single_fault_skill_stays_one_file(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    package = build_package(example_graph, playbook, BuildOptions(name="isis-neighbor-down"))
    assert list(package.files) == ["SKILL.md"]
    # 一个场景拆出去只是多一层间接：跳转表只有一行，文件只有一个
    assert "reference/" not in package.files["SKILL.md"]


def test_several_scenarios_split_into_one_file_each(multi):
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    assert sorted(package.files) == [
        "SKILL.md",
        "reference/adjacency-flap.md",
        "reference/neighbor-down.md",
    ]


def test_the_entry_file_keeps_the_inputs_and_the_shared_collection(multi):
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    entry = package.files["SKILL.md"]
    assert "# 入参列表" in entry and "# 前置检查" in entry and "## 场景跳转表" in entry
    # 步骤与根因随场景走了，入口不再重复一遍
    assert "# 根因对照表" not in entry and "## 步骤1：" not in entry


def test_the_entry_file_points_at_every_scenario_file(multi):
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    entry = package.files["SKILL.md"]
    assert "进入对应场景后" in entry
    assert set(reference_paths(entry)) == {
        "reference/neighbor-down.md",
        "reference/adjacency-flap.md",
    }


def test_a_scenario_file_carries_its_own_header_steps_and_causes(multi):
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    scenario = package.files["reference/neighbor-down.md"]
    assert scenario.startswith("---\nname: neighbor-down\n")
    assert "\n# 场景A：" in scenario
    assert "\n## 步骤1：" in scenario
    assert "\n# 根因对照表" in scenario
    # 它是怎么被走到的，文件自己要说清楚
    assert "isis-troubleshooting" in scenario.split("---")[1]


def test_document_kind_tells_the_three_shapes_apart(multi, example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    single = build_package(example_graph, playbook, BuildOptions(name="x")).files["SKILL.md"]
    _graph, package = build(multi, scenario_slugs=["a", "b"])
    assert document_kind(_headings(single)) == "skill"
    assert document_kind(_headings(package.files["SKILL.md"])) == "index"
    assert document_kind(_headings(package.files["reference/a.md"])) == "scenario"


def _headings(text):
    return [line[2:].strip() for line in text.splitlines() if line.startswith("# ")]


# -- 文件名 ---------------------------------------------------------------
def test_the_caller_names_the_scenario_files(multi):
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    assert "reference/neighbor-down.md" in package.files
    assert not package.unnamed_scenarios


def test_an_unnamed_scenario_gets_a_placeholder_and_is_reported(multi):
    # 中文场景名没法机械翻译，和技能名一样得由调用方给；没给就报出来
    _graph, package = build(multi, scenario_slugs=["neighbor-down"])
    assert "reference/scenario-b.md" in package.files
    assert [name for name, _slug in package.unnamed_scenarios] and all(
        slug.startswith("scenario-") for _name, slug in package.unnamed_scenarios
    )


def test_two_scenarios_never_share_a_file(multi):
    # 场景名允许重复，文件名不能——后一个会被前一个覆盖掉
    _graph, package = build(multi, scenario_slugs=["same", "same"])
    assert len([path for path in package.files if path != "SKILL.md"]) == 2


def test_a_scenario_slug_that_is_not_a_slug_is_coerced(multi):
    _graph, package = build(multi, scenario_slugs=["Neighbor Down", "adjacency-flap"])
    assert "reference/neighbor-down.md" in package.files


# -- lint 与 verify 看的是整包 --------------------------------------------
def test_lint_checks_every_delivered_file(multi):
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    result = lint_files(package.files)
    assert result.ok, [issue.render() for issue in result.errors]


def test_lint_reports_which_file_an_issue_is_in(multi):
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    broken = dict(package.files)
    broken["reference/neighbor-down.md"] = broken["reference/neighbor-down.md"].replace(
        "## 步骤1：", "## 步骤2：", 1
    )
    messages = [issue.message for issue in lint_files(broken).issues]
    assert any(message.startswith("reference/neighbor-down.md: ") for message in messages)


def test_a_scenario_files_parameters_are_checked_against_the_entry_file(multi):
    """场景文件的命令用的是 SKILL.md 里声明的入参——单看一个文件查不了这件事。"""
    _graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    broken = dict(package.files)
    broken["reference/neighbor-down.md"] = broken["reference/neighbor-down.md"].replace(
        "# 根因对照表",
        "## 步骤9：无中生有\n\n1. **步骤名称**：无中生有\n"
        "2. **CLI 命令**：`display isis peer <never-declared>`\n"
        "3. **跳转信息**：\n   - 以上判据均不命中：判定“未找到根因”。\n"
        "4. **根因定位**：\n   - 无\n\n# 根因对照表",
        1,
    )
    messages = " ".join(issue.message for issue in lint_files(broken).issues)
    assert "未在入参列表声明的参数" in messages


def test_verify_reads_the_scenario_files_too(multi):
    graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    result = verify_files(package.files, graph)
    assert result.ok, [finding.render() for finding in result.errors]
    # 根因几乎全在场景文件里：只查 SKILL.md 等于只查了封面
    assert result.checked.get("cause", 0) > 0


def test_a_fabricated_command_in_a_scenario_file_is_caught(multi):
    graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    poisoned = dict(package.files)
    poisoned["reference/neighbor-down.md"] = poisoned["reference/neighbor-down.md"].replace(
        "display isis system-id conflict", "display invented command"
    )
    result = verify_files(poisoned, graph)
    assert result.errors
    assert all("reference/neighbor-down.md" in finding.where for finding in result.errors)


def test_the_entry_files_pointer_table_is_not_read_as_knowledge(multi):
    """入口的参考文件表列的是文件名，不是图里的内容——按内容回查会把场景名报成幻觉。"""
    graph, package = build(multi, scenario_slugs=["neighbor-down", "adjacency-flap"])
    result = verify_files({"SKILL.md": package.files["SKILL.md"]}, graph)
    assert result.ok, [finding.render() for finding in result.errors]


# -- 落盘与读回 -----------------------------------------------------------
def test_build_writes_the_reference_directory(tmp_path, capsys):
    manifest = tmp_path / "scenarios.json"
    assert main(["list", str(MULTI), "--export-scenarios", str(manifest)]) == 0
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["name"] = "isis-troubleshooting"
    for entry, slug in zip(payload["scenarios"], ["neighbor-down", "adjacency-flap"]):
        entry["slug"] = slug
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    out = tmp_path / "skill"
    assert main(["build", str(MULTI), "--scenarios", str(manifest), "--out", str(out)]) == 0
    assert (out / "SKILL.md").exists()
    assert {path.name for path in (out / "reference").iterdir()} == {
        "neighbor-down.md",
        "adjacency-flap.md",
    }
    assert lint_path(out).ok
    assert verify_path(out, [str(MULTI)]).ok


def test_the_exported_manifest_leaves_a_place_for_the_file_names(tmp_path, capsys):
    manifest = tmp_path / "scenarios.json"
    main(["list", str(MULTI), "--export-scenarios", str(manifest)])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert all(entry["slug"] == "" for entry in payload["scenarios"])


def test_an_unnamed_scenario_is_reported_on_the_command_line(tmp_path, capsys):
    manifest = tmp_path / "scenarios.json"
    main(["list", str(MULTI), "--export-scenarios", str(manifest)])
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["name"] = "isis-troubleshooting"
    manifest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    capsys.readouterr()

    out = tmp_path / "skill"
    assert main(["build", str(MULTI), "--scenarios", str(manifest), "--out", str(out)]) == 0
    assert "没给英文名，用了占位文件名" in capsys.readouterr().out
