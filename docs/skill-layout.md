# 生成物结构与智能体的使用路径

`subkg2skill build` 产出的是一个**框架无关的技能目录**。这一页说明每个文件为什么存在、
智能体应该按什么顺序读，以及数据规模变大时布局怎么变。

## 目录

```
<skill>/
├── SKILL.md                     # 入口：何时用、怎么用、证据纪律、输出模板、目录
├── INSTALL.md                   # Claude Code / opencode / 其他框架的安装位置
├── references/
│   ├── index.md                 # 症状 → 手册路由表；症状 >150 时变成分片目录
│   ├── index/*.md               # （分片模式）按诊断单元或固定块切分的路由表
│   ├── playbooks/*.md           # 每个 symptom 一份排查手册
│   ├── reading-guide.md         # 本子图出现过的节点/边语义、条件状态、操作符、质量标记
│   └── coverage.md              # 构建报告：分布、载入丢弃项、未进入手册的节点
├── data/subgraph.json           # 选中的节点与边 + meta（来源、生成时间、计数）
└── scripts/kg_query.py          # 零依赖查询脚本
```

## 渐进式加载

入口小、细节按需——这是布局的唯一目的：

1. `SKILL.md`（几 KB）说明流程与纪律，不含具体故障内容；
2. `references/index.md` 用“触发说法”把用户描述路由到**一份**手册；
3. 手册（每份约 2–10 KB）展开该症状的完整排查路径；
4. 手册没展开的部分（其他症状的原因、完整来源列表、任意节点的全字段）交给 `scripts/kg_query.py`；
5. `data/subgraph.json` 是最终事实来源，全字段保留，便于回查与二次加工。

全量图（1,352 个症状）下 `index.md` 会自动分片：先按诊断单元分组，
诊断单元过多（>40）时退化为固定块分片（`index/part-001.md`…），入口文件始终保持在几 KB。

## 手册的固定结构

| 段落 | 来自哪些边/字段 |
| --- | --- |
| 头部 | `name` + `aliases` + `attrs.match_phrases`；`scope`；`diagnostic_contexts`；`status`/`review_status`/`semantic_review.human_reviewed` |
| 0. 现象与需补齐的信息 | `attrs.object_type` / `abnormal_behavior` / `expected_behavior` / `trigger_context` / `required_slots` |
| 1. 入口检查 | `symptom -diagnosed_by-> check`，并沿 `next_step` 展开检查链（最长 6 步，遇环即停） |
| 2. 候选原因对照表 | 每条 `has_cause` 一行，汇总确认/排除观测与修复动作 |
| 2.x 原因分支 | `cause` 属性 + 判据（`confirms`/`supports`/`excludes`）+ `diagnosed_by` 检查 + `repaired_by` 修复 + `refines`/`leads_to`/`refers_to` |
| 3. 转向/升级 | `refers_to`、`next_step` 指向的 escalation |
| 4. 出处 | 症状的 `provenance` |

同一个检查在一份手册里只展开一次，重复出现时标注“已在前文展开”，避免同样的命令列三遍。

## 忠实性约定

手册里的每一处措辞都对应一条数据事实：

| 数据情况 | 手册写法 |
| --- | --- |
| `condition` 非空 | `条件（<condition_status 的中文解释>，未求值）：…` |
| 只有 `supports`，没有 `confirms` | 显式提示“不要据此宣布根因已确认” |
| `excludes` | 说明只排除它指向的那个原因 |
| `rank` 有值 | 标注“仅排序提示，不是概率或置信度” |
| `example_specific: true` | 标注“案例特定，勿直接套用到其他设备” |
| `service_impact` / `rollback` / `expected_effect` 为空 | 写“来源未给出”，并说明空值不等于无影响/无需回退 |
| `preconditions: []` | 写“空数组不证明无需前置条件” |
| `quality_flags` | 逐条给出中文解释 |
| `command_templates` | 标注“参数需绑定现场上下文后才可执行”，并列出 `parameters` |

## 查询脚本

`scripts/kg_query.py` 只依赖标准库，数据路径相对脚本解析，可从任意目录调用：

| 子命令 | 用途 |
| --- | --- |
| `stats` | 规模、类型分布、生成信息 |
| `search <词> [--type T] [--limit N]` | 按名称/别名/匹配短语/描述/章节标题检索 |
| `show <node_id> [--evidence N] [--json]` | 全字段 + 来源 + 出入边（支持 id 前缀） |
| `neighbors <node_id> [--edge-type T] [--direction out\|in\|both]` | 只看邻接 |
| `expand <node_id> [--depth N]` | 向前展开子树，并把判据边拉回来 |
| `path <a> <b> [--undirected]` | 两节点间的最短关系链 |

## 重新生成

同一份子图重复构建结果稳定：手册文件名由 `node_id` 派生，分片按序号命名。
用 `--force` 覆盖时会清理上一次遗留、这次不再产生的手册文件（只删 `references/playbooks/*.md`）。
