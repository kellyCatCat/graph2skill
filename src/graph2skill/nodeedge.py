"""另一种图格式：扁平的 ``node.json`` + ``edge.json``。

这类导出是从 PDF 抽取管线来的，和 ``cograg.domain-decision-subgraph.v1`` 的
区别在于：顶层是数组而不是对象、节点与关系分成两个文件、出处叫 ``provenance``
（节点）或 ``evidence``（关系）、且带有 scope / review_status / quality_flags
这类审核字段。这里把它翻译成统一的 :class:`~graph2skill.model.Graph`，
之后所有命令（build / merge / steps / lint）都能和另一种格式混着用。

**命令只从明确的字段取**（``attrs.command`` 等，或本身就是一条 CLI 的检查项名称）——
证据里的 ``quote`` 是 PDF 原文摘录，不当成命令来源。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from graph2skill.model import Graph, Node, Provenance, Relation, as_list, as_text_list, dedupe, first_str
from graph2skill.util import one_line

NODE_FILE_NAMES = ("node.json", "nodes.json", "node_list.json", "nodes.jsonl")
EDGE_FILE_NAMES = ("edge.json", "edges.json", "edge_list.json", "edges.jsonl")

COMMAND_KEYS = ("command", "commands", "cli", "command_line", "commandLine", "命令行")
CLI_VERBS = (
    "display", "undo", "reset", "ping", "tracert", "traceroute", "system-view",
    "interface", "quit", "commit", "save", "debugging", "terminal", "screen-length",
)
CHECK_TYPES = ("check", "command", "step", "diagnosis", "detect")

# 节点/关系字段里，已经被专门映射过、不需要再原样保留的键
_CONSUMED_NODE_KEYS = frozenset({"node_id", "node_type", "name", "description", "aliases", "provenance"})
_CONSUMED_EDGE_KEYS = frozenset({"edge_id", "edge_type", "source", "target", "condition", "evidence"})


def join_pdf_lines(text: str) -> str:
    """把 PDF 抽取里被换行截断的句子接回去。

    ``"display isis last-peer-\\nchange"`` → ``"display isis last-peer-change"``；
    中文两侧直接相接，英文之间补一个空格。
    """
    if not text:
        return ""
    out: List[str] = []
    lines = [line.strip() for line in str(text).splitlines()]
    for index, line in enumerate(lines):
        if not line:
            continue
        if not out:
            out.append(line)
            continue
        previous = out[-1]
        if previous.endswith("-"):
            out[-1] = previous + line  # 断词，直接接上
            continue
        if previous and line and (ord(previous[-1]) > 127 or ord(line[0]) > 127):
            out[-1] = previous + line  # 中文，不补空格
            continue
        out[-1] = previous + " " + line
    return "".join(out).strip()


def looks_like_cli(text: str) -> bool:
    """一段文字是否本身就是一条命令行。"""
    cleaned = one_line(join_pdf_lines(text)).lower()
    return bool(cleaned) and cleaned.split()[0] in CLI_VERBS


def classify_records(records: Sequence[Any]) -> str:
    """判断一个数组是节点表还是关系表。"""
    for record in records:
        if not isinstance(record, dict):
            continue
        if "node_id" in record or "node_type" in record:
            return "nodes"
        if "edge_id" in record or "edge_type" in record:
            return "edges"
        if "source" in record and "target" in record:
            return "edges"
        if "id" in record and "type" in record and "from" not in record:
            return "nodes"
    return ""


def _provenance(records: Any) -> List[Provenance]:
    out: List[Provenance] = []
    for raw in as_list(records):
        if not isinstance(raw, dict):
            continue
        section = first_str(raw, ("section",))
        page = raw.get("printed_page") or raw.get("page")
        locator_parts = [part for part in (section, first_str(raw, ("title",))) if part]
        locator = " ".join(locator_parts)
        if page:
            locator = f"{locator} · p{page}" if locator else f"p{page}"
        out.append(
            Provenance(
                anchor=first_str(raw, ("block_id", "anchor", "hash")),
                excerpt=join_pdf_lines(first_str(raw, ("quote", "excerpt", "text"))),
                locator=locator,
                source_path=first_str(raw, ("document", "sourcePath", "source_path", "file")),
            )
        )
    return [item for item in out if not item.is_empty]


def _commands(raw: Dict[str, Any], node_type: str, name: str) -> List[str]:
    """只从明确的字段取命令；检查项的名称本身是 CLI 时也算。"""
    found: List[str] = []
    attrs = raw.get("attrs") if isinstance(raw.get("attrs"), dict) else {}
    for source in (raw, attrs):
        for key in COMMAND_KEYS:
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                found.append(join_pdf_lines(value))
            elif isinstance(value, list):
                found.extend(join_pdf_lines(str(item)) for item in value if item)
    if node_type.lower() in CHECK_TYPES:
        for candidate in [name] + as_text_list(raw.get("aliases")):
            if looks_like_cli(candidate):
                found.append(join_pdf_lines(candidate))
    return dedupe([one_line(item) for item in found if one_line(item)])


def node_from_record(raw: Dict[str, Any], graph_id: str = "") -> Optional[Node]:
    node_id = first_str(raw, ("node_id", "id", "_id"))
    if not node_id:
        return None
    node_type = (first_str(raw, ("node_type", "type")) or "UNKNOWN").upper()
    name = join_pdf_lines(first_str(raw, ("name", "title", "label")))
    data: Dict[str, Any] = {}
    description = join_pdf_lines(first_str(raw, ("description", "summary")))
    if description:
        data["description"] = description
    commands = _commands(raw, node_type, name)
    if commands:
        data["parameterExamples"] = commands
    provenance = _provenance(raw.get("provenance"))
    if provenance:
        data["provenance"] = [item.to_dict() for item in provenance]
    aliases = as_text_list(raw.get("aliases"))
    if aliases:
        data["nameAliases"] = aliases
    contexts = as_list(raw.get("diagnostic_contexts")) or as_list(raw.get("diagnostic_context"))
    for context in contexts:
        if isinstance(context, dict):
            section = first_str(context, ("section",))
            if section:
                data.setdefault("section", section)
                data.setdefault("sectionTitle", first_str(context, ("title",)))
            break
    attrs = raw.get("attrs")
    if isinstance(attrs, dict):
        mechanism = join_pdf_lines(first_str(attrs, ("fault_mechanism", "mechanism")))
        if mechanism and mechanism != description:
            data["observations"] = [mechanism]
    for key, value in raw.items():
        if key in _CONSUMED_NODE_KEYS or value in (None, "", [], {}):
            continue
        data.setdefault(key, value)
    sources = as_text_list(raw.get("sources")) or [first_str(raw, ("origin", "source"))]
    scope = raw.get("scope") if isinstance(raw.get("scope"), dict) else {}
    vendor = first_str(scope, ("vendor",))
    if not any(sources) and vendor:
        sources = [vendor]
    return Node(
        id=node_id,
        type=node_type,
        name=name,
        source=sources[0] if sources and sources[0] else "",
        sources=[item for item in sources if item],
        data=data,
        graph_ids=[graph_id] if graph_id else [],
    )


def relation_from_record(raw: Dict[str, Any], graph_id: str = "", index: int = 0) -> Optional[Relation]:
    from_id = first_str(raw, ("source", "from", "source_id"))
    to_id = first_str(raw, ("target", "to", "target_id"))
    if not from_id or not to_id:
        return None
    rel_type = (first_str(raw, ("edge_type", "type")) or "RELATED_TO").upper()
    rel_id = first_str(raw, ("edge_id", "id")) or f"{graph_id or 'edge'}:{rel_type}:{from_id}->{to_id}#{index}"
    data: Dict[str, Any] = {"edgeType": rel_type}
    condition = raw.get("condition")
    if isinstance(condition, str) and condition.strip():
        data["condition"] = join_pdf_lines(condition)
    evidence = _provenance(raw.get("evidence") or raw.get("provenance"))
    if evidence:
        data["provenance"] = [item.to_dict() for item in evidence]
    context = raw.get("diagnostic_context")
    if isinstance(context, dict):
        section = first_str(context, ("section",))
        if section:
            data["section"] = section
            data["sectionTitle"] = first_str(context, ("title",))
    for key, value in raw.items():
        if key in _CONSUMED_EDGE_KEYS or value in (None, "", [], {}):
            continue
        data.setdefault(key, value)
    sources = as_text_list(raw.get("sources")) or [first_str(raw, ("origin",))]
    return Relation(
        id=rel_id,
        type=rel_type,
        from_id=from_id,
        to_id=to_id,
        source=sources[0] if sources and sources[0] else "",
        sources=[item for item in sources if item],
        data=data,
        graph_ids=[graph_id] if graph_id else [],
    )


def graph_from_records(
    node_records: Sequence[Any] = (),
    edge_records: Sequence[Any] = (),
    graph_id: str = "node-edge-graph",
    domain: str = "",
    origin: str = "",
) -> Graph:
    """把节点表 + 关系表翻译成统一的 Graph。"""
    graph = Graph(graph_id=graph_id, domain=domain, origin=origin)
    for raw in node_records:
        if not isinstance(raw, dict):
            graph.warnings.append(f"跳过非对象节点：{raw!r}")
            continue
        node = node_from_record(raw, graph_id)
        if node is None:
            graph.warnings.append(f"节点缺少 node_id，已跳过：{json.dumps(raw, ensure_ascii=False)[:120]}")
            continue
        if node.id in graph.nodes:
            graph.warnings.append(f"重复的 node_id：{node.id}")
            continue
        graph.nodes[node.id] = node
    for index, raw in enumerate(edge_records):
        if not isinstance(raw, dict):
            graph.warnings.append(f"跳过非对象关系：{raw!r}")
            continue
        relation = relation_from_record(raw, graph_id, index)
        if relation is None:
            graph.warnings.append(f"关系缺少 source/target，已跳过：{json.dumps(raw, ensure_ascii=False)[:120]}")
            continue
        graph.relations[relation.id] = relation

    if not graph.domain:
        for node in graph.nodes.values():
            scope = node.data.get("scope")
            if isinstance(scope, dict):
                family = first_str(scope, ("product_family", "vendor"))
                if family:
                    graph.domain = family
                    break
    targets = {relation.to_id for relation in graph.relations.values()}
    graph.entry_node_ids = [node_id for node_id in graph.nodes if node_id not in targets]
    return graph


def find_pair(directory: Path) -> Tuple[Optional[Path], Optional[Path]]:
    """在目录里找 node/edge 文件（大小写不敏感）。"""
    node_path = edge_path = None
    for child in sorted(directory.iterdir()):
        if not child.is_file():
            continue
        lowered = child.name.lower()
        if node_path is None and lowered in NODE_FILE_NAMES:
            node_path = child
        elif edge_path is None and lowered in EDGE_FILE_NAMES:
            edge_path = child
    return node_path, edge_path
