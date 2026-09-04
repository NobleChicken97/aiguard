"""Phase 1 authorization tests: every stateful endpoint requires an
authenticated, scoped session; no cross-user data leakage anywhere.

Conventions under test:
- anonymous traffic gets 303-to-/login on pages, 401 on JSON APIs
- cross-user reads resolve to 404 (never 403), so IDs cannot be probed
- admins may read across users (cross-user approval-queue demo)
"""

import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient
from agent.memory import LongTermMemory
from agent.orchestrator import Orchestrator
from approval.gate import AutoDenyHandler, get_pending_approvals
from auth import SESSION_COOKIE, create_user, sign_session
from db.database import get_connection, reset_db
from db.seed import seed_demo_data
from webapp import app
import webapp as webapp_module


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _make_user(email=None, password="testpass123", role="user"):
    email = email or f"{uuid4().hex[:8]}@test.local"
    return create_user(email, password, role=role)


def _authed_client(user_id):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, sign_session(user_id))
    return client


def _chat_session_id(client, text="hello"):
    """Drive one chat turn through the override LLM; return the session_id."""
    webapp_module._chat_llm_client_override = FakeLLMClient(
        [FakeLLMClient.text_response("noted.")]
    )
    try:
        resp = client.post("/api/chat", json={"message": text})
        assert resp.status_code == 200
        return resp.json()["session_id"]
    finally:
        webapp_module._chat_llm_client_override = None


# --------------------------------------------------------------------------
# Registration / login / logout
# --------------------------------------------------------------------------

def test_register_creates_account_and_logs_in():
    with TestClient(app) as client:
        resp = client.post(
            "/register", data={"email": "alice@test.local", "password": "testpass123"}
        )
        assert resp.status_code == 200  # followed redirect into /chat
        assert SESSION_COOKIE in client.cookies
        assert "Agent Chat" in resp.text


def test_register_rejects_duplicate_email():
    _make_user(email="dup@test.local")
    with TestClient(app) as client:
        resp = client.post(
            "/register", data={"email": "dup@test.local", "password": "testpass123"}
        )
        assert resp.status_code == 400
        assert "already registered" in resp.text


def test_register_rejects_short_password():
    with TestClient(app) as client:
        resp = client.post(
            "/register", data={"email": "new@test.local", "password": "short"}
        )
        assert resp.status_code == 400
        assert "8 characters" in resp.text


def test_register_rejects_bad_email():
    with TestClient(app) as client:
        resp = client.post(
            "/register", data={"email": "not-an-email", "password": "testpass123"}
        )
        assert resp.status_code == 400
        assert "valid email" in resp.text


def test_login_round_trip_and_logout():
    _make_user(email="bob@test.local", password="bobspass1")
    with TestClient(app) as client:
        bad = client.post(
            "/login", data={"email": "bob@test.local", "password": "wrongpass1"}
        )
        assert bad.status_code == 401
        assert SESSION_COOKIE not in client.cookies

        good = client.post(
            "/login", data={"email": "bob@test.local", "password": "bobspass1"}
        )
        assert good.status_code == 200
        assert SESSION_COOKIE in client.cookies

        out = client.post("/logout")
        assert out.status_code == 200
        assert "Login" in out.text


def test_anonymous_pages_redirect_to_login():
    with TestClient(app) as client:
        for path in ("/chat", "/dashboard", "/approval-queue", "/traces"):
            resp = client.get(path)
            assert resp.status_code == 200  # followed 303 into /login
            assert "Login" in resp.text


# --------------------------------------------------------------------------
# Cookie forgery / expiry
# --------------------------------------------------------------------------

def test_tampered_cookie_gets_401():
    uid = _make_user()
    good = sign_session(uid)
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, good[:-2] + ("xx" if not good.endswith("xx") else "yy"))
        resp = client.post("/api/chat", json={"message": "hi"})
        assert resp.status_code == 401


def test_expired_cookie_gets_401():
    uid = _make_user()
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(uid, ttl=-10))
        resp = client.post("/api/chat", json={"message": "hi"})
        assert resp.status_code == 401


# --------------------------------------------------------------------------
# Cross-user isolation (7 tables)
# --------------------------------------------------------------------------

def test_user_cannot_read_another_users_session_messages():
    uid_a = _make_user()
    uid_b = _make_user()
    sid = _chat_session_id(_authed_client(uid_a))

    resp = _authed_client(uid_b).get(f"/api/sessions/{sid}/messages")
    assert resp.status_code == 404


def test_user_cannot_read_another_users_trace():
    uid_a = _make_user()
    uid_b = _make_user()
    sid = _chat_session_id(_authed_client(uid_a))

    api = _authed_client(uid_b).get(f"/api/traces/{sid}")
    assert api.status_code == 404

    page = _authed_client(uid_b).get(f"/traces/{sid}")
    assert page.status_code == 404


def test_sessions_list_excludes_other_users():
    uid_a = _make_user()
    uid_b = _make_user()
    sid = _chat_session_id(_authed_client(uid_a))

    payload = _authed_client(uid_b).get("/api/sessions").json()
    assert all(s["user_id"] == uid_b for s in payload["sessions"])
    assert sid not in {s["session_id"] for s in payload["sessions"]}


def test_user_cannot_read_another_users_memory():
    uid_a = _make_user()
    uid_b = _make_user()
    LongTermMemory().save_facts(uid_a, ["A breeds axolotls."], source_session_id="s1")

    resp = _authed_client(uid_b).get(f"/api/users/{uid_a}/memory")
    assert resp.status_code == 404


def test_user_cannot_delete_another_users_fact():
    uid_a = _make_user()
    uid_b = _make_user()
    LongTermMemory().save_facts(uid_a, ["A breeds axolotls."], source_session_id="s1")
    fact_id = LongTermMemory().get_all_facts(uid_a)[0]["fact_id"]

    resp = _authed_client(uid_b).delete(f"/api/users/{uid_a}/memory/{fact_id}")
    assert resp.status_code == 404
    # Still there for the owner.
    assert LongTermMemory().get_all_facts(uid_a)[0]["fact_id"] == fact_id


def _seed_approval_for(sql, user_id):
    session_id, call_id, approval_id = (str(uuid4()), str(uuid4()), str(uuid4()))
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            (session_id, user_id, "2026-07-10T00:00:00+00:00", "active"),
        )
        conn.execute(
            "INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (call_id, session_id, "sql_tool", sql, "pending_approval", "2026-07-10T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO app_approval_requests
               (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, call_id, session_id, "review me", None, None, None, "2026-07-10T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()
    return approval_id


def test_user_cannot_see_or_resolve_another_users_approval():
    uid_a = _make_user()
    uid_b = _make_user()
    approval_id = _seed_approval_for("UPDATE customers SET city = 'X' WHERE id = 1;", uid_a)

    client_b = _authed_client(uid_b)
    queue = client_b.get("/approval-queue")
    assert queue.status_code == 200
    assert approval_id not in queue.text

    resp = client_b.post(f"/approvals/{approval_id}/approve", data={"csrf_token": "x"})
    # Ownership is checked after auth+CSRF: wrong CSRF gives 403, but even a
    # right one must 404 — prove the 404 path with a real token.
    assert resp.status_code in (403, 404)

    page = client_b.get("/approval-queue")
    import re as _re

    match = _re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    if match:
        resp2 = client_b.post(
            f"/approvals/{approval_id}/approve", data={"csrf_token": match.group(1)}
        )
        assert resp2.status_code == 404

    # Still pending for the owner.
    assert any(p["approval_id"] == approval_id for p in get_pending_approvals())


def test_builder_run_is_attributed_to_caller():
    uid = _make_user()
    resp = _authed_client(uid).post(
        "/api/query-builder/run", json={"table": "customers", "columns": ["name"], "limit": 5}
    )
    assert resp.status_code == 200
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, row_count FROM app_builder_runs ORDER BY executed_at DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    assert row["user_id"] == uid
    assert row["row_count"] >= 1


def test_admin_sees_and_resolves_across_users():
    uid_a = _make_user()
    admin_id = _make_user(password="adminpass123", role="admin")
    approval_id = _seed_approval_for("UPDATE customers SET city = 'Y' WHERE id = 2;", uid_a)

    admin = _authed_client(admin_id)
    queue = admin.get("/approval-queue")
    assert queue.status_code == 200
    assert approval_id in queue.text

    import re as _re

    token = _re.search(r'name="csrf_token" value="([^"]+)"', queue.text).group(1)
    resp = admin.post(f"/approvals/{approval_id}/approve", data={"csrf_token": token})
    assert resp.status_code in (200, 303)
    assert not any(p["approval_id"] == approval_id for p in get_pending_approvals())


# --------------------------------------------------------------------------
# Per-user rate limits + memory fix
# --------------------------------------------------------------------------

def test_rate_limit_is_per_user_not_global():
    from webapp_ratelimit import configure as configure_ratelimit

    uid_a = _make_user()
    uid_b = _make_user()
    webapp_module._chat_llm_client_override = FakeLLMClient(
        [FakeLLMClient.text_response("ok")] * 4
    )
    configure_ratelimit(chat_per_min=1, sse_max_per_ip=3)
    try:
        client_a = _authed_client(uid_a)
        assert client_a.post("/api/chat", json={"message": "one"}).status_code == 200
        assert client_a.post("/api/chat", json={"message": "two"}).status_code == 429
        # B is unaffected by A's throttle.
        assert _authed_client(uid_b).post("/api/chat", json={"message": "hi"}).status_code == 200
    finally:
        webapp_module._chat_llm_client_override = None
        configure_ratelimit(chat_per_min=30, sse_max_per_ip=3)


def test_long_term_fact_reaches_next_session_prompt():
    """Finding 5 regression test: facts saved in session 1 must appear in
    the system prompt the workers see in session 2."""
    LongTermMemory().save_facts("fact_user", ["The user breeds axolotls."], source_session_id="s0")

    captured = []

    class CaptureClient(FakeLLMClient):
        def call(self, system, messages, tools=None):
            captured.append(system or "")
            return super().call(system, messages, tools)

    orch = Orchestrator(
        llm_client=CaptureClient([FakeLLMClient.text_response("noted.")]),
        approval_handler=AutoDenyHandler(),
        user_id="fact_user",
    )
    assert orch.run("hi") == "noted."
    assert any("axolotls" in s for s in captured), (
        "LTM fact never reached any LLM system prompt"
    )


def test_login_required_on_json_apis():
    with TestClient(app) as client:
        assert client.post("/api/chat", json={"message": "hi"}).status_code == 401
        assert client.get("/api/stats").status_code == 401
        assert client.get("/api/sessions").status_code == 401
        assert client.post("/api/query-builder/run", json={"table": "x"}).status_code == 401
        # /health stays open for load-balancer probes.
        assert client.get("/health").status_code == 200
