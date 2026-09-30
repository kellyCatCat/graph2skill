"""Ontology of the IP-RAN fault-diagnosis knowledge graph.

Everything here mirrors the published field dictionary: six node roles, eleven
edge types with the endpoint combinations the schema allows, plus the label
tables used when rendering human-readable documents.  Nothing in this module
reads data; it is the vocabulary the rest of the package validates against.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, Tuple

NODE_TYPES: Tuple[str, ...] = (
    "symptom",
    "cause",
    "check",
    "observation",
    "repair",
    "escalation",
)

NODE_LABELS: Dict[str, str] = {
    "symptom": "故障症状",
    "cause": "故障原因",
    "check": "检查动作",
    "observation": "观测结果",
    "repair": "修复动作",
    "escalation": "转交/升级",
}

# edge_type -> (allowed (source_type, target_type) pairs, label, one-line meaning)
EDGE_RULES: Dict[str, Tuple[FrozenSet[Tuple[str, str]], str, str]] = {
    "has_cause": (
        frozenset({("symptom", "cause")}),
        "可能原因",
        "症状可能由该原因引起；方向是症状指向原因，不是原因传播方向。",
    ),
    "diagnosed_by": (
        frozenset({("cause", "check"), ("symptom", "check")}),
        "通过检查定位",
        "通过该检查动作定位源症状或源原因。",
    ),
    "observes": (
        frozenset({("check", "observation")}),
        "可能观测到",
        "该检查可产生或识别这个观测结果。",
    ),
    "supports": (
        frozenset({("observation", "cause")}),
        "支持",
        "观测为原因提供支持证据，尚不等于确认根因。",
    ),
    "confirms": (
        frozenset({("observation", "cause")}),
        "确认",
        "在给定条件与范围内，观测被表述为确认该原因；强于 supports。",
    ),
    "excludes": (
        frozenset({("observation", "cause")}),
        "排除",
        "在给定条件与范围内，观测可排除该原因，不能外推为排除全部原因。",
    ),
    "repaired_by": (
        frozenset({("cause", "repair")}),
        "修复动作",
        "该修复动作用于处置源原因。",
    ),
    "refines": (
        frozenset({("cause", "cause"), ("cause", "symptom")}),
        "细化为",
        "把源原因细化为更具体的原因或症状描述，不是因果传播。",
    ),
    "refers_to": (
        frozenset(
            {
                ("cause", "escalation"),
                ("cause", "symptom"),
                ("symptom", "escalation"),
                ("symptom", "symptom"),
            }
        ),
        "转向",
        "转向其他故障入口、章节或支持流程；不是具体修复承诺。",
    ),
    "next_step": (
        frozenset(
            {
                ("check", "check"),
                ("check", "escalation"),
                ("check", "repair"),
                ("observation", "check"),
                ("observation", "escalation"),
                ("observation", "repair"),
                ("repair", "check"),
                ("repair", "escalation"),
                ("repair", "repair"),
                ("symptom", "check"),
                ("symptom", "escalation"),
                ("symptom", "repair"),
            }
        ),
        "下一步",
        "诊断或处置流程的下一步，可能受 condition 限制；不代表因果推断。",
    ),
    "leads_to": (
        frozenset({("cause", "cause"), ("cause", "symptom")}),
        "导致",
        "原因向后果传播：源原因导致目标原因或症状。",
    ),
}

EDGE_TYPES: Tuple[str, ...] = tuple(EDGE_RULES)

#: Edges whose target is reached by moving *forward* through a diagnosis.
FORWARD_EDGES: Tuple[str, ...] = (
    "has_cause",
    "diagnosed_by",
    "observes",
    "repaired_by",
    "refines",
    "refers_to",
    "next_step",
    "leads_to",
)

#: Forward edges that stay inside one fault — ``refers_to`` and ``leads_to``
#: both hand over to a different fault entry, so a selection that follows them
#: pulls in unrelated scenarios.
WITHIN_FAULT_EDGES: Tuple[str, ...] = (
    "has_cause",
    "diagnosed_by",
    "observes",
    "repaired_by",
    "refines",
    "next_step",
)

#: Observation -> cause verdicts, strongest first.
VERDICT_EDGES: Tuple[str, ...] = ("confirms", "supports", "excludes")

VERDICT_LABELS: Dict[str, str] = {
    "confirms": "确认",
    "supports": "支持",
    "excludes": "排除",
}

OPERATOR_LABELS: Dict[str, str] = {
    "eq": "等于",
    "ne": "不等于",
    "gt": "大于",
    "ge": "大于等于",
    "lt": "小于",
    "le": "小于等于",
    "in": "属于",
    "not_in": "不属于",
    "exists": "存在",
    "not_exists": "不存在",
    "contains": "包含",
    "trend": "趋势",
    # 历史/来源写法，保留原样并标注
    "not_contains": "不包含（来源写法）",
    "not_eq": "不等于（来源写法）",
    "and": "复合语义残留（来源写法）",
    "==": "等于（来源写法）",
    "!=": "不等于（来源写法）",
    ">": "大于（来源写法）",
    "<": "小于（来源写法）",
    ">=": "大于等于（来源写法）",
    "<=": "小于等于（来源写法）",
    "is_null": "为空（来源写法）",
}

def operator_label(operator: str) -> str:
    """Return a Chinese label for *operator*, falling back to the raw token."""
    if not operator:
        return ""
    return OPERATOR_LABELS.get(operator, f"{operator}（未知操作符，按原文解释）")


def edge_label(edge_type: str) -> str:
    rule = EDGE_RULES.get(edge_type)
    return rule[1] if rule else edge_type


def endpoints_allowed(edge_type: str, source_type: str, target_type: str) -> bool:
    rule = EDGE_RULES.get(edge_type)
    if rule is None:
        return False
    return (source_type, target_type) in rule[0]
