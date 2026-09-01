"""Visual query builder backend — Phase 8 UI fallback.

When the agent struggles with a complex schema question, a human can assemble
an equivalent SELECT visually instead of fighting prompts. This module owns
that feature end-to-end: schema introspection for the UI controls, validated
SELECT construction, guarded execution, and PII-masked results.

Safety model (mirrors the agent's sql_tool path):
- Only tables in ``config.ALLOWED_TABLES`` can be introspected or queried.
- Identifiers (table, columns, order-by) are validated against live schema
  introspection before interpolation; every filter value is a bound parameter
  (? placeholders, translated for PostgreSQL by the db wrapper).
- Only a single SELECT statement can ever be produced.
- Generated SQL still passes through SQLGuardrail.check() before execution,
  preserving the invariant that every execution path crosses the guardrail.
- Result cells go through PIIGuardrail masking exactly like sql_tool output.

Audit trail: each run persists one row to the dedicated ``app_builder_runs``
table (SQL, verdict, row count, timing). The table is deliberately separate
from ``app_tool_calls``/``app_trace_events`` so builder activity stays
visible on its own without ever polluting agent metrics.
"""

import time
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from typing import Literal

import config
from db.database import get_connection, _is_postgres
from guardrails.pii_guardrail import PIIGuardrail
from guardrails.sql_guardrail import SQLGuardrail


class QueryBuilderError(Exception):
    """Raised when a builder request references unknown schema objects."""


FILTER_OPERATORS = ("=", "!=", ">", ">=", "<", "<=", "LIKE")

_NUMERIC_TYPE_TOKENS = ("INT", "REAL", "FLOA", "DOUB", "NUM", "DEC")


class FilterCondition(BaseModel):
    column: str
    operator: Literal["=", "!=", ">", ">=", "<", "<=", "LIKE"]
    value: str


class QueryBuilderRequest(BaseModel):
    table: str
    columns: list[str] = []
    filters: list[FilterCondition] = []
    order_by: str | None = None
    order_dir: Literal["ASC", "DESC"] = "ASC"
    limit: int = Field(default=50, ge=1, le=200)


def _is_numeric_column(declared_type):
    return any(token in str(declared_type).upper() for token in _NUMERIC_TYPE_TOKENS)


def _introspect_table_columns(conn, table):
    if _is_postgres():
        rows = conn.execute(
            """SELECT column_name, data_type FROM information_schema.columns
               WHERE table_name = ? ORDER BY ordinal_position""",
            (table,),
        ).fetchall()
        return [
            {"name": row["column_name"], "type": row["data_type"] or ""}
            for row in rows
        ]

    rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    return [
        {"name": row["name"], "type": row["type"] or ""}
        for row in rows
    ]


def get_builder_schema():
    """Introspect live columns/types for ALLOWED_TABLES on either dialect."""
    allowed = sorted(config.ALLOWED_TABLES)
    conn = get_connection()
    try:
        tables = []
        for table in allowed:
            columns = _introspect_table_columns(conn, table)
            if columns:
                tables.append({"name": table, "columns": columns})
        return {"tables": tables, "operators": list(FILTER_OPERATORS)}
    finally:
        conn.close()


def build_select_sql(spec):
    """Validate spec against the live schema and build (sql, params, columns).

    Raises QueryBuilderError with an operator-friendly message for any
    reference outside the allow-listed schema. Values are never interpolated;
    they are returned as bound parameters.
    """
    if spec.table not in config.ALLOWED_TABLES:
        raise QueryBuilderError(
            f"Table '{spec.table}' is not in the allow-list: {sorted(config.ALLOWED_TABLES)}."
        )

    schema = get_builder_schema()
    table_meta = next((t for t in schema["tables"] if t["name"] == spec.table), None)
    if table_meta is None:
        raise QueryBuilderError(f"Table '{spec.table}' does not exist in the database.")
    known = {c["name"]: c for c in table_meta["columns"]}

    selected = []
    for col in spec.columns:
        col = col.strip()
        if not col:
            continue
        if col not in known:
            raise QueryBuilderError(
                f"Column '{col}' does not exist on table '{spec.table}'."
                f" Available: {sorted(known)}."
            )
        if col not in selected:
            selected.append(col)
    if not selected:
        selected = [c["name"] for c in table_meta["columns"]]
        if not selected:
            raise QueryBuilderError(f"Table '{spec.table}' has no columns.")

    where_parts = []
    params = []
    for f in spec.filters:
        value = (f.value or "").strip()
        if not value:
            continue
        col_meta = known.get(f.column)
        if col_meta is None:
            raise QueryBuilderError(
                f"Filter column '{f.column}' does not exist on table '{spec.table}'."
                f" Available: {sorted(known)}."
            )
        numeric = _is_numeric_column(col_meta["type"])
        if f.operator == "LIKE":
            if numeric:
                raise QueryBuilderError(
                    f"LIKE is not supported for numeric column '{f.column}'."
                )
            params.append(f"%{value}%")
        elif numeric:
            try:
                parsed = float(value)
            except ValueError:
                raise QueryBuilderError(
                    f"Column '{f.column}' is numeric ({col_meta['type']});"
                    f" value '{value}' is not a number."
                )
            params.append(int(parsed) if parsed.is_integer() else parsed)
        else:
            params.append(value)
        where_parts.append(f'"{f.column}" {f.operator} ?')

    order_by = None
    if spec.order_by:
        order_by = spec.order_by.strip()
        if order_by and order_by not in known:
            raise QueryBuilderError(
                f"order_by column '{order_by}' does not exist on table '{spec.table}'."
                f" Available: {sorted(known)}."
            )

    select_list = ", ".join(f'"{c}"' for c in selected)
    sql = f'SELECT {select_list} FROM "{spec.table}"'
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if order_by:
        sql += f' ORDER BY "{order_by}" {spec.order_dir}'
    sql += f" LIMIT {int(spec.limit)}"
    return sql, tuple(params), selected


def _audit_run(table_name, sql, verdict, row_count, elapsed_ms):
    """Persist one audit row per builder run to ``app_builder_runs``.

    Deliberately not ``app_tool_calls``: builder runs are human-initiated,
    session-less reads, and keeping them in their own table lets the
    dashboard show builder usage without inflating agent metrics.
    """
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO app_builder_runs
               (run_id, table_name, sql_text, verdict, row_count, elapsed_ms, executed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                table_name,
                sql,
                verdict,
                row_count,
                elapsed_ms,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def run_builder_query(spec):
    """Build, guardrail-check, execute, and PII-mask a builder SELECT."""
    started = time.perf_counter()
    sql, params, columns = build_select_sql(spec)

    result = SQLGuardrail().check(sql)
    if not result.allowed:
        _audit_run(spec.table, sql, result.verdict, 0, round((time.perf_counter() - started) * 1000, 2))
        return {
            "sql": sql,
            "guardrail": result.to_dict(),
            "columns": [],
            "rows": [],
            "row_count": 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        }

    conn = get_connection()
    try:
        cursor = conn.execute(sql, params)
        rows = cursor.fetchall()
        cells = [[row[c] for c in columns] for row in rows]
    finally:
        conn.close()

    masked_rows = [
        [PIIGuardrail.mask_pii(cell) if isinstance(cell, str) else cell for cell in row_cells]
        for row_cells in cells
    ]
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    _audit_run(spec.table, sql, result.verdict, len(masked_rows), elapsed_ms)
    return {
        "sql": sql,
        "guardrail": result.to_dict(),
        "columns": columns,
        "rows": masked_rows,
        "row_count": len(masked_rows),
        "elapsed_ms": elapsed_ms,
    }
