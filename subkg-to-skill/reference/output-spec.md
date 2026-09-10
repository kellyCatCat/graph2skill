# 产出 skill 的结构规范

`build_skill.py build` 写出的目录只有三样东西：`SKILL.md`、`reference/`、`scripts/`。
这一页说明每个文件为什么存在、内容从哪来，便于核对产物或手工微调。

## 目录

```
<skill>/
├── SKILL.md              入口：何时用、使用流程、证据纪律、输出模板、目录表
├── reference/
│   ├── index.md          症状 → 手册路由表
│   ├── index-001-*.md    （症状 >150 时的分片，仍在 reference/ 同层）
│   ├── fault-*.md        每个 symptom 一份排查手册
│   ├── reading-guide.md  本子图出现过的节点/边语义、条件状态、操作符、质量标记
│   ├── coverage.md       构建报告
│   └── subgraph.json     选中的节点与边 + meta（来源、生成时间、计数）
└── scripts/kg_query.py   零依赖查询脚本，按相对路径读 ../reference/subgraph.json
```

## 渐进式加载

入口小、细节按需，这是布局的唯一目的：

1. `SKILL.md`（几 KB）讲流程与纪律，不含具体故障内容；
2. `reference/index.md` 用“触发说法”把用户描述路由到**一份**手册；
3. 手册（每份约 2–10 KB）展开该症状的完整排查路径；
4. 手册没展开的部分（其他症状、完整来源列表、任意节点全字段）交给 `scripts/kg_query.py`；
5. `reference/subgraph.json` 是最终事实来源，全字段保留。

## 手册的固定段落

| 段落 | 来自哪些边/字段 |
| --- | --- |
| 头部 | `name` + `aliases` + `attrs.match_phrases`（触发说法）；`scope`；`diagnostic_contexts`；`status` / `review_status` / `semantic_review.human_reviewed` |
| 0. 现象与需补齐的信息 | `attrs.object_type` / `abnormal_behavior` / `expected_behavior` / `trigger_context` / `required_slots` |
| 1. 入口检查 | `symptom -diagnosed_by-> check`，并沿 `next_step` 展开检查链（最长 6 步，遇环即停） |
| 2. 候选原因对照表 | 每条 `has_cause` 一行：分类、确认观测、排除观测、修复动作、是否带条件 |
| 2.x 原因分支 | `cause` 属性 + 判据（`confirms` / `supports` / `excludes`）+ `diagnosed_by` 检查 + `repaired_by` 修复 + `refines` / `leads_to` / `refers_to` |
| 3. 转向 / 升级 | `refers_to`、`next_step` 指向的 escalation：转交对象与需收集的材料 |
| 4. 出处 | 症状的 `provenance` |

同一个检查在一份手册里只展开一次，重复出现时标注“已在前文展开”，避免同样的命令列三遍。

## 文件命名

- 手册：`fault-<症状名的 ASCII 片段>-<node_id 尾部 12 位>.md`，例如
  `fault-is-is-7f1c02aa93be.md`；症状名没有 ASCII 字符时退化为 `fault-<id 尾部>.md`。
  命名只依赖 `node_id`，同一子图重复构建结果稳定。
- 索引分片：先按诊断单元分组（`index-001-<单元>.md`）；诊断单元超过 40 个时
  退化为固定块分片（`index-001.md`、`index-002.md`…，每片 150 个症状）。
  无论哪种模式，`index.md` 本身都保持在几 KB。

## 重新生成

`--force` 覆盖时只清理**这次不再产生的** `fault-*.md` / `index-*.md`；
人工加进 `reference/` 的其他文件不会被删。

## 查询脚本

| 子命令 | 用途 |
| --- | --- |
| `stats` | 规模、类型分布、生成信息 |
| `search <词> [--type T] [--limit N]` | 按名称/别名/匹配短语/描述/章节标题检索 |
| `show <node_id> [--evidence N] [--json]` | 全字段 + 来源 + 出入边（支持 id 前缀） |
| `neighbors <node_id> [--edge-type T] [--direction out\|in\|both]` | 只看邻接 |
| `expand <node_id> [--depth N]` | 向前展开子树，并把判据边拉回来 |
| `path <a> <b> [--undirected]` | 两节点间的最短关系链 |
