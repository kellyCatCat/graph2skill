# `build_skill.py` 参数参考

只依赖 Python 3.9+ 标准库，从技能目录直接运行，无需安装：

```bash
python3 scripts/build_skill.py <子命令> [输入...] [参数]
```

| 子命令 | 用途 | 退出码 |
| --- | --- | --- |
| `list` | 列出故障场景（症状 × 诊断单元）、规模、触发说法、建议 slug 与生成命令 | 0 / 2 |
| `build` | 为**一个**故障场景生成 skill | 0 成功；1 模板检查有错误或 `--strict` 下有校验错误；2 输入/参数错误 |
| `build-all` | 给每个故障场景各生成一份 skill | 同上 |
| `inspect` | 看规模、类型分布、章节、质量标记 | 0 / 2 |
| `validate` | 只做结构校验 | 0 无错误；1 有错误；2 输入错误 |
| `lint` | 检查已生成的 skill 是否符合模板 | 0 通过；1 有 ERROR |

一个 skill 对应一个**故障场景**：一个 symptom × 一个诊断单元。子图里有多个入口时
`build` 必须用 `--entry` 指定；症状横跨多个诊断单元时必须用 `--unit` 指定其一
（或 `--all-units` 明确要合并，代价是场景杂糅与长度爆炸）。

## 输入

| 参数 | 说明 |
| --- | --- |
| 位置参数 | 文件或目录，可给多个。目录内 `node*.json` / `edge*.json` 自动识别；单文件按记录形状拆分（带 `edge_type`，或同时带 `source`+`target` 的算边） |
| `--nodes PATH` | 明确指定节点文件，可重复 |
| `--edges PATH` | 明确指定边文件，可重复 |
| `--strict` | 把校验告警也当错误；`build` 直接失败 |

支持 `.json`、`.jsonc`（`//`、`/* */`、尾逗号、BOM）、`.jsonl` / `.ndjson`，
以及 `{"nodes": [...], "edges": [...]}` 整包对象。

## 子图选择（`build` 与 `inspect`）

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

## 输出参数（`build` / `build-all` 共用）

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--out DIR` | 必填 | 输出目录；`build-all` 在其下按 slug 建子目录 |
| `--evidence N` | 3 | `reference/evidence.md` 里每个条目展示几条来源 |
| `--data full\|slim\|none` | `full` | `slim` 去掉 `canonical_key`、`semantic_review` 等簿记字段并截断证据；`none` 不带数据（查询脚本会无数据可读） |
| `--no-script` | 关 | 不生成 `scripts/kg_query.py` |
| `--no-lead` | 关 | 不在 frontmatter 后加指向 `reference/` 的提示行 |
| `--include-example-specific` | 关 | 保留 `example_specific` 条目（含案例地址、设备名与组网）；默认剔除并记进 evidence.md |
| `--keep-undecidable` | 关 | 保留既无判定观测也无修复动作的原因（默认剔除，这类步骤没有信息量） |
| `--max-steps N` | 0（不限） | 排查步骤上限；被截掉的原因会记进 evidence.md，不会静默丢失 |
| `--force` | 关 | 覆盖已有目录 |
| `--dry-run` | 关 | 只打印将写出的文件与模板检查结果，不落盘 |

### `build` 专有

| 参数 | 说明 |
| --- | --- |
| `--entry VALUE` | 入口症状：`node_id`、id 前缀或名称关键词。子图里只有一个 symptom 时可省略 |
| `--unit SECTION` | 诊断单元（章节号如 `28.21`，或案例 ID 如 `case:loop-001`，前缀匹配）。症状横跨多个单元时必填 |
| `--all-units` | 合并该症状的全部诊断单元；会把多个故障场景写进一份文档，仅在用户明确要求时用 |
| `--name SLUG` | **必填**，英文技能名（`^[a-z0-9-]+$`）。模板硬性要求，中文名会报错 |
| `--description TEXT` | frontmatter 描述；不给则由症状的名称、别名、`match_phrases`、`trigger_context` 生成 |

### `build-all` 专有

| 参数 | 说明 |
| --- | --- |
| `--names FILE` | `{node_id: slug}` 或 `{"node_id@unit": slug}` 的 JSON 映射；没给的场景会用机械 slug 并在结尾列出，提醒改名 |
| `--limit N` | 最多生成多少份（0=不限） |
| `--min-causes N` | 候选原因少于 N 个的场景不生成（默认 1；这类 skill 排查步骤会是空的） |
| `--all-units` | 每个症状一份，不按诊断单元拆分 |

### `lint`

```bash
python3 scripts/build_skill.py lint <skill 目录或 SKILL.md> [更多路径...]
```

## 例子

```bash
# 摸底 + 看有哪些故障场景
python3 scripts/build_skill.py inspect /data/kg
python3 scripts/build_skill.py list /data/kg

# 生成一份（先干跑）；--unit 从 list 的输出里抄
python3 scripts/build_skill.py build /data/kg --entry symptom_7f1c --unit 28.21.3 \
    --name isis-neighbor-down --out out/isis-neighbor-down --dry-run

# 按章节切一块，批量生成并指定英文名
python3 scripts/build_skill.py build-all /data/node.json /data/edge.json \
    --section 28.21 --out out/ --names names.json --data slim

# 自检
python3 scripts/build_skill.py lint out/isis-neighbor-down
```

`names.json` 形如（键可以是 `node_id`，也可以是 `node_id@诊断单元` 以区分同一症状的不同场景）：

```json
{
  "symptom_7f1c02aa93be4d61b0c5e210@28.21.3": "isis-neighbor-down",
  "symptom_7f1c02aa93be4d61b0c5e210@4.3.3": "isis-route-flapping",
  "symptom_2ad4471b8c0f4e2ab7d31f55": "service-interruption"
}
```

## 性能

全量 17,392 节点 / 17,088 边的图，`build-all` 会产出 1,352 份 skill（每个故障入口一份）；
每份只带自己那一片子图，所以单份体积很小。先用 `--section` / `--vendor` / `--limit` 收窄范围
通常更实用。
