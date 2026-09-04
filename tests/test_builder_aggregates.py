"""Builder aggregates + GROUP BY (ticket 06).

Aggregate mode produces group columns + aliased aggregate expressions only,
runs through the same guardrail + PII pipeline, and rejects invalid combos
with operator-friendly errors.
"""

import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from db.database import reset_db
from db.seed import seed_demo_data
from auth import SESSION_COOKIE, create_user, sign_session
from tools.query_builder import (
    AggregateSpec,
    FilterCondition,
    QueryBuilderError,
    QueryBuilderRequest,
    run_builder_query,
)
from webapp import app


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


def test_group_by_aggregate_returns_one_row_per_group():
    spec = QueryBuilderRequest(
        table="orders",
        group_by=["status"],
        aggregates=[AggregateSpec(function="COUNT", column=None)],
    )
    result = run_builder_query(spec)
    assert result["guardrail"]["verdict"] == "ALLOWED"
    assert result["columns"] == ["status", "count_all"]
    counts = {row[0]: row[1] for row in result["rows"]}
    assert counts == {"delivered": 8, "cancelled": 1, "pending": 1}
    assert result["row_count"] == 3


def test_avg_aggregate_without_group_by_returns_single_row():
    spec = QueryBuilderRequest(
        table="products", aggregates=[AggregateSpec(function="AVG", column="price")]
    )
    result = run_builder_query(spec)
    assert result["columns"] == ["avg_price"]
    assert result["row_count"] == 1
    assert 0 < result["rows"][0][0] < 5000


def test_sum_min_max_over_numeric_column():
    spec = QueryBuilderRequest(
        table="products",
        group_by=["category"],
        aggregates=[
            AggregateSpec(function="SUM", column="price"),
            AggregateSpec(function="MIN", column="price"),
            AggregateSpec(function="MAX", column="stock"),
        ],
    )
    result = run_builder_query(spec)
    assert result["columns"] == ["category", "sum_price", "min_price", "max_stock"]
    electronics = next(r for r in result["rows"] if r[0] == "Electronics")
    assert electronics[1] == pytest.approx(1819.95)
    assert electronics[2] == 29.99
    assert electronics[3] == 200


def test_filters_apply_before_aggregation():
    spec = QueryBuilderRequest(
        table="customers",
        group_by=["city"],
        aggregates=[AggregateSpec(function="COUNT", column=None)],
        filters=[FilterCondition(column="city", operator="=", value="New York")],
    )
    result = run_builder_query(spec)
    assert result["row_count"] == 1
    assert result["rows"][0] == ["New York", 2]


def test_plain_columns_rejected_in_aggregate_mode():
    spec = QueryBuilderRequest(
        table="orders",
        columns=["status"],
        group_by=["status"],
        aggregates=[AggregateSpec(function="COUNT", column=None)],
    )
    with pytest.raises(QueryBuilderError, match="cannot be combined"):
        run_builder_query(spec)


def test_sum_on_non_numeric_column_rejected():
    spec = QueryBuilderRequest(
        table="customers", aggregates=[AggregateSpec(function="SUM", column="name")]
    )
    with pytest.raises(QueryBuilderError, match="numeric"):
        run_builder_query(spec)


def test_non_count_star_rejected():
    spec = QueryBuilderRequest(
        table="customers", aggregates=[AggregateSpec(function="AVG", column=None)]
    )
    with pytest.raises(QueryBuilderError, match="COUNT"):
        run_builder_query(spec)


def test_order_by_must_be_group_or_alias_in_aggregate_mode():
    spec = QueryBuilderRequest(
        table="orders",
        group_by=["status"],
        aggregates=[AggregateSpec(function="COUNT", column=None)],
        order_by="total",
    )
    with pytest.raises(QueryBuilderError, match="order-by"):
        run_builder_query(spec)


def test_aggregate_endpoint_run_and_invariant():
    uid = create_user("agg@test.local", "testpass123")
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, sign_session(uid))
        resp = client.post(
            "/api/query-builder/run",
            json={
                "table": "orders",
                "group_by": ["status"],
                "aggregates": [{"function": "COUNT", "column": None}],
                "order_by": "count_all",
                "order_dir": "DESC",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["columns"] == ["status", "count_all"]
        assert body["rows"][0][1] == 8  # delivered first when sorted desc
        # every generated sql still passes the guardrail
        from guardrails.sql_guardrail import SQLGuardrail

        assert SQLGuardrail().check(body["sql"]).allowed
