# 另一种图格式：node.json + edge.json

从 PDF 抽取管线导出的图是**两个扁平数组**：`node.json` 放节点、`edge.json` 放关系，
和 `cograg.domain-decision-subgraph.v1` 是两套字段。graph2skill 两种都认，**可以混着传**：

```bash
python run_graph2skill.py stats  图目录/ 另一份.json
python run_graph2skill.py steps  图目录/ 另一份.json --fault 5 -d out/
python run_graph2skill.py merge  图目录/ --into isis -s skills/skillset.json --dry-run
```

## 怎么被识别

| 传入 | 处理 |
| --- | --- |
| 含 `node.json` / `edge.json` 的**目录** | 整个目录算**一份图**（不会被拆成两份） |
| 顶层是数组的 `.json` 文件 | 按第一条记录的字段判断是节点表还是关系表 |
| 顶层是对象的 `.json` | 按 `cograg.domain-decision-subgraph.v1` 处理 |

文件名认这些：`node.json` / `nodes.json` / `node_list.json`，`edge.json` / `edges.json` / `edge_list.json`。
两个文件也可以分别传，合并时会自动拼到一起。

## 字段映射

**节点**

| 源字段 | 映射到 | 说明 |
| --- | --- | --- |
| `node_id` | 节点 id | 必需，缺了这条记录会被跳过并记 warning |
| `node_type` | 类型 | 转大写：`cause`→CAUSE、`check`→CHECK、`symptom`→SYMPTOM、`solution`→SOLUTION… |
| `name` / `description` | 名称 / 描述 | PDF 换行会被接回去（见下） |
| `aliases` | `nameAliases` | |
| `attrs.command` / `commands` / `cli` | 命令 | **命令只从这里取** |
| `attrs.fault_mechanism` | 观察/现象 | |
| `diagnostic_contexts[0].section/title` | `section` / `sectionTitle` | 也用于公共章节匹配 |
| `provenance[]` | 出处 | `document`→来源文件、`block_id`→锚点、`quote`→原文摘录、`section + title + p页码`→定位 |
| `scope.product_family` / `vendor` | 图的 domain | 取第一个非空值 |
| 其余字段（`status`、`review_status`、`quality_flags`、`semantic_review`、`canonical_key`…） | 原样保留 | 渲染在「其他字段」里，不会丢 |

**关系**

| 源字段 | 映射到 |
| --- | --- |
| `edge_id` / `edge_type` | 关系 id / 类型（`diagnosed_by`→DIAGNOSED_BY） |
| `source` / `target` | 起点 / 终点（缺一个整条丢弃并记 warning） |
| `condition` | 分支判据 —— **会成为排查步骤里的跳转判据**，比节点上的 trigger 优先 |
| `evidence[]` | 出处（字段同节点的 `provenance`） |
| `diagnostic_context` | `section` / `sectionTitle` |
| 其余字段（`condition_status`、`rank`、`origin`、`quality_flags`…） | 原样保留 |

入口节点没有显式声明，按**入度为 0** 推断。

## 两个要点

**1. 证据里的 `quote` 不是命令来源。**
`quote` 是 PDF 原文摘录，`"display isis last-peer-\nchange"` 这种看着像命令，其实是版面截断的文本。
命令只从 `attrs.command` 这类明确字段取；另外，**检查项节点的名称本身就是一条 CLI 时**
（以 display/undo/reset/ping/tracert/system-view/interface 等开头）也算一条命令。
这条口径和模板规范里「回显列不是命令来源」是一致的。

**2. PDF 换行会被接回去。**

```
"display isis last-peer-\nchange"        → display isis last-peer-change
"IS-IS 1引入IS-IS 2路由\n时未配置路由策略。" → IS-IS 1引入IS-IS 2路由时未配置路由策略。
```

断词（行尾是 `-`）直接接上；中文两侧不补空格；英文之间补一个空格。

## 生成模板 skill 时

- **故障**：`symptom` / `fault` 类型且下面挂着 `cause` 的节点；边的类型不限（`caused_by`、`has_cause` 都行）。
- **步骤判据**：优先用边上的 `condition`，没有才退回节点的 `trigger`。
- **技能名**：这类 id 是哈希（`symptom_9a1b2c3d…`），会去掉哈希后缀、必要时冠上 domain，
  例如 `netengine40e-symptom-5`；想要好名字就用 `--name`，或在节点里加 `identifier` 字段。

## 混合两种格式时

domain 不一致会被如实记成一条冲突（不会悄悄挑一个），用 `--domain` 指定即可：

```bash
python run_graph2skill.py stats 图目录/ isis.json --domain IP网络
```

示例数据见 `examples/nodeedge/`（字段结构照着真实导出写的，含 PDF evidence）。
