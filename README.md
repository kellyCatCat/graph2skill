# subkg-to-skill

本仓库交付**一个 skill**：[`subkg-to-skill/`](subkg-to-skill/)。
它的作用是——把 JSON 格式的**故障诊断知识图谱子图**（`node.json` + `edge.json`）
编译成符合模板的**排障 skill**：**一个故障场景一份**（场景 = 一个 symptom × 一个诊断单元），
文档为「入参列表 → 前置检查 → 排查步骤 → 根因对照表」四章节。

```
node.json ─┐                                        <生成的 skill>/
           ├─▶ subkg-to-skill ─▶ 载入 → 校验 →      ├── SKILL.md          四章节模板
edge.json ─┘                     选子图 → 按故障     ├── reference/
                                 入口展开 → 模板自检  │   ├── evidence.md   出处与证据强度
                                                    │   └── subgraph.json 该故障的子图切片
                                                    └── scripts/kg_query.py
```

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

```bash
S=subkg-to-skill/scripts/build_skill.py

python3 $S inspect examples/subgraph          # 规模与分布
python3 $S list    examples/subgraph          # 有哪些故障场景（症状 × 诊断单元）
python3 $S build   examples/subgraph --entry symptom_7f1c --unit 28.21.3 \
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

一份 skill 也可以覆盖多个故障场景：公共前置检查 + `## 场景跳转表` + 每个场景
`### 场景X：xxx`（步骤在场景内从 1 计数）+ 按场景分节的根因对照表。

```bash
python3 $S list  kg/ --export-scenarios scenarios.json   # 导出分组，改名字/删场景
python3 $S build kg/ --scenarios scenarios.json --out out/isis-troubleshooting
```

出处不塞进四章节，全部集中在 `reference/evidence.md`；
`scripts/kg_query.py` 用来回查子图切片：

```bash
python3 <skill>/scripts/kg_query.py stats
python3 <skill>/scripts/kg_query.py show observation_6b40      # 支持 id 前缀
python3 <skill>/scripts/kg_query.py expand symptom_7f1c --depth 2
```

## 怎么防住四种烂输出

真图喂进来最容易出的四个问题，生成器各有对策：

| 症状 | 处理 |
| --- | --- |
| 同一条命令重复几十次 | 前置检查按命令合并，步骤写“复用前置检查步骤 N 回显” |
| 步骤里出现 IP、设备名、拓扑 | `example_specific` 条目默认剔除并记录；保留时标出案例字面量 |
| 几十个步骤、长度爆炸 | 按诊断单元切分场景；`--max-steps` 兜底；>25 步 lint 告警 |
| 场景杂糅（ISIS 里混进 MPLS/BGP） | `--unit` 只保留该单元的关系；无判据又无修复的原因不进正文 |
| 同一故障被拆成几份薄 skill | 按故障跨来源归并（手册 + 作战树 + 案例库），同名根因折成一步、判据与修复取并集 |
| 名字不同实为一事 | `list --suggest-merge` 按根因重叠度找出候选并给出合并命令——只建议，合不合由人判断 |

剔除了什么、为什么剔除，都写在生成物的 `reference/evidence.md` 里，不会静默丢失。

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
| `subkg-to-skill/scripts/subkg2skill/` | 实现：载入 / 校验 / 选图 / 展开 / 模板渲染 / lint |
| `examples/subgraph/` | 可运行的最小示例：17 节点 / 26 边，六类节点与十一类边全覆盖 |
| `tests/data/messy/` | 回归用的“脏”子图：跨三个诊断单元、命令重复、案例特定内容、无判据原因 |
| `tests/data/multisource/` | 同一故障被手册 / 作战树 / 案例库各写一遍的子图，用于验证跨来源合并 |
| `tests/` | pytest 用例（267 个） |

## 开发

```bash
pip install pytest
python -m pytest -q
```
