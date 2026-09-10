---
name: subkg-to-skill
description: "把 JSON 格式的知识图谱子图（故障诊断图谱的 node.json + edge.json）编译成一份可直接使用的排障 skill：SKILL.md + reference/ 排查手册 + scripts/ 查询脚本，Claude Code、opencode 等框架拷进去就能加载。当用户说“把这个子图/知识图谱变成 skill”“根据图谱生成排查技能包/排障手册”，或手里有 node.json、edge.json 想变成智能体能用的东西时使用。Turns a fault-diagnosis knowledge-graph subgraph into a ready-to-load agent skill."
---

# 子图 → skill 生成器

输入：故障诊断知识图谱导出的 `node.json` + `edge.json`（六类节点：symptom / cause / check /
observation / repair / escalation；十一类边：has_cause、diagnosed_by、observes、supports /
confirms / excludes、repaired_by、refines、refers_to、next_step、leads_to）。

输出：一个框架无关的技能目录，**每个症状一份排查手册**，判据、命令、出处逐条可回查。

```
<输出目录>/
├── SKILL.md              入口：何时用、使用流程、证据纪律、输出模板
├── reference/
│   ├── index.md          症状 → 手册路由表（症状 >150 时自动分片成 index-001.md…）
│   ├── fault-*.md        每个症状一份：入口检查 → 候选原因 → 判据 → 修复 → 转交
│   ├── reading-guide.md  本子图出现过的节点/边语义、条件状态、操作符、质量标记
│   ├── coverage.md       构建报告：分布、载入丢弃项、未覆盖节点
│   └── subgraph.json     选中的节点与边（全字段，可回查出处）
└── scripts/kg_query.py   零依赖查询脚本（search / show / neighbors / expand / path / stats）
```

生成过程**不调用大模型**：全部内容由数据直接展开，因此可重复、可比对、可追溯。

## 使用流程

### 1. 确认输入

问清楚（或自己确认）三件事：图文件在哪、要不要只取一部分、技能叫什么名字。
技能名必须能规范成 `^[a-z0-9-]+$`（中文名会报错，让用户给一个英文 slug，或按内容建议一个）。

文件形状不用纠结：目录（自动识别 `node*.json` / `edge*.json`）、两个数组文件、
`{"nodes": [...], "edges": [...]}` 整包对象、混合数组、`.jsonc`、`.jsonl` 都能读；
猜不准时用 `--nodes` / `--edges` 明确指定。

### 2. 先摸底，别直接生成

```bash
python3 scripts/build_skill.py inspect <图文件或目录>
```

看清楚：节点/关系分布、诊断单元（章节）、厂商范围、质量标记、能生成多少份手册、有没有孤立节点。
结构可疑时再跑 `validate`（有错误退出码为 1）：

```bash
python3 scripts/build_skill.py validate <图文件或目录>
```

悬空边、端点类型非法的边会被丢弃并记进产物的 `reference/coverage.md`；
`--strict` 让这类问题直接中止构建。

### 3. 需要的话先选子图

不给选择参数就用全部输入。给了就先选出**种子节点**，再沿诊断方向向前闭包
（并把 `supports` / `confirms` / `excludes` / `observes` 这些反向证据拉回来）：

| 用户想要 | 参数 |
| --- | --- |
| 只针对某个症状 | `--root symptom_7f1c --depth 3` |
| 某章节 / 诊断单元 | `--section 28.21`（前缀匹配） |
| 某厂商 / 某产品线 | `--vendor Huawei` |
| 与某关键词相关 | `--query "光模块"` |
| 全图 | 不加参数 |

`--root` 接受 `node_id`、id 前缀或名称关键词。

### 4. 生成

```bash
python3 scripts/build_skill.py build <图文件或目录> \
    --out <输出目录> --name <skill-slug>
```

先加 `--dry-run` 看会写出哪些文件、多大，确认后再真写。常用开关见
[`reference/cli.md`](reference/cli.md)（`--data slim`、`--max-playbooks`、`--evidence`、
`--allowed-tools`、`--force` 等）。

### 5. 自检并交付

生成后**必须**做这几件事，再回报用户：

1. 读一眼 `reference/coverage.md`：把载入丢弃项（悬空边、端点类型不合法）和未覆盖节点数量如实告诉用户，
   不要只报“已生成”。
2. 抽查一份 `reference/fault-*.md`：确认判据、命令模板、出处都在。
3. 跑一次脚本确认数据可用：`python3 <输出目录>/scripts/kg_query.py stats`。
4. 给出安装路径（构建命令末尾也会打印）：

```bash
cp -r <输出目录> .claude/skills/<skill-slug>          # Claude Code（项目级）
cp -r <输出目录> ~/.claude/skills/<skill-slug>        # Claude Code（全局）
cp -r <输出目录> .opencode/skill/<skill-slug>         # opencode（项目级）
cp -r <输出目录> ~/.config/opencode/skill/<skill-slug>
```

## 硬性约束

生成器已经把下面这些写进产物；**你在手工润色产物时也必须守住**，改坏了就失去可信度：

- `condition` 一律标“未求值”，附 `condition_status` 的中文解释——不要替现场下判断。
- `supports` 不能写成“确认”；只有 `supports` 的原因必须保留“不要宣布根因”的提示。
- `excludes` 只排除它指向的那个原因，不能外推成“排除全部”。
- `service_impact` / `rollback` / `preconditions` 为空写“来源未给出”，不能写成“无影响/无需回退”。
- `command_templates` 保留待绑定参数，不改写成可直接下发的命令。
- 每条结论带 `node_id` 和来源（文档 / 页码 / 章节 / 引文）。

完整措辞对照见 [`reference/evidence-rules.md`](reference/evidence-rules.md)。

## 不要做的事

- 不要为了“看起来完整”补写来源里没有的字段（预期效果、回退方法、命令参数值）。
- 不要合并同名节点：身份以 `node_id` 为准，同名节点可能范围不同。
- 不要在生成的手册里替用户执行命令；那是使用该技能时的事，且变更类操作要用户确认。
- 不要把 `--data none` 和查询脚本一起用：脚本会没有数据可读。

## 参考文件

| 文件 | 内容 |
| --- | --- |
| [`reference/graph-schema.md`](reference/graph-schema.md) | 输入字段字典：节点/边类型、端点规则、`attrs`、`scope`、`condition`、来源定位、质量标记 |
| [`reference/output-spec.md`](reference/output-spec.md) | 产出 skill 的结构规范：每份手册的段落、文件命名、索引分片规则 |
| [`reference/evidence-rules.md`](reference/evidence-rules.md) | 数据情况 → 措辞的对照表 |
| [`reference/cli.md`](reference/cli.md) | `build_skill.py` 全部子命令与参数 |
| `scripts/build_skill.py` | 生成器入口（只依赖 Python 3.9+ 标准库） |
