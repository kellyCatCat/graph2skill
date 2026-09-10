"""The skill this repository ships must itself be a valid skill directory."""

import re
import subprocess
import sys

import pytest

from tests.conftest import ROOT

SKILL_DIR = ROOT / "subkg-to-skill"
SKILL_MD = SKILL_DIR / "SKILL.md"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def frontmatter(skill_text) -> dict:
    assert skill_text.startswith("---\n"), "SKILL.md 必须以 YAML frontmatter 开头"
    block = skill_text.split("---\n", 2)[1]
    fields = {}
    for line in block.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def test_layout_is_skill_md_reference_scripts():
    entries = {path.name for path in SKILL_DIR.iterdir() if not path.name.startswith(".")}
    assert entries == {"SKILL.md", "reference", "scripts"}


def test_frontmatter_name_and_description(frontmatter):
    assert re.match(r"^[a-z0-9][a-z0-9-]*$", frontmatter["name"])
    assert frontmatter["name"] == "subkg-to-skill" == SKILL_DIR.name
    description = frontmatter["description"].strip('"')
    assert len(description) <= 1024
    # the description has to say both what it eats and what it produces
    assert "子图" in description and "skill" in description


def test_every_referenced_file_exists(skill_text):
    for target in re.findall(r"\]\((reference/[^)]+|scripts/[^)]+)\)", skill_text):
        assert (SKILL_DIR / target).exists(), f"SKILL.md 指向了不存在的文件：{target}"


def test_reference_docs_are_all_linked(skill_text):
    for path in (SKILL_DIR / "reference").glob("*.md"):
        assert f"reference/{path.name}" in skill_text, f"{path.name} 没有在 SKILL.md 里被引用"


def test_skill_md_stays_short(skill_text):
    assert len(skill_text.splitlines()) < 200


def test_documented_commands_run(tmp_path, example_dir):
    entry = SKILL_DIR / "scripts" / "build_skill.py"
    assert subprocess.run([sys.executable, str(entry), "inspect", str(example_dir)]).returncode == 0
    out = tmp_path / "generated"
    built = subprocess.run(
        [sys.executable, str(entry), "build", str(example_dir), "--out", str(out), "--name", "demo"],
        capture_output=True,
        text=True,
    )
    assert built.returncode == 0, built.stderr
    # the install lines the skill promises are printed for the user
    assert ".claude/skills/demo" in built.stdout and ".opencode/skill/demo" in built.stdout
    assert {path.name for path in out.iterdir()} == {"SKILL.md", "reference", "scripts"}


def test_scripts_need_no_third_party_imports():
    sources = list((SKILL_DIR / "scripts").rglob("*.py"))
    assert sources
    banned = re.compile(r"^\s*(?:import|from)\s+(requests|yaml|anthropic|openai|pydantic)\b", re.M)
    for path in sources:
        assert not banned.search(path.read_text(encoding="utf-8")), f"{path} 引入了第三方依赖"
