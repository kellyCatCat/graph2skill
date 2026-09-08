"""Command line interface: ``graph2skill <command> [...]``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from graph2skill import __version__
from graph2skill.analyze import Issue
from graph2skill.bundle import SkillBundle, SkillOptions
from graph2skill.loader import GraphLoadError
from graph2skill.ontology import EN, ZH, node_type_label
from graph2skill.skill import build_bundle, write_skill

EXIT_OK = 0
EXIT_ISSUES = 1
EXIT_USAGE = 2


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("inputs", nargs="+", help="graph JSON/YAML files or directories containing them")
    parser.add_argument("--graph-id", default=None, help="override the merged graphId")
    parser.add_argument("--domain", default=None, help="override the domain name")
    parser.add_argument("--max-depth", type=int, default=12, help="maximum decision tree depth (default: 12)")
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="do not descend into sub-directories of input directories",
    )
    parser.add_argument(
        "--respect-declared-scope",
        action="store_true",
        help="limit each playbook to decisionTrees[].nodeIds/relationIds instead of traversing the whole reachable sub-graph",
    )
    parser.add_argument(
        "--no-inferred-entries",
        action="store_true",
        help="only use declared entryNodeIds / decisionTrees as playbook entries",
    )


def _options_from_args(args: argparse.Namespace) -> SkillOptions:
    return SkillOptions(
        name=getattr(args, "name", None),
        description=getattr(args, "description", None),
        title=getattr(args, "title", None),
        lang=getattr(args, "lang", ZH),
        max_depth=args.max_depth,
        include_inferred_entries=not args.no_inferred_entries,
        respect_declared_scope=args.respect_declared_scope,
        diagrams=not getattr(args, "no_diagram", False),
        include_graph_asset=not getattr(args, "no_graph_asset", False),
        include_query_script=not getattr(args, "no_script", False),
        include_report=not getattr(args, "no_report", False),
    )


def _load(args: argparse.Namespace) -> SkillBundle:
    return build_bundle(
        args.inputs,
        options=_options_from_args(args),
        graph_id=args.graph_id,
        domain=args.domain,
        recursive=not args.no_recursive,
    )


def _print_issues(issues: Sequence[Issue], stream=sys.stderr, limit: int = 0) -> None:
    shown = issues if limit <= 0 else issues[:limit]
    for issue in shown:
        print(issue.format(), file=stream)
    if limit and len(issues) > limit:
        print(f"... and {len(issues) - limit} more", file=stream)


def cmd_build(args: argparse.Namespace) -> int:
    bundle = _load(args)
    errors = bundle.errors()
    if errors and args.strict:
        print(f"refusing to build: {len(errors)} structural error(s) (--strict)", file=sys.stderr)
        _print_issues(errors, limit=20)
        return EXIT_ISSUES
    result = write_skill(bundle, Path(args.output), overwrite=args.force)
    print(f"skill '{result.skill_name}' written to {result.output_dir}")
    for relative in result.relative_files:
        print(f"  {relative}")
    stats = bundle.stats
    print(
        f"nodes={stats.node_count} relations={stats.relation_count} playbooks={stats.tree_count} "
        f"commands={stats.command_count} errors={len(errors)} warnings={len(bundle.warnings())}"
    )
    if bundle.warnings() and args.verbose:
        _print_issues(bundle.warnings(), limit=20)
    return EXIT_OK


def cmd_validate(args: argparse.Namespace) -> int:
    bundle = _load(args)
    issues = bundle.issues
    if args.json:
        print(
            json.dumps(
                {
                    "graphId": bundle.graph.graph_id,
                    "nodes": bundle.stats.node_count,
                    "relations": bundle.stats.relation_count,
                    "issues": [
                        {"severity": i.severity, "code": i.code, "message": i.message, "ref": i.ref} for i in issues
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        if not issues:
            print("no issues found")
        else:
            _print_issues(issues, stream=sys.stdout)
        print(
            f"{len(bundle.errors())} error(s), {len(bundle.warnings())} warning(s) "
            f"over {bundle.stats.node_count} nodes / {bundle.stats.relation_count} relations"
        )
    return EXIT_ISSUES if bundle.errors() else EXIT_OK


def cmd_stats(args: argparse.Namespace) -> int:
    bundle = _load(args)
    stats = bundle.stats
    if args.json:
        payload = {
            "graphId": bundle.graph.graph_id,
            "domain": bundle.graph.domain,
            "nodes": stats.node_count,
            "relations": stats.relation_count,
            "trees": stats.tree_count,
            "commands": stats.command_count,
            "provenance": stats.provenance_count,
            "orphans": stats.orphan_count,
            "nodeTypes": stats.node_types,
            "relationTypes": stats.relation_types,
            "sources": stats.sources,
            "playbooks": [
                {
                    "treeId": tree.tree_id,
                    "entryNodeId": tree.entry_node_id,
                    "title": tree.title,
                    "nodes": tree.size,
                    "maxDepth": tree.max_depth,
                    "declared": tree.declared,
                }
                for tree in bundle.trees
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    print(f"graphId    : {bundle.graph.graph_id}")
    print(f"domain     : {bundle.graph.domain or '-'}")
    print(f"nodes      : {stats.node_count}")
    print(f"relations  : {stats.relation_count}")
    print(f"trees      : {stats.tree_count} (orphan nodes: {stats.orphan_count})")
    print(f"commands   : {stats.command_count}")
    print(f"provenance : {stats.provenance_count}")
    print("node types :")
    for node_type, count in stats.node_types.items():
        print(f"  {node_type:<14} {count:>5}  {node_type_label(node_type, args.lang)}")
    print("relations  :")
    for rel_type, count in stats.relation_types.items():
        print(f"  {rel_type:<14} {count:>5}")
    print("playbooks  :")
    for tree in bundle.trees:
        flag = "declared" if tree.declared else "inferred"
        print(f"  [{flag}] {tree.size:>4} nodes  depth {tree.max_depth:>2}  {tree.title}")
    return EXIT_OK


def cmd_inspect(args: argparse.Namespace) -> int:
    bundle = _load(args)
    node = bundle.graph.nodes.get(args.node_id)
    if node is None:
        matches = [
            candidate
            for candidate in bundle.graph.nodes.values()
            if args.node_id.lower() in candidate.id.lower() or args.node_id.lower() in candidate.label.lower()
        ]
        if not matches:
            print(f"no node matches '{args.node_id}'", file=sys.stderr)
            return EXIT_ISSUES
        print(f"{len(matches)} candidate(s):", file=sys.stderr)
        for candidate in matches[:20]:
            print(f"  [{candidate.type}] {candidate.label}  ({candidate.id})")
        return EXIT_OK
    print(json.dumps(node.to_dict(), ensure_ascii=False, indent=2))
    print("\nincoming:")
    for relation in bundle.index.predecessors(node.id):
        other = bundle.graph.nodes.get(relation.from_id)
        print(f"  {relation.type} <- {other.label if other else relation.from_id} ({relation.from_id})")
    print("outgoing:")
    for relation in bundle.index.successors(node.id):
        other = bundle.graph.nodes.get(relation.to_id)
        print(f"  {relation.type} -> {other.label if other else relation.to_id} ({relation.to_id})")
    tree = bundle.tree_for_node(node.id)
    if tree:
        print(f"\nplaybook: {bundle.playbook_path(tree)}  ({tree.title})")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="graph2skill",
        description="Convert one or more domain-decision subgraphs into a skill package.",
    )
    parser.add_argument("--version", action="version", version=f"graph2skill {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="render the skill package")
    _add_common_arguments(build)
    build.add_argument("-o", "--output", required=True, help="output directory of the skill package")
    build.add_argument("--name", default=None, help="skill name for the SKILL.md front matter")
    build.add_argument("--description", default=None, help="skill description for the front matter")
    build.add_argument("--title", default=None, help="H1 title of SKILL.md")
    build.add_argument("--lang", choices=[ZH, EN], default=ZH, help="language of the generated prose (default: zh)")
    build.add_argument("--force", action="store_true", help="overwrite a non-empty output directory")
    build.add_argument("--strict", action="store_true", help="fail when the graph has structural errors")
    build.add_argument("--no-diagram", action="store_true", help="do not emit mermaid diagrams")
    build.add_argument("--no-script", action="store_true", help="do not ship scripts/graph_query.py")
    build.add_argument("--no-graph-asset", action="store_true", help="do not ship assets/graph.json")
    build.add_argument("--no-report", action="store_true", help="do not ship the conversion report")
    build.add_argument("-v", "--verbose", action="store_true", help="print warnings after building")
    build.set_defaults(func=cmd_build)

    validate = sub.add_parser("validate", help="check the graphs without writing anything")
    _add_common_arguments(validate)
    validate.add_argument("--json", action="store_true", help="machine readable output")
    validate.add_argument("--lang", choices=[ZH, EN], default=ZH, help=argparse.SUPPRESS)
    validate.set_defaults(func=cmd_validate)

    stats = sub.add_parser("stats", help="summarise the merged graph")
    _add_common_arguments(stats)
    stats.add_argument("--json", action="store_true", help="machine readable output")
    stats.add_argument("--lang", choices=[ZH, EN], default=ZH, help="language of type labels")
    stats.set_defaults(func=cmd_stats)

    inspect = sub.add_parser("inspect", help="show one node and its neighbours")
    _add_common_arguments(inspect)
    inspect.add_argument("--node-id", required=True, help="node id, or a substring to search for")
    inspect.add_argument("--lang", choices=[ZH, EN], default=ZH, help=argparse.SUPPRESS)
    inspect.set_defaults(func=cmd_inspect)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GraphLoadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
