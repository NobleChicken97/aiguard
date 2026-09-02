import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
import config


@dataclass
class ContentBlock:
    """One block of an LLM response.

    Mirrors the Anthropic SDK's content-block shape: a ``text`` block
    for prose, a ``tool_use`` block when the model wants to call a tool.
    Only one of ``text`` or the tool fields is meaningful per instance.
    """
    type: str
    text: Optional[str] = None
    tool_use_id: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, d):
        return cls(
            type=d.get("type"),
            text=d.get("text"),
            tool_use_id=d.get("id"),
            tool_name=d.get("name"),
            tool_input=d.get("input"),
        )

    def to_dict(self):
        """Serialize to the Anthropic Messages API wire format.

        Assistant ``tool_use`` blocks must use the keys ``id``/``name``/
        ``input`` — emitting the internal field names (``tool_use_id`` etc.)
        makes the next ``messages.create`` call fail with a 400 once the
        block is appended back into the conversation (workers do exactly
        that after executing a tool).
        """
        d = {"type": self.type}
        if self.text is not None:
            d["text"] = self.text
        if self.tool_use_id is not None:
            d["id"] = self.tool_use_id
        if self.tool_name is not None:
            d["name"] = self.tool_name
        if self.tool_input is not None:
            d["input"] = self.tool_input
        return d


@dataclass
class LLMResponse:
    """A normalized LLM response.

    Wraps the SDK response so the rest of the system (orchestrator,
    workers, tests) can depend on a single shape independent of provider.
    ``stop_reason`` is ``"end_turn"`` for plain text replies and
    ``"tool_use"`` when ``content`` contains tool calls.
    """
    stop_reason: str
    content: List[ContentBlock]
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def text(self):
        parts = [b.text for b in self.content if b.type == "text" and b.text]
        return "\n".join(parts)

    @property
    def tool_calls(self):
        return [b for b in self.content if b.type == "tool_use"]


class ClaudeLLMClient:
    """Anthropic Claude client used by the orchestrator and workers.

    Lazy-imports the ``anthropic`` SDK on first use so unit tests that
    never call ``call()`` (e.g. ones that swap in ``FakeLLMClient``) do
    not require the package to be importable at module load time.
    """

    def __init__(self, api_key=None, model=None):
        self._api_key = api_key or config.ANTHROPIC_API_KEY
        self._model = model or config.CLAUDE_MODEL
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def call(self, system, messages, tools=None):
        kwargs = {
            "model": self._model,
            "max_tokens": config.MAX_TOKENS,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        content = [ContentBlock.from_dict(b) for b in response.content]
        return LLMResponse(
            stop_reason=response.stop_reason,
            content=content,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


class FakeLLMClient:
    """Scripted LLM client for testing. Returns canned responses in sequence.

    Supervisor routing prompts are intercepted (never consume scripted
    responses) and answered with ``route_decision`` — "SQL" by default, or
    "RESEARCH" to exercise the research-worker path in tests. Memory
    distillation prompts are intercepted the same way: they return
    ``distill_facts`` lines (default: none), or scripted responses when
    ``distill_facts`` is explicitly provided.
    """

    def __init__(self, responses, route_decision="SQL", distill_facts=None):
        self._responses = list(responses)
        self._index = 0
        self.route_decision = route_decision
        self.distill_facts = distill_facts

    def call(self, system, messages, tools=None):
        lowered = system.lower()
        if "router" in lowered:
            # Intercept router prompt so we don't consume a scripted response
            return LLMResponse(
                stop_reason="end_turn",
                content=[ContentBlock(type="text", text=self.route_decision)],
                input_tokens=10,
                output_tokens=10,
            )

        if "factual statements" in lowered:
            text = "\n".join(self.distill_facts) if self.distill_facts else ""
            return LLMResponse(
                stop_reason="end_turn",
                content=[ContentBlock(type="text", text=text)],
                input_tokens=10,
                output_tokens=10,
            )

        if self._index >= len(self._responses):
            return LLMResponse(
                stop_reason="end_turn",
                content=[ContentBlock(type="text", text="I have no more scripted responses.")],
                input_tokens=10,
                output_tokens=10,
            )
        resp = self._responses[self._index]
        self._index += 1
        return resp

    @staticmethod
    def text_response(text):
        return LLMResponse(
            stop_reason="end_turn",
            content=[ContentBlock(type="text", text=text)],
            input_tokens=10,
            output_tokens=20,
        )

    @staticmethod
    def tool_use_response(tool_name, tool_input, tool_use_id="toolu_fake"):
        return LLMResponse(
            stop_reason="tool_use",
            content=[
                ContentBlock(type="text", text=f"Let me use {tool_name}."),
                ContentBlock(
                    type="tool_use",
                    tool_use_id=tool_use_id,
                    tool_name=tool_name,
                    tool_input=tool_input,
                ),
            ],
            input_tokens=10,
            output_tokens=30,
        )


# ---------------------------------------------------------------------------
# Provider layer (v1.6.4): run the same agent loops on free-tier,
# OpenAI-compatible providers (Gemini, Groq, NVIDIA NIM, ...) instead of or
# alongside Anthropic. Presets verified against provider docs (2026-09).
# ---------------------------------------------------------------------------

PROVIDER_PRESETS = {
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.5-flash",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        # llama-3.3-70b-versatile was decommissioned by Groq (verified live
        # 2026-09-03 against the key's /models list); gpt-oss-120b is the
        # strongest remaining tool-calling chat model on the free tier.
        "default_model": "openai/gpt-oss-120b",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "default_model": "meta/llama-3.3-70b-instruct",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
}


def _to_openai_tool(schema):
    """Project tool schema ({name, description, input_schema}) -> OpenAI function tool."""
    return {
        "type": "function",
        "function": {
            "name": schema["name"],
            "description": schema.get("description", ""),
            "parameters": schema.get("input_schema") or {"type": "object", "properties": {}},
        },
    }


def _to_openai_messages(messages):
    """Translate project messages (Anthropic-shaped) to OpenAI chat format.

    - Plain-string user/assistant messages pass through.
    - Assistant content-block lists become one assistant message whose
      tool_use blocks (keys ``id``/``name``/``input`` after the v1.6.1 wire
      fix, legacy ``tool_use_id``/``tool_name``/``tool_input`` accepted)
      turn into OpenAI ``tool_calls`` with JSON-string arguments.
    - User tool_result blocks become ``role:"tool"`` messages keyed by
      ``tool_call_id`` — one per result, order preserved.
    """
    out = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        blocks = content or []
        if role == "assistant":
            text = "\n".join(
                b.get("text", "") for b in blocks if b.get("type") == "text" and b.get("text")
            )
            entry = {"role": "assistant", "content": text or None}
            tool_calls = []
            for b in blocks:
                if b.get("type") != "tool_use":
                    continue
                tool_calls.append(
                    {
                        "id": b.get("tool_use_id") or b.get("id"),
                        "type": "function",
                        "function": {
                            "name": b.get("tool_name") or b.get("name"),
                            "arguments": json.dumps(
                                b.get("tool_input") or b.get("input") or {},
                                default=str,
                            ),
                        },
                    }
                )
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
        else:  # user message carrying tool_result / text blocks
            for b in blocks:
                if b.get("type") == "tool_result":
                    text = str(b.get("content", ""))
                    if b.get("is_error"):
                        text = f"ERROR: {text}"
                    out.append(
                        {"role": "tool", "tool_call_id": b.get("tool_use_id"), "content": text}
                    )
                elif b.get("type") == "text" and b.get("text"):
                    out.append({"role": "user", "content": b["text"]})
    return out


def _from_openai_response(response):
    """Map an OpenAI chat completion onto the internal LLMResponse shape."""
    choice = response.choices[0]
    message = choice.message
    blocks = []
    if message.content:
        blocks.append(ContentBlock(type="text", text=message.content))
    tool_calls = getattr(message, "tool_calls", None) or []
    for tc in tool_calls:
        raw_arguments = tc.function.arguments or "{}"
        try:
            tool_input = json.loads(raw_arguments)
        except (TypeError, ValueError):
            tool_input = {"_raw_arguments": raw_arguments}
        blocks.append(
            ContentBlock(
                type="tool_use",
                tool_use_id=tc.id,
                tool_name=tc.function.name,
                tool_input=tool_input,
            )
        )
    usage = getattr(response, "usage", None)
    return LLMResponse(
        stop_reason="tool_use" if tool_calls else "end_turn",
        content=blocks,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
    )


class OpenAICompatLLMClient:
    """Client for any OpenAI-compatible chat-completions endpoint.

    The supervisor/worker loops depend only on ``call(system, messages,
    tools)`` returning an ``LLMResponse``, so this adapter makes the whole
    agent (routing, tool loops, budgets) provider-agnostic. Free-tier
    presets: gemini (AI Studio key), groq, nvidia.
    """

    def __init__(self, api_key=None, model=None, base_url=None, client=None):
        self._api_key = api_key or config.LLM_API_KEY
        self._model = model or config.LLM_MODEL or "gpt-4o-mini"
        self._base_url = base_url or config.LLM_BASE_URL or None
        if client is not None:
            # Test seam: a pre-built client object (stub or real OpenAI()).
            self._client = client
        else:
            from openai import OpenAI

            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)

    def call(self, system, messages, tools=None):
        body_messages = []
        if system:
            body_messages.append({"role": "system", "content": system})
        body_messages.extend(_to_openai_messages(messages))

        kwargs = {
            "model": self._model,
            "max_tokens": config.MAX_TOKENS,
            "messages": body_messages,
        }
        if tools:
            kwargs["tools"] = [_to_openai_tool(t) for t in tools]

        response = self._client.chat.completions.create(**kwargs)
        return _from_openai_response(response)


def build_llm_client():
    """Factory honoring ``config.LLM_PROVIDER``; returns None without a key.

    "anthropic" (default) needs ANTHROPIC_API_KEY; every other provider needs
    LLM_API_KEY (plus LLM_BASE_URL when provider is "openai-compat"). Model
    and base URL default from the provider preset unless overridden.
    """
    provider = (config.LLM_PROVIDER or "anthropic").strip().lower()
    if provider == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            return None
        return ClaudeLLMClient()

    preset = PROVIDER_PRESETS.get(provider)
    if preset is None and provider != "openai-compat":
        raise ValueError(
            f"Unsupported LLM_PROVIDER '{provider}'."
            f" Known: anthropic, openai-compat, {', '.join(sorted(PROVIDER_PRESETS))}."
        )
    if preset is None and not config.LLM_BASE_URL:
        raise ValueError("LLM_PROVIDER=openai-compat requires LLM_BASE_URL.")
    if not config.LLM_API_KEY:
        return None
    return OpenAICompatLLMClient(
        api_key=config.LLM_API_KEY,
        model=config.LLM_MODEL or (preset or {}).get("default_model"),
        base_url=config.LLM_BASE_URL or (preset or {}).get("base_url"),
    )
