"""OpenAI-compatible provider layer (v1.6.4).

Covers the Anthropic<->OpenAI wire translation, the provider factory, the
provider-aware budget, and a full SQLWorker tool loop running against a
stubbed OpenAI-compatible client — the path a free Gemini/Groq key takes.
"""

import json
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, ".")

import config
from agent.budget import BudgetExceededError, BudgetGuardedLLMClient, estimate_cost_usd
from agent.llm_client import (
    ClaudeLLMClient,
    FakeLLMClient,
    OpenAICompatLLMClient,
    _from_openai_response,
    _to_openai_messages,
    _to_openai_tool,
    build_llm_client,
)
from agent.workers import create_sql_worker
from db.database import reset_db
from db.seed import seed_demo_data


# ---------------------------------------------------------------- helpers

def _completion(text=None, tool_calls=None, prompt_tokens=10, completion_tokens=5):
    """Stub an OpenAI chat.completion response with SimpleNamespace."""
    message = SimpleNamespace(content=text, tool_calls=None)
    if tool_calls:
        message.tool_calls = [
            SimpleNamespace(
                id=t["id"],
                function=SimpleNamespace(name=t["name"], arguments=json.dumps(t["arguments"])),
            )
            for t in tool_calls
        ]
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="tool_calls" if tool_calls else "stop")],
        usage=SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens),
    )


def _stub_client(responses, capture):
    """An object shaped like openai.OpenAI().chat.completions for tests."""
    def create(**kwargs):
        capture.append(kwargs)
        return responses.pop(0)

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


# ------------------------------------------------------- wire translation

def test_tool_schema_translates_to_openai_function_shape():
    schema = {
        "name": "sql_tool",
        "description": "Execute SQL.",
        "input_schema": {"type": "object", "properties": {"sql": {"type": "string"}}},
    }
    assert _to_openai_tool(schema) == {
        "type": "function",
        "function": {
            "name": "sql_tool",
            "description": "Execute SQL.",
            "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}},
        },
    }


def test_plain_string_messages_pass_through():
    out = _to_openai_messages([
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ])
    assert out == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_assistant_tool_use_blocks_translate_to_tool_calls():
    out = _to_openai_messages([
        {"role": "assistant", "content": [
            {"type": "text", "text": "Checking."},
            {"type": "tool_use", "id": "call_1", "name": "sql_tool", "input": {"sql": "SELECT 1"}},
        ]},
    ])
    assert out == [{
        "role": "assistant",
        "content": "Checking.",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {"name": "sql_tool", "arguments": '{"sql": "SELECT 1"}'},
        }],
    }]


def test_assistant_legacy_block_keys_still_translate():
    out = _to_openai_messages([
        {"role": "assistant", "content": [
            {"type": "tool_use", "tool_use_id": "call_9", "tool_name": "calculator",
             "tool_input": {"expression": "2+2"}},
        ]},
    ])
    assert out[0]["tool_calls"][0]["id"] == "call_9"
    assert out[0]["tool_calls"][0]["function"]["name"] == "calculator"


def test_tool_result_blocks_translate_to_role_tool():
    out = _to_openai_messages([
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "10 rows", "is_error": False},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_2", "content": "boom", "is_error": True},
        ]},
    ])
    assert out == [
        {"role": "tool", "tool_call_id": "call_1", "content": "10 rows"},
        {"role": "tool", "tool_call_id": "call_2", "content": "ERROR: boom"},
    ]


def test_response_text_only_maps_to_end_turn():
    response = _completion(text="All done.", prompt_tokens=42, completion_tokens=7)
    result = _from_openai_response(response)
    assert result.stop_reason == "end_turn"
    assert result.text == "All done."
    assert result.input_tokens == 42 and result.output_tokens == 7


def test_response_tool_calls_map_to_tool_use_blocks():
    response = _completion(
        tool_calls=[{"id": "call_5", "name": "sql_tool", "arguments": {"sql": "SELECT 2"}}],
        prompt_tokens=30,
        completion_tokens=12,
    )
    result = _from_openai_response(response)
    assert result.stop_reason == "tool_use"
    assert len(result.tool_calls) == 1
    block = result.tool_calls[0]
    assert block.tool_use_id == "call_5"
    assert block.tool_name == "sql_tool"
    assert block.tool_input == {"sql": "SELECT 2"}


# ------------------------------------------------------------ client call

def test_client_call_sends_system_tools_and_model():
    capture = []
    stub = _stub_client([_completion(text="ok")], capture)
    client = OpenAICompatLLMClient(
        api_key="k", model="gemini-2.5-flash", base_url="https://x/v1", client=stub
    )
    result = client.call(
        "You are a router.",
        [{"role": "user", "content": "hi"}],
        tools=[{"name": "t", "description": "d", "input_schema": {"type": "object", "properties": {}}}],
    )
    assert result.text == "ok"
    body = capture[0]
    assert body["model"] == "gemini-2.5-flash"
    assert body["messages"][0] == {"role": "system", "content": "You are a router."}
    assert body["tools"][0]["function"]["name"] == "t"


# ------------------------------------------------- worker-loop integration

def test_sql_worker_completes_on_openai_compat_client():
    reset_db()
    seed_demo_data()
    capture = []
    stub = _stub_client(
        [
            _completion(
                tool_calls=[{"id": "call_1", "name": "sql_tool",
                             "arguments": {"sql": "SELECT COUNT(*) AS cnt FROM customers"}}],
                prompt_tokens=60, completion_tokens=25,
            ),
            _completion(text="There are 10 customers."),
        ],
        capture,
    )
    client = OpenAICompatLLMClient(api_key="k", client=stub)
    worker = create_sql_worker(llm_client=client)
    answer = worker.run("How many customers are there?")

    assert answer == "There are 10 customers."
    # the second request carried the tool result back as a role:"tool" message
    assert any(
        m.get("role") == "tool" and m.get("tool_call_id") == "call_1"
        for m in capture[1]["messages"]
    )


# ---------------------------------------------------------------- factory

def test_factory_anthropic_path(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-test")
    assert isinstance(build_llm_client(), ClaudeLLMClient)


def test_factory_anthropic_without_key_returns_none(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    assert build_llm_client() is None


def test_factory_gemini_preset(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_API_KEY", "g-key")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    client = build_llm_client()
    assert isinstance(client, OpenAICompatLLMClient)
    assert client._model == "gemini-2.5-flash"
    assert client._base_url == "https://generativelanguage.googleapis.com/v1beta/openai"


def test_factory_gemini_without_key_returns_none(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    assert build_llm_client() is None


def test_factory_groq_preset(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "groq")
    monkeypatch.setattr(config, "LLM_API_KEY", "gsk-test")
    monkeypatch.setattr(config, "LLM_MODEL", "")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    client = build_llm_client()
    assert client._model == "llama-3.3-70b-versatile"
    assert client._base_url == "https://api.groq.com/openai/v1"


def test_factory_openai_compat_requires_base_url(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai-compat")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    monkeypatch.setattr(config, "LLM_BASE_URL", "")
    with pytest.raises(ValueError, match="LLM_BASE_URL"):
        build_llm_client()


def test_factory_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(config, "LLM_PROVIDER", "openrouter")
    monkeypatch.setattr(config, "LLM_API_KEY", "k")
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        build_llm_client()


# ----------------------------------------------------------------- budget

def test_free_tier_providers_estimate_zero_cost():
    assert estimate_cost_usd(1_000_000, 1_000_000, provider="gemini") == 0.0
    assert estimate_cost_usd(1_000_000, 1_000_000, provider="groq") == 0.0
    assert estimate_cost_usd(1_000_000, 1_000_000, provider="anthropic") == 18.0


def test_rate_card_env_override(monkeypatch):
    monkeypatch.setenv("BUDGET_RATE_CARD_USD_PER_M", "0.5,1.5")
    assert estimate_cost_usd(1_000_000, 1_000_000, provider="gemini") == 2.0


def test_budget_wrapper_uses_provider_rate_card():
    inner = FakeLLMClient([])
    guard = BudgetGuardedLLMClient(inner, provider="gemini")
    assert guard.provider == "gemini"
    guard.call(system="s", messages=[{"role": "user", "content": "x"}])
    assert guard.total_tokens == 20  # FakeLLMClient interception tokens


def test_budget_wrapper_still_enforces_token_budget():
    inner = FakeLLMClient([])
    guard = BudgetGuardedLLMClient(inner, provider="gemini")
    guard.total_input_tokens = config.SESSION_MAX_TOKENS  # simulate a full session
    with pytest.raises(BudgetExceededError):
        guard.call(system="s", messages=[{"role": "user", "content": "x"}])
