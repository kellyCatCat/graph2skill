"""Installing and updating the skill in place."""

import json
import subprocess
import sys

from tests.conftest import ROOT

INSTALL = ROOT / "install.py"


def run(home, *args, cwd=None):
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin"}
    return subprocess.run(
        [sys.executable, str(INSTALL), *args],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or ROOT),
    )


def claude_dir(home):
    return home / ".claude" / "skills" / "subkg-to-skill"


def opencode_dir(home):
    return home / ".config" / "opencode" / "skill" / "subkg-to-skill"


def test_installs_the_whole_skill(tmp_path):
    result = run(tmp_path)
    assert result.returncode == 0, result.stderr
    target = claude_dir(tmp_path)
    assert (target / "SKILL.md").exists()
    assert (target / "reference" / "skill-template.md").exists()
    assert (target / "scripts" / "build_skill.py").exists()
    assert (target / ".claude-plugin" / "plugin.json").exists()


def test_python_caches_are_not_copied(tmp_path):
    run(tmp_path)
    assert not list(claude_dir(tmp_path).rglob("__pycache__"))


def test_rerunning_is_the_update(tmp_path):
    run(tmp_path)
    stale = claude_dir(tmp_path) / "reference" / "gone.md"
    stale.write_text("旧文件", encoding="utf-8")
    assert run(tmp_path).returncode == 0
    assert not stale.exists(), "重装应整体替换，不该留下已删除的文件"
    assert (claude_dir(tmp_path) / "SKILL.md").exists()


def test_both_targets(tmp_path):
    assert run(tmp_path, "--target", "all").returncode == 0
    assert (claude_dir(tmp_path) / "SKILL.md").exists()
    assert (opencode_dir(tmp_path) / "SKILL.md").exists()


def test_link_mode_points_at_the_repo(tmp_path):
    assert run(tmp_path, "--link").returncode == 0
    target = claude_dir(tmp_path)
    assert target.is_symlink()
    assert target.resolve() == (ROOT / "subkg-to-skill").resolve()


def test_link_can_be_replaced_by_a_copy(tmp_path):
    run(tmp_path, "--link")
    assert run(tmp_path).returncode == 0
    assert not claude_dir(tmp_path).is_symlink()


def test_project_scope(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assert run(tmp_path, "--project", cwd=workspace).returncode == 0
    assert (workspace / ".claude" / "skills" / "subkg-to-skill" / "SKILL.md").exists()


def test_refuses_to_replace_a_stranger(tmp_path):
    target = claude_dir(tmp_path)
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: someone-else\n---\n", encoding="utf-8")
    result = run(tmp_path)
    assert result.returncode == 1
    assert "--force" in result.stdout
    assert "someone-else" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_force_replaces_a_stranger(tmp_path):
    target = claude_dir(tmp_path)
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: someone-else\n---\n", encoding="utf-8")
    assert run(tmp_path, "--force").returncode == 0
    assert "subkg-to-skill" in (target / "SKILL.md").read_text(encoding="utf-8")


def test_dry_run_changes_nothing(tmp_path):
    result = run(tmp_path, "--dry-run")
    assert result.returncode == 0
    assert "将安装到" in result.stdout
    assert not claude_dir(tmp_path).exists()


def test_uninstall(tmp_path):
    run(tmp_path, "--target", "all")
    assert run(tmp_path, "--target", "all", "--uninstall").returncode == 0
    assert not claude_dir(tmp_path).exists()
    assert not opencode_dir(tmp_path).exists()


def test_uninstall_is_idempotent(tmp_path):
    result = run(tmp_path, "--uninstall")
    assert result.returncode == 0 and "未安装" in result.stdout


def test_installed_skill_still_runs(tmp_path, example_dir):
    run(tmp_path)
    entry = claude_dir(tmp_path) / "scripts" / "build_skill.py"
    out = tmp_path / "generated"
    built = subprocess.run(
        [sys.executable, str(entry), "build", str(example_dir),
         "--entry", "symptom_7f1c", "--name", "demo", "--out", str(out)],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    assert (out / "SKILL.md").exists()


# -- plugin / marketplace manifests --------------------------------------
def test_plugin_manifest_matches_the_skill():
    manifest = json.loads(
        (ROOT / "subkg-to-skill" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    assert manifest["name"] == "subkg-to-skill"
    skill = (ROOT / "subkg-to-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert f"name: {manifest['name']}" in skill
    assert manifest["description"]


def test_marketplace_points_at_the_plugin():
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert market["name"] and market["owner"]["name"]
    entry = next(p for p in market["plugins"] if p["name"] == "subkg-to-skill")
    assert entry["source"] == "./subkg-to-skill"
    assert (ROOT / entry["source"].lstrip("./") / ".claude-plugin" / "plugin.json").exists()
