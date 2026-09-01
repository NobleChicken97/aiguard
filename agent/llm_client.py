from dataclasses import dataclass, field
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

    def to_message(self):
        return {"role": "assistant", "content": [b.to_dict() for b in self.content]}


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
    "RESEARCH" to exercise the research-worker path in tests.
    """

    def __init__(self, responses, route_decision="SQL"):
        self._responses = list(responses)
        self._index = 0
        self.call_count = 0
        self.route_decision = route_decision

    def call(self, system, messages, tools=None):
        if "router" in system.lower():
            # Intercept router prompt so we don't consume a scripted response
            return LLMResponse(
                stop_reason="end_turn",
                content=[ContentBlock(type="text", text=self.route_decision)],
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
        self.call_count += 1
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

    @staticmethod
    def multi_tool_use_response(calls):
        content = [ContentBlock(type="text", text="Let me use multiple tools.")]
        for name, inp, uid in calls:
            content.append(
                ContentBlock(
                    type="tool_use",
                    tool_use_id=uid,
                    tool_name=name,
                    tool_input=inp,
                )
            )
        return LLMResponse(
            stop_reason="tool_use",
            content=content,
            input_tokens=10,
            output_tokens=40,
        )
