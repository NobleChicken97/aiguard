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


class AggregateSpec(BaseModel):
    """One aggregate expression; ``column=None`` means COUNT(*)."""

    function: Literal["COUNT", "SUM", "AVG", "MIN", "MAX"]
    column: str | None = None


class QueryBuilderRequest(BaseModel):
    table: str
    columns: list[str] = []
    filters: list[FilterCondition] = []
    order_by: str | None = None
    order_dir: Literal["ASC", "DESC"] = "ASC"
    limit: int = Field(default=50, ge=1, le=200)
    aggregates: list[AggregateSpec] = []
    group_by: list[str] = []
    # Join support: `join_column` must be a *declared* foreign key of `table`;
    # the referenced table is derived from that declaration, never guessed.
    join_column: str | None = None
    join_columns: list[str] = []


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


def _introspect_fks(conn, table):
    """Declared foreign keys of a table as [{column, target_table, target_column}].

    Only allow-listed targets are returned, and joins are built from these
    declarations exclusively — the builder never infers a relationship.
    """
    if _is_postgres():
        try:
            rows = conn.execute(
                """SELECT kcu.column_name AS from_col, ccu.table_name AS target_table,
                          ccu.column_name AS target_col
                   FROM information_schema.table_constraints tc
                   JOIN information_schema.key_column_usage kcu
                     ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_name = kcu.table_name
                   JOIN information_schema.constraint_column_usage ccu
                     ON tc.constraint_name = ccu.constraint_name
                   WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = ?""",
                (table,),
            ).fetchall()
        except Exception:
            return []
        return [
            {
                "column": r["from_col"],
                "target_table": r["target_table"],
                "target_column": r["target_col"],
            }
            for r in rows
            if r["target_table"] in config.ALLOWED_TABLES
        ]

    rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    fks = []
    for r in rows:
        target = r["table"]
        if target not in config.ALLOWED_TABLES:
            continue
        fks.append(
            {
                "column": r["from"],
                "target_table": target,
                "target_column": r["to"] or "id",
            }
        )
    return fks


def get_builder_schema():
    """Introspect live columns/types/foreign keys for ALLOWED_TABLES."""
    allowed = sorted(config.ALLOWED_TABLES)
    conn = get_connection()
    try:
        tables = []
        for table in allowed:
            columns = _introspect_table_columns(conn, table)
            if columns:
                tables.append(
                    {"name": table, "columns": columns, "fks": _introspect_fks(conn, table)}
                )
        return {"tables": tables, "operators": list(FILTER_OPERATORS)}
    finally:
        conn.close()


def _build_filters(spec, known, table):
    """Validate filter conditions against known columns; return (parts, params).

    Shared by the plain and aggregate paths so both apply identical
    numeric-coercion and LIKE rules.
    """
    where_parts = []
    params = []
    for f in spec.filters:
        value = (f.value or "").strip()
        if not value:
            continue
        col_meta = known.get(f.column)
        if col_meta is None:
            raise QueryBuilderError(
                f"Filter column '{f.column}' does not exist on table '{table}'."
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
    return where_parts, params


def _build_aggregate_sql(spec, known):
    """Aggregate/group-by path: group columns + aggregate aliases only."""
    if spec.columns:
        raise QueryBuilderError(
            "Plain columns cannot be combined with aggregates or group-by;"
            " add those columns to 'group by' instead."
        )

    group_cols = []
    for col in spec.group_by:
        col = (col or "").strip()
        if not col:
            continue
        if col not in known:
            raise QueryBuilderError(
                f"Group-by column '{col}' does not exist on table '{spec.table}'."
                f" Available: {sorted(known)}."
            )
        if col not in group_cols:
            group_cols.append(col)

    select_parts = [f'"{c}"' for c in group_cols]
    output_names = list(group_cols)
    for agg in spec.aggregates:
        func = agg.function
        if agg.column is None:
            if func != "COUNT":
                raise QueryBuilderError(
                    f"{func}(*) is not supported; '(all rows)' aggregates require COUNT."
                )
            alias = "count_all"
            expr = "COUNT(*)"
        else:
            col = agg.column.strip()
            if col not in known:
                raise QueryBuilderError(
                    f"Aggregate column '{col}' does not exist on table '{spec.table}'."
                    f" Available: {sorted(known)}."
                )
            if func in ("SUM", "AVG") and not _is_numeric_column(known[col]["type"]):
                raise QueryBuilderError(
                    f"{func} requires a numeric column;"
                    f" '{col}' is {known[col]['type'] or 'non-numeric'}."
                )
            alias = f"{func.lower()}_{col}"
            expr = f'{func}("{col}")'
        select_parts.append(f'{expr} AS "{alias}"')
        output_names.append(alias)

    where_parts, params = _build_filters(spec, known, spec.table)

    order_by = None
    if spec.order_by:
        order_by = spec.order_by.strip()
        if order_by and order_by not in group_cols and order_by not in output_names:
            raise QueryBuilderError(
                "When aggregating, order-by must be a group-by column or an"
                f" aggregate alias (one of: {output_names})."
            )

    sql = f'SELECT {", ".join(select_parts)} FROM "{spec.table}"'
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if group_cols:
        sql += " GROUP BY " + ", ".join(f'"{c}"' for c in group_cols)
    if order_by:
        sql += f' ORDER BY "{order_by}" {spec.order_dir}'
    sql += f" LIMIT {int(spec.limit)}"
    return sql, tuple(params), output_names


def _build_join_sql(spec, table_meta, known):
    """FK-join path: base + declared-referenced table, every output aliased.

    Join direction comes exclusively from the declared foreign key the user
    picked; filters apply to the base table; order-by must be one of the
    aliased output columns. Output aliases (``orders_total`` etc.) keep
    duplicate names like ``id`` unambiguous in the result cells.
    """
    base = spec.table
    fk = next(
        (f for f in table_meta.get("fks", []) if f["column"] == (spec.join_column or "").strip()),
        None,
    )
    if fk is None:
        declared = ", ".join(
            f"{f['column']} -> {f['target_table']}" for f in table_meta.get("fks", [])
        )
        detail = f" Declared keys: {declared}." if declared else " No foreign keys are declared for this table."
        raise QueryBuilderError(
            f"Column '{spec.join_column}' is not a declared foreign key of '{base}'." + detail
        )
    target = fk["target_table"]
    if target not in config.ALLOWED_TABLES:
        raise QueryBuilderError(f"Joined table '{target}' is not in the allow-list.")

    schema = get_builder_schema()
    target_meta = next((t for t in schema["tables"] if t["name"] == target), None)
    if target_meta is None:
        raise QueryBuilderError(f"Joined table '{target}' does not exist in the database.")
    target_known = {c["name"]: c for c in target_meta["columns"]}

    base_cols = []
    for col in spec.columns:
        col = col.strip()
        if not col:
            continue
        if col not in known:
            raise QueryBuilderError(
                f"Column '{col}' does not exist on table '{base}'. Available: {sorted(known)}."
            )
        if col not in base_cols:
            base_cols.append(col)
    if not base_cols:
        base_cols = [c["name"] for c in table_meta["columns"]]
        if not base_cols:
            raise QueryBuilderError(f"Table '{base}' has no columns.")

    join_cols = []
    for col in spec.join_columns:
        col = col.strip()
        if not col:
            continue
        if col not in target_known:
            raise QueryBuilderError(
                f"Column '{col}' does not exist on joined table '{target}'."
                f" Available: {sorted(target_known)}."
            )
        if col not in join_cols:
            join_cols.append(col)
    if not join_cols:
        join_cols = [c["name"] for c in target_meta["columns"]]
        if not join_cols:
            raise QueryBuilderError(f"Joined table '{target}' has no columns.")

    select_parts = [f'"{base}"."{c}" AS "{base}_{c}"' for c in base_cols]
    select_parts += [f'"{target}"."{c}" AS "{target}_{c}"' for c in join_cols]
    output_names = [f"{base}_{c}" for c in base_cols] + [f"{target}_{c}" for c in join_cols]

    where_parts, params = _build_filters(spec, known, base)

    order_by = None
    if spec.order_by:
        order_by = spec.order_by.strip()
        if order_by and order_by not in output_names:
            raise QueryBuilderError(
                "When joining, order-by must be one of the selected output columns"
                f" such as {output_names[:4]}."
            )

    fk_col = fk["column"]
    fk_target_col = fk["target_column"]
    sql = f'SELECT {", ".join(select_parts)} FROM "{base}"'
    sql += f' JOIN "{target}" ON "{base}"."{fk_col}" = "{target}"."{fk_target_col}"'
    if where_parts:
        sql += " WHERE " + " AND ".join(where_parts)
    if order_by:
        sql += f' ORDER BY "{order_by}" {spec.order_dir}'
    sql += f" LIMIT {int(spec.limit)}"
    return sql, tuple(params), output_names


def build_select_sql(spec):
    """Validate spec against the live schema and build (sql, params, columns).

    Raises QueryBuilderError with an operator-friendly message for any
    reference outside the allow-listed schema. Values are never interpolated;
    they are returned as bound parameters. Returns the *output* column names
    (aliases included for aggregates) so result cells can be indexed by name.
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

    if spec.join_column:
        if spec.aggregates or spec.group_by:
            raise QueryBuilderError(
                "Joins cannot be combined with aggregates or group-by yet."
            )
        return _build_join_sql(spec, table_meta, known)

    if spec.aggregates or spec.group_by:
        return _build_aggregate_sql(spec, known)

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

    where_parts, params = _build_filters(spec, known, spec.table)

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


def _audit_run(table_name, sql, verdict, row_count, elapsed_ms, user_id=None):
    """Persist one audit row per builder run to ``app_builder_runs``.

    Deliberately not ``app_tool_calls``: builder runs are human-initiated,
    session-less reads, and keeping them in their own table lets the
    dashboard show builder usage without inflating agent metrics.
    """
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO app_builder_runs
               (run_id, table_name, sql_text, verdict, row_count, elapsed_ms, executed_at, user_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid.uuid4()),
                table_name,
                sql,
                verdict,
                row_count,
                elapsed_ms,
                datetime.now(timezone.utc).isoformat(),
                user_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def run_builder_query(spec, user_id=None):
    """Build, guardrail-check, execute, and PII-mask a builder SELECT."""
    started = time.perf_counter()
    sql, params, columns = build_select_sql(spec)

    result = SQLGuardrail().check(sql)
    if not result.allowed:
        _audit_run(spec.table, sql, result.verdict, 0, round((time.perf_counter() - started) * 1000, 2), user_id=user_id)
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
    _audit_run(spec.table, sql, result.verdict, len(masked_rows), elapsed_ms, user_id=user_id)
    return {
        "sql": sql,
        "guardrail": result.to_dict(),
        "columns": columns,
        "rows": masked_rows,
        "row_count": len(masked_rows),
        "elapsed_ms": elapsed_ms,
    }
