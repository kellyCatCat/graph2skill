"""Write a :class:`~subkg2skill.doc.SkillDoc` out as markdown.

One fault is one ``SKILL.md``.  Several faults are an entry ``SKILL.md``
(inputs, shared collection, routing) plus ``reference/<slug>.md`` per scenario.
Pure formatting: every decision about content was made in
:mod:`subkg2skill.compose`.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

from subkg2skill.doc import DocScenario, Precheck, RootCause, RoutingRow, SkillDoc, Step


#: Where a multi-scenario skill keeps one file per scenario.
REFERENCE_DIR = "reference"


#: What the index says the reader should do once the routing table picks a scenario.
ENTER_SCENARIO = (
    "进入对应场景后，先读取 `reference/` 目录下该场景的参考文件，"
    "再按其中的步骤顺序执行（默认从步骤 1 开始，按跳转信息顺序执行）："
)


def scenario_path(slug: str) -> str:
    return f"{REFERENCE_DIR}/{slug}.md"


def scenario_description(scenario: DocScenario, skill: str) -> str:
    """Frontmatter description of one scenario file — what it is and how it is reached."""
    return (
        f"{scenario.title} —— 排查步骤与根因对照表；"
        f"由 skill {skill} 的前置检查分流进入。"
    )


def render_package(doc: SkillDoc, *, name: str, description: str) -> Dict[str, str]:
    """Every file the skill delivers, keyed by its path inside the skill directory."""
    if not doc.multi:
        return {"SKILL.md": render_doc(doc, name=name, description=description)}
    files = {"SKILL.md": render_index(doc, name=name, description=description)}
    for scenario in doc.scenarios:
        files[scenario_path(scenario.slug)] = render_scenario(
            scenario,
            name=scenario.slug,
            description=scenario_description(scenario, name),
        )
    return files


def render_doc(doc: SkillDoc, *, name: str, description: str) -> str:
    """One fault, one file — the four sections, nothing else.

    No pointer to the graph: the subgraph is build-time material and is not
    delivered with the skill, so a reference to it would dangle.
    """
    lines, routing = _render_head(doc, name=name, description=description)
    lines += ["# 排查步骤", ""]
    if not doc.steps:
        lines += ["本子图未给出该症状的候选原因，无法展开排查步骤。", ""]
    else:
        lines.append(
            ("按步骤跳转表进入对应步骤，判据都不命中时" if routing else "默认")
            + "按顺序执行；判据来自前置检查回显的步骤不重复下发命令。"
        )
        lines.append("")
        for step in doc.scenarios[0].steps:
            lines += _render_step(step, heading="##")

    lines += ["# 根因对照表", ""]
    lines += _render_cause_table(doc.scenarios[0].root_causes)
    return "\n".join(lines)


def render_index(doc: SkillDoc, *, name: str, description: str) -> str:
    """The entry file of a multi-scenario skill: inputs, shared collection, routing.

    The steps themselves live one file per scenario.  A reader follows exactly
    one of them, so putting all of them here costs every reader the ones that
    are not theirs — the same asymmetry that decides the shared collection
    phase, one level up.
    """
    lines, _routing = _render_head(doc, name=name, description=description)
    lines += ["# 排查步骤", ""]
    if not doc.steps:
        lines += ["本子图未给出该症状的候选原因，无法展开排查步骤。", ""]
        return "\n".join(lines)
    lines += [ENTER_SCENARIO, ""]
    lines += ["| 场景 | 参考文件 | 内容 |", "| --- | --- | --- |"]
    for scenario in doc.scenarios:
        content = "排查步骤 + 根因对照表" if scenario.steps else "（本场景没有可展开的候选原因）"
        lines.append(f"| {scenario.title} | {scenario_path(scenario.slug)} | {content} |")
    lines.append("")
    return "\n".join(lines)


def render_scenario(scenario: DocScenario, *, name: str, description: str) -> str:
    """One scenario's own file: its collection, its steps, its root causes."""
    lines = ["---", f"name: {name}", f"description: {description}", "---", ""]
    lines += [f"# {scenario.title}", ""]
    if scenario.collection:
        lines += ["**本场景采集**（公共前置之外，只有本场景需要，进入本场景后再执行）：", ""]
        for index, precheck in enumerate(scenario.collection, start=1):
            lines += _render_collection(precheck, index)
    if not scenario.steps:
        lines += ["本场景在子图中没有可展开的候选原因。", ""]
    else:
        lines.append("按顺序执行；判据来自前置检查回显的步骤不重复下发命令。")
        lines.append("")
        for step in scenario.steps:
            lines += _render_step(step, heading="##")
    lines += ["# 根因对照表", ""]
    lines += _render_cause_table(scenario.root_causes)
    return "\n".join(lines)


def _render_head(doc: SkillDoc, *, name: str, description: str) -> Tuple[List[str], List["RoutingRow"]]:
    """Frontmatter, 入参列表 and 前置检查 — identical in both shapes."""
    lines = ["---", f"name: {name}", f"description: {description}", "---", ""]
    lines += ["# 入参列表", ""]
    if doc.params:
        lines += ["| 信息 | 是否必填 | 说明 |", "| --- | --- | --- |"]
        for param in doc.params:
            lines.append(f"| {param.display} | {'是' if param.required else '否'} | {param.note} |")
    else:
        lines.append("本排查流程不需要额外入参（子图未给出必填槽位与命令参数）。")
    lines.append("")

    routing = doc.scenarios[0].routing if doc.scenarios and not doc.multi else []
    lines += ["# 前置检查", ""]
    if doc.prechecks:
        lines.append(
            "前置检查按顺序线性执行，仅用于采集后续排查所需的回显信息，不做跳转"
            + ("；采完后按下面的步骤跳转表进入对应步骤。" if routing else "。")
        )
        lines.append("")
        for index, precheck in enumerate(doc.prechecks, start=1):
            lines += _render_collection(
                precheck,
                index,
                audience=_audience(precheck, doc),
                audience_label="适用场景" if doc.multi else "适用步骤",
            )
    else:
        lines += ["本子图未给出该症状的入口检查动作，直接进入排查步骤。", ""]

    if routing:
        lines += ["## 步骤跳转表", ""]
        lines.append("公共采集做完后，按下表判据直接进入对应步骤；判据都不命中时从步骤 1 起顺序执行。")
        lines.append("")
        lines += ["| 前置检查步骤 | 判据 | 跳转步骤 |", "| --- | --- | --- |"]
        for row in routing:
            lines.append(f"| {row.precheck} | {row.criterion} | → **{row.scenario}** |")
        lines.append("")

    if doc.multi:
        lines += ["## 场景跳转表", ""]
        lines.append("公共采集做完后，按下表判据进入对应场景；判据都不命中时逐个场景排查。")
        lines.append("")
        lines += ["| 前置检查步骤 | 判据 | 跳转场景 |", "| --- | --- | --- |"]
        for scenario in doc.scenarios:
            for row in scenario.routing:
                lines.append(f"| {row.precheck} | {row.criterion} | → **{row.scenario}** |")
        lines.append("")
    return lines, routing


def _render_collection(
    precheck: Precheck,
    index: int,
    *,
    indent: str = "   ",
    audience: str = "",
    audience_label: str = "适用场景",
) -> List[str]:
    """One collection step — the same shape wherever it is rendered."""
    lines = [f"{index}. **{precheck.title}**"]
    if precheck.commands:
        if len(precheck.commands) == 1:
            lines.append(f"{indent}- CLI 命令：`{precheck.commands[0]}`")
        else:
            lines.append(f"{indent}- CLI 命令：")
            lines += [f"{indent}  - `{command}`" for command in precheck.commands]
    else:
        lines.append(f"{indent}- CLI 命令：来源未给出命令模板，按来源步骤说明人工采集")
    if precheck.literals:
        lines.append(
            f"{indent}- 注意：命令含案例字面量（"
            + "、".join(precheck.literals[:4])
            + "），执行前替换为现场对象"
        )
    if precheck.hardcoded:
        lines.append(
            f"{indent}- 注意：命令含示例取值（"
            + "、".join(precheck.hardcoded[:4])
            + "），按现场实际替换"
        )
    lines.append(f"{indent}- 采集内容：{precheck.collect}")
    if audience:
        lines.append(f"{indent}- {audience_label}：{audience}")
    if precheck.variants:
        lines.append(
            f"{indent}- 来源另有写法："
            + "、".join(f"`{variant}`" for variant in precheck.variants[:3])
            + "，按设备版本确认"
        )
    for verdict in precheck.verdicts:
        lines.append(f"{indent}- 根因定位：{verdict}")
    lines.append("")
    return lines


def _audience(precheck: Precheck, doc: "SkillDoc") -> str:
    """Who a shared collection step is actually for.

    In a document covering several faults, a step every reader runs and a step
    that only decides whether to enter one scenario are different instructions;
    saying which is which is what keeps the shared phase honest.
    """
    if not doc.multi:
        steps = doc.scenarios[0].steps if doc.scenarios else []
        readers = [label for label in precheck.owners if label]
        if len(steps) < 2 or not readers or len(readers) == len(steps):
            return "全部步骤" if readers and len(steps) >= 2 else ""
        named = "、".join(readers)
        if precheck.routes:
            return f"{named}（读数用于分流判断，其他步骤可跳过）"
        return f"{named}（其他步骤不读这条回显）"
    titles = {scenario.label: scenario.title for scenario in doc.scenarios}
    readers = [label for label in precheck.owners if label in titles]
    if not readers or len(readers) == len(titles):
        return "全部场景"
    named = "、".join(titles[label] for label in readers)
    if precheck.routes:
        return f"{named}（读数用于分流判断，其他场景可跳过）"
    return f"{named}（其他场景可跳过）"


def _render_step(step: Step, *, heading: str) -> List[str]:
    lines = [f"{heading} 步骤{step.index}：{step.name}", ""]
    lines.append(f"1. **步骤名称**：{step.name}")
    if step.commands:
        if len(step.commands) == 1:
            lines.append(f"2. **CLI 命令**：`{step.commands[0]}`")
        else:
            lines.append("2. **CLI 命令**：")
            lines += [f"   - `{command}`" for command in step.commands]
    else:
        lines.append(f"2. **CLI 命令**：{step.reuse_note or '复用前置检查回显'}")
    if step.literals:
        lines.append(
            "   - 注意：命令含案例字面量（" + "、".join(step.literals[:4]) + "），执行前替换为现场对象"
        )
    lines.append("3. **跳转信息**：")
    for branch in step.branches:
        lines.append(f"   - {branch.criterion}：{branch.outcome}")
    lines.append("4. **根因定位**：")
    if step.causes:
        lines += [f"   - {cause}" for cause in step.causes]
    else:
        lines.append("   - 无")
    lines.append("")
    return lines


def _render_cause_table(causes: Sequence[RootCause]) -> List[str]:
    lines = ["| 根因 | 现象 | 修复CLI和方法 | 复检命令（可选） |", "| --- | --- | --- | --- |"]
    for cause in causes:
        lines.append(f"| {cause.name} | {cause.evidence} | {cause.fix} | {cause.recheck} |")
    lines.append("")
    return lines
