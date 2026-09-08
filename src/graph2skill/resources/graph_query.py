#!/usr/bin/env python3
"""Query the knowledge graph that backs this skill (stdlib only).

Usage:
  python scripts/graph_query.py search "LDP"          # full-text search over nodes
  python scripts/graph_query.py node <node-id>        # one node + its neighbours
  python scripts/graph_query.py tree <tree-or-entry>  # print a decision tree
  python scripts/graph_query.py entries               # list entry nodes / trees
  python scripts/graph_query.py commands [pattern]    # list command samples
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_GRAPH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "graph.json")


def load_graph(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def node_map(graph: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {node.get("id", ""): node for node in graph.get("nodes", [])}


def texts_of(node: Dict[str, Any]) -> List[str]:
    data = node.get("data") or {}
    values = [node.get("id", ""), node.get("name", ""), str(data.get("description", ""))]
    for key in ("observations", "parameterExamples"):
        for item in data.get(key) or []:
            values.append(item if isinstance(item, str) else json.dumps(item, ensure_ascii=False))
    for prov in data.get("provenance") or []:
        if isinstance(prov, dict):
            values.append(str(prov.get("excerpt", "")))
            values.append(str(prov.get("sourcePath", "")))
    return [value for value in values if value]


def commands_of(node: Dict[str, Any]) -> List[str]:
    data = node.get("data") or {}
    out: List[str] = []
    for item in (data.get("parameterExamples") or []) + (data.get("commands") or []):
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in ("command", "text", "value"):
                if item.get(key):
                    out.append(str(item[key]))
                    break
    return out


def summarize(node: Dict[str, Any]) -> str:
    return f"[{node.get('type', '?')}] {node.get('name') or node.get('id')}  ({node.get('id')})"


def cmd_search(graph: Dict[str, Any], args: argparse.Namespace) -> int:
    needle = args.pattern.lower()
    hits = [node for node in graph.get("nodes", []) if any(needle in text.lower() for text in texts_of(node))]
    if args.type:
        hits = [node for node in hits if node.get("type", "").upper() == args.type.upper()]
    for node in hits[: args.limit]:
        print(summarize(node))
    print(f"-- {len(hits)} hit(s), showing {min(len(hits), args.limit)}", file=sys.stderr)
    return 0 if hits else 1


def cmd_node(graph: Dict[str, Any], args: argparse.Namespace) -> int:
    nodes = node_map(graph)
    node = nodes.get(args.node_id)
    if node is None:
        print(f"unknown node: {args.node_id}", file=sys.stderr)
        return 1
    print(json.dumps(node, ensure_ascii=False, indent=2))
    print("\n-- incoming --")
    for relation in graph.get("relations", []):
        if relation.get("to") == args.node_id:
            print(f"  {relation.get('type')} <- {summarize(nodes.get(relation.get('from'), {'id': relation.get('from')}))}")
    print("-- outgoing --")
    for relation in graph.get("relations", []):
        if relation.get("from") == args.node_id:
            print(f"  {relation.get('type')} -> {summarize(nodes.get(relation.get('to'), {'id': relation.get('to')}))}")
    return 0


def cmd_entries(graph: Dict[str, Any], args: argparse.Namespace) -> int:
    nodes = node_map(graph)
    for entry in graph.get("entryNodeIds", []):
        print(f"entry  {summarize(nodes.get(entry, {'id': entry}))}")
    for tree in graph.get("decisionTrees", []):
        print(f"tree   {tree.get('treeId')}  entry={tree.get('entryNodeId')}  nodes={len(tree.get('nodeIds') or [])}")
    return 0


def cmd_tree(graph: Dict[str, Any], args: argparse.Namespace) -> int:
    nodes = node_map(graph)
    entry = args.tree_id
    for tree in graph.get("decisionTrees", []):
        if tree.get("treeId") == args.tree_id:
            entry = tree.get("entryNodeId") or entry
            break
    if entry not in nodes:
        print(f"unknown tree or entry node: {args.tree_id}", file=sys.stderr)
        return 1
    out_edges: Dict[str, List[Dict[str, Any]]] = {}
    for relation in graph.get("relations", []):
        out_edges.setdefault(relation.get("from", ""), []).append(relation)

    def walk(node_id: str, depth: int, seen: Iterable[str]) -> None:
        node = nodes.get(node_id, {"id": node_id})
        print("  " * depth + "- " + summarize(node))
        for command in commands_of(node)[:2]:
            print("  " * depth + "    $ " + command)
        if node_id in seen or depth >= args.max_depth:
            return
        for relation in sorted(out_edges.get(node_id, []), key=lambda item: (item.get("type", ""), item.get("to", ""))):
            print("  " * (depth + 1) + f"({relation.get('type')})")
            walk(relation.get("to", ""), depth + 1, list(seen) + [node_id])

    walk(entry, 0, [])
    return 0


def cmd_commands(graph: Dict[str, Any], args: argparse.Namespace) -> int:
    pattern = (args.pattern or "").lower()
    count = 0
    for node in graph.get("nodes", []):
        for command in commands_of(node):
            if pattern and pattern not in command.lower():
                continue
            print(f"{command}\n    <- {summarize(node)}")
            count += 1
    print(f"-- {count} command(s)", file=sys.stderr)
    return 0 if count else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--graph", default=DEFAULT_GRAPH, help="path to graph.json (default: ../assets/graph.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    search = sub.add_parser("search", help="full-text search over nodes")
    search.add_argument("pattern")
    search.add_argument("--type", default="", help="filter by node type")
    search.add_argument("--limit", type=int, default=20)
    search.set_defaults(func=cmd_search)

    node = sub.add_parser("node", help="show one node and its neighbours")
    node.add_argument("node_id")
    node.set_defaults(func=cmd_node)

    tree = sub.add_parser("tree", help="print a decision tree")
    tree.add_argument("tree_id")
    tree.add_argument("--max-depth", type=int, default=12)
    tree.set_defaults(func=cmd_tree)

    entries = sub.add_parser("entries", help="list entry nodes and declared trees")
    entries.set_defaults(func=cmd_entries)

    commands = sub.add_parser("commands", help="list command samples")
    commands.add_argument("pattern", nargs="?", default="")
    commands.set_defaults(func=cmd_commands)

    args = parser.parse_args(argv)
    try:
        graph = load_graph(args.graph)
    except OSError as exc:
        print(f"cannot read graph: {exc}", file=sys.stderr)
        return 2
    return args.func(graph, args)


if __name__ == "__main__":
    raise SystemExit(main())
