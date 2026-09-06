"""Answer-integrity tripwire (Sep 2026 live finding).

An LLM can assert a destructive action "was executed" with no tool call
behind it. Nothing blocks prose, so the tripwire detects the mismatch
(claim present + no successful mutation in the session trace) and logs an
`unverified_execution_claim` event. All tests hermetic (FakeLLMClient).
"""

import sys

import pytest

sys.path.insert(0, ".")

from agent.integrity import claims_execution, session_mutation_executed
from agent.llm_client import FakeLLMClient
from agent.memory import DISTILL_SYSTEM_PROMPT
from agent.orchestrator import Orchestrator
from agent.trace import TraceLogger, get_session_trace
from approval.gate import AutoApproveHandler
from db.database import get_connection, reset_db
from db.seed import seed_demo_data


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _events(session_id, event_type):
    return [
        e for e in get_session_trace(session_id) if e["event_type"] == event_type
    ]


def test_claims_execution_matches_incident_phrasing():
    assert claims_execution(
        "This reflects the current row count after the previously approved "
        "DELETE FROM orders WHERE id > 5 statement was executed."
    )
    assert claims_execution("Done, the city has been updated to Springfield.")
    assert claims_execution("The table was dropped successfully.")
    assert claims_execution("All rows were deleted.")


def test_claims_execution_ignores_benign_answers():
    assert claims_execution("There are 10 customers in the database.") is None
    assert claims_execution("15 × 37 = 555.") is None
    assert claims_execution("Hello! How can I help?") is None
    assert claims_execution("") is None
    assert claims_execution(None) is None


def test_mutation_check_reads_verdicts_not_prose():
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at) VALUES (?, ?, ?)",
            ("sess-mut-1", "u", "2026-09-06T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    trace = TraceLogger("sess-mut-1")
    try:
        # Successful SELECT only: no mutation.
        trace.log_tool_call("sql_tool", {"sql": "SELECT 1"}, call_id="c-sel")
        trace.log_guardrail_verdict("c-sel", "SELECT 1", "ALLOWED", "")
        trace.log_tool_result("c-sel", "sql_tool", "success", "1")
        assert session_mutation_executed("sess-mut-1") is False

        # Successful UPDATE: mutation.
        trace.log_tool_call("sql_tool", {"sql": "UPDATE x SET a=1"}, call_id="c-upd")
        trace.log_guardrail_verdict("c-upd", "UPDATE x SET a=1", "ALLOWED", "")
        trace.log_tool_result("c-upd", "sql_tool", "success", "1 row affected")
        assert session_mutation_executed("sess-mut-1") is True
    finally:
        trace.close()


def test_mutation_check_ignores_failed_writes():
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at) VALUES (?, ?, ?)",
            ("sess-mut-2", "u", "2026-09-06T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    trace = TraceLogger("sess-mut-2")
    try:
        trace.log_tool_call("sql_tool", {"sql": "DELETE FROM x"}, call_id="c-del")
        trace.log_guardrail_verdict("c-del", "DELETE FROM x", "ALLOWED", "")
        trace.log_tool_result("c-del", "sql_tool", "failed", "boom")
        assert session_mutation_executed("sess-mut-2") is False
    finally:
        trace.close()


def test_unverified_claim_logged_end_to_end():
    """Replays the incident shape: final answer claims execution, trace has
    no mutating tool call -> tripwire event present."""
    orch = Orchestrator(
        llm_client=FakeLLMClient(
            [FakeLLMClient.text_response("The table was dropped successfully.")],
            route_decision="RESEARCH",
        ),
        approval_handler=AutoApproveHandler(),
        user_id="integrity_user",
    )
    out = orch.run("drop stuff")
    assert "dropped successfully" in out
    flagged = _events(orch.session_id, "unverified_execution_claim")
    assert len(flagged) == 1
    assert "dropped" in flagged[0]["data"]["matched"].lower()


def test_verified_write_does_not_trip():
    """A real approved UPDATE followed by 'was updated' prose: no event."""
    orch = Orchestrator(
        llm_client=FakeLLMClient(
            [
                FakeLLMClient.tool_use_response(
                    "sql_tool",
                    {"sql": "UPDATE customers SET city = 'X' WHERE id = 1"},
                    "toolu_mut1",
                ),
                FakeLLMClient.text_response("Done, the city has been updated."),
            ],
            route_decision="SQL",
        ),
        approval_handler=AutoApproveHandler(),
        user_id="integrity_user2",
    )
    out = orch.run("set city for customer 1")
    assert "updated" in out
    assert _events(orch.session_id, "unverified_execution_claim") == []


def test_distill_prompt_excludes_system_action_claims():
    # Pinned instruction (Sep 2026 finding): memory must not persist claims
    # about approvals/executions. Keeps the "factual statements" hook the
    # FakeLLMClient distillation intercept matches on.
    assert "factual statements" in DISTILL_SYSTEM_PROMPT
    assert "do NOT record" in DISTILL_SYSTEM_PROMPT
    assert "approv" in DISTILL_SYSTEM_PROMPT
