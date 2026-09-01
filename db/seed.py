import sqlite3
from db.database import get_connection, initialize_db


CUSTOMERS = [
    ("Alice Johnson", "alice@example.com", "New York", "2024-01-15"),
    ("Bob Smith", "bob@example.com", "Los Angeles", "2024-02-20"),
    ("Carol White", "carol@example.com", "Chicago", "2024-03-10"),
    ("Dave Brown", "dave@example.com", "Houston", "2024-04-05"),
    ("Eve Davis", "eve@example.com", "New York", "2024-05-22"),
    ("Frank Miller", "frank@example.com", "Seattle", "2024-06-18"),
    ("Grace Lee", "grace@example.com", "Boston", "2024-07-30"),
    ("Henry Wilson", "henry@example.com", "Chicago", "2024-08-14"),
    ("Ivy Chen", "ivy@example.com", "San Francisco", "2024-09-01"),
    ("Jack Taylor", "jack@example.com", "Miami", "2024-10-12"),
]

PRODUCTS = [
    ("Laptop Pro 15", "Electronics", 1299.99, 25),
    ("Wireless Mouse", "Electronics", 29.99, 150),
    ("Mechanical Keyboard", "Electronics", 89.99, 80),
    ("USB-C Hub", "Electronics", 49.99, 200),
    ("Office Chair", "Furniture", 249.99, 30),
    ("Standing Desk", "Furniture", 499.99, 15),
    ("Notebook A5", "Stationery", 9.99, 500),
    ("Pen Set (10pc)", "Stationery", 14.99, 300),
    ("Monitor 27\"", "Electronics", 349.99, 40),
    ("Desk Lamp", "Furniture", 39.99, 100),
]

ORDERS = [
    (1, "2024-03-01", 1329.98, "delivered"),
    (2, "2024-03-15", 29.99, "delivered"),
    (3, "2024-04-02", 489.98, "delivered"),
    (1, "2024-05-10", 179.98, "delivered"),
    (4, "2024-06-20", 499.99, "delivered"),
    (5, "2024-07-05", 349.99, "delivered"),
    (6, "2024-08-12", 139.98, "cancelled"),
    (7, "2024-09-18", 24.98, "delivered"),
    (2, "2024-10-01", 849.99, "pending"),
    (8, "2024-10-25", 39.99, "delivered"),
]

ORDER_ITEMS = [
    (1, 1, 1, 1299.99),
    (1, 2, 1, 29.99),
    (2, 2, 1, 29.99),
    (3, 5, 1, 249.99),
    (3, 7, 24, 9.99),
    (4, 3, 1, 89.99),
    (4, 4, 2, 49.99),
    (5, 6, 1, 499.99),
    (6, 9, 1, 349.99),
    (7, 2, 2, 29.99),
    (7, 8, 5, 14.99),
    (8, 7, 2, 9.99),
    (8, 8, 1, 14.99),
    (9, 1, 1, 1299.99),
    (9, 3, 2, 89.99),
    (9, 9, 2, 349.99),
    (10, 10, 1, 39.99),
]


def seed_demo_data():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM order_items")
        conn.execute("DELETE FROM orders")
        conn.execute("DELETE FROM products")
        conn.execute("DELETE FROM customers")

        conn.executemany(
            "INSERT INTO customers (name, email, city, signup_date) VALUES (?,?,?,?)",
            CUSTOMERS,
        )
        conn.executemany(
            "INSERT INTO products (name, category, price, stock) VALUES (?,?,?,?)",
            PRODUCTS,
        )
        conn.executemany(
            "INSERT INTO orders (customer_id, order_date, total, status) VALUES (?,?,?,?)",
            ORDERS,
        )
        conn.executemany(
            "INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES (?,?,?,?)",
            ORDER_ITEMS,
        )
        conn.commit()
    finally:
        conn.close()


def setup_fresh_db():
    initialize_db()
    seed_demo_data()


if __name__ == "__main__":
    setup_fresh_db()
    print("Database initialized and seeded successfully.")
