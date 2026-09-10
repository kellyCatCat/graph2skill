"""Shared fixtures: the bundled example subgraph, plus small hand-built ones."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "subkg-to-skill" / "scripts"))

from subkg2skill.graph import Graph  # noqa: E402
from subkg2skill.loader import load  # noqa: E402

EXAMPLE_DIR = ROOT / "examples" / "subgraph"


@pytest.fixture(scope="session")
def example_dir() -> Path:
    return EXAMPLE_DIR


@pytest.fixture()
def example_graph() -> Graph:
    bundle, _ = load([str(EXAMPLE_DIR)])
    graph, report = Graph.from_bundle(bundle)
    assert not report.errors
    return graph


def scope() -> dict:
    return {
        "vendor": "Huawei",
        "product_family": "NetEngine40E",
        "software_version": None,
        "component_type": None,
        "scenario": None,
        "scope_basis": "document_product",
    }


def make_node(node_id: str, node_type: str, name: str, **overrides) -> dict:
    record = {
        "node_id": node_id,
        "node_type": node_type,
        "name": name,
        "description": f"{name} 的说明",
        "aliases": [],
        "attrs": {},
        "scope": scope(),
        "status": "candidate",
        "review_status": "machine_checked",
        "diagnostic_contexts": [{"section": "1.2.3", "title": "1.2.3 示例章节"}],
        "provenance": [],
        "quality_flags": [],
    }
    record.update(overrides)
    return record


def make_edge(edge_id: str, edge_type: str, source: str, target: str, **overrides) -> dict:
    record = {
        "edge_id": edge_id,
        "edge_type": edge_type,
        "source": source,
        "target": target,
        "condition": None,
        "condition_status": "unconditional",
        "rank": None,
        "scope": scope(),
        "diagnostic_context": {"section": "1.2.3", "title": "1.2.3 示例章节"},
        "evidence": [],
        "quality_flags": [],
        "review_status": "machine_checked",
        "status": "candidate",
        "attrs": {},
    }
    record.update(overrides)
    return record


@pytest.fixture()
def tiny_records():
    """A minimal symptom → cause → check → observation → repair chain."""
    nodes = [
        make_node("symptom_a", "symptom", "端口不通"),
        make_node("cause_b", "cause", "配置错误", attrs={"cause_kind": "configuration"}),
        make_node("check_c", "check", "查看配置", attrs={"check_kind": "cli"}),
        make_node(
            "observation_d",
            "observation",
            "配置缺失",
            attrs={"field": "配置项", "operator": "not_exists", "value": None},
        ),
        make_node("repair_e", "repair", "补齐配置", attrs={"repair_kind": "configuration"}),
    ]
    edges = [
        make_edge("edge_1", "has_cause", "symptom_a", "cause_b", rank=1),
        make_edge("edge_2", "diagnosed_by", "cause_b", "check_c"),
        make_edge("edge_3", "observes", "check_c", "observation_d"),
        make_edge("edge_4", "confirms", "observation_d", "cause_b"),
        make_edge("edge_5", "repaired_by", "cause_b", "repair_e"),
    ]
    return nodes, edges


@pytest.fixture()
def tiny_graph(tiny_records) -> Graph:
    from subkg2skill.loader import RawBundle

    nodes, edges = tiny_records
    graph, report = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["tiny"]))
    assert not report.errors
    return graph


@pytest.fixture()
def write_bundle(tmp_path):
    """Write node/edge records to a temp directory and return that directory."""

    def _write(nodes, edges, name: str = "graph") -> Path:
        directory = tmp_path / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "node.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
        (directory / "edge.json").write_text(json.dumps(edges, ensure_ascii=False), encoding="utf-8")
        return directory

    return _write
