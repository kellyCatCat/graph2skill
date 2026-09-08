import json

import pytest

from graph2skill.skillset import SkillEntry, SkillSet, SkillSetError, missing_graphs


def test_discover_pairs_docs_graphs_and_includes(house_dir):
    skillset = SkillSet.discover(house_dir)
    names = [entry.name for entry in skillset.skills]
    assert names == ["common", "bgp"]
    bgp = skillset.get("bgp")
    assert bgp.doc == "SKILL-bgp.md"
    assert bgp.graph == "graphs/bgp.json"
    assert bgp.includes == ["common"]
    assert bgp.statistics == "statistics/bgp-neighbor-abnormal-stats.json"
    assert skillset.get("common").role == "common"
    assert not missing_graphs(skillset)


def test_discover_reports_skills_without_a_graph(house_dir):
    (house_dir / "graphs" / "bgp.json").unlink()
    skillset = SkillSet.discover(house_dir)
    assert [entry.name for entry in missing_graphs(skillset)] == ["bgp"]


def test_manifest_round_trip(house_dir, tmp_path):
    skillset = SkillSet.discover(house_dir)
    path = skillset.save(tmp_path / "manifest.json")
    reloaded = SkillSet.load(path)
    assert reloaded.to_dict() == skillset.to_dict()
    assert reloaded.root == house_dir.resolve()
    assert json.loads(path.read_text(encoding="utf-8"))["root"] == "skills"


def test_get_accepts_name_or_document_path(house_set, house_dir):
    assert house_set.get("bgp").name == "bgp"
    assert house_set.get("SKILL-bgp.md").name == "bgp"
    assert house_set.get(str(house_dir / "SKILL-bgp.md")).name == "bgp"


def test_unknown_skill_lists_known_names(house_set):
    with pytest.raises(SkillSetError) as excinfo:
        house_set.get("ospf")
    assert "bgp" in str(excinfo.value)


def test_included_entries_are_transitive_and_deduplicated():
    skillset = SkillSet(
        root=".",
        skills=[
            SkillEntry(name="base", doc="base.md", role="common"),
            SkillEntry(name="common", doc="common.md", role="common", includes=["base"]),
            SkillEntry(name="bgp", doc="SKILL-bgp.md", includes=["common", "base"]),
        ],
    )
    assert [entry.name for entry in skillset.included_entries(skillset.get("bgp"))] == ["common", "base"]


def test_missing_manifest_is_a_clear_error(tmp_path):
    with pytest.raises(SkillSetError) as excinfo:
        SkillSet.load(tmp_path)
    assert "skillset init" in str(excinfo.value)


def test_graph_path_error_names_the_skill():
    skillset = SkillSet(root=".", skills=[SkillEntry(name="bgp", doc="SKILL-bgp.md")])
    with pytest.raises(SkillSetError) as excinfo:
        skillset.graph_path(skillset.get("bgp"))
    assert "bgp" in str(excinfo.value)


def test_discover_rejects_a_directory_without_skills(tmp_path):
    with pytest.raises(SkillSetError):
        SkillSet.discover(tmp_path)


def test_manifest_is_json_and_relative(house_dir):
    skillset = SkillSet.discover(house_dir)
    payload = json.loads(skillset.save().read_text(encoding="utf-8"))
    assert payload["root"] == "."
    assert all(not entry["doc"].startswith("/") for entry in payload["skills"])
