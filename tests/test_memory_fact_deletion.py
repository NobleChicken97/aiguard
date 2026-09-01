"""Memory fact management: DELETE /api/users/{user_id}/memory/{fact_id}.

The data-layer delete always existed; this suite pins the API contract:
scoped to the user, 404 on unknown fact ids, and facts disappear from the
user's memory listing after deletion.
"""

import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from agent.memory import LongTermMemory
from db.database import reset_db
from db.seed import seed_demo_data
from webapp import app


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def _save_fact(user_id, text):
    ltm = LongTermMemory()
    try:
        ltm.save_facts(user_id, [text], source_session_id="s1")
        return ltm.get_all_facts(user_id)[0]["fact_id"]
    finally:
        ltm.close()


def test_delete_removes_fact_from_listing():
    fact_id = _save_fact("del_user", "Likes cold coffee.")
    with TestClient(app) as client:
        resp = client.delete(f"/api/users/del_user/memory/{fact_id}")
        assert resp.status_code == 200
        assert resp.json() == {"deleted": fact_id, "user_id": "del_user"}
        assert client.get("/api/users/del_user/memory").json()["facts"] == []


def test_delete_is_scoped_to_the_path_user():
    fact_id = _save_fact("owner_user", "Secret fact.")
    with TestClient(app) as client:
        resp = client.delete(f"/api/users/other_user/memory/{fact_id}")
        assert resp.status_code == 404
        facts = client.get("/api/users/owner_user/memory").json()["facts"]
        assert [f["fact_id"] for f in facts] == [fact_id]


def test_delete_unknown_fact_returns_404():
    with TestClient(app) as client:
        resp = client.delete("/api/users/any_user/memory/no-such-fact-id")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()
