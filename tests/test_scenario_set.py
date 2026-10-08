"""What one skill covers, however its scenarios were chosen."""

import pytest

from subkg2skill.cli import main
from subkg2skill.graph import Graph
from subkg2skill.loader import load
from subkg2skill.scenarios import from_entries, from_groups, plan_each, resolve_entry

from tests.conftest import ROOT

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


@pytest.fixture()
def multi_graph():
    bundle, _ = load([str(ROOT / "tests" / "data" / "multisource")])
    graph, _report = Graph.from_bundle(bundle)
    return graph


def test_entries_and_groups_agree_on_a_one_fault_subgraph(example_graph):
    grouped = from_groups(example_graph)
    entry = from_entries(example_graph, [resolve_entry(example_graph, ISIS)], grouped.units)
    assert [name for name, _book in grouped.named] == [name for name, _book in entry.named]
    assert grouped.covered == entry.covered


def test_exclusions_leave_the_reference_graph_whole(multi_graph):
    found = from_entries(multi_graph, [resolve_entry(multi_graph, "symptom_manual")], exclude=["认证"])
    assert found.removed and all(spec == "认证" for spec, _name in found.removed)
    dropped = set(found.reference.nodes) - set(found.graph.nodes)
    assert dropped and all(node_id in found.reference.nodes for node_id in dropped)


def test_a_one_scenario_build_is_summarised_as_one_fault(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--name", "x", "--out", str(out), "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "入口：IS-IS邻居无法建立" in output and "场景 1 个" not in output


def test_a_supplied_skill_index_wins_over_the_batch(example_graph):
    plan = plan_each(example_graph, names={ISIS: "isis-neighbor-down"})
    index = plan.index({ISIS: "chosen-elsewhere"})
    assert index[ISIS] == "chosen-elsewhere"
    assert plan.index()[ISIS] == "isis-neighbor-down"
