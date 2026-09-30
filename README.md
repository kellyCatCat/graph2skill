# subkg-to-skill

本仓库交付**一个 skill**：[`subkg-to-skill/`](subkg-to-skill/)。它把 JSON 格式的**故障诊断知识图谱子图**
（`node.json` + `edge.json`）编译成符合模板的**排障 skill**：一个子图一份，图里每个故障（一个 symptom ×
一个诊断单元）是它的一个场景，文档为「入参列表 → 前置检查 → 排查步骤 → 根因对照表」四章节。

```
node.json ─┐                                                    <生成的 skill>/
           ├─▶ inspect → list → plan → build ─▶ check ─▶   ├── SKILL.md    入参/前置检查/场景跳转
edge.json ─┘   摸底      编排   规划   生成     形状+出处    └── reference/  每个场景的步骤与根因
```

生成过程不调用大模型，内容由数据直接展开：可重复、可比对、可追溯。生成后逐条回查原图，
没有来源的内容不许留。零依赖，Python 3.9+。

## 安装

```bash
python3 install.py                    # 装到 Claude Code 用户目录（更新就是重跑）
python3 install.py --target all       # Claude Code + opencode 都装
python3 install.py --project          # 装到当前项目（.claude/skills、.opencode/skill）
python3 install.py --link             # 软链到本仓库，改完即生效（开发用）
python3 install.py --uninstall
```

也可以作为 Claude Code 插件安装（仓库根目录就是插件市场）：

```
/plugin marketplace add kellyCatCat/graph2skill
/plugin install subkg-to-skill@graph2skill
```

装好后跟智能体说「把这个子图变成 skill」并给出图文件路径，它会按 `SKILL.md` 的流程走完。

## 直接当命令行用

```bash
S=subkg-to-skill/scripts/build_skill.py

python3 $S inspect examples/subgraph                                   # 摸底 + 结构校验
python3 $S list    examples/subgraph --export-scenarios scenarios.json # 编排：改名、拆并、剔除
python3 $S plan    examples/subgraph --scenarios scenarios.json        # 生成前看实际规模
python3 $S build   examples/subgraph --scenarios scenarios.json --out out/isis
python3 $S check   out/isis --graph examples/subgraph                  # 模板检查 + 后校验
```

只做一个故障：`build <图> --entry symptom_7f1c --unit 28.21.3 --name isis-neighbor-down --out …`；
每个故障各一份：`build <图> --each --names names.json --out out/`。

## 文档

| 看什么 | 在哪 |
| --- | --- |
| 使用流程、交付要求、硬性约束 | [`subkg-to-skill/SKILL.md`](subkg-to-skill/SKILL.md) |
| 编排怎么判断、聚合边界、`plan` 怎么读、防住了哪些烂输出 | [`reference/workflow.md`](subkg-to-skill/reference/workflow.md) |
| 命令与参数全表、交付统计 | [`reference/cli.md`](subkg-to-skill/reference/cli.md) |
| 产出 skill 的模板规范 | [`reference/skill-template.md`](subkg-to-skill/reference/skill-template.md) |
| 图谱字段 → 四章节的映射 | [`reference/output-spec.md`](subkg-to-skill/reference/output-spec.md) |
| 措辞对照、后校验口径 | [`reference/evidence-rules.md`](subkg-to-skill/reference/evidence-rules.md) |
| 输入字段字典 | [`reference/graph-schema.md`](subkg-to-skill/reference/graph-schema.md) |

## 开发

```bash
pip install pytest
python -m pytest -q
```

`examples/subgraph/` 是可运行的最小示例；`tests/data/messy/`（跨单元、命令重复、案例内容）与
`tests/data/multisource/`（同一故障多来源各写一遍）是回归用的"脏"子图。
