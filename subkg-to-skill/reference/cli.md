# `build_skill.py` 参数参考

只依赖 Python 3.9+ 标准库，从技能目录直接运行，无需安装：

```bash
python3 scripts/build_skill.py <子命令> [输入...] [参数]
```

| 子命令 | 用途 | 退出码 |
| --- | --- | --- |
| `build` | 生成技能目录 | 0 成功；1 `--strict` 下有校验错误；2 输入/参数错误 |
| `inspect` | 看规模、类型分布、章节、质量标记、能生成多少份手册 | 0 / 2 |
| `validate` | 只做结构校验 | 0 无错误；1 有错误；2 输入错误 |

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

## `build` 专有参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--out DIR` | 必填 | 输出目录 |
| `--name SLUG` | `kg-fault-diagnosis` | 技能名，会规范成 `^[a-z0-9-]+$`；纯中文名无法规范化，会报错 |
| `--title TEXT` | `IP-RAN 故障诊断（知识图谱子图）` | 产物 `SKILL.md` 的标题 |
| `--description TEXT` | 自动生成 | frontmatter 描述，超过 1024 字符会截断 |
| `--max-playbooks N` | 0（不限） | 只生成前 N 份手册，按“真入口”优先排序 |
| `--evidence N` | 3 | 每个条目展示几条来源 |
| `--data full\|slim\|none` | `full` | `slim` 去掉 `canonical_key`、`semantic_review` 等簿记字段并截断证据；`none` 不带数据（查询脚本会无数据可读） |
| `--allowed-tools TEXT` | 空 | 写入产物 frontmatter 的 `allowed-tools` |
| `--no-script` | 关 | 不生成 `scripts/kg_query.py` |
| `--force` | 关 | 覆盖已有目录，并清理这次不再产生的 `fault-*.md` / `index-*.md` |
| `--dry-run` | 关 | 只打印将写出的文件与大小，不落盘 |

## 例子

```bash
# 摸底
python3 scripts/build_skill.py inspect /data/kg

# 只取一个症状及其三层邻域，先看会写什么
python3 scripts/build_skill.py build /data/kg --root symptom_7f1c --depth 3 \
    --out out/isis --name isis-neighbor-down --dry-run

# 按章节切一份，随包数据瘦身
python3 scripts/build_skill.py build /data/node.json /data/edge.json \
    --section 28.21 --out out/isis --name isis-diagnosis --data slim

# 覆盖重建
python3 scripts/build_skill.py build /data/kg --out out/full --name ipran-diagnosis --force
```

## 性能

全量 17,392 节点 / 17,088 边约 5 秒完成，产出 1,352 份手册；
`--data slim` 时数据文件约 29 MB，`index.md` 自动分片后仍是几 KB。
