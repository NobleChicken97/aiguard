"""
PostgreSQL integration tests for the dialect-aware data layer.

These run ONLY when ``TEST_DATABASE_URL`` points at a disposable PostgreSQL
instance, e.g.:

    # throwaway instance via Docker
    docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:16
    set TEST_DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres
    pytest tests/test_postgres_integration.py -v

Without the variable the whole module is skipped, so the default SQLite
suite stays green everywhere. The tests exist to catch SQLite-only syntax
and API usage (e.g. INSERT OR IGNORE, conn.total_changes) that would
otherwise only explode in production.
"""

import os
import uuid

import pytest

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")

pytestmark = pytest.mark.skipif(
    not TEST_DATABASE_URL.startswith("postgres"),
    reason="Set TEST_DATABASE_URL to a disposable PostgreSQL instance to run these tests",
)


@pytest.fixture()
def pg_env():
    import config
    import db.database as dbmod

    original_url = config.DATABASE_URL
    config.DATABASE_URL = TEST_DATABASE_URL
    dbmod._pg_pool = None
    try:
        # A fresh database (e.g. the CI service container) has no schema yet;
        # initialize_db() is idempotent (CREATE TABLE IF NOT EXISTS), so run
        # it before TRUNCATE. Reused instances are then cleared and SERIAL
        # sequences restarted so seed_demo_data() stays deterministic.
        dbmod.initialize_db()
        conn = dbmod.get_connection()
        try:
            conn.execute(
                """TRUNCATE customers, products, orders, order_items,
                   app_sessions, app_users, app_messages, app_tool_calls,
                   app_approval_requests, app_pending_resumes,
                   app_memory_facts, app_trace_events, app_builder_runs
                   RESTART IDENTITY"""
            )
            conn.commit()
        finally:
            conn.close()
        yield dbmod
    finally:
        if dbmod._pg_pool is not None:
            try:
                dbmod._pg_pool.closeall()
            except Exception:
                pass
            dbmod._pg_pool = None
        config.DATABASE_URL = original_url


def _insert_session(conn):
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
        (session_id, "pg_test_user", "2026-08-21T00:00:00+00:00", "active"),
    )
    conn.commit()
    return session_id


class TestPostgresDataLayer:
    def test_initialize_seed_and_query(self, pg_env):
        from db.database import initialize_db, get_connection
        from db.seed import seed_demo_data

        initialize_db()
        seed_demo_data()

        conn = get_connection()
        try:
            row = conn.execute("SELECT COUNT(*) AS cnt FROM customers").fetchone()
            assert row["cnt"] == 10
        finally:
            conn.close()

    def test_record_tool_call_is_idempotent(self, pg_env):
        from db.database import get_connection, initialize_db, record_tool_call

        initialize_db()
        conn = _setup_conn = get_connection()
        try:
            session_id = _insert_session(_setup_conn)
        finally:
            conn.close()

        call_id = str(uuid.uuid4())
        record_tool_call(session_id, call_id, "sql_tool", {"sql": "SELECT 1"})
        record_tool_call(session_id, call_id, "sql_tool", {"sql": "SELECT 1"})

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT call_id FROM app_tool_calls WHERE call_id = ?", (call_id,)
            ).fetchall()
        finally:
            conn.close()
        assert len(rows) == 1

    def test_resolve_approval_rowcount_semantics(self, pg_env):
        from db.database import get_connection, initialize_db, record_tool_call
        from approval.gate import resolve_approval

        initialize_db()
        conn = get_connection()
        try:
            session_id = _insert_session(conn)
        finally:
            conn.close()

        call_id = str(uuid.uuid4())
        record_tool_call(session_id, call_id, "sql_tool", {"sql": "SELECT 1"})

        approval_id = str(uuid.uuid4())
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO app_approval_requests
                   (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
                   VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?)""",
                (approval_id, call_id, session_id, "test risk", "2026-08-21T00:00:00+00:00"),
            )
            conn.commit()
        finally:
            conn.close()

        assert resolve_approval(approval_id, "approved") is True
        # Second resolution must not report success (already decided).
        assert resolve_approval(approval_id, "denied") is False
