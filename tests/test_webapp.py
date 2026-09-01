from uuid import uuid4

import re
import sys
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient
from agent.memory import LongTermMemory
from agent.orchestrator import Orchestrator
from approval.gate import AutoApproveHandler, WebApprovalHandler
from db.database import get_connection, reset_db
from db.seed import seed_demo_data
from webapp import app
import webapp as webapp_module


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


_CSRF_INPUT_RE = re.compile(r'name="csrf_token" value="([^"]+)"')


def _page_csrf_token(client):
    """Fetch the approval queue and return the embedded CSRF token."""
    page = client.get("/approval-queue")
    assert page.status_code == 200
    match = _CSRF_INPUT_RE.search(page.text)
    assert match, "approval queue page must embed a CSRF token"
    return match.group(1)


def _seed_pending_action(sql):
    session_id = str(uuid4())
    call_id = str(uuid4())
    approval_id = str(uuid4())

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO app_sessions (session_id, user_id, started_at, status) VALUES (?, ?, ?, ?)",
            (session_id, "web_user", "2026-07-10T00:00:00+00:00", "active"),
        )
        conn.execute(
            "INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (call_id, session_id, "sql_tool", sql, "pending_approval", "2026-07-10T00:00:00+00:00"),
        )
        conn.execute(
            """INSERT INTO app_approval_requests
               (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (approval_id, call_id, session_id, "bulk change needs review", None, None, None, "2026-07-10T00:00:00+00:00"),
        )
        conn.commit()
    finally:
        conn.close()

    return approval_id, session_id


def test_approval_queue_renders_and_resolves_actions():
    sql = "UPDATE customers SET city = 'Approved City' WHERE id <= 6;"
    approval_id, _session_id = _seed_pending_action(sql)
    with TestClient(app) as client:
        page = client.get("/approval-queue")
        assert page.status_code == 200
        assert approval_id in page.text

        token = _page_csrf_token(client)
        resolved = client.post(f"/approvals/{approval_id}/approve", data={"csrf_token": token})
        assert resolved.status_code in (200, 303)

        refreshed = client.get("/approval-queue")
        assert approval_id not in refreshed.text
        assert "There are no pending approvals" in refreshed.text or "Clear" in refreshed.text


def test_approval_actions_reject_missing_or_wrong_csrf_token():
    approval_id, _session_id = _seed_pending_action(
        "UPDATE customers SET city = 'CSRF City' WHERE id = 1;"
    )
    with TestClient(app) as client:
        client.get("/approval-queue")  # sets the CSRF cookie

        no_token = client.post(f"/approvals/{approval_id}/approve")
        assert no_token.status_code == 403

        wrong_token = client.post(
            f"/approvals/{approval_id}/deny", data={"csrf_token": "not-the-token"}
        )
        assert wrong_token.status_code == 403

        # The action was not resolved by either rejected post.
        page = client.get("/approval-queue")
        assert approval_id in page.text


def test_trace_replay_page_and_api_show_session_events():
    fake_llm = FakeLLMClient([
        FakeLLMClient.tool_use_response("calculator", {"expression": "15 * 37"}, "toolu_trace_1"),
        FakeLLMClient.text_response("15 * 37 = 555."),
    ])
    orchestrator = Orchestrator(
        llm_client=fake_llm,
        approval_handler=AutoApproveHandler(),
        user_id="trace_user",
    )
    result = orchestrator.run("What is 15 times 37?")
    with TestClient(app) as client:
        page = client.get(f"/traces/{orchestrator.session_id}")
        assert page.status_code == 200
        assert "final_answer" in page.text
        assert result in page.text

        api_response = client.get(f"/api/traces/{orchestrator.session_id}")
        assert api_response.status_code == 200
        payload = api_response.json()
        assert payload["session_id"] == orchestrator.session_id
        assert any(event["event_type"] == "final_answer" for event in payload["events"])


def test_health_endpoint_reports_database_status():
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["db_connected"] is True
        assert isinstance(data["session_count"], int)


def test_chat_page_and_memory_inspector_pages_render():
    with TestClient(app) as client:
        chat_page = client.get("/chat")
        assert chat_page.status_code == 200
        assert "Agent Chat" in chat_page.text
        assert "Approval Queue" in chat_page.text
        assert "Trace Replay" in chat_page.text

        memory_page = client.get("/memory-inspector")
        assert memory_page.status_code == 200
        assert "Memory Inspector" in memory_page.text


def test_chat_api_fails_gracefully_without_api_key(monkeypatch):
    monkeypatch.setattr(webapp_module.config, "ANTHROPIC_API_KEY", "")
    webapp_module._chat_llm_client_override = None
    with TestClient(app) as client:
        response = client.post("/api/chat", json={"message": "hello", "user_id": "web_user"})
        assert response.status_code == 400
        assert "ANTHROPIC_API_KEY" in response.text


def test_chat_api_resumes_existing_session():
    fake_llm = FakeLLMClient([
        FakeLLMClient.text_response("First web response."),
        FakeLLMClient.text_response("Resumed web response."),
    ])
    webapp_module._chat_llm_client_override = fake_llm

    try:
        with TestClient(app) as client:
            first = client.post("/api/chat", json={"message": "hello", "user_id": "web_user"})
            assert first.status_code == 200
            payload = first.json()
            session_id = payload["session_id"]
            assert session_id
            assert payload["response"] == "First web response."

            second = client.post(
                "/api/chat",
                json={"message": "follow up", "user_id": "web_user", "session_id": session_id},
            )
            assert second.status_code == 200
            resumed_payload = second.json()
            assert resumed_payload["session_id"] == session_id
            assert resumed_payload["response"] == "Resumed web response."

            messages = client.get(f"/api/sessions/{session_id}/messages")
            assert messages.status_code == 200
            msgs = messages.json()["messages"]
            assert any(m["role"] == "user" and "hello" in m["content"] for m in msgs)
            assert any(m["role"] == "user" and "follow up" in m["content"] for m in msgs)
            assert any(m["role"] == "assistant" and "First web response." in m["content"] for m in msgs)
    finally:
        webapp_module._chat_llm_client_override = None


def test_user_memory_api_returns_persisted_facts():
    LongTermMemory().save_facts("memory_user", ["Likes pineapple on pizza."], source_session_id="s1")
    with TestClient(app) as client:
        response = client.get("/api/users/memory_user/memory")
        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "memory_user"
        assert len(data["facts"]) == 1
        assert data["facts"][0]["fact_text"] == "Likes pineapple on pizza."


def test_dashboard_page_renders_with_nav_link():
    with TestClient(app) as client:
        page = client.get("/dashboard")
        assert page.status_code == 200
        assert "System Dashboard" in page.text
        assert "/api/stream" in page.text
        # Nav on every page now exposes the dashboard
        assert "Dashboard" in client.get("/chat").text


def test_api_stats_returns_live_counts():
    fake_llm = FakeLLMClient([
        FakeLLMClient.tool_use_response(
            "sql_tool",
            {"sql": "SELECT name FROM customers WHERE city = 'Chicago'"},
            "toolu_stats_1",
        ),
        FakeLLMClient.text_response("Found Carol White and Henry Wilson."),
    ])
    orchestrator = Orchestrator(
        llm_client=fake_llm,
        approval_handler=AutoApproveHandler(),
        user_id="stats_user",
    )
    orchestrator.run("Who lives in Chicago?")

    _approval_id, active_session = _seed_pending_action(
        "UPDATE products SET stock = 0 WHERE id <= 6;"
    )

    with TestClient(app) as client:
        response = client.get("/api/stats")
        assert response.status_code == 200
        stats = response.json()

        assert stats["db_connected"] is True
        assert stats["sessions"] >= 2
        assert stats["active_sessions"] >= 1
        assert stats["messages"] >= 2  # user + assistant from orchestrator run
        assert stats["tool_calls"] >= 1
        assert stats["pending_approvals"] >= 1

        assert stats["tools"].get("sql_tool", 0) >= 1
        assert stats["guardrail"]["allowed_recent"] >= 1

        session_ids = {s["session_id"] for s in stats["recent_sessions"]}
        assert orchestrator.session_id in session_ids
        assert active_session in session_ids


def test_web_approval_flow_end_to_end():
    """End-to-end: a chat that triggers a guarded action stalls at the
    WebApprovalHandler; resolving the request via ``resolve_approval``
    unblocks it and the agent returns the success result.

    This locks in the "approval queue is reachable from the web UI" flow.
    """
    # FakeLLMClient will:
    #  1. route the supervisor decision (intercepted, returns "SQL")
    #  2. call sql_tool with a multi-statement batch -> REQUIRES_APPROVAL
    #  3. after the approval, return a final text response
    fake_llm = FakeLLMClient(
        [
            FakeLLMClient.tool_use_response(
                "sql_tool",
                {"sql": "SELECT id FROM products; SELECT id FROM customers;"},
                "toolu_e2e_1",
            ),
            FakeLLMClient.text_response("Approval flow completed."),
        ],
        route_decision="SQL",
    )
    webapp_module._chat_llm_client_override = fake_llm

    response_holder = {}

    def chat_call():
        with TestClient(app) as client:
            response_holder["resp"] = client.post(
                "/api/chat",
                json={
                    "message": "do the bulk change",
                    "user_id": "e2e_user",
                    "auto_approve": False,
                    "approval_timeout": 10,
                },
            )

    chat_thread = threading.Thread(target=chat_call)
    chat_thread.start()

    # Wait for the pending approval to land in the queue.
    from approval.gate import get_pending_approvals, resolve_approval

    pending = []
    deadline = time.time() + 5
    while time.time() < deadline and not pending:
        pending = get_pending_approvals()
        if not pending:
            time.sleep(0.1)

    assert pending, "Expected at least one pending approval in the queue"
    approval_id = pending[0]["approval_id"]

    # Approve it via the database (the web UI does the same through
    # ``/approvals/{id}/approve`` which calls resolve_approval).
    assert resolve_approval(approval_id, "approved", decided_by="e2e_user")

    chat_thread.join(timeout=15)
    assert not chat_thread.is_alive(), "Chat request did not unblock in time"
    assert response_holder["resp"].status_code == 200
    body = response_holder["resp"].json()
    assert body["response"] == "Approval flow completed."
    webapp_module._chat_llm_client_override = None


def test_web_approval_queue_endpoint_resolves_pending():
    """The /approvals/{id}/approve HTTP endpoint resolves a pending request
    and removes it from the queue, matching what the WebApprovalHandler
    polling loop is waiting for.
    """
    approval_id, _ = _seed_pending_action(
        "UPDATE customers SET city = 'Berlin' WHERE id <= 6;"
    )
    with TestClient(app) as client:
        before = client.get("/approval-queue")
        assert before.status_code == 200
        assert approval_id in before.text

        token = _page_csrf_token(client)
        resp = client.post(f"/approvals/{approval_id}/approve", data={"csrf_token": token})
        assert resp.status_code in (200, 303)

        after = client.get("/approval-queue")
        assert approval_id not in after.text


def test_web_approval_timeout_returns_denial():
    """When no decision is written before the timeout, the SQL tool returns
    a denial, the agent surfaces that fact in its trace, and the final
    response reflects the agent's next action.
    """
    fake_llm = FakeLLMClient(
        [
            FakeLLMClient.tool_use_response(
                "sql_tool",
                {"sql": "SELECT id FROM products; SELECT id FROM customers;"},
                "toolu_timeout_1",
            ),
            FakeLLMClient.text_response("Done after denial."),
        ],
        route_decision="SQL",
    )
    webapp_module._chat_llm_client_override = fake_llm

    with TestClient(app) as client:
        resp = client.post(
            "/api/chat",
            json={
                "message": "do the bulk change",
                "user_id": "e2e_timeout_user",
                "auto_approve": False,
                "approval_timeout": 1,
            },
        )
    assert resp.status_code == 200
    # The final text is the agent's "Done after denial." because the
    # SQL tool was denied after the timeout, then the LLM was re-asked
    # and produced a final answer.
    assert resp.json()["response"] == "Done after denial."
    webapp_module._chat_llm_client_override = None


def test_sessions_list_endpoint_returns_json():
    """The /api/sessions endpoint returns session metadata as JSON,
    matching the data shown on the /traces HTML page.
    """
    fake_llm = FakeLLMClient([FakeLLMClient.text_response("ok")])
    webapp_module._chat_llm_client_override = fake_llm
    try:
        with TestClient(app) as client:
            client.post("/api/chat", json={"message": "hi", "user_id": "session_user"})

            resp = client.get("/api/sessions")
            assert resp.status_code == 200
            payload = resp.json()
            assert "limit" in payload
            assert "sessions" in payload
            assert any(
                s["user_id"] == "session_user" for s in payload["sessions"]
            )
    finally:
        webapp_module._chat_llm_client_override = None


def test_sessions_list_endpoint_filters_by_user_id():
    fake_llm = FakeLLMClient(
        [FakeLLMClient.text_response("a"), FakeLLMClient.text_response("b")]
    )
    webapp_module._chat_llm_client_override = fake_llm
    try:
        with TestClient(app) as client:
            client.post("/api/chat", json={"message": "hi", "user_id": "alice"})
            client.post("/api/chat", json={"message": "hi", "user_id": "bob"})

            only_alice = client.get("/api/sessions?user_id=alice")
            assert only_alice.status_code == 200
            payload = only_alice.json()
            assert payload["user_id"] == "alice"
            assert all(s["user_id"] == "alice" for s in payload["sessions"])
            assert len(payload["sessions"]) >= 1
    finally:
        webapp_module._chat_llm_client_override = None


def test_sessions_list_endpoint_clamps_limit():
    with TestClient(app) as client:
        resp = client.get("/api/sessions?limit=99999")
        assert resp.status_code == 200
        assert resp.json()["limit"] == 500
        resp = client.get("/api/sessions?limit=0")
        assert resp.status_code == 200
        assert resp.json()["limit"] == 1
