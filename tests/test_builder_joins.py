"""Builder FK-based joins (ticket 07).

Joins come only from *declared* foreign keys, every output column is
aliased per table (orders_total, customers_name, ...), and the result
goes through the same guardrail + PII pipeline.
"""

import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from db.database import reset_db
from db.seed import seed_demo_data
from auth import SESSION_COOKIE, create_user, sign_session
from tools.query_builder import QueryBuilderError, QueryBuilderRequest, run_builder_query
from webapp import app


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def test_schema_endpoint_exposes_declared_foreign_keys():
    uid = create_user("joins@test.local", "testpass123")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(uid))
        data = client.get("/api/query-builder/schema").json()
    fks_by_table = {t["name"]: t.get("fks", []) for t in data["tables"]}
    assert {"column": "customer_id", "target_table": "customers", "target_column": "id"} in fks_by_table["orders"]
    assert {"column": "order_id", "target_table": "orders", "target_column": "id"} in fks_by_table["order_items"]
    assert fks_by_table["customers"] == []


def test_join_orders_to_customers_with_aliasing():
    spec = QueryBuilderRequest(
        table="orders",
        columns=["total", "status"],
        join_column="customer_id",
        join_columns=["name", "city"],
        filters=[],
    )
    result = run_builder_query(spec)
    assert result["guardrail"]["verdict"] == "ALLOWED"
    assert result["columns"] == ["orders_total", "orders_status", "customers_name", "customers_city"]
    assert result["row_count"] == 10  # every seeded order joins a customer
    # order row for customer 1 (Alice Johnson): total 1329.98
    first = result["rows"][0]
    assert first[0] == 1329.98
    assert first[2] == "Alice Johnson"


def test_join_masks_pii_from_joined_side():
    spec = QueryBuilderRequest(
        table="orders",
        columns=["total"],
        join_column="customer_id",
        join_columns=["email"],
    )
    result = run_builder_query(spec)
    emails = {row[1] for row in result["rows"]}
    assert "alice@example.com" not in emails
    assert all(e.startswith("***@") for e in emails)


def test_undeclared_join_column_rejected():
    spec = QueryBuilderRequest(
        table="customers", join_column="city"  # not a foreign key
    )
    with pytest.raises(QueryBuilderError, match="not a declared foreign key"):
        run_builder_query(spec)


def test_join_column_on_wrong_side_rejected():
    spec = QueryBuilderRequest(
        table="orders", join_column="id"  # id is not a declared FK of orders
    )
    with pytest.raises(QueryBuilderError, match="not a declared foreign key"):
        run_builder_query(spec)


def test_unknown_joined_column_rejected():
    spec = QueryBuilderRequest(
        table="orders", join_column="customer_id", join_columns=["nickname"]
    )
    with pytest.raises(QueryBuilderError, match="joined table"):
        run_builder_query(spec)


def test_join_with_aggregates_rejected():
    spec = QueryBuilderRequest(
        table="orders",
        join_column="customer_id",
        group_by=["status"],
        aggregates=[{"function": "COUNT", "column": None}],
    )
    with pytest.raises(QueryBuilderError, match="cannot be combined"):
        run_builder_query(spec)


def test_join_order_by_must_be_output_alias():
    spec = QueryBuilderRequest(
        table="orders",
        columns=["total"],
        join_column="customer_id",
        join_columns=["name"],
        order_by="total",  # bare column name no longer exists in output
    )
    with pytest.raises(QueryBuilderError, match="order-by"):
        run_builder_query(spec)

    ok = QueryBuilderRequest(
        table="orders",
        columns=["total"],
        join_column="customer_id",
        join_columns=["name"],
        order_by="orders_total",
        order_dir="DESC",
    )
    result = run_builder_query(ok)
    assert result["rows"][0][0] == 1329.98


def test_join_endpoint_run_passes_guardrail_invariant():
    uid = create_user("joins2@test.local", "testpass123")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(uid))
        resp = client.post(
            "/api/query-builder/run",
            json={
                "table": "order_items",
                "columns": ["quantity"],
                "join_column": "product_id",
                "join_columns": ["name", "price"],
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["guardrail"]["verdict"] == "ALLOWED"
        assert body["columns"] == ["order_items_quantity", "products_name", "products_price"]
        from guardrails.sql_guardrail import SQLGuardrail

        assert SQLGuardrail().check(body["sql"]).allowed
