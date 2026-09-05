"""Approval pause/resume load probe (Phase 3).

Spins N concurrent chat turns that each hit the approval gate and asserts
every one returns 202 fast (worker thread released) instead of holding a
thread until timeout. Reports max/mean hold time — the Phase 3 headline
number (before: up to 120s per pending approval on /api/chat; after: ms).

Setup (register) runs sequentially and untimed; only the gated chat turn
is timed, so the number measures gate behavior rather than bcrypt/IO
contention. Runs fully in-process (TestClient, scratch DB).

Usage:
    python scripts/load_approvals.py [--n 30]

Exit codes: 0 = every turn paused fast, 1 = any failure or slowdown.
"""

import argparse
import concurrent.futures
import os
import sys
import tempfile
import time
from uuid import uuid4

_tmp = tempfile.mkdtemp(prefix="aiguard-load-")
os.environ["DB_PATH"] = os.path.join(_tmp, "load.db")
sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient
from db.database import initialize_db
from db.seed import seed_demo_data
from webapp import app
import webapp as webapp_module

MULTI = "SELECT id FROM products; SELECT id FROM customers;"
HOLD_BUDGET_MS = 10000


class AlwaysApprovalLLM:
    """Stateless stub: every worker call demands approval (unique call id
    per turn so concurrent resume rows never collide)."""

    def call(self, system, messages, tools=None):
        if "router" in (system or "").lower():
            return FakeLLMClient.text_response("SQL")
        return FakeLLMClient.tool_use_response(
            "sql_tool", {"sql": MULTI}, f"load-{uuid4().hex[:8]}"
        )


def _setup_user(i):
    """Register one user; returns its session cookie (untimed setup)."""
    import re as _re

    from fastapi.testclient import TestClient

    client = TestClient(app)
    page = client.get("/register")
    token = _re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    reg = client.post(
        "/register",
        data={
            "email": f"load{i:03d}@test.local",
            "password": "loadpass123",
            "csrf_token": token,
        },
    )
    assert reg.status_code == 200, f"register -> {reg.status_code}"
    return dict(client.cookies)


def _timed_chat(args):
    """One gated turn; times ONLY the chat POST (the gate behavior)."""
    from fastapi.testclient import TestClient

    i, cookies = args
    started = time.monotonic()
    try:
        client = TestClient(app)
        client.cookies.update(cookies)
        resp = client.post("/api/chat", json={"message": "do the bulk change"})
        held_ms = (time.monotonic() - started) * 1000
        if resp.status_code != 202:
            return (i, False, held_ms, f"chat -> {resp.status_code}")
        body = resp.json()
        if body.get("status") != "pending_approval" or not body.get("approval_id"):
            return (i, False, held_ms, f"bad 202 body: {body}")
        return (i, True, held_ms, "")
    except Exception as e:  # noqa: BLE001 — probe reports, never raises
        return (i, False, (time.monotonic() - started) * 1000, f"{type(e).__name__}: {e}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Approval pause/resume load probe.")
    parser.add_argument("--n", type=int, default=30, help="Concurrent gated turns.")
    args = parser.parse_args(argv)

    initialize_db()
    seed_demo_data()
    # No TestClient lifespan runs here, so mirror what it configures.
    from webapp_ratelimit import configure as configure_ratelimit

    configure_ratelimit(chat_per_min=30, sse_max_per_ip=3, auth_per_min=0)
    webapp_module._chat_llm_client_override = AlwaysApprovalLLM()

    # Warm the Redis verdict cache once so timed turns measure gate + DB
    # behavior, not one dead-Redis connect timeout each (see STATUS Phase 3).
    from agent.memory import ShortTermMemory

    ShortTermMemory()

    print(f"registering {args.n} users (untimed setup)...")
    cookies = [_setup_user(i) for i in range(args.n)]

    started = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.n) as pool:
        results = list(pool.map(_timed_chat, [(i, cookies[i]) for i in range(args.n)]))
    wall_s = time.monotonic() - started

    ok = [r for r in results if r[1]]
    holds = [r[2] for r in ok]
    print(f"\nAiGuard approval load probe — n={args.n}")
    print("=" * 68)
    print(f"paused fast : {len(ok)}/{len(results)}")
    if holds:
        print(f"hold max    : {max(holds):8.1f} ms  (budget {HOLD_BUDGET_MS} ms)")
        print(f"hold mean   : {sum(holds) / len(holds):8.1f} ms")
    print(f"wall total  : {wall_s:8.1f} s for {args.n} concurrent gated turns")
    for i, passed, held_ms, detail in results:
        if not passed:
            print(f"[FAIL] turn {i}: {detail}")
    print("=" * 68)

    if len(ok) != len(results) or (holds and max(holds) > HOLD_BUDGET_MS):
        print("LOAD PROBE FAILED")
        return 1
    print("LOAD PROBE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
