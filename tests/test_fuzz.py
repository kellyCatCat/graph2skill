"""Random subgraphs through the whole pipeline, held to invariants.

The generator throws what extraction produces at the tool: names with pipes,
backticks, line breaks and "步骤 3" in them, junk command templates, empty and
oddly typed attributes, conditions on every kind of edge, cycles, units that
overlap, case-specific nodes.  For every graph:

* nothing raises;
* the skill ``build`` writes passes its own template check and grounding check;
* building twice gives the same files;
* every root-cause table row has four cells;
* every candidate cause either reaches the document or is reported as left out.

A failure names its seed; ``random_graph(random.Random(seed))`` rebuilds the graph.
"""

import contextlib
import io
import json
import random
import re

import pytest

from subkg2skill import schema
from subkg2skill.cli import main
from subkg2skill.playbook import fault_key

from tests.conftest import make_edge, make_node

SEEDS = range(150)

UNITS = ["17.4.1", "17.4.3", "tree:r159", "case:loop-001", ""]
NASTY = ["|", "〔", "〕", "：", "；", "`", "\n", "（", "）", "「", "」", "步骤 3", "<br>", "*", "#"]
NAMES = {
    "symptom": ["IS-IS邻居无法建立", "ISIS邻居无法建立", "端口不通", "BGP邻居震荡", "设备异常重启"],
    "cause": ["两端接口MTU不一致", "Level不匹配", "System ID冲突", "认证方式不匹配", "光模块故障", "链路拥塞"],
    "check": ["查看IS-IS邻居", "查看接口状态", "查看告警日志", "查看配置"],
    "observation": ["邻居停留在Init", "接口为 Up", "存在 LOS 告警", "配置项不存在"],
    "repair": ["修改MTU", "更换光模块", "修正配置"],
    "escalation": ["收集信息并转技术支持"],
}
COMMANDS = [
    "display isis peer", "display isis peer verbose", "display interface {interface-type}{interface-number}",
    "display current-config config bgp", "display current-configuration configuration bgp",
    "display alarm active", "Peer : <ip>", "1.3.1  4  100  0  0  0  0965h20m  Connect  0",
    "mtu {mtu-value}", "undo shutdown", "isis circuit-level [level-1 | level-2]", "slot 3 reset",
    "<HUAWEI> display version", "display interface GE0/1/0",
]


def _name(rng, kind):
    text = rng.choice(NAMES[kind])
    roll = rng.random()
    if roll < 0.15:
        text += rng.choice(NASTY) + "补充"
    elif roll < 0.2:
        text += "很长的描述" * rng.randint(20, 60)
    return text


def _condition(rng, depth=0):
    roll = rng.random()
    if roll < 0.55 or depth > 2:
        return None
    if roll < 0.7:
        return {"type": "text", "expression": "条件" + rng.choice(NASTY + ["A", "B"]) + "成立"}
    if roll < 0.85:
        return {"type": "atomic", "object": "接口", "field": rng.choice(["MTU", "状态|x"]),
                "operator": rng.choice(["eq", "ne", "gt", "exists", "weird"]),
                "value": rng.choice([1500, "Up", None, [1, 2], True])}
    kind = rng.choice(["and", "or", "not"])
    if kind == "not":
        return {"type": "not", "condition": _condition(rng, depth + 1) or {"type": "text", "expression": "X"}}
    return {"type": kind, "conditions": [_condition(rng, depth + 1) or {"type": "text", "expression": "Y"}
                                         for _ in range(rng.randint(1, 3))]}


def _attrs(rng, kind):
    attrs = {}
    if kind in ("check", "repair", "escalation"):
        if rng.random() < 0.8:
            attrs["command_templates"] = rng.sample(COMMANDS, rng.randint(0, 3))
        if rng.random() < 0.4:
            attrs["procedure"] = rng.choice([[], ["按手册操作"], "单个字符串步骤", None])
    if kind == "repair":
        for key in ("service_impact", "rollback"):
            attrs[key] = rng.choice([None, "", "业务中断" + rng.choice(NASTY)])
        attrs["preconditions"] = rng.choice([[], ["已获得变更窗口"], None, "字符串前置"])
    if kind == "observation":
        attrs["normalized_expression"] = rng.choice([None, "状态 == 'Init'", "值" + rng.choice(NASTY)])
        attrs["field"] = rng.choice([None, "邻居状态", "字段|管道"])
        attrs["operator"] = rng.choice([None, "eq", "not_eq", "trend"])
        attrs["value"] = rng.choice([None, "Init", 3, [1, "a"], False])
    if kind == "symptom":
        attrs["required_slots"] = rng.choice([[], ["neName"], ["peer ip c8be5e6454"], ["device B"]])
        attrs["match_phrases"] = rng.choice([[], ["邻居Down"]])
    if rng.random() < 0.15:
        attrs["example_specific"] = True
    return attrs


def random_graph(rng):
    nodes, ids = [], {kind: [] for kind in schema.NODE_TYPES}
    counts = {"symptom": rng.randint(1, 4), "cause": rng.randint(0, 8), "check": rng.randint(0, 6),
              "observation": rng.randint(0, 6), "repair": rng.randint(0, 4), "escalation": rng.randint(0, 2)}
    for kind, count in counts.items():
        for index in range(count):
            node_id = f"{kind}_{index}"
            ids[kind].append(node_id)
            unit = rng.choice(UNITS)
            nodes.append(make_node(node_id, kind, _name(rng, kind), attrs=_attrs(rng, kind),
                                   diagnostic_contexts=[{"section": unit, "title": unit}] if unit else []))
    edges = []
    # Sorted: the schema keeps endpoint pairs in frozensets, whose order is per process.
    allowed = sorted((t, s, d) for t, (pairs, _label, _meaning) in schema.EDGE_RULES.items() for s, d in pairs)
    for index in range(rng.randint(len(nodes), len(nodes) * 3)):
        edge_type, source_kind, target_kind = rng.choice(allowed)
        if not ids[source_kind] or not ids[target_kind]:
            continue
        unit = rng.choice(UNITS)
        condition = _condition(rng)
        edges.append(make_edge(
            f"e{index}", edge_type, rng.choice(ids[source_kind]), rng.choice(ids[target_kind]),
            rank=rng.choice([None, 1, 2, 3]),
            diagnostic_context={"section": unit, "title": unit} if unit else {},
            condition=condition,
            condition_status="unconditional" if condition is None else rng.choice(
                ["parsed", "text_only", "requires_interpretation", "preserved_unparsed"]),
            original_condition=rng.choice([None, {"type": "atomic", "var": "v", "comparison": ">", "other": "x=1"}]),
        ))
    return nodes, edges


def _run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue() + err.getvalue()


def _files(path):
    return {str(p.relative_to(path)): p.read_text(encoding="utf-8") for p in sorted(path.rglob("*.md"))}


@pytest.mark.parametrize("seed", SEEDS)
def test_random_subgraph_holds_every_invariant(seed, tmp_path, write_bundle):
    nodes, edges = random_graph(random.Random(seed))
    directory = write_bundle(nodes, edges)
    where = f"seed={seed}"

    code, output = _run(["inspect", str(directory)])
    assert code in (0, 1), (where, output)
    for argv in (["list", str(directory), "--show-causes", "--suggest-merge"], ["plan", str(directory)]):
        code, output = _run(argv)
        assert code == 0, (where, argv[0], output)

    code, output = _run(["build", str(directory), "--each", "--out", str(tmp_path / "each"), "--min-causes", "0"])
    assert code in (0, 2), (where, "build --each", output)
    if code == 0:
        for skill in (tmp_path / "each").iterdir():
            checked, report = _run(["check", str(skill), "--graph", str(directory)])
            assert checked == 0, (where, skill.name, report)

    names = tmp_path / "names.json"
    symptoms = [node["node_id"] for node in nodes if node["node_type"] == "symptom"]
    names.write_text(json.dumps({node_id: f"scen-{i}" for i, node_id in enumerate(symptoms)}), encoding="utf-8")
    runs = []
    for attempt in (1, 2):
        out = tmp_path / f"build{attempt}"
        code, output = _run(["build", str(directory), "--name", "fz", "--names", str(names),
                             "--out", str(out), "--min-causes", "0"])
        runs.append((code, output, out))
    code, output, out = runs[0]
    if code == 2:
        assert "没有可生成的故障场景" in output or "没有 symptom" in output, (where, output)
        return
    assert code == 0, (where, "build", output)
    first = _files(out)
    assert first == _files(runs[1][2]), (where, "两次构建输出不同")

    checked, report = _run(["check", str(out), "--graph", str(directory)])
    assert checked == 0, (where, report)

    for path, text in first.items():
        for line in text.splitlines():
            if line.startswith("| ") and not line.startswith("| ---"):
                cells = re.split(r"(?<!\\)\|", line)[1:-1]
                assert len(cells) in (3, 4), (where, path, line)

    body = "\n".join(first.values())
    by_id = {node["node_id"]: node for node in nodes}
    for edge in edges:
        if edge["edge_type"] != "has_cause":
            continue
        cause = " ".join(by_id[edge["target"]]["name"].split()).replace("`", "'")
        assert cause in body or cause in output or fault_key(cause) in fault_key(body), (
            where, f"原因「{cause[:40]}」既不在文档里也不在构建输出里")
