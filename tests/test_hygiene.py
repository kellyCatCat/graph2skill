"""Telling a command from what an extractor mistook for one.

Two jobs: recognise one command written at two abbreviations, and refuse to put
screen output, table rows or prose into a document as something to type.
"""

from __future__ import annotations

import pytest

from subkg2skill import hygiene
from subkg2skill.graph import Graph
from subkg2skill.loader import RawBundle
from subkg2skill.lint import lint_text
from subkg2skill.playbook import build_merged_playbook, build_playbook
from subkg2skill.template import (
    build_doc,
    command_templates,
    command_variants,
    param_key,
    render_doc,
    same_command_set,
)

from conftest import make_edge, make_node


# ---------------------------------------------------------------- identity
def test_abbreviated_and_spelled_out_forms_are_one_command():
    assert hygiene.same_command(
        "display current-config config bgp",
        "display current-configuration configuration bgp",
    )


def test_a_different_keyword_is_not_an_abbreviation():
    assert not hygiene.same_command("display bgp peer", "display isis peer")


def test_token_counts_must_line_up():
    assert not hygiene.same_command("display bgp peer", "display bgp peer verbose")


def test_parameter_names_must_match_exactly():
    # <interface-name> and <interface> are different slots, not one abbreviated.
    assert not hygiene.same_command(
        "display interface <interface-name>", "display interface <interface>"
    )


def test_numbers_are_never_folded_by_prefix():
    assert not hygiene.same_command("display isis 1 peer", "display isis 100 peer")


def test_two_letter_stubs_do_not_fold():
    # `dis` is a real abbreviation; `di` would fold far too much.
    assert hygiene.same_command("dis bgp peer", "display bgp peer")
    assert not hygiene.same_command("di bgp peer", "display bgp peer")


def test_the_longest_spelling_wins():
    forms = hygiene.merge_forms(
        ["display current-config config bgp", "display current-configuration configuration bgp"]
    )
    assert len(forms) == 1
    assert forms[0].command == "display current-configuration configuration bgp"
    assert forms[0].variants == ["display current-config config bgp"]


def test_unrelated_commands_stay_apart():
    forms = hygiene.merge_forms(["display bgp peer", "display isis peer"])
    assert len(forms) == 2
    assert all(not form.has_conflict for form in forms)


def test_command_sets_compare_regardless_of_order():
    assert same_command_set(
        ["display bgp peer", "dis current-config config bgp"],
        ["display current-configuration configuration bgp", "display bgp peer"],
    )


# ------------------------------------------------------------------ noise
@pytest.mark.parametrize(
    "text",
    [
        "Peer : <ip>",
        "Interface Name : 100GE0/3/3",
        "BGP Peer is 1.1.1.1,  remote AS 100",
    ],
)
def test_echo_lines_are_not_commands(text):
    verdict = hygiene.classify(text)
    assert verdict is not None and verdict.kind == "echo"


def test_a_table_row_is_not_a_command():
    verdict = hygiene.classify("1.3.1  4  100  0  0  0  0965h20m  Connect  0")
    assert verdict is not None and verdict.kind == "table"


def test_a_sentence_is_not_a_command():
    verdict = hygiene.classify("但由于 Device A 是双路由上行，发包存在Hash问题")
    assert verdict is not None and verdict.kind == "prose"


def test_a_real_command_passes():
    assert hygiene.classify("display bgp peer verbose") is None


def test_a_configuration_line_passes_for_a_repair():
    assert hygiene.classify("mtu <mtu-value>") is None
    assert hygiene.classify("undo shutdown") is None


def test_a_configuration_line_is_refused_where_only_a_check_belongs():
    verdict = hygiene.classify("car bgp cir 20", allow_config=False)
    assert verdict is not None


def test_a_device_prompt_is_stripped_from_the_command():
    body, stripped = hygiene.strip_prompt("[~DeviceA-bgp] peer 1.1.1.1 as-number 100")
    assert stripped is True
    assert body == "peer 1.1.1.1 as-number 100"


def test_filtering_keeps_the_reason_for_every_rejection():
    kept, rejected = hygiene.filter_commands(
        ["display bgp peer", "Peer : <ip>", "1.3.1  4  100  0  0  Connect  0"]
    )
    assert kept == ["display bgp peer"]
    assert {item.kind for item in rejected} == {"echo", "table"}
    assert all(item.reason for item in rejected)


def test_hardcoded_example_values_are_reported():
    assert hygiene.hardcoded_literals("display cpu-defend statistics-all slot 3") == ["slot 3"]
    assert hygiene.hardcoded_literals("display interface <interface-name>") == []


# ------------------------------------------------------------ cause names
def test_a_noun_phrase_is_a_valid_cause_name():
    assert hygiene.looks_like_cause_name("两端接口 MTU 配置不一致") is None
    assert hygiene.looks_like_cause_name("物理链路或光模块异常") is None


def test_a_sentence_is_not_a_valid_cause_name():
    verdict = hygiene.looks_like_cause_name("但由于 Device A 是双路由上行，发包存在Hash问题")
    assert verdict is not None and verdict.kind == "prose"


def test_a_table_row_is_not_a_valid_cause_name():
    verdict = hygiene.looks_like_cause_name("1.3.1  4  100  0  0  0  Connect  0")
    assert verdict is not None and verdict.kind == "table"


# ------------------------------------------------------------ in the document
def _graph(nodes, edges) -> Graph:
    graph, report = Graph.from_bundle(RawBundle(nodes=nodes, edges=edges, sources=["t"]))
    assert not report.errors
    return graph


def _doc(nodes, edges):
    graph = _graph(nodes, edges)
    symptom = next(node for node in graph.iter_nodes() if node.node_type == "symptom")
    return build_doc(graph, build_playbook(graph, symptom))


def test_echo_lines_never_reach_the_document():
    nodes = [
        make_node("symptom_a", "symptom", "邻居无法建立"),
        make_node("cause_b", "cause", "MD5 不一致"),
        make_node(
            "check_c",
            "check",
            "查看 BGP 配置",
            attrs={"command_templates": ["display bgp peer", "Peer : <ip>"]},
        ),
        make_node("observation_d", "observation", "密码不符", attrs={"field": "password"}),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge("e2", "diagnosed_by", "symptom_a", "check_c"),
        make_edge("e3", "observes", "check_c", "observation_d"),
        make_edge("e4", "confirms", "observation_d", "cause_b"),
    ]
    doc = _doc(nodes, edges)
    assert doc.prechecks[0].commands == ["display bgp peer"]
    assert any("Peer : <ip>" in name for name, _reason in doc.omitted)


def test_two_spellings_collect_once_and_register_the_other():
    nodes = [
        make_node("symptom_a", "symptom", "邻居无法建立"),
        make_node("cause_b", "cause", "对等体配置错误"),
        make_node(
            "check_c",
            "check",
            "查看 BGP 配置（手册）",
            attrs={"command_templates": ["display current-configuration configuration bgp"]},
        ),
        make_node(
            "check_d",
            "check",
            "查看 BGP 配置（案例）",
            attrs={"command_templates": ["display current-config config bgp"]},
        ),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge("e2", "diagnosed_by", "symptom_a", "check_c"),
        make_edge("e3", "diagnosed_by", "symptom_a", "check_d"),
        make_edge("e4", "repaired_by", "cause_b", "repair_x"),
        make_edge("e5", "diagnosed_by", "cause_b", "check_c"),
    ]
    nodes.append(make_node("repair_x", "repair", "改配置", attrs={"command_templates": ["peer <ip> as-number <as>"]}))
    doc = _doc(nodes, edges)
    assert len(doc.prechecks) == 1, "缩写与全写应合并为一条采集步骤"
    assert doc.prechecks[0].commands == ["display current-configuration configuration bgp"]
    assert doc.prechecks[0].variants == ["display current-config config bgp"]


def test_a_prose_cause_is_left_out_with_its_reason():
    nodes = [
        make_node("symptom_a", "symptom", "流量中断"),
        make_node("cause_b", "cause", "但由于 Device A 是双路由上行，发包存在Hash问题"),
        make_node("repair_c", "repair", "调整", attrs={"command_templates": ["undo shutdown"]}),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_b"),
        make_edge("e2", "repaired_by", "cause_b", "repair_c"),
    ]
    doc = _doc(nodes, edges)
    assert doc.steps == []
    assert any(reason.startswith("正文句子") for _name, reason in doc.omitted)


def test_command_variants_are_reported_per_node():
    node = make_node(
        "check_c",
        "check",
        "查看配置",
        attrs={
            "command_templates": [
                "display current-config config bgp",
                "display current-configuration configuration bgp",
            ]
        },
    )
    graph = _graph([node], [])
    check = graph.get("check_c")
    assert command_templates(check) == ["display current-configuration configuration bgp"]
    assert command_variants(check) == [
        ("display current-configuration configuration bgp", ["display current-config config bgp"])
    ]


# ------------------------------------------------------------ 抽取哈希
# 抽取给同一个输入在每个来源上各贴一个哈希（`peer ip c8be5e6454`）。
# 原样透传的结果是入参列表里同一个问题问好几遍，命令里印着现场无意义的 id。
def test_a_hash_is_recognised_and_a_readable_id_is_not():
    assert hygiene.is_hash_token("c8be5e6454")
    assert hygiene.is_hash_token("f317a60748")
    assert not hygiene.is_hash_token("20230115")  # 全数字是读得出的编号
    assert not hygiene.is_hash_token("ip")
    assert not hygiene.is_hash_token("bfd12")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("slot f317a60748", "slot"),
        ("case ip fb3f6f7e17", "case-ip"),
        ("peer ip c8be5e6454", "peer-ip"),
        ("peer_ip_9d10bb2201", "peer-ip"),
        ("neName", "neName"),
        ("interface-type", "interface-type"),
        ("f317a60748", ""),  # 只剩哈希，没有可填的名字
    ],
)
def test_slot_names_are_generalised(raw, expected):
    assert hygiene.generalise_slot(raw) == expected


def test_the_same_input_hashed_twice_is_one_key():
    assert param_key("peer ip c8be5e6454") == param_key("peer ip 4f2ab19c07")
    assert param_key("peer ip c8be5e6454") == param_key("peer-ip")
    assert param_key("peer ip c8be5e6454") != param_key("case ip fb3f6f7e17")


def _hashed_records():
    nodes = [
        make_node(
            "symptom_a", "symptom", "BGP邻居异常",
            attrs={"required_slots": ["peer ip c8be5e6454", "slot f317a60748"]},
        ),
        make_node(
            "symptom_b", "symptom", "BGP邻居异常",
            attrs={"required_slots": ["peer ip 4f2ab19c07", "peer_ip_9d10bb2201"]},
        ),
        make_node("cause_a", "cause", "对端未配置"),
        make_node(
            "check_a", "check", "看邻居",
            attrs={"command_templates": ["display bgp peer <peer ip c8be5e6454>"]},
        ),
        make_node("obs_a", "observation", "邻居 Idle", attrs={"field": "State"}),
    ]
    edges = [
        make_edge("e1", "has_cause", "symptom_a", "cause_a", rank=1),
        make_edge("e2", "diagnosed_by", "cause_a", "check_a"),
        make_edge("e3", "observes", "check_a", "obs_a"),
        make_edge("e4", "confirms", "obs_a", "cause_a"),
    ]
    return nodes, edges


def test_slots_hashed_per_source_become_one_input():
    nodes, edges = _hashed_records()
    graph = _graph(nodes, edges)
    playbook = build_merged_playbook(graph, [graph.nodes["symptom_a"], graph.nodes["symptom_b"]])
    doc = build_doc(graph, playbook)
    assert [param.display for param in doc.params] == ["peer ip", "slot"]


def test_a_slot_that_is_only_a_hash_is_reported_not_asked_for():
    nodes, edges = _hashed_records()
    nodes[0]["attrs"]["required_slots"] = ["f317a60748"]
    doc = _doc(nodes, edges)
    assert not any(param.display == "f317a60748" for param in doc.params)
    assert any("只剩抽取哈希" in reason for _name, reason in doc.omitted)


def test_the_command_asks_for_the_same_name_the_parameter_list_declares():
    nodes, edges = _hashed_records()
    doc = _doc(nodes, edges)
    assert doc.steps[0].commands == ["display bgp peer <peer-ip>"]
    assert lint_text(render_doc(doc, name="demo", description="现象。出现时使用。")).ok


def test_lint_rejects_a_hash_left_in_a_command():
    nodes, edges = _hashed_records()
    doc = _doc(nodes, edges)
    text = render_doc(doc, name="demo", description="现象。出现时使用。")
    broken = text.replace("<peer-ip>", "<peer ip c8be5e6454>")
    messages = [issue.message for issue in lint_text(broken).issues]
    assert any("带抽取哈希" in message for message in messages)
