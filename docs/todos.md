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

## v1.6.5 — Live-API Smoke Harness (Sep 2, 2026) — completed
- [x] **Ticket 01 — live-API smoke harness**: `scripts/live_api_smoke.py`
  runs four fixed prompts (routing-only, read-only SQL, destructive DROP,
  research) end-to-end through `Orchestrator` with `AutoDenyHandler` and
  asserts trace outcomes: route recorded (ResearchWorker for research),
  sql_tool called + successful, destructive attempt `BLOCKED` at the
  guardrail with no successful sql_tool call, no DB access on the research
  path. Exit 0 pass/skip, 1 fail — usable as a release check. Skips cleanly
  with setup instructions when no LLM key is configured. Logic covered by
  7 scripted-client tests (`tests/test_live_smoke_harness.py`); the live
  run itself awaits a key in `.env`.

## v1.6.6 — Groq Preset Fix + Live Smoke Verification (Sep 3, 2026) — completed
- [x] **Groq preset default model fixed**: `llama-3.3-70b-versatile` was
  decommissioned by Groq (found live via 404 model_not_found); preset now
  `openai/gpt-oss-120b`, verified live for plain + tool calls.
- [x] **Destructive-scenario check refined**: guardrail block OR model
  refusal both pass; only a successful sql_tool call on the destructive
  prompt fails.
- [x] **Hermetic tests**: cost-budget + webapp no-key tests pin
  `LLM_PROVIDER`/keys so a local `.env` can't flip their outcomes.
- [x] **LIVE SMOKE 4/4 PASS, exit 0** (groq / openai/gpt-oss-120b,
  2026-09-03) — ticket 01 fully closed including the real-key run.

## v1.6.7 — Production CI Pipeline + Deployment Guidance (Sep 3, 2026) — completed
- [x] **CI rewritten project-specifically** (`.github/workflows/ci.yml`):
  five parallel gates — `static-checks` (pip check, ruff bug-class lint,
  module importability), `test-sqlite` (adversarial suite + full suite +
  real uvicorn boot probed via `/health`), `test-postgres` (postgres:16
  service so the 8 PG-gated tests execute), `docker-build` (Dockerfile
  builds + compose parses), and a dispatch-only `live-smoke` (LLM_API_KEY
  secret, never on push) — plus a `release-gate` aggregate job for branch
  protection. Concurrency-cancels superseded runs; `permissions:
  contents: read`.
- [x] **Ruff (bug subset E9/F63/F7/F82)** added to `pyproject.toml` dev
  extras + `[tool.ruff.lint]`; codebase passes clean.
- [x] **Deployment recommendation documented** (README + DEPLOYMENT.md):
  AWS App Runner + RDS PostgreSQL with existing credits; Vercel/serverless
  rejected with architectural reasons (in-process approval polling,
  per-instance rate limiters, SSE, SQLite disk).
- [x] **.gitignore** extended: run artifacts (`*.log`, coverage), OS/editor
  noise.

## Technical-debt audit (Sep 3, 2026) — findings pending approval
Full report: [`docs/audit-2026-09-03.md`](audit-2026-09-03.md). Highlights:
- **P1 (behavioral)**: system prompt + LTM facts are computed in
  `Orchestrator` but never sent to the LLM on the supervisor/worker path
  (dead since the supervisor refactor; docs claim otherwise). Fix = small
  behavior change, needs owner approval.
- **P2 (zero-risk deletions)**: `TraceLogger.log_plan`/`get_events`,
  `ContentBlock.to_message`, `FakeLLMClient.multi_tool_use_response`,
  `ToolRegistry.list_names`, dead `config.MAX_ITERATIONS` (+ stale doc
  mentions), dead `AGGREGATE_FUNCTIONS` constant, ~23 unused imports/locals.
- **P3 (consolidation)**: `_now`/`_uuid` ×4 files; no-key hint string ×2.
- **P4 (opportunistic)**: db connection helper, silent excepts → debug log,
  `db/__init__` export trim.

## Next Up (recommended order)
Ground truth for ordering: [`STATUS.md`](../STATUS.md) (Phase 0, 2026-09-04 — real 90% coverage, open tickets 02/03/10, one fail-open default).
Tracked as tracer-bullet tickets in `.scratch/netsentry-vnext/issues/` (dependency-ordered; see ticket files for acceptance criteria). **Done:** 04–09, **11** (v1.6.2–v1.6.4), **01 — live-API smoke harness** (v1.6.5, live-verified 4/4 in v1.6.6). Remaining frontier:
1. **02** Publish repo to GitHub + activate CI — **needs the owner to create the repo/remote** → gates **03** Redis-in-CI
2. **10** Supervisor routing spike — **now unblocked**: a free key is configured (`groq` / gpt-oss-120b), so latency/accuracy/cost of LLM routing vs a deterministic prefilter can be measured for real




