# graph2skill

把一份或多份**领域决策子图**（`cograg.domain-decision-subgraph.v1`）编译成一份可直接使用的 **Skill**：
`SKILL.md` + 分场景排查手册 + 节点/命令/来源索引 + 原图资产 + 查询脚本。

```
多个 graph.json ──▶ 加载(容错) ──▶ 合并去重 ──▶ 校验+决策树展开 ──▶ 渲染 ──▶ skill/
```

## 安装

```bash
pip install -e .          # 需要 Python 3.9+，核心功能零依赖
pip install -e '.[yaml]'  # 可选：支持 YAML 格式的图文件
```

也可以不安装直接用：`PYTHONPATH=src python -m graph2skill ...`

## 快速开始

```bash
# 用仓库自带的两份示例图生成技能
graph2skill build examples/isis/ -o out/isis-skill --graph-id ISIS_ALL

# 只看图长什么样，不生成文件
graph2skill stats examples/isis/
graph2skill validate examples/isis/ --json
graph2skill inspect examples/isis/ --node-id cause:isis:isis-75700908-1
```

生成结果：

```
out/isis-skill/
├── SKILL.md                       # 技能入口：适用场景 + 使用流程 + 场景路由表
├── references/
│   ├── overview.md                # 图谱总览：规模、类型分布、路由表
│   ├── playbooks/<入口>.md         # 每个入口一份排查手册（示意图 + 决策路径 + 节点详情）
│   ├── node-catalog.md            # 全量节点索引，含未挂接到决策树的节点全文
│   ├── commands.md                # 命令速查（命令 → 所属节点 → 手册位置）
│   ├── sources.md                 # 按来源文档聚合的出处索引
│   └── conversion-report.md       # 转换报告：合并情况、冲突、校验问题
├── assets/graph.json              # 合并归一后的图，供程序化查询
└── scripts/graph_query.py         # 零依赖查询脚本（search / node / tree / entries / commands）
```

排查手册里每个节点都带 `出处`（来源文件 + 定位 + 原文摘录），因此技能可以在回答时给出可追溯的引用。

## 命令行

| 命令 | 作用 |
| --- | --- |
| `graph2skill build <输入...> -o <目录>` | 生成技能包 |
| `graph2skill validate <输入...>` | 只做结构校验，有 error 时退出码为 1 |
| `graph2skill stats <输入...>` | 打印规模、类型分布与各手册入口 |
| `graph2skill inspect <输入...> --node-id X` | 查看单个节点及其上下游（也可传子串做搜索） |

输入可以是文件，也可以是目录（默认递归查找 `.json/.jsonc/.yaml/.yml`）。常用参数：

| 参数 | 说明 |
| --- | --- |
| `--name` / `--description` / `--title` | 覆盖 `SKILL.md` 的 front matter 与标题（`name` 会自动规范成小写连字符形式） |
| `--lang zh\|en` | 生成中文或英文文案，默认中文 |
| `--graph-id` / `--domain` | 覆盖合并后的图 ID 与领域名 |
| `--max-depth N` | 决策树展开的最大深度，默认 12 |
| `--respect-declared-scope` | 只走 `decisionTrees[].nodeIds/relationIds` 声明的范围（默认走全图可达子图，因为声明往往不完整） |
| `--no-inferred-entries` | 只用声明的 `entryNodeIds` / `decisionTrees` 作为入口，不再推断根节点 |
| `--no-diagram` / `--no-script` / `--no-graph-asset` / `--no-report` | 裁剪对应产物 |
| `--strict` | 图存在结构性错误时拒绝生成 |
| `--force` | 覆盖非空输出目录 |

## 作为库使用

```python
from graph2skill import build_bundle, build_skill, SkillOptions

# 一步到位
result = build_skill(["graphs/"], "out/skill", SkillOptions(lang="zh"))
print(result.skill_name, result.relative_files)

# 或者只做分析，自己决定怎么用
bundle = build_bundle(["graphs/a.json", "graphs/b.json"])
for tree in bundle.trees:
    print(tree.title, tree.size, tree.max_depth)
for issue in bundle.errors():
    print(issue.format())
```

`render_files(bundle)` 返回「相对路径 → 文件内容」的字典，方便接入其他发布流程。

## 输入格式

完整字段说明见 [docs/schema.md](docs/schema.md)。要点：

- 只有 `nodes[].id` 是必需的，其余字段全部可选；未知字段不会丢失，会渲染在「其他字段」里。
- `type` 缺失时回退到 `data.nodeType`；`source` 会自动并入 `sources`。
- `parameterExamples` 支持 `[{"command": ...}]`、字符串数组两种写法。
- 加载器容忍**尾逗号、`//` 与 `/* */` 注释、BOM**（示例里的 `examples/isis/isis_cot_cases.jsonc` 就是这种脏数据），装了 PyYAML 还能读 YAML。

多图合并规则：

- 节点按 `id` 合并；列表取并集、字典递归合并、标量以先出现的文件为准，名称冲突会记入 `nameAliases` 并写进转换报告。
- 关系按 `(type, from, to)` 语义去重，避免不同管线的同一条边在手册里出现两次。
- `entryNodeIds`、`sources` 取并集；`domain`、`schemaVersion` 不一致会记为冲突（可用 `--domain` 强制指定）。

## 校验规则

| code | 级别 | 含义 |
| --- | --- | --- |
| `empty-graph` | error | 图里没有节点 |
| `dangling-relation` | error | 关系端点指向不存在的节点 |
| `missing-entry` / `missing-tree-entry` | error | 入口或决策树根节点不存在 |
| `tree-unknown-node` / `tree-unknown-relation` | warning | 决策树声明了图中没有的节点/关系 |
| `no-entry-points` | warning | 既没有 `entryNodeIds` 也没有 `decisionTrees`，入口靠推断 |
| `isolated-node` | warning | 节点没有任何关系 |
| `cycle` / `depth-limit` | warning | 遍历时遇到环 / 触达深度上限（分支会在此截断并标注） |
| `unknown-type` | info | 节点类型无法识别，按原样渲染 |

## 开发

```bash
pytest -q          # 覆盖容错加载、合并冲突、决策树遍历、渲染、CLI，以及产物的链接完整性
```

代码结构：

| 模块 | 职责 |
| --- | --- |
| `model.py` | 容错数据模型（`Graph` / `Node` / `Relation` / `DecisionTree` / `Provenance`） |
| `loader.py` | 文件发现与容错解析 |
| `merge.py` | 多图合并与冲突记录 |
| `analyze.py` | 索引、结构校验、决策树展开、统计 |
| `ontology.py` | 节点/关系类型词表（中英标签、排序、角色）——新增领域词汇改这里 |
| `render.py` | 全部 Markdown 渲染 |
| `bundle.py` / `skill.py` | 选项与产物落盘 |
| `cli.py` | 命令行入口 |

扩展提示：新增节点类型只需在 `ontology.py` 的 `_NODE_TYPES` 里加一行（`order` 决定它在决策路径里的先后，`role` 决定语义分组）；未登记的类型也能正常渲染，只是用原始类型名。

## License

MIT
