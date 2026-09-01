import sqlite3
import os
import config
from contextlib import contextmanager

_pg_pool = None

def _is_postgres():
    return config.DATABASE_URL.startswith("postgres")

def _init_pg_pool():
    global _pg_pool
    if _pg_pool is None and _is_postgres():
        from psycopg2.pool import SimpleConnectionPool
        _pg_pool = SimpleConnectionPool(1, 20, config.DATABASE_URL)

def get_db_path():
    path = config.DB_PATH
    if not os.path.isabs(path):
        path = os.path.join(config.PROJECT_ROOT, path)
    return path

class PGConnectionWrapper:
    """Wraps psycopg2 connection to mimic sqlite3 row factory and execute behavior."""
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, parameters=()):
        # Replace ? with %s for psycopg2
        sql = sql.replace("?", "%s")
        import psycopg2.extras
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.execute(sql, parameters)
        return cursor

    def executemany(self, sql, seq_of_parameters):
        # Replace ? with %s for psycopg2
        sql = sql.replace("?", "%s")
        import psycopg2.extras
        cursor = self.conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cursor.executemany(sql, seq_of_parameters)
        return cursor

    def executescript(self, sql):
        cursor = self.conn.cursor()
        cursor.execute(sql)
        return cursor

    def commit(self):
        self.conn.commit()

    def close(self):
        global _pg_pool
        if _pg_pool:
            _pg_pool.putconn(self.conn)
        else:
            self.conn.close()

def get_connection():
    if _is_postgres():
        _init_pg_pool()
        conn = _pg_pool.getconn()
        return PGConnectionWrapper(conn)
    else:
        path = get_db_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

def initialize_db():
    from db.schema import APP_SCHEMA, DEMO_SCHEMA
    
    if _is_postgres():
        # Quick and dirty: replace INTEGER PRIMARY KEY with SERIAL PRIMARY KEY
        app_schema_pg = APP_SCHEMA.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
        demo_schema_pg = DEMO_SCHEMA.replace("INTEGER PRIMARY KEY", "SERIAL PRIMARY KEY")
        # And replace AUTOINCREMENT with nothing, Postgres uses SERIAL
        app_schema_pg = app_schema_pg.replace("AUTOINCREMENT", "")
        demo_schema_pg = demo_schema_pg.replace("AUTOINCREMENT", "")
        
        conn = get_connection()
        try:
            conn.executescript(app_schema_pg)
            conn.executescript(demo_schema_pg)
            conn.commit()
        finally:
            conn.close()
    else:
        conn = get_connection()
        try:
            conn.executescript(APP_SCHEMA)
            conn.executescript(DEMO_SCHEMA)
            conn.commit()
        finally:
            conn.close()

def reset_db():
    if not _is_postgres():
        path = get_db_path()
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    initialize_db()


def record_tool_call(session_id, call_id, tool_name, tool_input, status="executed", created_at=None):
    """Persist a tool invocation to ``app_tool_calls`` (idempotent per call_id).

    Shared by the orchestrator loop and supervisor/worker path so the audit
    trail stays complete and ``get_pending_approvals()``'s JOIN on
    ``app_tool_calls`` keeps working regardless of which path raised the
    approval. No-ops when there is no persisted session (``session_id`` is
    required NOT NULL by the schema).
    """
    if not session_id or not call_id:
        return

    import json as _json

    from datetime import datetime, timezone

    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()

    conn = get_connection()
    try:
        # "INSERT OR IGNORE" is SQLite-only; PostgreSQL uses ON CONFLICT.
        if _is_postgres():
            insert_sql = """INSERT INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT (call_id) DO NOTHING"""
        else:
            insert_sql = """INSERT OR IGNORE INTO app_tool_calls (call_id, session_id, tool_name, input, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)"""
        conn.execute(
            insert_sql,
            (
                call_id,
                session_id,
                tool_name,
                _json.dumps(tool_input, default=str),
                status,
                created_at,
            ),
        )
        conn.commit()
    finally:
        conn.close()
