"""Tests for the webapp in-process rate limiters.

Covers the standalone primitives (TokenBucket, ConcurrentStreamGuard) and
the integration with /api/chat and /api/stream.
"""

import sys
import threading
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient
from db.database import reset_db
from db.seed import seed_demo_data
from webapp import app
import webapp as webapp_module
import webapp_ratelimit
from webapp_ratelimit import (
    ConcurrentStreamGuard,
    TokenBucket,
    configure as configure_ratelimit,
)


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


class TestTokenBucket:
    def test_allows_under_cap(self):
        b = TokenBucket(3)
        for _ in range(3):
            assert b.allow("k")

    def test_blocks_over_cap(self):
        b = TokenBucket(2)
        assert b.allow("k")
        assert b.allow("k")
        assert not b.allow("k")

    def test_keys_are_independent(self):
        b = TokenBucket(1)
        assert b.allow("a")
        assert b.allow("b")
        assert not b.allow("a")

    def test_disabled_when_zero(self):
        b = TokenBucket(0)
        for _ in range(1000):
            assert b.allow("k")

    def test_window_expiry(self):
        b = TokenBucket(1, window_seconds=0.2)
        assert b.allow("k")
        assert not b.allow("k")
        time.sleep(0.25)
        assert b.allow("k")

    def test_reset(self):
        b = TokenBucket(1)
        assert b.allow("k")
        assert not b.allow("k")
        b.reset("k")
        assert b.allow("k")


class TestConcurrentStreamGuard:
    def test_acquire_under_cap(self):
        g = ConcurrentStreamGuard(2)
        s1 = g.acquire("ip1")
        s2 = g.acquire("ip1")
        s1.__exit__(None, None, None)
        s2.__exit__(None, None, None)

    def test_acquire_over_cap_raises(self):
        g = ConcurrentStreamGuard(1)
        s1 = g.acquire("ip1")
        try:
            with pytest.raises(g.StreamLimitExceeded):
                g.acquire("ip1")
        finally:
            s1.__exit__(None, None, None)

    def test_release_frees_slot(self):
        g = ConcurrentStreamGuard(1)
        s1 = g.acquire("ip1")
        s1.__exit__(None, None, None)
        s2 = g.acquire("ip1")
        s2.__exit__(None, None, None)

    def test_keys_are_independent(self):
        g = ConcurrentStreamGuard(1)
        s1 = g.acquire("ip1")
        s2 = g.acquire("ip2")
        s1.__exit__(None, None, None)
        s2.__exit__(None, None, None)

    def test_disabled_when_zero(self):
        g = ConcurrentStreamGuard(0)
        for _ in range(50):
            s = g.acquire("ip1")
            s.__exit__(None, None, None)


class TestChatEndpointRateLimit:
    def test_429_when_chat_per_min_exceeded(self, monkeypatch):
        fake_llm = FakeLLMClient([FakeLLMClient.text_response("ok")])
        webapp_module._chat_llm_client_override = fake_llm
        try:
            with TestClient(app) as client:
                # The lifespan has already configured the limiter to the
                # default (disabled). Reconfigure with a small cap.
                configure_ratelimit(chat_per_min=2, sse_max_per_ip=0)
                ok1 = client.post("/api/chat", json={"message": "hi", "user_id": "u1"})
                ok2 = client.post("/api/chat", json={"message": "hi", "user_id": "u1"})
                blocked = client.post("/api/chat", json={"message": "hi", "user_id": "u1"})

            assert ok1.status_code == 200
            assert ok2.status_code == 200
            assert blocked.status_code == 429
            assert "Too many" in blocked.text
        finally:
            webapp_module._chat_llm_client_override = None
            configure_ratelimit(chat_per_min=0, sse_max_per_ip=0)

    def test_rate_limit_is_per_ip(self, monkeypatch):
        fake_llm = FakeLLMClient(
            [
                FakeLLMClient.text_response("a"),
                FakeLLMClient.text_response("b"),
            ]
        )
        webapp_module._chat_llm_client_override = fake_llm
        try:
            with TestClient(app) as client:
                configure_ratelimit(chat_per_min=1, sse_max_per_ip=0)
                ip1 = client.post(
                    "/api/chat",
                    json={"message": "hi"},
                    headers={"X-Forwarded-For": "1.2.3.4"},
                )
                ip2 = client.post(
                    "/api/chat",
                    json={"message": "hi"},
                    headers={"X-Forwarded-For": "5.6.7.8"},
                )

            assert ip1.status_code == 200
            assert ip2.status_code == 200
        finally:
            webapp_module._chat_llm_client_override = None
            configure_ratelimit(chat_per_min=0, sse_max_per_ip=0)


class TestSSEStreamLimit:
    def test_429_when_sse_per_ip_exceeded(self):
        """The /api/stream endpoint rejects a second stream from the same
        IP while one is already open. The guard is acquired inside the
        endpoint and released when the streaming response closes; we
        verify the unit-level guard because the TestClient cannot easily
        hold two streams from the same client concurrently.
        """
        from webapp_ratelimit import ConcurrentStreamGuard

        g = ConcurrentStreamGuard(1)
        s1 = g.acquire("9.9.9.9")
        try:
            with pytest.raises(g.StreamLimitExceeded):
                g.acquire("9.9.9.9")
        finally:
            s1.__exit__(None, None, None)

    def test_sse_limit_is_per_ip(self):
        from webapp_ratelimit import ConcurrentStreamGuard

        g = ConcurrentStreamGuard(1)
        s1 = g.acquire("10.0.0.1")
        try:
            s2 = g.acquire("10.0.0.2")
            s2.__exit__(None, None, None)
        finally:
            s1.__exit__(None, None, None)

    def test_sse_endpoint_rejects_over_cap(self):
        """End-to-end at the endpoint: hold the only stream slot via the
        guard, then a second request from the same client gets 429.
        """
        with TestClient(app) as client:
            configure_ratelimit(chat_per_min=0, sse_max_per_ip=1)
            try:
                # Hold the slot in-process before the request so the
                # endpoint sees the cap is already full.
                from webapp_ratelimit import RL_STATE

                held = RL_STATE["stream_guard"].acquire("7.7.7.7")
                try:
                    resp = client.get(
                        "/api/stream", headers={"X-Forwarded-For": "7.7.7.7"}
                    )
                    assert resp.status_code == 429
                finally:
                    held.__exit__(None, None, None)
            finally:
                configure_ratelimit(chat_per_min=0, sse_max_per_ip=0)
