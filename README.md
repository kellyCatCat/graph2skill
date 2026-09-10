# subkg-to-skill

本仓库交付**一个 skill**：[`subkg-to-skill/`](subkg-to-skill/)。
它的作用是——把 JSON 格式的**故障诊断知识图谱子图**（`node.json` + `edge.json`）
编译成符合模板的**排障 skill**：一个故障入口（symptom）一份，
文档为「入参列表 → 前置检查 → 排查步骤 → 根因对照表」四章节。

```
node.json ─┐                                        <生成的 skill>/
           ├─▶ subkg-to-skill ─▶ 载入 → 校验 →      ├── SKILL.md          四章节模板
edge.json ─┘                     选子图 → 按故障     ├── reference/
                                 入口展开 → 模板自检  │   ├── evidence.md   出处与证据强度
                                                    │   └── subgraph.json 该故障的子图切片
                                                    └── scripts/kg_query.py
```

## 装这个 skill

```bash
cp -r subkg-to-skill .claude/skills/subkg-to-skill          # Claude Code（项目级）
cp -r subkg-to-skill ~/.claude/skills/subkg-to-skill        # Claude Code（全局）
cp -r subkg-to-skill .opencode/skill/subkg-to-skill         # opencode（项目级）
cp -r subkg-to-skill ~/.config/opencode/skill/subkg-to-skill
```

零依赖，Python 3.9+ 即可。装好后跟智能体说「把这个子图变成 skill」并给出图文件路径，
它会按 `SKILL.md` 的流程：摸底 → 列出故障入口并和你敲定英文技能名 → 生成 → 模板自检 → 给安装路径。

## 也可以直接当命令行用

```bash
S=subkg-to-skill/scripts/build_skill.py

python3 $S inspect examples/subgraph          # 规模与分布
python3 $S list    examples/subgraph          # 有哪些故障入口，各自建议的 slug
python3 $S build   examples/subgraph --entry symptom_7f1c \
        --name isis-neighbor-down --out out/isis-neighbor-down
python3 $S lint    out/isis-neighbor-down     # 模板符合性检查
```

批量（每个入口一个子目录，用 `{node_id: slug}` 映射指定英文名）：

```bash
python3 $S build-all examples/subgraph --out out/ --names names.json
```

参数全表见 [`subkg-to-skill/reference/cli.md`](subkg-to-skill/reference/cli.md)。

## 生成的 skill 长什么样

`SKILL.md` 严格按模板（[`reference/skill-template.md`](subkg-to-skill/reference/skill-template.md)）：

| 章节 | 内容 | 图谱来源 |
| --- | --- | --- |
| `# 入参列表` | 信息 / 是否必填 / 说明 | `required_slots` + 命令里的 `<参数>` |
| `# 前置检查` | 线性采集，每条给 CLI 命令与采集内容 | `symptom -diagnosed_by-> check` 及 `next_step` 链 |
| `# 排查步骤` | `## 步骤N：名称` + 步骤名称 / CLI 命令 / 跳转信息 / 根因定位 | 每条 `has_cause` 一步，判据来自 `confirms` / `supports` / `excludes` |
| `# 根因对照表` | 根因 / 现象 / 修复CLI和方法 / 复检命令 | `repaired_by` 的命令模板或文字说法 |

出处不塞进四章节，全部集中在 `reference/evidence.md`；
`scripts/kg_query.py` 用来回查子图切片：

```bash
python3 <skill>/scripts/kg_query.py stats
python3 <skill>/scripts/kg_query.py show observation_6b40      # 支持 id 前缀
python3 <skill>/scripts/kg_query.py expand symptom_7f1c --depth 2
```

## 关键取舍：不把候选知识写成结论

图谱里全是 `status=candidate` 的候选知识，`machine_checked` 不等于人工确认。生成器守住这些：

- **命令只能来自源数据**（`check` / `repair` / `escalation` 的 `command_templates`、`procedure`）；
  `observation` 是回显，不是命令来源。来源只给一句修复方向就照实写，来源为空就写“无直接修复CLI”。
- **不升级证据强度**：`supports` 判定的根因带“仅支持性证据，需人工确认”；`excludes` 只排除它指向的那个原因。
- **空值不是承诺**：`service_impact` / `rollback` / `preconditions` 为空写“来源未给出”。
- **参数名沿用来源写法**，只把 `{x}` / `[x]` 规整成 `<x>`，不翻译、不换词。
- **技能名不音译**：模板要求英文 slug，由调用方按语义给出。

完整对照表见 [`reference/evidence-rules.md`](subkg-to-skill/reference/evidence-rules.md)。
生成过程**不调用大模型**，内容由数据直接展开，可重复、可比对、可追溯。

`build` / `build-all` 会自动跑 `lint`：四章节顺序、步骤编号连续、跳转目标存在、
根因在对照表里逐字可查、CLI 参数都在入参列表内、占位符写法、接口名缩写——有 ERROR 就不算完成。

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `subkg-to-skill/SKILL.md` | skill 入口：使用流程、硬性约束 |
| `subkg-to-skill/reference/skill-template.md` | 产出 skill 的模板规范（硬性要求） |
| `subkg-to-skill/reference/graph-schema.md` | 输入字段字典 |
| `subkg-to-skill/reference/output-spec.md` | 图谱字段 → 四章节的映射 |
| `subkg-to-skill/reference/evidence-rules.md` | 措辞对照表 |
| `subkg-to-skill/reference/cli.md` | CLI 参数全表 |
| `subkg-to-skill/scripts/build_skill.py` | 生成器入口 |
| `subkg-to-skill/scripts/subkg2skill/` | 实现：载入 / 校验 / 选图 / 展开 / 模板渲染 / lint |
| `examples/subgraph/` | 可运行的最小示例：17 节点 / 26 边，六类节点与十一类边全覆盖 |
| `tests/` | pytest 用例（177 个） |

## 开发

```bash
pip install pytest
python -m pytest -q
```
