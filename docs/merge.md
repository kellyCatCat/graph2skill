# 把子图融入已有 skill

已有的 skill 是人写的：`SKILL-bgp.md` 里的「根因迭代到底层」映射表、话术、章节编号都是人的判断，
整篇重生成会把这些丢掉。所以 `graph2skill merge` 只做**手术式改动**——只改新子图带来的那部分，
其余字节保持不变，重复执行是幂等的（第二次跑没有任何待写文件）。

## 一、技能集清单

`merge` 需要知道三件事：技能文档是哪份、它配对的 JSON 子图是哪份、它 include 了谁。
这些记在 `skillset.json` 里，可以从现有目录自动推断：

```bash
graph2skill skillset init skills/
graph2skill skillset status -s skills/skillset.json
```

```
skills/
├── skillset.json
├── common.md                       # role=common，被子技能引用
├── SKILL-bgp.md                    # includes: [common]
├── graphs/{common,bgp}.json        # 与文档一一对应的子图
├── reference/fault-10-bgp-neighbor.md
└── statistics/bgp-neighbor-abnormal-stats.json
```

推断规则：`SKILL-*.md` / `common.md` 是文档；配对子图先按 `graphs/<name>.json` 精确找，找不到再按
前缀找（`SKILL-isis.md` 会配上 `isis_all.json`；两个候选时不猜，留空）；文档里「引用通用规范」表里
出现的 `.md` 文件名 → `includes`；正文里的 `statistics/*.json` → 统计文件。

场景技能推断不出子图会被列出来并以退出码 1 提示，需要在清单里手工补 `graph`；
**公共技能没有配对子图是允许的**——这时公共节点按 `common.md` 的章节标题匹配，只少了按节点 id 判定的能力。

## 图里建议补的字段

新故障小节的编号、标识、分类直接取自故障节点，缺了就只能推断（`faultId` 退化成节点 id 的尾数、
`identifier` 退化成 id 转的 ASCII、分类写 `-`），需要人工改。合并前补上这三个字段最省事：

```json
{ "id": "scenario:isis:IS-IS-1", "type": "SCENARIO", "name": "IS-IS 邻居无法建立",
  "data": { "faultId": 20, "identifier": "isis-neighbor-down", "category": "路由协议类",
            "referenceFile": "reference/fault-20-isis-neighbor.md" } }
```

已经写进文档的故障不受影响：定位时依次按「故障序号」→「标识:」→ 名称匹配，
名称能对上就会合并到已有小节，并沿用文档里已写的 reference 路径。

清单字段：

| 字段 | 含义 |
| --- | --- |
| `name` / `doc` | 技能名与文档路径（相对 `root`） |
| `graph` | 配对的 JSON 子图 |
| `role` | `scenario`（默认）或 `common` |
| `includes` | 引用的技能名，公共节点从这里判定 |
| `referenceDir` | 决策树文件目录，默认 `reference` |
| `statistics` | 统计文件路径 |
| `sections.faults` / `sections.iterate` | 手工指定承载故障小节 / 根因迭代表的章节号（自动识别不了时用） |

## 二、融入

```bash
graph2skill merge new-subgraph.json --into bgp -s skills/skillset.json --dry-run   # 先看差异
graph2skill merge new-subgraph.json --into bgp -s skills/skillset.json             # 再写
```

`--into` 可以写技能名，也可以写文档路径（`skills/SKILL-bgp.md`）；不传 `-s` 时会在该目录找
`skillset.json`，找不到就按目录结构临时推断并在 stderr 里说明。

会发生四类改动：

| 位置 | 改动 | 保护方式 |
| --- | --- | --- |
| 配对的 JSON 子图 | 并入新节点/关系后回写 | `--no-graph-update` 可关；写出的是精简形式，不含内部字段 |
| `SKILL-*.md` 的故障章节 | 新故障插入 `### 2.N 故障序号M：xxx`；已有故障的根因表追加行、`根因: N类` 同步 | 按 `故障序号` / `标识:` / 名称定位；表格按「根因名称」列合并，手写单元格不被空值覆盖 |
| 「根因迭代到底层」表 | 补上尚未出现的 `common.md` 章节跳转 | 按章节号去重——`§3.6` 已在任何一行里出现过就不再加行 |
| `reference/fault-*.md` | 新根因写进托管区 | 托管区在 `<!-- graph2skill:begin id=causes -->` 与 `end` 之间，手写内容永不改动 |

**托管区的约定**：标记之间的内容归 graph2skill 所有，每次运行都会重新生成；要手工补充请写在标记之外。
技能文档本身**不使用**标记——新小节插入一次之后，后续运行按「故障序号」定位并只做表格合并，
所以你对新小节的润色不会被覆盖。

## 三、公共节点只留引用

被 `includes` 的技能（`common.md`）拥有的节点，不会被复制进子技能：

- 子图回写时这些节点被剔除，指向它们的关系保留 —— 这条关系就是「引用」；
- 决策树文件里写成「底层下钻：见 common.md §3.6 链路故障」；
- 「根因迭代到底层」表里补一行 `根因方向 | §3.6 | 链路故障`。

章节号的来源依次是：节点 `data.section`（如 `"3.6"`）→ `common.md` 里标题相同的章节编号。
两者都取不到时不会瞎猜，而是报一条告警（`未能定位章节号`），由人决定写到哪一节。

## 四、统计文件

`--update-stats` 才会动统计文件，且只更新已经存在的键（如 `causeCount`），不新增字段。
默认不动，并在结果里提示。

## 五、什么时候会拒绝改

- 技能没有配对子图 → 报错，让你在清单里补 `graph`；
- 找不到承载故障小节的章节 → 只告警并跳过故障表更新（可用 `sections.faults` 指定）；
- 找不到「根因迭代到底层」章节 → 告警并列出未写入的跳转条数（可用 `sections.iterate` 指定）；
- 找不到根因表 → 告警，不猜位置。

所有告警都在 stderr 里以 `!` 开头列出，`--dry-run` 时同样打印。
