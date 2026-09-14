# 措辞对照表：数据情况 → 怎么写

图谱里是**候选知识**（`status=candidate`），`machine_checked` 只表示机器复核，
`semantic_review.human_reviewed` 通常是 `false`。生成器按下表措辞；
手工润色产物、或直接用这些数据回答用户时，也守同一套。

## 关系与判据

| 数据情况 | 必须这么写 | 不能这么写 |
| --- | --- | --- |
| `confirms` 边 | “在给定条件与范围内，该观测确认此原因” | “已确认根因” |
| 只有 `supports` | “该观测支持此原因”，并保留“不要据此宣布根因已确认” | “确认”“基本可以断定” |
| `excludes` 边 | “可排除**这个**原因” | “排除了硬件问题”这类外推 |
| `has_cause` | “症状可能由该原因引起” | “该原因导致了症状”（因果传播是 `leads_to`） |
| `refines` | “细化为更具体的描述” | 当作因果链 |
| `refers_to` | “转向其他入口/章节/支持流程” | 当作修复承诺 |
| `next_step` | “流程上的下一步” | 当作因果推断 |
| `rank` 有值 | “来源顺序号，仅排序提示” | “优先级/概率/置信度” |

## 条件

| 数据情况 | 写法 |
| --- | --- |
| `condition` 非空 | `条件（<condition_status 的中文解释>，未求值）：…` |
| `condition_status=parsed` | 补一句“仍需绑定对象/字段/采样数据后判断” |
| `text_only` / `requires_interpretation` | 明确写“需解释”，不要把文本里的 AND/OR 当可执行逻辑 |
| `original_condition` 与 `condition` 不同 | 并列显示原始写法，供核查原意 |
| 现场数据不足以判断 | 标“未验证”，不要默认成立或默认不成立 |

## 属性缺失

| 数据情况 | 写法 |
| --- | --- |
| `service_impact` 为空 | “来源未给出（空值不表示无影响）” |
| `rollback` 为空 | “来源未给出（空值不表示无需回退）” |
| `expected_effect` 为空 | “来源未给出（不要自行补充）” |
| `preconditions: []` | “空数组不证明无需前置条件” |
| `command_templates` | “参数需绑定现场上下文后才可执行”，并列出 `parameters` |
| `execution_policy` 有值 | 照写“模板需绑定现场上下文”或“原文操作需解释后使用” |

## 范围与适用性

| 数据情况 | 写法 |
| --- | --- |
| `scope.software_version=null` | “版本未限定”，不是“适用所有版本” |
| `scope_basis=technote_specific_scope_required` | 提示“仍需核对具体产品与版本” |
| `example_specific: true` | “案例特定，勿直接套用到其他设备或场景” |
| 同名不同 `node_id` | 分别对待，不合并 |

## 质量标记

`quality_flags` 逐条给出中文解释，尤其：`non_executable_knowledge`（不保证可执行）、
`ocr_only` / `ocr_only_requires_visual_review`（仅 OCR 依据，需图像复核）、
`semantic_review_pending`、`human_review_pending`、
`condition_requires_semantic_interpretation`、`unmapped_cause_kind:<原值>`。

## 引用

每条结论附 `node_id` 和来源定位：文档、物理页（印刷页）、章节、`block_id`、原文引文；
Excel 来源给 `sheet`/`cell`，案例来源给 `case_uuid`。
**引文可定位只说明文字依据可查，不自动证明关系方向或诊断结论正确**；
`image_ocr` 尤其不能证明流程图箭头方向。回查不到就直说。

## 后校验：什么算有来源

`build_skill.py verify <skill> --graph <原图>` 把成品 `SKILL.md` 拆开，逐条回查原图。
生成器本身不会写出无来源的内容，所以这一步防的是**产物被改过之后**：人工润色、
智能体合并、"补全"一句看着更完整的说法——这些都会带进图里没有的东西。

| 文档里的内容 | 只认这些来源 | 判定 |
| --- | --- | --- |
| CLI 命令（前置检查、场景跳转表、排查步骤、修复/复检列） | `check` / `repair` / `escalation` 的 `attrs.command_templates`，经与生成器相同的清洗（`{x}` → `<x>`、去设备提示符、缩写与全称折叠、剔除回显与表格行） | ERROR |
| 根因名（对照表首列、根因定位、跳转里的"定位/排除根因"） | `cause` 节点的 `name` | ERROR；只是写法不同（空格、连字符）会直接报出来源写法 |
| 判据（跳转信息、场景跳转表、现象列、采集字段） | `observation` 的表达式、`field`、取值、`normalized_expression` | ERROR |
| 入参列表的「信息」列 | 症状的 `required_slots`，或正文命令里真实出现的 `<参数>` | ERROR |
| 修复说法、采集说明等自由文本 | 来源记录里出现过的原文（含 `procedure`、`service_impact`、`rollback`） | WARNING |

不查的：模板自带的固定说法（"未找到根因""复用前置检查步骤 N 回显""无直接修复CLI""现场提供"
以及场景跳转表的引导语）是渲染脚手架，不是对网络的断言；`supports` / `confirms` / `excludes`
是图谱词汇，也不算判据。

**ERROR 必须删掉或改回来源原样的写法**——"意思差不多""命令应该是这么写的"都不成立：
`observation` 是某台设备当时的回显，里面的配置片段不是命令来源；
来源只给一句修复方向就照实写，不要补全成可执行序列。
WARNING 要逐条看：确认是补写的，同样删掉或改回原文。

校验用的图要给**原图**（`--graph` 指向 node/edge 导出），而不是生成物；
不给 `--graph` 时只会去找 `<skill>.internal/subgraph.json`（`build --with-subgraph` 才有），
那只是构建期切片，够用于刚生成完的快速自查。
