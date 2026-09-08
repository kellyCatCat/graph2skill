"""Vocabulary used to turn raw graph types into human readable prose.

The upstream schema does not constrain ``type``, so everything here is a *hint*:
unknown types fall back to their raw name and are still rendered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

ZH = "zh"
EN = "en"


@dataclass(frozen=True)
class TypeInfo:
    key: str
    zh: str
    en: str
    role: str  # entry | cause | check | evidence | outcome | context
    order: int

    def label(self, lang: str = ZH) -> str:
        return self.zh if lang == ZH else self.en


_NODE_TYPES: List[TypeInfo] = [
    TypeInfo("SCENARIO", "场景", "Scenario", "entry", 10),
    TypeInfo("THEME", "主题", "Theme", "entry", 20),
    TypeInfo("TOPIC", "主题", "Topic", "entry", 21),
    TypeInfo("FAULT", "故障", "Fault", "entry", 30),
    TypeInfo("SYMPTOM", "现象", "Symptom", "entry", 31),
    TypeInfo("PHENOMENON", "现象", "Phenomenon", "entry", 32),
    TypeInfo("CONDITION", "前置条件", "Condition", "context", 40),
    TypeInfo("CHECK", "检查项", "Check", "check", 44),
    TypeInfo("DIAGNOSIS", "诊断", "Diagnosis", "check", 45),
    TypeInfo("COMMAND", "命令", "Command", "check", 46),
    TypeInfo("STEP", "步骤", "Step", "check", 47),
    TypeInfo("CAUSE", "可能原因", "Cause", "cause", 50),
    TypeInfo("ROOT_CAUSE", "根因", "Root cause", "cause", 51),
    TypeInfo("RELATION", "关联知识", "Related knowledge", "context", 60),
    TypeInfo("ACTION", "处理动作", "Action", "outcome", 80),
    TypeInfo("SOLUTION", "解决方案", "Solution", "outcome", 81),
    TypeInfo("FIX", "修复措施", "Fix", "outcome", 82),
    TypeInfo("CONCLUSION", "结论", "Conclusion", "outcome", 83),
    TypeInfo("CASE", "案例", "Case", "evidence", 90),
    TypeInfo("EVIDENCE", "证据", "Evidence", "evidence", 91),
    TypeInfo("NOTE", "备注", "Note", "context", 100),
    TypeInfo("PRINCIPLE", "原理", "Principle", "context", 101),
    TypeInfo("RISK", "风险", "Risk", "context", 102),
    TypeInfo("DEVICE", "设备", "Device", "context", 103),
    TypeInfo("CONFIG", "配置", "Configuration", "context", 104),
]

_RELATION_TYPES: Dict[str, Dict[str, str]] = {
    "HAS_RELATION": {ZH: "关联", EN: "relates to"},
    "RELATED_TO": {ZH: "关联", EN: "relates to"},
    "HAS_THEME": {ZH: "所属主题", EN: "has theme"},
    "HAS_SCENARIO": {ZH: "包含场景", EN: "has scenario"},
    "HAS_CAUSE": {ZH: "可能原因", EN: "has cause"},
    "CAUSED_BY": {ZH: "由…引起", EN: "caused by"},
    "HAS_CHECK": {ZH: "检查项", EN: "has check"},
    "HAS_STEP": {ZH: "步骤", EN: "has step"},
    "NEXT": {ZH: "下一步", EN: "next"},
    "HAS_ACTION": {ZH: "处理动作", EN: "has action"},
    "HAS_SOLUTION": {ZH: "解决方案", EN: "has solution"},
    "HAS_CASE": {ZH: "相关案例", EN: "has case"},
    "HAS_EVIDENCE": {ZH: "证据", EN: "has evidence"},
    "BELONGS_TO": {ZH: "属于", EN: "belongs to"},
    "DERIVED_FROM": {ZH: "来源于", EN: "derived from"},
    "REFERENCES": {ZH: "参考", EN: "references"},
}

_BY_KEY: Dict[str, TypeInfo] = {info.key: info for info in _NODE_TYPES}

ENTRY_ROLE_TYPES = tuple(info.key for info in _NODE_TYPES if info.role == "entry")


def node_type_info(node_type: str) -> Optional[TypeInfo]:
    return _BY_KEY.get((node_type or "").strip().upper())


def node_type_label(node_type: str, lang: str = ZH) -> str:
    info = node_type_info(node_type)
    if info is None:
        return (node_type or "UNKNOWN").strip().upper()
    return info.label(lang)


def node_type_order(node_type: str) -> int:
    info = node_type_info(node_type)
    return info.order if info else 500


def node_role(node_type: str) -> str:
    info = node_type_info(node_type)
    return info.role if info else "context"


def relation_label(relation_type: str, lang: str = ZH) -> str:
    entry = _RELATION_TYPES.get((relation_type or "").strip().upper())
    if entry is None:
        return (relation_type or "RELATED_TO").strip().upper()
    return entry[lang if lang in entry else ZH]


def is_known_node_type(node_type: str) -> bool:
    return (node_type or "").strip().upper() in _BY_KEY
