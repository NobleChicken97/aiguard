"""Phase 6 observability: /metrics exposition, JSON logs, deep health.

Also pins the DEPLOYMENT secrets-management section (docs are a Phase 6
deliverable: an on-call reader must find the secrets path without code).
"""

import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient
from app_logging import configure_logging, get_logger
from auth import SESSION_COOKIE, create_user, sign_session
from db.database import reset_db
from db.seed import seed_demo_data
from webapp import app
import webapp as webapp_module


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _make_user(password="testpass123"):
    from uuid import uuid4

    return create_user(f"{uuid4().hex[:8]}@test.local", password)


def _uuid():
    from uuid import uuid4

    return uuid4().hex[:8]


def _authed_client(user_id):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, sign_session(user_id))
    return client


def test_metrics_requires_login():
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == 401


def test_metrics_exposition_shape():
    uid = _make_user()
    webapp_module._chat_llm_client_override = FakeLLMClient(
        [FakeLLMClient.text_response("shaped.")]
    )
    try:
        with _authed_client(uid) as client:
            assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
            resp = client.get("/metrics")
    finally:
        webapp_module._chat_llm_client_override = None
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        body = resp.text
        for series in (
            "aiguard_sessions_total",
            "aiguard_tool_calls_total",
            "aiguard_guardrail_verdicts",
            "aiguard_approvals_pending",
            "aiguard_http_requests",
            "aiguard_uptime_seconds",
        ):
            assert series in body, series
        assert "# HELP aiguard_sessions_total" in body
        assert "# TYPE aiguard_sessions_total gauge" in body


def test_metrics_counts_chat_turn():
    uid = _make_user()
    webapp_module._chat_llm_client_override = FakeLLMClient(
        [FakeLLMClient.text_response("metered.")]
    )
    try:
        with _authed_client(uid) as client:
            assert client.post("/api/chat", json={"message": "hi"}).status_code == 200
            body = client.get("/metrics").text
    finally:
        webapp_module._chat_llm_client_override = None
    chat_lines = [
        line for line in body.splitlines()
        if line.startswith("aiguard_http_requests{") and 'path="/api/chat"' in line
    ]
    assert chat_lines, "expected an http_requests series for /api/chat"
    assert sum(float(line.rsplit(" ", 1)[1]) for line in chat_lines) >= 1


def test_json_log_format_emits_parseable_objects():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf, format="json")
    try:
        get_logger("test.jsonmod").info("hello json")
    finally:
        configure_logging(level="INFO")
    line = buf.getvalue().strip().splitlines()[-1]
    obj = json.loads(line)
    assert obj["level"] == "INFO"
    assert obj["msg"] == "hello json"
    assert obj["logger"] == "test.jsonmod"
    assert "ts" in obj


def test_health_detailed_shape_and_gating():
    with TestClient(app) as client:
        assert client.get("/health/detailed").status_code == 401
    uid = _make_user()
    with _authed_client(uid) as client:
        resp = client.get("/health/detailed")
        assert resp.status_code == 200  # always 200: scrape the status field
        payload = resp.json()
        assert payload["status"] in ("ok", "degraded")
        assert payload["checks"]["db"]["ok"] is True
        assert set(payload["checks"]["redis"]) >= {"ok", "mode"}
        assert isinstance(payload["checks"]["llm"]["configured"], bool)


def test_deployment_documents_secrets_management():
    text = (Path(".") / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert "Secrets Manager" in text
    assert "SESSION_SECRET" in text
    assert "never commit it" in text


def test_robots_txt_and_favicon_public():
    with TestClient(app) as client:
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert "Disallow: /" in robots.text
        icon = client.get("/favicon.ico")
        assert icon.status_code == 200
        assert icon.headers["content-type"].startswith("image/")


def test_seo_meta_present_in_pages():
    uid = create_user(f"{_uuid()}@test.local", "testpass123")
    with _authed_client(uid) as client:
        chat = client.get("/chat")
        assert chat.status_code == 200
        assert 'name="description"' in chat.text
        assert 'property="og:title"' in chat.text
        assert 'rel="canonical"' in chat.text
        assert 'href="/favicon.ico"' in chat.text
