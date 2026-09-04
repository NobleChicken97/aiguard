"""Redis-backed short-term memory (ticket 03).

Runs only when a Redis is reachable (``TEST_REDIS_URL`` — set in CI by the
redis service; absent locally, where these skip exactly like the
PG-gated tests). Covers: sync-to-Redis on write, restore on resume, and
graceful fallback when Redis is unreachable.
"""

import os
import sys

import pytest

sys.path.insert(0, ".")

import config
from agent.memory import ShortTermMemory


def _redis_url():
    url = os.getenv("TEST_REDIS_URL", "")
    if not url:
        pytest.skip("no TEST_REDIS_URL configured")
    try:
        import redis

        client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=2)
        client.ping()
    except Exception:
        pytest.skip("redis unreachable")
    return url


def test_messages_sync_to_redis(monkeypatch):
    url = _redis_url()
    monkeypatch.setattr(config, "REDIS_URL", url)
    mem = ShortTermMemory()
    mem.set_session_id("redis-sess-1")
    mem.add_user_message("hello redis")

    import redis

    raw = redis.Redis.from_url(url, decode_responses=True).get(
        "session:redis-sess-1:messages"
    )
    assert raw is not None and "hello redis" in raw


def test_state_restores_on_resume(monkeypatch):
    url = _redis_url()
    monkeypatch.setattr(config, "REDIS_URL", url)
    first = ShortTermMemory()
    first.set_session_id("redis-sess-2")
    first.add_user_message("remember me")

    second = ShortTermMemory()
    second.set_session_id("redis-sess-2")
    assert any(
        m.get("role") == "user" and "remember me" in str(m.get("content"))
        for m in second.get_messages()
    )


def test_graceful_fallback_when_redis_unreachable(monkeypatch):
    monkeypatch.setattr(config, "REDIS_URL", "redis://localhost:9/0")
    mem = ShortTermMemory()
    assert mem.redis_client is None
    mem.set_session_id("redis-sess-3")
    mem.add_user_message("local only")
    assert any("local only" in str(m.get("content")) for m in mem.get_messages())
