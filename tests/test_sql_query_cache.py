"""Tests for the bounded LRU SELECT cache in SQLTool.

Covers:
- Bounded size: oldest entries are evicted
- Thread-safety: concurrent reads/writes don't corrupt the cache
- Write invalidation: any successful INSERT/UPDATE/DELETE clears the cache
- Disabled mode: size=0 turns the cache into a no-op
- SELECT-only: writes and failures don't pollute the cache
"""

import sys
import threading

import pytest

sys.path.insert(0, ".")

from tools.sql_tool import _ThreadSafeLRUCache, SQLTool


class TestThreadSafeLRUCache:
    def test_basic_get_set(self):
        c = _ThreadSafeLRUCache(3)
        c.set("a", 1)
        c.set("b", 2)
        assert c.get("a") == 1
        assert c.get("b") == 2
        assert c.get("missing") is None

    def test_bounded_eviction(self):
        c = _ThreadSafeLRUCache(2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)  # evicts "a"
        assert c.get("a") is None
        assert c.get("b") == 2
        assert c.get("c") == 3
        assert len(c) == 2

    def test_lru_recency(self):
        c = _ThreadSafeLRUCache(2)
        c.set("a", 1)
        c.set("b", 2)
        c.get("a")  # mark "a" as recently used
        c.set("c", 3)  # evicts "b"
        assert c.get("a") == 1
        assert c.get("b") is None
        assert c.get("c") == 3

    def test_clear(self):
        c = _ThreadSafeLRUCache(3)
        c.set("a", 1)
        c.set("b", 2)
        c.clear()
        assert len(c) == 0
        assert c.get("a") is None

    def test_disabled_when_max_size_is_zero(self):
        c = _ThreadSafeLRUCache(0)
        c.set("a", 1)
        c.set("b", 2)
        assert len(c) == 0
        assert c.get("a") is None
        c.clear()
        assert len(c) == 0

    def test_overwrite_keeps_recency(self):
        c = _ThreadSafeLRUCache(2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("a", 11)  # overwrites, moves to most-recent
        c.set("c", 3)   # evicts "b"
        assert c.get("a") == 11
        assert c.get("b") is None
        assert c.get("c") == 3

    def test_concurrent_set_get(self):
        c = _ThreadSafeLRUCache(100)
        errors = []

        def writer(start):
            try:
                for i in range(100):
                    c.set(f"{start}:{i}", i)
            except Exception as e:
                errors.append(e)

        def reader(start):
            try:
                for i in range(100):
                    c.get(f"{start}:{i}")
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(4):
            t = threading.Thread(target=writer, args=(i,))
            threads.append(t)
            t = threading.Thread(target=reader, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Concurrent access raised: {errors}"


class TestSQLToolCacheIntegration:
    """The cache is wired into SQLTool via _execute_sql.

    These tests confirm that:
    - repeated SELECTs are short-circuited after the first call (cache hit
      on the second execute)
    - any successful write invalidates the cache (next SELECT re-runs)
    - size=0 (disabled) does not short-circuit
    """

    def test_select_cache_short_circuits_repeat(self):
        from approval.gate import AutoApproveHandler

        tool = SQLTool(approval_handler=AutoApproveHandler())
        tool._query_cache.clear()

        sql = "SELECT id, name FROM products ORDER BY id LIMIT 3"
        first = tool.execute(sql=sql, _call_id="c1", _session_id="s1")
        assert first.status == "success"
        # The first call must have populated the cache.
        assert sql in tool._query_cache._data
        first_cached = tool._query_cache.get(sql)

        second = tool.execute(sql=sql, _call_id="c2", _session_id="s1")
        # Second call returns the same cached ToolResult object identity.
        assert second is first_cached
        assert second.output == first.output

    def test_write_invalidates_select_cache(self):
        from approval.gate import AutoApproveHandler

        tool = SQLTool(approval_handler=AutoApproveHandler())
        tool._query_cache.clear()

        select_sql = "SELECT id, name FROM products ORDER BY id LIMIT 3"
        update_sql = "UPDATE products SET stock = stock + 1 WHERE id = 1"

        tool.execute(sql=select_sql, _call_id="c1", _session_id="s1")
        # Confirm it's cached.
        assert tool._query_cache.get(select_sql) is not None

        # A successful write must clear the cache.
        write_result = tool.execute(sql=update_sql, _call_id="c2", _session_id="s1")
        assert write_result.status == "success"
        assert tool._query_cache.get(select_sql) is None

        # Next SELECT re-populates the cache.
        tool.execute(sql=select_sql, _call_id="c3", _session_id="s1")
        assert tool._query_cache.get(select_sql) is not None

    def test_disabled_cache_does_not_short_circuit(self):
        from approval.gate import AutoApproveHandler

        tool = SQLTool(approval_handler=AutoApproveHandler())
        # Force the cache to disabled.
        tool._query_cache = _ThreadSafeLRUCache(0)

        sql = "SELECT id, name FROM products ORDER BY id LIMIT 3"
        first = tool.execute(sql=sql, _call_id="c1", _session_id="s1")
        second = tool.execute(sql=sql, _call_id="c2", _session_id="s1")

        # With caching disabled, every call goes to the DB and returns a
        # freshly-built ToolResult. The two objects are not the same.
        assert first is not second
        assert first.output == second.output
        assert len(tool._query_cache) == 0

    def test_blocked_query_does_not_pollute_cache(self):
        from approval.gate import AutoApproveHandler

        tool = SQLTool(approval_handler=AutoApproveHandler())
        tool._query_cache.clear()

        # Bad table name triggers the guardrail; SQLTool returns "blocked"
        # without touching the cache.
        bad = "SELECT * FROM nonexistent_table"
        result = tool.execute(sql=bad, _call_id="c1", _session_id="s1")
        assert result.status == "blocked"
        assert tool._query_cache.get(bad) is None
