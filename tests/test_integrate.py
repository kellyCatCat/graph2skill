import json

import pytest

from graph2skill.integrate import build_common_index, integrate
from graph2skill.mdsection import MarkdownDoc
from graph2skill.skillset import SkillSetError


def _doc(house_dir, name="SKILL-bgp.md"):
    return (house_dir / name).read_text(encoding="utf-8")


def _section_text(text, number):
    doc = MarkdownDoc(text)
    section = doc.find_section(number=number)
    return "\n".join(doc.lines[section.start : section.end])


def test_new_fault_is_inserted_in_house_style(house_set, house_dir, incoming_graph):
    integrate(house_set, "bgp", [incoming_graph]).apply()
    text = _doc(house_dir)
    assert "### 2.2 故障序号11：BGP路由震荡" in text
    assert "标识: bgp-route-flap | 分类: 路由协议类 | 根因: 3类" in text
    assert "| 1 | 承载链路震荡 | Carrier Link Flapping | 接口频繁 UP/DOWN |" in text
    assert "→ 诊断决策树与详细根因分析详见：reference/fault-11-bgp-route-flap.md" in text


def test_existing_fault_gains_rows_and_updates_its_meta_line(house_set, house_dir, incoming_graph):
    integrate(house_set, "bgp", [incoming_graph]).apply()
    section = _section_text(_doc(house_dir), "2.1")
    assert "根因: 5类" in section
    assert "| 5 | 路径MTU不一致导致会话重建 | Path MTU Mismatch | 大报文丢弃、会话周期性重建 |" in section
    assert "| 1 | BGP邻居数量超限 | Number of BGP neighbors exceeds the limit | 见reference决策树 |" in section


def test_handwritten_sections_are_untouched(house_set, house_dir, incoming_graph):
    before = _doc(house_dir)
    integrate(house_set, "bgp", [incoming_graph]).apply()
    after = _doc(house_dir)
    for number in ("1", "1.1", "4", "4.1"):
        assert _section_text(before, number) == _section_text(after, number)
    assert before.split("## 1.")[0] == after.split("## 1.")[0]  # title + 引用通用规范


def test_merge_is_idempotent(house_set, house_dir, incoming_graph):
    integrate(house_set, "bgp", [incoming_graph]).apply()
    snapshot = {path.name: path.read_text(encoding="utf-8") for path in house_dir.rglob("*") if path.is_file()}
    second = integrate(house_set, "bgp", [incoming_graph])
    assert second.pending == []
    assert {path.name: path.read_text(encoding="utf-8") for path in house_dir.rglob("*") if path.is_file()} == snapshot


def test_common_nodes_are_referenced_not_copied(house_set, house_dir, tmp_path):
    """A subgraph that carries its own copy of a common node must not duplicate it."""
    payload = {
        "graphId": "EXTRA",
        "nodes": [
            {"id": "fault:bgp:12", "type": "FAULT", "name": "BGP会话被复位",
             "data": {"faultId": 12, "identifier": "bgp-session-reset", "category": "路由协议类"}},
            {"id": "cause:bgp:port", "type": "CAUSE", "name": "端口异常", "data": {"nameEn": "Port Failure"}},
            {"id": "common:infra:port-down", "type": "FAULT", "name": "物理端口故障（重复定义）",
             "data": {"section": "3.1"}},
        ],
        "relations": [
            {"id": "x1", "type": "HAS_CAUSE", "from": "fault:bgp:12", "to": "cause:bgp:port"},
            {"id": "x2", "type": "REFERENCES", "from": "cause:bgp:port", "to": "common:infra:port-down"},
        ],
    }
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    report = integrate(house_set, "bgp", [str(path)])
    assert "common:infra:port-down" in report.common_owned
    report.apply()

    graph = json.loads((house_dir / "graphs" / "bgp.json").read_text(encoding="utf-8"))
    ids = {node["id"] for node in graph["nodes"]}
    assert "common:infra:port-down" not in ids  # owned by common.md
    assert {relation["to"] for relation in graph["relations"]} >= {"common:infra:port-down"}  # reference kept
    reference = (house_dir / "reference" / "fault-12-bgp-session-reset.md").read_text(encoding="utf-8")
    assert "底层下钻：见 common.md §3.1 物理端口故障" in reference
    assert "物理端口故障（重复定义）" not in reference


def test_common_jump_table_gains_only_missing_sections(house_set, house_dir, tmp_path):
    """A jump target already covered by a hand-written row is not duplicated."""
    common_path = house_dir / "graphs" / "common.json"
    common = json.loads(common_path.read_text(encoding="utf-8"))
    common["nodes"].append(
        {"id": "common:infra:qos", "type": "FAULT", "name": "QoS拥塞故障", "data": {"section": "3.9"}}
    )
    common_path.write_text(json.dumps(common, ensure_ascii=False), encoding="utf-8")

    payload = {
        "graphId": "EXTRA",
        "nodes": [
            {"id": "fault:bgp:13", "type": "FAULT", "name": "BGP选路异常",
             "data": {"faultId": 13, "identifier": "bgp-path-selection"}},
            {"id": "cause:bgp:optical", "type": "CAUSE", "name": "光模块异常"},
            {"id": "cause:bgp:congestion", "type": "CAUSE", "name": "链路拥塞丢弃"},
        ],
        "relations": [
            {"id": "y1", "type": "HAS_CAUSE", "from": "fault:bgp:13", "to": "cause:bgp:optical"},
            {"id": "y2", "type": "HAS_CAUSE", "from": "fault:bgp:13", "to": "cause:bgp:congestion"},
            {"id": "y3", "type": "REFERENCES", "from": "cause:bgp:optical", "to": "common:infra:optical-module"},
            {"id": "y4", "type": "REFERENCES", "from": "cause:bgp:congestion", "to": "common:infra:qos"},
        ],
    }
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    integrate(house_set, "bgp", [str(path)]).apply()

    table = _section_text(_doc(house_dir), "3")
    assert table.count("§3.4") == 1  # already covered by the hand-written 光模块/光功率 row
    assert "| 链路拥塞丢弃 | §3.9 | QoS拥塞故障 |" in table


def test_common_reference_without_a_section_is_reported(house_set, house_dir, tmp_path):
    common_path = house_dir / "graphs" / "common.json"
    common = json.loads(common_path.read_text(encoding="utf-8"))
    common["nodes"].append({"id": "common:infra:unknown", "type": "FAULT", "name": "尚未编号的公共故障"})
    common_path.write_text(json.dumps(common, ensure_ascii=False), encoding="utf-8")

    payload = {
        "graphId": "EXTRA",
        "nodes": [
            {"id": "fault:bgp:14", "type": "FAULT", "name": "BGP异常X", "data": {"faultId": 14, "identifier": "bgp-x"}},
            {"id": "cause:bgp:x", "type": "CAUSE", "name": "未知方向"},
        ],
        "relations": [
            {"id": "z1", "type": "HAS_CAUSE", "from": "fault:bgp:14", "to": "cause:bgp:x"},
            {"id": "z2", "type": "REFERENCES", "from": "cause:bgp:x", "to": "common:infra:unknown"},
        ],
    }
    path = tmp_path / "extra.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report = integrate(house_set, "bgp", [str(path)])
    assert any("未能定位章节号" in warning for warning in report.warnings)


def test_reference_file_keeps_handwritten_content(house_set, house_dir, incoming_graph):
    before = (house_dir / "reference" / "fault-10-bgp-neighbor.md").read_text(encoding="utf-8")
    integrate(house_set, "bgp", [incoming_graph]).apply()
    after = (house_dir / "reference" / "fault-10-bgp-neighbor.md").read_text(encoding="utf-8")
    assert after.startswith(before.rstrip("\n"))
    assert "<!-- graph2skill:begin id=causes -->" in after
    assert "## 根因5：路径MTU不一致导致会话重建" in after
    assert after.count("## 根因4：告警") == 1


def test_new_reference_file_is_created_with_decision_tree(house_set, house_dir, incoming_graph):
    integrate(house_set, "bgp", [incoming_graph]).apply()
    text = (house_dir / "reference" / "fault-11-bgp-route-flap.md").read_text(encoding="utf-8")
    assert text.startswith("# 故障11：BGP路由震荡 — 诊断决策树")
    assert "display bgp routing-table flap-info" in text
    assert "处理：调整路由衰减参数" in text
    assert "底层下钻：见 common.md §3.25 LDP故障" in text


def test_paired_graph_is_written_back(house_set, house_dir, incoming_graph):
    integrate(house_set, "bgp", [incoming_graph]).apply()
    graph = json.loads((house_dir / "graphs" / "bgp.json").read_text(encoding="utf-8"))
    ids = {node["id"] for node in graph["nodes"]}
    assert "fault:bgp:11" in ids and "cause:bgp:mtu" in ids
    assert graph["entryNodeIds"] == ["fault:bgp:10", "fault:bgp:11"]
    assert "graphIds" not in json.dumps(graph)  # bookkeeping stays out of the source file


def test_graph_write_back_can_be_disabled(house_set, house_dir, incoming_graph):
    before = (house_dir / "graphs" / "bgp.json").read_text(encoding="utf-8")
    integrate(house_set, "bgp", [incoming_graph], update_graph=False).apply()
    assert (house_dir / "graphs" / "bgp.json").read_text(encoding="utf-8") == before


def test_report_lists_changes_without_writing(house_set, house_dir, incoming_graph):
    before = _doc(house_dir)
    report = integrate(house_set, "bgp", [incoming_graph])
    assert {path.name for path in (change.path for change in report.pending)} == {
        "bgp.json",
        "SKILL-bgp.md",
        "fault-10-bgp-neighbor.md",
        "fault-11-bgp-route-flap.md",
    }
    assert "+### 2.2 故障序号11：BGP路由震荡" in report.diff()
    assert _doc(house_dir) == before  # nothing written yet


def test_resync_without_incoming_files_is_a_noop(house_set, incoming_graph):
    integrate(house_set, "bgp", [incoming_graph]).apply()
    assert integrate(house_set, "bgp", []).pending == []


def test_statistics_are_updated_only_when_asked(house_set, house_dir, incoming_graph):
    stats = house_dir / "statistics" / "bgp-neighbor-abnormal-stats.json"
    integrate(house_set, "bgp", [incoming_graph]).apply()
    assert json.loads(stats.read_text(encoding="utf-8"))["causeCount"] == 4
    integrate(house_set, "bgp", [incoming_graph], update_statistics=True).apply()
    assert json.loads(stats.read_text(encoding="utf-8"))["causeCount"] == 5


def test_missing_fault_section_is_reported_not_fatal(house_set, house_dir, incoming_graph):
    doc = house_dir / "SKILL-bgp.md"
    text = doc.read_text(encoding="utf-8")
    doc.write_text(text.split("## 2.")[0] + text.split("## 3.")[1], encoding="utf-8")
    report = integrate(house_set, "bgp", [incoming_graph])
    assert any("未找到承载故障小节" in warning for warning in report.warnings)


def test_unknown_target_skill_raises(house_set, incoming_graph):
    with pytest.raises(SkillSetError):
        integrate(house_set, "ospf", [incoming_graph])


def test_common_index_maps_nodes_to_sections(house_set):
    index, warnings = build_common_index(house_set, house_set.get("bgp"))
    assert not warnings
    assert index.by_id("common:infra:link-fault").section == "3.6"
    assert index.by_name("OSPF状态异常").section == "3.8"
    assert index.by_name("自检清单").section == "3.31"  # from common.md headings alone
