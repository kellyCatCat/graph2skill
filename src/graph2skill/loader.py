"""Load graph documents from files or directories.

Real-world exports are frequently hand-edited, so the loader is deliberately
forgiving: it accepts trailing commas, ``//`` and ``/* */`` comments, a UTF-8
BOM, and (when PyYAML is installed) YAML documents.  Everything else is a hard
error with the offending file named.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from graph2skill.model import Graph

JSON_SUFFIXES = {".json", ".jsonc"}
YAML_SUFFIXES = {".yaml", ".yml"}
GRAPH_SUFFIXES = JSON_SUFFIXES | YAML_SUFFIXES


class GraphLoadError(RuntimeError):
    """Raised when a graph document cannot be parsed."""


def _scan(text: str, handler) -> str:
    """Walk *text* outside of string literals, letting *handler* rewrite it.

    ``handler(text, index, out)`` returns the next index to continue from, or
    ``None`` to copy the current character verbatim.
    """
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        char = text[i]
        if char in ('"', "'"):
            quote = char
            out.append(char)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        moved = handler(text, i, out)
        if moved is None:
            out.append(char)
            i += 1
        else:
            i = moved
    return "".join(out)


def strip_comments(text: str) -> str:
    """Remove ``//`` line comments and ``/* */`` block comments outside strings."""

    def handler(source: str, i: int, out: List[str]):
        if source[i] == "/" and source.startswith("//", i):
            end = source.find("\n", i)
            return len(source) if end == -1 else end
        if source[i] == "/" and source.startswith("/*", i):
            end = source.find("*/", i + 2)
            return len(source) if end == -1 else end + 2
        return None

    return _scan(text, handler)


def strip_trailing_commas(text: str) -> str:
    """Remove commas that directly precede a ``]`` or ``}`` outside strings."""

    def handler(source: str, i: int, out: List[str]):
        if source[i] != ",":
            return None
        j = i + 1
        while j < len(source) and source[j] in " \t\r\n":
            j += 1
        if j < len(source) and source[j] in "]}":
            return i + 1
        return None

    return _scan(text, handler)


def relax_json(text: str) -> str:
    """Make a hand-edited JSON document parseable by :mod:`json`."""
    return strip_trailing_commas(strip_comments(text))


GRAPH_HINT_KEYS = ("nodes", "relations", "graphId", "entryNodeIds", "decisionTrees", "schemaVersion")


def parse_document(text: str, origin: str = "", prefer_yaml: bool = False) -> Dict[str, Any]:
    """Parse a JSON (or YAML) graph document into a plain dict."""
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise GraphLoadError(f"{origin or '<text>'}: file is empty")

    yaml_error = None
    if prefer_yaml:
        loaded, yaml_error = _try_yaml(text)
        if loaded is not None:
            return loaded

    strict_error = None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        strict_error = exc
    try:
        return json.loads(relax_json(text))
    except json.JSONDecodeError:
        pass

    if not prefer_yaml:
        # Last resort only: YAML is lenient enough to "successfully" parse broken
        # JSON into nonsense, so the result must still look like a graph.
        loaded, yaml_error = _try_yaml(text)
        if loaded is not None and any(key in loaded for key in GRAPH_HINT_KEYS):
            return loaded

    detail = f"{strict_error.msg} (line {strict_error.lineno}, column {strict_error.colno})"
    if yaml_error:
        detail += f"; YAML fallback failed: {yaml_error}"
    raise GraphLoadError(f"{origin or '<text>'}: cannot parse document: {detail}")


def _try_yaml(text: str):
    try:
        import yaml  # type: ignore
    except ImportError:
        return None, "PyYAML is not installed (pip install graph2skill[yaml])"
    try:
        loaded = yaml.safe_load(text)
    except Exception as exc:  # pragma: no cover - depends on PyYAML internals
        return None, str(exc)
    if isinstance(loaded, dict):
        return loaded, None
    return None, "YAML document is not a mapping"


def load_graph(path: os.PathLike | str) -> Graph:
    """Load a single graph document."""
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GraphLoadError(f"{file_path}: cannot read file: {exc}") from exc
    raw = parse_document(text, str(file_path), prefer_yaml=file_path.suffix.lower() in YAML_SUFFIXES)
    try:
        graph = Graph.from_raw(raw, origin=str(file_path))
    except ValueError as exc:
        raise GraphLoadError(f"{file_path}: {exc}") from exc
    return graph


def expand_inputs(paths: Sequence[os.PathLike | str], recursive: bool = True) -> List[Path]:
    """Expand files/directories into a deterministic, de-duplicated file list."""
    found: List[Path] = []
    for raw_path in paths:
        path = Path(raw_path)
        if path.is_dir():
            pattern = "**/*" if recursive else "*"
            children = [p for p in sorted(path.glob(pattern)) if p.is_file() and p.suffix.lower() in GRAPH_SUFFIXES]
            if not children:
                raise GraphLoadError(f"{path}: directory contains no graph documents ({', '.join(sorted(GRAPH_SUFFIXES))})")
            found.extend(children)
        elif path.is_file():
            found.append(path)
        else:
            raise GraphLoadError(f"{path}: no such file or directory")
    unique: List[Path] = []
    seen = set()
    for path in found:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def load_graphs(paths: Sequence[os.PathLike | str], recursive: bool = True) -> List[Graph]:
    """Load every graph document referenced by *paths*."""
    files = expand_inputs(paths, recursive=recursive)
    if not files:
        raise GraphLoadError("no input documents were given")
    return [load_graph(file_path) for file_path in files]


def iter_graph_warnings(graphs: Iterable[Graph]) -> Iterable[Tuple[str, str]]:
    for graph in graphs:
        for warning in graph.warnings:
            yield graph.origin or graph.graph_id, warning
