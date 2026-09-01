# Future Tasks and Roadmap (Todos)

> **Last verified against code**: August 21, 2026 — 79/79 tests passing without PostgreSQL (8 PG-gated tests skip); 87/87 with a live instance (`TEST_DATABASE_URL`).
>
> **Status legend**: ✅ done · 🔶 partially done (gaps listed) · ⬜ not started

## Phase 6 — Advanced Security & Compliance (Completed)
- [x] **PII Detection Guardrail**: `guardrails/pii_guardrail.py` masks emails/phone numbers in SQL results before presentation (`tools/sql_tool.py:_format_rows`). Covered by `tests/test_pii_guardrail.py`.
- [x] **Advanced Cost Control**: Hard token cutoffs (`SESSION_MAX_TOKENS`) in addition to cost calculation (`SESSION_COST_BUDGET_USD`).
- [x] **SQL Injection Variants**: Adversarial suite expanded from 15 to 17 cases in `tests/test_prompt_adversarial.py`.

## Phase 7 — Scalability & Multi-Agent Collaboration
- [x] **Multi-Agent Architecture**: Supervisor-worker model implemented — `agent/supervisor.py` (LLM-based SQL/RESEARCH routing) + `agent/workers.py` (SQLWorker, ResearchWorker), wired into `Orchestrator.run()`.
- [x] **Redis Memory Store**: Short-term session state syncs to Redis when available with graceful SQLite-only fallback (`agent/memory.py:ShortTermMemory._try_init_redis`). Dependency already in requirements.txt.
- [x] **PostgreSQL Enterprise Migration (data layer)**: Dialect-aware `get_connection()` in `db/database.py` — `DATABASE_URL` starting with `postgres` switches to a psycopg2 `SimpleConnectionPool` behind `PGConnectionWrapper` (?→%s placeholder translation), with schema translation (`INTEGER PRIMARY KEY`→`SERIAL`, drop `AUTOINCREMENT`) in `initialize_db()`.
- [x] **PostgreSQL dialect fixes** (Aug 21 audit): `record_tool_call` used SQLite-only `INSERT OR IGNORE` (now `ON CONFLICT DO NOTHING` on PG) and `resolve_approval` used SQLite-only `conn.total_changes` (now portable `cursor.rowcount`). Both would have crashed on PostgreSQL.
- [x] **PostgreSQL Integration Tests**: `tests/test_postgres_integration.py` — schema/seed, idempotent tool-call persistence, approval resolution semantics. Auto-skipped unless `TEST_DATABASE_URL` points at a disposable instance; setup documented in `docs/DEPLOYMENT.md`.

### Phase 7 Consolidation Gaps (found in Aug 21 audit — being closed)
The supervisor refactor left the following regressions vs documented behavior:
- [x] **Budget bypass**: cost/token budgets were enforced only in `Orchestrator._call_llm`, which is dead code since `run()` delegates to `SupervisorAgent`. → Fixed via `agent/budget.py::BudgetGuardedLLMClient` wrapping all supervisor/worker LLM calls.
- [x] **Token accounting zeros**: session-end trace logged 0 tokens. → Wrapper accumulates real usage from every LLM call on any path.
- [x] **Audit-trail gap**: worker-path tool calls were never persisted to `app_tool_calls`, breaking the approval queue's INNER JOIN (`approval/gate.py:get_pending_approvals`) and full trace replay. → Shared `db.database.record_tool_call` used by both paths.
- [x] **Resilience gap**: workers executed tools without retry/backoff. → Shared `execute_with_retry` helper reused by orchestrator and workers.
- [x] **Untested research path**: `FakeLLMClient` hardcoded router→"SQL", so ResearchWorker was never exercised and several integration tests passed vacuously. → Route decision is now injectable; dedicated tests cover both routes.

## Phase 8 — UI/UX Enhancements
- [x] **Real-Time Monitoring Dashboard**: `/dashboard` page + `GET /api/stats` snapshot + `GET /api/stream` SSE feed (2s push, threadpool-isolated DB reads, automatic fetch-polling fallback if a proxy blocks SSE). Live cards for sessions/messages/tool calls/pending approvals, guardrail verdict breakdown (recent 500 events), per-tool usage table, and recent-session links into trace replay. Verified against real uvicorn.
- [x] **Visual Query Builder Fallback**: `/query-builder` page — when the agent struggles with a complex schema question, a human assembles the SELECT visually instead. Backend in `tools/query_builder.py`: dialect-aware schema introspection (`GET /api/query-builder/schema`, ALLOWED_TABLES only), validated construction (identifiers checked against live schema, filter values always bound parameters, numeric coercion by declared column type, LIKE restricted to text columns with contains semantics), and execution through the same `SQLGuardrail.check()` + `PIIGuardrail` masking path as agent queries. Read-only and session-less by design: single-SELECT only, no trace/app_tool_calls writes. Covered by `tests/test_query_builder.py` (9 tests) and verified against real uvicorn.

## Phase 9 — Production Hardening (Aug 21, 2026)
- [x] **SQLite→PostgreSQL migration script**: `db/migrate_sqlite_to_pg.py` — FK-safe copy order, idempotent `ON CONFLICT DO NOTHING` merges (safe re-runs), optional `--truncate` clean-cutover mode, SERIAL sequence resync past migrated ids (guarded to tables that actually expose an integer `id`), per-table source/target count verification with nonzero exit on mismatch. CLI: `DATABASE_URL=postgres://... python -m db.migrate_sqlite_to_pg [--source path] [--truncate]`. Covered by 5 PG-gated tests + 1 always-on refusal test (`tests/test_migration_script.py`); verified end-to-end against a real postgres:16 container.
- [x] **CI with postgres service**: `.github/workflows/ci.yml` — pytest on Python 3.12 with a `postgres:16` service container and `TEST_DATABASE_URL` set, so the previously-skip-only PG integration + migration suites actually execute on every push/PR.
- [x] **Latent PostgreSQL-path bugs fixed** (surfaced the moment the PG tests could actually run):
  - `PGConnectionWrapper` lacked `executemany`, crashing `seed_demo_data()` on PostgreSQL → added to `db/database.py`.
  - `test_postgres_integration.py` had two tests calling bare `initialize_db()` without importing it (NameError; never caught while skipped) → imports added.
  - Reused disposable PG instances left SERIAL sequences advanced, breaking deterministic seeding → both `pg_env` fixtures now `TRUNCATE ... RESTART IDENTITY` before each test.

## Maintenance and Technical Debt
- [x] Resolve `StarletteDeprecationWarning` noise in the test suite (upstream deprecation; harmless, suppressed in pytest config if needed).
- [x] Implement query caching for frequent identical SELECTs (`SQLTool._query_cache`).
- [x] `WebApprovalHandler` polling runs inside FastAPI's threadpool so it does not block the event loop (sync poll loop by design; revisit if moving to async DB layer).
- [x] `/health` endpoint closes its connection deterministically (`try/finally`) — no leak under concurrency.

## v1.6.0 hardening & quality (Sep 2026) — completed
- [x] **Bounded thread-safe SELECT cache**: `tools/sql_tool.py` now uses a
  `_ThreadSafeLRUCache` (sized by `config.SQL_QUERY_CACHE_SIZE`). Any
  successful write invalidates the cache so we never serve stale rows
  after an INSERT/UPDATE/DELETE. Set `SQL_QUERY_CACHE_SIZE=0` to disable.
- [x] **Web approval flow reachable from chat**: `/api/chat` now uses
  `WebApprovalHandler` by default, so the `/approval-queue` UI is
  actually exercised end-to-end. Pass `auto_approve: true` in the
  request body to fall back to `AutoApproveHandler` for scripted demos.
- [x] **In-process rate limiters**: new `webapp_ratelimit.py` exposes a
  per-IP `TokenBucket` for `/api/chat` (`CHAT_RATE_PER_MIN`, default
  30) and a `ConcurrentStreamGuard` for `/api/stream`
  (`SSE_MAX_PER_IP`, default 3). Both are no-ops when their env var
  is 0.
- [x] **Extended `PIIGuardrail`**: Luhn-validated credit card numbers,
  US SSNs, IPv4 addresses, and E.164 international phone numbers are
  now masked in addition to emails and US/Canada phone numbers.
- [x] **Structured logging**: `app_logging.configure_logging` installs
  a single stream handler on the root logger; the webapp lifespan
  configures it on startup. Override level with `LOG_LEVEL`.
- [x] **`pyproject.toml`** with package list, `agentic-guardrails`
  console script, and dev extras.
- [x] **`__all__` exports** in `agent`, `tools`, `approval`, `db`.
- [x] **`/api/sessions` JSON list** with optional `user_id` filter and
  clamped `limit` (1–500).
- [x] **Removed dead `Orchestrator._call_llm`** (was unreachable after
  the supervisor refactor).
- [x] **Test suite grew from 87 → 131 tests** (123 pass without PG,
  8 PG-gated).

## Next Up (recommended order)
Tracked as tracer-bullet tickets in `.scratch/netsentry-vnext/issues/` (dependency-ordered; see ticket files for acceptance criteria). **Done:** 04 (approval CSRF), 05 (memory fact deletion), 06 (builder aggregates/GROUP BY), 07 (builder FK joins), 08 (builder audit rows), 09 (session lifecycle) — all in v1.6.2/v1.6.3. Remaining frontier:
1. **01** Live-API smoke harness — **needs an `ANTHROPIC_API_KEY`** (none configured; no `.env` exists)
2. **02** Publish repo to GitHub + activate CI — **needs the owner to create the repo/remote** → gates **03** Redis-in-CI
3. **10** Supervisor routing spike — LLM-vs-keyword accuracy measurement **needs a key**; the deterministic prefilter could be built without it if decided



