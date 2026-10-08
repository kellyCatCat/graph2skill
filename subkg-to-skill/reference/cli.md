# `build_skill.py` 参数参考

只依赖 Python 3.9+ 标准库，从技能目录直接运行，无需安装：

```bash
python3 scripts/build_skill.py <子命令> [输入...] [参数]
```

按四段流程使用：

| 阶段 | 子命令 |
| --- | --- |
| 一 · 数据摸底 | `inspect` |
| 二 · 语义判断与编排 | `list`（`--show-causes` / `--suggest-merge` / `--export-scenarios`） |
| 三 · 生成前规划与生成 | `plan` → `build`（`--each` 每个故障一份） |
| 四 · 交付前检查 | `check`：模板检查（形状）+ 后校验（出处） |

| 子命令 | 用途 | 退出码 |
| --- | --- | --- |
| `inspect` | 看规模、类型分布、章节、质量标记，并做结构校验 | 0 无错误；1 有校验错误；2 输入错误 |
| `list` | 列出故障（默认跨来源归并）、规模、触发说法、建议 slug 与生成命令 | 0 / 2 |
| `plan` | 生成前预览：每个场景实际会有多少步骤/根因/修复命令，以及还能合并什么。不写盘 | 0 / 2 |
| `build` | 把**一个子图**编成一份 skill（`--entry` 只做其中一个故障；`--each` 每个故障各自一份） | 0 成功；1 模板检查/后校验有错误或 `--strict` 下有校验错误；2 输入/参数错误 |
| `check` | 已生成的 skill：模板检查，给了 `--graph` 再逐条回查原图 | 0 通过；1 有 ERROR；2 输入错误 |

**默认粒度是一个子图一份 skill**：`build <子图> --name <slug> --out <目录>` 把这张图里的
每个故障编成一个场景，公共前置共用，步骤按场景写进 `reference/`。一个故障 = 跨来源归并后的
一个症状 × 一个诊断单元，可以由手册 / 作战树 / 案例库的同名症状合并而成，但**不跨同一症状
节点的多个诊断单元**。

要窄一点或宽一点时：

| 想要 | 怎么做 |
| --- | --- |
| 只做子图里的某一个故障 | `--entry`（可重复，多个即合并）；单症状横跨多个单元时再加 `--unit` |
| 编排要人工过一遍（改场景名、拆并、剔除） | `list --export-scenarios` → 编辑 → `build --scenarios` |
| 每个故障各自独立成一份 skill | `build --each` |

## 输入

| 参数 | 说明 |
| --- | --- |
| 位置参数 | 文件或目录，可给多个。目录内 `node*.json` / `edge*.json` 自动识别；单文件按记录形状拆分（带 `edge_type`，或同时带 `source`+`target` 的算边） |
| `--nodes PATH` | 明确指定节点文件，可重复 |
| `--edges PATH` | 明确指定边文件，可重复 |
| `--strict` | 把校验告警也当错误；`build` 直接失败 |

支持 `.json`、`.jsonc`（`//`、`/* */`、尾逗号、BOM）、`.jsonl` / `.ndjson`，
以及 `{"nodes": [...], "edges": [...]}` 整包对象。

## 子图选择（`inspect` / `list` / `plan` / `build`）

都不给就用全部输入；给了则先选**种子节点**，再沿诊断方向向前闭包，
并把 `supports` / `confirms` / `excludes` / `observes` 反向证据拉回来。

| 参数 | 说明 |
| --- | --- |
| `--root VALUE` | 起点：`node_id`、id 前缀或名称关键词，可重复 |
| `--depth N` | 从起点向前展开的层数，0 = 不限 |
| `--node-type T` | 按节点类型筛种子，可重复 |
| `--section S` | 按诊断单元/章节号筛种子，前缀匹配（`28.21` 命中 `28.21.4`），可重复 |
| `--vendor V` | 按 `scope.vendor` 筛种子（大小写不敏感），可重复 |
| `--query 词` | 按关键词筛种子 |

同时给 `--root` 和其他筛选条件时取交集；筛不中任何节点会报错退出（码 2）。

## 输出参数（`build`）

交付的是**整个 skill 目录**：单故障只有 `SKILL.md`；多场景是 `SKILL.md` +
`reference/<场景 slug>.md`（一个场景一份，`reference/` 是 skill 的一部分，装的时候一起拷）。
子图不对外暴露，两种形态下输出目录里都不会有别的东西。

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--out DIR` | 必填 | 输出目录；`--each` 时在其下按 slug 建子目录 |
| `--include-example-specific` | 关 | 保留 `example_specific` 条目（含案例地址、设备名与组网）；默认剔除，剔了什么在构建输出里逐条列出 |
| `--keep-undecidable` | 关 | 保留既无判定观测也无修复动作的原因（默认剔除，这类步骤没有信息量） |
| `--max-steps N` | 0（不限） | 排查步骤上限；被截掉的原因会在构建输出里列出，不会静默丢失 |
| `--shared-coverage R` | 0.8 | 公共前置的门槛：一条采集要被这个比例的**子场景**读到才留在公共层（两个 → 两个都读；十个 → 八个）。多场景文档里子场景是各个场景，低于门槛的下沉成「本场景采集」；单场景文档里子场景是各个排查步骤，低于门槛且只有一个步骤读的还给那个步骤自己下发。分流判据与采集期判根因的采集不受此限 |
| `--exclude VALUE` | 无 | 剔除与本场景无关的节点：`node_id` 或名称关键词（如 `MPLS`），可重复。整条链一起走（原因带着它的检查、观测、修复），并逐条列出；**匹配不到任何节点会报错**，避免写错静默漏掉 |
| `--skill-index FILE` | 无 | `{node_id: slug}` JSON（格式同 `--names`）：本批次别的 skill 覆盖了哪些故障入口。图里 `refers_to` / `leads_to` 指向的故障据此写成 `（skill: <slug>）`。**`--each` 自己算好，不用给**；分开跑 `build` 时才需要。不给也照样写转向，只是不带 slug；给了却没覆盖到的目标写成「本批次未生成对应 skill」并由模板检查告警 |
| `--force` | 关 | 覆盖已有目录 |
| `--dry-run` | 关 | 只打印将写出的文件、模板检查与后校验结果，不落盘 |

### `build` 专有

| 参数 | 说明 |
| --- | --- |
| `--entry VALUE` | **只做这一个故障**：`node_id`、id 前缀或名称关键词，**可重复**（多个即合并）。不给则把整个子图编成一份多场景 skill |
| `--names FILE` | `{node_id: slug}` JSON：自动分组时每个场景的参考文件名（`reference/<slug>.md`）。**多场景时必给**——没有语义的文件名说不出文件里是哪个故障，缺一个就报错退出（码 2）并列出缺哪几个；用 `--scenarios` 时改在清单里填 `slug` |
| `--min-causes N` / `--no-merge` / `--limit N` | 自动分组时的分组参数，与 `list` 同义。整张图的故障都不够 `--min-causes` 时会退回 0 并提示——调用方指名要这个子图的 skill，空手报错不如照实做出来 |
| `--unit SECTION` | 诊断单元（章节号如 `28.21`，或案例 ID 如 `case:loop-001`，前缀匹配）。**可重复**；单症状横跨多个单元时必填 |
| `--merge-same-name` | 把其他来源里同名（拼写差异归一后）的症状及其单元一并合并进来 |
| `--scenarios FILE` | 场景清单 JSON：一份 skill 覆盖多个故障，公共前置检查 + 场景跳转表 + 每场景一份 `reference/<slug>.md`。清单里每个场景的 `slug` 就是它的参考文件名，**必填**，由调用方按语义给英文名；留空就报错退出（码 2）。给了它就忽略 `--entry`/`--unit` |
| `--all-units` | 合并该症状的全部诊断单元；会把多个故障场景写进一份文档，仅在用户明确要求时用 |
| `--name SLUG` | **必填**，英文技能名（`^[a-z0-9-]+$`）。模板硬性要求，中文名会报错 |
| `--description TEXT` | frontmatter 描述；不给则由症状的名称、别名、`match_phrases`、`trigger_context` 生成 |
| `--each` | 每个故障各自独立成一份 skill，写到 `<输出目录>/<slug>/`；不能与 `--entry` / `--scenarios` / `--name` 同用 |

### `build --each`

每个故障（跨来源归并后的症状 × 诊断单元）各自一份，其余参数含义随之变化：

| 参数 | 说明 |
| --- | --- |
| `--names FILE` | 每份 skill 的名字：`{node_id: slug}` 或 `{"node_id@unit": slug}`；没给的用机械 slug 并在结尾列出，提醒改名 |
| `--limit N` | 最多生成多少份（0=不限） |
| `--min-causes N` | 候选原因少于 N 个的场景不生成（默认 1；这类 skill 排查步骤会是空的） |
| `--all-units` | 每个症状一份，不按诊断单元拆分 |
| `--no-merge` | 不按故障跨来源归并，一个场景一份 |

`--each` 先把整批的 slug 全定下来，再逐份写盘：跨故障的转向要写出目标 skill 的名字，
边生成边命名的话，先写的那份不知道后写的那份叫什么。所以这里不需要 `--skill-index`。

### `list` 专有

| 参数 | 说明 |
| --- | --- |
| `--limit N` | 最多列出多少个（默认 30） |
| `--min-causes N` | 至少几个候选原因才算一个场景（默认 1） |
| `--no-merge` | 不按故障跨来源归并，逐场景列出 |
| `--all-units` | 不按诊断单元拆分 |
| `--export-scenarios FILE` | 把当前分组导出成场景清单 JSON（含留空的 `slug` 字段），编辑后交给 `build --scenarios` 生成一份多场景 skill |
| `--show-causes` | 按来源列出每组的根因清单——判断一个合并组是不是混了两类故障（例如"中断"和"震荡"），最直接的依据 |
| `--suggest-merge` | 额外报告名字不同但根因高度重叠的故障（默认阈值：共享 ≥2 个根因且重叠度 ≥34%），并给出可直接执行的合并命令。重叠只用来**提名候选**，判断依据是下面的修复动作对比。**只建议，不自动合并** |

### `plan` 专有

| 参数 | 说明 |
| --- | --- |
| `--scenarios FILE` | 按场景清单规划；不给则用自动分组 |
| `--limit N` | 提示最多列出多少条（默认 20） |
| `--min-causes N` / `--no-merge` | 自动分组时的分组参数，与 `list` 同义 |
| `--include-example-specific` / `--keep-undecidable` / `--max-steps N` / `--exclude VALUE` | 与 `build` 同义，用来预演剔除策略的效果 |

输出的「步骤 / 根因 / 修复命令」是**文档里实际会有的量**（已算进剔除、命令去重、根因折叠），
不是图上的原始计数；「复用的公共前置检查」列出该场景的步骤读了哪几条采集步骤。

### `check`

```bash
python3 scripts/build_skill.py check <skill 目录或 SKILL.md> [更多路径...] --graph <原图>
```

| 参数 | 说明 |
| --- | --- |
| `--graph PATH` | 回查用的**原图**（node/edge 文件或目录），可重复。不给就只做模板检查，并提示后校验被跳过——交付前必须补跑 |

**模板检查**查形状：四章节顺序、步骤编号、跳转目标、根因可查、入参覆盖、占位符写法。
**后校验**查出处：命令只认 `check` / `repair` / `escalation` 的 `command_templates`（经与生成器相同的清洗：
`{x}` 规整成 `<x>`、去设备提示符、缩写与全称折叠），根因只认 `cause` 的名字，
判据只认 `observation` 的表达式/字段/取值，入参只认 `required_slots` 与正文命令里的 `<参数>`；
查不到的报 ERROR，自由文本查不到原文报 WARNING。模板自带的固定说法不算断言，不会误报。
判定口径见 [`evidence-rules.md`](evidence-rules.md#后校验什么算有来源)。

`build` 会自动对自己的产物跑一遍两道检查（对照的是**剔除前**的图：`exclude` 是编排决定，
不是"图里没有"），有 ERROR 退出码为 1。

## 例子

```bash
# 摸底 + 看有哪些故障场景
python3 scripts/build_skill.py inspect /data/kg
python3 scripts/build_skill.py list /data/kg

# 生成一份（先干跑）；--unit 从 list 的输出里抄
python3 scripts/build_skill.py build /data/kg --entry symptom_7f1c --unit 28.21.3 \
    --name isis-neighbor-down --out out/isis-neighbor-down --dry-run

# 按章节切一块，每个故障一份并指定英文名
python3 scripts/build_skill.py build /data/node.json /data/edge.json --each \
    --section 28.21 --out out/ --names names.json

# 交付前检查（改过文档后必须重跑）
python3 scripts/build_skill.py check out/isis-neighbor-down --graph /data/kg
```

`scenarios.json` 形如：

```json
{
  "name": "isis-troubleshooting",
  "description": "",
  "scenarios": [
    {
      "name": "IS-IS 邻居无法建立",
      "slug": "neighbor-down",
      "entries": ["symptom_manual", "symptom_tree", "symptom_case"],
      "units": ["17.4.1", "ipran_battle_tree:s0:r159", "ipran_icase"],
      "exclude": ["MPLS", "cause_bgp_loop_1a2b"]
    },
    { "name": "IS-IS 邻居震荡", "slug": "adjacency-flap", "entries": ["symptom_flap"], "units": ["17.4.3"] }
  ]
}
```

每个场景的 `exclude` 只作用于该场景；命令行 `--exclude` 对所有场景生效。
`slug` 是该场景在 `reference/` 下的文件名（上例生成 `reference/neighbor-down.md`）：
中文场景名和技能名一样无法机械翻译，**必须**由调用方按语义给出：留空就报错退出，
并列出还缺哪几个。文件名是读者和 agent 区分场景的唯一依据，`scenario-a.md` 说不出
文件里是哪个故障；`plan` 会在 `build` 之前先把缺名字的场景报出来。

`names.json` 形如（键可以是 `node_id`，也可以是 `node_id@诊断单元` 以区分同一症状的不同场景）：

```json
{
  "symptom_7f1c02aa93be4d61b0c5e210@28.21.3": "isis-neighbor-down",
  "symptom_7f1c02aa93be4d61b0c5e210@4.3.3": "isis-route-flapping",
  "symptom_2ad4471b8c0f4e2ab7d31f55": "service-interruption"
}
```

## 性能

对全量图跑 `build --each`，份数等于 `list` 报告的故障数（跨来源归并后的“症状 × 诊断单元”），
通常成百上千；每份只带自己那一片子图，单份体积很小。先用 `--section` / `--vendor` / `--limit`
收窄范围通常更实用。

## 交付统计

`plan` 末尾、以及 `build` 之后会给出这张表。它衡量的不是文档合不合模板（那是 `check` 的事），
而是这一轮优化有没有真的落地——一份结构挑不出毛病的文档，仍然可能每步一条命令、每步一条判据。

| 指标 | 健康值 | 不达标意味着 | 回哪一步 |
| --- | --- | --- | --- |
| 采集 命令数 / 步骤数 | 命令数 < 步骤数 × 3 | 一步塞了太多命令，回显读不完 | 拆分采集步骤 |
| 必填参数 | 越少越好，每个都应服务多条命令 | 只服务一个根因的参数该降级为按需下钻 | 参数治理 |
| 步骤数 : 根因数 | 1:1 | 有步骤排了不止一个根因，或有根因没有步骤 | 场景与步骤划分 |
| 判据 / 步骤 | > 1.2 | 多数步骤只有一条判据，区分力不足 | 判据治理 |
| 命令复用率 | > 80% | 前置检查没合够，步骤各自下发命令 | 命令合并 |
| 复检覆盖率 | > 70% | 改完无从验证；来源没给复检命令就如实说 | 数据完整度 |
| 「仅定位」根因 | 记录即可 | 反映数据完整度，不是文档缺陷 | — |
| 场景规模均衡性 | 最大不超最小的 3 倍 | 不是缺陷，但交付时要说明覆盖不均 | 交付说明 |
| 场景数 | 5–7 个 | 超了就该拆成几份 skill：frontmatter 的 description 只列得下头几个场景名，列不进去的场景 agent 不会为它选中这份 skill | 阶段二重新编排 |
| 公共前置 | 3–7 条 | 塌到 0–1 条说明这些场景没有共同入口，本就不该是一份；过多说明公共层混进了单场景的采集 | 阶段二重新编排 |
| 根因覆盖 | ≥ 30 个 | 太少说明边界画得过窄，该并进相邻主题 | 阶段二重新编排 |
| 文档规模 | ≈ 500 行 | 技能命中后整篇进上下文，过长会把真正相关的几步埋掉 | 阶段二重新编排 |

后四项衡量的是**聚合边界**：一份 skill 应该是"5–7 个场景、30+ 根因、500 行、共用 3–7 条入口采集"。
一个上百症状的子图不是一份上百章节的文档，而是十几份这样的 skill——
先按"有没有共同入口采集"和"根因是否重叠"切分，再给每组起主题名。

命令复用率按「自己下发命令的步骤数 ÷ 总步骤数」算：步骤写"复用前置检查步骤 N 回显"
就算复用。判据密度不含每步末尾的"以上判据均不命中"兜底行。

超出健康值不会让 `build` 失败——**它是要在交付时说明的事**，不是可以静默放过的事。

## `--suggest-merge` 怎么判

重叠度只负责把候选挑出来；**合不合的唯一标准是修复动作是否相同**，所以每组候选还会
逐个共享根因比对双方的 `repaired_by` 命令，分成三类：

| 输出行 | 含义 | 该怎么做 |
| --- | --- | --- |
| `修复相同` | 两边的修复命令逐字一致 | 可以直接合并 |
| `单边有CLI` | 一方给了命令，另一方只给了方向或什么都没给 | 合并，取有命令的那份 |
| `修复不同` | 两边都有命令但不一样 | 合并成一条根因就会丢掉差异；两个动作都保留并按影响面排序，或者干脆是两个故障 |

`共用命令` 也会列出来，但它**不是合并依据**：`display isis peer` 横跨十几个故障，
按命令聚类得到的是"用同一条命令排查的一堆故障"，不是同一个故障。

还有两种情况自动比对认不出来，要靠人看：

- **一方是现象、一方是原因**（「Hold Timer超时」与「MTU不匹配」）——不能平行合并，
  应该让现象行指向多个子原因。
- **动作相同但方向不同**（import 与 export）——不按场景切，改按方向切。
