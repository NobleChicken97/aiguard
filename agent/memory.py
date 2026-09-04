import json
from typing import List, Dict, Any

from app_util import new_uuid as _uuid, now_utc as _now
from db.database import get_connection

# Process-wide Redis reachability verdict (Phase 3 hardening). A dead Redis
# must degrade to None fast on EVERY construction, not re-pay a connect
# timeout per chat turn (measured 2s stalls against dead localhost).
# On failure we skip re-pinging for a minute; a success always re-verifies
# (cheap when Redis is actually up). GIL-atomic float ops: no lock needed.
_REDIS_DOWN_UNTIL = 0.0
_REDIS_DOWN_TTL_SECONDS = 60.0


class ShortTermMemory:
    """Holds the full message + action history for the current session."""

    def __init__(self, session_id=None):
        self._messages: List[Dict[str, Any]] = []
        self.session_id = session_id
        self.redis_client = None
        self._try_init_redis()

    def _try_init_redis(self):
        import config
        import time as _time

        global _REDIS_DOWN_UNTIL
        try:
            import redis
            if _time.monotonic() < _REDIS_DOWN_UNTIL:
                self.redis_client = None
                return
            # Bounded timeouts: an unreachable Redis must degrade to None
            # quickly, never stall a request path for seconds (measured 2-4s
            # bare-ping hangs against a dead localhost on some stacks).
            client = redis.Redis.from_url(
                config.REDIS_URL,
                decode_responses=True,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
            client.ping()
            self.redis_client = client
        except Exception:
            _REDIS_DOWN_UNTIL = _time.monotonic() + _REDIS_DOWN_TTL_SECONDS
            self.redis_client = None

    def set_session_id(self, session_id):
        self.session_id = session_id
        if self.redis_client:
            cached = self.redis_client.get(f"session:{self.session_id}:messages")
            if cached:
                self._messages = json.loads(cached)

    def _sync(self):
        if self.redis_client and self.session_id:
            self.redis_client.set(f"session:{self.session_id}:messages", json.dumps(self._messages), ex=86400) # 24h expiry

    def add_user_message(self, content):
        self._messages.append({"role": "user", "content": content})
        self._sync()

    def add_assistant_message(self, content_blocks):
        self._messages.append({"role": "assistant", "content": content_blocks})
        self._sync()

    def add_tool_result(self, tool_use_id, content, is_error=False):
        self._messages.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": content,
                    "is_error": is_error,
                }
            ],
        })
        self._sync()

    def get_messages(self):
        return list(self._messages)

    def to_dict(self):
        return {"messages": self._messages}


class LongTermMemory:
    """Persisted facts that survive across sessions."""

    def __init__(self):
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = get_connection()
        return self._conn

    def save_facts(self, user_id, facts, source_session_id=None):
        for fact_text in facts:
            self.conn.execute(
                """INSERT INTO app_memory_facts (fact_id, user_id, fact_text, source_session_id, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (_uuid(), user_id, fact_text, source_session_id, _now()),
            )
        self.conn.commit()

    def retrieve_facts(self, user_id, limit=20):
        rows = self.conn.execute(
            "SELECT fact_text, created_at FROM app_memory_facts WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_facts(self, user_id):
        rows = self.conn.execute(
            "SELECT fact_id, fact_text, source_session_id, created_at FROM app_memory_facts WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_fact(self, fact_id, user_id=None):
        """Delete a fact by id; returns True when a row was actually deleted.

        When ``user_id`` is given the delete is scoped to that user's facts,
        so a valid fact id belonging to someone else resolves to False
        rather than deleting across users.
        """
        sql = "DELETE FROM app_memory_facts WHERE fact_id = ?"
        params = [fact_id]
        if user_id is not None:
            sql += " AND user_id = ?"
            params.append(user_id)
        cursor = self.conn.execute(sql, tuple(params))
        self.conn.commit()
        return (cursor.rowcount or 0) > 0

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


def distill_facts_from_session(messages, llm_client=None):
    """Extract key facts from a session's conversation history.

    If an LLM client is provided, uses it to distill facts.
    Otherwise, returns a simple summary of user messages.
    """
    user_messages = [
        m for m in messages
        if m.get("role") == "user" and isinstance(m.get("content"), str)
    ]

    if llm_client is not None and user_messages:
        conversation = "\n".join(m["content"] for m in user_messages)
        try:
            response = llm_client.call(
                system="Extract 1-3 concise factual statements about the user from this conversation. Return one fact per line, no numbering or bullets.",
                messages=[{"role": "user", "content": conversation}],
            )
            facts = [
                line.strip()
                for line in response.text.strip().split("\n")
                if line.strip()
            ]
            return facts[:5]
        except Exception:
            pass

    facts = []
    for m in user_messages:
        content = m["content"]
        if len(content) > 5:
            facts.append(content[:200])
    return facts[:5]
