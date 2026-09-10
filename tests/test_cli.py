"""End-to-end CLI behaviour."""

import json

import pytest

from subkg2skill.cli import main

ISIS = "symptom_7f1c02aa93be4d61b0c5e210"


def build(tmp_path, example_dir, *extra, name="isis-neighbor-down"):
    out = tmp_path / "skill"
    args = ["build", str(example_dir), "--out", str(out), "--entry", ISIS]
    if name:
        args += ["--name", name]
    return main(args + list(extra)), out


def test_build_writes_a_conforming_skill(tmp_path, example_dir, capsys):
    code, out = build(tmp_path, example_dir)
    assert code == 0
    assert {path.name for path in out.iterdir()} == {"SKILL.md", "reference", "scripts"}
    text = (out / "SKILL.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: isis-neighbor-down\n")
    for section in ("# 入参列表", "# 前置检查", "# 排查步骤", "# 根因对照表"):
        assert section in text
    output = capsys.readouterr().out
    assert "模板检查：通过" in output
    assert ".claude/skills/isis-neighbor-down" in output


def test_build_requires_an_english_name(tmp_path, example_dir, capsys):
    code, _ = build(tmp_path, example_dir, name="")
    assert code == 2
    assert "英文技能名" in capsys.readouterr().err


def test_build_rejects_a_chinese_name(tmp_path, example_dir, capsys):
    code, _ = build(tmp_path, example_dir, name="邻居震荡")
    assert code == 2
    assert "英文" in capsys.readouterr().err


def test_build_needs_an_entry_when_the_graph_has_several(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    code = main(["build", str(example_dir), "--out", str(out), "--name", "x"])
    assert code == 2
    assert "请用 --entry 指定" in capsys.readouterr().err


def test_entry_accepts_a_name_keyword(tmp_path, example_dir):
    out = tmp_path / "skill"
    code = main(
        ["build", str(example_dir), "--out", str(out), "--entry", "承载业务中断", "--name", "svc"]
    )
    assert code == 0
    assert "承载业务中断" in (out / "SKILL.md").read_text(encoding="utf-8")


def test_unknown_entry_is_an_error(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    code = main(["build", str(example_dir), "--out", str(out), "--entry", "没有这个症状", "--name", "x"])
    assert code == 2
    assert "没有匹配到任何 symptom" in capsys.readouterr().err


def test_build_refuses_to_overwrite_without_force(tmp_path, example_dir):
    code, out = build(tmp_path, example_dir)
    assert code == 0
    assert build(tmp_path, example_dir)[0] == 2
    assert build(tmp_path, example_dir, "--force")[0] == 0


def test_dry_run_writes_nothing(tmp_path, example_dir, capsys):
    code, out = build(tmp_path, example_dir, "--dry-run")
    assert code == 0
    assert not out.exists()
    output = capsys.readouterr().out
    assert "将写出" in output and "模板检查" in output


def test_data_and_script_switches(tmp_path, example_dir):
    code, out = build(tmp_path, example_dir, "--data", "none", "--no-script")
    assert code == 0
    assert not (out / "reference" / "subgraph.json").exists()
    assert not (out / "scripts").exists()


def test_list_shows_entries_and_suggested_slugs(example_dir, capsys):
    assert main(["list", str(example_dir)]) == 0
    output = capsys.readouterr().out
    assert "IS-IS邻居无法建立" in output
    assert ISIS in output
    assert "建议 slug" in output


def test_build_all_uses_the_name_mapping(tmp_path, example_dir, capsys):
    names = tmp_path / "names.json"
    names.write_text(
        json.dumps({ISIS: "isis-neighbor-down", "symptom_2ad4471b8c0f4e2ab7d31f55": "service-down"}),
        encoding="utf-8",
    )
    out = tmp_path / "all"
    assert main(["build-all", str(example_dir), "--out", str(out), "--names", str(names)]) == 0
    assert {path.name for path in out.iterdir()} == {"isis-neighbor-down", "service-down"}
    assert (out / "isis-neighbor-down" / "SKILL.md").exists()


def test_build_all_warns_about_mechanical_slugs(tmp_path, example_dir, capsys):
    out = tmp_path / "all"
    assert main(["build-all", str(example_dir), "--out", str(out)]) == 0
    assert "请按语义改写" in capsys.readouterr().out


def test_build_all_limit(tmp_path, example_dir):
    out = tmp_path / "all"
    assert main(["build-all", str(example_dir), "--out", str(out), "--limit", "1"]) == 0
    assert len(list(out.iterdir())) == 1


def test_missing_names_file_is_an_error(tmp_path, example_dir, capsys):
    out = tmp_path / "all"
    code = main(["build-all", str(example_dir), "--out", str(out), "--names", str(tmp_path / "x.json")])
    assert code == 2
    assert "命名映射文件不存在" in capsys.readouterr().err


def test_lint_passes_on_generated_output(tmp_path, example_dir, capsys):
    _, out = build(tmp_path, example_dir)
    assert main(["lint", str(out)]) == 0
    assert "模板检查：通过" in capsys.readouterr().out


def test_lint_fails_on_a_broken_document(tmp_path, capsys):
    skill = tmp_path / "broken"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# 排查步骤\n", encoding="utf-8")
    assert main(["lint", str(skill)]) == 1
    assert "ERROR" in capsys.readouterr().out


def test_section_filter_narrows_the_graph(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    code = main(
        ["build", str(example_dir), "--out", str(out), "--section", "4.3.3", "--name", "svc", "--dry-run"]
    )
    assert code == 0
    assert "承载业务中断" in capsys.readouterr().out


def test_filters_that_match_nothing_are_an_error(tmp_path, example_dir, capsys):
    out = tmp_path / "skill"
    code = main(["build", str(example_dir), "--out", str(out), "--vendor", "Nokia", "--name", "x"])
    assert code == 2
    assert "没有选中任何节点" in capsys.readouterr().err


def test_validate_reports_clean_input(example_dir, capsys):
    assert main(["validate", str(example_dir)]) == 0
    assert "结构完整" in capsys.readouterr().out


def test_validate_fails_on_dangling_edges(write_bundle, capsys):
    from tests.conftest import make_edge, make_node

    directory = write_bundle(
        [make_node("symptom_a", "symptom", "现象")],
        [make_edge("edge_1", "has_cause", "symptom_a", "cause_missing")],
    )
    assert main(["validate", str(directory)]) == 1
    assert "edge_dangling" in capsys.readouterr().out


def test_strict_build_stops_on_errors(tmp_path, write_bundle):
    from tests.conftest import make_edge, make_node

    directory = write_bundle(
        [make_node("symptom_a", "symptom", "现象")],
        [make_edge("edge_1", "has_cause", "symptom_a", "cause_missing")],
    )
    out = tmp_path / "skill"
    assert main(["build", str(directory), "--out", str(out), "--name", "x", "--strict"]) == 1
    assert not out.exists()


def test_inspect_summarises_the_input(example_dir, capsys):
    assert main(["inspect", str(example_dir)]) == 0
    output = capsys.readouterr().out
    assert "可生成 skill：2 份" in output
    assert "symptom (故障症状): 2" in output


def test_missing_input_is_a_usage_error():
    with pytest.raises(SystemExit):
        main(["build", "--out", "x", "--name", "y"])


def test_missing_file_is_reported(tmp_path, capsys):
    assert main(["inspect", str(tmp_path / "nope.json")]) == 2
    assert "文件不存在" in capsys.readouterr().err
