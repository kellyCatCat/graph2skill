from graph2skill.analyze import GraphIndex, resolve_trees, validate
from graph2skill.loader import load_graph
from graph2skill.merge import merge_graphs


def _graph(data_dir, name):
    return load_graph(data_dir / name)


def test_validate_flags_dangling_relations_and_missing_entries(data_dir):
    merged, _ = merge_graphs([_graph(data_dir, "messy.jsonc")])
    issues = validate(merged)
    codes = {issue.code for issue in issues}
    assert "dangling-relation" in codes  # r3 -> node:does-not-exist
    assert "missing-entry" in codes  # scenario:demo:missing
    assert issues[0].severity == "error"  # sorted by severity


def test_validate_reports_isolated_nodes(data_dir):
    graph = _graph(data_dir, "cyclic.json")
    issues = validate(graph)
    isolated = [issue for issue in issues if issue.code == "isolated-node"]
    assert [issue.ref for issue in isolated] == ["lonely"]


def test_clean_graph_has_no_errors(data_dir):
    assert not [issue for issue in validate(_graph(data_dir, "minimal.json")) if issue.severity == "error"]


def test_cycles_are_cut_and_reported(data_dir):
    graph = _graph(data_dir, "cyclic.json")
    trees = resolve_trees(graph)
    tree = next(tree for tree in trees if tree.entry_node_id == "a")
    repeated = [view for view in tree.root.iter_views() if view.repeated]
    assert [view.node_id for view in repeated] == ["a"]
    assert any(issue.code == "cycle" for issue in tree.issues)


def test_declared_scope_limits_traversal(data_dir):
    merged, _ = merge_graphs([_graph(data_dir, "minimal.json"), _graph(data_dir, "messy.jsonc")])
    free = next(t for t in resolve_trees(merged) if t.entry_node_id == "scenario:demo:1")
    scoped = next(
        t for t in resolve_trees(merged, respect_declared_scope=True) if t.entry_node_id == "scenario:demo:1"
    )
    assert "case:demo:1" in free.node_ids  # reachable through cause:demo:1
    assert "case:demo:1" not in scoped.node_ids  # not listed by the declared tree


def test_max_depth_truncates(data_dir):
    graph = _graph(data_dir, "cyclic.json")
    tree = next(t for t in resolve_trees(graph, max_depth=1) if t.entry_node_id == "a")
    assert tree.max_depth == 1
    assert any(view.truncated for view in tree.root.iter_views())


def test_inferred_entries_can_be_disabled(data_dir):
    graph = _graph(data_dir, "cyclic.json")
    trees = resolve_trees(graph, include_inferred=False)
    assert [tree.entry_node_id for tree in trees] == ["a"]


def test_index_orders_children_by_ontology_then_source_order(example_bundle):
    index: GraphIndex = example_bundle.index
    children = [relation.to_id for relation in index.successors("scenario:isis:IS-IS-1")]
    assert children[0] == "check:isis:peer-status"  # checks before causes
    assert children[1] == "cause:isis:network-type-mismatch"  # declaration order kept


def test_stats_count_commands_and_provenance(example_bundle):
    stats = example_bundle.stats
    assert stats.node_count == 17
    assert stats.orphan_count == 0
    assert stats.command_count > 20
    assert stats.sources["mp_ne40"] > stats.sources["ipr_cot"]
