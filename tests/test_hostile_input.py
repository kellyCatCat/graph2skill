"""Source text that looks like document syntax must not change the document's structure.

Names, expressions and conditions come from extraction: they can hold a pipe,
a backtick, a line break, a 」, or the words "步骤 3".  Each case here was found
by running random subgraphs through the whole pipeline; the generated skill
must still pass its own template check and grounding check.
"""

import json
import os
import subprocess
import sys

from subkg2skill.cli import main
from subkg2skill.loader import load

from tests.conftest import ROOT, make_edge, make_node

SCRIPT = ROOT / "subkg-to-skill" / "scripts" / "build_skill.py"


def _graph(symptom="端口不通", cause="配置错误", check="查看配置", expression="配置项不存在", extra=()):
    nodes = [
        make_node("symptom_a", "symptom", symptom),
        make_node("cause_b", "cause", cause),
        make_node("check_c", "check", check, attrs={"command_templates": ["display config"]}),
        make_node("obs_d", "observation", "配置缺失", attrs={"normalized_expression": expression}),
        make_node("repair_e", "repair", "修正配置", attrs={"command_templates": ["undo shutdown"]}),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge("e2", "diagnosed_by", "symptom_a", "check_c"),
        make_edge("e3", "observes", "check_c", "obs_d"),
        make_edge("e4", "supports", "obs_d", "cause_b"),
        make_edge("e5", "repaired_by", "cause_b", "repair_e"),
    ]
    for node, edge in extra:
        if node:
            nodes.append(node)
        if edge:
            edges.append(edge)
    return nodes, edges


def _build_and_check(tmp_path, write_bundle, nodes, edges, *extra, capsys=None):
    directory = write_bundle(nodes, edges)
    out = tmp_path / "skill"
    code = main(["build", str(directory), "--name", "x", "--out", str(out), "--force", *extra])
    checked = main(["check", str(out), "--graph", str(directory)])
    return code, checked, out


def _text(out):
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(out.rglob("*.md")))


def test_a_line_break_in_a_name_stays_one_line(tmp_path, write_bundle):
    nodes, edges = _graph(cause="链路拥塞\n补充")
    code, checked, out = _build_and_check(tmp_path, write_bundle, nodes, edges)
    assert (code, checked) == (0, 0)
    assert "链路拥塞 补充" in _text(out)


def test_a_pipe_in_names_and_expressions_does_not_break_a_table(tmp_path, write_bundle):
    nodes, edges = _graph(cause="配置|错误", expression="状态|x == 1")
    code, checked, out = _build_and_check(tmp_path, write_bundle, nodes, edges)
    assert (code, checked) == (0, 0)
    assert "| 配置\\|错误 |" in _text(out)


def test_a_backtick_in_source_text_does_not_open_a_code_span(tmp_path, write_bundle):
    nodes, edges = _graph(cause="Level`不匹配", expression="值`补充")
    code, checked, out = _build_and_check(tmp_path, write_bundle, nodes, edges)
    assert (code, checked) == (0, 0)
    assert "Level'不匹配" in _text(out) and "`值'补充`" in _text(out)


def test_step_words_inside_names_are_not_jumps(tmp_path, write_bundle):
    nodes, edges = _graph(symptom="异常步骤 3", cause="配置错误步骤 9", check="查看步骤 7")
    code, checked, out = _build_and_check(tmp_path, write_bundle, nodes, edges)
    assert (code, checked) == (0, 0)


def test_a_closing_bracket_inside_a_handoff_target_is_read_whole(tmp_path, write_bundle):
    escalation = make_node("escalation_f", "escalation", "转技术支持」补充")
    nodes, edges = _graph(extra=[(escalation, make_edge("e9", "refers_to", "symptom_a", "escalation_f"))])
    code, checked, out = _build_and_check(tmp_path, write_bundle, nodes, edges)
    assert (code, checked) == (0, 0)
    assert "转交：「转技术支持」补充」" in _text(out)


def test_a_long_handoff_is_never_cut_through_its_skill_name(tmp_path, write_bundle):
    target = make_node("symptom_z", "symptom", "很长的故障名" * 80)
    nodes, edges = _graph(extra=[(target, make_edge("e9", "leads_to", "cause_b", "symptom_z"))])
    index = tmp_path / "index.json"
    index.write_text(json.dumps({"symptom_z": "long-fault"}), encoding="utf-8")
    code, checked, out = _build_and_check(
        tmp_path, write_bundle, nodes, edges, "--entry", "symptom_a", "--skill-index", str(index)
    )
    assert (code, checked) == (0, 0)
    assert "（skill: long-fault）" in _text(out)


def test_supports_and_confirms_of_one_reading_make_one_row(tmp_path, write_bundle):
    nodes, edges = _graph(extra=[(None, make_edge("e9", "confirms", "obs_d", "cause_b"))])
    code, checked, out = _build_and_check(tmp_path, write_bundle, nodes, edges)
    assert (code, checked) == (0, 0)
    step = _text(out).split("## 步骤1", 1)[1].split("# 根因对照表", 1)[0]
    assert step.count("`配置项不存在`：") == 1
    assert "仅支持性证据" not in step  # the strongest verdict wins


def test_scenario_files_are_listed_even_when_no_scenario_has_steps(tmp_path, write_bundle):
    nodes = [make_node("symptom_a", "symptom", "端口不通"), make_node("symptom_b", "symptom", "设备重启")]
    names = tmp_path / "names.json"
    names.write_text(json.dumps({"symptom_a": "port-down", "symptom_b": "reboot"}), encoding="utf-8")
    code, checked, out = _build_and_check(
        tmp_path, write_bundle, nodes, [], "--names", str(names), "--min-causes", "0"
    )
    assert (code, checked) == (0, 0)
    index = (out / "SKILL.md").read_text(encoding="utf-8")
    assert "reference/port-down.md" in index and "reference/reboot.md" in index


def test_the_document_does_not_depend_on_the_process_hash_seed(tmp_path, write_bundle):
    # One observation produced by two checks: which one a step cites used to
    # follow set iteration order, which changes with PYTHONHASHSEED.
    second = make_node("check_g", "check", "再查配置", attrs={"command_templates": ["display config all"]})
    nodes, edges = _graph(
        extra=[
            (second, make_edge("e9", "diagnosed_by", "symptom_a", "check_g")),
            (None, make_edge("e10", "observes", "check_g", "obs_d")),
            (make_node("cause_h", "cause", "协议未使能"), make_edge("e11", "has_cause", "symptom_a", "cause_h")),
            (None, make_edge("e12", "supports", "obs_d", "cause_h")),
        ]
    )
    directory = write_bundle(nodes, edges)
    outputs = []
    for seed in ("1", "2", "3"):
        out = tmp_path / f"h{seed}"
        subprocess.run(
            [sys.executable, str(SCRIPT), "build", str(directory), "--name", "x", "--out", str(out)],
            env={**os.environ, "PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
        )
        outputs.append(_text(out))
    assert outputs[0] == outputs[1] == outputs[2]


def test_a_manifest_saved_next_to_the_export_is_not_read_as_graph_data(write_bundle):
    nodes, edges = _graph()
    directory = write_bundle(nodes, edges)
    (directory / "scenarios.json").write_text(json.dumps({"name": "x", "scenarios": []}), encoding="utf-8")
    bundle, sources = load([str(directory)])
    assert not any("scenarios.json" in source for source in sources)
    assert any("scenarios.json" in skipped for skipped in bundle.skipped)
    assert len(bundle.nodes) == len(nodes)
