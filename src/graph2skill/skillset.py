"""The manifest that ties each skill document to its source subgraph.

Existing skills are hand written, so graph2skill needs to be told (or to infer)
three things per skill: which markdown file it is, which JSON subgraph it was
built from, and which other skills it includes (``common.md``).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

MANIFEST_VERSION = 1
DEFAULT_MANIFEST_NAME = "skillset.json"

SKILL_DOC_PATTERNS = ("SKILL-*.md", "SKILL_*.md", "skill-*.md", "skill_*.md")
COMMON_DOC_NAMES = ("common.md", "COMMON.md", "通用规范.md")
GRAPH_DIR_CANDIDATES = ("graphs", "graph", "json", ".")
REFERENCE_DIR_CANDIDATES = ("reference", "references")
STATS_RE = re.compile(r"(statistics/[\w\-./]+\.json)")
MD_LINK_RE = re.compile(r"([\w\-.]+\.md)")


class SkillSetError(RuntimeError):
    """Raised when the manifest is unusable."""


@dataclass
class SkillEntry:
    """One skill document and everything graph2skill needs to update it."""

    name: str
    doc: str
    graph: str = ""
    role: str = "scenario"  # scenario | common
    includes: List[str] = field(default_factory=list)
    reference_dir: str = "reference"
    statistics: str = ""
    sections: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_raw(cls, raw: Dict[str, object]) -> "SkillEntry":
        name = str(raw.get("name") or "").strip()
        doc = str(raw.get("doc") or "").strip()
        if not name or not doc:
            raise SkillSetError(f"skill entry needs both 'name' and 'doc': {raw!r}")
        return cls(
            name=name,
            doc=doc,
            graph=str(raw.get("graph") or "").strip(),
            role=str(raw.get("role") or "scenario").strip() or "scenario",
            includes=[str(item) for item in (raw.get("includes") or [])],
            reference_dir=str(raw.get("referenceDir") or raw.get("reference_dir") or "reference"),
            statistics=str(raw.get("statistics") or ""),
            sections={str(k): str(v) for k, v in (raw.get("sections") or {}).items()},
        )

    def to_dict(self) -> Dict[str, object]:
        payload: Dict[str, object] = {"name": self.name, "doc": self.doc}
        if self.role != "scenario":
            payload["role"] = self.role
        if self.graph:
            payload["graph"] = self.graph
        if self.includes:
            payload["includes"] = list(self.includes)
        if self.reference_dir != "reference":
            payload["referenceDir"] = self.reference_dir
        if self.statistics:
            payload["statistics"] = self.statistics
        if self.sections:
            payload["sections"] = dict(self.sections)
        return payload


@dataclass
class SkillSet:
    """A directory of related skill documents."""

    root: Path
    skills: List[SkillEntry] = field(default_factory=list)
    manifest_path: Optional[Path] = None

    # -- lookup -----------------------------------------------------------------
    def get(self, name: str) -> SkillEntry:
        for entry in self.skills:
            if entry.name == name:
                return entry
        for entry in self.skills:  # accept a document path or file name too
            if entry.doc == name or Path(entry.doc).name == Path(name).name:
                return entry
        known = ", ".join(entry.name for entry in self.skills) or "(none)"
        raise SkillSetError(f"unknown skill '{name}'; known skills: {known}")

    def doc_path(self, entry: SkillEntry) -> Path:
        return self.root / entry.doc

    def graph_path(self, entry: SkillEntry) -> Path:
        if not entry.graph:
            raise SkillSetError(
                f"skill '{entry.name}' has no 'graph' in the manifest; "
                "add the JSON subgraph it was generated from"
            )
        return self.root / entry.graph

    def reference_dir(self, entry: SkillEntry) -> Path:
        return self.root / entry.reference_dir

    def included_entries(self, entry: SkillEntry) -> List[SkillEntry]:
        """Transitive includes, nearest first, without duplicates."""
        seen: List[SkillEntry] = []
        queue = list(entry.includes)
        while queue:
            name = queue.pop(0)
            try:
                included = self.get(name)
            except SkillSetError:
                continue
            if any(existing.name == included.name for existing in seen):
                continue
            seen.append(included)
            queue.extend(included.includes)
        return seen

    # -- persistence ------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "SkillSet":
        path = Path(path)
        if path.is_dir():
            path = path / DEFAULT_MANIFEST_NAME
        if not path.is_file():
            raise SkillSetError(f"{path}: manifest not found (run `graph2skill skillset init` first)")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SkillSetError(f"{path}: invalid manifest JSON: {exc}") from exc
        root = (path.parent / str(raw.get("root") or ".")).resolve()
        skills = [SkillEntry.from_raw(item) for item in raw.get("skills") or []]
        if not skills:
            raise SkillSetError(f"{path}: manifest lists no skills")
        return cls(root=root, skills=skills, manifest_path=path)

    def to_dict(self, manifest_dir: Optional[Path] = None) -> Dict[str, object]:
        """Serialise the manifest; paths stay relative to where it is stored."""
        root = "."
        if manifest_dir is not None:
            root = os.path.relpath(self.root, Path(manifest_dir).resolve()).replace("\\", "/")
        return {
            "version": MANIFEST_VERSION,
            "root": root,
            "skills": [entry.to_dict() for entry in self.skills],
        }

    def save(self, path: Optional[Path] = None) -> Path:
        target = Path(path or self.manifest_path or (self.root / DEFAULT_MANIFEST_NAME))
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self.to_dict(manifest_dir=target.parent)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.manifest_path = target
        return target

    # -- discovery --------------------------------------------------------------
    @classmethod
    def discover(cls, root: Path) -> "SkillSet":
        """Infer a manifest from an existing directory of skill documents."""
        root = Path(root).resolve()
        if not root.is_dir():
            raise SkillSetError(f"{root}: not a directory")
        docs: List[Path] = []
        for pattern in SKILL_DOC_PATTERNS:
            docs.extend(sorted(root.glob(pattern)))
        commons = [root / name for name in COMMON_DOC_NAMES if (root / name).is_file()]
        if not docs and not commons:
            raise SkillSetError(f"{root}: no SKILL-*.md or common.md found")

        reference_dir = next(
            (name for name in REFERENCE_DIR_CANDIDATES if (root / name).is_dir()), REFERENCE_DIR_CANDIDATES[0]
        )
        entries: List[SkillEntry] = []
        for path in commons:
            entries.append(
                SkillEntry(
                    name=path.stem.lower(),
                    doc=path.name,
                    role="common",
                    graph=_guess_graph(root, path.stem.lower()),
                    reference_dir=reference_dir,
                )
            )
        common_names = {entry.doc: entry.name for entry in entries}
        for path in sorted(set(docs)):
            name = re.sub(r"^skill[-_]", "", path.stem, flags=re.IGNORECASE).lower()
            text = path.read_text(encoding="utf-8", errors="replace")
            includes = [
                common_names[link]
                for link in dict.fromkeys(MD_LINK_RE.findall(text))
                if link in common_names and link != path.name
            ]
            stats_match = STATS_RE.search(text)
            entries.append(
                SkillEntry(
                    name=name,
                    doc=path.name,
                    graph=_guess_graph(root, name),
                    includes=includes,
                    reference_dir=reference_dir,
                    statistics=stats_match.group(1) if stats_match else "",
                )
            )
        return cls(root=root, skills=entries)


def _guess_graph(root: Path, name: str) -> str:
    """Find the subgraph paired with a skill: exact names first, then one prefix match.

    `SKILL-isis.md` is paired with `isis_all.json` this way; an ambiguous prefix
    (two candidates) is left unresolved rather than guessed.
    """
    for directory in GRAPH_DIR_CANDIDATES:
        for candidate in (f"{name}.json", f"graph-{name}.json", f"{name}-graph.json"):
            path = root / directory / candidate
            if path.is_file():
                return str(path.relative_to(root)).replace("\\", "/")
    for directory in GRAPH_DIR_CANDIDATES:
        base = root / directory
        if not base.is_dir():
            continue
        matches = sorted(
            path
            for path in base.glob("*.json")
            if path.is_file() and path.stem.lower().startswith(name) and path.name != DEFAULT_MANIFEST_NAME
        )
        if len(matches) == 1:
            return str(matches[0].relative_to(root)).replace("\\", "/")
    return ""


def missing_graphs(skillset: SkillSet, roles: Optional[Sequence[str]] = ("scenario",)) -> List[SkillEntry]:
    """Skills that cannot be merged into because their subgraph is missing.

    Only scenario skills are blocking by default: a common skill without a graph
    still works — its nodes are then matched by ``common.md`` section titles.
    """
    return [
        entry
        for entry in skillset.skills
        if (roles is None or entry.role in roles) and (not entry.graph or not (skillset.root / entry.graph).is_file())
    ]
