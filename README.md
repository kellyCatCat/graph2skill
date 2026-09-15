# subkg-to-skill

本仓库交付**一个 skill**：[`subkg-to-skill/`](subkg-to-skill/)。
它的作用是——把 JSON 格式的**故障诊断知识图谱子图**（`node.json` + `edge.json`）
编译成符合模板的**排障 skill**：**一个故障场景一份**（场景 = 一个 symptom × 一个诊断单元），
文档为「入参列表 → 前置检查 → 排查步骤 → 根因对照表」四章节。

```
node.json ─┐                                           <生成的 skill>/
           ├─▶ subkg-to-skill ─▶ 载入 → 校验 → 选子图  └── SKILL.md   四章节模板
edge.json ─┘                     → 按故障入口展开
                                 → 模板自检 lint（形状）
                                 → 后校验 verify（出处）：逐条回查原图，没来源的不许留
```

**交付物只有 `SKILL.md` 一个文件**：子图不对外暴露。出处清单、子图切片、查询脚本是
构建期的内部产物，按需导出到 `<输出目录>.internal/`，不随 skill 交付。

## 装这个 skill（三选一）

### A. 一条命令安装/更新（推荐，Claude Code + opencode 通用）

```bash
python3 install.py                    # 装到 Claude Code 用户目录
python3 install.py --target all       # Claude Code + opencode 都装
python3 install.py --project          # 装到当前项目（.claude/skills、.opencode/skill）
python3 install.py --uninstall
```

**更新就是重跑同一条命令**（`git pull` 之后），目标目录整体替换——不用逐个文件比对，
仓库里删掉的文件在目标端也会消失。加 `--dry-run` 先看会动哪些目录。

### B. 软链，改完立即生效（自己开发时最省事）

```bash
python3 install.py --link             # ~/.claude/skills/subkg-to-skill → 本仓库
```

之后 `git pull` 就是更新，不用再跑安装；会话里 `/reload-plugins` 让改动即时生效。

### C. 作为 Claude Code 插件安装（团队分发、带版本）

仓库根目录已带 `.claude-plugin/marketplace.json`，本身就是一个插件市场：

```
/plugin marketplace add kellyCatCat/graph2skill
/plugin install subkg-to-skill@graph2skill
```

以后更新：

```
/plugin marketplace update graph2skill
/plugin update subkg-to-skill@graph2skill
```

本地调试插件不用装：`claude --plugin-dir ./subkg-to-skill`。

<details>
<summary>手动复制（等价于 A，但要自己处理更新）</summary>

```bash
cp -r subkg-to-skill ~/.claude/skills/subkg-to-skill
cp -r subkg-to-skill ~/.config/opencode/skill/subkg-to-skill
```
</details>

零依赖，Python 3.9+ 即可。装好后跟智能体说「把这个子图变成 skill」并给出图文件路径，
它会按 `SKILL.md` 的流程：摸底 → 列出故障入口并和你敲定英文技能名 → 生成 → 模板自检 → 给安装路径。

## 也可以直接当命令行用

三段流程：**摸底 → 判断与编排 → 规划后生成**。

```bash
S=subkg-to-skill/scripts/build_skill.py

# 一 · 摸底
python3 $S inspect examples/subgraph
python3 $S validate examples/subgraph

# 二 · 判断与编排（合并 / 拆分 / 剔除），结果固化成场景清单
python3 $S list examples/subgraph --show-causes      # 该不该拆
python3 $S list examples/subgraph --suggest-merge    # 该不该合
python3 $S list examples/subgraph --export-scenarios scenarios.json

# 三 · 生成前规划 → 生成
python3 $S plan  examples/subgraph --scenarios scenarios.json
python3 $S build examples/subgraph --scenarios scenarios.json --out out/isis

# 四 · 交付前两道检查：形状 + 出处
python3 $S lint   out/isis
python3 $S verify out/isis --graph examples/subgraph
```

单个故障一份：

```bash
python3 $S list    examples/subgraph          # 有哪些故障场景（症状 × 诊断单元）
python3 $S build   examples/subgraph --entry symptom_7f1c --unit 28.21.3 \
        --name isis-neighbor-down --out out/isis-neighbor-down
python3 $S lint    out/isis-neighbor-down     # 模板符合性检查
python3 $S verify  out/isis-neighbor-down --graph examples/subgraph   # 后校验：回查原图
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

一份 skill 也可以覆盖多个故障场景：公共前置检查 + `## 场景跳转表` + 每个场景
`### 场景X：xxx`（步骤在场景内从 1 计数）+ 按场景分节的根因对照表。

```bash
python3 $S list  kg/ --export-scenarios scenarios.json   # 导出分组，改名字/删场景
python3 $S build kg/ --scenarios scenarios.json --out out/isis-troubleshooting
```

出处不塞进四章节。需要核对时把内部产物导出到 `<输出目录>.internal/`：

```bash
python3 $S build kg/ --entry symptom_7f1c --unit 28.21.3 --name isis-neighbor-down \
        --out out/isis-neighbor-down --with-evidence --with-subgraph --with-script

python3 out/isis-neighbor-down.internal/kg_query.py show observation_6b40   # 支持 id 前缀
python3 out/isis-neighbor-down.internal/kg_query.py expand symptom_7f1c --depth 2
```

这些文件写在 skill 目录**旁边**，`cp -r out/isis-neighbor-down ~/.claude/skills/` 带不走它们。

## 怎么防住四种烂输出

真图喂进来最容易出的问题，生成器各有对策：

| 症状 | 处理 |
| --- | --- |
| 同一条命令重复几十次 | 前置检查按命令合并，步骤写“复用前置检查步骤 N 回显”；缩写与全写（`display current-config config bgp` / `…configuration configuration…`）认作同一条，保留全写、另一种写法登记为“来源另有写法” |
| 步骤里出现 IP、设备名、拓扑 | `example_specific` 条目默认剔除并记录；保留时标出案例字面量 |
| 几十个步骤、长度爆炸 | 按诊断单元切分场景；`--max-steps` 兜底；>25 步 lint 告警 |
| 场景杂糅（ISIS 里混进 MPLS/BGP） | `--unit` 按诊断单元收窄；选图不跨 `refers_to`/`leads_to`（那是另一个故障）；剩下的用 `--exclude` 人工剔除，逐条记账 |
| 同一故障被拆成几份薄 skill | 按故障跨来源归并（手册 + 作战树 + 案例库），同名根因折成一步、判据与修复取并集 |
| 名字不同实为一事 | `list --suggest-merge` 按根因重叠找出候选，再逐条比对双方的**修复动作**——修复相同才该合，只建议，由人判断 |
| 回显、表格、正文句子被当成命令或根因 | `Peer : <ip>`、`1.3.1 4 100 0 Connect 0`、「但由于…存在Hash问题」按形状识别并剔除，理由记进 `evidence.md` |
| 判据没有区分力 | 一条判据被同场景三步以上共用（入场条件的重述）、正反判据跳同一处 —— lint 报 ERROR |
| 参数填不出来 | 来源示意图的设备编号（`device B`）报 ERROR；没被任何命令引用的入参告警；`slot 3` 这类示例取值提示按现场替换 |
| 公共前置里混进少数场景才用的命令 | 只有分流前必须跑的留在公共前置并标出适用场景：场景覆盖率 ≥ 80%（`--shared-coverage` 可调）、或决定跳哪个场景、或采集期就判根因；其余下沉成各读者场景的「本场景采集」 |
| 优化有没有到位说不清 | `plan` / `build` 给交付统计：步骤:根因、判据密度、命令复用率、复检覆盖率等对照健康值 |
| 润色时补出图里没有的命令/根因 | `verify` 后校验逐条回查原图，没来源的报 ERROR，必须删掉或改回原文 |

剔除了什么、为什么剔除，构建输出里逐条列出（`--with-evidence` 导出完整清单），不会静默丢失。

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

## 两道检查：形状 + 出处

`build` / `build-all` 两道都会自动跑，有 ERROR 就不算完成。

**`lint` 查形状**：四章节顺序、步骤编号连续、跳转目标存在、根因在对照表里逐字可查、
CLI 参数都在入参列表内、占位符写法、接口名缩写。

**`verify` 查出处（后校验）**：把成品 `SKILL.md` 拆开，命令、根因、判据、入参逐条回原图查——
命令只认 `check` / `repair` / `escalation` 的 `command_templates`（`observation` 是回显，不是命令来源），
根因只认 `cause` 的名字，判据只认 `observation` 的表达式/字段/取值，
入参只认 `required_slots` 与正文命令里的 `<参数>`。查不到的就是幻觉，**删掉或改回来源原样的写法**；
修复说法一类自由文本查不到原文时报 WARNING，逐条看。

生成器不调模型，产物天然有来源——所以这一步真正防的是**文档被人或智能体改过之后**：
润色、合并、补一句“看起来更完整”的说明。手工动过就必须重跑：

```bash
python3 $S verify out/isis-neighbor-down --graph examples/subgraph
```

## 仓库结构

| 路径 | 内容 |
| --- | --- |
| `install.py` | 一条命令安装/更新到 Claude Code / opencode |
| `.claude-plugin/marketplace.json` | 插件市场清单，供 `/plugin marketplace add` 使用 |
| `subkg-to-skill/.claude-plugin/plugin.json` | 插件清单（单技能插件，`SKILL.md` 在插件根） |
| `subkg-to-skill/SKILL.md` | skill 入口：使用流程、硬性约束 |
| `subkg-to-skill/reference/skill-template.md` | 产出 skill 的模板规范（硬性要求） |
| `subkg-to-skill/reference/graph-schema.md` | 输入字段字典 |
| `subkg-to-skill/reference/output-spec.md` | 图谱字段 → 四章节的映射 |
| `subkg-to-skill/reference/evidence-rules.md` | 措辞对照表 |
| `subkg-to-skill/reference/cli.md` | CLI 参数全表 |
| `subkg-to-skill/scripts/build_skill.py` | 生成器入口 |
| `subkg-to-skill/scripts/subkg2skill/` | 实现：载入 / 校验 / 选图 / 展开 / 编排 / 规划 / 模板渲染 / lint / verify |
| `examples/subgraph/` | 可运行的最小示例：17 节点 / 26 边，六类节点与十一类边全覆盖 |
| `tests/data/messy/` | 回归用的“脏”子图：跨三个诊断单元、命令重复、案例特定内容、无判据原因 |
| `tests/data/multisource/` | 同一故障被手册 / 作战树 / 案例库各写一遍的子图，用于验证跨来源合并 |
| `tests/` | pytest 用例（349 个） |

## 开发

```bash
pip install pytest
python -m pytest -q
```
