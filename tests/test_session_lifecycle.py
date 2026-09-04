"""Session lifecycle: idle-window activity model (ticket 09).

A turn finishing no longer ends the session row. Active-ness is derived
from ``app_sessions.last_active_at`` vs ``SESSION_IDLE_MINUTES``, legacy
databases get the column via migration with a started_at backfill.
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

import config
from agent.llm_client import FakeLLMClient
from agent.orchestrator import Orchestrator
from approval.gate import AutoApproveHandler
from auth import SESSION_COOKIE, create_user, sign_session
from db.database import get_connection, initialize_db, reset_db
from db.seed import seed_demo_data
from webapp import app


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _session_row(session_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT session_id, status, last_active_at FROM app_sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def test_run_keeps_session_active_and_stamps_activity():
    orchestrator = Orchestrator(
        llm_client=FakeLLMClient([FakeLLMClient.text_response("done.")]),
        approval_handler=AutoApproveHandler(),
        user_id="lifecycle_user",
    )
    orchestrator.run("Hello")
    row = _session_row(orchestrator.session_id)
    assert row["status"] == "active"

    last_active = datetime.fromisoformat(row["last_active_at"])
    assert datetime.now(timezone.utc) - last_active < timedelta(minutes=1)


def test_second_turn_refreshes_activity_timestamp():
    orchestrator = Orchestrator(
        llm_client=FakeLLMClient([FakeLLMClient.text_response("a."), FakeLLMClient.text_response("b.")]),
        approval_handler=AutoApproveHandler(),
        user_id="lifecycle_user",
    )
    orchestrator.run("first")
    first_ts = _session_row(orchestrator.session_id)["last_active_at"]
    orchestrator.run("second")
    second_ts = _session_row(orchestrator.session_id)["last_active_at"]
    assert second_ts >= first_ts
    assert _session_row(orchestrator.session_id)["status"] == "active"


def test_active_sessions_counts_only_activity_within_idle_window():
    stale_session = str(uuid4())
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO app_sessions (session_id, user_id, started_at, status, last_active_at)
               VALUES (?, 'stale_user', ?, 'active', ?)""",
            (stale_session, stale_ts, stale_ts),
        )
        conn.commit()
    finally:
        conn.close()

    orchestrator = Orchestrator(
        llm_client=FakeLLMClient([FakeLLMClient.text_response("done.")]),
        approval_handler=AutoApproveHandler(),
        user_id=create_user("fresh@test.local", "testpass123"),
    )
    orchestrator.run("Hello")

    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(orchestrator.user_id))
        stats = client.get("/api/stats").json()
    assert stats["active_sessions"] == 1  # only the just-touched session
    session_ids = {s["session_id"] for s in stats["recent_sessions"]}
    assert orchestrator.session_id in session_ids
    # Row-level isolation: another user's stale session is not listed.
    assert stale_session not in session_ids


def test_initialize_db_migrates_legacy_sessions_table(tmp_path, monkeypatch):
    legacy_db = tmp_path / "legacy.db"
    monkeypatch.setattr(config, "DB_PATH", str(legacy_db))

    # Simulate a pre-1.6.3 database whose app_sessions lacks last_active_at.
    conn = sqlite3.connect(legacy_db)
    conn.execute(
        """CREATE TABLE app_sessions (
               session_id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL,
               started_at TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'active'
           )"""
    )
    conn.execute(
        "INSERT INTO app_sessions VALUES ('legacy-1', 'u', '2024-01-01T00:00:00+00:00', 'active')"
    )
    conn.commit()
    conn.close()

    initialize_db()

    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT status, last_active_at FROM app_sessions WHERE session_id = 'legacy-1'"
        ).fetchone()
    finally:
        conn.close()
    assert row["status"] == "active"
    assert row["last_active_at"] == "2024-01-01T00:00:00+00:00"  # backfilled
