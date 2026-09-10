# 图谱字段 → 四章节的映射

模板本身见 [`skill-template.md`](skill-template.md)。这一页说明生成器**怎么把图谱字段填进四章节**，
便于核对产物、或在数据缺失时判断该写什么。

一份 skill 覆盖一个 `symptom` 及其向前可达的原因、检查、观测、修复、升级节点
（并把 `supports` / `confirms` / `excludes` 这些指回原因的判据边拉回来）。

## 产物

```
<skill>/
├── SKILL.md                # 四章节，不放出处
├── reference/
│   ├── evidence.md         # 出处、证据强度、未求值条件、质量标记
│   └── subgraph.json       # 该故障的切片 + meta（来源、生成时间、计数）
└── scripts/kg_query.py     # 按相对路径读 ../reference/subgraph.json
```

frontmatter 的 `description` 由症状生成：`name`（+ `attrs.abnormal_behavior`）作故障现象，
`aliases` + `attrs.match_phrases` 作适用时机，`attrs.trigger_context` 作补充；
`name` 必须由调用方给出英文 slug。

## `# 入参列表`

| 来源 | 是否必填 | 说明列写什么 |
| --- | --- | --- |
| `symptom.attrs.required_slots` | 是 | 现场提供 |
| 前置检查命令里的 `<token>` | 是 | 前置检查步骤 N 命令参数 |
| 只在排查步骤命令里出现的 `<token>` | 否 | 从前置检查回显中提取，无需人工输入 |
| 只在修复/复检命令里出现的 `<token>` | 否 | 修复动作参数，按现场规划或回显确定 |

「信息」列由参数名把 `-` / `_` 换成空格得到（`<interface-type>` →「interface type」），
所以去掉空格和连字符后仍与 CLI 参数名一致；中文参数名原样保留。
同一参数按“去空格、去连字符、小写”归一，槽位与 CLI 参数会合并成一行。

## `# 前置检查`

来自 `symptom -diagnosed_by-> check`，以及 `symptom -next_step-> check` 及其 `next_step` 链
（最长 6 步，遇环即停）。每条：

- **CLI 命令**：该 check 的 `attrs.command_templates`（`{}` / `[]` 规整为 `<>`）；来源没有命令模板时
  写“来源未给出命令模板，按来源步骤说明人工采集”。
- **采集内容**：`attrs.intent` + 该检查可能观测到的 `attrs.field` 字段名；两者都没有时用 `attrs.procedure`。
- **根因定位**：只在该观测判定的原因**没有自己的排查步骤**时才写，避免同一根因判两遍。

## `# 排查步骤`

每条 `symptom -has_cause-> cause` 一步，顺序按边的 `rank`：

| 模板要求 | 数据来源 |
| --- | --- |
| 步骤名称 | `检查<原因名>` |
| CLI 命令 | 判据观测由某条前置检查产生 → “复用前置检查步骤 N 回显（检查名，查看 `字段` 字段）”；否则用该原因 `diagnosed_by` 检查的命令模板 |
| 跳转信息 | `confirms` / `supports` 观测 → “定位根因……结束排查”；`excludes` 观测 → “排除根因……顺序执行步骤 N+1”；末尾补“以上判据均不命中”一行 |
| 根因定位 | 该步骤能判定的原因名 |

- `supports` 判定的根因一律带“（仅支持性证据 `supports`，需人工确认）”。
- 最后一步的兜底写“判定‘未找到根因’，输出已执行的全部检查步骤及结果摘要，结束排查”。
- 原因没有任何判据观测时，跳转信息写“本子图未给出该原因的判定观测”，仍把它列进根因定位，
  对照表里的现象列注明“本子图未给出判定观测”。

## `# 根因对照表`

汇总前置检查与排查步骤里出现的全部根因，外加一行「未找到根因」。

| 列 | 数据来源 |
| --- | --- |
| 根因 | `cause.name`，与「根因定位」逐字一致 |
| 现象 | 判定该原因的观测表达式（优先 `normalized_expression`）+ 证据强度标注 |
| 修复CLI和方法 | `cause -repaired_by-> repair` 的 `command_templates`；没有命令就照抄 `procedure` 文字；都没有写“无直接修复CLI”。附 `service_impact` 作“影响”、`rollback` 作“回退” |
| 复检命令（可选） | 仅当 `repair -next_step-> check` 存在时取该检查的命令；否则 `-` |

多条命令在表格内用 `<br>` 分行。

## `reference/evidence.md`

四章节里不放出处，全部集中在这里：症状、检查动作、判据（观测 → 原因，含未求值条件）、
候选原因、修复动作、转交升级，每个节点给出 `node_id`、知识状态（`status` / `review_status` /
`human_reviewed`）、适用范围、诊断单元、质量标记与原文引文，末尾附读法提醒。
