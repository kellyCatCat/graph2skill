"""Read node/edge exports off disk.

Exports arrive in a few shapes — two flat arrays (``node.json`` + ``edge.json``),
one bundle object with ``nodes``/``edges`` keys, a directory holding either, or
JSON Lines.  Hand-edited files also show up, so the parser tolerates a UTF-8
BOM, ``//`` and ``/* */`` comments and trailing commas.  Anything else is a hard
error naming the offending file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

JSON_SUFFIXES = {".json", ".jsonc"}
JSONL_SUFFIXES = {".jsonl", ".ndjson"}
LOADABLE_SUFFIXES = JSON_SUFFIXES | JSONL_SUFFIXES

NODE_FILE_HINTS = ("node", "nodes")
EDGE_FILE_HINTS = ("edge", "edges")


class SubgraphLoadError(RuntimeError):
    """Raised when an export cannot be read or has an unexpected shape."""


@dataclass
class RawBundle:
    """Nodes and edges exactly as they were written, plus where they came from."""

    nodes: List[Dict[str, Any]] = field(default_factory=list)
    edges: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[str] = field(default_factory=list)

    def extend(self, other: "RawBundle") -> None:
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        self.sources.extend(other.sources)


def _strip_comments_and_trailing_commas(text: str) -> str:
    """Remove ``//`` / ``/* */`` comments and trailing commas outside strings."""
    out: List[str] = []
    i, n = 0, len(text)
    while i < n:
        char = text[i]
        if char == '"':
            out.append(char)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if char == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i = min(i + 2, n)
                continue
        if char == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "]}":
                i += 1
                continue
        out.append(char)
        i += 1
    return "".join(out)


def _parse_text(text: str, origin: Path) -> Any:
    text = text.lstrip("﻿")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_strip_comments_and_trailing_commas(text))
    except json.JSONDecodeError as exc:  # pragma: no cover - message only
        raise SubgraphLoadError(f"{origin}: 不是合法 JSON（{exc}）") from exc


def _parse_jsonl(text: str, origin: Path) -> List[Any]:
    records: List[Any] = []
    for lineno, line in enumerate(text.lstrip("﻿").splitlines(), start=1):
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SubgraphLoadError(f"{origin}:{lineno}: 不是合法 JSON 行（{exc}）") from exc
    return records


def _read(path: Path) -> Any:
    if not path.exists():
        raise SubgraphLoadError(f"{path}: 文件不存在")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SubgraphLoadError(f"{path}: 不是 UTF-8 文本（{exc}）") from exc
    if path.suffix.lower() in JSONL_SUFFIXES:
        return _parse_jsonl(text, path)
    return _parse_text(text, path)


def _as_records(payload: Any, origin: Path, what: str) -> List[Dict[str, Any]]:
    if payload is None:
        return []
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise SubgraphLoadError(f"{origin}: {what} 应为数组，实际是 {type(payload).__name__}")
    records: List[Dict[str, Any]] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise SubgraphLoadError(
                f"{origin}: {what}[{index}] 应为对象，实际是 {type(item).__name__}"
            )
        records.append(item)
    return records


def _split_bundle(payload: Any, origin: Path) -> RawBundle:
    """Interpret one parsed document as nodes, edges or a bundle of both."""
    bundle = RawBundle(sources=[str(origin)])
    if isinstance(payload, dict):
        node_key = next((k for k in ("nodes", "node", "vertices") if k in payload), None)
        edge_key = next((k for k in ("edges", "edge", "links", "relations") if k in payload), None)
        if node_key or edge_key:
            bundle.nodes = _as_records(payload.get(node_key), origin, "nodes")
            bundle.edges = _as_records(payload.get(edge_key), origin, "edges")
            return bundle
        payload = [payload]
    records = _as_records(payload, origin, "records")
    for record in records:
        if "edge_type" in record or ("source" in record and "target" in record):
            bundle.edges.append(record)
        else:
            bundle.nodes.append(record)
    return bundle


def _looks_like(path: Path, hints: Sequence[str]) -> bool:
    stem = path.stem.lower()
    return any(hint == stem or stem.startswith(hint) or stem.endswith(hint) for hint in hints)


def load_paths(
    paths: Iterable[Path],
    *,
    nodes: Optional[Iterable[Path]] = None,
    edges: Optional[Iterable[Path]] = None,
) -> RawBundle:
    """Load every path in *paths* (files or directories) into one bundle.

    ``nodes`` / ``edges`` force the role of a file instead of inferring it from
    the record shape, which matters for exports whose node objects happen to
    carry ``source``-like fields.
    """
    bundle = RawBundle()
    for path in nodes or ():
        bundle.extend(
            RawBundle(nodes=_as_records(_read(Path(path)), Path(path), "nodes"), sources=[str(path)])
        )
    for path in edges or ():
        bundle.extend(
            RawBundle(edges=_as_records(_read(Path(path)), Path(path), "edges"), sources=[str(path)])
        )
    for raw_path in paths or ():
        path = Path(raw_path)
        if path.is_dir():
            bundle.extend(load_directory(path))
            continue
        bundle.extend(_split_bundle(_read(path), path))
    if not bundle.sources:
        raise SubgraphLoadError("没有给出任何输入文件")
    return bundle


def load_directory(directory: Path) -> RawBundle:
    """Load ``*.json`` / ``*.jsonl`` from *directory*, nodes before edges."""
    candidates = sorted(
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in LOADABLE_SUFFIXES
    )
    if not candidates:
        raise SubgraphLoadError(f"{directory}: 目录下没有 .json/.jsonc/.jsonl 文件")
    ordered = [p for p in candidates if _looks_like(p, NODE_FILE_HINTS)]
    ordered += [p for p in candidates if _looks_like(p, EDGE_FILE_HINTS) and p not in ordered]
    ordered += [p for p in candidates if p not in ordered]
    bundle = RawBundle()
    for path in ordered:
        bundle.extend(_split_bundle(_read(path), path))
    return bundle


def load(
    inputs: Sequence[str],
    *,
    node_files: Sequence[str] = (),
    edge_files: Sequence[str] = (),
) -> Tuple[RawBundle, List[str]]:
    """Convenience wrapper used by the CLI; returns the bundle and its sources."""
    bundle = load_paths(
        [Path(p) for p in inputs],
        nodes=[Path(p) for p in node_files],
        edges=[Path(p) for p in edge_files],
    )
    return bundle, list(bundle.sources)
