# 排查型 skill 模板

四段式模板（完整规范见 [`src/graph2skill/resources/step_skill_spec.md`](../src/graph2skill/resources/step_skill_spec.md)）：

```
---
name: bgp-route-flap
description: 故障现象 + 适用时机
---
# 入参列表      表格：信息 | 是否必填 | 说明
# 前置检查      有序列表：CLI 命令 / 采集内容 /（可选）根因定位，线性执行不跳转
# 排查步骤      ## 步骤N：名称，四项：步骤名称 / CLI 命令 / 跳转信息 / 根因定位
# 根因对照表    表格：根因 | 现象 | 修复CLI和方法 | 复检命令（可选）
```

## 生成

```bash
graph2skill steps graphs/*.json -d out/            # 每个故障一份文档
graph2skill steps graphs/*.json --fault 11 -o a.md # 只生成一个
graph2skill steps graphs/*.json --common graphs/common.json --common-doc common.md
```

`--common` 传公共技能的子图后，公共节点不会被写进这份 skill，只保留引用。

## 图数据怎么映射到模板

| 模板位置 | 取自 |
| --- | --- |
| `name` | 故障节点 `data.identifier`，否则由节点 id 转 ASCII |
| `description` | 故障节点的 `description` + 前三条 `observations` |
| 入参列表 | 故障节点 `data.parameters`（显式声明）+ 所有命令里的 `<...>` 占位符 |
| 前置检查 | 挂在故障节点下的 CHECK 节点（命令 = `parameterExamples`，采集内容 = `observations`） |
| 排查步骤 | 每个 CAUSE 一步；命令取该原因下的 CHECK，判据取 `data.trigger` |
| 根因对照表 | CAUSE 的 `name` / `trigger` / 修复（`data.fix` 或其 ACTION 节点）/ `data.verify` |

显式声明入参的写法：

```json
"parameters": [
  {"cli": "<endpoint-ipv6>", "label": "endpoint IPv6", "required": true, "note": "告警中的 endpoint 地址"},
  {"cli": "<segment-list-id>", "label": "segment-list ID", "required": false, "note": "从前置检查步骤1回显中提取"}
]
```

命令在前置检查里已经出现过时，步骤里不会重复下发，而是写「复用前置检查步骤N回显」。

## 校验规则

`graph2skill lint <文档...> [--graph <图...>]`，带 `--graph` 时才校验命令白名单。

| code | 级别 | 规则 |
| --- | --- | --- |
| `front-matter-missing` / `name-missing` / `name-format` / `description-missing` | error | front matter 必须有 name（小写连字符）与 description |
| `sections` | error | 一级标题必须依次为 入参列表 → 前置检查 → 排查步骤 → 根因对照表 |
| `param-table-missing` / `param-table-header` | error | 入参列表必须有表，表头为 `信息 \| 是否必填 \| 说明` |
| `param-undeclared` | error | 命令里的 `<参数>` 必须出现在入参列表（去空格连字符后比对） |
| `param-inconsistent` | error | 同一参数全篇必须同名 |
| `precheck-param-undeclared` / `precheck-param-optional` | error | 前置检查只能用入参列表里的**必填**项 |
| `precheck-jump` | warning | 前置检查不应出现跳转 |
| `step-field-missing` | error | 每步必须有 步骤名称 / CLI 命令 / 跳转信息 / 根因定位 |
| `step-numbering` | error | 步骤编号从 1 连续 |
| `step-jump-unknown` | error | 跳转的步骤号必须真实存在 |
| `last-step-outcome` | error | 最后一步必须写清全不命中时判定「未找到根因」 |
| `cause-not-in-table` | error | 步骤里的根因必须逐字出现在根因对照表 |
| `cause-not-in-steps` | warning | 对照表里的根因没有任何步骤定位到它 |
| `no-cause-row-missing` / `no-cause-row-fix` | error | 必须有「未找到根因」行，修复列写「输出已执行的全部检查步骤及结果摘要」 |
| `cause-fix-empty` | error | 修复列为空 → 应写「无直接修复CLI」并说明只能定位 |
| `cause-verify-empty` | warning | 复检列为空 → 应写 `-` |
| `command-not-in-source` | error | **命令必须来自源数据**，比对时忽略空白与具体参数名 |
| `step-cause-has-command` | warning | 根因定位里不应出现修复命令 |
| `interface-abbreviated` | warning | 接口名要写全称 |
| `placeholder-style` | warning | 可变参数要用 `<>`，不要 `{}` / `[]` / `XXX` |

命令识别的口径：反引号里、以小写字母开头且含空格的代码片段算命令候选，
所以 `Policy State`、`Down (Shutdown)` 这类字段名和取值不会被误判成命令。

## 为什么初稿是程序生成的

模板里绝大多数约束是结构性的（章节顺序、编号连续、两处根因名称一致、命令来自源数据）。
程序渲染出的初稿天然满足这些，模型只需要在此基础上改措辞——而不是从零凑格式。
即使不接模型，`graph2skill steps` 的产物也是一份可用且合规的 skill。
