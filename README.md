# subkg-to-skill

本仓库交付**一个 skill**：[`subkg-to-skill/`](subkg-to-skill/)。
它的作用是——把 JSON 格式的**故障诊断知识图谱子图**（`node.json` + `edge.json`）
编译成另一份可直接使用的**排障 skill**。

```
node.json ─┐                                          <生成的 skill>/
           ├─▶ subkg-to-skill ─▶ 载入 → 校验 → 选子图 ─▶ ├── SKILL.md
edge.json ─┘                     → 按症状展开排查手册     ├── reference/
                                                        │   ├── index.md
                                                        │   ├── fault-*.md
                                                        │   ├── reading-guide.md
                                                        │   ├── coverage.md
                                                        │   └── subgraph.json
                                                        └── scripts/kg_query.py
```

## 装这个 skill

```bash
cp -r subkg-to-skill .claude/skills/subkg-to-skill          # Claude Code（项目级）
cp -r subkg-to-skill ~/.claude/skills/subkg-to-skill        # Claude Code（全局）
cp -r subkg-to-skill .opencode/skill/subkg-to-skill         # opencode（项目级）
cp -r subkg-to-skill ~/.config/opencode/skill/subkg-to-skill
```

零依赖，Python 3.9+ 即可；`SKILL.md` 只有标准 YAML frontmatter（`name` / `description`），
没有框架私有字段，其他智能体直接把它拼进系统提示、并允许读 `reference/` 也能用。

装好之后，跟智能体说「把这个子图变成 skill」并给出图文件路径即可；
它会按 `SKILL.md` 的流程先摸底、再（按需）选子图、然后生成并自检。

## 也可以直接当命令行用

```bash
python3 subkg-to-skill/scripts/build_skill.py inspect examples/subgraph
python3 subkg-to-skill/scripts/build_skill.py build examples/subgraph \
    --out out/ipran --name ipran-fault-diagnosis
python3 out/ipran/scripts/kg_query.py search "区域地址"
```

参数全表见 [`subkg-to-skill/reference/cli.md`](subkg-to-skill/reference/cli.md)。

## 生成的 skill 长什么样

每个 `symptom` 一份手册，段落固定：触发说法与适用范围 → 需补齐的信息槽位 →
入口检查（目的、步骤、命令模板、待绑定参数）→ 候选原因对照表 →
每个原因的判据（`confirms` / `supports` / `excludes`）、定位检查、修复动作、相关条目 →
转交升级 → 出处（文档、物理页/印刷页、章节、原文引文）。

配套的 `scripts/kg_query.py` 用来回查手册没展开的部分：

```bash
python3 <skill>/scripts/kg_query.py stats
python3 <skill>/scripts/kg_query.py search "区域地址" --type cause
python3 <skill>/scripts/kg_query.py show observation_6b40      # 支持 id 前缀
python3 <skill>/scripts/kg_query.py expand symptom_7f1c --depth 2
python3 <skill>/scripts/kg_query.py path symptom_7f1c repair_2c9d
```

## 关键取舍：不把候选知识写成结论

图谱里全是 `status=candidate` 的候选知识，`machine_checked` 不等于人工确认。
生成器把这一点贯彻到措辞里：`condition` 一律标“未求值”并附解析状态；
只有 `supports` 的原因会显式提示“不要宣布根因”；`excludes` 只排除它指向的那个原因；
`service_impact` / `rollback` / `preconditions` 为空写“来源未给出”而不是“无影响 / 无需回退”；
`command_templates` 保留待绑定参数；`example_specific=true` 标注“勿推广”；
每条结论带 `node_id` + 文档 / 页码 / 章节 / 引文。

完整对照表见 [`subkg-to-skill/reference/evidence-rules.md`](subkg-to-skill/reference/evidence-rules.md)。
生成过程**不调用大模型**，内容由数据直接展开，可重复、可比对、可追溯。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `subkg-to-skill/SKILL.md` | skill 入口：使用流程、硬性约束 |
| `subkg-to-skill/reference/` | 输入字段字典、产出结构规范、措辞对照表、CLI 参数 |
| `subkg-to-skill/scripts/build_skill.py` | 生成器入口 |
| `subkg-to-skill/scripts/subkg2skill/` | 生成器实现（载入 / 校验 / 选图 / 展开 / 渲染） |
| `examples/subgraph/` | 可运行的最小示例：17 节点 / 26 边，六类节点与十一类边全覆盖 |
| `tests/` | pytest 用例（124 个） |

## 开发

```bash
pip install pytest
python -m pytest -q
```

规模参考：全量 17,392 节点 / 17,088 边约 5 秒构建完，产出 1,352 份手册，
症状超过 150 个时索引自动分片，入口文件始终是几 KB。
