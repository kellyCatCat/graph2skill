from graph2skill.model import Graph, Node, as_text_list, dedupe


def test_as_text_list_normalises_shapes():
    assert as_text_list("a") == ["a"]
    assert as_text_list([{"command": "show x"}, "show x", None, ""]) == ["show x"]
    assert as_text_list([{"weird": 1}])[0].startswith("{")


def test_dedupe_keeps_first_occurrence():
    assert dedupe(["b", "a", "b"]) == ["b", "a"]


def test_node_falls_back_to_data_node_type_and_description():
    node = Node.from_raw({"id": "x", "data": {"nodeType": "case", "description": "只有描述"}})
    assert node.type == "CASE"
    assert node.label == "只有描述"


def test_node_commands_merge_parameter_examples_and_commands():
    node = Node.from_raw(
        {"id": "x", "type": "CHECK", "data": {"parameterExamples": [{"command": "a"}], "commands": ["b", "a"]}}
    )
    assert node.commands == ["a", "b"]


def test_node_extra_data_excludes_typed_keys():
    node = Node.from_raw({"id": "x", "data": {"description": "d", "customField": 7}})
    assert node.extra_data == {"customField": 7}


def test_relation_id_is_synthesised_when_absent():
    graph = Graph.from_raw(
        {
            "graphId": "G",
            "nodes": [{"id": "a"}, {"id": "b"}],
            "relations": [{"from": "a", "to": "b", "data": {"edgeType": "next"}}],
        }
    )
    relation = next(iter(graph.relations.values()))
    assert relation.type == "NEXT"
    assert "a->b" in relation.id


def test_relations_without_endpoints_are_dropped_with_warning():
    graph = Graph.from_raw({"graphId": "G", "nodes": [{"id": "a"}], "relations": [{"id": "r", "from": "a"}]})
    assert not graph.relations
    assert graph.warnings


def test_source_is_folded_into_sources():
    node = Node.from_raw({"id": "x", "source": "s1", "sources": ["s2"]})
    assert node.sources == ["s1", "s2"]
