import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from db.database import reset_db
from db.seed import seed_demo_data
from guardrails.sql_guardrail import SQLGuardrail
from webapp import app


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def _run(client, **overrides):
    payload = {
        "table": "customers",
        "columns": ["name", "email"],
        "filters": [],
        "order_by": None,
        "order_dir": "ASC",
        "limit": 50,
    }
    payload.update(overrides)
    return client.post("/api/query-builder/run", json=payload)


def test_query_builder_page_renders_with_nav_link(client):
    page = client.get("/query-builder")
    assert page.status_code == 200
    assert "Query Builder" in page.text
    assert "/api/query-builder/schema" in page.text
    assert "Run query" in page.text
    assert "Query Builder" in client.get("/chat").text


def test_schema_endpoint_lists_allowed_tables_and_columns(client):
    response = client.get("/api/query-builder/schema")
    assert response.status_code == 200
    data = response.json()

    tables = {t["name"]: t for t in data["tables"]}
    assert set(tables) == {"customers", "products", "orders", "order_items"}

    customer_cols = [(c["name"], c["type"]) for c in tables["customers"]["columns"]]
    assert ("id", "INTEGER") in customer_cols
    assert ("email", "TEXT") in customer_cols

    product_cols = [c["name"] for c in tables["products"]["columns"]]
    assert product_cols == ["id", "name", "category", "price", "stock"]

    assert "=" in data["operators"]
    assert "LIKE" in data["operators"]


def test_run_endpoint_executes_filtered_select_with_pii_masking(client):
    response = _run(
        client,
        filters=[{"column": "city", "operator": "=", "value": "Chicago"}],
    )
    assert response.status_code == 200
    data = response.json()

    assert data["guardrail"]["verdict"] == "ALLOWED"
    assert data["sql"].startswith("SELECT")
    assert data["columns"] == ["name", "email"]
    assert data["row_count"] == 2
    names = [row[0] for row in data["rows"]]
    assert names == ["Carol White", "Henry Wilson"]
    for row in data["rows"]:
        assert row[1] == "***@example.com"


def test_run_endpoint_supports_like_contains_order_and_limit(client):
    like_response = _run(
        client,
        filters=[{"column": "name", "operator": "LIKE", "value": "son"}],
    )
    assert like_response.status_code == 200
    like_data = like_response.json()
    assert like_data["row_count"] == 2
    assert {row[0] for row in like_data["rows"]} == {"Alice Johnson", "Henry Wilson"}

    ordered_response = _run(
        client,
        table="products",
        columns=["name", "price"],
        order_by="price",
        order_dir="DESC",
        limit=3,
    )
    assert ordered_response.status_code == 200
    ordered = ordered_response.json()
    assert ordered["sql"].endswith("LIMIT 3")
    assert ordered["row_count"] == 3
    assert ordered["rows"][0][0] == "Laptop Pro 15"
    prices = [row[1] for row in ordered["rows"]]
    assert prices == sorted(prices, reverse=True)


def test_run_endpoint_coerces_numeric_filters_and_rejects_bad_values(client):
    numeric_response = _run(
        client,
        table="products",
        columns=["name", "stock"],
        filters=[{"column": "stock", "operator": ">", "value": "100"}],
    )
    assert numeric_response.status_code == 200
    numeric_data = numeric_response.json()
    assert numeric_data["row_count"] == 4
    assert all(row[1] > 100 for row in numeric_data["rows"])

    bad_value = _run(
        client,
        table="products",
        columns=["name"],
        filters=[{"column": "stock", "operator": ">", "value": "lots"}],
    )
    assert bad_value.status_code == 400
    assert "not a number" in bad_value.text

    like_on_numeric = _run(
        client,
        table="products",
        columns=["name"],
        filters=[{"column": "price", "operator": "LIKE", "value": "9"}],
    )
    assert like_on_numeric.status_code == 400
    assert "LIKE is not supported" in like_on_numeric.text


def test_run_endpoint_rejects_disallowed_table_even_system_tables(client):
    response = _run(client, table="sqlite_master")
    assert response.status_code == 400
    assert "allow-list" in response.text

    sneaky = _run(client, table="customers; DROP TABLE customers")
    assert sneaky.status_code == 400
    assert "allow-list" in sneaky.text


def test_run_endpoint_rejects_unknown_column_and_order_by(client):
    bad_column = _run(client, columns=["name", "nope"])
    assert bad_column.status_code == 400
    assert "does not exist" in bad_column.text

    bad_order = _run(client, order_by="secret_field")
    assert bad_order.status_code == 400
    assert "order_by" in bad_order.text


def test_run_endpoint_validates_operator_and_limit_via_pydantic(client):
    bad_operator = _run(client, filters=[{"column": "name", "operator": "~", "value": "x"}])
    assert bad_operator.status_code == 422

    too_high = _run(client, limit=500)
    assert too_high.status_code == 422

    too_low = _run(client, limit=0)
    assert too_low.status_code == 422

    bad_direction = _run(client, order_by="name", order_dir="SIDEWAYS")
    assert bad_direction.status_code == 422


def test_every_generated_sql_passes_the_guardrail_invariant(client):
    specs = [
        {"filters": [{"column": "city", "operator": "=", "value": "Chicago"}]},
        {
            "table": "orders",
            "columns": ["id", "total", "status"],
            "filters": [
                {"column": "status", "operator": "=", "value": "delivered"},
                {"column": "total", "operator": ">=", "value": "100"},
            ],
            "order_by": "total",
            "order_dir": "ASC",
            "limit": 10,
        },
        {"table": "products", "columns": []},
    ]
    guardrail = SQLGuardrail()
    for spec in specs:
        response = _run(client, **spec)
        assert response.status_code == 200
        data = response.json()
        verdict = guardrail.check(data["sql"])
        assert verdict.allowed, data["sql"]
        assert data["guardrail"]["verdict"] == "ALLOWED"
