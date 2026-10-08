# 输入数据字段字典

本技能读取的是 IP-RAN 故障诊断知识图谱导出的两个 JSON 数组：`node.json` 与 `edge.json`。
本页只写工具依赖的部分——节点角色、关系方向、`attrs` 业务字段、`scope`、`condition`、来源定位与质量标记。
校验（`python3 scripts/build_skill.py inspect`）只验证结构：ID 唯一、端点存在、端点类型组合合法；**结构合法不等于关系语义正确**。

本页不写数量：节点、边、各类型的条数随每次导出变化，以 `inspect` 对手里这份数据的输出为准。

## 1. 节点类型

| node_type | 中文 | 含义 |
| --- | --- | --- |
| `symptom` | 故障症状 | 用户可感知的异常现象或诊断入口。一个症状在一个诊断单元下是一个故障，即生成的 skill 里的一个场景。 |
| `cause` | 故障原因 | 解释症状的候选原因或故障机制；存在此节点不表示现场已确认。 |
| `check` | 检查动作 | 采集状态、查看配置或日志、探测连通性等诊断动作。 |
| `observation` | 观测结果 | 检查可能得到的状态、数值或现象；也可包含正常、否定结果。 |
| `repair` | 修复动作 | 原文支持的处置、恢复或规避措施。 |
| `escalation` | 转交/升级 | 移交技术支持、收集材料、转入其他故障流程。 |

## 2. 关系类型与允许的端点

方向即语义，工具据此展开手册；端点组合不合法的边会被丢弃并记入 `inspect` 的校验明细。

| edge_type | 允许的 source → target | 含义 |
| --- | --- | --- |
| `has_cause` | `symptom → cause` | 症状可能由该原因引起（不是原因传播方向）。 |
| `diagnosed_by` | `cause → check`，`symptom → check` | 通过该检查定位源症状或源原因。 |
| `observes` | `check → observation` | 该检查可产生或识别这个观测结果。 |
| `supports` | `observation → cause` | 观测为原因提供支持证据，尚不等于确认根因。 |
| `confirms` | `observation → cause` | 在给定条件与范围内，观测被表述为确认该原因；强于 `supports`。 |
| `excludes` | `observation → cause` | 在给定条件与范围内可排除该原因，不能外推为排除全部原因。 |
| `repaired_by` | `cause → repair` | 该修复动作用于处置源原因。 |
| `refines` | `cause → cause`，`cause → symptom` | 细化为更具体的描述，不是因果传播。 |
| `refers_to` | `cause|symptom → escalation`，`cause|symptom → symptom` | 转向其他入口、章节或支持流程；不是修复承诺。 |
| `next_step` | `check`/`observation`/`repair`/`symptom` → `check`/`repair`/`escalation` | 流程上的下一步，可能受 `condition` 限制。 |
| `leads_to` | `cause → cause`，`cause → symptom` | 原因向后果传播。 |

工具眼中的“向前”方向是 `has_cause → diagnosed_by → observes → repaired_by / next_step / refines / refers_to / leads_to`；
`supports` / `confirms` / `excludes` 指回原因，因此在子图选择时会被额外拉回来（见 `--root` / `--depth`）。

## 3. 节点顶层字段

| 字段 | 类型 | 工具怎么用 |
| --- | --- | --- |
| `node_id` | string | 唯一标识，视为不透明 ID；文件名、引用、脚本查询都用它。 |
| `node_type` | string | 六类角色之一，决定 `attrs` 的读法。 |
| `name` / `description` | string | 手册标题与说明。同名节点不合并——范围可能不同。 |
| `aliases` | array | 与 `attrs.match_phrases` 一起构成“触发说法”。 |
| `attrs` | object | 类型相关业务属性，见第 4 节。 |
| `scope` | object | 适用范围，见第 5 节。 |
| `status` / `review_status` | string | 生命周期与审核状态；快照通常是 `candidate` / `machine_checked`。 |
| `diagnostic_contexts` | array | `{section, title}` 数组，作为“诊断单元”，也是 `--section` 与索引分片的依据。 |
| `provenance` | array | 来源证据，见第 7 节。 |
| `quality_flags` | array | 质量与待核查标记，见第 8 节。 |
| `semantic_review` | object | 语义复核元数据；`human_reviewed` 为 `false` 时只是机器复核。工具不读。 |
| `canonical_key`、`scope_context_ids`、`source_keys`、`concept_alignment_ids`、`automatic_resolution` | — | 身份、来源分类与历史修正的簿记字段。工具不读，生成的 skill 里也不出现。 |

## 4. `attrs` 业务字段

工具只读下面加粗说明的几个键；其余字段后校验时仍算作“来源原文”，但不进文档。

**symptom**：`object_type`、`abnormal_behavior`、`expected_behavior`、`trigger_context`、
`required_slots`（现场需补齐的信息槽位）、`match_phrases`、`example_specific`。

**cause**：`cause_kind`、`source_cause_kind`、`affected_object`、`fault_mechanism`、`granularity`、`example_specific`。

**check**：`check_kind`、`target_object`、`intent`、`procedure`、`command_templates`、`parameters`、
`execution_context`、`collection_spec`、`execution_effect`、`operational_context`、`execution_policy`、
`preconditions`、`example_specific`。

**observation**：`object_type`、`field`、`operator`、`value`、`unit`、`aggregation`、`window_seconds`、
`sample_count`、`normalized_expression`、`predicate`、`knowledge_predicate`、`predicate_execution`、
`source_operator`、`knowledge_mode`、`example_specific`。
渲染时优先用 `normalized_expression`，没有才回落到 `object_type.field operator value (unit)`。

**repair**：`repair_kind`、`target_object`、`procedure`、`command_templates`、`parameters`、
`execution_context`、`preconditions`、`expected_effect`、`service_impact`、`rollback`、
`operational_context`、`execution_policy`、`example_specific`。

**escalation**：`destination_role`、`collection_requirements`（数组或字符串）、`handoff_template`、
`instructions`（数组、字符串或 null），也可能带 `procedure` / `command_templates` / `preconditions`。

几条读法约定：

- `command_templates` 是**模板**，含待绑定参数（`parameters`）或案例常量，不是可直接下发的脚本。
- 修复动作的 `preconditions` / `service_impact` / `rollback` 有值就写进根因对照表；为空只表示来源没写，
  文档里写“来源未给出：…”，不当作“无前置条件 / 无影响 / 无需回退”。
- `execution_policy`、`expected_effect`、`collection_spec`、`execution_context`，以及 escalation 的
  `destination_role` / `collection_requirements` / `instructions`，工具目前不写进文档。
- `example_specific: true` 表示带案例特定背景，不能推广到其他设备。
- `check_kind` / `repair_kind` 是自由文本分类，没有统一枚举，工具不按枚举解析。

## 5. `scope` 与诊断上下文

`scope` = `{vendor, product_family, software_version, component_type, scenario, scope_basis}`。
`null` 表示**未明确**，不表示“适用所有版本/场景”。`scope_basis` 说明范围信息的来源依据
（`document_product`、`document_solution`、`case_metadata`、`source_scenario`、`source_explicit`、
`technote_specific_scope_required`）。

`scope.vendor` 保留来源写法，`Huawei`、`HUAWEI`、`华为` 并存；`--vendor` 只做大小写无关比较，
不会把中英文写法认作同一个，按厂商筛选时要把几种写法都给上。

`diagnostic_contexts[].section` 是诊断单元标识：PDF 通常是章节号，其他来源可能是
`ipran_battle_tree:s0:r56` 这类 ID——**不要一律当数字处理**。`--section` 做前缀匹配（`28.21` 命中 `28.21.4`）。
切分故障用的是**边**的 `diagnostic_context.section`，不是节点的：节点可以跨多个诊断单元复用，边保存自己的上下文。

## 6. 边字段与 `condition`

边除 `edge_id` / `edge_type` / `source` / `target` 外，工具关心：

- `condition`：递归结构，`type` 为 `atomic` / `text` / `and` / `or` / `not`；
  `and`/`or` 用 `conditions` 数组，`not` 用 `condition` 单子条件。
  **任何类型的边都可能带条件**；文档里用到这条关系的地方就把条件写成 `〔条件：…（未求值）〕`，
  写在哪见 [`evidence-rules.md`](evidence-rules.md#条件)。
- `condition_status`：`unconditional`、`parsed`、`preserved_unparsed`、`text_only`、`requires_interpretation`。
  它描述**解析状态**，不是现场求值结果——文档里一律标注“未求值”。
- `original_condition`：融合时保留的来源写法，可能带 `var`、`comparison`、`other`、`negate`；
  `condition` 为空而 `condition_status` 又不是 `unconditional` 时，用它代替。
- `rank`：流程中的顺序号/排序提示，**不是概率、置信度或全图统一的优先级**；工具用它给排查步骤排序，
  多个来源合并时各来源的 `rank` 并不可比。
- `source_contexts`：极少数边由多个来源的同一关系合并而成，这里保留各来源的上下文；
  工具只按边自己的 `diagnostic_context` 切分诊断单元。
- `review_status`：边有 `machine_checked` 和 `unreviewed` 两种，文档目前不区分。
- `diagnostic_context`、`evidence`、`quality_flags`：工具不写进文档；`evidence` 等原文在后校验时算作来源。

操作符：`eq`、`ne`、`gt`、`ge`、`lt`、`le`、`in`、`not_in`、`exists`、`not_exists`、`contains`、`trend`；
另有来源写法 `not_contains`、`not_eq`、`==`、`!=`、`>`、`is_null` 等，会带“来源写法”标注输出，
未知操作符原样保留并标为“未知操作符，按原文解释”。

## 7. 来源定位

节点 `provenance[]`、边 `evidence[]`、动作 `attrs.operational_context[]` 共用一组字段：
`document`、`document_version`、`page`、`printed_page`、`section`、`block_id`、`bbox`、`quote`、
`char_start`/`char_end`、`source_kind`（`pdf_text` / `image_ocr` / `xlsx_cell` / `icase_json`）、
`anchor_method`、`source_key`；Excel 来源另有 `sheet`、`cell`、`row`、`column`、`field`、`context`；
案例来源另有 `record`、`json_path`、`case_uuid`、`original_source`。

工具不把来源定位写进文档；`quote` 等原文在后校验时算作来源。
引文可定位只说明**文字依据可查**，不自动证明关系方向或诊断结论正确；`image_ocr` 尤其不能证明流程图箭头。

## 8. 质量标记

`inspect` 会统计 `quality_flags`，文档里不写。常见值：`non_executable_knowledge`、`ocr_only`、
`ocr_only_requires_visual_review`、`procedure_uses_source_quote`、`semantic_review_pending`、
`human_review_pending`、`condition_requires_semantic_interpretation`、`verify_technote_product_and_version`、
`cause_kind_pending`，以及 `unmapped_cause_kind:<原始值>`、`ungrounded_<字段>_removed`、
`unverified_<字段>_removed` 这类前缀形式。

`machine_checked` 不能替代 `human_reviewed`；节点与边通常全部是 `candidate`（候选知识）。

## 9. 输入形状

以下都能读：

- 目录：自动识别 `node*.json` / `edge*.json`（也接受 `nodes` / `edges` 命名）；
- 两个数组文件：`python3 scripts/build_skill.py build node.json edge.json ...`；
- 整包对象：`{"nodes": [...], "edges": [...]}`；
- 混合数组：带 `edge_type` 或同时带 `source`+`target` 的记录算作边；
- `.jsonc`（`//`、`/* */`、尾逗号、BOM）与 `.jsonl` / `.ndjson`。

形状不好猜时用 `--nodes` / `--edges` 明确指定文件角色。
