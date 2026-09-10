"""Turn a node's ``attrs`` into the markdown fragments the playbooks are built from.

Two rules run through everything here: never invent a field the export did not
carry, and never let a knowledge template read like a verified instruction.
Missing values are reported as missing.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Sequence

from subkg2skill import schema
from subkg2skill.graph import Node, _string_list, _text

EVIDENCE_DEFAULT_LIMIT = 3


def scope_line(scope: Dict[str, Any]) -> str:
    """One-line applicability: vendor / family / version / component / scenario."""
    if not scope:
        return "适用范围未标注"
    parts: List[str] = []
    for key, label, fallback in (
        ("vendor", "厂商", "厂商未标注"),
        ("product_family", "产品", "产品未限定"),
        ("software_version", "版本", "版本未限定"),
        ("component_type", "组件", "组件未限定"),
        ("scenario", "场景", "场景未限定"),
    ):
        value = _text(scope.get(key))
        parts.append(f"{label}={value}" if value else fallback)
    basis = _text(scope.get("scope_basis"))
    if basis:
        parts.append("依据=" + schema.SCOPE_BASIS_LABELS.get(basis, basis))
    return "；".join(parts)


def context_line(contexts: Sequence[Dict[str, Any]]) -> str:
    """Diagnostic units (section + title) a node belongs to."""
    rendered = []
    for ctx in contexts:
        section = _text(ctx.get("section"))
        title = _text(ctx.get("title"))
        if section and title and title.startswith(section):
            rendered.append(title)
        elif section or title:
            rendered.append(" ".join(part for part in (section, title) if part))
    return "；".join(dict.fromkeys(rendered))


def evidence_line(item: Dict[str, Any]) -> str:
    """Format one provenance / evidence entry down to a citable locator."""
    head: List[str] = []
    document = _text(item.get("document"))
    if document:
        version = _text(item.get("document_version"))
        head.append(f"《{document}》" + (f" v{version}" if version else ""))
    page = item.get("page")
    printed = item.get("printed_page")
    if isinstance(page, int):
        head.append(f"物理页 {page}" + (f"（印刷页 {printed}）" if isinstance(printed, int) else ""))
    section = _text(item.get("section"))
    if section:
        head.append(f"§{section}")
    sheet, cell = _text(item.get("sheet")), _text(item.get("cell"))
    if sheet or cell:
        head.append(" ".join(part for part in (sheet, cell) if part))
    case_uuid = _text(item.get("case_uuid"))
    if case_uuid:
        head.append(f"案例 {case_uuid[:12]}")
    block_id = _text(item.get("block_id"))
    if block_id:
        head.append(block_id)
    source_kind = _text(item.get("source_kind"))
    if source_kind:
        head.append(source_kind)
    quote = _text(item.get("quote")).replace("\n", " ⏎ ")
    if len(quote) > 200:
        quote = quote[:200] + "…"
    prefix = "、".join(part for part in head if part) or "来源未标注"
    return f"{prefix}：“{quote}”" if quote else prefix


def evidence_lines(items: Sequence[Dict[str, Any]], limit: int = EVIDENCE_DEFAULT_LIMIT) -> List[str]:
    lines = [evidence_line(item) for item in items[:limit] if isinstance(item, dict)]
    if len(items) > limit:
        lines.append(f"（另有 {len(items) - limit} 条来源未展开，见 data/subgraph.json）")
    return lines


def flag_lines(flags: Iterable[str]) -> List[str]:
    return [f"`{flag}` — {schema.quality_flag_note(flag)}" for flag in flags]


def trust_line(node: Node) -> str:
    """State the review level plainly so nothing reads as confirmed knowledge."""
    parts = [f"status={node.status or '未标注'}", f"review={node.review_status or '未标注'}"]
    parts.append("人工复核=" + ("是" if node.human_reviewed else "否"))
    if node.example_specific:
        parts.append("**案例特定（example_specific=true，勿直接推广到其他设备）**")
    return "；".join(parts)


def _bullet_list(values: Sequence[str], prefix: str = "  - ") -> List[str]:
    return [f"{prefix}{value}" for value in values if value]


def observation_expression(node: Node) -> str:
    """Best available reading of what the observation asserts."""
    attrs = node.attrs
    normalized = _text(attrs.get("normalized_expression"))
    if normalized:
        return normalized
    field = _text(attrs.get("field"))
    operator = _text(attrs.get("operator"))
    parts: List[str] = []
    object_type = _text(attrs.get("object_type"))
    subject = ".".join(part for part in (object_type, field) if part)
    if subject:
        parts.append(subject)
    if operator:
        parts.append(schema.operator_label(operator))
    if "value" in attrs and operator not in ("exists", "not_exists"):
        from subkg2skill.condition import format_value

        parts.append(format_value(attrs.get("value")))
    unit = _text(attrs.get("unit"))
    if unit:
        parts.append(f"({unit})")
    rendered = " ".join(parts).strip()
    return rendered or node.name


def observation_details(node: Node) -> List[str]:
    """Sampling constraints and the free-text predicates kept alongside."""
    attrs = node.attrs
    lines: List[str] = []
    sampling = []
    if _text(attrs.get("aggregation")):
        sampling.append(f"聚合={_text(attrs.get('aggregation'))}")
    if attrs.get("window_seconds") is not None:
        sampling.append(f"时间窗口={attrs['window_seconds']}s")
    if attrs.get("sample_count") is not None:
        sampling.append(f"样本数={attrs['sample_count']}")
    if sampling:
        lines.append("采样约束：" + "；".join(sampling))
    for key, label in (
        ("predicate", "文本谓词"),
        ("knowledge_predicate", "来源知识谓词"),
    ):
        predicate = attrs.get(key)
        if isinstance(predicate, dict) and _text(predicate.get("expression")):
            lines.append(f"{label}（需解释）：{_text(predicate.get('expression'))}")
    if _text(attrs.get("source_operator")):
        lines.append(f"原始操作符：{_text(attrs.get('source_operator'))}（未纳入规范比较集合）")
    if _text(attrs.get("knowledge_mode")) == "possible_check_result":
        lines.append("该观测是“可能出现的检查结果”模板，不代表现场已经出现。")
    return lines


def _command_block(commands: Sequence[str]) -> List[str]:
    commands = [cmd for cmd in commands if _text(cmd)]
    if not commands:
        return []
    lines = ["```text"]
    lines += [_text(cmd) for cmd in commands]
    lines.append("```")
    return lines


def action_block(node: Node, *, kind_key: str, kind_label: str) -> List[str]:
    """Shared renderer for check / repair / escalation action nodes."""
    attrs = node.attrs
    lines: List[str] = []
    kind = _text(attrs.get(kind_key))
    target = _text(attrs.get("target_object"))
    intent = _text(attrs.get("intent"))
    meta = []
    if kind:
        meta.append(f"{kind_label}={kind}")
    if target:
        meta.append(f"作用对象={target}")
    if meta:
        lines.append("- " + "；".join(meta))
    if intent:
        lines.append(f"- 判断目的：{intent}")
    if node.description and node.description != node.name:
        lines.append(f"- 说明：{node.description}")

    preconditions = _string_list(attrs.get("preconditions"))
    if preconditions:
        lines.append("- 前置条件：")
        lines += _bullet_list(preconditions)
    elif "preconditions" in attrs:
        lines.append("- 前置条件：来源未给出（空数组不证明无需前置条件）")

    procedure = _string_list(attrs.get("procedure"))
    if procedure:
        lines.append("- 操作步骤：")
        lines += [f"  {index}. {step}" for index, step in enumerate(procedure, start=1)]

    commands = _string_list(attrs.get("command_templates"))
    if commands:
        lines.append("- 命令模板（参数需绑定现场上下文后才可执行）：")
        lines += [f"  {line}" for line in _command_block(commands)]
    parameters = _string_list(attrs.get("parameters"))
    if parameters:
        lines.append("- 待绑定参数：" + "、".join(f"`{p}`" for p in parameters))

    execution_context = attrs.get("execution_context")
    if isinstance(execution_context, dict):
        if execution_context.get("requires_configuration_change"):
            lines.append("- 执行上下文：需要配置变更")
        else:
            lines.append(f"- 执行上下文：{_text(execution_context)}")
    elif _text(execution_context):
        lines.append(f"- 执行上下文：{_text(execution_context)}")

    collection_spec = attrs.get("collection_spec")
    if isinstance(collection_spec, dict) and collection_spec:
        spec = "；".join(f"{k}={v}" for k, v in collection_spec.items())
        lines.append(f"- 采样要求：{spec}")

    for key, label in (
        ("execution_effect", "执行影响"),
        ("expected_effect", "预期效果"),
        ("service_impact", "业务影响"),
        ("rollback", "回退方法"),
    ):
        value = _text(attrs.get(key))
        if value:
            lines.append(f"- {label}：{value}")
        elif key in attrs:
            hint = {
                "service_impact": "来源未给出（空值不表示无影响）",
                "rollback": "来源未给出（空值不表示无需回退）",
                "expected_effect": "来源未给出（不要自行补充）",
                "execution_effect": "来源未给出",
            }[key]
            lines.append(f"- {label}：{hint}")

    policy = _text(attrs.get("execution_policy"))
    if policy == "knowledge_template_requires_context_binding":
        lines.append("- 执行策略：模板需绑定现场上下文后使用")
    elif policy == "source_text_requires_interpretation":
        lines.append("- 执行策略：原文操作需解释后使用")
    elif policy:
        lines.append(f"- 执行策略：{policy}")

    operational = [
        item for item in attrs.get("operational_context") or [] if isinstance(item, dict)
    ]
    if operational:
        lines.append("- 操作补充来源：")
        lines += _bullet_list(evidence_lines(operational, limit=2))
    return lines


def check_block(node: Node) -> List[str]:
    return action_block(node, kind_key="check_kind", kind_label="检查方式")


def repair_block(node: Node) -> List[str]:
    return action_block(node, kind_key="repair_kind", kind_label="修复方式")


def escalation_block(node: Node) -> List[str]:
    attrs = node.attrs
    lines: List[str] = []
    destination = _text(attrs.get("destination_role"))
    if destination:
        lines.append(f"- 转交对象/去向：{destination}")
    requirements = _string_list(attrs.get("collection_requirements"))
    if requirements:
        lines.append("- 转交前需收集：")
        lines += _bullet_list(requirements)
    instructions = _string_list(attrs.get("instructions"))
    if instructions:
        lines.append("- 操作说明：")
        lines += _bullet_list(instructions)
    template = _text(attrs.get("handoff_template"))
    if template:
        lines.append(f"- 转交模板：{template}")
    lines += action_block(node, kind_key="repair_kind", kind_label="方式")
    return lines


def cause_block(node: Node) -> List[str]:
    attrs = node.attrs
    lines: List[str] = []
    meta = []
    kind = _text(attrs.get("cause_kind"))
    if kind:
        meta.append(f"分类={kind}")
    source_kind = _text(attrs.get("source_cause_kind"))
    if source_kind:
        meta.append(f"原始分类={source_kind}")
    affected = _text(attrs.get("affected_object"))
    if affected:
        meta.append(f"受影响对象={affected}")
    granularity = _text(attrs.get("granularity"))
    if granularity:
        meta.append(f"粒度={granularity}")
    if meta:
        lines.append("- " + "；".join(meta))
    mechanism = _text(attrs.get("fault_mechanism"))
    if mechanism:
        lines.append(f"- 故障机制：{mechanism}")
    elif node.description:
        lines.append(f"- 说明：{node.description}")
    return lines


def symptom_block(node: Node) -> List[str]:
    attrs = node.attrs
    lines: List[str] = []
    for key, label in (
        ("object_type", "对象类别"),
        ("abnormal_behavior", "异常行为"),
        ("expected_behavior", "期望行为"),
        ("trigger_context", "触发场景"),
    ):
        value = _text(attrs.get(key))
        if value:
            lines.append(f"- {label}：{value}")
    slots = _string_list(attrs.get("required_slots"))
    if slots:
        lines.append("- 需向用户/现场补齐的信息槽位：" + "、".join(f"`{slot}`" for slot in slots))
    return lines


NODE_BLOCKS = {
    "symptom": symptom_block,
    "cause": cause_block,
    "check": check_block,
    "observation": lambda node: (
        [f"- 判断表达式：{observation_expression(node)}"]
        + [f"- {line}" for line in observation_details(node)]
        + ([f"- 说明：{node.description}"] if node.description else [])
    ),
    "repair": repair_block,
    "escalation": escalation_block,
}


def node_block(node: Node) -> List[str]:
    """Type-appropriate attribute rendering for any node."""
    builder = NODE_BLOCKS.get(node.node_type)
    return builder(node) if builder else ([f"- 说明：{node.description}"] if node.description else [])


def node_headline(node: Node) -> str:
    """``名称（角色｜node_id）`` used in tables and cross references."""
    return f"{node.name}（{schema.NODE_LABELS.get(node.node_type, node.node_type)}｜`{node.node_id}`）"
