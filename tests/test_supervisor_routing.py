"""Supervisor routing: structured JSON contract (Phase 4).

Contract change from v1.6.1 (recorded here, not a regression): the router
now asks for JSON {"route", "confidence", "reasoning"} and only clean
single-token legacy replies ("SQL"/"RESEARCH") still route directly.
Anything else — empty replies, chatter like "RESEARCH (not SQL)" — asks
the user to clarify instead of guessing a worker. The old first-token
fallback routed garbage with full confidence; the new code refuses to.
"""

from agent.llm_client import ContentBlock, FakeLLMClient, LLMResponse
from agent.supervisor import CLARIFY_TEXT, SupervisorAgent


class _StubClient:
    """Returns a fixed router reply (or none at all) for the routing call."""

    def __init__(self, text):
        self._text = text

    def call(self, system, messages, tools=None):
        content = [] if self._text is None else [ContentBlock(type="text", text=self._text)]
        return LLMResponse(stop_reason="end_turn", content=content)


def test_reply_without_text_block_asks_to_clarify():
    decision = SupervisorAgent(llm_client=_StubClient(None)).route("What is 15 times 37?")
    assert decision.worker == "clarify"
    assert decision.confidence == 0.0
    assert decision.tier == "unparseable"


def test_chattery_reply_asks_to_clarify_not_research():
    # v1.6.1 pinned first-token routing for "RESEARCH (not SQL)". Phase 4
    # deliberately supersedes it: a reply ignoring the JSON format is a
    # weak-following signal, so we ask instead of trusting token one.
    decision = SupervisorAgent(llm_client=_StubClient("RESEARCH (not SQL)")).route(
        "Tell me about the history of computing."
    )
    assert decision.worker == "clarify"


def test_plain_sql_reply_routes_to_sql_worker():
    decision = SupervisorAgent(llm_client=_StubClient("SQL")).route(
        "How many customers are in New York?"
    )
    assert (decision.worker, decision.confidence, decision.tier) == (
        "SQLWorker",
        1.0,
        "legacy",
    )


def test_structured_json_routes_with_claimed_confidence():
    stub = _StubClient('{"route": "RESEARCH", "confidence": 0.9, "reasoning": "needs the web"}')
    decision = SupervisorAgent(llm_client=stub).route("Who created Python?")
    assert decision.worker == "ResearchWorker"
    assert decision.confidence == 0.9
    assert decision.reasoning == "needs the web"
    assert decision.tier == "structured"


def test_embedded_json_is_extracted():
    stub = _StubClient(
        'Sure: {"route": "SQL", "confidence": 0.8, "reasoning": "mentions orders"} thanks'
    )
    decision = SupervisorAgent(llm_client=stub).route("Show me the orders.")
    assert (decision.worker, decision.tier) == ("SQLWorker", "extracted")


def test_low_confidence_json_defers_to_clarification():
    stub = _StubClient('{"route": "SQL", "confidence": 0.2, "reasoning": "unsure"}')
    agent = SupervisorAgent(llm_client=stub)
    assert agent.route("vague question").needs_clarification
    assert agent.run("vague question") == CLARIFY_TEXT


def test_invalid_structured_replies_ask_to_clarify():
    for bad in [
        '{"route": "SQL", "confidence": 5}',  # out of range
        '{"route": "DELETE", "confidence": 0.9}',  # unknown route
        '{"confidence": 0.9}',  # missing route
        "[1, 2, 3]",  # not an object
        "not json at all",
    ]:
        decision = SupervisorAgent(llm_client=_StubClient(bad)).route("anything")
        assert decision.worker == "clarify", bad


def test_fakellmclient_default_routing_still_targets_sql():
    decision = SupervisorAgent(llm_client=FakeLLMClient([])).route("anything")
    assert decision.worker == "SQLWorker"
