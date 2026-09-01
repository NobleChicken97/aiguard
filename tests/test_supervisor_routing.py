"""Supervisor routing hardening tests.

Routing used to match the substring "SQL" anywhere in the router reply (so
"RESEARCH (not SQL)" routed to the SQL worker) and crashed on replies with
no text block. It now matches the first token and degrades safely.
"""

from agent.llm_client import ContentBlock, FakeLLMClient, LLMResponse
from agent.supervisor import SupervisorAgent


class _StubClient:
    """Returns a fixed router reply (or none at all) for the routing call."""

    def __init__(self, text):
        self._text = text

    def call(self, system, messages, tools=None):
        content = [] if self._text is None else [ContentBlock(type="text", text=self._text)]
        return LLMResponse(stop_reason="end_turn", content=content)


def test_reply_without_text_block_defaults_to_sql_worker():
    supervisor = SupervisorAgent(llm_client=_StubClient(None))
    assert supervisor.route("What is 15 times 37?") == "SQLWorker"


def test_research_reply_with_sql_substring_routes_to_research():
    """Substring matching would send 'RESEARCH (not SQL)' to the SQL worker."""
    supervisor = SupervisorAgent(llm_client=_StubClient("RESEARCH (not SQL)"))
    assert supervisor.route("Tell me about the history of computing.") == "ResearchWorker"


def test_plain_sql_reply_routes_to_sql_worker():
    supervisor = SupervisorAgent(llm_client=_StubClient("SQL"))
    assert supervisor.route("How many customers are in New York?") == "SQLWorker"


def test_fakellmclient_default_routing_still_targets_sql():
    supervisor = SupervisorAgent(llm_client=FakeLLMClient([]))
    assert supervisor.route("anything") == "SQLWorker"
