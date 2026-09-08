import json

from graph2skill.cli import main


def test_build_command(tmp_path, examples_dir, capsys):
    code = main(["build", str(examples_dir), "-o", str(tmp_path / "skill")])
    out = capsys.readouterr().out
    assert code == 0
    assert (tmp_path / "skill" / "SKILL.md").exists()
    assert "playbooks=" in out


def test_build_refuses_existing_output_without_force(tmp_path, examples_dir, capsys):
    target = tmp_path / "skill"
    assert main(["build", str(examples_dir), "-o", str(target)]) == 0
    assert main(["build", str(examples_dir), "-o", str(target)]) == 2
    assert "--force" in capsys.readouterr().err
    assert main(["build", str(examples_dir), "-o", str(target), "--force"]) == 0


def test_build_strict_fails_on_errors(tmp_path, data_dir, capsys):
    code = main(["build", str(data_dir / "messy.jsonc"), "-o", str(tmp_path / "skill"), "--strict"])
    assert code == 1
    assert "structural error" in capsys.readouterr().err
    assert not (tmp_path / "skill").exists()


def test_validate_json_output(data_dir, capsys):
    code = main(["validate", str(data_dir / "messy.jsonc"), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert {issue["code"] for issue in payload["issues"]} >= {"dangling-relation", "missing-entry"}


def test_validate_clean_graph(data_dir, capsys):
    assert main(["validate", str(data_dir / "minimal.json")]) == 0
    assert "0 error(s)" in capsys.readouterr().out


def test_stats_json(examples_dir, capsys):
    assert main(["stats", str(examples_dir), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["domain"] == "ISIS"
    assert payload["nodes"] == 17
    assert len(payload["playbooks"]) == payload["trees"]


def test_stats_text(examples_dir, capsys):
    assert main(["stats", str(examples_dir), "--lang", "en"]) == 0
    assert "node types" in capsys.readouterr().out


def test_inspect_known_node(examples_dir, capsys):
    assert main(["inspect", str(examples_dir), "--node-id", "cause:isis:isis-75700908-1"]) == 0
    out = capsys.readouterr().out
    assert "outgoing:" in out
    assert "playbook: references/playbooks/" in out


def test_inspect_falls_back_to_search(examples_dir, capsys):
    assert main(["inspect", str(examples_dir), "--node-id", "LDP"]) == 0
    assert "candidate(s)" in capsys.readouterr().err


def test_missing_input_is_a_usage_error(tmp_path, capsys):
    assert main(["stats", str(tmp_path / "nope.json")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_language_switch_produces_english_skill(tmp_path, examples_dir):
    main(["build", str(examples_dir), "-o", str(tmp_path / "en"), "--lang", "en", "--name", "isis-en"])
    text = (tmp_path / "en" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: isis-en" in text
    assert "When to use" in text
