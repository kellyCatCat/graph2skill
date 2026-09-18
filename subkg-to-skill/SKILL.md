---
name: subkg-to-skill
description: "把 JSON 格式的知识图谱子图（故障诊断图谱的 node.json + edge.json）编译成符合模板的排障 skill：一个故障入口一份，交付物就是一个 SKILL.md，文档为「入参列表 / 前置检查 / 排查步骤 / 根因对照表」四章节，生成后逐条回查原图、删除没有来源的内容，Claude Code、opencode 拷进去就能加载。当用户说“把这个子图/知识图谱变成 skill”“根据图谱生成排查技能包”，或手里有 node.json、edge.json 想变成智能体能用的排障文档时使用。Turns a fault-diagnosis knowledge-graph subgraph into template-conformant agent skills."
---

# 子图 → skill 生成器

输入：故障诊断知识图谱导出的 `node.json` + `edge.json`（六类节点：symptom / cause / check /
observation / repair / escalation；十一类边：has_cause、diagnosed_by、observes、supports /
confirms / excludes、repaired_by、refines、refers_to、next_step、leads_to）。

输出：**一个故障场景一份 skill**——场景 = 一个 symptom × 一个诊断单元（章节号或案例 ID）。
知识图谱会把同一个症状在几十个章节、案例里的原因合并到一个节点上，不按诊断单元切分就会把
互不相干的故障塞进同一份文档。**交付物只有 `<skill>/SKILL.md` 一个文件**，
严格按四章节模板写：入参列表 → 前置检查 → 排查步骤 → 根因对照表。

子图切片、出处清单、查询脚本是**构建期的内部产物，不对外暴露**：默认不生成，要人工核对时用
`--with-evidence` / `--with-subgraph` / `--with-script` 导出到 `<输出目录>.internal/`，不要拷走。

模板细则见 [`reference/skill-template.md`](reference/skill-template.md)，**这是硬性要求**。
生成过程不调用大模型：内容由数据直接展开，可重复、可比对、可追溯。

## 使用流程（四段，不要跳段）

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
python3 scripts/build_skill.py list <图> --suggest-merge    # 名字不同但根因重叠 → 按修复动作判断该不该合
```

要做的四类决策：

| 决策 | 依据 | 怎么落实 |
| --- | --- | --- |
| **定边界**：这些场景该做成几份 skill | ① 有没有共同入口采集 ② 根因是否重叠 ③ 规模到顶就停（见下） | 一份清单一份 skill；装不下就再导一份清单 |
| **合并**：同一故障被多来源各写一遍 | `list` 已自动归并同名；`--suggest-merge` 给候选并比对双方的修复动作 | 多个 `--entry`，或把它们写进同一个场景 |
| **拆分**：一组里混了两类故障（如"中断"和"震荡"） | `--show-causes` 看根因是否分属两个技术域 | 拆成两个场景，各自 `--unit` / `--entry` |
| **剔除（自动）**：案例特定内容、没有判据也没有修复的原因 | 默认就剔除并记录；`plan` 会列出剔除清单 | 需要保留时才加 `--include-example-specific` / `--keep-undecidable` |
| **剔除（人工）**：同一单元里混进来的跨领域内容（查 ISIS 却混进 MPLS/BGP/组播） | `list --show-causes` 看根因清单，挑出不属于本场景的 | 场景清单的 `exclude`（只作用于该场景）或 `--exclude`（全局），写 `node_id` 或名称关键词 |

**聚合边界：一份 skill 装 5–7 个场景、30+ 根因、3–7 条公共前置、约 500 行**（`plan` 逐项报）。
一个上百症状的子图不是一份上百章节的文档，而是**十几份这样的 skill**。按顺序判：

1. **有没有共同入口采集**（能不能同一份）：`plan` 里公共前置塌到 0–1 条，就是这些场景没有
   共同入口——一份 skill 的形态是"先公共采集、再按跳转表分流"，没有共同入口就别硬凑。
2. **根因是否重叠**（该不该同一份）：`--suggest-merge` 的重叠度；根因互不相干的两组放一起，
   跳转表只是个目录，没有分流价值。
3. **规模到顶就停**：场景数 / 根因覆盖 / 文档规模任一超了，就另起一份清单。

主题名（"设备硬件"）是前两步的**结果**，不是判据：先验按主题分会把共用 `display fan` 的
高温与风扇故障拆开。**也别用"主症状进正文、其余列成附表"绕过边界**——附表里的症状没判据、
没根因对照，读者走不下去；超出的场景名字进不了 description，agent 不会为它选中这份 skill，
正文有、检索不到等于没覆盖（`lint` 会告警）。

**关于"无关的点和边"**：跨诊断单元的靠 `--unit`、案例特定的默认剔除、跨故障的靠边语义
（`refers_to` / `leads_to` 指向另一个故障入口，选图默认不跨越）。剩下**同一单元里的跨领域内容**
机器判不了，就是这一步的人工活：`exclude` 把该节点及其所有边整条剔掉（原因带着它的检查、
观测、修复一起走），在 `plan` 与构建输出里逐条记账；写错的关键词直接报错，不会静默漏掉。

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

`plan` 末尾还会打一张**交付统计**：步骤:根因是否 1:1、判据密度、命令复用率、复检覆盖率、
必填参数个数、场景规模是否均衡——健康值与不达标的含义见
[`reference/cli.md`](reference/cli.md#交付统计)。超出健康值的项 `build` 会再提醒一次，
**要么回到对应阶段再做一轮，要么交付时说明**，不要默默放过。

看四件事，不满意就回阶段二改清单再 plan：

1. **每个场景的步骤数**：超过 10 步多半还能拆；只有 1 步且修复命令为 0 的场景考虑并掉或不做。
2. **修复命令数为 0 的场景**：生成出来只能定位不能处置，要么先不做，要么交付时说清楚。
3. **合并提示**：根因被完全包含 → 并；同一根因在多个场景各排一遍 → 说明场景边界画错了。
4. **公共前置的规模**：塌到 0–1 条或居高不下都说明边界画错了，回阶段二（`--shared-coverage` 调门槛；单故障 skill 里同一条门槛按排查步骤算，只有一个步骤读、又不决定分流的采集还给那个步骤）。
5. **剔除清单**：逐条看"未进入正文的条目"——自动剔除的对不对、还有没有跨领域内容漏网。
   漏网的加进 `exclude` 再 plan 一次；`plan --exclude "MPLS"` 可以先试效果再写进清单。

规划满意了再生成：

```bash
python3 scripts/build_skill.py build <图> --scenarios scenarios.json --out out/isis-troubleshooting
python3 scripts/build_skill.py lint   out/isis-troubleshooting            # 形状
python3 scripts/build_skill.py verify out/isis-troubleshooting --graph <图>  # 出处
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

### 阶段四 · 后校验：每条内容都回查原图，删掉幻觉

`lint` 查**形状**，`verify` 查**出处**：命令、根因、判据、入参逐条回原图查，查不到就是 ERROR
（自由文本报 WARNING）——命令只认 `check` / `repair` / `escalation` 的 `command_templates`，
根因只认 `cause` 的名字，判据只认 `observation` 的表达式/字段/取值，入参只认 `required_slots`
与正文命令里的 `<参数>`；口径见
[`reference/evidence-rules.md`](reference/evidence-rules.md#后校验什么算有来源)。

`build` 自动跑一遍；**要手动跑的是文档被人或智能体动过之后**——润色、补一句"看起来更完整"的
说明，幻觉就这样进来。**ERROR 只有两种处理：删掉，或改回来源原样的写法**，不要凭经验"修正"、
不要换个说法绕过；改完重跑两道。`--graph` 要给**原图**，给生成物不叫回查。

### 交付

**`lint` 与 `verify` 的 ERROR 必须都修到零**；WARNING（如"来源未给出修复命令"）如实转告用户，
不要自己补命令消灭它。回报时说清楚六件事：生成了哪些 skill（场景 + 英文名）、`plan` 的规模数字、
模板检查与后校验结果、**交付统计里超出健康值的项**、以及安装路径：

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
| **步骤里出现 IP、设备名、拓扑** | 案例节点（`example_specific`）带着某次事故的地址与组网 | 默认整体剔除，构建输出逐条列出剔除原因；确需保留时 `--include-example-specific`，且命令旁会标出案例字面量 |
| **几十个步骤、长度爆炸** | 一个症状合并了多个章节/案例的原因 | 按诊断单元切分；`--max-steps` 再设上限；超过 25 步 lint 告警 |
| **场景杂糅（ISIS 里混进 MPLS、BGP）** | 跨单元的边被一并展开 | `--unit` 只保留该单元的关系；无判据又无修复的原因不进正文 |
| **同一故障被拆成几份薄 skill** | 手册、作战树、案例库各写一遍，节点不同名不同 | `list` 按故障归并，`--merge-same-name` 合并；同名根因折成一步，判据与修复取并集 |
| **润色时补出图里没有的命令/根因** | 人或智能体事后编辑文档 | `verify` 逐条回查原图，没来源的报 ERROR |

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
- **每条结论可回查**：四章节里不塞出处，但每条内容都要能在原图里找到来源——改完用 `verify` 验，
  不要凭印象判断；出处清单需要时用 `--with-evidence` 导出，供内部核对。

完整措辞对照见 [`reference/evidence-rules.md`](reference/evidence-rules.md)；
图谱字段含义见 [`reference/graph-schema.md`](reference/graph-schema.md)；
四章节与图谱字段的对应关系见 [`reference/output-spec.md`](reference/output-spec.md)。

## 不要做的事

- 不要音译或凭空生成英文技能名——让用户定，或按症状语义拟定后请用户确认。
- 不要为了“看起来完整”补写来源里没有的字段（预期效果、回退方法、命令参数值）。
- 不要合并同名节点：身份以 `node_id` 为准，同名节点可能范围不同。
- 不要在生成的 skill 里替用户执行命令；变更类操作要用户确认。
- 不要把 `.internal/` 里的子图、出处、查询脚本跟着 skill 交付：子图不对外暴露。
- 不要跳过后校验：过不了 `verify` 的内容就是幻觉，删掉或改回原文，不要换个说法绕过。
- 不要为了“覆盖全”而用 `--all-units` 把多个诊断单元合成一份：那正是长度爆炸和场景杂糅的来源。
- 不要手工把案例里的 IP、设备名改成看起来通用的值——那是编造；要么剔除该条目，要么留着并标注。
