APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_sessions (
    session_id     TEXT PRIMARY KEY,
    user_id        TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'active',
    last_active_at TEXT
);

CREATE TABLE IF NOT EXISTS app_messages (
    message_id   TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT,
    timestamp    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES app_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS app_tool_calls (
    call_id             TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    tool_name           TEXT NOT NULL,
    input               TEXT,
    output              TEXT,
    status              TEXT NOT NULL DEFAULT 'pending',
    guardrail_verdict   TEXT,
    created_at          TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES app_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS app_approval_requests (
    approval_id   TEXT PRIMARY KEY,
    call_id       TEXT NOT NULL,
    session_id    TEXT NOT NULL,
    risk_reason   TEXT NOT NULL,
    decided_by    TEXT,
    decision      TEXT,
    decided_at    TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (call_id) REFERENCES app_tool_calls(call_id),
    FOREIGN KEY (session_id) REFERENCES app_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS app_memory_facts (
    fact_id              TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    fact_text            TEXT NOT NULL,
    source_session_id    TEXT,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_trace_events (
    trace_id     TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    event_type   TEXT NOT NULL,
    data         TEXT NOT NULL,
    timestamp    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_users (
    user_id      TEXT PRIMARY KEY,
    email        TEXT NOT NULL UNIQUE,
    pw_hash      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'user',
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_builder_runs (
    run_id       TEXT PRIMARY KEY,
    table_name   TEXT NOT NULL,
    sql_text     TEXT NOT NULL,
    verdict      TEXT NOT NULL,
    row_count    INTEGER NOT NULL DEFAULT 0,
    elapsed_ms   REAL NOT NULL DEFAULT 0,
    executed_at  TEXT NOT NULL,
    user_id      TEXT
);
"""

DEMO_SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    email        TEXT NOT NULL,
    city         TEXT NOT NULL,
    signup_date  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    category  TEXT NOT NULL,
    price     REAL NOT NULL,
    stock     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY,
    customer_id  INTEGER NOT NULL,
    order_date   TEXT NOT NULL,
    total        REAL NOT NULL,
    status       TEXT NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES customers(id)
);

CREATE TABLE IF NOT EXISTS order_items (
    id           INTEGER PRIMARY KEY,
    order_id     INTEGER NOT NULL,
    product_id   INTEGER NOT NULL,
    quantity     INTEGER NOT NULL,
    unit_price   REAL NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(id),
    FOREIGN KEY (product_id) REFERENCES products(id)
);
"""
