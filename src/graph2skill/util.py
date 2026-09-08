"""Small helpers shared by the renderers."""

from __future__ import annotations

import hashlib
import posixpath
import re
import unicodedata
from typing import Iterable, List, Optional

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"\s+")


def short_hash(text: str, length: int = 6) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:length]


def slugify(text: str, fallback: str = "item", max_length: int = 60) -> str:
    """ASCII slug; CJK-only input degrades to ``fallback-<hash>``."""
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("-", ascii_text).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:max_length].strip("-")
    if not slug:
        return f"{fallback}-{short_hash(text or fallback)}"
    return slug


def unique_slug(text: str, taken: set, fallback: str = "item") -> str:
    base = slugify(text, fallback=fallback)
    slug = base
    counter = 2
    while slug in taken:
        slug = f"{base}-{counter}"
        counter += 1
    taken.add(slug)
    return slug


def one_line(text: str) -> str:
    return _WS.sub(" ", (text or "").strip())


def truncate(text: str, limit: int = 160, ellipsis: str = "…") -> str:
    text = one_line(text)
    if limit <= 0 or len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


def escape_table_cell(text: str) -> str:
    return one_line(text).replace("\\", "\\\\").replace("|", "\\|")


def escape_md(text: str) -> str:
    """Escape characters that would break inline markdown."""
    return one_line(text).replace("*", "\\*").replace("_", "\\_").replace("[", "\\[").replace("]", "\\]")


def indent_block(text: str, prefix: str) -> str:
    return "\n".join(prefix + line if line else line for line in text.splitlines())


def bullet_list(items: Iterable[str], indent: str = "") -> List[str]:
    return [f"{indent}- {item}" for item in items if item]


def fence(text: str, lang: str = "") -> List[str]:
    """A fenced block whose delimiter never collides with the content."""
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return [f"{ticks}{lang}", text.rstrip("\n"), ticks]


def anchor(node_id: str) -> str:
    """Stable HTML anchor for a node id."""
    return f"n-{slugify(node_id, fallback='node')}-{short_hash(node_id, 4)}"


def yaml_scalar(value: str) -> str:
    """Quote a scalar for the SKILL.md front matter."""
    text = one_line(value)
    if not text:
        return '""'
    needs_quotes = (
        re.search(r"[:#\[\]{}&*!|>'\"%@`,?]", text)
        or text[0] in "-?:," 
        or text.lower() in {"true", "false", "null", "yes", "no", "on", "off", "~"}
    )
    if needs_quotes:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return text


def relative_link(target: str, from_dir: str = "") -> str:
    """Link to *target* (a package-relative path) from a file living in *from_dir*."""
    if not from_dir:
        return target
    return posixpath.relpath(target, from_dir)


def pluralize(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"
