import sqlglot
from collections import OrderedDict
import threading

from tools.base import Tool, ToolResult
from approval.gate import ApprovalPending
from guardrails.sql_guardrail import (
    SHAPE_COLUMN_LIMIT,
    SHAPE_TABLE_LIMIT,
    SQLGuardrail,
    VERDICT_REQUIRES_APPROVAL,
    VERDICT_ALLOWED,
    get_allowed_schema_columns,
    query_shape,
)
from guardrails.pii_guardrail import PIIGuardrail
from db.database import get_connection
import config


class _ThreadSafeLRUCache:
    """Bounded LRU cache. Thread-safe via a single lock.

    Used to short-circuit repeated identical SELECTs inside a long-running
    session. Cleared on any successful write so we never return stale rows
    after an INSERT/UPDATE/DELETE. When ``max_size`` is 0, the cache is
    disabled (all operations are no-ops).
    """

    def __init__(self, max_size):
        self._max_size = max(0, int(max_size))
        self._data = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key):
        if self._max_size == 0 or key is None:
            return None
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def set(self, key, value):
        if self._max_size == 0 or key is None:
            return
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._max_size:
                self._data.popitem(last=False)

    def clear(self):
        with self._lock:
            self._data.clear()

    def __len__(self):
        with self._lock:
            return len(self._data)


class SQLTool(Tool):
    """Guarded SQL execution against the allow-listed schema.

    Every query crosses ``SQLGuardrail`` before execution. Successful writes
    (INSERT/UPDATE/DELETE) clear the SELECT cache so we never return stale
    rows after a mutation. The cache itself is a bounded, thread-safe LRU
    sized by ``config.SQL_QUERY_CACHE_SIZE``; setting that to 0 disables it.
    """

    def __init__(self, approval_handler=None):
        super().__init__()
        # Live schema enables precise SELECT * expansion in the column
        # policy; unknown schema only ever fails closed, never open.
        self.guardrail = SQLGuardrail(schema_columns=get_allowed_schema_columns())
        self.approval_handler = approval_handler
        self._query_cache = _ThreadSafeLRUCache(config.SQL_QUERY_CACHE_SIZE)

    def get_name(self):
        return "sql_tool"

    def get_description(self):
        return (
            "Execute a SQL query against the e-commerce database. "
            "Provide a single SQLite-compatible SQL statement. "
            "Allowed tables: customers, products, orders, order_items."
        )

    def get_input_schema(self):
        return {
            "type": "object",
            "properties": {
                "sql": {
                    "type": "string",
                    "description": "The SQL query to execute. Must target only allowed tables.",
                }
            },
            "required": ["sql"],
        }

    def execute(self, sql=None, _call_id=None, _session_id=None, _trace=None, **kwargs):
        if not sql:
            return ToolResult(
                status="failed",
                output="Error: 'sql' parameter is required.",
            )

        call_id = _call_id or "unknown"
        session_id = _session_id or "unknown"

        result = self.guardrail.check(sql)

        if _trace:
            _trace.log_guardrail_verdict(call_id, sql, result.verdict, result.reason)

        if result.blocked:
            return ToolResult(
                status="blocked",
                output=f"BLOCKED by guardrail: {result.reason}",
                guardrail_verdict=result.verdict,
            )

        if result.requires_approval:
            gated = self._approval_gate(result.reason, sql, call_id, session_id, _trace)
            if gated is not None:
                return gated

        if result.statement_type in ("UPDATE", "DELETE") and result.verdict == VERDICT_ALLOWED:
            approval_needed = self._check_row_count(sql, result, call_id, session_id, _trace)
            if approval_needed is not None:
                return approval_needed

        # Log-only anomaly signal (never blocks): unusually wide queries get
        # a trace event for future abuse-detection work.
        shape = query_shape(sql)
        if _trace and (
            len(shape["tables"]) > SHAPE_TABLE_LIMIT
            or shape["columns"] > SHAPE_COLUMN_LIMIT
            or shape["star"]
        ):
            _trace.log("query_shape_anomaly", {"call_id": call_id, **shape})

        return self._execute_sql(sql, call_id, result)

    def _approval_gate(self, reason, sql, call_id, session_id, _trace):
        """Shared approval flow for every gated write.

        Returns a ToolResult when the action must not proceed (no handler
        configured, or denied by the human) and ``None`` when approved.
        Non-blocking handlers (Phase 3) raise ApprovalPending instead of
        waiting: the worker unwinds and the thread is released.
        """
        if getattr(self.approval_handler, "non_blocking", False):
            approval_id = self.approval_handler.create_pending(
                call_id, session_id, reason, "sql_tool", {"sql": sql}
            )
            if _trace:
                _trace.log_approval_request(call_id, reason)
            raise ApprovalPending(
                approval_id, call_id, session_id, reason, "sql_tool", {"sql": sql}
            )
        if self.approval_handler is None:
            return ToolResult(
                status="blocked",
                output=f"Approval required but no handler configured: {reason}",
                guardrail_verdict=VERDICT_REQUIRES_APPROVAL,
                approval_reason=reason,
            )
        if _trace:
            _trace.log_approval_request(call_id, reason)
        approved = self.approval_handler.request_approval(
            call_id, session_id, reason, "sql_tool", {"sql": sql}
        )
        if _trace:
            _trace.log_approval_decision(call_id, "approved" if approved else "denied")
        if not approved:
            return ToolResult(
                status="denied",
                output=f"Action denied by human: {reason}",
                guardrail_verdict=VERDICT_REQUIRES_APPROVAL,
                approval_reason=reason,
            )
        return None

    def _check_row_count(self, sql, guardrail_result, call_id, session_id, _trace):
        count = self._estimate_affected_rows(sql)
        if count is None:
            # Fail closed: the blast radius cannot be bounded, so the
            # statement is treated like any other bulk operation instead of
            # silently skipping the row-count gate.
            reason = (
                f"{guardrail_result.statement_type} affected-row count could not be "
                f"estimated, so it cannot be shown to stay within the threshold "
                f"({config.RISKY_ROW_THRESHOLD} rows). This operation requires approval."
            )
            return self._approval_gate(reason, sql, call_id, session_id, _trace)

        if count > config.RISKY_ROW_THRESHOLD:
            reason = (
                f"{guardrail_result.statement_type} would affect {count} rows "
                f"(threshold: {config.RISKY_ROW_THRESHOLD}). "
                f"This bulk operation requires approval."
            )
            return self._approval_gate(reason, sql, call_id, session_id, _trace)
        return None

    def _estimate_affected_rows(self, sql):
        """Best-effort row count for an UPDATE/DELETE's WHERE clause.

        Returns the count, or ``None`` when it cannot be computed (parse
        failure, missing table, count-query error). Callers treat ``None``
        as fail-closed and require approval.
        """
        try:
            parsed = sqlglot.parse_one(sql, read="sqlite")

            if hasattr(parsed, "args") and parsed.args.get("where") is not None:
                where_node = parsed.args["where"]
                table = None
                for t in parsed.find_all(sqlglot.exp.Table):
                    table = t
                    break

                if table is None:
                    return None

                count_sql = (
                    f"SELECT COUNT(*) AS cnt FROM {table.name} WHERE {where_node.this.sql()}"
                )
                conn = get_connection()
                try:
                    row = conn.execute(count_sql).fetchone()
                    return row["cnt"] if row else 0
                finally:
                    conn.close()
            return None
        except Exception:
            return None

    def _execute_sql(self, sql, call_id, guardrail_result):
        conn = get_connection()
        try:
            # Check query cache for SELECTs
            if guardrail_result.statement_type == "SELECT":
                cached = self._query_cache.get(sql)
                if cached is not None:
                    return cached

            cursor = conn.execute(sql)
            if guardrail_result.statement_type == "SELECT":
                columns = [desc[0] for desc in cursor.description] if cursor.description else []
                rows = cursor.fetchall()
                if not rows:
                    result = ToolResult(
                        status="success",
                        output="Query returned 0 rows.",
                        guardrail_verdict=guardrail_result.verdict,
                    )
                    self._query_cache.set(sql, result)
                    return result
                formatted = self._format_rows(columns, rows)
                result = ToolResult(
                    status="success",
                    output=formatted,
                    guardrail_verdict=guardrail_result.verdict,
                )
                self._query_cache.set(sql, result)
                return result
            else:
                conn.commit()
                affected = cursor.rowcount
                # Invalidate the cache: any write may have changed the rows
                # a subsequent SELECT would return, so a stale cached SELECT
                # would be incorrect.
                self._query_cache.clear()
                return ToolResult(
                    status="success",
                    output=f"Statement executed successfully. {affected} row(s) affected.",
                    guardrail_verdict=guardrail_result.verdict,
                )
        except Exception as e:
            return ToolResult(
                status="failed",
                output=f"SQL execution error: {e}",
                guardrail_verdict=guardrail_result.verdict,
            )
        finally:
            conn.close()

    def _format_rows(self, columns, rows, max_rows=50):
        # Index by column NAME, never by position/iteration: sqlite3.Row
        # iterates values but psycopg2 RealDictRow iterates KEYS, so `for v
        # in row` silently returns the header as data on PostgreSQL (caught
        # live in prod, Sep 2026 — every PG SELECT rendered column names).
        lines = [" | ".join(columns)]
        lines.append("-" * len(lines[0]))
        for row in rows[:max_rows]:
            try:
                values = [row[c] for c in columns]
            except (KeyError, IndexError, TypeError):
                values = [row[i] for i in range(len(columns))]
            lines.append(" | ".join(str(v) for v in values))
        if len(rows) > max_rows:
            lines.append(f"... ({len(rows) - max_rows} more rows)")
            
        formatted_text = "\n".join(lines)
        masked = PIIGuardrail.mask_pii(formatted_text)
        if config.PII_NER_ENABLED:
            masked = PIIGuardrail.mask_pii_ner(masked)
        return masked
