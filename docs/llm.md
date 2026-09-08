# 调用大模型

## 一、模型接入配置

沿用 [skill_distill](https://github.com/kellyCatCat/skill_distill) 的约定：地址和密钥放在**不入库**的
`.env`，`src/graph2skill/llm.py` 里的 `MODEL_PROFILES` 按模型名登记环境变量前缀、是否开思考、输出预算。

```bash
cp .env.example .env
```

```ini
QWEN38_BASE_URL=http://<host>:<port>/v1
QWEN38_API_KEY=
# QWEN38_MAX_TOKENS=32768        # 留空用 MODEL_PROFILES 里的默认值
```

- 变量名规则：`<前缀>_BASE_URL` / `<前缀>_API_KEY` / `<前缀>_MAX_TOKENS`。
- 真实环境变量优先于 `.env`，可临时覆盖：`QWEN38_BASE_URL=... graph2skill steps ...`。
- `.env` 默认在当前工作目录，可用 `--env` 或 `GRAPH2SKILL_ENV` 指定。
- 地址只写到 `/v1` 即可，会自动补成 `/v1/chat/completions`。
- 模型主机会自动加进 `NO_PROXY`，避免请求被环境里的代理拦掉。

登记一个新模型：在 `MODEL_PROFILES` 里加一条即可。

```python
MODEL_PROFILES = {
    "qwen3.8-27b": {"env_prefix": "QWEN38", "thinking": False, "max_tokens": 16384},
    "my-model":    {"env_prefix": "MYMODEL", "thinking": True,  "max_tokens": 32768},
}
```

`thinking` 要按**实测**登记而不是看名字：`thinking: False` 时才发关思考的
`chat_template_kwargs={"enable_thinking": False, "thinking": False}`——这个字段发给会思考的模型
会把思考压掉；`thinking: True` 时整个字段都不发，并把输出预算留给推理。

## 二、先确认链路通

```bash
graph2skill models            # 打印解析出的地址、打码后的密钥、预算、思考开关
graph2skill models --probe    # 再发一个最小请求（"回复OK两个字"），确认真的能拿到正文
```

`--probe` 的判定：能拿到正文 → `[OK]`；只回了推理、正文为空 → 报错并提示调大 `<前缀>_MAX_TOKENS`；
响应体不是 JSON → 把状态码、Content-Type、正文开头带出来，便于分辨是网关断连还是模型的问题。

## 三、生成

```bash
graph2skill steps graphs/bgp.json --fault 11 --llm -d out/
```

链路是：

```
程序初稿（已合规） ─▶ 模型改写 ─▶ lint ─┬─ 无错误 ─▶ 落盘
                        ▲              └─ 有错误 ─▶ 把错误清单回灌 ─▶ 重来（最多 --max-repairs 轮）
                        └──────────────────────────────────────────┘
                                     仍不过 ─▶ 回退到程序初稿
```

所以模型不可用、限流、或表现不佳时，产出的仍然是一份合规文档，不会是半成品。

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--provider openai\|anthropic` | 默认 `openai`（自建端点，读 `.env`）；`anthropic` 走 Claude API |
| `--model` | 模型名，默认 `qwen3.8-27b`；Claude 通道默认 `claude-opus-5` |
| `--base-url` | 直接指定地址，优先于 `.env` |
| `--max-tokens` / `--timeout` | 覆盖该模型的输出预算 / 单次请求超时（默认 300 秒） |
| `--max-repairs` | 校验不过时回灌重生成的轮数，默认 2 |
| `--strict` | 最终仍有校验错误时退出码为 1 |

## 四、不联网怎么用

```bash
# 把系统提示 + 证据包 + 初稿导出，粘到任意模型对话里
graph2skill steps graphs/bgp.json --fault 11 --prompt-only prompt.txt
# 把别处生成的回复喂回来，走同一套校验后落盘
graph2skill steps graphs/bgp.json --fault 11 --from-response answer.md -o out/skill.md --strict
```

## 五、提示词里有什么

- **系统提示**：角色 + 完整模板规范 + 5 条铁律（第一条是「只能用证据包 allowedCommands 里的 CLI」）。
  Claude 通道上这段带 `cache_control`，多次调用命中缓存。
- **用户提示**：`<证据包>`（JSON：故障、入参、前置检查、步骤、根因、命令白名单、出处）+ `<程序初稿>`。

模型只被允许在证据包范围内改写。它编出来的命令会被 `command-not-in-source` 拦下并回灌重来——
**这条规则由代码保证，不依赖模型自觉**。

## 六、回复解析上的坑

这几处都按 skill_distill 踩过的坑处理：

| 现象 | 处理 |
| --- | --- |
| 端点无视 `stream: False` 返回 SSE | 按 `data:` 块拼回正文与 `reasoning_content`；收尾块给的是完整 message 也认 |
| SSE 的 Content-Type 不带 charset | 显式 `encoding = "utf-8"`，否则中文全是乱码 |
| `finish_reason=length` | 直接报错并指名要调大哪个 `<前缀>_MAX_TOKENS` |
| 只回推理、正文为空 | 报错并带上 `reasoning_content` 长度与 `finish_reason` |
| 回复里带 `<think>` 块 | 剥掉；只有开标签（被截断）时丢弃该标签之前的内容 |
| 回复被 ```markdown 围栏包住 | 剥围栏；没有围栏时从 `---\nname:` 开始截取 |
| 超时 / 连接失败 / 5xx | 重试 3 次，退避 1s、2s |
