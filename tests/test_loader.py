import pytest

from graph2skill.loader import GraphLoadError, expand_inputs, load_graph, parse_document, relax_json


def test_relax_json_handles_trailing_commas_and_comments():
    text = '{"a": [1, 2,], /* keep me out */ "b": {"u": "http://x//y",}, // tail\n}'
    assert parse_document(text) == {"a": [1, 2], "b": {"u": "http://x//y"}}


def test_relax_json_leaves_string_content_untouched():
    text = '{"note": "a, } and // not a comment"}'
    assert relax_json(text) == text


def test_broken_json_raises_with_file_name(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text('{"nodes": [', encoding="utf-8")
    with pytest.raises(GraphLoadError) as excinfo:
        load_graph(broken)
    assert "broken.json" in str(excinfo.value)


def test_empty_file_is_rejected(tmp_path):
    empty = tmp_path / "empty.json"
    empty.write_text("   ", encoding="utf-8")
    with pytest.raises(GraphLoadError):
        load_graph(empty)


def test_bom_is_stripped(tmp_path):
    path = tmp_path / "bom.json"
    path.write_text('﻿{"graphId": "X", "nodes": []}', encoding="utf-8")
    assert load_graph(path).graph_id == "X"


def test_load_messy_document_records_warnings(data_dir):
    graph = load_graph(data_dir / "messy.jsonc")
    assert graph.graph_id == "MESSY"
    assert set(graph.nodes) == {"scenario:demo:1", "case:demo:1"}
    assert any("without id" in warning for warning in graph.warnings)


def test_yaml_document_round_trip(tmp_path):
    yaml = pytest.importorskip("yaml")
    path = tmp_path / "graph.yaml"
    path.write_text(
        yaml.safe_dump(
            {"graphId": "Y", "domain": "D", "nodes": [{"id": "n1", "type": "SCENARIO", "name": "N"}]},
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    graph = load_graph(path)
    assert graph.nodes["n1"].name == "N"


def test_expand_inputs_is_sorted_and_deduplicated(data_dir):
    files = expand_inputs([data_dir, data_dir / "minimal.json"])
    names = [path.name for path in files]
    assert names == sorted(names)
    assert len(names) == len(set(names))


def test_expand_inputs_rejects_missing_path(tmp_path):
    with pytest.raises(GraphLoadError):
        expand_inputs([tmp_path / "nope.json"])
