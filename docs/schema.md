# 输入图格式

graph2skill 面向 `cograg.domain-decision-subgraph.v1`，但对字段缺失、类型不一致、脏 JSON 都做了兜底。
下面标注「必需」的字段只有一个：`nodes[].id`。

## 顶层

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `schemaVersion` | string | 缺省填 `cograg.domain-decision-subgraph.v1`；多图不一致时记为冲突 |
| `graphId` | string | 缺省用文件名；`--graph-id` 可覆盖 |
| `domain` | string | 领域名，用于技能名与文案；`--domain` 可覆盖 |
| `sources` | string[] | 数据来源标识，合并时取并集 |
| `entryNodeIds` | string[] | 入口节点，决定生成哪些排查手册 |
| `nodes` | object[] | 节点列表 |
| `relations` | object[] | 有向边列表 |
| `decisionTrees` | object[] | 已声明的子图；用于确定入口，`nodeIds/relationIds` 默认不用于裁剪 |

## 节点

```jsonc
{
  "id": "cause:isis:isis-75700908-1",   // 必需，全图唯一
  "type": "CAUSE",                       // 缺失时回退 data.nodeType，再缺失记为 UNKNOWN
  "name": "IS-IS 与 LDP 互相依赖导致会话无法 Up",
  "source": "mp_ne40",                   // 会自动并入 sources
  "sources": ["mp_ne40"],
  "data": {
    "description": "……",                 // name 缺失时用作标题
    "executionClass": "EXECUTABLE",
    "scenarioId": "scenario:isis:…",
    "sourceVersion": "mp-ne40-v8",
    "observations": ["现象一", "现象二"],
    "parameterExamples": [ { "command": "display isis peer" } ],  // 也接受纯字符串数组
    "provenance": [
      { "anchor": "sha256:…", "excerpt": "原文片段", "locator": "章节路径", "sourcePath": "文档.md" }
    ]
  }
}
```

`data` 里的其他字段不会被丢弃，会以「其他字段」的形式渲染到节点详情中。

已登记的节点类型（见 `ontology.py`，未登记的类型按原名渲染）：

`SCENARIO` `THEME` `TOPIC` `FAULT` `SYMPTOM` `PHENOMENON` `CONDITION` `CHECK` `DIAGNOSIS` `COMMAND`
`STEP` `CAUSE` `ROOT_CAUSE` `RELATION` `ACTION` `SOLUTION` `FIX` `CONCLUSION` `CASE` `EVIDENCE`
`NOTE` `PRINCIPLE` `RISK` `DEVICE` `CONFIG`

决策路径里的分支顺序 = 类型 `order`（前置条件 → 检查 → 原因 → 处理动作 → 案例）+ 源图中关系的声明顺序。

## 关系

```jsonc
{
  "id": "rel_001",                 // 缺省会用 (graphId, type, from, to, 序号) 合成
  "type": "HAS_RELATION",          // 缺失时回退 data.edgeType
  "from": "cause:isis:…",          // 必需，否则整条边被丢弃并记录 warning
  "to": "relation:isis:…",         // 必需
  "sources": ["mp_ne40"],
  "data": {
    "edgeType": "HAS_RELATION",
    "condition": "两端 Type 不一致",   // 可选：分支条件，会渲染在决策路径上
    "provenance": [ … ]
  }
}
```

已登记的边类型：`HAS_RELATION` `RELATED_TO` `HAS_THEME` `HAS_SCENARIO` `HAS_CAUSE` `CAUSED_BY`
`HAS_CHECK` `HAS_STEP` `NEXT` `HAS_ACTION` `HAS_SOLUTION` `HAS_CASE` `HAS_EVIDENCE` `BELONGS_TO`
`DERIVED_FROM` `REFERENCES`。

## 决策树

```jsonc
{
  "treeId": "tree:scenario:isis:IS-IS-1",
  "entryNodeId": "scenario:isis:IS-IS-1",
  "nodeIds": ["…"],
  "relationIds": ["…"],
  "sources": ["mp_ne40"]
}
```

`nodeIds` / `relationIds` 在实际导出中经常只覆盖一部分子图，所以默认只用 `entryNodeId` 决定入口，
展开时走全图可达子图；需要严格按声明裁剪时加 `--respect-declared-scope`。

## 入口的确定顺序

1. 顶层 `entryNodeIds`（存在于图中的）；
2. `decisionTrees[].entryNodeId`；
3. 推断：入度为 0 且类型属于场景/主题类，或有出边的根节点（`--no-inferred-entries` 可关闭）。

每个入口生成一份 `references/playbooks/<slug>.md`。slug 由节点 id 转 ASCII 得到；纯中文 id 会退化成
`playbook-<hash>` 形式，保证文件名稳定且可移植。

## 遍历行为

- **环**：沿路径再次遇到同一节点时截断，标注「同一节点已在上文展开」，并记 `cycle` warning。
- **菱形**：同一节点在树中多次出现时只展开一次，其余位置指向首次出现的锚点。
- **深度**：超过 `--max-depth`（默认 12）截断并记 `depth-limit` warning。
- **未覆盖节点**：不在任何入口可达范围内的节点，会在 `references/node-catalog.md` 中完整渲染，不会丢失。
