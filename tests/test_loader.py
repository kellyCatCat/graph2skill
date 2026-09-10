"""Loading node/edge exports in every shape they arrive in."""

import json

import pytest

from subkg2skill.loader import SubgraphLoadError, load, load_paths
from tests.conftest import make_edge, make_node


def test_loads_directory_with_node_and_edge_files(example_dir):
    bundle, sources = load([str(example_dir)])
    assert len(bundle.nodes) == 17
    assert len(bundle.edges) == 26
    assert any("node.json" in source for source in sources)


def test_loads_explicit_node_and_edge_files(example_dir):
    bundle, _ = load(
        [],
        node_files=[str(example_dir / "node.json")],
        edge_files=[str(example_dir / "edge.json")],
    )
    assert len(bundle.nodes) == 17
    assert len(bundle.edges) == 26


def test_splits_records_by_shape(tmp_path):
    mixed = tmp_path / "mixed.json"
    records = [make_node("symptom_a", "symptom", "现象"), make_edge("edge_1", "has_cause", "symptom_a", "cause_b")]
    mixed.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    bundle, _ = load([str(mixed)])
    assert [node["node_id"] for node in bundle.nodes] == ["symptom_a"]
    assert [edge["edge_id"] for edge in bundle.edges] == ["edge_1"]


def test_reads_bundle_object(tmp_path):
    path = tmp_path / "bundle.json"
    path.write_text(
        json.dumps({"nodes": [make_node("symptom_a", "symptom", "现象")], "edges": []}),
        encoding="utf-8",
    )
    bundle, _ = load([str(path)])
    assert len(bundle.nodes) == 1


def test_tolerates_comments_trailing_commas_and_bom(tmp_path):
    path = tmp_path / "messy.jsonc"
    path.write_text(
        '﻿[\n  // 首个节点\n  {"node_id": "symptom_a", "node_type": "symptom", '
        '"name": "现象", /* 行内注释 */ "attrs": {},},\n]\n',
        encoding="utf-8",
    )
    bundle, _ = load([str(path)])
    assert bundle.nodes[0]["node_id"] == "symptom_a"


def test_reads_jsonl(tmp_path):
    path = tmp_path / "nodes.jsonl"
    path.write_text(
        "\n".join(json.dumps(make_node(f"symptom_{i}", "symptom", f"现象{i}")) for i in range(3)),
        encoding="utf-8",
    )
    bundle, _ = load([str(path)])
    assert len(bundle.nodes) == 3


def test_missing_file_names_the_path(tmp_path):
    with pytest.raises(SubgraphLoadError) as excinfo:
        load([str(tmp_path / "nope.json")])
    assert "nope.json" in str(excinfo.value)


def test_broken_json_names_the_path(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text('[{"node_id": ', encoding="utf-8")
    with pytest.raises(SubgraphLoadError) as excinfo:
        load([str(path)])
    assert "broken.json" in str(excinfo.value)


def test_rejects_non_object_records(tmp_path):
    path = tmp_path / "weird.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(SubgraphLoadError):
        load([str(path)])


def test_empty_directory_is_an_error(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(SubgraphLoadError):
        load([str(tmp_path / "empty")])


def test_no_input_is_an_error():
    with pytest.raises(SubgraphLoadError):
        load_paths([])
