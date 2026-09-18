# subkg-to-skill 设计文档

面向维护者：这套工具吃什么、吐什么、一次构建内部发生了什么、每个文件负责哪一块。

使用者视角的文档在别处，本文不重复：
[`subkg-to-skill/SKILL.md`](../subkg-to-skill/SKILL.md)（四段流程）、
[`reference/cli.md`](../subkg-to-skill/reference/cli.md)（参数全表）、
[`reference/skill-template.md`](../subkg-to-skill/reference/skill-template.md)（产出模板规范）、
[`reference/graph-schema.md`](../subkg-to-skill/reference/graph-schema.md)（输入字段字典）、
[`reference/output-spec.md`](../subkg-to-skill/reference/output-spec.md)（字段 → 章节映射）、
[`reference/evidence-rules.md`](../subkg-to-skill/reference/evidence-rules.md)（措辞与后校验口径）。

---

## 1. 输入与输出

### 输入：故障诊断知识图谱的子图

两个 JSON 数组：`node.json` + `edge.json`。**全部是候选知识**（`status=candidate`），
`review_status=machine_checked` 只表示机器复核，不等于人工确认——这个前提决定了下游所有措辞规则。

**六类节点**

| `node_type` | 含义 | 生成时的用途 |
| --- | --- | --- |
| `symptom` | 故障症状 | 文档入口；`attrs.required_slots` 变成入参列表 |
| `cause` | 故障原因 | 一条 `has_cause` 一个排查步骤 + 一行根因对照表 |
| `check` | 检查动作 | **命令的合法来源之一**（`attrs.command_templates` / `procedure`） |
| `observation` | 观测结果 | 判据来源；**不是命令来源**（回显里的配置片段不是模板） |
| `repair` | 修复动作 | 根因对照表的「修复CLI和方法」；命令来源之一 |
| `escalation` | 转交 / 升级 | 命令来源之一；转交对象与需收集的材料 |

**十一类边**（方向即语义，端点组合由 `schema.EDGE_RULES` 约束）

`has_cause`、`diagnosed_by`、`observes`、`supports` / `confirms` / `excludes`、
`repaired_by`、`refines`、`refers_to`、`next_step`、`leads_to`。

其中三条判定边强度不等：`confirms` > `supports`（必须带"仅支持性证据，需人工确认"）；
`excludes` 只排除它指向的那一个原因，不能外推。

**文件形状**不挑：目录（自动识别 `node*.json` / `edge*.json`）、两个数组文件、
`{"nodes": [...], "edges": [...]}` 整包对象、混合数组、`.jsonc`（注释与尾逗号）、`.jsonl`；
猜不准时用 `--nodes` / `--edges` 指定。

### 输出：一个 `SKILL.md`

**交付物只有一个文件**，子图不对外暴露。四章节顺序固定：

```
out/isis-neighbor-down/
└── SKILL.md      # 入参列表 → 前置检查 → 排查步骤 → 根因对照表
```

文档有两种形态，取决于覆盖几个故障：

| 形态 | 分流表 | 步骤标题 | 根因对照表 | 采集标注 |
| --- | --- | --- | --- | --- |
| 单故障 | `## 步骤跳转表` | `## 步骤N` | 一张 | 适用步骤 |
| 多故障 | `## 场景跳转表` | `### 场景X` + `#### 步骤N` | 每场景一张 | 适用场景 |

**构建期内部产物**默认不生成，按需导出到 `<输出目录>.internal/`（`INTERNAL_SUFFIX`），
与技能目录并排，所以 `cp -r <skill>` 永远带不走它们：

| 文件 | 开关 | 内容 |
| --- | --- | --- |
| `evidence.md` | `--with-evidence` | 每条判据/命令/修复的出处、证据强度、未求值条件、被剔除与被拒绝的条目 |
| `subgraph.json` | `--with-subgraph` | 该故障的子图切片 + meta（`--data slim` 可去簿记字段） |
| `kg_query.py` | `--with-script` | 零依赖查询脚本，读同目录的 `subgraph.json` |

---

## 2. 如何使用

零依赖，Python 3.9+ 标准库，从技能目录直接运行：

```bash
S=subkg-to-skill/scripts/build_skill.py
```

### 四段流程（不要跳段）

```bash
# 一 · 数据摸底：手里这张图是什么
python3 $S inspect  <图>          # 节点/边分布、诊断单元、厂商、质量标记、孤立节点
python3 $S validate <图>          # 只做结构校验；有错误退出码 1

# 二 · 编排判断：哪些症状进同一份 skill（人做判断，工具给证据）
python3 $S list <图> --show-causes        # 每组根因按来源列出 → 该不该拆
python3 $S list <图> --suggest-merge      # 名字不同但根因重叠 → 比对修复动作定夺
python3 $S list <图> --export-scenarios scenarios.json   # 判断固化成清单

# 三 · 规划生成：文档实际会有多大
python3 $S plan  <图> --scenarios scenarios.json
python3 $S build <图> --scenarios scenarios.json --out out/isis-troubleshooting

# 四 · 交付校验：形状 + 出处
python3 $S lint   out/isis-troubleshooting
python3 $S verify out/isis-troubleshooting --graph <图>
```

单故障一份、以及批量：

```bash
python3 $S build <图> --entry symptom_7f1c --unit 28.21.3 \
        --name isis-neighbor-down --out out/isis-neighbor-down
python3 $S build-all <图> --out out/ --names names.json
```

### 安装生成器本身

```bash
python3 install.py                 # 装到 Claude Code 用户目录
python3 install.py --target all    # Claude Code + opencode
python3 install.py --link          # 软链到仓库，git pull 即更新
```

### 常用旋钮

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `--unit SECTION` | — | 只保留该诊断单元的关系；单症状横跨多单元时必填 |
| `--entry NODE` | — | 入口症状，可重复（多个即合并成一份） |
| `--merge-same-name` | 关 | 把其他来源同名症状并进来 |
| `--exclude VALUE` | — | 人工剔除跨领域节点（整条链一起走），匹配不到会报错 |
| `--shared-coverage R` | 0.8 | 公共采集的门槛（见 §3） |
| `--include-example-specific` | 关 | 保留案例特定内容（IP、设备名、组网） |
| `--keep-undecidable` | 关 | 保留既无判据也无修复的原因 |
| `--max-steps N` | 0 | 排查步骤上限 |
| `--strict` | 关 | 把校验告警也当错误 |
| `--dry-run` | 关 | 只打印将写出的内容与检查结果 |

---

## 3. 主要流程

### 一次 `build` 的数据流

```
node.json / edge.json
   │
   ├─ loader.load ──────────── 容错解析（BOM、注释、尾逗号、JSONL），按记录形状拆分节点与边
   │
   ├─ graph.Graph.from_bundle ─ 结构校验：缺 id / 未知类型 / 悬空边 / 端点非法 → 丢弃并记账
   │                            重复 id、重复关系、自环 → 告警（--strict 时升级为错误）
   │
   ├─ cli._select ──────────── 可选收窄：--root/--node-type/--section/--vendor/--query
   │                            → graph.reachable 沿诊断方向前向闭包，把判据边反向拉回
   │                            → 不跨 refers_to / leads_to（那是另一个故障）
   │
   ├─ graph.scope_to_units ─── 按诊断单元切分（这是"一份 skill 只讲一个故障"的关键）
   ├─ cli._apply_exclusions ── 人工剔除；后校验对照的是剔除**前**的图
   │
   ├─ playbook.build_merged_playbook
   │      symptom → has_cause → cause → diagnosed_by → check → observes → observation
   │      判定边 confirms/supports/excludes 反向挂回 cause；next_step 链最多展开 MAX_CHAIN=6
   │      跨来源同名症状在此折叠：同名根因合成一条，判据与修复取并集
   │
   ├─ template.build_multi_doc ── 见下
   ├─ template.render_doc ─────── 四章节 Markdown
   │
   ├─ lint.lint_text ─────────── 形状检查
   ├─ verify.verify_text ─────── 出处检查（对照剔除前的图切片）
   └─ render.SkillPackage.write ─ SKILL.md 写到 out/，内部产物写到 out.internal/
```

### `build_multi_doc` 内部（文档成形的地方）

1. **逐场景展开**（`_build_scenario`）：入口检查 → 前置检查候选；每条 `has_cause` → 一个步骤；
   判据来自 `confirms` / `supports` / `excludes`；修复来自 `repaired_by`。
   命令经 `hygiene` 清洗：回显行、表格行、散文被剔除并记账；缩写与全称折叠成一条，
   另一种写法登记为"来源另有写法"。
2. **前置检查按命令签名合并**（`_equivalent_precheck` + `same_command_set`）：
   跑同一条命令的多个 check 节点合成一条采集，采集内容取并集。
3. **剪枝**：没人引用、又不判定根因的采集删掉。
4. **公共采集的两级判定**（同一把尺子，子单位不同）：

   | 层级 | 子单位 | 函数 | 留在公共层的条件 |
   | --- | --- | --- | --- |
   | 多故障文档 | 场景 | `_split_collection` | 覆盖率 ≥ `--shared-coverage`（默认 0.8），**或**读数决定分流，**或**采集期就判根因 |
   | 单故障文档 | 步骤 | `_share_across_steps` | 同上，`max(2, ceil(0.8×步骤数))` |

   不够格的**下沉到每个读它的子单位**（"本场景采集" / 由第一个需要它的步骤下发），
   留在公共层意味着其余读者在真设备上白敲一条命令。
5. **分流表**（`_route_to_steps` / 场景版同理）：只有能**唯一指向**一个子单位的读数才成行；
   指向所有子单位的读数不是分类依据，退回普通采集。
6. **引用解析**（`_resolve_references`）：步骤先用 `@@node_id@@` 占位，
   等两层都定下来再决定它指向"前置检查步骤 N"还是"本场景采集 N"。
7. **入参汇总**（`_build_params`）：`required_slots` + 正文命令里真实出现的 `<参数>`；
   抽取哈希不计入参数身份（`peer ip c8be5e6454` 与 `peer ip 4f2ab19c07` 合成一行）；
   纯哈希与拓扑编号（`device B`）报为伪参数，不向现场索取。

### 两道检查的分工

| | `lint`（形状） | `verify`（出处） |
| --- | --- | --- |
| 看什么 | 章节顺序、步骤编号连续、跳转目标存在、根因逐字可查、参数已声明且全篇同名、占位符写法、判据区分力 | 命令 / 根因 / 判据 / 入参逐条回原图查 |
| 对照物 | 文档自身 | 原图（`--graph`）或构建期切片 |
| 失败含义 | 不符合模板 | **幻觉**：删掉，或改回来源原样的写法 |

两者都在 `build` / `build-all` 里自动跑，有 ERROR 退出码为 1。

### 交付统计（只报不拦）

`plan` 与 `build` 末尾输出。前半衡量优化是否落地（命令复用率、判据密度、步骤:根因、复检覆盖率），
后半衡量**聚合边界**——一份 skill 该有多大：

| 指标 | 健康值 | 常量 |
| --- | --- | --- |
| 场景数 | 5–7 个 | `plan.TARGET_SCENARIOS` |
| 公共前置 | 3–7 条 | `plan.TARGET_PRECHECKS` |
| 根因覆盖 | ≥ 30 个 | `plan.TARGET_CAUSES` |
| 文档规模 | ≈ 500 行 | `plan.TARGET_LINES` |

超出不会让构建失败——它是交付时要说明的事，不是可以静默放过的事。

---

## 4. 代码文件职责

### 生成器（`subkg-to-skill/scripts/subkg2skill/`）

| 文件 | 职责 | 关键出口 |
| --- | --- | --- |
| `loader.py` | **读盘**。容错解析 JSON / JSONC / JSONL，按记录形状拆分节点与边；不认识的形状报错并指出文件 | `load`、`RawBundle`、`SubgraphLoadError` |
| `schema.py` | **本体词表**。六类节点、十一类边及其合法端点组合、操作符 / 条件状态 / 质量标记的中文解释。不读数据，只提供词汇 | `EDGE_RULES`、`endpoints_allowed`、`quality_flag_note` |
| `graph.py` | **内存子图**。`Node` / `Edge` 是原始 dict 的类型化视图（保留全部字段以便追溯）；负责校验丢弃、邻接查询、按单元/条件选图、统计 | `Graph.from_bundle`、`reachable`、`scope_to_units`、`ValidationReport` |
| `hygiene.py` | **抽取噪声治理**。把回显行、表格行、散文从 `command_templates` 里剔除并给出理由；缩写与全称按 token 前缀折叠；识别拓扑编号与抽取哈希 | `filter_commands`、`same_command`、`merge_forms`、`generalise_slot` |
| `playbook.py` | **按图展开**。从症状走出一份诊断剧本（入口检查、候选原因、判据、修复）；跨来源同名故障归并；合并候选建议 | `build_playbook`、`build_merged_playbook`、`fault_groups`、`suggest_merges`、`entry_scenarios` |
| `template.py` | **文档成形**（最大的一个）。剧本 → 四章节：前置检查合并、公共采集两级判定、分流表、步骤与根因表、入参汇总，最后渲染 Markdown | `build_multi_doc`、`render_doc`、`SkillDoc`、`BuildPolicy`、`SHARED_COVERAGE` |
| `describe.py` | **属性转文字**。把节点 `attrs` 渲染成 Markdown 片段（检查/修复/升级/原因/症状各一套），空值如实写"来源未给出" | `node_block`、`observation_expression`、`trust_line`、`scope_line` |
| `condition.py` | **条件转文字**。递归条件（`and`/`or`/`not`/`atomic`/`text`）渲染成一行，并始终标注"未求值" | `format_condition`、`describe_edge_condition` |
| `render.py` | **组包**。frontmatter（技能名 slug、description）、证据文件、子图切片、落盘布局（交付物与内部产物分家） | `build_package`、`BuildOptions`、`SkillPackage`、`normalise_name` |
| `lint.py` | **形状检查**。四章节顺序、步骤编号、跳转目标、根因逐字可查、参数声明、占位符、判据区分力、文档过长 | `lint_text`、`lint_path`、`LintResult` |
| `verify.py` | **出处检查**。把成品拆回命令/根因/判据/入参，逐条在图里找来源；模板脚手架不误报 | `verify_text`、`verify_path`、`Ground`、`Finding` |
| `plan.py` | **生成前预演 + 交付统计**。文档实际会有多少步骤/根因/修复命令、还能合并什么、四项边界指标 | `plan_document`、`render_plan`、`metrics`、`shape_metrics` |
| `cli.py` | **命令行**。八个子命令的参数、编排与输出；场景清单的读写；把上面各层串起来 | `main`、`build_parser`、`cmd_*` |
| `resources/kg_query.py` | 随内部产物导出的**查询脚本**，零依赖，读同目录 `subgraph.json`（stats / search / show / neighbors / expand / path） | — |

### 仓库其他部分

| 路径 | 内容 |
| --- | --- |
| `subkg-to-skill/SKILL.md` | skill 入口：四段流程、硬性约束。**上限 220 行**（`tests/test_skill_doc.py` 守着），细则进 `reference/` |
| `subkg-to-skill/reference/*.md` | 模板规范、字段字典、映射表、措辞对照、CLI 全表 |
| `subkg-to-skill/scripts/build_skill.py` | 入口脚本，只负责把 `scripts/` 加进 `sys.path` 后调 `cli.main` |
| `install.py` | 一条命令安装/更新到 Claude Code / opencode；重跑即整体替换 |
| `.claude-plugin/marketplace.json` | 插件市场清单 |
| `examples/subgraph/` | 最小可运行示例：17 节点 / 26 边，六类节点与十一类边全覆盖 |
| `tests/data/messy/` | 回归用"脏"子图：跨三个诊断单元、命令重复、案例特定内容、无判据原因 |
| `tests/data/multisource/` | 同一故障被手册 / 作战树 / 案例库各写一遍，用于验证跨来源合并 |

### 测试（`tests/`）

| 文件 | 覆盖 |
| --- | --- |
| `test_loader.py` / `test_graph.py` | 读盘容错、结构校验与丢弃、选图 |
| `test_condition.py` / `test_describe.py` | 条件与属性的措辞规则 |
| `test_playbook.py` / `test_merge.py` / `test_scenarios.py` | 展开、跨来源归并、按诊断单元切分 |
| `test_hygiene.py` | 回显/表格/散文识别、缩写折叠、拓扑编号与抽取哈希 |
| `test_template.py` / `test_bundle.py` / `test_shared_steps.py` | 四章节成形、多场景形态、公共采集两级判定 |
| `test_render.py` | 组包、交付物与内部产物分家 |
| `test_lint.py` / `test_verify.py` | 形状检查与出处检查（含幻觉注入） |
| `test_metrics.py` | 交付统计与边界指标 |
| `test_cli.py` / `test_skill_doc.py` / `test_kg_query.py` / `test_install.py` | 端到端命令、skill 自身的合规、查询脚本、安装器 |

```bash
pip install pytest && python -m pytest -q
```

---

## 5. 几条不变量

改代码前先知道这些，它们是整套设计的地基：

1. **不调用大模型。** 内容由数据直接展开，可重复、可比对、可追溯。生成器里出现推理式改写就是走错方向。
2. **命令只能来自源数据**：`check` / `repair` / `escalation` 的 `attrs.command_templates` 与 `procedure`。
   `observation` 是回显，不是命令来源。来源只给一句方向就照实写，为空就写"无直接修复CLI"。
3. **不升级证据强度**：`supports` 不能写成确认；`excludes` 只排除它指向的那个原因。
4. **空值不是承诺**：`service_impact` / `rollback` / `preconditions` 为空写"来源未给出"。
5. **剔除必须记账**：任何被丢弃的节点、边、命令、参数都要带理由出现在构建输出里，
   不能静默消失（`--with-evidence` 可导出完整清单）。
6. **零第三方依赖**：`tests/test_skill_doc.py` 会拒绝 `requests` / `yaml` / `pydantic` 之类的 import。
7. **交付物只有 `SKILL.md`**：子图、出处、查询脚本都是构建期内部产物，写在技能目录之外。

## 6. 可调常量速查

| 常量 | 位置 | 默认 | 含义 |
| --- | --- | --- | --- |
| `MAX_CHAIN` | `playbook.py` | 6 | `next_step` 链最多展开几步 |
| `SHARED_COVERAGE` | `template.py` | 0.8 | 公共采集门槛（`--shared-coverage` 可覆盖） |
| `MAX_REASONABLE_STEPS` | `lint.py` | 25 | 单条阅读路径上的步骤数上限（告警） |
| `MAX_CRITERION_REUSE` | `lint.py` | 2 | 一条判据最多被同场景几步共用 |
| `MAX_COMMAND_REPEATS` | `lint.py` | 2 | 同一命令在前置检查里出现几次算没合并 |
| `NEAR_DUPLICATE_RATIO` | `lint.py` | 0.8 | 根因名相似到什么程度算疑似重复 |
| `DESCRIPTION_NAMED_SCENARIOS` | `lint.py` | 6 | description 大约列得下几个场景名 |
| `MAX_DESCRIPTION` | `render.py` | 1024 | frontmatter description 字符上限 |
| `EVIDENCE_DEFAULT_LIMIT` | `describe.py` | 3 | 每个条目展示几条来源 |
| `MAX_DEPTH` | `condition.py` | 12 | 条件递归渲染的深度上限 |
