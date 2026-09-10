"""End-to-end CLI behaviour."""

import json

import pytest

from subkg2skill.cli import main
from tests.conftest import make_edge, make_node


def build(tmp_path, example_dir, *extra):
    out = tmp_path / "skill"
    code = main(["build", str(example_dir), "--out", str(out), *extra])
    return code, out


def test_build_writes_a_usable_skill(tmp_path, example_dir, capsys):
    code, out = build(tmp_path, example_dir, "--name", "ipran-fault-diagnosis")
    assert code == 0
    assert (out / "SKILL.md").read_text(encoding="utf-8").startswith("---\n")
    assert (out / "reference" / "index.md").exists()
    assert (out / "scripts" / "kg_query.py").exists()
    assert len(list((out / "reference").glob("fault-*.md"))) == 2
    assert "ipran-fault-diagnosis" in capsys.readouterr().out


def test_build_rejects_a_name_it_cannot_slugify(tmp_path, example_dir, capsys):
    code, _ = build(tmp_path, example_dir, "--name", "知识图谱")
    assert code == 2
    assert "错误" in capsys.readouterr().err


def test_build_refuses_to_overwrite_without_force(tmp_path, example_dir):
    code, out = build(tmp_path, example_dir)
    assert code == 0
    assert main(["build", str(example_dir), "--out", str(out)]) == 2
    assert main(["build", str(example_dir), "--out", str(out), "--force"]) == 0


def test_dry_run_writes_nothing(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--out", str(out), "--dry-run"]) == 0
    assert not out.exists()
    assert "将写出" in capsys.readouterr().out


def test_root_and_depth_narrow_the_subgraph(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    assert main(
        ["build", str(example_dir), "--out", str(out), "--root", "光模块", "--depth", "2", "--dry-run"]
    ) == 0
    assert "节点 9" in capsys.readouterr().out


def test_section_filter_selects_one_entry(tmp_path, example_dir):
    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--out", str(out), "--section", "4.3.3"]) == 0
    assert len(list((out / "reference").glob("fault-*.md"))) == 1


def test_filters_that_match_nothing_are_an_error(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--out", str(out), "--vendor", "Nokia"]) == 2
    assert "没有选中任何节点" in capsys.readouterr().err


def test_unknown_root_is_an_error(example_dir, capsys):
    assert main(["inspect", str(example_dir), "--root", "不存在的现象"]) == 2
    assert "既不是 node_id" in capsys.readouterr().err


def test_max_playbooks_limits_output(tmp_path, example_dir):
    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--out", str(out), "--max-playbooks", "1"]) == 0
    assert len(list((out / "reference").glob("fault-*.md"))) == 1


def test_data_and_script_switches(tmp_path, example_dir):
    out = tmp_path / "skill"
    assert main(["build", str(example_dir), "--out", str(out), "--data", "none", "--no-script"]) == 0
    assert not (out / "reference" / "subgraph.json").exists()
    assert not (out / "scripts").exists()


def test_explicit_node_and_edge_files(tmp_path, example_dir):
    out = tmp_path / "skill"
    code = main(
        [
            "build",
            "--nodes", str(example_dir / "node.json"),
            "--edges", str(example_dir / "edge.json"),
            "--out", str(out),
        ]
    )
    assert code == 0
    data = json.loads((out / "reference" / "subgraph.json").read_text(encoding="utf-8"))
    assert len(data["nodes"]) == 17


def test_validate_reports_clean_input(example_dir, capsys):
    assert main(["validate", str(example_dir)]) == 0
    assert "结构完整" in capsys.readouterr().out


def test_validate_fails_on_dangling_edges(write_bundle, capsys):
    directory = write_bundle(
        [make_node("symptom_a", "symptom", "现象")],
        [make_edge("edge_1", "has_cause", "symptom_a", "cause_missing")],
    )
    assert main(["validate", str(directory)]) == 1
    assert "edge_dangling" in capsys.readouterr().out


def test_strict_build_stops_on_errors(tmp_path, write_bundle, capsys):
    directory = write_bundle(
        [make_node("symptom_a", "symptom", "现象")],
        [make_edge("edge_1", "has_cause", "symptom_a", "cause_missing")],
    )
    out = tmp_path / "skill"
    assert main(["build", str(directory), "--out", str(out), "--strict"]) == 1
    assert not out.exists()


def test_non_strict_build_keeps_going_and_records_the_drop(tmp_path, write_bundle):
    directory = write_bundle(
        [make_node("symptom_a", "symptom", "现象")],
        [make_edge("edge_1", "has_cause", "symptom_a", "cause_missing")],
    )
    out = tmp_path / "skill"
    assert main(["build", str(directory), "--out", str(out)]) == 0
    coverage = (out / "reference" / "coverage.md").read_text(encoding="utf-8")
    assert "edge_dangling" in coverage


def test_inspect_summarises_the_input(example_dir, capsys):
    assert main(["inspect", str(example_dir)]) == 0
    output = capsys.readouterr().out
    assert "可生成排查手册：2 份" in output
    assert "symptom (故障症状): 2" in output


def test_missing_input_is_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        main(["build", "--out", "x"])


def test_missing_file_is_reported(tmp_path, capsys):
    assert main(["inspect", str(tmp_path / "nope.json")]) == 2
    assert "文件不存在" in capsys.readouterr().err
