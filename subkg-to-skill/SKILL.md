---
name: subkg-to-skill
description: "把 JSON 格式的知识图谱子图（故障诊断图谱的 node.json + edge.json）编译成符合模板的排障 skill：一个子图一份，图里每个故障成为一个场景，文档为「入参列表 / 前置检查 / 排查步骤 / 根因对照表」四章节，多场景时步骤按场景拆到 reference/ 下，生成后逐条回查原图、删除没有来源的内容，Claude Code、opencode 拷进去就能加载。当用户说“把这个子图/知识图谱变成 skill”“根据图谱生成排查技能包”，或手里有 node.json、edge.json 想变成智能体能用的排障文档时使用。Turns a fault-diagnosis knowledge-graph subgraph into template-conformant agent skills."
---

# 子图 → skill 生成器

**输入**：故障诊断知识图谱导出的 `node.json` + `edge.json`（字段见 [`reference/graph-schema.md`](reference/graph-schema.md)）。

**输出**：一个子图一份 skill 目录。故障 = 一个 symptom × 一个诊断单元，每个故障是一个场景；
四章节（入参列表 → 前置检查 → 排查步骤 → 根因对照表）是硬性要求，见
[`reference/skill-template.md`](reference/skill-template.md)。单故障只有 `SKILL.md`；多场景时
`SKILL.md` 留入参、公共前置与场景跳转表，每个场景拆到 `reference/<slug>.md`。

生成不调用大模型，内容由数据直接展开。**必须用脚本生成，不要照着模板手写**——命令去重、
案例剔除、跳转编号一致性这些是机器保证的。

## 流程（四段，不要跳段）

```bash
S=scripts/build_skill.py
```

1. **摸底**：`python3 $S inspect <图>`——节点/边分布、诊断单元、质量标记、结构校验（有错误退出码 1）。
2. **编排**：人和你一起判断，工具只给证据。判断依据与聚合边界见 [`reference/workflow.md`](reference/workflow.md)。
   ```bash
   python3 $S list <图> --show-causes                     # 一组里混了两类故障 → 拆
   python3 $S list <图> --suggest-merge                   # 名字不同、修复动作相同 → 合
   python3 $S list <图> --export-scenarios scenarios.json # 固化编排：填 name、每场景英文 slug、exclude
   ```
3. **规划后生成**：先 `plan` 看文档里实际会有什么，不满意回第 2 步改清单。
   ```bash
   python3 $S plan  <图> --scenarios scenarios.json
   python3 $S build <图> --scenarios scenarios.json --out out/<slug>
   ```
   只做一个故障用 `--entry <症状> --name <slug>`，每个故障各一份用 `--each`；参数全表见
   [`reference/cli.md`](reference/cli.md)。
4. **交付前检查**：`python3 $S check out/<slug> --graph <原图>`——模板检查（形状）+ 后校验
   （命令、根因、判据、入参、转向逐条回查原图）。`build` 会自动跑；**文档被人或智能体改过后必须重跑**，
   `--graph` 要给原图，不是生成物。

## 交付

- `check` 的 ERROR 必须修到零，只有两种处理：**删掉，或改回来源原样的写法**；不要换个说法绕过。
- WARNING（如"来源未给出修复命令"）如实转告用户，不要自己补命令消灭它。
- 回报六件事：生成了哪些 skill（场景 + 英文名）、`plan` 的规模数字、两道检查结果、
  交付统计里超出健康值的项、构建输出里「未进入正文的条目」、安装路径（整个目录一起拷，
  如 `cp -r out/<slug> ~/.claude/skills/<slug>`，`build` 输出会列出全部位置）。

## 硬性约束（手工润色时也要守住）

- **命令只能来自源数据**：`check` / `repair` / `escalation` 的 `command_templates` 与 `procedure`；
  `observation` 是回显，不是命令来源。来源只给修复方向就照实写，来源为空写"无直接修复CLI"。
- **不升级证据强度**：只有 `supports` 的根因带"仅支持性证据，需人工确认"；`excludes` 只排除它指向的原因。
- **空值不是承诺**：`service_impact` / `rollback` / `preconditions` 为空写"来源未给出"。
- **参数名沿用来源写法**，只把 `{x}` / `[x]` 规整成 `<x>`；命令里写死的取值（`slot 3`）保留原样和注意行，
  自造 `<slot-id>` 就是编造参数。案例里的 IP、设备名要么剔除，要么保留并标注，不要改成看似通用的值。
- **名字由人定**：技能名和场景 slug 是有语义的英文 slug，按症状语义拟定后请用户确认，不要音译。
- 不要补写来源里没有的字段（预期效果、回退方法、参数值）；不要为"覆盖全"用 `--all-units`。
- 不要合并同名节点：身份以 `node_id` 为准。生成的 skill 不替用户执行命令，变更类操作要用户确认。

措辞对照见 [`reference/evidence-rules.md`](reference/evidence-rules.md)；
图谱字段到四章节的映射见 [`reference/output-spec.md`](reference/output-spec.md)。
