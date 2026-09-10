"""The query script shipped inside the skill, exercised as a subprocess."""

import json
import subprocess
import sys

import pytest

from subkg2skill.cli import main


@pytest.fixture(scope="module")
def skill_dir(tmp_path_factory):
    from tests.conftest import EXAMPLE_DIR

    out = tmp_path_factory.mktemp("skill") / "ipran"
    assert main(["build", str(EXAMPLE_DIR), "--out", str(out), "--name", "ipran"]) == 0
    return out


def run(skill_dir, *args):
    result = subprocess.run(
        [sys.executable, str(skill_dir / "scripts" / "kg_query.py"), *args],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_stats_reports_the_bundled_counts(skill_dir):
    output = run(skill_dir, "stats")
    assert "节点：17；关系：26" in output
    assert "candidate" in output


def test_search_finds_nodes_by_alias(skill_dir):
    output = run(skill_dir, "search", "ISIS邻居Down")
    assert "IS-IS邻居无法建立" in output


def test_search_can_filter_by_type(skill_dir):
    output = run(skill_dir, "search", "区域地址", "--type", "cause")
    assert "[cause]" in output and "[observation]" not in output


def test_search_without_hits_says_so(skill_dir):
    assert "没有匹配" in run(skill_dir, "search", "不存在的东西")


def test_show_accepts_an_id_prefix_and_prints_sources(skill_dir):
    output = run(skill_dir, "show", "observation_6b40")
    assert "两端区域地址不同" in output
    assert "NE40E 维护宝典.pdf" in output
    assert "→ [confirms]" in output
    assert "未求值" in output


def test_show_json_returns_the_raw_record(skill_dir):
    payload = json.loads(run(skill_dir, "show", "cause_9c11", "--json"))
    assert payload["attrs"]["cause_kind"] == "configuration"


def test_booleans_render_as_json_not_python(skill_dir):
    assert "example_specific: false" in run(skill_dir, "show", "observation_6b40")


def test_neighbors_filters_by_edge_type(skill_dir):
    output = run(skill_dir, "neighbors", "symptom_7f1c", "--edge-type", "has_cause")
    assert output.count("[has_cause]") == 3
    assert "[diagnosed_by]" not in output


def test_expand_walks_forward_and_pulls_verdicts(skill_dir):
    output = run(skill_dir, "expand", "symptom_7f1c", "--depth", "2")
    assert "← [supports]" in output or "← [confirms]" in output


def test_path_finds_a_chain(skill_dir):
    output = run(skill_dir, "path", "symptom_7f1c", "repair_2c9d")
    assert "[has_cause]" in output and "[repaired_by]" in output


def test_path_reports_unreachable(skill_dir):
    output = run(skill_dir, "path", "repair_2c9d", "symptom_7f1c")
    assert "没有可达路径" in output


def test_ambiguous_prefix_lists_candidates(skill_dir):
    result = subprocess.run(
        [sys.executable, str(skill_dir / "scripts" / "kg_query.py"), "show", "cause_"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "前缀不唯一" in result.stderr


def test_missing_data_file_is_reported(skill_dir, tmp_path):
    result = subprocess.run(
        [sys.executable, str(skill_dir / "scripts" / "kg_query.py"), "--data", str(tmp_path / "x.json"), "stats"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "找不到数据文件" in result.stderr
