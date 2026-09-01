"""Builder-run audit rows: every visual-builder SELECT persists to its own
``app_builder_runs`` table, and that table never touches agent metrics.
"""

import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from db.database import get_connection, reset_db
from db.seed import seed_demo_data
from tools.query_builder import QueryBuilderRequest, run_builder_query
from webapp import app


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _audit_rows():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM app_builder_runs").fetchall()]
    finally:
        conn.close()


def test_builder_run_persists_audit_row():
    spec = QueryBuilderRequest(table="customers", filters=[])
    result = run_builder_query(spec)

    rows = _audit_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["table_name"] == "customers"
    assert row["verdict"] == "ALLOWED"
    assert row["sql_text"] == result["sql"]
    assert row["row_count"] == result["row_count"]
    assert row["elapsed_ms"] == result["elapsed_ms"]
    assert row["run_id"] and row["executed_at"]


def test_builder_runs_counted_in_stats_without_touching_agent_metrics():
    run_builder_query(QueryBuilderRequest(table="products", filters=[]))
    run_builder_query(QueryBuilderRequest(table="orders", filters=[]))

    with TestClient(app) as client:
        stats = client.get("/api/stats").json()
    assert stats["builder_runs"] == 2
    assert stats["tool_calls"] == 0

    conn = get_connection()
    try:
        agent_tool_calls = conn.execute(
            "SELECT COUNT(*) AS cnt FROM app_tool_calls"
        ).fetchone()["cnt"]
    finally:
        conn.close()
    assert agent_tool_calls == 0
