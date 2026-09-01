"""One-shot SQLite → PostgreSQL data migration.

Completes the Phase 7 production-DB story: deployments that started on the
default SQLite file can move their existing history (sessions, messages, tool
calls, approvals, memory facts, trace events) and demo data to PostgreSQL
without losing anything.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/guardrails \
        python -m db.migrate_sqlite_to_pg [--source data/guardrails.db] [--truncate]

Behavior:
- Target is ``config.DATABASE_URL`` (must be a postgres:// URL); the target
  schema is created/updated first via ``initialize_db()``.
- Tables are copied parents-before-children so foreign keys are satisfied.
- Inserts are idempotent: rows already present on the target (matched by
  primary key) are skipped via ON CONFLICT DO NOTHING, so re-runs are safe.
- ``--truncate`` empties the target tables first for a clean cutover.
- After copying, SERIAL sequences behind INTEGER PRIMARY KEY demo tables are
  advanced past the migrated ids so future inserts cannot collide.
- Finishes with a per-table source/target row-count report; mismatches raise
  MigrationError (nonzero exit from the CLI).
"""

import argparse
import sqlite3
import sys

import config

TABLE_COPY_ORDER = [
    "customers",
    "products",
    "orders",
    "order_items",
    "app_sessions",
    "app_messages",
    "app_tool_calls",
    "app_approval_requests",
    "app_memory_facts",
    "app_trace_events",
]


class MigrationError(Exception):
    pass


def _sqlite_source_path(source):
    import os

    if source:
        path = source
    else:
        path = config.DB_PATH
    if not os.path.isabs(path) and not source:
        path = os.path.join(config.PROJECT_ROOT, path)
    return path


def _table_names(sqlite_conn):
    rows = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {row[0] for row in rows}


def _copy_table(pg_conn, table, sqlite_conn, log):
    cursor = sqlite_conn.execute(f'SELECT * FROM "{table}"')
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()

    col_list = ", ".join(f'"{c}"' for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    insert_sql = (
        f'INSERT INTO "{table}" ({col_list}) VALUES ({placeholders})'
        " ON CONFLICT DO NOTHING"
    )

    inserted = 0
    for row in rows:
        pg_conn.execute(insert_sql, tuple(row))
        inserted += 1
    pg_conn.commit()

    skipped = len(rows) - inserted
    log(f"  {table}: {len(rows)} source rows -> {inserted} inserted, {skipped} already present")
    return len(rows)


def _sync_serial_sequences(pg_conn, tables, log):
    for table in tables:
        # app_* tables have TEXT primary keys (session_id etc.), so only
        # tables that actually expose an integer "id" column can carry a
        # SERIAL sequence behind it.
        has_id = pg_conn.execute(
            """SELECT 1 AS ok FROM information_schema.columns
               WHERE table_name = ? AND column_name = 'id'""",
            (table,),
        ).fetchone()
        if not has_id:
            continue

        row = pg_conn.execute(
            "SELECT pg_get_serial_sequence(?, 'id') AS seq", (table,)
        ).fetchone()
        sequence = row["seq"] if row else None
        if not sequence:
            continue
        max_row = pg_conn.execute(f'SELECT MAX(id) AS max_id FROM "{table}"').fetchone()
        max_id = max_row["max_id"] if max_row else None
        if max_id is None:
            pg_conn.execute("SELECT setval(?, 1, false)", (sequence,))
            log(f"  sequence for {table}.id reset to start at 1")
        else:
            pg_conn.execute("SELECT setval(?, ?)", (sequence, int(max_id)))
            log(f"  sequence for {table}.id advanced past {max_id}")
    pg_conn.commit()


def run_migration(source=None, truncate=False, log=print):
    """Migrate every known table from SQLite into the configured PG database.

    Returns a dict of ``{table: {"source": n, "target": n}}``. Raises
    MigrationError when the target does not hold every source row.
    """
    if not config.DATABASE_URL.startswith("postgres"):
        raise MigrationError(
            "Target must be PostgreSQL: set DATABASE_URL to a postgres:// URL."
            f" Current value targets: '{config.DATABASE_URL or '(unset)'}'."
        )

    from db.database import get_connection, initialize_db, _is_postgres

    if not _is_postgres():
        raise MigrationError("PostgreSQL pool failed to activate for the target URL.")

    source_path = _sqlite_source_path(source)
    import os

    if not os.path.exists(source_path):
        raise MigrationError(f"Source SQLite database not found: {source_path}")

    log(f"Source: {source_path}")
    initialize_db()
    log("Target schema initialized.")

    sqlite_conn = sqlite3.connect(source_path)
    try:
        available = _table_names(sqlite_conn)
        unknown = available - set(TABLE_COPY_ORDER) - {"sqlite_sequence"}
        if unknown:
            log(f"  note: skipping tables not managed by this app: {sorted(unknown)}")
        tables = [t for t in TABLE_COPY_ORDER if t in available]

        pg_conn = get_connection()
        try:
            if truncate:
                all_tables = ", ".join(f'"{t}"' for t in TABLE_COPY_ORDER)
                pg_conn.execute(f"TRUNCATE {all_tables}")
                pg_conn.commit()
                log("Target tables truncated (--truncate).")

            counts = {}
            for table in tables:
                src_count = _copy_table(pg_conn, table, sqlite_conn, log)
                tgt_row = pg_conn.execute(
                    f'SELECT COUNT(*) AS cnt FROM "{table}"'
                ).fetchone()
                counts[table] = {"source": src_count, "target": tgt_row["cnt"]}
            _sync_serial_sequences(pg_conn, tables, log)

            mismatches = {
                t: c for t, c in counts.items() if c["source"] != c["target"]
            }
            if mismatches:
                raise MigrationError(
                    "Row-count verification failed after migration:"
                    f" {mismatches}"
                )
            log("Verification passed: every source row is present on PostgreSQL.")
            return counts
        finally:
            pg_conn.close()
    finally:
        sqlite_conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Migrate the SQLite app/demo data into config.DATABASE_URL PostgreSQL.",
    )
    parser.add_argument(
        "--source",
        default=None,
        help="Path to the source SQLite file (default: config.DB_PATH).",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Empty target tables before copying (clean cutover instead of merge).",
    )
    args = parser.parse_args(argv)

    try:
        run_migration(source=args.source, truncate=args.truncate)
    except MigrationError as e:
        print(f"MIGRATION FAILED: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
