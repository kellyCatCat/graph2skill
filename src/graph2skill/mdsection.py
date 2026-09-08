"""Surgical markdown editing.

Existing skill documents are hand-written, so graph2skill never re-renders them
wholesale.  This module locates headings, tables and marker-delimited managed
blocks, and rewrites *only* the lines that actually change; everything else is
kept byte for byte.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)[.、]?\s+(.*)$")
FENCE_RE = re.compile(r"^\s*(```+|~~~+)")

BLOCK_BEGIN = "<!-- graph2skill:begin id={block_id} -->"
BLOCK_END = "<!-- graph2skill:end id={block_id} -->"
BLOCK_BEGIN_RE = re.compile(r"^\s*<!--\s*graph2skill:begin\s+id=(?P<id>[^\s>]+)\s*-->\s*$")
BLOCK_END_RE = re.compile(r"^\s*<!--\s*graph2skill:end\s+id=(?P<id>[^\s>]+)\s*-->\s*$")


@dataclass
class Section:
    """A heading and everything under it, up to the next heading of same/lower level."""

    level: int
    number: str
    title: str
    start: int  # index of the heading line
    end: int  # exclusive

    @property
    def heading_line(self) -> int:
        return self.start

    def contains(self, index: int) -> bool:
        return self.start <= index < self.end


@dataclass
class Table:
    """A GFM pipe table found inside a document."""

    start: int  # index of the header row
    end: int  # exclusive
    header: List[str]
    rows: List[List[str]] = field(default_factory=list)
    alignment: str = ""

    def render(self) -> List[str]:
        width = len(self.header)
        lines = ["| " + " | ".join(self.header) + " |"]
        lines.append(self.alignment or "| " + " | ".join(["---"] * width) + " |")
        for row in self.rows:
            cells = list(row) + [""] * (width - len(row))
            lines.append("| " + " | ".join(cells[:width]) + " |")
        return lines

    def column_index(self, name: str) -> Optional[int]:
        for index, cell in enumerate(self.header):
            if cell.strip() == name.strip():
                return index
        return None


def split_row(line: str) -> List[str]:
    """Split a pipe-table row, honouring ``\\|`` escapes."""
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|") and not stripped.endswith("\\|"):
        stripped = stripped[:-1]
    cells: List[str] = []
    current: List[str] = []
    index = 0
    while index < len(stripped):
        char = stripped[index]
        if char == "\\" and index + 1 < len(stripped) and stripped[index + 1] == "|":
            current.append("\\|")
            index += 2
            continue
        if char == "|":
            cells.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    cells.append("".join(current).strip())
    return cells


def is_alignment_row(line: str) -> bool:
    cells = split_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{1,}:?", cell.strip()) for cell in cells if cell.strip() != "")


class MarkdownDoc:
    """A parsed markdown document that can be edited in place."""

    def __init__(self, text: str, path: Optional[str] = None):
        self.path = path
        self.newline = "\r\n" if "\r\n" in text else "\n"
        self.ends_with_newline = text.endswith(("\n", "\r\n")) or text == ""
        self.lines: List[str] = text.replace("\r\n", "\n").split("\n")
        if self.ends_with_newline and self.lines and self.lines[-1] == "":
            self.lines.pop()
        self.dirty = False

    # -- reading ----------------------------------------------------------------
    @property
    def text(self) -> str:
        body = self.newline.join(self.lines)
        return body + self.newline if self.ends_with_newline else body

    def code_fence_mask(self) -> List[bool]:
        """True for lines that sit inside a fenced code block."""
        mask = [False] * len(self.lines)
        fence: Optional[str] = None
        for index, line in enumerate(self.lines):
            match = FENCE_RE.match(line)
            if fence is None and match:
                fence = match.group(1)[:3]
                mask[index] = True
                continue
            if fence is not None:
                mask[index] = True
                if match and match.group(1).startswith(fence):
                    fence = None
        return mask

    def sections(self) -> List[Section]:
        mask = self.code_fence_mask()
        raw: List[Tuple[int, int, str, str]] = []
        for index, line in enumerate(self.lines):
            if mask[index]:
                continue
            match = HEADING_RE.match(line)
            if not match:
                continue
            level = len(match.group(1))
            rest = match.group(2).strip()
            number_match = NUMBER_RE.match(rest)
            number = number_match.group(1) if number_match else ""
            title = number_match.group(2).strip() if number_match else rest
            raw.append((index, level, number, title))
        sections: List[Section] = []
        for position, (index, level, number, title) in enumerate(raw):
            end = len(self.lines)
            for later_index, later_level, _, _ in raw[position + 1 :]:
                if later_level <= level:
                    end = later_index
                    break
            sections.append(Section(level=level, number=number, title=title, start=index, end=end))
        return sections

    def find_section(
        self,
        number: Optional[str] = None,
        title_contains: Optional[str] = None,
        level: Optional[int] = None,
        within: Optional[Section] = None,
    ) -> Optional[Section]:
        for section in self.sections():
            if within is not None and not (within.start < section.start < within.end):
                continue
            if number is not None and section.number != number:
                continue
            if level is not None and section.level != level:
                continue
            if title_contains is not None and title_contains not in section.title:
                continue
            return section
        return None

    def subsections(self, section: Section) -> List[Section]:
        return [
            candidate
            for candidate in self.sections()
            if section.start < candidate.start < section.end and candidate.level == section.level + 1
        ]

    def tables(self, section: Optional[Section] = None) -> List[Table]:
        mask = self.code_fence_mask()
        start = section.start if section else 0
        stop = section.end if section else len(self.lines)
        tables: List[Table] = []
        index = start
        while index < stop - 1:
            if mask[index] or "|" not in self.lines[index]:
                index += 1
                continue
            if not is_alignment_row(self.lines[index + 1]) or "|" not in self.lines[index + 1]:
                index += 1
                continue
            header = split_row(self.lines[index])
            alignment = self.lines[index + 1].strip()
            rows: List[List[str]] = []
            cursor = index + 2
            while cursor < stop and "|" in self.lines[cursor] and self.lines[cursor].strip() and not mask[cursor]:
                rows.append(split_row(self.lines[cursor]))
                cursor += 1
            tables.append(Table(start=index, end=cursor, header=header, rows=rows, alignment=alignment))
            index = cursor
        return tables

    # -- writing ----------------------------------------------------------------
    def replace_lines(self, start: int, end: int, new_lines: Sequence[str]) -> bool:
        old = self.lines[start:end]
        if list(old) == list(new_lines):
            return False
        self.lines[start:end] = list(new_lines)
        self.dirty = True
        return True

    def insert_lines(self, at: int, new_lines: Sequence[str]) -> bool:
        if not new_lines:
            return False
        self.lines[at:at] = list(new_lines)
        self.dirty = True
        return True

    def update_table(self, table: Table) -> bool:
        return self.replace_lines(table.start, table.end, table.render())

    # -- managed blocks ---------------------------------------------------------
    def find_block(self, block_id: str) -> Optional[Tuple[int, int]]:
        """Return ``(start, end)`` line range of a managed block, markers included."""
        start = None
        for index, line in enumerate(self.lines):
            begin = BLOCK_BEGIN_RE.match(line)
            if begin and begin.group("id") == block_id:
                start = index
                continue
            end = BLOCK_END_RE.match(line)
            if end and end.group("id") == block_id and start is not None:
                return start, index + 1
        return None

    def upsert_block(
        self,
        block_id: str,
        content: Sequence[str],
        insert_at: Optional[int] = None,
    ) -> bool:
        """Create or refresh a managed block; returns True when the file changed."""
        wrapped = [BLOCK_BEGIN.format(block_id=block_id), *content, BLOCK_END.format(block_id=block_id)]
        existing = self.find_block(block_id)
        if existing is not None:
            return self.replace_lines(existing[0], existing[1], wrapped)
        position = len(self.lines) if insert_at is None else insert_at
        prefix: List[str] = []
        if position > 0 and self.lines[position - 1].strip():
            prefix = [""]
        suffix = [""] if position < len(self.lines) and self.lines[position].strip() else []
        return self.insert_lines(position, prefix + wrapped + suffix)


def upsert_rows(
    table: Table,
    rows: Sequence[Sequence[str]],
    key_column: int = 0,
    ordinal_column: Optional[int] = None,
) -> Tuple[int, int]:
    """Merge *rows* into *table*, returning ``(added, updated)``.

    Rows are matched on the normalised value of *key_column*; existing cells are
    only overwritten when the incoming cell is non-empty, so hand-written notes
    survive a re-run.  When *ordinal_column* is given it is renumbered 1..n.
    """
    added = updated = 0
    index_by_key = {}
    for position, row in enumerate(table.rows):
        if key_column < len(row):
            index_by_key.setdefault(_norm(row[key_column]), position)
    for row in rows:
        key = _norm(row[key_column]) if key_column < len(row) else ""
        position = index_by_key.get(key)
        if position is None:
            table.rows.append(list(row))
            index_by_key[key] = len(table.rows) - 1
            added += 1
            continue
        current = table.rows[position]
        merged = list(current) + [""] * (len(row) - len(current))
        changed = False
        for column, cell in enumerate(row):
            if column == ordinal_column:
                continue
            if cell and _norm(merged[column]) != _norm(cell):
                merged[column] = cell
                changed = True
        if changed:
            table.rows[position] = merged
            updated += 1
    if ordinal_column is not None:
        for position, row in enumerate(table.rows):
            while len(row) <= ordinal_column:
                row.append("")
            row[ordinal_column] = str(position + 1)
    return added, updated


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip().lower()
