# subkg2skill

把 **JSON 格式的故障诊断知识图谱子图**（`node.json` + `edge.json`）编译成一份**可直接使用的智能体技能包**——
Claude Code、opencode，或任何会读 `SKILL.md` 的框架都能用。

```
node.json ─┐
           ├─▶ 载入(容错) ─▶ 结构校验 ─▶ 子图选择 ─▶ 按症状展开排查手册 ─▶ 技能目录
edge.json ─┘                （丢弃悬空边）  （起点/深度/章节）   （检查→观测→原因→修复）
```

一句话说明它的取舍：**图谱里是候选知识，不是结论**。生成的手册把 `condition` 标成“未求值”、
把命令标成“待绑定参数的模板”、把 `machine_checked` 和人工复核分开写，
让智能体照着排查而不是照着下结论。

## 安装

零依赖，Python 3.9+：

```bash
pip install -e .          # 提供 subkg2skill 命令
pip install -e '.[dev]'   # 附带 pytest
```

装不了包也能跑（离线 / 代理需要鉴权时）：

```bash
python run_subkg2skill.py --version
PYTHONPATH=src python -m subkg2skill --version   # 等价写法
```

## 快速开始

```bash
subkg2skill build examples/subgraph --out out/ipran --name ipran-fault-diagnosis
```

```
out/ipran/
├── SKILL.md                      # 入口文档：何时用、怎么用、证据纪律、输出模板
├── INSTALL.md                    # 各框架的安装位置
├── references/
│   ├── index.md                  # 症状 → 手册路由表（子图大时自动分片）
│   ├── playbooks/*.md            # 每个症状一份排查手册
│   ├── reading-guide.md          # 节点/边语义、条件与操作符、质量标记怎么读
│   └── coverage.md               # 构建报告：分布、丢弃项、未覆盖节点
├── data/subgraph.json            # 选中的节点与边（全字段，可回查出处）
└── scripts/kg_query.py           # 零依赖查询脚本
```

先看会生成什么，再决定要不要写盘：

```bash
subkg2skill build examples/subgraph --out out/ipran --dry-run
```

## 装进你的框架

```bash
cp -r out/ipran .claude/skills/ipran-fault-diagnosis        # Claude Code（项目级）
cp -r out/ipran ~/.claude/skills/ipran-fault-diagnosis      # Claude Code（全局）
cp -r out/ipran .opencode/skill/ipran-fault-diagnosis       # opencode（项目级）
cp -r out/ipran ~/.config/opencode/skill/ipran-fault-diagnosis
```

`SKILL.md` 只有 YAML frontmatter（`name` / `description`，可选 `allowed-tools`）+ Markdown 正文，
没有任何框架私有字段；其他智能体直接把它拼进系统提示、并允许读 `references/` 即可。
生成的每份技能都附 `INSTALL.md` 重复说明这些路径。

## 排查手册长什么样

每个 `symptom` 一份，结构固定：

| 段落 | 内容 |
| --- | --- |
| 头部 | 触发说法（`name` + `aliases` + `match_phrases`）、适用范围、诊断单元、知识状态 |
| 0. 现象与需补齐的信息 | `required_slots`、异常/期望行为、触发场景 |
| 1. 入口检查 | `diagnosed_by` / `next_step` 的检查链：目的、步骤、命令模板、待绑定参数、执行影响 |
| 2. 候选原因对照表 | 每个 `has_cause` 一行：分类、确认观测、排除观测、修复动作、是否带条件 |
| 2.x 原因分支 | 故障机制 + 判据（`confirms`/`supports`/`excludes`）+ 定位检查 + 修复动作 + 相关条目 |
| 3. 转向/升级 | `refers_to`、`next_step` 到 escalation：转交对象与需收集的材料 |
| 4. 出处 | 文档、物理页/印刷页、章节、原文引文 |

判据强弱、条件状态、`rank` 的含义都随行标注，例如
`条件（条件已结构化，仍需绑定对象/字段/采样数据后判断，未求值）：互联接口.MTU 不等于 对端MTU`。

## 查询脚本

手册是展开好的路径，脚本用来回查手册没展开的部分：

```bash
python3 out/ipran/scripts/kg_query.py stats
python3 out/ipran/scripts/kg_query.py search "区域地址" --type cause
python3 out/ipran/scripts/kg_query.py show observation_6b40        # 支持 id 前缀
python3 out/ipran/scripts/kg_query.py neighbors symptom_7f1c --edge-type has_cause
python3 out/ipran/scripts/kg_query.py expand symptom_7f1c --depth 2
python3 out/ipran/scripts/kg_query.py path symptom_7f1c repair_2c9d
```

## 命令

| 命令 | 用途 |
| --- | --- |
| `build` | 生成技能目录 |
| `inspect` | 看子图规模、类型分布、章节、质量标记、能生成多少份手册 |
| `validate` | 只做结构校验；有错误时退出码为 1 |

### 输入

位置参数可以是文件或目录：目录里 `node*.json` / `edge*.json` 自动识别，
单个文件按记录形状拆分（带 `edge_type` 或 `source`+`target` 的算边）。
也接受 `{"nodes": [...], "edges": [...]}` 的整包对象、`.jsonc`（注释/尾逗号）与 `.jsonl`。
形状不好猜时用 `--nodes` / `--edges` 明确指定。

### 子图选择

不给任何选择参数就用全部输入；给了就先选出**种子节点**，再沿诊断方向向前闭包
（并把 `supports`/`confirms`/`excludes`/`observes` 这些反向证据拉回来）：

```bash
subkg2skill build node.json edge.json --out out/one --root symptom_7f1c --depth 3
subkg2skill build node.json edge.json --out out/isis --section 28.21 --vendor Huawei
subkg2skill build node.json edge.json --out out/opt  --query "光模块" --node-type symptom
```

`--root` 接受 `node_id`、id 前缀或名称关键词。

### 其他常用参数

| 参数 | 说明 |
| --- | --- |
| `--name` / `--title` / `--description` | 技能名与 frontmatter（技能名会规范成 `^[a-z0-9-]+$`） |
| `--allowed-tools` | 写入 frontmatter 的 `allowed-tools` |
| `--max-playbooks N` | 只生成前 N 份手册（按“真入口”优先排序） |
| `--evidence N` | 每个条目展示几条来源，默认 3 |
| `--data full\|slim\|none` | 随包数据详细程度；`slim` 去掉 `canonical_key`、`semantic_review` 等并截断证据 |
| `--no-script` | 不生成查询脚本 |
| `--strict` | 校验告警也当错误，构建直接失败 |
| `--force` | 覆盖已有目录（同时清理上一次遗留的手册） |

全量 17,392 节点 / 17,088 边的图约 5 秒构建完；症状超过 150 个时 `index.md` 自动分片，
入口文件始终保持在几 KB，智能体不必整份读入。

## 这个工具不做什么

- **不判定条件成立**：`condition` 原样保留并标注 `condition_status`，一律写成“未求值”。
- **不升级证据强度**：`supports` 不会被写成“确认”；只有 `supports` 的原因会显式提示不要宣布根因。
- **不补全缺失字段**：`service_impact` / `rollback` / `preconditions` 为空就写“来源未给出”，
  并说明空值不等于“无影响 / 无需回退 / 无前置条件”。
- **不改写命令**：`command_templates` 原样输出，标明待绑定参数与 `execution_policy`。
- **不合并同名节点**：身份以 `node_id` 为准；同名节点可能范围不同。
- **不调用大模型**：全部内容由数据直接展开，逐条可回查 `provenance` / `evidence`。

## 输入格式

节点与边的字段字典见 [`docs/schema.md`](docs/schema.md)：六类节点、十一类边及其允许的端点组合、
`attrs` 业务字段、`scope`、`condition` 递归结构、来源定位字段与质量标记。
校验只保证结构（ID 唯一、端点存在、端点类型合法），不保证关系语义正确。

## 开发

```bash
pip install -e '.[dev]'
python -m pytest -q
```

`examples/subgraph/` 是一份可运行的最小示例（17 节点 / 26 边，六类节点与十一类边全覆盖）。
