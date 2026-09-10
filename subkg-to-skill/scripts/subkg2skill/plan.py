"""Plan a skill before writing it.

``list`` counts what the graph holds; this counts what the document would
actually contain — after case material is dropped, causes with no criterion and
no fix are removed, prechecks are merged by command and steps are numbered.
That is the number worth deciding on, and it is also where scenarios and steps
that could still be folded together show up.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set, Tuple

from subkg2skill.playbook import fault_key
from subkg2skill.template import NOT_FOUND, DocScenario, SkillDoc

COMMAND_RE = re.compile(r"`([^`]+)`")
REUSE_NUMBER_RE = re.compile(r"复用前置检查步骤 (\d+)")


@dataclass
class ScenarioPlan:
    """What one scenario would look like once generated."""

    label: str
    name: str
    steps: int
    causes: int
    fix_commands: int
    prechecks: List[int] = field(default_factory=list)
    cause_names: Set[str] = field(default_factory=set)
    step_names: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"场景{self.label}：{self.name}"


@dataclass
class Hint:
    """A merge worth considering, with the evidence for it."""

    kind: str  # scenario | step
    message: str


@dataclass
class Plan:
    scenarios: List[ScenarioPlan]
    prechecks: int
    omitted: List[Tuple[str, str]]
    hints: List[Hint] = field(default_factory=list)

    @property
    def steps(self) -> int:
        return sum(scenario.steps for scenario in self.scenarios)

    @property
    def causes(self) -> int:
        return sum(scenario.causes for scenario in self.scenarios)

    @property
    def fix_commands(self) -> int:
        return sum(scenario.fix_commands for scenario in self.scenarios)


def _fix_commands(scenario: DocScenario) -> Set[str]:
    commands: Set[str] = set()
    for cause in scenario.root_causes:
        if "无直接修复CLI" in cause.fix:
            continue
        commands.update(COMMAND_RE.findall(cause.fix))
    return commands


def plan_document(doc: SkillDoc) -> Plan:
    """Measure a built document and point at what could still be folded."""
    scenarios: List[ScenarioPlan] = []
    for scenario in doc.scenarios:
        cited = sorted(
            {int(number) for step in scenario.steps for number in REUSE_NUMBER_RE.findall(step.reuse_note)}
        )
        scenarios.append(
            ScenarioPlan(
                label=scenario.label,
                name=scenario.name,
                steps=len(scenario.steps),
                causes=len([c for c in scenario.root_causes if c.name != NOT_FOUND]),
                fix_commands=len(_fix_commands(scenario)),
                prechecks=cited,
                cause_names={c.name for c in scenario.root_causes if c.name != NOT_FOUND},
                step_names=[step.name for step in scenario.steps],
            )
        )

    plan = Plan(scenarios=scenarios, prechecks=len(doc.prechecks), omitted=list(doc.omitted))
    plan.hints = _hints(doc, scenarios)
    return plan


def _hints(doc: SkillDoc, scenarios: Sequence[ScenarioPlan]) -> List[Hint]:
    hints: List[Hint] = []

    # 场景之间：一个场景的根因被另一个完全包含 → 多半该并
    for left in scenarios:
        for right in scenarios:
            if left is right or not left.cause_names or not right.cause_names:
                continue
            shared = {fault_key(name) for name in left.cause_names} & {
                fault_key(name) for name in right.cause_names
            }
            if not shared:
                continue
            if len(shared) == len({fault_key(name) for name in left.cause_names}):
                hints.append(
                    Hint(
                        "scenario",
                        f"{left.title} 的 {len(shared)} 个根因全部也出现在 {right.title}，"
                        f"可考虑并入（合并后少一个场景）",
                    )
                )
            elif len(shared) >= 2 and left.label < right.label:
                hints.append(
                    Hint(
                        "scenario",
                        f"{left.title} 与 {right.title} 共享 {len(shared)} 个根因，"
                        "确认是不是同一个故障的两种说法",
                    )
                )

    # 场景之内：多个步骤读同一条前置检查回显 → 可以合成一步多判据
    for scenario, built in zip(scenarios, doc.scenarios):
        by_precheck: Dict[int, List[str]] = {}
        for step in built.steps:
            for number in REUSE_NUMBER_RE.findall(step.reuse_note):
                by_precheck.setdefault(int(number), []).append(f"步骤{step.index}（{step.name}）")
        for number, steps in by_precheck.items():
            if len(steps) > 1:
                hints.append(
                    Hint(
                        "step",
                        f"{scenario.title} 的 " + "、".join(steps)
                        + f" 都只读前置检查步骤 {number} 的回显，判据不同则保持分开，"
                        "判据其实相同就该合成一步",
                    )
                )

    # 跨场景重名步骤 = 同一个根因排两遍
    seen: Dict[str, List[str]] = {}
    for scenario in scenarios:
        for name in scenario.step_names:
            seen.setdefault(fault_key(name), []).append(f"{scenario.title} 的「{name}」")
    for _key, places in seen.items():
        if len(places) > 1:
            hints.append(Hint("step", "同一个根因在多处各排一遍：" + "；".join(places)))
    return hints


def render_plan(plan: Plan, *, limit: int = 20) -> List[str]:
    """Human-readable planning report."""
    lines = [
        "生成规划（未写盘，数字是实际会写进文档的量）：",
        "",
        "| 场景 | 步骤 | 根因 | 修复命令 | 复用的公共前置检查 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for scenario in plan.scenarios:
        cited = "、".join(f"步骤 {number}" for number in scenario.prechecks) or "—"
        lines.append(
            f"| {scenario.title} | {scenario.steps} | {scenario.causes} | "
            f"{scenario.fix_commands} | {cited} |"
        )
    lines.append(
        f"| **合计** | **{plan.steps}** | **{plan.causes}** | **{plan.fix_commands}** | "
        f"公共前置检查 {plan.prechecks} 条 |"
    )
    lines.append("")

    if plan.hints:
        lines.append("还能再合并的地方（只是提示，合不合由你判断）：")
        lines.append("")
        for hint in plan.hints[:limit]:
            lines.append(f"  - {hint.message}")
        if len(plan.hints) > limit:
            lines.append(f"  - …另有 {len(plan.hints) - limit} 条")
        lines.append("")
    else:
        lines += ["没有发现可以进一步合并的场景或步骤。", ""]

    if plan.omitted:
        lines.append(f"未进入正文的条目 {len(plan.omitted)} 条：")
        for name, reason in plan.omitted[:limit]:
            lines.append(f"  - {name}：{reason}")
        if len(plan.omitted) > limit:
            lines.append(f"  - …另有 {len(plan.omitted) - limit} 条")
        lines.append("")
    return lines
