"""Fail-closed behavior for the affected-row approval gate.

``SQLTool._estimate_affected_rows`` can return ``None`` when the row count
cannot be computed. That used to skip the bulk-operation gate entirely
(fail-open); it now requires approval like any other bulk write.

Approval rows carry a FK on ``app_tool_calls.call_id``, so tests seed the
session/tool-call rows first — the same rows ``record_tool_call`` writes on
the real worker path.
"""

import uuid

import pytest

from approval.gate import AutoApproveHandler, AutoDenyHandler
from db.database import get_connection, reset_db
from db.seed import seed_demo_data
from tools.sql_tool import SQLTool

BULK_UPDATE = "UPDATE products SET stock = 0 WHERE category = 'Electronics'"


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _seed_call(session_id, call_id):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            (session_id, "rowcount_user", "2026-09-01T00:00:00+00:00", "active"),
        )
        conn.execute(
            """INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at)
               VALUES (?, ?, 'sql_tool', ?, 'pending_approval', ?)""",
            (call_id, session_id, '{"sql": "%s"}' % BULK_UPDATE, "2026-09-01T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _denied_when_human_says_no(monkeypatch):
    session_id, call_id = f"sess-{uuid.uuid4()}", f"call-{uuid.uuid4()}"
    _seed_call(session_id, call_id)
    tool = SQLTool(approval_handler=AutoDenyHandler())
    monkeypatch.setattr(tool, "_estimate_affected_rows", lambda sql: None)
    return tool.execute(sql=BULK_UPDATE, _call_id=call_id, _session_id=session_id)


def test_unestimatable_row_count_is_denied_when_human_says_no(monkeypatch):
    result = _denied_when_human_says_no(monkeypatch)
    assert result.status == "denied"
    assert result.guardrail_verdict == "REQUIRES_APPROVAL"
    assert "could not be estimated" in result.output


def test_unestimatable_row_count_executes_after_approval(monkeypatch):
    session_id, call_id = f"sess-{uuid.uuid4()}", f"call-{uuid.uuid4()}"
    _seed_call(session_id, call_id)
    tool = SQLTool(approval_handler=AutoApproveHandler())
    monkeypatch.setattr(tool, "_estimate_affected_rows", lambda sql: None)
    result = tool.execute(sql=BULK_UPDATE, _call_id=call_id, _session_id=session_id)
    assert result.status == "success"


def test_unestimatable_row_count_blocked_without_handler(monkeypatch):
    tool = SQLTool(approval_handler=None)
    monkeypatch.setattr(tool, "_estimate_affected_rows", lambda sql: None)
    result = tool.execute(sql=BULK_UPDATE)
    assert result.status == "blocked"
    assert "no handler configured" in result.output


def test_small_scoped_update_still_runs_without_approval():
    """The normal path is unchanged: a provably small UPDATE needs no gate."""
    tool = SQLTool(approval_handler=None)
    result = tool.execute(sql="UPDATE products SET stock = 5 WHERE id = 1")
    assert result.status == "success"
