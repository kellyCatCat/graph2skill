"""调用大模型生成 skill，并用校验器把关。

模型接入沿用 skill_distill 仓的约定：地址和密钥放在**不入库**的 `.env`
（`<前缀>_BASE_URL` / `<前缀>_API_KEY` / `<前缀>_MAX_TOKENS`），
:data:`MODEL_PROFILES` 按模型名登记前缀、是否开思考、输出预算。
默认走 OpenAI 兼容的 ``/v1/chat/completions``；另提供 Claude API 通道。

模型不决定硬规则：它拿到的是模板规范 + 从图谱抽出的证据包 + 一份已经合规的
程序初稿；它的输出一律过 :func:`graph2skill.stepskill.lint`，仍有错误就把
错误回灌重来，超过轮数还不过就退回程序初稿。
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, Sequence, Tuple

from graph2skill.analyze import Issue
from graph2skill.stepskill import StepSkillContext, lint, read_spec, render_markdown

# ---------------------------------------------------------------------------
# 模型接入配置（.env + MODEL_PROFILES）
# ---------------------------------------------------------------------------

ENV_FILENAME = ".env"

#: 每个模型的调用参数；base_url / api_key / max_tokens 从 .env 里按 env_prefix 取。
#: thinking=False 时才发关思考的 chat_template_kwargs——把这个字段发给会思考的
#: 模型会把思考压掉；thinking=True 时整个字段都不发，并把输出预算留给推理。
MODEL_PROFILES: Dict[str, Dict[str, object]] = {
    "qwen3.6-27b": {"env_prefix": "QWEN36", "thinking": False, "max_tokens": 16384},
    "qwen3.8-27b": {"env_prefix": "QWEN38", "thinking": False, "max_tokens": 16384},
}
DEFAULT_PROFILE: Dict[str, object] = {"env_prefix": "QWEN38", "thinking": False, "max_tokens": 16384}
DEFAULT_MODEL = "qwen3.8-27b"

#: Claude 通道的默认模型与预算（provider="anthropic" 时使用）
DEFAULT_ANTHROPIC_MODEL = "claude-opus-5"
DEFAULT_ANTHROPIC_MAX_TOKENS = 32000
DEFAULT_ANTHROPIC_EFFORT = "high"

REASONING_BLOCK_RE = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)
MARKDOWN_FENCE_RE = re.compile(r"```markdown\s*(.+)\s*```", re.DOTALL)
FRONT_MATTER_RE = re.compile(r"^-{3}\s*\n\s*name\s*:", re.MULTILINE)


class LLMError(RuntimeError):
    """模型不可达、被拒绝，或返回了不可用的内容。"""


def env_path(start: Optional[str] = None) -> str:
    """`.env` 的位置：优先 GRAPH2SKILL_ENV，其次当前工作目录。"""
    override = os.environ.get("GRAPH2SKILL_ENV")
    if override:
        return override
    return os.path.join(start or os.getcwd(), ENV_FILENAME)


def load_env_file(path: Optional[str] = None) -> Dict[str, str]:
    """读 `.env`（KEY=VALUE，# 开头为注释）。真实环境变量优先，便于临时覆盖。"""
    values: Dict[str, str] = {}
    target = path or env_path()
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip("'\"")
                values[key] = os.environ.get(key) or value
    for key, value in os.environ.items():
        if key.endswith(("_BASE_URL", "_API_KEY", "_MAX_TOKENS")):
            values.setdefault(key, value)
    return values


def chat_completions_url(base_url: str) -> str:
    """把 `.../v1` 补成 `.../v1/chat/completions`；已是完整路径的原样返回。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def allow_direct_connection(url: str) -> None:
    """把模型主机加进 NO_PROXY，避免请求被环境里的代理拦掉。"""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if not host:
        return
    for var in ("NO_PROXY", "no_proxy"):
        current = [item for item in os.environ.get(var, "").split(",") if item]
        if host not in current:
            os.environ[var] = ",".join(current + [host])


@dataclass
class ModelConfig:
    """一个模型的全部调用参数。"""

    model: str
    api_url: str = ""
    api_key: str = ""
    max_tokens: int = 16384
    thinking: bool = False
    temperature: float = 0.4
    timeout: int = 300
    max_retries: int = 3
    retry_delay: float = 1.0
    registered: bool = True
    env_prefix: str = ""


def resolve_model(
    model_name: Optional[str] = None,
    api_url: Optional[str] = None,
    env_file: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> ModelConfig:
    """按模型名解析地址、密钥、预算与思考开关（显式参数优先于 `.env`）。"""
    name = model_name or DEFAULT_MODEL
    profile = MODEL_PROFILES.get(name, DEFAULT_PROFILE)
    env = load_env_file(env_file)
    prefix = str(profile["env_prefix"])

    url = chat_completions_url(api_url or env.get(f"{prefix}_BASE_URL", ""))
    if not url:
        raise LLMError(
            f"模型 {name!r} 没有可用地址：请在 .env 里设置 {prefix}_BASE_URL"
            f"（可参考仓库根目录的 .env.example），或调用时显式传 --base-url"
        )
    allow_direct_connection(url)
    budget = env.get(f"{prefix}_MAX_TOKENS", "").strip()
    return ModelConfig(
        model=name,
        api_url=url,
        api_key=env.get(f"{prefix}_API_KEY", "").strip(),
        max_tokens=max_tokens or (int(budget) if budget.isdigit() else int(profile["max_tokens"])),
        thinking=bool(profile["thinking"]),
        registered=name in MODEL_PROFILES,
        env_prefix=prefix,
    )


def mask_secret(secret: str) -> str:
    if not secret:
        return "（无，按不鉴权处理）"
    if len(secret) <= 11:
        return f"{'*' * len(secret)}（{len(secret)}字符）"
    return f"{secret[:7]}…{secret[-4:]}（{len(secret)}字符）"


# ---------------------------------------------------------------------------
# 响应解析
# ---------------------------------------------------------------------------

def parse_sse_stream(text: str) -> Tuple[str, str, Optional[str]]:
    """把 SSE 流拼回 (正文, 推理内容, finish_reason)。

    有的端点无视 ``stream: False`` 一律返回 text/event-stream；收尾那条给的可能是
    完整 message 而不是 delta，两个字段都认。
    """
    content_parts: List[str] = []
    reasoning_parts: List[str] = []
    finish_reason: Optional[str] = None
    chunks = 0
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            chunk = json.loads(data)
        except ValueError:
            continue
        chunks += 1
        for choice in chunk.get("choices") or ():
            delta = choice.get("delta") or choice.get("message") or {}
            if delta.get("content"):
                content_parts.append(delta["content"])
            if delta.get("reasoning_content"):
                reasoning_parts.append(delta["reasoning_content"])
            if choice.get("finish_reason"):
                finish_reason = choice["finish_reason"]
    if not chunks:
        raise LLMError("SSE 流里没有解析出任何 data: 块")
    return "".join(content_parts), "".join(reasoning_parts), finish_reason


def strip_reasoning(content: str) -> str:
    """剥掉内联推理块；只有开标签（被截断）时丢弃该标签之前的内容。"""
    content = REASONING_BLOCK_RE.sub("", content or "")
    unclosed = re.search(r"<(?:think|thinking|reasoning)>", content, re.IGNORECASE)
    if unclosed:
        content = content[unclosed.end() :]
    return content.strip()


def extract_markdown_content(text: str) -> str:
    """从模型回复里取出 skill 正文；取不到返回空串表示这次回复无效。"""
    match = MARKDOWN_FENCE_RE.search(text or "")
    if match:
        return match.group(1).strip()
    front = FRONT_MATTER_RE.search(text or "")
    if front:
        content = text[front.start() :].strip()
        if "```" in text[: front.start()] and content.endswith("```"):
            content = content[:-3].strip()
        return content
    return ""


# ---------------------------------------------------------------------------
# 客户端
# ---------------------------------------------------------------------------

class LLMClient(Protocol):
    """任何能把提示词变成文本的东西。"""

    def complete(self, system: str, prompt: str) -> str: ...


@dataclass
class OpenAICompatClient:
    """自建 OpenAI 兼容端点（vLLM / qwen 等），按 `.env` 取地址与密钥。"""

    config: ModelConfig

    @classmethod
    def from_env(
        cls,
        model: Optional[str] = None,
        api_url: Optional[str] = None,
        env_file: Optional[str] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> "OpenAICompatClient":
        config = resolve_model(model, api_url, env_file, max_tokens)
        if timeout:
            config.timeout = timeout
        return cls(config=config)

    def _payload(self, system: str, prompt: str) -> Dict[str, object]:
        payload: Dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
        }
        if not self.config.thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False, "thinking": False}
        return payload

    def complete(self, system: str, prompt: str) -> str:
        try:
            import requests
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise LLMError("需要 requests：pip install 'graph2skill[llm]'") from exc

        payload = self._payload(system, prompt)
        headers = {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else None
        last_error = ""
        for attempt in range(self.config.max_retries):
            try:
                response = requests.post(
                    self.config.api_url,
                    json=payload,
                    headers=headers,
                    timeout=self.config.timeout,
                    verify=False,
                )
                response.raise_for_status()
                content, reasoning, finish_reason = self._read(response)
                content = strip_reasoning(content)
                if finish_reason == "length":
                    raise LLMError(
                        f"输出被截断(finish_reason=length)，正文{len(content)}字符"
                        f"（max_tokens={self.config.max_tokens}"
                        f"{'，thinking 开启，推理 token 也占预算' if self.config.thinking else ''}）："
                        f"请调大 .env 里的 {self.config.env_prefix}_MAX_TOKENS 或拆分输入"
                    )
                if not content and reasoning:
                    raise LLMError(
                        f"只返回了推理内容、正文为空（reasoning_content {len(reasoning)}字符，"
                        f"finish_reason={finish_reason!r}，max_tokens={self.config.max_tokens}）"
                    )
                extracted = extract_markdown_content(content) or content
                if not extracted.strip():
                    raise LLMError(f"回复为空（finish_reason={finish_reason!r}）")
                return extracted
            except requests.exceptions.Timeout:
                last_error = f"请求超时（尝试 {attempt + 1}/{self.config.max_retries}）"
            except requests.exceptions.ConnectionError:
                last_error = f"连接失败（尝试 {attempt + 1}/{self.config.max_retries}）"
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else "?"
                last_error = f"HTTP 错误 {status}（尝试 {attempt + 1}/{self.config.max_retries}）"
            except LLMError as exc:
                last_error = f"{exc}（尝试 {attempt + 1}/{self.config.max_retries}）"
            if attempt < self.config.max_retries - 1:
                time.sleep(self.config.retry_delay * (attempt + 1))
        raise LLMError(last_error or "模型调用失败")

    def _read(self, response) -> Tuple[str, str, Optional[str]]:
        content_type = response.headers.get("Content-Type", "?")
        if "text/event-stream" in content_type:
            # SSE 的 Content-Type 通常不带 charset，requests 会退回 ISO-8859-1，
            # 中文全成乱码，必须显式指定
            response.encoding = "utf-8"
            return parse_sse_stream(response.text)
        response.encoding = response.encoding or "utf-8"
        body = (response.text or "").strip()
        try:
            payload = response.json()
        except ValueError:
            hint = "；响应体为空，通常是网关在模型生成完之前就断开了连接" if not body else f"开头: {body[:200]!r}"
            raise LLMError(
                f"响应体不是 JSON（HTTP {response.status_code}，Content-Type={content_type}，{hint}）"
            )
        try:
            choice = payload["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"回复结构里没有 choices[0].message：{json.dumps(payload, ensure_ascii=False)[:200]}")
        return message.get("content") or "", message.get("reasoning_content") or "", choice.get("finish_reason")


@dataclass
class AnthropicClient:
    """Claude API 通道（pip install anthropic；密钥取 ANTHROPIC_API_KEY 或 ant auth login）。"""

    model: str = DEFAULT_ANTHROPIC_MODEL
    max_tokens: int = DEFAULT_ANTHROPIC_MAX_TOKENS
    effort: str = DEFAULT_ANTHROPIC_EFFORT
    max_retries: int = 2
    timeout: Optional[float] = None

    def complete(self, system: str, prompt: str) -> str:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise LLMError("需要 anthropic SDK：pip install 'graph2skill[anthropic]'") from exc

        kwargs: Dict[str, object] = {"max_retries": self.max_retries}
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        client = anthropic.Anthropic(**kwargs)
        try:
            with client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                thinking={"type": "adaptive"},
                output_config={"effort": self.effort},
                messages=[{"role": "user", "content": prompt}],
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise LLMError("鉴权失败：请设置 ANTHROPIC_API_KEY 或执行 ant auth login") from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("触发限流，请稍后重试") from exc
        except anthropic.APIStatusError as exc:
            raise LLMError(f"接口返回 {exc.status_code}：{exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError(f"无法连接 Claude API：{exc}") from exc

        if message.stop_reason == "refusal":
            detail = getattr(message, "stop_details", None)
            raise LLMError(f"模型拒绝了该请求（{getattr(detail, 'category', 'unknown')}）")
        text = "".join(block.text for block in message.content if block.type == "text").strip()
        if not text:
            raise LLMError("模型返回了空响应")
        return extract_markdown_content(text) or text


@dataclass
class StaticClient:
    """回放预置响应，供测试与离线演练使用。"""

    responses: List[str] = field(default_factory=list)
    prompts: List[str] = field(default_factory=list)

    def complete(self, system: str, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self.responses:
            raise LLMError("StaticClient 没有更多预置响应")
        return self.responses.pop(0)


def make_client(
    provider: str = "openai",
    model: Optional[str] = None,
    api_url: Optional[str] = None,
    env_file: Optional[str] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[int] = None,
) -> LLMClient:
    """按 provider 造客户端：``openai``（默认，自建端点）或 ``anthropic``。"""
    if provider == "anthropic":
        return AnthropicClient(
            model=model or DEFAULT_ANTHROPIC_MODEL,
            max_tokens=max_tokens or DEFAULT_ANTHROPIC_MAX_TOKENS,
            timeout=float(timeout) if timeout else None,
        )
    if provider in ("openai", "openai-compat", "local"):
        return OpenAICompatClient.from_env(model, api_url, env_file, max_tokens, timeout)
    raise LLMError(f"未知的 provider: {provider}（可用：openai / anthropic）")


def probe(config: ModelConfig, timeout: int = 120, max_tokens: int = 1024) -> str:
    """发一个小请求确认链路是否真的通（对应 skill_distill 的 model_config --probe）。"""
    client = OpenAICompatClient(
        config=ModelConfig(**{**config.__dict__, "max_tokens": max_tokens, "timeout": timeout, "max_retries": 1})
    )
    try:
        text = client.complete("你是一个连通性探测工具。", "回复OK两个字")
    except LLMError as exc:
        return f"[FAIL] {exc}"
    return f"[OK] {text[:40]!r}"


# ---------------------------------------------------------------------------
# 提示词与生成
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """你是网络故障排查 skill 的编写者。你会收到一份 skill 模板规范、一份从知识图谱抽取的\
证据包，以及一份由程序生成的、已经满足模板硬性要求的初稿。

你的任务是产出**最终的 skill 文档**：保持模板结构与硬性约束不变，在此基础上改进措辞、步骤顺序、\
判据表述与根因命名，使其读起来像一份可执行的现场排查手册。

铁律：
1. **只能使用证据包 allowedCommands 里出现过的 CLI，一条都不许自己生成或补全。**
   证据包没给修复命令时，照实写方向性描述或「无直接修复CLI」，不要编造可执行配置。
2. 四个一级章节顺序固定、缺一不可：# 入参列表 → # 前置检查 → # 排查步骤 → # 根因对照表。
3. 步骤编号从 1 连续；跳转只能指向真实存在的步骤号；最后一步必须写清判据全不命中时判定「未找到根因」。
4. 步骤里的「根因定位」只写根因名称，修复与复检只出现在根因对照表；两处的根因名称必须逐字一致。
5. 命令里的可变参数一律 <尖括号>，同一参数全篇同名，且必须出现在入参列表里；接口名写全称。

只输出 skill 文档本身（以 --- 开头的 front matter 起始），不要任何解释性前后缀。"""


def evidence_pack(context: StepSkillContext) -> str:
    """模型被允许使用的全部事实，JSON 格式。"""
    payload = {
        "faultName": context.fault_name,
        "suggestedName": context.name,
        "suggestedDescription": context.description,
        "allowedCommands": context.allowed_commands,
        "parameters": [
            {"cli": param.cli, "label": param.label, "required": param.required, "note": param.note}
            for param in context.params
        ],
        "prechecks": [
            {"title": item.title, "commands": item.commands, "collect": item.collect, "rootCause": item.root_cause}
            for item in context.prechecks
        ],
        "steps": [
            {
                "index": step.index,
                "title": step.title,
                "commands": [command for command, _ in step.commands],
                "reuse": step.reuse_note,
                "condition": step.condition,
                "cause": step.cause,
            }
            for step in context.steps
        ],
        "causes": [
            {"name": row.name, "symptom": row.symptom, "fix": row.fix, "verify": row.verify}
            for row in context.causes
        ],
        "provenance": context.evidence,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def build_system_prompt() -> str:
    return f"{SYSTEM_PROMPT}\n\n<模板规范>\n{read_spec()}\n</模板规范>"


def build_prompt(context: StepSkillContext, draft: Optional[str] = None) -> str:
    draft = draft if draft is not None else render_markdown(context)
    return (
        f"<证据包>\n{evidence_pack(context)}\n</证据包>\n\n"
        f"<程序初稿>\n{draft}\n</程序初稿>\n\n"
        "请基于证据包改进初稿，输出最终 skill 文档。"
    )


def build_repair_prompt(text: str, issues: Sequence[Issue]) -> str:
    listed = "\n".join(f"- [{issue.code}] {issue.message}" for issue in issues)
    return (
        f"上一版文档没有通过校验，问题如下：\n{listed}\n\n"
        f"<上一版文档>\n{text}\n</上一版文档>\n\n"
        "请修正全部问题后重新输出完整的 skill 文档，不要输出解释。"
    )


@dataclass
class GenerationResult:
    """生成结果及其来路。"""

    text: str
    issues: List[Issue] = field(default_factory=list)
    rounds: int = 0
    used_llm: bool = False
    fell_back: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def errors(self) -> List[Issue]:
        return [issue for issue in self.issues if issue.severity == "error"]


def generate(
    context: StepSkillContext,
    client: Optional[LLMClient] = None,
    max_repairs: int = 2,
    on_round: Optional[Callable[[int, List[Issue]], None]] = None,
) -> GenerationResult:
    """出初稿 →（可选）过模型 → 一律校验 → 有错回灌重来 → 兜底回退。"""
    draft = render_markdown(context)
    allowed = context.allowed_commands
    if client is None:
        return GenerationResult(text=draft, issues=lint(draft, allowed), rounds=0, used_llm=False)

    system = build_system_prompt()
    prompt = build_prompt(context, draft)
    best: Optional[GenerationResult] = None
    for round_index in range(max_repairs + 1):
        text = client.complete(system, prompt).strip()
        issues = lint(text, allowed)
        errors = [issue for issue in issues if issue.severity == "error"]
        if on_round is not None:
            on_round(round_index + 1, issues)
        result = GenerationResult(text=text, issues=issues, rounds=round_index + 1, used_llm=True)
        if best is None or len(errors) < len(best.errors):
            best = result
        if not errors:
            return result
        prompt = build_repair_prompt(text, errors)

    assert best is not None
    draft_issues = lint(draft, allowed)
    if len(draft_issues) <= len(best.errors):
        return GenerationResult(
            text=draft,
            issues=draft_issues,
            rounds=best.rounds,
            used_llm=True,
            fell_back=True,
            notes=[f"模型输出仍有 {len(best.errors)} 个错误，已回退到程序初稿"],
        )
    best.notes.append(f"模型输出仍有 {len(best.errors)} 个错误，请人工确认")
    return best
