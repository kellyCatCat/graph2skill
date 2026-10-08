"""Decide what one skill covers: which faults, read from which graph.

A skill's scenarios come from one of three places — named entries
(``build --entry``), a scenario manifest (``--scenarios``), or the same
automatic grouping ``list`` shows — and all three end as a
:class:`ScenarioSet`, so building, planning and checking never need to know
which it was.  Nothing here reads command-line arguments.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple

from subkg2skill.graph import Graph, Node
from subkg2skill.playbook import (
    Playbook,
    build_merged_playbook,
    entry_scenarios,
    entry_symptoms,
    fault_groups,
    fault_key,
)
from subkg2skill.render import RenderError, normalise_name, suggested_slug


@dataclass
class ScenarioSet:
    """The faults one skill covers, and the graphs it is built and checked against."""

    #: ``(场景名, playbook)`` per scenario, in document order.
    named: List[Tuple[str, Playbook]]
    #: Diagnostic units the scenarios were scoped to.
    units: List[str]
    #: The graph scoped to those units, before exclusions.  Exclusions are an
    #: editorial decision about what to document, not about what the graph
    #: contains — so the grounding check reads this one, or a scenario's own
    #: material would look invented.
    reference: Graph
    #: ``reference`` minus the excluded nodes: what the document is built from.
    graph: Graph
    #: ``(exclude 写法, 节点名)`` for every node a person excluded.
    removed: List[Tuple[str, str]] = field(default_factory=list)
    #: Reference file name per scenario, in order; empty where nobody named one.
    slugs: List[str] = field(default_factory=list)
    #: Skill name and description a manifest supplied.
    name: str = ""
    description: str = ""
    #: Things the caller should tell the user before building.
    notes: List[str] = field(default_factory=list)

    @property
    def covered(self) -> Set[str]:
        covered: Set[str] = set()
        for _name, playbook in self.named:
            covered |= playbook.covered
        return covered


# ------------------------------------------------------------------ select
def select(
    graph: Graph,
    *,
    roots: Sequence[str] = (),
    node_types: Sequence[str] = (),
    sections: Sequence[str] = (),
    vendors: Sequence[str] = (),
    query: str = "",
    depth: int = 0,
) -> Graph:
    """Narrow *graph* to what the seeds reach; no filters means all of it."""
    seeds: Set[str] = set()
    if roots:
        seeds |= resolve_roots(graph, roots)
    if node_types or sections or vendors or query:
        filtered = graph.filter_nodes(
            node_types=node_types, sections=sections, vendors=vendors, query=query
        )
        seeds = (seeds & filtered) if roots else filtered
    if not seeds:
        if roots or node_types or sections or vendors or query:
            raise RenderError("筛选条件没有选中任何节点")
        return graph
    return graph.subgraph(graph.reachable(seeds, depth=depth if depth > 0 else None))


def resolve_roots(graph: Graph, roots: Sequence[str]) -> Set[str]:
    resolved: Set[str] = set()
    for root in roots:
        if root in graph:
            resolved.add(root)
            continue
        prefixed = [nid for nid in graph.nodes if nid.startswith(root)]
        if prefixed:
            resolved.update(prefixed)
            continue
        hits = graph.search(root)
        if not hits:
            raise RenderError(f"起点 {root!r} 既不是 node_id，也没有匹配到任何节点")
        resolved.update(node.node_id for node in hits)
    return resolved


def resolve_entry(graph: Graph, entry: str) -> Node:
    """Find the one symptom a skill will document."""
    symptoms = entry_symptoms(graph)
    if not symptoms:
        raise RenderError("子图里没有 symptom 节点，无法生成 skill")
    if not entry:
        if len(symptoms) == 1:
            return symptoms[0]
        raise RenderError(
            f"子图里有 {len(symptoms)} 个故障入口，请用 --entry 指定一个"
            "（先跑 `list` 看清单），或用 build --each 批量生成"
        )
    if entry in graph and graph.nodes[entry].node_type == "symptom":
        return graph.nodes[entry]
    candidates = [node for node in symptoms if node.node_id.startswith(entry)]
    if not candidates:
        candidates = [node for node in graph.search(entry, node_types=["symptom"])]
    if not candidates:
        raise RenderError(f"入口 {entry!r} 没有匹配到任何 symptom 节点")
    if len(candidates) > 1:
        listed = "、".join(f"{node.name}({node.node_id})" for node in candidates[:5])
        raise RenderError(f"入口 {entry!r} 匹配到多个症状：{listed}…；请给出确切的 node_id")
    return candidates[0]


def same_fault_symptoms(graph: Graph, symptom: Node) -> List[Node]:
    """*symptom* first, then symptom nodes from other sources describing the same fault."""
    key = fault_key(symptom.name)
    same = [
        node
        for node in graph.of_type("symptom")
        if node.node_id != symptom.node_id and fault_key(node.name) == key
    ]
    return [symptom] + sorted(same, key=lambda node: node.node_id)


def units_of(graph: Graph, symptom: Node) -> Dict[str, int]:
    return graph.edge_units(symptom.node_id, "has_cause", "diagnosed_by", "next_step")


def resolve_units(
    graph: Graph, symptoms: Sequence[Node], units: Sequence[str], all_units: bool
) -> List[str]:
    """Pick the diagnostic units this skill covers, or explain why one is needed."""
    if all_units:
        return []
    available: Dict[str, int] = {}
    for symptom in symptoms:
        for name, count in units_of(graph, symptom).items():
            if name != "(未标注)":
                available[name] = available.get(name, 0) + count
    if units:
        resolved: List[str] = []
        for unit in units:
            if not any(Graph.unit_matches(name, unit) for name in available):
                listed = "、".join(list(available)[:8]) or "（无）"
                raise RenderError(f"诊断单元 {unit!r} 在这些症状下没有关系；现有单元：{listed}")
            resolved.append(unit)
        return resolved
    if len(available) <= 1:
        return [next(iter(available))] if available else []
    if len(symptoms) > 1:
        # Merging sources is the point of this build; their units all belong.
        return list(available)
    listed = "\n".join(f"    {name}（{count} 条关系）" for name, count in list(available.items())[:10])
    raise RenderError(
        f"“{symptoms[0].name}”的关系分布在 {len(available)} 个诊断单元里，合成一份 skill 会把多个"
        f"故障场景混在一起。请用 --unit 指定其中一个（可重复；或 --all-units 明确要合并）：\n{listed}"
    )


def apply_exclusions(
    graph: Graph,
    specs: Sequence[str],
    removed: List[Tuple[str, str]],
    removed_ids: Set[str],
) -> Graph:
    """Drop nodes a person judged unrelated, recording what went.

    An exclusion that matches nothing is an error, not a no-op: a typo would
    otherwise silently leave the unrelated material in the document.
    """
    specs = [spec for spec in specs if spec]
    if not specs:
        return graph
    ids, matched = graph.resolve_exclusions(specs)
    unmatched = set(specs) - {spec for spec, _node in matched}
    if unmatched:
        raise RenderError(
            "以下 --exclude / exclude 没有匹配到任何节点（写错了会静默漏掉内容）："
            + "、".join(sorted(unmatched))
        )
    for spec, node in matched:
        if node.node_id not in removed_ids:
            removed_ids.add(node.node_id)
            removed.append((spec, node.name))
    return graph.without(ids)


# ------------------------------------------------------------------- files
def _load_json(path: Path, what: str):
    if not path.exists():
        raise RenderError(f"{path}: {what}文件不存在")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RenderError(f"{path}: 不是合法 JSON（{exc}）") from exc


def load_slug_map(path: Path, what: str) -> Dict[str, str]:
    """Read a ``{node_id: slug}`` JSON file (``--names`` / ``--skill-index``)."""
    payload = _load_json(path, what)
    if not isinstance(payload, dict):
        raise RenderError(f"{path}: 应为 {{node_id: slug}} 的对象")
    return {str(key): str(value) for key, value in payload.items()}


def load_manifest(path: Path) -> Dict:
    manifest = _load_json(path, "场景清单")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("scenarios"), list):
        raise RenderError(f"{path}: 应为 {{name, description, scenarios: [...]}} 的对象")
    if not manifest["scenarios"]:
        raise RenderError(f"{path}: scenarios 是空的")
    return manifest


# --------------------------------------------------------------- scenarios
def from_entries(
    graph: Graph,
    symptoms: Sequence[Node],
    units: Sequence[str] = (),
    exclude: Sequence[str] = (),
    cache: Optional[Dict[str, Graph]] = None,
) -> ScenarioSet:
    """One fault: these symptom nodes (several when sources are merged) in these units.

    ``cache`` lets a batch reuse one unit's scoped graph across the skills it builds.
    """
    units = [unit for unit in units if unit]
    unit_key = "|".join(sorted(units))
    if cache is not None and unit_key in cache:
        reference = cache[unit_key]
    else:
        reference = graph.scope_to_units(units) if units else graph
        if cache is not None:
            cache[unit_key] = reference
    removed: List[Tuple[str, str]] = []
    scoped = apply_exclusions(reference, exclude, removed, set())
    playbook = build_merged_playbook(scoped, list(symptoms), units)
    return ScenarioSet(
        named=[(playbook.symptom.name, playbook)],
        units=units,
        reference=reference,
        graph=scoped,
        removed=removed,
    )


def from_manifest(graph: Graph, path: Path, exclude: Sequence[str] = ()) -> ScenarioSet:
    """The scenarios a person settled on in a manifest (``list --export-scenarios``)."""
    manifest = load_manifest(path)
    units: List[str] = []
    for entry in manifest["scenarios"]:
        units += [unit for unit in entry.get("units") or [] if unit]
    units = list(dict.fromkeys(units))
    reference = graph.scope_to_units(units) if units else graph

    removed: List[Tuple[str, str]] = []
    removed_ids: Set[str] = set()
    named: List[Tuple[str, Playbook]] = []
    slugs: List[str] = []
    for index, entry in enumerate(manifest["scenarios"]):
        ids = entry.get("entries") or []
        if not ids:
            raise RenderError(f"第 {index + 1} 个场景没有 entries")
        symptoms = [resolve_entry(reference, node_id) for node_id in ids]
        unit_scope = [unit for unit in entry.get("units") or [] if unit]
        base = reference.scope_to_units(unit_scope) if unit_scope else reference
        # 全局 --exclude 对所有场景生效，清单里的 exclude 只作用于本场景
        base = apply_exclusions(
            base, list(exclude) + list(entry.get("exclude") or []), removed, removed_ids
        )
        playbook = build_merged_playbook(base, symptoms, unit_scope)
        named.append((entry.get("name") or symptoms[0].name, playbook))
        slugs.append(str(entry.get("slug") or "").strip())
    return ScenarioSet(
        named=named,
        units=units,
        reference=reference,
        graph=reference.without(removed_ids),
        removed=removed,
        slugs=slugs,
        name=manifest.get("name", ""),
        description=manifest.get("description", ""),
    )


def from_groups(
    graph: Graph,
    *,
    min_causes: int = 1,
    merge: bool = True,
    limit: int = 0,
    exclude: Sequence[str] = (),
    names: Optional[Dict[str, str]] = None,
) -> ScenarioSet:
    """Every fault of this subgraph as one scenario — what ``list`` shows.

    ``names`` is the ``{node_id: slug}`` map giving each scenario its file name.
    """
    notes: List[str] = []
    groups = fault_groups(graph, min_causes=min_causes, merge=merge)
    if not groups and min_causes and entry_symptoms(graph):
        # 这张图的故障全都候选原因不足。门槛是用来挑"值不值得单独成一份 skill"的，
        # 而调用方已经指名要这个子图的 skill——空着手回去不如照实做出来，
        # 排查步骤会是空的，交付统计和提示都会说。
        groups = fault_groups(graph, min_causes=0, merge=merge)
        if groups:
            notes.append(
                f"{len(groups)} 个故障的候选原因都少于 {min_causes} 个，"
                "仍按本子图生成一份（排查步骤会是空的）"
            )
    if limit:
        groups = groups[:limit]
    if not groups:
        raise RenderError("子图里没有可生成的故障场景")
    units = list(dict.fromkeys(unit for group in groups for unit in group.units))
    reference = graph.scope_to_units(units) if units else graph
    removed: List[Tuple[str, str]] = []
    removed_ids: Set[str] = set()
    scoped = apply_exclusions(reference, exclude, removed, removed_ids)
    named = [
        (
            group.name,
            build_merged_playbook(
                scoped.scope_to_units(group.units) if group.units else scoped,
                group.symptoms,
                group.units,
            ),
        )
        for group in groups
    ]
    names = names or {}
    return ScenarioSet(
        named=named,
        units=units,
        reference=reference,
        graph=reference.without(removed_ids),
        removed=removed,
        slugs=[
            names.get(book.symptom.node_id) or names.get(fault_key(book.symptom.name)) or ""
            for _name, book in named
        ],
        notes=notes,
    )


@dataclass
class EachPlan:
    """``build --each``: one skill per fault, every name settled before any is written."""

    #: ``(symptoms, units, skill name)`` per skill.
    skills: List[Tuple[List[Node], List[str], str]]
    #: Skills that got a mechanical slug, as ``key  症状名  →  slug`` lines.
    unnamed: List[str]
    #: Scenarios left out for having fewer than ``min_causes`` causes.
    skipped: int

    def index(self, supplied: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        """``node_id -> slug`` for the whole batch, so hand-offs can name their target.

        Every merged source of a fault answers to the same skill: a cross-fault
        edge may point at any one of their symptom nodes.
        """
        index = dict(supplied or {})
        for symptoms, _units, name in self.skills:
            for node in symptoms:
                index.setdefault(node.node_id, normalise_name(name))
        return index


def plan_each(
    graph: Graph,
    *,
    names: Optional[Dict[str, str]] = None,
    all_units: bool = False,
    min_causes: int = 1,
    merge: bool = True,
    limit: int = 0,
) -> EachPlan:
    """Name every fault's skill first.

    A skill can only point at another one by its slug, and a batch does not
    know the slug of a skill it has not reached yet — so the names are settled
    before anything is written.
    """
    names = names or {}
    skipped = 0
    if all_units:
        faults = [([symptom], []) for symptom in entry_symptoms(graph)]
    else:
        groups = fault_groups(graph, min_causes=min_causes, merge=merge)
        skipped = len(entry_scenarios(graph, min_causes=0)) - sum(
            len(group.symptoms) for group in groups
        )
        faults = [(list(group.symptoms), list(group.units)) for group in groups]
    if limit:
        faults = faults[:limit]
    if not faults:
        raise RenderError("子图里没有可生成的故障场景（symptom × 诊断单元）")

    skills: List[Tuple[List[Node], List[str], str]] = []
    unnamed: List[str] = []
    for symptoms, units in faults:
        symptom = symptoms[0]
        unit = units[0] if units else ""
        key = f"{symptom.node_id}@{unit}" if unit else symptom.node_id
        name = (
            names.get(fault_key(symptom.name))
            or names.get(key)
            or names.get(symptom.node_id)
            or names.get(symptom.name)
            or ""
        )
        if not name:
            name = suggested_slug(symptom, unit)
            unnamed.append(f"{key}  {symptom.name}  →  {name}")
        skills.append((symptoms, units, name))
    return EachPlan(skills=skills, unnamed=unnamed, skipped=skipped)
