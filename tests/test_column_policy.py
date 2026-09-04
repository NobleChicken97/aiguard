"""Column-level policy + INSERT volume gate + shape anomaly (Phase 2).

The default COLUMN_POLICY is empty by design (demo schema has no truly
sensitive column), so every test here pins an explicit policy — the
mechanism is what's under test, proven against a deny on customers.email.
"""

import sys

import pytest

sys.path.insert(0, ".")

import config
from db.database import reset_db
from db.seed import seed_demo_data
from guardrails.sql_guardrail import (
    SHAPE_COLUMN_LIMIT,
    SHAPE_TABLE_LIMIT,
    SQLGuardrail,
    VERDICT_ALLOWED,
    VERDICT_REQUIRES_APPROVAL,
    query_shape,
)
from tools.sql_tool import SQLTool


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


POLICY = {"customers": {"deny": {"email"}}}
SCHEMA = {
    "customers": ["id", "name", "email", "city", "signup_date"],
    "orders": ["id", "customer_id", "order_date", "total", "status"],
    "products": ["id", "name", "category", "price", "stock"],
}


def _guard(policy=POLICY, schema=SCHEMA):
    return SQLGuardrail(column_policy=policy, schema_columns=schema)


def _verdict(sql, **kwargs):
    return _guard(**kwargs).check(sql).verdict


def test_clean_select_stays_allowed():
    assert _verdict("SELECT name, city FROM customers") == "ALLOWED"


def test_denied_column_blocked_and_named():
    res = _guard().check("SELECT email FROM customers")
    assert res.blocked
    assert "customers.email" in res.reason


def test_matching_is_case_insensitive_and_quoted():
    assert _verdict("SELECT EMAIL FROM customers") == "BLOCKED"
    assert _verdict('SELECT "email" FROM customers') == "BLOCKED"


def test_alias_qualifier_resolves_to_real_table():
    assert _verdict("SELECT c.email FROM customers c") == "BLOCKED"
    assert _verdict("SELECT c.name FROM customers c") == "ALLOWED"


def test_where_and_order_and_aggregate_columns_checked():
    assert _verdict("SELECT name FROM customers WHERE email LIKE '%@%'") == "BLOCKED"
    assert _verdict("SELECT name FROM customers ORDER BY email") == "BLOCKED"
    assert _verdict("SELECT COUNT(email) FROM customers") == "BLOCKED"
    assert _verdict("SELECT name FROM customers ORDER BY name") == "ALLOWED"


def test_star_expansion_names_the_denied_column():
    res = _guard().check("SELECT * FROM customers")
    assert res.blocked
    assert "email" in res.reason


def test_qualified_star_and_clean_table_star():
    assert _verdict("SELECT customers.* FROM customers") == "BLOCKED"
    assert _verdict("SELECT * FROM products") == "ALLOWED"


def test_unknown_schema_star_fails_closed():
    g = SQLGuardrail(column_policy=POLICY, schema_columns=None)
    assert g.check("SELECT * FROM customers").blocked
    assert g.check("SELECT * FROM products").verdict == "ALLOWED"


def test_subselect_union_and_join_checked():
    assert _verdict("SELECT * FROM (SELECT email FROM customers)") == "BLOCKED"
    assert (
        _verdict("SELECT name FROM customers UNION SELECT email FROM customers")
        == "BLOCKED"
    )
    assert (
        _verdict(
            "SELECT o.total, c.email FROM orders o JOIN customers c "
            "ON o.customer_id = c.id"
        )
        == "BLOCKED"
    )
    assert (
        _verdict(
            "SELECT o.total, c.name FROM orders o JOIN customers c "
            "ON o.customer_id = c.id"
        )
        == "ALLOWED"
    )


def test_insert_explicit_columns():
    assert _verdict("INSERT INTO customers (name) VALUES ('A')") == "ALLOWED"
    assert _verdict("INSERT INTO customers (name, email) VALUES ('A', 'a@x.y')") == "BLOCKED"


def test_insert_without_column_list_fails_closed():
    res = _guard().check(
        "INSERT INTO customers VALUES (1, 'A', 'a@x.y', 'NY', '2024-01-01')"
    )
    assert res.blocked
    assert "column list" in res.reason


def test_insert_volume_gate_mirrors_bulk_model(monkeypatch):
    monkeypatch.setattr(config, "RISKY_ROW_THRESHOLD", 5)
    one = "INSERT INTO customers (name) VALUES ('A')"
    assert _verdict(one) == "ALLOWED"
    six_rows = (
        "INSERT INTO products (name, price) VALUES "
        + ", ".join(f"('P{i}', {i}.0)" for i in range(6))
    )
    res = _guard().check(six_rows)
    assert res.verdict == VERDICT_REQUIRES_APPROVAL
    assert "6 rows" in res.reason


def test_insert_select_source_requires_approval():
    res = _guard().check("INSERT INTO customers (name) SELECT name FROM orders")
    assert res.verdict == VERDICT_REQUIRES_APPROVAL
    assert "could not be estimated" in res.reason


def test_update_and_delete_columns_checked():
    assert _verdict("UPDATE customers SET email = 'x' WHERE id = 1") == "BLOCKED"
    assert _verdict("UPDATE customers SET city = 'X' WHERE email LIKE '%@%'") == "BLOCKED"
    assert _verdict("UPDATE customers SET city = 'X' WHERE id = 1") == "ALLOWED"
    assert _verdict("DELETE FROM customers WHERE email LIKE '%@%'") == "BLOCKED"


def test_block_beats_approval_in_batches():
    res = _guard().check("SELECT name FROM customers; SELECT email FROM customers")
    assert res.blocked


def test_empty_policy_changes_nothing():
    g = SQLGuardrail(column_policy={}, schema_columns=SCHEMA)
    assert g.check("SELECT * FROM customers").verdict == "ALLOWED"
    assert g.check("INSERT INTO customers (name) VALUES ('A')").verdict == "ALLOWED"


def test_query_shape_unit():
    assert query_shape("SELECT id FROM customers") == {
        "tables": ["customers"],
        "columns": 1,
        "star": False,
        "statements": 1,
    }
    wide = query_shape(
        "SELECT * FROM orders JOIN customers ON 1 = 1 JOIN products ON 1 = 1"
    )
    assert len(wide["tables"]) == 3
    assert wide["star"] is True
    assert query_shape("nonsense (((")["statements"] == 0


class _StubTrace:
    def __init__(self):
        self.events = []

    def log(self, event_type, data):
        self.events.append((event_type, data))

    def log_guardrail_verdict(self, *args):
        pass


def test_shape_anomaly_logged_not_blocked():
    tool = SQLTool()
    trace = _StubTrace()
    res = tool.execute("SELECT * FROM customers", _call_id="t1", _trace=trace)
    assert res.status == "success"  # log-only: never blocks
    anomalies = [d for kind, d in trace.events if kind == "query_shape_anomaly"]
    assert len(anomalies) == 1
    assert anomalies[0]["star"] is True

    trace2 = _StubTrace()
    res2 = tool.execute(
        "SELECT id FROM customers WHERE id = 1", _call_id="t2", _trace=trace2
    )
    assert res2.status == "success"
    assert trace2.events == []


def test_sqltool_honors_config_column_policy(monkeypatch):
    monkeypatch.setattr(config, "COLUMN_POLICY", POLICY)
    tool = SQLTool()
    res = tool.execute("SELECT email FROM customers", _call_id="t3")
    assert res.status == "blocked"
    assert "denied by policy" in res.output
    ok = tool.execute("SELECT name FROM customers WHERE id = 1", _call_id="t4")
    assert ok.status == "success"


def test_shape_limits_are_documented_constants():
    assert SHAPE_TABLE_LIMIT == 2
    assert SHAPE_COLUMN_LIMIT == 8
    assert VERDICT_ALLOWED == "ALLOWED"
