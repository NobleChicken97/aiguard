"""
Integration tests for the SQLite -> PostgreSQL migration script.

These run ONLY when ``TEST_DATABASE_URL`` points at a disposable PostgreSQL
instance (same contract as tests/test_postgres_integration.py):

    docker run --rm -d -p 5432:5432 -e POSTGRES_PASSWORD=test postgres:16
    set TEST_DATABASE_URL=postgresql://postgres:test@localhost:5432/postgres
    pytest tests/test_migration_script.py -v

The guardrail-refusal test runs unconditionally so the default SQLite suite
still exercises the script's safety checks everywhere.
"""

import os
import sqlite3

import pytest

from db.schema import APP_SCHEMA, DEMO_SCHEMA

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@pytest.fixture()
def pg_env():
    if not TEST_DATABASE_URL.startswith("postgres"):
        pytest.skip(
            "Set TEST_DATABASE_URL to a disposable PostgreSQL instance to run these tests"
        )
    import config
    import db.database as dbmod

    original_url = config.DATABASE_URL
    config.DATABASE_URL = TEST_DATABASE_URL
    dbmod._pg_pool = None
    try:
        # Same contract as test_postgres_integration: initialize_db() first
        # (idempotent) because a fresh database — e.g. the CI service
        # container — has no schema yet; reused instances are then cleared
        # and SERIAL sequences restarted so id-dependent assertions stay
        # deterministic across runs.
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


@pytest.fixture()
def source_db(tmp_path):
    path = tmp_path / "source.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(APP_SCHEMA)
        conn.executescript(DEMO_SCHEMA)

        conn.execute(
            "INSERT INTO customers (id, name, email, city, signup_date) VALUES (?, ?, ?, ?, ?)",
            (101, "Migrate Alice", "alice@migrate.test", "Chicago", "2026-01-01"),
        )
        conn.execute(
            "INSERT INTO products (id, name, category, price, stock) VALUES (?, ?, ?, ?, ?)",
            (201, "Migrate Widget", "Tools", 19.99, 7),
        )
        conn.execute(
            "INSERT INTO orders (id, customer_id, order_date, total, status) VALUES (?, ?, ?, ?, ?)",
            (301, 101, "2026-02-02", 19.99, "delivered"),
        )
        conn.execute(
            "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
            (401, 301, 201, 1, 19.99),
        )
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            ("sess-1", "migrate_user", "2026-03-01T00:00:00+00:00", "active"),
        )
        conn.execute(
            "INSERT INTO app_messages (message_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            ("msg-1", "sess-1", "user", "hello from sqlite", "2026-03-01T00:00:01+00:00"),
        )
        conn.execute(
            "INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("call-1", "sess-1", "sql_tool", "{}", "executed", "2026-03-01T00:00:02+00:00"),
        )
        conn.execute(
            """INSERT INTO app_approval_requests
               (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
               VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?)""",
            ("appr-1", "call-1", "sess-1", "bulk change", "2026-03-01T00:00:03+00:00"),
        )
        conn.execute(
            "INSERT INTO app_memory_facts (fact_id, user_id, fact_text, source_session_id, created_at) VALUES (?, ?, ?, ?, ?)",
            ("fact-1", "migrate_user", "Prefers terse answers.", "sess-1", "2026-03-01T00:00:04+00:00"),
        )
        conn.execute(
            "INSERT INTO app_trace_events (trace_id, session_id, event_type, data, timestamp) VALUES (?, ?, ?, ?, ?)",
            ("tr-1", "sess-1", "final_answer", '{"text": "done"}', "2026-03-01T00:00:05+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _pg_counts(conn):
    return {
        table: conn.execute(f'SELECT COUNT(*) AS cnt FROM "{table}"').fetchone()["cnt"]
        for table in [
            "customers",
            "products",
            "orders",
            "order_items",
            "app_sessions",
            "app_messages",
            "app_tool_calls",
            "app_approval_requests",
            "app_memory_facts",
            "app_trace_events",
        ]
    }


EXPECTED_COUNTS = {
    "customers": 1,
    "products": 1,
    "orders": 1,
    "order_items": 1,
    "app_sessions": 1,
    "app_messages": 1,
    "app_tool_calls": 1,
    "app_approval_requests": 1,
    "app_memory_facts": 1,
    "app_trace_events": 1,
}


def test_refuses_to_run_without_postgres_target(monkeypatch):
    import config
    from db.migrate_sqlite_to_pg import MigrationError, run_migration

    monkeypatch.setattr(config, "DATABASE_URL", "")
    with pytest.raises(MigrationError, match="postgres"):
        run_migration(source="whatever.db")

    monkeypatch.setattr(config, "DATABASE_URL", "sqlite://")
    with pytest.raises(MigrationError, match="PostgreSQL"):
        run_migration(source="whatever.db")


def test_missing_source_file_fails_cleanly(pg_env):
    from db.migrate_sqlite_to_pg import MigrationError, run_migration

    with pytest.raises(MigrationError, match="not found"):
        run_migration(source="does/not/exist.db")


def test_migration_copies_all_rows_and_round_trips_values(pg_env, source_db):
    from db.database import get_connection
    from db.migrate_sqlite_to_pg import run_migration

    counts = run_migration(source=source_db, truncate=True)
    assert {t: c["target"] for t, c in counts.items()} == EXPECTED_COUNTS

    conn = get_connection()
    try:
        name_row = conn.execute(
            'SELECT name FROM "customers" WHERE id = ?', (101,)
        ).fetchone()
        assert name_row["name"] == "Migrate Alice"

        event_row = conn.execute(
            'SELECT data FROM "app_trace_events" WHERE trace_id = ?', ("tr-1",)
        ).fetchone()
        assert '"text"' in event_row["data"]
    finally:
        conn.close()


def test_migration_rerun_is_idempotent_merge(pg_env, source_db):
    from db.database import get_connection
    from db.migrate_sqlite_to_pg import run_migration

    run_migration(source=source_db, truncate=True)
    counts_second = run_migration(source=source_db)

    assert {t: c["target"] for t, c in counts_second.items()} == EXPECTED_COUNTS

    conn = get_connection()
    try:
        assert _pg_counts(conn) == EXPECTED_COUNTS
    finally:
        conn.close()


def test_truncate_mode_clears_target_only_rows(pg_env, source_db):
    from db.database import get_connection
    from db.migrate_sqlite_to_pg import run_migration

    run_migration(source=source_db, truncate=True)

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            ("sess-extra", "stray", "2026-04-01T00:00:00+00:00", "active"),
        )
        conn.commit()
        assert conn.execute("SELECT COUNT(*) AS cnt FROM app_sessions").fetchone()["cnt"] == 2
    finally:
        conn.close()

    run_migration(source=source_db, truncate=True)

    conn = get_connection()
    try:
        assert _pg_counts(conn) == EXPECTED_COUNTS
    finally:
        conn.close()


def test_serial_sequence_advances_past_migrated_ids(pg_env, source_db):
    from db.database import get_connection
    from db.migrate_sqlite_to_pg import run_migration

    run_migration(source=source_db, truncate=True)

    conn = get_connection()
    try:
        cursor = conn.execute(
            """INSERT INTO customers (name, email, city, signup_date)
               VALUES (?, ?, ?, ?) RETURNING id""",
            ("Post Migrate Bob", "bob@migrate.test", "Denver", "2026-05-01"),
        )
        conn.commit()
        new_id = cursor.fetchone()["id"]
        assert new_id > 101
    finally:
        conn.close()
