import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

DB_PATH = os.getenv("DB_PATH", "data/guardrails.db")

MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "15"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
BACKOFF_BASE_SECONDS = float(os.getenv("BACKOFF_BASE_SECONDS", "1.0"))
WORKER_MAX_ITERATIONS = int(os.getenv("WORKER_MAX_ITERATIONS", "5"))

RISKY_ROW_THRESHOLD = int(os.getenv("RISKY_ROW_THRESHOLD", "5"))

SESSION_COST_BUDGET_USD = float(os.getenv("SESSION_COST_BUDGET_USD", "0"))

# A session counts as "active" (dashboard stat) while it had activity within
# this many minutes; it is never marked ended just because a turn finished.
SESSION_IDLE_MINUTES = int(os.getenv("SESSION_IDLE_MINUTES", "15"))

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

ALLOWED_TABLES = {"customers", "products", "orders", "order_items"}

DEMO_SCHEMA_DESCRIPTION = """\
You have access to an e-commerce database with the following tables:

1. customers (id INTEGER PRIMARY KEY, name TEXT, email TEXT, city TEXT, signup_date TEXT)
2. products (id INTEGER PRIMARY KEY, name TEXT, category TEXT, price REAL, stock INTEGER)
3. orders (id INTEGER PRIMARY KEY, customer_id INTEGER, order_date TEXT, total REAL, status TEXT)
4. order_items (id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, quantity INTEGER, unit_price REAL)

Allowed tables: customers, products, orders, order_items.
Only generate SQL against these tables.
"""

SYSTEM_PROMPT = """\
You are a helpful AI assistant with access to tools: a calculator, a web search, and a SQL database tool.

When the user asks a question that requires database access, use the sql_tool to query the e-commerce database.
Always generate valid SQLite-compatible SQL.

Rules:
- Only query the allowed tables: customers, products, orders, order_items.
- Never attempt to destroy or modify data maliciously.
- If a tool call fails, report the failure honestly instead of making up results.
- Be concise in your responses.

{schema}
"""

MAX_TOKENS = 4096
SESSION_MAX_TOKENS = int(os.getenv("SESSION_MAX_TOKENS", "8192"))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite://")

# Bounded LRU cache for repeated SELECTs in SQLTool. 0 disables caching.
SQL_QUERY_CACHE_SIZE = int(os.getenv("SQL_QUERY_CACHE_SIZE", "128"))

# Per-IP rate limit on the chat + SSE endpoints. ``CHAT_RATE_PER_MIN=0``
# disables the limit. Defaults: 30 chat POSTs/min and 3 concurrent SSE
# streams per client.
CHAT_RATE_PER_MIN = int(os.getenv("CHAT_RATE_PER_MIN", "30"))
SSE_MAX_PER_IP = int(os.getenv("SSE_MAX_PER_IP", "3"))
