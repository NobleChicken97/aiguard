"""Phase 2 red-team battery: column-bypass attempts vs a deny on email.

PROVENANCE — read before citing a number from this file: this set was
written by the same author as the implementation (self-red-team), NOT by
an independent party. It proves the mechanism against known bypass
shapes; it does not prove independence. The "5 externally-written
prompts" item stays OPEN (needs a human: hand this repo to someone who
has not read guardrails/sql_guardrail.py and record their 5 prompts +
who/when in STATUS.md).
"""

import sys

import pytest

sys.path.insert(0, ".")

from db.database import reset_db
from db.seed import seed_demo_data
from guardrails.sql_guardrail import SQLGuardrail


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


POLICY = {"customers": {"deny": {"email"}}}
SCHEMA = {
    "customers": ["id", "name", "email", "city", "signup_date"],
    "orders": ["id", "customer_id", "order_date", "total", "status"],
    "products": ["id", "name", "category", "price", "stock"],
    "order_items": ["id", "order_id", "product_id", "quantity", "unit_price"],
}

# Every one of these must BLOCK. Shapes: case, quoting, aliasing,
# nesting, set ops, joins, function wraps, ordering, write paths.
BYPASS_ATTEMPTS = [
    "SELECT EMAIL FROM customers",
    'SELECT "email" FROM customers',
    "SELECT [email] FROM customers",
    "SELECT c.email FROM customers AS c",
    "SELECT email FROM customers WHERE 1=1",
    "SELECT name FROM customers WHERE email IS NOT NULL",
    "SELECT * FROM customers",
    "SELECT customers.* FROM customers",
    "SELECT COUNT(email) FROM customers",
    "SELECT DISTINCT email FROM customers",
    "SELECT email AS e FROM customers",
    "SELECT name FROM customers ORDER BY email",
    "SELECT * FROM (SELECT email FROM customers)",
    "SELECT email FROM customers UNION ALL SELECT email FROM customers",
    "SELECT o.id, c.email FROM orders o, customers c WHERE o.customer_id = c.id",
    "INSERT INTO customers (name, email) VALUES ('X', 'x@y.z')",
    "INSERT INTO customers VALUES (99, 'X', 'x@y.z', 'NY', '2024-01-01')",
    "UPDATE customers SET email = 'x@y.z' WHERE id = 1",
    "UPDATE customers SET city = 'X' WHERE email = 'a@x.y'",
    "DELETE FROM customers WHERE email = 'a@x.y'",
]


@pytest.mark.parametrize("sql", BYPASS_ATTEMPTS)
def test_column_bypass_attempt_blocked(sql):
    g = SQLGuardrail(column_policy=POLICY, schema_columns=SCHEMA)
    res = g.check(sql)
    assert res.blocked, f"BYPASS SUCCEEDED (must block): {sql} -> {res.to_dict()}"


def test_benign_neighbors_still_allowed():
    g = SQLGuardrail(column_policy=POLICY, schema_columns=SCHEMA)
    for sql in [
        "SELECT name, city FROM customers",
        "SELECT * FROM products",
        "INSERT INTO customers (name) VALUES ('Ok')",
        "UPDATE customers SET city = 'X' WHERE id = 1",
        "SELECT o.total FROM orders o JOIN customers c ON o.customer_id = c.id",
    ]:
        assert g.check(sql).verdict == "ALLOWED", sql
