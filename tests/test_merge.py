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
    groups = fault_groups(multi_graph)
    assert len(groups) == 1
    return groups[0]


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


def test_units_of_one_node_are_not_merged_back():
    # 同一个症状节点跨三个单元，是三个不同故障，不能因为同名就并回去
    groups = fault_groups(graph_of(MESSY))
    assert len(groups) == 3
    assert all(len(group.symptoms) == 1 for group in groups)


def test_no_merge_lists_every_scenario(multi_graph):
    assert len(fault_groups(multi_graph, merge=False)) == 3


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
    assert len(list(out.iterdir())) == 1


def test_build_all_no_merge_emits_one_per_source(tmp_path):
    out = tmp_path / "all"
    assert main(["build-all", str(MULTI), "--out", str(out), "--no-merge"]) == 0
    assert len(list(out.iterdir())) == 3


def test_list_shows_the_merge_and_its_build_command(capsys):
    assert main(["list", str(MULTI)]) == 0
    output = capsys.readouterr().out
    assert "合并来源 : 3 个" in output
    assert "--entry symptom_manual --entry symptom_tree --entry symptom_case" in output
