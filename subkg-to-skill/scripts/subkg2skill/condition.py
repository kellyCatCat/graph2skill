"""Render edge conditions as text a human (or an agent) can act on.

Conditions are recursive — ``atomic`` / ``text`` / ``and`` / ``or`` / ``not`` —
and ``original_condition`` additionally keeps source-side spellings (``var``,
``comparison``, ``other``, ``negate``).  None of it has been evaluated against a
live device, so every rendering says so rather than reading like a verdict.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from subkg2skill import schema

MAX_DEPTH = 12


def format_value(value: Any) -> str:
    if value is None:
        return "(未给出)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip() or "(空字符串)"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(format_value(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False)


def _atomic(cond: Dict[str, Any]) -> str:
    expression = cond.get("expression")
    operator = str(cond.get("operator") or cond.get("comparison") or "").strip()
    field = str(cond.get("field") or cond.get("var") or "").strip()
    if not field and not operator and isinstance(expression, str) and expression.strip():
        return expression.strip()
    obj = str(cond.get("object") or "").strip()
    subject = ".".join(part for part in (obj, field) if part) or "(未给出对象)"
    parts: List[str] = [subject]
    if operator:
        parts.append(schema.operator_label(operator))
    if "value" in cond and operator not in ("exists", "not_exists"):
        parts.append(format_value(cond.get("value")))
    unit = str(cond.get("unit") or "").strip()
    if unit:
        parts.append(f"({unit})")
    rendered = " ".join(part for part in parts if part)
    extras: List[str] = []
    if cond.get("aggregation"):
        extras.append(f"聚合={cond['aggregation']}")
    if cond.get("window_seconds") is not None:
        extras.append(f"窗口={cond['window_seconds']}s")
    if cond.get("other"):
        extras.append(f"来源补充：{cond['other']}")
    if isinstance(expression, str) and expression.strip() and (field or operator):
        extras.append(f"原文：{expression.strip()}")
    if extras:
        rendered += "（" + "；".join(extras) + "）"
    if cond.get("negate"):
        rendered = f"非（{rendered}）"
    return rendered


def format_condition(cond: Any, *, depth: int = 0) -> str:
    """Return a one-line rendering of a (possibly nested) condition object."""
    if cond is None:
        return ""
    if isinstance(cond, str):
        return cond.strip()
    if not isinstance(cond, dict):
        return json.dumps(cond, ensure_ascii=False)
    if depth >= MAX_DEPTH:
        return "（条件嵌套过深，见原始 JSON）"
    kind = str(cond.get("type") or "").strip()
    if kind == "text":
        text = str(cond.get("expression") or "").strip()
        if not text:
            return ""
        # Nested inside and/or/not the reader needs to see which operand is
        # free text; on its own the condition_status label already says so.
        return f"文本「{text}」" if depth else text
    if kind in ("and", "or"):
        joiner = " 且 " if kind == "and" else " 或 "
        parts = [
            format_condition(child, depth=depth + 1) for child in cond.get("conditions") or []
        ]
        parts = [part for part in parts if part]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        return "（" + joiner.join(parts) + "）"
    if kind == "not":
        inner = format_condition(cond.get("condition"), depth=depth + 1)
        return f"非（{inner}）" if inner else ""
    if kind == "atomic" or not kind:
        return _atomic(cond)
    return f"{kind}：" + json.dumps(cond, ensure_ascii=False)


def describe_edge_condition(edge) -> str:
    """Condition line for *edge*, including its unevaluated-status caveat."""
    status = edge.condition_status
    text = format_condition(edge.condition)
    if not text and status in ("", "unconditional"):
        return ""
    label = schema.CONDITION_STATUS_LABELS.get(status, status)
    if not text:
        original = format_condition(edge.original_condition)
        if original:
            return f"条件（{label}，未求值）：{original}【原始条件，未规范化】"
        return f"条件状态：{label}" if label else ""
    line = f"条件（{label}，未求值）：{text}"
    original = format_condition(edge.original_condition)
    if original and original != text:
        line += f"｜原始写法：{original}"
    return line


def condition_is_binding(edge) -> bool:
    """True when the relation carries a branch condition that must be checked."""
    return bool(edge.condition) or edge.condition_status not in ("", "unconditional")
