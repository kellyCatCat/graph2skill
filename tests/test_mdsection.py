import pytest

from graph2skill.mdsection import MarkdownDoc, Table, split_row, upsert_rows

SAMPLE = """# Title

## 1. 触发条件

正文一。

### 1.1 子章节

| 编号 | 名称 | 备注 |
| --- | --- | --- |
| 1 | 甲 | 手写备注 |
| 2 | 乙 |  |

## 2. 其他

```
## 这是代码块里的假标题
| 也不是 | 表格 |
```

结束。
"""


def test_round_trip_is_byte_identical(examples_dir):
    for name in ("SKILL-bgp.md", "common.md"):
        text = (examples_dir / "skillset" / name).read_text(encoding="utf-8")
        assert MarkdownDoc(text).text == text


def test_sections_carry_numbers_and_levels():
    doc = MarkdownDoc(SAMPLE)
    sections = doc.sections()
    assert [(s.level, s.number, s.title) for s in sections[:4]] == [
        (1, "", "Title"),
        (2, "1", "触发条件"),
        (3, "1.1", "子章节"),
        (2, "2", "其他"),
    ]


def test_headings_inside_code_fences_are_ignored():
    doc = MarkdownDoc(SAMPLE)
    assert not any("代码块" in section.title for section in doc.sections())


def test_tables_inside_code_fences_are_ignored():
    doc = MarkdownDoc(SAMPLE)
    assert len(doc.tables()) == 1


def test_section_end_stops_at_next_same_level_heading():
    doc = MarkdownDoc(SAMPLE)
    section = doc.find_section(number="1")
    assert doc.lines[section.end].startswith("## 2.")


def test_split_row_handles_escaped_pipes():
    assert split_row(r"| a \| b | c |") == [r"a \| b", "c"]


def test_upsert_rows_adds_updates_and_renumbers():
    doc = MarkdownDoc(SAMPLE)
    table = doc.tables()[0]
    added, updated = upsert_rows(
        table,
        [["1", "甲", "新备注"], ["9", "丙", "新增行"]],
        key_column=1,
        ordinal_column=0,
    )
    assert (added, updated) == (1, 1)
    assert [row[0] for row in table.rows] == ["1", "2", "3"]
    assert table.rows[0][2] == "新备注"
    assert table.rows[2][1] == "丙"


def test_upsert_rows_keeps_handwritten_cells_when_incoming_is_empty():
    doc = MarkdownDoc(SAMPLE)
    table = doc.tables()[0]
    upsert_rows(table, [["1", "甲", ""]], key_column=1, ordinal_column=0)
    assert table.rows[0][2] == "手写备注"


def test_update_table_only_rewrites_when_changed():
    doc = MarkdownDoc(SAMPLE)
    table = doc.tables()[0]
    assert doc.update_table(table) is False
    assert doc.dirty is False


def test_managed_block_upsert_is_idempotent():
    doc = MarkdownDoc(SAMPLE)
    assert doc.upsert_block("causes", ["## 根因1：甲"]) is True
    once = doc.text
    doc2 = MarkdownDoc(once)
    assert doc2.upsert_block("causes", ["## 根因1：甲"]) is False
    assert doc2.text == once


def test_managed_block_refresh_replaces_only_block_content():
    doc = MarkdownDoc(SAMPLE)
    doc.upsert_block("causes", ["old"])
    doc2 = MarkdownDoc(doc.text)
    doc2.upsert_block("causes", ["new"])
    assert "new" in doc2.text and "old" not in doc2.text
    assert doc2.text.startswith("# Title")
    assert "手写备注" in doc2.text
