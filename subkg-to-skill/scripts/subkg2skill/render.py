"""Assemble the skill package around one fault entry.

One fault is one file.  A skill covering several scenarios splits them: the
reader follows exactly one scenario, so carrying all of them in the entry file
costs every reader the ones that are not theirs.

    out/isis-neighbor-down/SKILL.md            单故障：四章节，就这一个文件
    out/bgp-troubleshooting/SKILL.md           多场景：入参 + 前置检查 + 场景跳转表
    out/bgp-troubleshooting/reference/*.md     每个场景的排查步骤与根因对照表

The knowledge graph never ships with a skill.  The document is built by
:mod:`subkg2skill.compose` and written out by :mod:`subkg2skill.markdown`;
this module supplies the names, the frontmatter description and the on-disk
layout.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set, Tuple, Union

from subkg2skill.compose import build_multi_doc
from subkg2skill.doc import SHARED_COVERAGE, BuildPolicy, SkillDoc
from subkg2skill.graph import Graph, Node, _text
from subkg2skill.markdown import render_package
from subkg2skill.playbook import Playbook

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
MAX_DESCRIPTION = 1024


class RenderError(RuntimeError):
    """Raised when the requested package cannot be produced."""


@dataclass
class BuildOptions:
    name: str = ""
    description: str = ""
    include_example_specific: bool = False
    keep_undecidable: bool = False
    max_steps: int = 0
    #: 公共前置的门槛：一条采集要被这个比例的场景读到，才留在公共层
    shared_coverage: float = SHARED_COVERAGE
    #: 人工剔除的条目：(匹配到它的 exclude 写法, 节点名)
    excluded: Sequence[Tuple[str, str]] = ()
    #: 本批次已生成的 skill：``node_id -> slug``，用来把跨故障的边写成可打开的引用
    skill_index: Dict[str, str] = field(default_factory=dict)
    #: 每个场景的参考文件名，按场景顺序给；留空的用占位名并报出来。
    #: 按顺序而不是按场景名对齐：两个场景完全可以重名。
    scenario_slugs: Sequence[str] = ()

    def policy(self) -> BuildPolicy:
        return BuildPolicy(
            include_example_specific=self.include_example_specific,
            keep_undecidable=self.keep_undecidable,
            max_steps=self.max_steps,
            shared_coverage=self.shared_coverage,
            skill_index=dict(self.skill_index),
        )


@dataclass
class SkillPackage:
    #: What ships, keyed by path inside the skill directory: ``SKILL.md``, plus
    #: ``reference/<scenario>.md`` when the skill covers several scenarios.
    files: Dict[str, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    #: What the finished document actually contains, after merging and pruning.
    stats: Dict[str, int] = field(default_factory=dict)
    #: The built document, for delivery metrics the caller reports on.
    doc: Optional["SkillDoc"] = None
    #: Causes / checks left out of the document, with the reason for each.
    omitted: List[Tuple[str, str]] = field(default_factory=list)


    def write(self, out_dir: Path, *, force: bool = False) -> List[Path]:
        """Write the skill under *out_dir*; refuse to clobber without ``force``."""
        out_dir = Path(out_dir)
        if out_dir.exists() and any(out_dir.iterdir()) and not force:
            what = "已有技能目录" if (out_dir / "SKILL.md").exists() else "非空目录"
            raise RenderError(f"{out_dir} 是{what}；确认后加 --force 覆盖")
        written: List[Path] = []
        for relative, content in sorted(self.files.items()):
            path = out_dir / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            written.append(path)
        return written


def normalise_name(name: str) -> str:
    """Coerce *name* into the ``^[a-z0-9-]+$`` slug the template requires."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not slug:
        raise RenderError(
            f"技能名 {name!r} 无法转换为合法 slug；模板要求英文名（^[a-z0-9-]+$），请给一个英文 slug"
        )
    return slug[:64].strip("-")


def suggested_slug(symptom: Node, unit: str = "") -> str:
    """A valid fallback slug — ASCII fragments of the name plus the id tail.

    It is deliberately not a translation: Chinese symptom names cannot be turned
    into meaningful English mechanically, so the caller is expected to supply a
    real name and this only keeps the build unblocked.
    """
    hint = re.sub(r"[^a-z0-9]+", "-", symptom.name.lower()).strip("-")
    tail = symptom.node_id.split("_", 1)[-1][:8]
    unit_hint = re.sub(r"[^a-z0-9]+", "-", unit.lower()).strip("-")
    base = f"{hint}-{tail}" if hint else f"fault-{tail}"
    return normalise_name(f"{base}-{unit_hint}" if unit_hint else base)


#: ``scenario-a`` / ``scenario-a16`` — a label, not a name.  Only ever used for
#: a preview; :func:`build_package` refuses to deliver one.
PLACEHOLDER_SLUG_RE = re.compile(r"^scenario-[a-z]\d*$")


def _assign_scenario_slugs(doc: SkillDoc, supplied: Sequence[str] = ()) -> List[Tuple[str, str]]:
    """Give every scenario its file name; return the ones nobody named.

    A Chinese scenario name cannot be turned into a meaningful English file
    name mechanically — the same reason the skill's own name is the caller's to
    give.  A placeholder lets ``plan`` measure a document before the names are
    settled; it is never delivered, because the file name is how a reader and
    an agent tell one scenario from another.
    """
    if not doc.multi:
        return []
    unnamed: List[Tuple[str, str]] = []
    taken: Set[str] = set()
    for index, scenario in enumerate(doc.scenarios):
        wanted = (supplied[index] if index < len(supplied) else "").strip()
        if wanted:
            slug = normalise_name(wanted)
        else:
            slug = f"scenario-{scenario.label.lower()}"
            unnamed.append((scenario.name, slug))
        while slug in taken:  # two scenarios must not share one file
            slug = f"{slug}-{scenario.label.lower()}"
        taken.add(slug)
        scenario.slug = slug
    return unnamed


def _multi_description(scenarios: Sequence[Tuple[str, Playbook]]) -> str:
    """Phenomenon + when to use, across every scenario the document covers."""
    if len(scenarios) == 1:
        name, playbook = scenarios[0]
        return default_description(playbook.symptom, playbook)
    names = "、".join(name for name, _book in scenarios[:6])
    triggers: List[str] = []
    for _name, playbook in scenarios:
        for term in playbook.trigger_terms():
            if term not in triggers:
                triggers.append(term)
    when = "、".join(triggers[:8])
    text = f"覆盖 {len(scenarios)} 个故障场景：{names}。出现 {when} 等现象或告警时使用；"
    text += "先做公共前置采集，再按场景跳转表进入对应场景。"
    return text[: MAX_DESCRIPTION - 1]


def default_description(symptom: Node, playbook: Optional[Playbook] = None) -> str:
    """``故障现象 + 适用时机``, as the template's example does it.

    When several sources were merged, their names and match phrases all become
    trigger wording — that is what makes one skill answer for the manual, the
    battle tree and the case library at once.
    """
    phenomenon = symptom.name
    abnormal = _text(symptom.attr("abnormal_behavior"))
    if abnormal:
        phenomenon = f"{symptom.name}：{abnormal}"
    terms = playbook.trigger_terms() if playbook else (symptom.aliases + symptom.match_phrases)
    triggers: List[str] = []
    for term in terms:
        term = term.strip()
        if term and term != symptom.name and term not in triggers:
            triggers.append(term)
    when = "、".join(triggers[:4])
    trigger_context = _text(symptom.attr("trigger_context"))
    tail = f"（常见于{trigger_context}）" if trigger_context else ""
    if when:
        description = f"{phenomenon}。出现 {when} 等现象或告警时使用{tail}。"
    else:
        description = f"{phenomenon}。出现该现象或相关告警时使用{tail}。"
    return description[: MAX_DESCRIPTION - 1]


# ----------------------------------------------------------------- build
def build_package(
    graph: Graph,
    playbook: Union[Playbook, Sequence[Tuple[str, Playbook]]],
    options: BuildOptions,
) -> SkillPackage:
    """Render one skill package: a single fault, or several as scenarios.

    A sequence of ``(场景名, playbook)`` produces one document with a shared
    collection phase, a routing table and ``### 场景X`` sections.
    """
    scenarios: List[Tuple[str, Playbook]]
    if isinstance(playbook, Playbook):
        scenarios = [(playbook.symptom.name, playbook)]
        primary = playbook
    else:
        scenarios = [(name, book) for name, book in playbook]
        if not scenarios:
            raise RenderError("至少需要一个场景")
        primary = scenarios[0][1]

    name = normalise_name(options.name or suggested_slug(primary.symptom))
    description = options.description or _multi_description(scenarios)
    if len(description) > MAX_DESCRIPTION:
        description = description[: MAX_DESCRIPTION - 1]

    covered: Set[str] = set()
    for _label, book in scenarios:
        covered |= book.covered
    slice_graph = graph.subgraph(covered)
    doc = build_multi_doc(slice_graph, scenarios, options.policy())
    for spec, node_name in options.excluded:
        doc.omitted.append((node_name, f"按 exclude 剔除（匹配 {spec!r}）"))
    unnamed = _assign_scenario_slugs(doc, options.scenario_slugs)
    if unnamed:
        # 文件名是读者和 agent 区分场景的唯一依据，占位名等于没名字。
        listed = "\n".join(f"    {name}" for name, _slug in unnamed)
        raise RenderError(
            f"{len(unnamed)} 个场景还没有英文文件名，它们会写成 reference/scenario-a.md "
            f"这种没有语义的名字：\n{listed}\n"
            "请按语义拟英文名后重试——自动分组时用 --names 给 {node_id: slug}，"
            "用场景清单时在每个场景里填 slug。"
        )
    package = SkillPackage(
        notes=list(doc.notes),
        stats={
            "prechecks": len(doc.prechecks),
            "steps": len(doc.steps),
            "root_causes": len(doc.root_causes) - 1,  # 不含「未找到根因」兜底行
            "omitted": len(doc.omitted),
        },
        doc=doc,
        omitted=list(doc.omitted),
    )
    package.files.update(render_package(doc, name=name, description=description))
    return package
