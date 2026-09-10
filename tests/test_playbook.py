"""Walking the subgraph into playbook structures."""

from subkg2skill.graph import Graph
from subkg2skill.loader import RawBundle
from subkg2skill.playbook import build_playbook, build_playbooks, coverage, entry_symptoms
from tests.conftest import make_edge, make_node

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


def test_causes_follow_rank_order(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    assert [branch.cause.name for branch in playbook.causes] == [
        "两端接口 MTU 配置不一致",
        "两端区域地址（Area ID）配置不一致",
        "物理链路或光模块异常",
    ]


def test_entry_checks_follow_next_step_chain(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    assert [step.check.name for step in playbook.entry_checks] == [
        "查看 IS-IS 邻居状态",
        "比对两端 IS-IS 接口配置",
    ]


def test_a_check_is_expanded_once_per_playbook(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    repeated = [
        step
        for branch in playbook.causes
        for step in branch.checks
        if step.check.name == "比对两端 IS-IS 接口配置"
    ]
    assert repeated and all(step.repeated for step in repeated)
    assert all(not step.outcomes for step in repeated)


def test_verdicts_are_collected_per_cause(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    optical = next(b for b in playbook.causes if b.cause.name == "物理链路或光模块异常")
    assert [v.kind for v in optical.verdicts] == ["supports", "excludes"]
    assert optical.verdicts_of("confirms") == []


def test_observations_record_the_causes_they_decide(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    first = playbook.entry_checks[0]
    verdicts = [v for outcome in first.outcomes for v in outcome.verdicts]
    assert [v.label for v in verdicts] == ["支持"]


def test_repairs_and_related_links_are_captured(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    optical = next(b for b in playbook.causes if b.cause.name == "物理链路或光模块异常")
    assert [link.node.node_type for link in optical.repairs] == ["repair"]
    assert [link.node.name for link in optical.refines] == ["光纤接头污染或弯折"]
    assert [link.node.name for link in optical.leads_to] == ["承载业务中断"]


def test_referrals_and_counts(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    assert [link.node.node_type for link in playbook.referrals] == ["escalation"]
    assert playbook.check_count == 3
    assert playbook.repair_count == 3


def test_trigger_terms_merge_aliases_and_match_phrases(example_graph):
    playbook = build_playbook(example_graph, example_graph.nodes[ISIS])
    terms = playbook.trigger_terms()
    assert terms[0] == "IS-IS邻居无法建立"
    assert "邻居停留在Init" in terms
    assert len(terms) == len(set(terms))


def test_entry_symptoms_rank_real_entries_first():
    nodes = [
        make_node("symptom_ref", "symptom", "只被引用的入口"),
        make_node("symptom_main", "symptom", "真入口"),
        make_node("cause_a", "cause", "原因"),
    ]
    edges = [
        make_edge("edge_1", "has_cause", "symptom_main", "cause_a"),
        make_edge("edge_2", "refers_to", "symptom_main", "symptom_ref"),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    assert [node.name for node in entry_symptoms(graph)] == ["真入口", "只被引用的入口"]


def test_next_step_cycles_do_not_loop_forever():
    nodes = [
        make_node("symptom_a", "symptom", "现象"),
        make_node("check_a", "check", "检查A"),
        make_node("check_b", "check", "检查B"),
    ]
    edges = [
        make_edge("edge_1", "diagnosed_by", "symptom_a", "check_a"),
        make_edge("edge_2", "next_step", "check_a", "check_b"),
        make_edge("edge_3", "next_step", "check_b", "check_a"),
    ]
    graph, _ = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    playbook = build_playbook(graph, graph.nodes["symptom_a"])
    assert [step.check.node_id for step in playbook.entry_checks] == ["check_a", "check_b"]


def test_symptom_without_causes_still_builds(tiny_graph):
    graph = tiny_graph.subgraph(["symptom_a"])
    playbook = build_playbook(graph, graph.nodes["symptom_a"])
    assert playbook.causes == [] and playbook.entry_checks == []


def test_build_playbooks_honours_the_limit(example_graph):
    assert len(build_playbooks(example_graph)) == 2
    assert len(build_playbooks(example_graph, limit=1)) == 1


def test_coverage_reports_undocumented_nodes(example_graph):
    playbooks = build_playbooks(example_graph)
    stats = coverage(example_graph, playbooks)
    assert len(stats["uncovered"]) == 0
    single = coverage(example_graph, [playbooks[1]])
    assert len(single["uncovered"]) > 0
