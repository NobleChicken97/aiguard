"""Regression tests for the ContentBlock <-> Anthropic wire format.

The Anthropic Messages API requires assistant ``tool_use`` blocks to carry
``id``/``name``/``input``. A previous regression emitted the internal field
names (``tool_use_id``/``tool_name``/``tool_input``), which broke every
real-API tool loop on the second LLM call — invisible to tests because
``FakeLLMClient`` never validates block shapes.
"""

from agent.llm_client import ContentBlock


def test_tool_use_block_serializes_to_api_shape():
    block = ContentBlock(
        type="tool_use",
        tool_use_id="toolu_1",
        tool_name="sql_tool",
        tool_input={"sql": "SELECT 1"},
    )
    d = block.to_dict()
    assert d == {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "sql_tool",
        "input": {"sql": "SELECT 1"},
    }


def test_text_block_serializes_to_api_shape():
    assert ContentBlock(type="text", text="hello").to_dict() == {
        "type": "text",
        "text": "hello",
    }


def test_tool_use_block_round_trips_through_wire_format():
    block = ContentBlock(
        type="tool_use",
        tool_use_id="toolu_2",
        tool_name="calculator",
        tool_input={"expression": "2+2"},
    )
    restored = ContentBlock.from_dict(block.to_dict())
    assert restored == block


def test_worker_assistant_round_trip_is_api_valid():
    """Simulates the worker loop: response content -> to_dict -> from_dict."""
    from agent.llm_client import LLMResponse

    response = LLMResponse(
        stop_reason="tool_use",
        content=[
            ContentBlock(type="text", text="Let me check."),
            ContentBlock(
                type="tool_use",
                tool_use_id="toolu_3",
                tool_name="sql_tool",
                tool_input={"sql": "SELECT city FROM customers"},
            ),
        ],
    )
    serialized = [b.to_dict() for b in response.content]
    for block in serialized:
        if block["type"] == "tool_use":
            assert set(block) == {"type", "id", "name", "input"}
        else:
            assert set(block) == {"type", "text"}
    reparsed = [ContentBlock.from_dict(b) for b in serialized]
    assert reparsed == response.content
