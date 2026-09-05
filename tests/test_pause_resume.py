"""Phase 3 pause/resume: gated turns release the worker thread in
milliseconds and continue exactly where they paused once decided.

The headline invariant: a chat that hits the approval gate returns 202
fast (no 120s thread hold), and resume replays the paused tool with the
human's decision as its result.
"""

import sys
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient
from agent.orchestrator import Orchestrator
from approval.gate import (
    ApprovalPending,
    AsyncApprovalHandler,
    PendingApproval,
    load_pending_resume,
    resolve_approval,
    save_pending_resume,
)
from auth import SESSION_COOKIE, create_user, sign_session
from db.database import reset_db
from db.seed import seed_demo_data
from tools.base import Tool, execute_with_retry
from webapp import app
import webapp as webapp_module

MULTI = "SELECT id FROM products; SELECT id FROM customers;"


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _make_user(email=None, password="testpass123", role="user"):
    from uuid import uuid4

    return create_user(email or f"{uuid4().hex[:8]}@test.local", password, role=role)


def _authed_client(user_id):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, sign_session(user_id))
    return client


class _PendingTool(Tool):
    def get_name(self):
        return "pending_tool"

    def get_description(self):
        return "always pauses"

    def get_input_schema(self):
        return {"type": "object", "properties": {}}

    def __init__(self):
        super().__init__()
        self.calls = 0

    def execute(self, **kwargs):
        self.calls += 1
        raise ApprovalPending("a1", "c1", "s1", "reason", "pending_tool", {})


def test_approval_pending_skips_retry_and_backoff():
    tool = _PendingTool()
    started = time.monotonic()
    with pytest.raises(ApprovalPending):
        execute_with_retry(tool, {}, "c1")
    assert tool.calls == 1  # exactly once: no retry, no backoff sleep
    assert (time.monotonic() - started) < 5


def test_async_handler_request_approval_raises():
    with pytest.raises(RuntimeError):
        AsyncApprovalHandler().request_approval("c", "s", "r", "t", {})


def test_run_returns_pending_and_releases_thread_fast():
    orch = Orchestrator(
        llm_client=FakeLLMClient(
            [FakeLLMClient.tool_use_response("sql_tool", {"sql": MULTI}, "toolu_pr1")],
            route_decision="SQL",
        ),
        approval_handler=AsyncApprovalHandler(),
        user_id="pause_user",
    )
    started = time.monotonic()
    out = orch.run("do the bulk thing")
    held_ms = (time.monotonic() - started) * 1000
    assert isinstance(out, PendingApproval)
    assert held_ms < 10000, f"worker thread held {held_ms:.0f}ms (old model: 120000ms+)"
    assert load_pending_resume(out.session_id)["call_id"] == out.call_id


def test_resume_without_row_raises():
    orch = Orchestrator(
        llm_client=FakeLLMClient([]),
        approval_handler=AsyncApprovalHandler(),
        user_id="nobody",
    )
    with pytest.raises(ValueError):
        orch.resume("no-such-session")


def _paused_chat(client, text="do the bulk thing"):
    webapp_module._chat_llm_client_override = FakeLLMClient(
        [
            FakeLLMClient.tool_use_response("sql_tool", {"sql": MULTI}, "toolu_web1"),
            FakeLLMClient.text_response("finished after resume."),
        ],
        route_decision="SQL",
    )
    try:
        resp = client.post("/api/chat", json={"message": text})
        assert resp.status_code == 202
        return resp.json()
    finally:
        webapp_module._chat_llm_client_override = None


def _approve_via_queue(client, approval_id):
    import re as _re

    page = client.get("/approval-queue")
    token = _re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    resp = client.post(f"/approvals/{approval_id}/approve", data={"csrf_token": token})
    assert resp.status_code in (200, 303)


def test_status_endpoint_tracks_pending_to_approved():
    uid = _make_user()
    with _authed_client(uid) as client:
        pending = _paused_chat(client)
        first = client.get(f"/api/approval/{pending['approval_id']}/status")
        assert first.status_code == 200
        assert first.json() == {
            "approval_id": pending["approval_id"],
            "status": "pending",
            "session_id": pending["session_id"],
        }
        _approve_via_queue(client, pending["approval_id"])
        second = client.get(f"/api/approval/{pending['approval_id']}/status")
        assert second.json()["status"] == "approved"


def test_status_endpoint_404s_unknown_and_cross_user():
    uid_a = _make_user()
    uid_b = _make_user()
    with _authed_client(uid_a) as client_a:
        pending = _paused_chat(client_a)
        assert client_a.get("/api/approval/nope/status").status_code == 404
    with _authed_client(uid_b) as client_b:
        assert client_b.get(f"/api/approval/{pending['approval_id']}/status").status_code == 404


def test_resume_404s_unknown_and_cross_user():
    uid_a = _make_user()
    uid_b = _make_user()
    with _authed_client(uid_a) as client_a:
        pending = _paused_chat(client_a)
        assert client_a.post("/api/chat/resume", json={"session_id": "nope"}).status_code == 404
    with _authed_client(uid_b) as client_b:
        resp = client_b.post("/api/chat/resume", json={"session_id": pending["session_id"]})
        assert resp.status_code == 404


def test_second_gate_repauses_with_fresh_state():
    """Two gated tool calls in one turn pause twice; the consumed resume
    row is replaced, never reused."""
    webapp_module._chat_llm_client_override = FakeLLMClient(
        [
            FakeLLMClient.tool_use_response("sql_tool", {"sql": MULTI}, "toolu_d1"),
            FakeLLMClient.tool_use_response("sql_tool", {"sql": MULTI}, "toolu_d2"),
            FakeLLMClient.text_response("twice approved."),
        ],
        route_decision="SQL",
    )
    uid = _make_user()
    try:
        with _authed_client(uid) as client:
            first = client.post("/api/chat", json={"message": "two bulk things"})
            assert first.status_code == 202
            aid1, sid = first.json()["approval_id"], first.json()["session_id"]

            _approve_via_queue(client, aid1)
            second = client.post("/api/chat/resume", json={"session_id": sid})
            assert second.status_code == 202
            aid2 = second.json()["approval_id"]
            assert aid2 != aid1  # fresh approval, fresh resume row
            assert load_pending_resume(sid)["call_id"] == "toolu_d2"

            _approve_via_queue(client, aid2)
            webapp_module._chat_llm_client_override = FakeLLMClient(
                [FakeLLMClient.text_response("twice approved.")], route_decision="SQL"
            )
            final = client.post("/api/chat/resume", json={"session_id": sid})
            assert final.status_code == 200
            assert final.json()["response"] == "twice approved."
            assert load_pending_resume(sid) is None  # consumed
    finally:
        webapp_module._chat_llm_client_override = None


def test_janitor_purges_only_decided_stale_rows():
    """Abandoned turns (decided + older than TTL) are purged opportunistically
    on the next pause; undecided rows are never touched."""
    from db.database import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            ("sess-jan", "jan_user", "2020-01-01T00:00:00+00:00", "active"),
        )
        for call_id, approval_id, decision in [
            ("c-old-decided", "a-old-decided", "approved"),
            ("c-old-pending", "a-old-pending", None),
        ]:
            conn.execute(
                "INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (call_id, "sess-jan", "sql_tool", "{}", "executed", "2020-01-01T00:00:00+00:00"),
            )
            conn.execute(
                """INSERT INTO app_approval_requests
                   (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (approval_id, call_id, "sess-jan", "r", "u", decision,
                 "2020-01-02T00:00:00+00:00" if decision else None,
                 "2020-01-01T00:00:00+00:00"),
            )
            conn.execute(
                """INSERT INTO app_pending_resumes
                   (call_id, approval_id, session_id, worker_name, messages, tool_name, tool_input,
                    input_tokens, output_tokens, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (call_id, approval_id, "sess-jan", "SQLWorker", "[]", "sql_tool", "{}",
                 5, 5, "2020-01-01T00:00:00+00:00"),
            )
        conn.commit()
    finally:
        conn.close()

    # Any new pause runs the janitor inside the same transaction.
    save_pending_resume("c-new", "a-old-pending", "sess-jan", "SQLWorker", [], "sql_tool", {})

    conn = get_connection()
    try:
        remaining = {
            r["call_id"] for r in conn.execute("SELECT call_id FROM app_pending_resumes").fetchall()
        }
    finally:
        conn.close()
    assert "c-old-decided" not in remaining
    assert {"c-old-pending", "c-new"} <= remaining


def test_token_accounting_continues_across_resume():
    """Pause persists usage; resume seeds (not restarts) the budget client."""
    orch = Orchestrator(
        llm_client=FakeLLMClient(
            [FakeLLMClient.tool_use_response("sql_tool", {"sql": MULTI}, "toolu_tok")],
            route_decision="SQL",
        ),
        approval_handler=AsyncApprovalHandler(),
        user_id="tok_user",
    )
    out = orch.run("do the bulk thing")
    assert isinstance(out, PendingApproval)
    saved = load_pending_resume(out.session_id)
    assert saved["input_tokens"] > 0 and saved["output_tokens"] > 0
    assert resolve_approval(out.approval_id, "approved")

    orch2 = Orchestrator(
        llm_client=FakeLLMClient([FakeLLMClient.text_response("counted.")]),
        approval_handler=AsyncApprovalHandler(),
        user_id="tok_user",
    )
    assert orch2.resume(out.session_id) == "counted."
    assert orch2.total_input_tokens > saved["input_tokens"]
    assert orch2.total_output_tokens > saved["output_tokens"]


def test_initialize_db_migrates_legacy_resume_table(tmp_path, monkeypatch):
    import sqlite3

    import config
    from db.database import initialize_db

    legacy_db = tmp_path / "legacy-resume.db"
    monkeypatch.setattr(config, "DB_PATH", str(legacy_db))
    conn = sqlite3.connect(legacy_db)
    conn.execute(
        """CREATE TABLE app_pending_resumes (
               call_id TEXT PRIMARY KEY, approval_id TEXT NOT NULL,
               session_id TEXT NOT NULL, worker_name TEXT NOT NULL,
               messages TEXT NOT NULL, tool_name TEXT NOT NULL,
               tool_input TEXT NOT NULL, created_at TEXT NOT NULL)"""
    )
    conn.execute(
        "INSERT INTO app_pending_resumes VALUES "
        "('c1', 'a1', 's1', 'SQLWorker', '[]', 'sql_tool', '{}', '2024-01-01T00:00:00+00:00')"
    )
    conn.commit()
    conn.close()

    initialize_db()

    conn = sqlite3.connect(legacy_db)
    conn.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(app_pending_resumes)").fetchall()}
        row = conn.execute(
            "SELECT input_tokens, output_tokens FROM app_pending_resumes WHERE call_id = 'c1'"
        ).fetchone()
    finally:
        conn.close()
    assert {"input_tokens", "output_tokens"} <= cols
    assert (row["input_tokens"], row["output_tokens"]) == (0, 0)
