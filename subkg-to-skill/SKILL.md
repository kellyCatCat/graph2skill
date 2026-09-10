---
name: subkg-to-skill
description: "把 JSON 格式的知识图谱子图（故障诊断图谱的 node.json + edge.json）编译成符合模板的排障 skill：一个故障入口一份，文档为「入参列表 / 前置检查 / 排查步骤 / 根因对照表」四章节，配 reference/ 出处与 scripts/ 查询脚本，Claude Code、opencode 拷进去就能加载。当用户说“把这个子图/知识图谱变成 skill”“根据图谱生成排查技能包”，或手里有 node.json、edge.json 想变成智能体能用的排障文档时使用。Turns a fault-diagnosis knowledge-graph subgraph into template-conformant agent skills."
---

# 子图 → skill 生成器

输入：故障诊断知识图谱导出的 `node.json` + `edge.json`（六类节点：symptom / cause / check /
observation / repair / escalation；十一类边：has_cause、diagnosed_by、observes、supports /
confirms / excludes、repaired_by、refines、refers_to、next_step、leads_to）。

输出：**一个故障场景一份 skill**——场景 = 一个 symptom × 一个诊断单元（章节号或案例 ID）。
知识图谱会把同一个症状在几十个章节、案例里的原因合并到一个节点上，不按诊断单元切分就会把
互不相干的故障塞进同一份文档。`SKILL.md` 严格按四章节模板写：

```
<skill>/
├── SKILL.md                # 入参列表 → 前置检查 → 排查步骤 → 根因对照表
├── reference/
│   ├── evidence.md         # 每条判据/命令/修复的出处、证据强度、未求值条件
│   └── subgraph.json       # 该故障的子图切片（全字段）
└── scripts/kg_query.py     # 零依赖查询脚本
```

模板细则见 [`reference/skill-template.md`](reference/skill-template.md)，**这是硬性要求**。
生成过程不调用大模型：内容由数据直接展开，可重复、可比对、可追溯。

## 使用流程（三段，不要跳段）

### 阶段一 · 数据摸底

先看清楚手里是什么，再谈生成。

```bash
python3 scripts/build_skill.py inspect <图文件或目录>     # 节点/边分布、诊断单元、厂商、质量标记、孤立节点
python3 scripts/build_skill.py validate <图文件或目录>    # 结构校验；有错误退出码为 1
```

要回答的问题：六类节点各多少、十一类边各多少、覆盖哪些诊断单元与来源、
有没有大量 `example_specific` 或 `ocr_only` 之类的质量标记、悬空边有多少。
悬空边和端点类型非法的边会被丢弃并记进产物；`--strict` 让这类问题直接中止。

文件形状不用纠结：目录（自动识别 `node*.json` / `edge*.json`）、两个数组文件、
`{"nodes": [...], "edges": [...]}` 整包对象、混合数组、`.jsonc`、`.jsonl` 都能读；
猜不准时用 `--nodes` / `--edges` 指定。

### 阶段二 · 语义判断与编排

这一段是**人和你一起做判断**，工具只提供证据。

```bash
python3 scripts/build_skill.py list <图>                    # 故障分组（默认已跨来源归并）
python3 scripts/build_skill.py list <图> --show-causes      # 每组的根因按来源列出 → 判断该不该拆
python3 scripts/build_skill.py list <图> --suggest-merge    # 名字不同但根因高度重叠 → 判断该不该合
```

要做的三类决策：

| 决策 | 依据 | 怎么落实 |
| --- | --- | --- |
| **合并**：同一故障被多来源各写一遍 | `list` 已自动归并同名；`--suggest-merge` 给名字不同但根因重叠的候选 | 多个 `--entry`，或把它们写进同一个场景 |
| **拆分**：一组里混了两类故障（如"中断"和"震荡"） | `--show-causes` 看根因是否分属两个技术域 | 拆成两个场景，各自 `--unit` / `--entry` |
| **剔除**：案例特定内容、没有判据也没有修复的原因 | 默认就剔除并记录；`plan` 会列出剔除清单 | 需要保留时才加 `--include-example-specific` / `--keep-undecidable` |

判断完把编排固化成场景清单，后面两段都用它：

```bash
python3 scripts/build_skill.py list <图> --export-scenarios scenarios.json
# 编辑 scenarios.json：填英文技能名 name、改中文场景名、按判断结果拆/并 entries 与 units、删掉不要的场景
```

### 阶段三 · 生成前规划

**先 plan 再 build。** `list` 数的是图上有什么，`plan` 数的是**文档里实际会有什么**——
剔除、按命令去重、按名称折叠根因都已经算进去了。

```bash
python3 scripts/build_skill.py plan <图> --scenarios scenarios.json
```

```
| 场景 | 步骤 | 根因 | 修复命令 | 复用的公共前置检查 |
| 场景A：IS-IS 邻居无法建立 | 4 | 4 | 4 | 步骤 1、步骤 2 |
| 场景B：协议邻居关系无法建立 | 3 | 3 | 0 | 步骤 1 |
| **合计** | **7** | **7** | **4** | 公共前置检查 2 条 |

还能再合并的地方（只是提示，合不合由你判断）：
  - 场景B 的 3 个根因全部也出现在 场景A，可考虑并入（合并后少一个场景）
  - 同一个根因在多处各排一遍：场景A 的「检查MTU不一致」；场景B 的「检查MTU不一致」
```

看三件事，不满意就回阶段二改清单再 plan：

1. **每个场景的步骤数**：超过 10 步多半还能拆；只有 1 步且修复命令为 0 的场景考虑并掉或不做。
2. **修复命令数为 0 的场景**：生成出来只能定位不能处置，要么先不做，要么交付时说清楚。
3. **合并提示**：根因被完全包含 → 并；同一根因在多个场景各排一遍 → 说明场景边界画错了。

规划满意了再生成：

```bash
python3 scripts/build_skill.py build <图> --scenarios scenarios.json --out out/isis-troubleshooting
python3 scripts/build_skill.py lint out/isis-troubleshooting
```

全部子命令与参数见 [`reference/cli.md`](reference/cli.md)。

单个故障一份（不需要多场景时）：

```bash
python3 scripts/build_skill.py build <图> --entry symptom_7f1c --unit 28.21.3 \
    --name isis-neighbor-down --out out/isis-neighbor-down
```

只给一个 `--entry` 时，若别的来源还有同名症状，命令会提示加 `--merge-same-name`。
批量：`build-all <图> --out out/ --names names.json`（每个故障一个子目录）。

**必须用脚本生成，不要照着模板手写文档。** 手写会漏掉命令去重、案例内容剔除、
跳转编号一致性这些机器保证的东西——这些恰恰是生成质量的关键。

### 交付

`build` / `build-all` 会自动跑模板检查。**有 ERROR 必须修到零再交付**；
WARNING（如"来源未给出修复命令"）如实转告用户，不要自己补命令消灭它。

回报时说清楚四件事：生成了哪些 skill（场景 + 英文名）、`plan` 的规模数字、
模板检查结果、以及安装路径（构建命令末尾会打印）：

```bash
cp -r <输出目录> .claude/skills/<slug>          # Claude Code（项目级）
cp -r <输出目录> ~/.claude/skills/<slug>        # Claude Code（全局）
cp -r <输出目录> .opencode/skill/<slug>         # opencode（项目级）
cp -r <输出目录> ~/.config/opencode/skill/<slug>
```

## 四种常见的烂输出，以及生成器怎么防

| 症状 | 成因 | 生成器的处理 |
| --- | --- | --- |
| **同一条命令出现几十次** | 图里多个 check 节点跑同一条命令 | 前置检查按命令合并成一条，采集内容取并集；排查步骤只写“复用前置检查步骤 N 回显”，不重复下发 |
| **步骤里出现 IP、设备名、拓扑** | 案例节点（`example_specific`）带着某次事故的地址与组网 | 默认整体剔除，并在 `reference/evidence.md` 里说明；确需保留时 `--include-example-specific`，且命令旁会标出案例字面量 |
| **几十个步骤、长度爆炸** | 一个症状合并了多个章节/案例的原因 | 按诊断单元切分（上面第 2、3 步）；`--max-steps` 可再设上限；超过 25 步 lint 会告警 |
| **场景杂糅（ISIS 里混进 MPLS、BGP）** | 跨单元的边被一并展开 | `--unit` 只保留该单元的关系；无判据又无修复的原因不进正文 |
| **同一故障被拆成几份薄 skill** | 手册、作战树、案例库各写一遍，节点不同名不同 | `list` 按故障归并，`--merge-same-name` 合并；同名根因折成一步，判据与修复取并集 |

跑完看一眼输出里的「N 个原因/检查未进入正文」和 lint 告警，把它们如实转告用户——
剔除了什么、为什么剔除，比假装“全都覆盖到了”有用。

## 硬性约束

生成器已经把下面这些写进产物；**你手工润色时也必须守住**：

- **命令只能来自源数据**：`check` / `repair` / `escalation` 的 `attrs.command_templates`
  与 `attrs.procedure`。`observation` 是回显，**不是命令来源**。来源只给了一句修复方向就照实写，
  不要补全成可执行的配置序列；来源为空就写“无直接修复CLI”。
- **不升级证据强度**：`supports` 判定的根因必须带“仅支持性证据，需人工确认”，不能写成确认。
- **`excludes` 只排除它指向的那个原因**，不能外推。
- **空值不是承诺**：`service_impact` / `rollback` / `preconditions` 为空写“来源未给出”。
- **参数名沿用来源写法**，只规整分隔符（`{interface-type}` → `<interface-type>`），不翻译、不换词；
  同一参数全篇同名，接口名用全称。
- **每条结论可回查**：出处放在 `reference/evidence.md`，四章节里不塞出处。

完整措辞对照见 [`reference/evidence-rules.md`](reference/evidence-rules.md)；
图谱字段含义见 [`reference/graph-schema.md`](reference/graph-schema.md)；
四章节与图谱字段的对应关系见 [`reference/output-spec.md`](reference/output-spec.md)。

## 本 skill 自身的更新

用户问「怎么更新这个生成器」时：仓库根目录有 `install.py`，`git pull` 后重跑
`python3 install.py`（或 `--target all` 同时更新 opencode）即整体替换，不需要逐个文件比对；
`python3 install.py --link` 装成软链后 `git pull` 即生效。仓库也带 `.claude-plugin/marketplace.json`，
可 `/plugin marketplace add kellyCatCat/graph2skill` 后用 `/plugin update` 管理。

## 不要做的事

- 不要音译或凭空生成英文技能名——让用户定，或按症状语义拟定后请用户确认。
- 不要为了“看起来完整”补写来源里没有的字段（预期效果、回退方法、命令参数值）。
- 不要合并同名节点：身份以 `node_id` 为准，同名节点可能范围不同。
- 不要在生成的 skill 里替用户执行命令；变更类操作要用户确认。
- 不要用 `--data none` 配查询脚本：脚本会没有数据可读。
- 不要为了“覆盖全”而用 `--all-units` 把多个诊断单元合成一份：那正是长度爆炸和场景杂糅的来源。
- 不要手工把案例里的 IP、设备名改成看起来通用的值——那是编造；要么剔除该条目，要么留着并标注。
