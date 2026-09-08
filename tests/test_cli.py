import json

from graph2skill.cli import main


def test_build_command(tmp_path, examples_dir, capsys):
    code = main(["build", str(examples_dir / "isis"), "-o", str(tmp_path / "skill")])
    out = capsys.readouterr().out
    assert code == 0
    assert (tmp_path / "skill" / "SKILL.md").exists()
    assert "playbooks=" in out


def test_build_refuses_existing_output_without_force(tmp_path, examples_dir, capsys):
    target = tmp_path / "skill"
    assert main(["build", str(examples_dir / "isis"), "-o", str(target)]) == 0
    assert main(["build", str(examples_dir / "isis"), "-o", str(target)]) == 2
    assert "--force" in capsys.readouterr().err
    assert main(["build", str(examples_dir / "isis"), "-o", str(target), "--force"]) == 0


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
    assert main(["stats", str(examples_dir / "isis"), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["domain"] == "ISIS"
    assert payload["nodes"] == 17
    assert len(payload["playbooks"]) == payload["trees"]


def test_stats_text(examples_dir, capsys):
    assert main(["stats", str(examples_dir / "isis"), "--lang", "en"]) == 0
    assert "node types" in capsys.readouterr().out


def test_inspect_known_node(examples_dir, capsys):
    assert main(["inspect", str(examples_dir / "isis"), "--node-id", "cause:isis:isis-75700908-1"]) == 0
    out = capsys.readouterr().out
    assert "outgoing:" in out
    assert "playbook: references/playbooks/" in out


def test_inspect_falls_back_to_search(examples_dir, capsys):
    assert main(["inspect", str(examples_dir / "isis"), "--node-id", "LDP"]) == 0
    assert "candidate(s)" in capsys.readouterr().err


def test_missing_input_is_a_usage_error(tmp_path, capsys):
    assert main(["stats", str(tmp_path / "nope.json")]) == 2
    assert "no such file" in capsys.readouterr().err


def test_language_switch_produces_english_skill(tmp_path, examples_dir):
    main(["build", str(examples_dir / "isis"), "-o", str(tmp_path / "en"), "--lang", "en", "--name", "isis-en"])
    text = (tmp_path / "en" / "SKILL.md").read_text(encoding="utf-8")
    assert "name: isis-en" in text
    assert "When to use" in text


# --- skill set / merge commands ------------------------------------------------

def test_skillset_init_writes_a_manifest(house_dir, capsys):
    (house_dir / "skillset.json").unlink()
    assert main(["skillset", "init", str(house_dir)]) == 0
    out = capsys.readouterr().out
    assert "manifest written" in out
    payload = json.loads((house_dir / "skillset.json").read_text(encoding="utf-8"))
    assert [entry["name"] for entry in payload["skills"]] == ["common", "bgp"]


def test_skillset_init_flags_skills_without_a_graph(house_dir, capsys):
    (house_dir / "graphs" / "bgp.json").unlink()
    assert main(["skillset", "init", str(house_dir)]) == 1
    assert "还没有配对的 JSON 子图" in capsys.readouterr().err


def test_skillset_status_lists_faults(house_dir, capsys):
    assert main(["skillset", "status", "-s", str(house_dir / "skillset.json")]) == 0
    out = capsys.readouterr().out
    assert "故障10 BGP邻居状态异常（4 类根因）" in out
    assert "includes=common" in out


def test_merge_dry_run_writes_nothing(house_dir, examples_dir, capsys):
    before = (house_dir / "SKILL-bgp.md").read_text(encoding="utf-8")
    code = main(
        [
            "merge",
            str(examples_dir / "incoming" / "bgp-route-flap.json"),
            "--into",
            "bgp",
            "-s",
            str(house_dir / "skillset.json"),
            "--dry-run",
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "--dry-run" in out
    assert "+### 2.2 故障序号11：BGP路由震荡" in out
    assert (house_dir / "SKILL-bgp.md").read_text(encoding="utf-8") == before


def test_merge_applies_and_reports(house_dir, examples_dir, capsys):
    code = main(
        [
            "merge",
            str(examples_dir / "incoming" / "bgp-route-flap.json"),
            "--into",
            str(house_dir / "SKILL-bgp.md"),
        ]
    )
    out = capsys.readouterr().out
    assert code == 0
    assert "+ 故障序号11：BGP路由震荡" in out
    assert "~ 故障序号10：BGP邻居状态异常" in out
    assert "### 2.2 故障序号11：BGP路由震荡" in (house_dir / "SKILL-bgp.md").read_text(encoding="utf-8")


def test_merge_without_a_manifest_infers_the_skill_set(house_dir, examples_dir, capsys):
    (house_dir / "skillset.json").unlink()
    code = main(
        [
            "merge",
            str(examples_dir / "incoming" / "bgp-route-flap.json"),
            "--into",
            str(house_dir / "SKILL-bgp.md"),
        ]
    )
    err = capsys.readouterr().err
    assert code == 0
    assert "已按目录结构推断技能集" in err


def test_merge_unknown_skill_is_a_usage_error(house_dir, examples_dir, capsys):
    code = main(
        [
            "merge",
            str(examples_dir / "incoming" / "bgp-route-flap.json"),
            "--into",
            "ospf",
            "-s",
            str(house_dir / "skillset.json"),
        ]
    )
    assert code == 2
    assert "unknown skill" in capsys.readouterr().err


# --- steps / lint / models -----------------------------------------------------

def test_steps_writes_one_file_per_fault(tmp_path, examples_dir, capsys):
    code = main(
        [
            "steps",
            str(examples_dir / "skillset" / "graphs" / "bgp.json"),
            str(examples_dir / "incoming" / "bgp-route-flap.json"),
            "-d",
            str(tmp_path),
        ]
    )
    assert code == 0
    names = sorted(path.name for path in tmp_path.glob("*.md"))
    assert names == ["bgp-neighbor-abnormal.md", "bgp-route-flap.md"]
    assert "0 个错误" in capsys.readouterr().out


def test_steps_can_select_one_fault(tmp_path, examples_dir):
    main(
        [
            "steps",
            str(examples_dir / "skillset" / "graphs" / "bgp.json"),
            str(examples_dir / "incoming" / "bgp-route-flap.json"),
            "--fault",
            "11",
            "-o",
            str(tmp_path / "flap.md"),
        ]
    )
    text = (tmp_path / "flap.md").read_text(encoding="utf-8")
    assert text.startswith("---\nname: bgp-route-flap")
    assert "# 根因对照表" in text


def test_steps_unknown_fault_is_reported(tmp_path, examples_dir, capsys):
    code = main(["steps", str(examples_dir / "skillset" / "graphs" / "bgp.json"), "--fault", "99", "-d", str(tmp_path)])
    assert code == 1
    assert "没有找到可生成的故障" in capsys.readouterr().err


def test_steps_prompt_only_writes_the_prompt(tmp_path, examples_dir, capsys):
    target = tmp_path / "prompt.txt"
    code = main(
        ["steps", str(examples_dir / "incoming" / "bgp-route-flap.json"), "--fault", "11", "--prompt-only", str(target)]
    )
    assert code == 0
    text = target.read_text(encoding="utf-8")
    assert "===== SYSTEM =====" in text and "<证据包>" in text
    assert not list(tmp_path.glob("*.md"))


def test_steps_from_response_lints_an_external_answer(tmp_path, examples_dir, capsys):
    response = tmp_path / "answer.md"
    response.write_text("这不是一份合规的 skill", encoding="utf-8")
    code = main(
        [
            "steps",
            str(examples_dir / "incoming" / "bgp-route-flap.json"),
            "--fault",
            "11",
            "--from-response",
            str(response),
            "-o",
            str(tmp_path / "out.md"),
            "--strict",
        ]
    )
    assert code == 1
    assert "front-matter-missing" in capsys.readouterr().err


def test_lint_command_reports_errors(tmp_path, examples_dir, capsys):
    target = tmp_path / "skill.md"
    main(["steps", str(examples_dir / "incoming" / "bgp-route-flap.json"), "--fault", "11", "-o", str(target)])
    assert main(["lint", str(target)]) == 0
    broken = tmp_path / "broken.md"
    broken.write_text(target.read_text(encoding="utf-8").replace("# 根因对照表", "# 根因表"), encoding="utf-8")
    assert main(["lint", str(broken)]) == 1
    assert "sections" in capsys.readouterr().out


def test_lint_with_a_graph_flags_invented_commands(tmp_path, examples_dir, capsys):
    target = tmp_path / "skill.md"
    main(["steps", str(examples_dir / "incoming" / "bgp-route-flap.json"), "--fault", "11", "-o", str(target)])
    tampered = target.read_text(encoding="utf-8").replace(
        "`display interface GigabitEthernet0/1/0`", "`display interface brief all`"
    )
    target.write_text(tampered, encoding="utf-8")
    code = main(["lint", str(target), "--graph", str(examples_dir / "incoming" / "bgp-route-flap.json")])
    assert code == 1
    assert "command-not-in-source" in capsys.readouterr().out


def test_models_command_reports_missing_configuration(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("QWEN38_BASE_URL", raising=False)
    assert main(["models", "--env", str(tmp_path / "absent.env")]) == 0
    out = capsys.readouterr().out
    assert "qwen3.8-27b" in out
    assert "QWEN38_BASE_URL" in out


def test_models_command_shows_resolved_configuration(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("QWEN38_BASE_URL=http://model.internal:8000/v1\nQWEN38_API_KEY=sk-abcdefghijklmnop\n", encoding="utf-8")
    assert main(["models", "--model", "qwen3.8-27b", "--env", str(env)]) == 0
    out = capsys.readouterr().out
    assert "http://model.internal:8000/v1/chat/completions" in out
    assert "sk-abcdefghijklmnop" not in out  # masked


def test_no_install_launcher_runs(examples_dir):
    """run_graph2skill.py 必须能在未安装包的情况下跑起来。"""
    import subprocess
    import sys
    from pathlib import Path

    launcher = Path(__file__).resolve().parents[1] / "run_graph2skill.py"
    env = {"PATH": "/usr/bin:/bin", "SYSTEMROOT": "C:\\Windows"}
    proc = subprocess.run(
        [sys.executable, str(launcher), "stats", str(examples_dir / "isis")],
        capture_output=True,
        text=True,
        env=env,
        cwd="/",
    )
    assert proc.returncode == 0, proc.stderr
    assert "domain     : ISIS" in proc.stdout
