import json
import sys
import types

import pytest

from graph2skill.llm import (
    DEFAULT_MODEL,
    LLMError,
    OpenAICompatClient,
    StaticClient,
    build_prompt,
    build_system_prompt,
    chat_completions_url,
    evidence_pack,
    extract_markdown_content,
    generate,
    load_env_file,
    make_client,
    mask_secret,
    parse_sse_stream,
    resolve_model,
    strip_reasoning,
)
from graph2skill.loader import load_graph
from graph2skill.merge import merge_graphs
from graph2skill.stepskill import contexts_for_graph, render_markdown


@pytest.fixture()
def context(examples_dir):
    merged, _ = merge_graphs(
        [
            load_graph(examples_dir / "skillset" / "graphs" / "bgp.json"),
            load_graph(examples_dir / "incoming" / "bgp-route-flap.json"),
        ]
    )
    return contexts_for_graph(merged, fault_id="11")[0][1]


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nQWEN38_BASE_URL=http://model.internal:8000/v1\nQWEN38_API_KEY='sk-secret-value-123'\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("QWEN38_BASE_URL", raising=False)
    monkeypatch.delenv("QWEN38_API_KEY", raising=False)
    return path


# --- configuration --------------------------------------------------------------

def test_env_file_is_parsed_and_quotes_stripped(env_file):
    values = load_env_file(str(env_file))
    assert values["QWEN38_BASE_URL"] == "http://model.internal:8000/v1"
    assert values["QWEN38_API_KEY"] == "sk-secret-value-123"


def test_real_environment_wins_over_env_file(env_file, monkeypatch):
    monkeypatch.setenv("QWEN38_BASE_URL", "http://override:9000/v1")
    assert load_env_file(str(env_file))["QWEN38_BASE_URL"] == "http://override:9000/v1"


def test_resolve_model_builds_the_chat_completions_url(env_file):
    config = resolve_model(DEFAULT_MODEL, env_file=str(env_file))
    assert config.api_url == "http://model.internal:8000/v1/chat/completions"
    assert config.api_key == "sk-secret-value-123"
    assert config.max_tokens == 16384
    assert config.thinking is False
    assert config.registered


def test_env_max_tokens_and_explicit_override(env_file):
    env_file.write_text(env_file.read_text(encoding="utf-8") + "QWEN38_MAX_TOKENS=32768\n", encoding="utf-8")
    assert resolve_model(DEFAULT_MODEL, env_file=str(env_file)).max_tokens == 32768
    assert resolve_model(DEFAULT_MODEL, env_file=str(env_file), max_tokens=4096).max_tokens == 4096


def test_unregistered_model_falls_back_to_the_default_profile(env_file):
    config = resolve_model("some-new-model", env_file=str(env_file))
    assert not config.registered
    assert config.env_prefix == "QWEN38"


def test_missing_base_url_names_the_variable(tmp_path):
    with pytest.raises(LLMError) as excinfo:
        resolve_model(DEFAULT_MODEL, env_file=str(tmp_path / "absent.env"))
    assert "QWEN38_BASE_URL" in str(excinfo.value)


def test_explicit_api_url_wins(tmp_path):
    config = resolve_model(DEFAULT_MODEL, api_url="http://h:1/v1", env_file=str(tmp_path / "absent.env"))
    assert config.api_url == "http://h:1/v1/chat/completions"


def test_chat_completions_url_is_idempotent():
    assert chat_completions_url("http://h:1/v1/chat/completions") == "http://h:1/v1/chat/completions"
    assert chat_completions_url("") == ""


def test_model_host_is_added_to_no_proxy(env_file, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "")
    resolve_model(DEFAULT_MODEL, env_file=str(env_file))
    import os

    assert "model.internal" in os.environ["NO_PROXY"]


def test_secrets_are_masked():
    assert "sk-secret-value-123"[7:] not in mask_secret("sk-secret-value-123")
    assert mask_secret("") == "（无，按不鉴权处理）"


# --- response parsing -----------------------------------------------------------

def test_parse_sse_stream_joins_content_and_reasoning():
    stream = (
        'data: {"choices":[{"delta":{"content":"你"}}]}\n'
        'data: {"choices":[{"delta":{"content":"好","reasoning_content":"想"}}]}\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n'
        "data: [DONE]\n"
    )
    assert parse_sse_stream(stream) == ("你好", "想", "stop")


def test_parse_sse_stream_accepts_a_final_full_message():
    stream = 'data: {"choices":[{"message":{"content":"完整"},"finish_reason":"stop"}]}\n'
    assert parse_sse_stream(stream)[0] == "完整"


def test_parse_sse_stream_rejects_a_non_stream_body():
    with pytest.raises(LLMError):
        parse_sse_stream("not a stream")


def test_strip_reasoning_removes_closed_and_unclosed_blocks():
    assert strip_reasoning("<think>x</think>正文") == "正文"
    assert strip_reasoning("推理中<thinking>没有闭合") == "没有闭合"


def test_extract_markdown_content_handles_fences_and_bare_front_matter():
    assert extract_markdown_content("```markdown\n---\nname: a\n---\n```") == "---\nname: a\n---"
    assert extract_markdown_content("闲聊\n---\nname: a\n---\n正文").startswith("---\nname: a")
    assert extract_markdown_content("完全没有 skill 内容") == ""


# --- HTTP client ----------------------------------------------------------------

class _RequestException(Exception):
    def __init__(self, *args, response=None):
        super().__init__(*args)
        self.response = response


class _Timeout(_RequestException):
    pass


class _ConnectionError(_RequestException):
    pass


class _HTTPError(_RequestException):
    pass


class _FakeResponse:
    def __init__(self, payload="", headers=None, status=200, raise_http=False):
        self.text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        self._payload = payload
        self.headers = headers or {"Content-Type": "application/json"}
        self.status_code = status
        self.encoding = None
        self._raise = raise_http

    def raise_for_status(self):
        if self._raise:
            raise _HTTPError(response=self)

    def json(self):
        if isinstance(self._payload, str):
            raise ValueError("not json")
        return self._payload


def _install_fake_requests(monkeypatch, responses):
    calls = []

    def post(url, json=None, headers=None, timeout=None, verify=None):
        calls.append({"url": url, "json": json, "headers": headers})
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    module = types.ModuleType("requests")
    module.post = post
    exceptions = types.ModuleType("requests.exceptions")
    exceptions.RequestException = _RequestException
    exceptions.Timeout = _Timeout
    exceptions.ConnectionError = _ConnectionError
    exceptions.HTTPError = _HTTPError
    module.exceptions = exceptions
    monkeypatch.setitem(sys.modules, "requests", module)
    return calls


def test_client_posts_the_expected_payload(monkeypatch, env_file):
    calls = _install_fake_requests(
        monkeypatch, [_FakeResponse({"choices": [{"message": {"content": "内容"}, "finish_reason": "stop"}]})]
    )
    client = OpenAICompatClient.from_env(DEFAULT_MODEL, env_file=str(env_file))
    assert client.complete("系统", "用户") == "内容"
    payload = calls[0]["json"]
    assert payload["model"] == DEFAULT_MODEL
    assert payload["messages"][0]["role"] == "system"
    assert payload["stream"] is False
    assert payload["chat_template_kwargs"] == {"enable_thinking": False, "thinking": False}
    assert calls[0]["headers"]["Authorization"].startswith("Bearer sk-")


def test_thinking_models_do_not_get_the_disable_field(monkeypatch, env_file):
    calls = _install_fake_requests(
        monkeypatch, [_FakeResponse({"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]})]
    )
    client = OpenAICompatClient.from_env(DEFAULT_MODEL, env_file=str(env_file))
    client.config.thinking = True
    client.complete("s", "u")
    assert "chat_template_kwargs" not in calls[0]["json"]


def test_sse_response_is_decoded_as_utf8(monkeypatch, env_file):
    stream = 'data: {"choices":[{"delta":{"content":"中文"},"finish_reason":"stop"}]}\n'
    _install_fake_requests(monkeypatch, [_FakeResponse(stream, {"Content-Type": "text/event-stream"})])
    client = OpenAICompatClient.from_env(DEFAULT_MODEL, env_file=str(env_file))
    assert client.complete("s", "u") == "中文"


def test_truncated_output_reports_the_budget_variable(monkeypatch, env_file):
    _install_fake_requests(
        monkeypatch,
        [_FakeResponse({"choices": [{"message": {"content": "半截"}, "finish_reason": "length"}]})] * 3,
    )
    client = OpenAICompatClient.from_env(DEFAULT_MODEL, env_file=str(env_file))
    client.config.retry_delay = 0
    with pytest.raises(LLMError) as excinfo:
        client.complete("s", "u")
    assert "QWEN38_MAX_TOKENS" in str(excinfo.value)


def test_reasoning_only_response_is_an_error(monkeypatch, env_file):
    _install_fake_requests(
        monkeypatch,
        [_FakeResponse({"choices": [{"message": {"content": "", "reasoning_content": "想了很久"}, "finish_reason": "stop"}]})] * 3,
    )
    client = OpenAICompatClient.from_env(DEFAULT_MODEL, env_file=str(env_file))
    client.config.retry_delay = 0
    with pytest.raises(LLMError) as excinfo:
        client.complete("s", "u")
    assert "只返回了推理内容" in str(excinfo.value)


def test_non_json_body_error_carries_diagnostics(monkeypatch, env_file):
    _install_fake_requests(monkeypatch, [_FakeResponse("<html>502</html>")] * 3)
    client = OpenAICompatClient.from_env(DEFAULT_MODEL, env_file=str(env_file))
    client.config.retry_delay = 0
    with pytest.raises(LLMError) as excinfo:
        client.complete("s", "u")
    assert "不是 JSON" in str(excinfo.value)


def test_transient_failures_are_retried(monkeypatch, env_file):
    module_calls = _install_fake_requests(monkeypatch, [None, None])
    import requests  # the fake installed above

    module_calls.clear()
    responses = [
        requests.exceptions.Timeout("timeout"),
        _FakeResponse({"choices": [{"message": {"content": "第二次成功"}, "finish_reason": "stop"}]}),
    ]

    def post(url, json=None, headers=None, timeout=None, verify=None):
        module_calls.append(url)
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    requests.post = post
    client = OpenAICompatClient.from_env(DEFAULT_MODEL, env_file=str(env_file))
    client.config.retry_delay = 0
    assert client.complete("s", "u") == "第二次成功"
    assert len(module_calls) == 2


def test_make_client_rejects_an_unknown_provider():
    with pytest.raises(LLMError):
        make_client(provider="gemini")


# --- prompt and generation loop --------------------------------------------------

def test_evidence_pack_only_lists_source_commands(context):
    payload = json.loads(evidence_pack(context))
    assert payload["allowedCommands"] == context.allowed_commands
    assert payload["faultName"] == "BGP路由震荡"
    assert payload["causes"][-1]["name"] == "未找到根因"


def test_system_prompt_carries_the_template_spec():
    system = build_system_prompt()
    assert "只能使用证据包 allowedCommands 里出现过的 CLI" in system
    assert "# 根因对照表" in system


def test_prompt_contains_the_draft(context):
    prompt = build_prompt(context)
    assert "<证据包>" in prompt and "<程序初稿>" in prompt
    assert "name: bgp-route-flap" in prompt


def test_generate_without_a_client_returns_the_linted_draft(context):
    result = generate(context)
    assert not result.used_llm
    assert result.errors == []
    assert result.text == render_markdown(context)


def test_generate_accepts_a_clean_model_answer(context):
    improved = render_markdown(context).replace("检查是否为承载链路震荡", "确认承载链路是否震荡")
    client = StaticClient(responses=[improved])
    result = generate(context, client)
    assert result.used_llm and result.rounds == 1 and not result.fell_back
    assert "确认承载链路是否震荡" in result.text
    assert result.errors == []


def test_generate_feeds_lint_errors_back_and_recovers(context):
    broken = render_markdown(context).replace("## 步骤1：", "## 步骤5：")
    client = StaticClient(responses=[broken, render_markdown(context)])
    rounds = []
    result = generate(context, client, max_repairs=2, on_round=lambda index, issues: rounds.append(index))
    assert rounds == [1, 2]
    assert result.rounds == 2 and result.errors == []
    assert "校验" in client.prompts[1] and "step-numbering" in client.prompts[1]


def test_generate_falls_back_to_the_draft_when_the_model_keeps_failing(context):
    broken = "完全不符合模板的输出"
    client = StaticClient(responses=[broken, broken, broken])
    result = generate(context, client, max_repairs=2)
    assert result.fell_back
    assert result.text == render_markdown(context)
    assert result.errors == []
    assert any("回退到程序初稿" in note for note in result.notes)
