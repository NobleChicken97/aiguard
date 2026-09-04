"""Memory fact management: DELETE /api/users/{user_id}/memory/{fact_id}.

The data-layer delete always existed; this suite pins the API contract:
owner-scoped deletion, 404 on path mismatch or unknown fact ids, and
facts disappear from the owner's memory listing after deletion.
"""

import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from agent.memory import LongTermMemory
from auth import SESSION_COOKIE, create_user, sign_session
from db.database import reset_db
from db.seed import seed_demo_data
from webapp import app


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _authed_client(user_id):
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, sign_session(user_id))
    return client


def _save_fact(user_id, text):
    ltm = LongTermMemory()
    try:
        ltm.save_facts(user_id, [text], source_session_id="s1")
        return ltm.get_all_facts(user_id)[0]["fact_id"]
    finally:
        ltm.close()


def test_delete_removes_fact_from_listing():
    uid = create_user("del@test.local", "testpass123")
    fact_id = _save_fact(uid, "Likes cold coffee.")
    with _authed_client(uid) as client:
        resp = client.delete(f"/api/users/{uid}/memory/{fact_id}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": fact_id, "user_id": uid}
        assert client.get(f"/api/users/{uid}/memory").json()["facts"] == []


def test_delete_is_scoped_to_the_path_user():
    owner_id = create_user("owner@test.local", "testpass123")
    other_id = create_user("other@test.local", "testpass123")
    fact_id = _save_fact(owner_id, "Secret fact.")
    with _authed_client(owner_id) as client:
        resp = client.delete(f"/api/users/{other_id}/memory/{fact_id}")
        assert resp.status_code == 404
        facts = client.get(f"/api/users/{owner_id}/memory").json()["facts"]
        assert [f["fact_id"] for f in facts] == [fact_id]


def test_delete_unknown_fact_returns_404():
    uid = create_user("any@test.local", "testpass123")
    with _authed_client(uid) as client:
        resp = client.delete(f"/api/users/{uid}/memory/no-such-fact-id")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
