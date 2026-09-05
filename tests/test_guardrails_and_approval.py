from uuid import uuid4

import sys

import pytest

sys.path.insert(0, ".")

from approval.gate import AutoApproveHandler, AutoDenyHandler, get_pending_approvals, resolve_approval
from db.database import get_connection, reset_db
from db.seed import seed_demo_data
from guardrails.sql_guardrail import SQLGuardrail, VERDICT_BLOCKED
from tools.sql_tool import SQLTool


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers;",
        "drop table customers;",
        "DROP TABLE IF EXISTS customers;",
        "TRUNCATE TABLE orders;",
        "truncate table order_items;",
        "ALTER TABLE customers ADD COLUMN phone TEXT;",
        "alter table orders rename to archived_orders;",
        "CREATE TABLE hacked(id INTEGER);",
        "create view hacked_view as select * from customers;",
        "DELETE FROM customers;",
        "delete from orders;",
        "UPDATE customers SET city = 'Berlin';",
        "update products set stock = 0;",
        "DROP INDEX idx_customers_name;",
        "DROP VIEW customer_view;",
    ],
)
def test_destructive_sql_attempts_are_blocked(sql):
    result = SQLGuardrail().check(sql)

    assert result.verdict == VERDICT_BLOCKED
    assert result.blocked
    assert result.reason


def test_bulk_update_is_approved_or_denied_through_handler():
    sql = "UPDATE customers SET city = 'Updated' WHERE id <= 6;"
    session_id = str(uuid4())
    call_id = str(uuid4())

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            (session_id, "approval_user", "2026-07-10T00:00:00+00:00", "active"),
        )
        conn.execute(
            "INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (call_id, session_id, "sql_tool", sql, "pending_approval", "2026-07-10T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    approve_tool = SQLTool(approval_handler=AutoApproveHandler())
    approve_result = approve_tool.execute(sql=sql, _call_id=call_id, _session_id=session_id)
    assert approve_result.status == "success"

    deny_tool = SQLTool(approval_handler=AutoDenyHandler())
    deny_result = deny_tool.execute(sql=sql, _call_id=call_id, _session_id=session_id)
    assert deny_result.status == "denied"
    assert deny_result.approval_reason


def test_pending_approval_queue_can_be_listed_and_resolved():
    session_id = str(uuid4())
    call_id = str(uuid4())
    approval_id = str(uuid4())

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            (session_id, "queue_user", "2026-07-10T00:00:00+00:00", "active"),
        )
        conn.execute(
            "INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (call_id, session_id, "sql_tool", '{"sql": "UPDATE customers SET city = \'Queued\' WHERE id <= 6;"}', "pending_approval", "2026-07-10T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO app_approval_requests
               (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, call_id, session_id, "bulk update requires review", None, None, None, "2026-07-10T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    pending = get_pending_approvals()
    assert len(pending) == 1
    assert pending[0]["approval_id"] == approval_id

    resolved = resolve_approval(approval_id, "approved")
    assert resolved is True
    assert get_pending_approvals() == []


def test_format_rows_handles_mapping_rows_like_postgres():
    """Hermetic pin for the PG header-as-data bug (live prod, Sep 2026).

    psycopg2 RealDictRow iterates KEYS while sqlite3.Row iterates VALUES,
    so positional formatting rendered column names on PostgreSQL. Keyed
    formatting is correct for both shapes (plus sqlite rows and tuples).
    """
    tool = SQLTool()
    mapping_rows = [
        {"id": 1, "name": "Alice Johnson"},
        {"id": 8, "name": "Henry Wilson"},
    ]
    out = tool._format_rows(["id", "name"], mapping_rows)
    assert "Alice Johnson" in out
    assert "Henry Wilson" in out
    assert out.splitlines()[2] == "1 | Alice Johnson"
