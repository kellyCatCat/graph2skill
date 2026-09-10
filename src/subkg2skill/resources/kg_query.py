#!/usr/bin/env python3
"""Query the knowledge subgraph bundled with this skill.

Standard library only (Python 3.9+).  Run it from anywhere — the data file is
resolved relative to this script.

    python3 kg_query.py stats
    python3 kg_query.py search "邻居震荡" --type symptom
    python3 kg_query.py show cause_6200a0200219ac655d65efa4
    python3 kg_query.py neighbors symptom_9a1b --direction both
    python3 kg_query.py expand symptom_9a1b --depth 2
    python3 kg_query.py path symptom_9a1b repair_77aa

Everything printed here is candidate knowledge: conditions are unevaluated and
`machine_checked` is not human review.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, deque
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "subgraph.json")

VERDICT_EDGES = ("confirms", "supports", "excludes")


def load(path: str) -> Tuple[Dict[str, dict], List[dict], dict]:
    if not os.path.exists(path):
        sys.exit(f"找不到数据文件：{path}（用 --data 指定路径）")
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, dict):
        nodes = payload.get("nodes") or []
        edges = payload.get("edges") or []
        meta = payload.get("meta") or {}
    else:
        nodes, edges, meta = payload, [], {}
    index = {node.get("node_id"): node for node in nodes if node.get("node_id")}
    return index, list(edges), meta


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "；".join(text(item) for item in value if text(item))
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def headline(node: Optional[dict]) -> str:
    if not node:
        return "(缺失节点)"
    return f"{text(node.get('name'))} [{node.get('node_type')}] ({node.get('node_id')})"


def resolve(index: Dict[str, dict], node_id: str) -> dict:
    if node_id in index:
        return index[node_id]
    matches = [nid for nid in index if nid.startswith(node_id)]
    if len(matches) == 1:
        return index[matches[0]]
    if not matches:
        sys.exit(f"未找到节点：{node_id}")
    sys.exit("前缀不唯一，候选：\n" + "\n".join(f"  {m}" for m in matches[:20]))


def adjacency(edges: Sequence[dict]) -> Tuple[Dict[str, List[dict]], Dict[str, List[dict]]]:
    out: Dict[str, List[dict]] = {}
    inc: Dict[str, List[dict]] = {}
    for edge in edges:
        out.setdefault(edge.get("source"), []).append(edge)
        inc.setdefault(edge.get("target"), []).append(edge)
    return out, inc


def format_condition(cond: Any, depth: int = 0) -> str:
    if cond is None:
        return ""
    if isinstance(cond, str):
        return cond
    if not isinstance(cond, dict):
        return json.dumps(cond, ensure_ascii=False)
    kind = cond.get("type")
    if kind == "text":
        expression = str(cond.get("expression") or "").strip()
        return (f"文本「{expression}」" if depth else expression) if expression else ""
    if kind in ("and", "or"):
        joiner = " 且 " if kind == "and" else " 或 "
        parts = [format_condition(c, depth + 1) for c in cond.get("conditions") or []]
        return "（" + joiner.join(p for p in parts if p) + "）"
    if kind == "not":
        return f"非（{format_condition(cond.get('condition'), depth + 1)}）"
    parts = [text(cond.get("object")), text(cond.get("field")) or text(cond.get("var"))]
    subject = ".".join(p for p in parts if p)
    pieces = [subject, text(cond.get("operator") or cond.get("comparison"))]
    if "value" in cond:
        pieces.append(json.dumps(cond.get("value"), ensure_ascii=False))
    if cond.get("expression"):
        pieces.append(f"原文：{cond['expression']}")
    return " ".join(p for p in pieces if p)


def edge_line(edge: dict, index: Dict[str, dict], *, direction: str = "out") -> str:
    other_id = edge.get("target") if direction == "out" else edge.get("source")
    arrow = "→" if direction == "out" else "←"
    line = f"  {arrow} [{edge.get('edge_type')}] {headline(index.get(other_id))}"
    condition = format_condition(edge.get("condition"))
    if condition:
        line += f"\n      条件（{edge.get('condition_status')}，未求值）：{condition}"
    if edge.get("rank") is not None:
        line += f"\n      rank={edge['rank']}（排序提示）"
    return line


def evidence_line(item: dict) -> str:
    head = []
    if item.get("document"):
        head.append(str(item["document"]))
    if item.get("page"):
        head.append(f"p.{item['page']}")
    if item.get("section"):
        head.append(f"§{item['section']}")
    if item.get("sheet") or item.get("cell"):
        head.append(" ".join(str(item.get(k)) for k in ("sheet", "cell") if item.get(k)))
    quote = text(item.get("quote")).replace("\n", " ⏎ ")
    return "、".join(head) + (f"：“{quote}”" if quote else "")


# ---------------------------------------------------------------- commands
def cmd_stats(args, index, edges, meta) -> None:
    print(f"数据文件：{args.data}")
    if meta:
        print(f"生成时间：{meta.get('generated_at', '未知')}；来源：{text(meta.get('sources'))}")
        if meta.get("notice"):
            print(f"提示：{meta['notice']}")
    print(f"节点：{len(index)}；关系：{len(edges)}")
    print("\n按节点类型：")
    for node_type, count in Counter(n.get("node_type") for n in index.values()).most_common():
        print(f"  {node_type}: {count}")
    print("\n按关系类型：")
    for edge_type, count in Counter(e.get("edge_type") for e in edges).most_common():
        print(f"  {edge_type}: {count}")


def _haystack(node: dict) -> str:
    parts = [text(node.get("name")), text(node.get("description")), text(node.get("node_id"))]
    parts += [text(a) for a in node.get("aliases") or []]
    attrs = node.get("attrs") or {}
    parts += [text(attrs.get(k)) for k in ("match_phrases", "intent", "fault_mechanism", "field")]
    parts += [text(c.get("title")) for c in node.get("diagnostic_contexts") or []]
    return "\n".join(p for p in parts if p).lower()


def cmd_search(args, index, edges, meta) -> None:
    needle = args.term.lower()
    hits = []
    for node in index.values():
        if args.type and node.get("node_type") not in args.type:
            continue
        if needle in _haystack(node):
            score = 0 if needle in text(node.get("name")).lower() else 1
            hits.append((score, text(node.get("name")), node))
    hits.sort(key=lambda h: (h[0], h[1]))
    if not hits:
        print(f"没有匹配「{args.term}」的节点。")
        return
    print(f"匹配 {len(hits)} 个节点（显示前 {min(args.limit, len(hits))} 个）：")
    for _, _, node in hits[: args.limit]:
        print(f"  {headline(node)}")
        contexts = node.get("diagnostic_contexts") or []
        if contexts:
            print(f"      诊断单元：{text(contexts[0].get('title') or contexts[0].get('section'))}")


def cmd_show(args, index, edges, meta) -> None:
    node = resolve(index, args.node_id)
    out, inc = adjacency(edges)
    if args.json:
        print(json.dumps(node, ensure_ascii=False, indent=2))
        return
    print(headline(node))
    if node.get("description"):
        print(f"描述：{text(node['description'])}")
    if node.get("aliases"):
        print(f"别名：{text(node['aliases'])}")
    scope = node.get("scope") or {}
    if scope:
        print("适用范围：" + "；".join(f"{k}={v}" for k, v in scope.items() if v))
    print(
        f"知识状态：status={node.get('status')}；review={node.get('review_status')}；"
        f"人工复核={'是' if (node.get('semantic_review') or {}).get('human_reviewed') else '否'}"
    )
    attrs = node.get("attrs") or {}
    if attrs:
        print("属性：")
        for key, value in attrs.items():
            rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else text(value)
            if rendered:
                print(f"  {key}: {rendered}")
    if node.get("quality_flags"):
        print("质量标记：" + text(node["quality_flags"]))
    contexts = node.get("diagnostic_contexts") or []
    if contexts:
        print("诊断单元：" + "；".join(text(c.get("title") or c.get("section")) for c in contexts))
    provenance = node.get("provenance") or []
    if provenance:
        print("来源：")
        for item in provenance[: args.evidence]:
            print(f"  - {evidence_line(item)}")
        if len(provenance) > args.evidence:
            print(f"  - （另有 {len(provenance) - args.evidence} 条）")
    node_id = node.get("node_id")
    if out.get(node_id):
        print("出边：")
        for edge in out[node_id]:
            print(edge_line(edge, index, direction="out"))
    if inc.get(node_id):
        print("入边：")
        for edge in inc[node_id]:
            print(edge_line(edge, index, direction="in"))


def cmd_neighbors(args, index, edges, meta) -> None:
    node = resolve(index, args.node_id)
    node_id = node.get("node_id")
    out, inc = adjacency(edges)
    print(headline(node))
    if args.direction in ("out", "both"):
        for edge in out.get(node_id, []):
            if args.edge_type and edge.get("edge_type") not in args.edge_type:
                continue
            print(edge_line(edge, index, direction="out"))
    if args.direction in ("in", "both"):
        for edge in inc.get(node_id, []):
            if args.edge_type and edge.get("edge_type") not in args.edge_type:
                continue
            print(edge_line(edge, index, direction="in"))


def cmd_expand(args, index, edges, meta) -> None:
    node = resolve(index, args.node_id)
    out, inc = adjacency(edges)
    seen = {node.get("node_id")}
    queue = deque([(node.get("node_id"), 0)])
    print(headline(node))
    while queue:
        current, level = queue.popleft()
        if level >= args.depth:
            continue
        outgoing = out.get(current, [])
        incoming = [e for e in inc.get(current, []) if e.get("edge_type") in VERDICT_EDGES]
        for edge, direction in [(e, "out") for e in outgoing] + [(e, "in") for e in incoming]:
            other = edge.get("target") if direction == "out" else edge.get("source")
            indent = "  " * (level + 1)
            marker = "→" if direction == "out" else "←"
            print(f"{indent}{marker} [{edge.get('edge_type')}] {headline(index.get(other))}")
            condition = format_condition(edge.get("condition"))
            if condition:
                print(f"{indent}    条件（{edge.get('condition_status')}，未求值）：{condition}")
            if other not in seen:
                seen.add(other)
                queue.append((other, level + 1))


def cmd_path(args, index, edges, meta) -> None:
    start = resolve(index, args.source).get("node_id")
    goal = resolve(index, args.target).get("node_id")
    out, inc = adjacency(edges)
    previous: Dict[str, Tuple[str, dict, str]] = {}
    queue = deque([start])
    seen = {start}
    while queue:
        current = queue.popleft()
        if current == goal:
            break
        neighbours = [(e.get("target"), e, "out") for e in out.get(current, [])]
        if args.undirected:
            neighbours += [(e.get("source"), e, "in") for e in inc.get(current, [])]
        for other, edge, direction in neighbours:
            if other and other not in seen:
                seen.add(other)
                previous[other] = (current, edge, direction)
                queue.append(other)
    if goal not in previous and goal != start:
        print("两点之间没有可达路径（可加 --undirected 忽略方向）。")
        return
    chain: List[str] = []
    cursor = goal
    while cursor != start:
        parent, edge, direction = previous[cursor]
        arrow = "→" if direction == "out" else "←"
        chain.append(f"  {arrow} [{edge.get('edge_type')}] {headline(index.get(cursor))}")
        cursor = parent
    print(headline(index.get(start)))
    for line in reversed(chain):
        print(line)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="查询本技能自带的故障诊断知识子图")
    parser.add_argument("--data", default=DEFAULT_DATA, help="subgraph.json 路径")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats", help="数据规模与分布").set_defaults(func=cmd_stats)

    search = sub.add_parser("search", help="按关键词检索节点")
    search.add_argument("term")
    search.add_argument("--type", action="append", help="限定 node_type，可重复")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=cmd_search)

    show = sub.add_parser("show", help="展开一个节点的全部字段与邻接关系")
    show.add_argument("node_id")
    show.add_argument("--evidence", type=int, default=3, help="展示的来源条数")
    show.add_argument("--json", action="store_true", help="输出原始 JSON")
    show.set_defaults(func=cmd_show)

    neighbors = sub.add_parser("neighbors", help="只看邻接关系")
    neighbors.add_argument("node_id")
    neighbors.add_argument("--edge-type", action="append", dest="edge_type")
    neighbors.add_argument("--direction", choices=("out", "in", "both"), default="out")
    neighbors.set_defaults(func=cmd_neighbors)

    expand = sub.add_parser("expand", help="按深度展开子树")
    expand.add_argument("node_id")
    expand.add_argument("--depth", type=int, default=2)
    expand.set_defaults(func=cmd_expand)

    path = sub.add_parser("path", help="两个节点之间的最短关系链")
    path.add_argument("source")
    path.add_argument("target")
    path.add_argument("--undirected", action="store_true", help="忽略边方向")
    path.set_defaults(func=cmd_path)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    args.data = os.path.normpath(args.data)
    index, edges, meta = load(args.data)
    try:
        args.func(args, index, edges, meta)
        sys.stdout.flush()
    except BrokenPipeError:  # piped into `head` and friends
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
