# Project Progress Tracking

**Overall Status**: ✅ Phases 1-8 COMPLETE · ✅ Phase 9 COMPLETE (SQLite→PG migration script + CI postgres service; latent PG-path bugs fixed)

> Last verified against code: August 21, 2026 — 79/79 tests passing without PostgreSQL (8 PG-gated skip); **87/87 with a live instance**.

## Phase 1 — Orchestrator + Tools
**Status:** ✅ Completed

**Step-by-step Execution Details:**
1. **Implemented Orchestration Loop**: Hand-built plan -> act -> observe Python state machine in `agent/orchestrator.py`. Supports maximum 15 iterations to prevent runaway execution.
2. **Integrated Claude API**: Utilized Anthropic's Claude API with native tool-calling (function calling) capabilities.
3. **Developed Calculator Tool**: Added zero-risk mathematical tool.
4. **Developed Web Search Tool**: Added external information retrieval capability.
5. **Developed SQL Tool**: Created execution engine for database queries against local SQLite database.
6. **Implemented Short-term Memory**: Session history tracking with `ShortTermMemory` in `agent/memory.py` to maintain full conversational context.

## Phase 2 — SQL Guardrail Layer (The Safety Shield)
**Status:** ✅ Completed

**Step-by-step Execution Details:**
1. **Integrated `sqlglot`**: Used for AST-based SQL analysis to move beyond bypassable regex matching.
2. **Developed Rule Engine**: Implemented `SQLGuardrail` in `guardrails/sql_guardrail.py`.
3. **Implemented Destructive Block Rules**: Hard-blocked `DROP`, `TRUNCATE`, `ALTER`, and `CREATE`.
4. **Implemented WHERE Clause Enforcement**: Blocked `DELETE` and `UPDATE` missing `WHERE` clauses.
5. **Implemented Allow-list Enforcement**: Checked extracted tables against `ALLOWED_TABLES` (customers, orders, products, etc.).
6. **Created Test Database**: Seeded demo SQLite database for safe execution.
7. **Created Adversarial Test Suite**: Developed 15 specific malicious prompts to verify guardrail.
8. **Achieved 100% Block Rate**: All 15 adversarial tests successfully blocked prior to execution.

## Phase 3 — Human-In-The-Loop Approval
**Status:** ✅ Completed

**Step-by-step Execution Details:**
1. **Added Risk Classification**: Classified multi-statement batches or large row-count queries as `REQUIRES_APPROVAL`.
2. **Built Approval Handlers**: Developed interactive gatekeepers in `approval/gate.py`.
3. **Implemented CLI Approval**: Added synchronous command-line prompt for development.
4. **Implemented Auto-Approve/Deny**: Added modes for demo and strict safety environments.
5. **Approval Routing**: Wired orchestrator to pause execution, store request, and await user decision.
6. **Execution After Approval**: Ensured queries execute only upon receiving explicit human approval.

## Phase 4 — Memory + Tracing
**Status:** ✅ Completed

**Step-by-step Execution Details:**
1. **Implemented Long-Term Memory**: Added fact distillation from sessions to persistent storage.
2. **Memory Retrieval**: Enabled loading facts upon session initialization for continuous context.
3. **Built Event Tracing**: Added structured logging for every plan, tool invocation, guardrail verdict, and final answer.
4. **Trace Replay API**: Created endpoints to reconstruct the entire session from logs.
5. **Error Handling & Resilience**: Hardened orchestrator to cleanly recover from tool failures and clean up traces.

## Phase 5 — Polish + Web Application
**Status:** ✅ Completed

**Step-by-step Execution Details:**
1. **Developed FastAPI Backend**: Built REST endpoints in `webapp.py` to serve UI.
2. **Integrated Security**: Added API key authentication.
3. **Built React UI**: Implemented chat interface, memory inspector, and approval queue visualization.
4. **Implemented Cost Budgeting**: Added `SESSION_COST_BUDGET_USD` to halt agents if budget is exceeded.
5. **Comprehensive Testing**: Validated test suite across webapp, resilience, guardrails, and smoke scenarios.
6. **Documentation**: Authored `DEPLOYMENT.md` and `report.md`.

## Phase 6 — Advanced Security & Compliance
**Status:** ✅ Completed

**Step-by-step Execution Details:**
1. **PII Detection Guardrail**: Implemented `guardrails/pii_guardrail.py` masking emails (`***@domain`) and phone numbers in query results before they reach the user; applied in `SQLTool._format_rows`.
2. **Hard Token Cutoffs**: `SESSION_MAX_TOKENS` enforcement alongside `SESSION_COST_BUDGET_USD` cost budgeting.
3. **Expanded Adversarial Suite**: Grew destructive-prompt coverage from 15 to 17 vectors (multi-statement DDL, obfuscated variants) — 17/17 blocked.

## Phase 7 — Scalability & Multi-Agent Collaboration
**Status:** 🔶 In Progress

**Completed so far:**
1. **Supervisor-Worker Architecture**: `agent/supervisor.py` routes tasks (LLM-based SQL vs RESEARCH decision, traced as `supervisor_route` events); `agent/workers.py` provides `SQLWorker` (sql_tool only) and `ResearchWorker` (web_search + calculator). Wired into `Orchestrator.run()`.
2. **Redis Short-Term Memory**: `ShortTermMemory` syncs message history to Redis (`REDIS_URL`, 24h TTL) when reachable; falls back gracefully to SQLite-only persistence otherwise. Dependency present in requirements.txt.
3. **PostgreSQL Data Layer**: Dialect-aware `get_connection()` in `db/database.py`; `DATABASE_URL=postgres://...` activates a pooled psycopg2 connection wrapper with placeholder/schema translation.

**Known gaps being closed (Aug 21 audit):**
1. Cost/token budgets were bypassed on the supervisor path (enforcement lived in now-dead `_call_llm`).
2. Worker-path tool calls were not persisted to `app_tool_calls`, breaking the approval queue JOIN and full audit trail.
3. Workers lacked retry/backoff on tool failures.
4. ResearchWorker path had zero test coverage; FakeLLMClient hardcoded routing to "SQL".
→ All fixed and tested in `tests/test_supervisor_and_budget.py` (see Phase 7 completion below).

## Phase 7 — Completion (Aug 21, 2026)
**Status:** ✅ Completed

1. **Budget enforcement restored**: `agent/budget.py::BudgetGuardedLLMClient` wraps every LLM call on the supervisor/worker path; `SESSION_COST_BUDGET_USD` / `SESSION_MAX_TOKENS` halt sessions with clear traced messages; token accounting reflects real usage at session end.
2. **Audit trail unified**: shared `db.database.record_tool_call` used by orchestrator loop and workers; approval-queue JOIN works on every path.
3. **Resilience unified**: shared `tools.base.execute_with_retry` (bounded retries + exponential backoff) on both execution routes; workers honor configurable `WORKER_MAX_ITERATIONS`.
4. **Test coverage**: both routing decisions, budget halts, persistence, retry recovery — 6 new tests.
5. **PostgreSQL hardened**: dialect fixes (`INSERT OR IGNORE` → `ON CONFLICT DO NOTHING`; `total_changes` → portable `rowcount`) + conditional integration tests (`tests/test_postgres_integration.py`) + setup docs in `DEPLOYMENT.md`.

## Phase 8 — UI/UX Enhancements
**Status:** ✅ Completed

1. **Real-Time Monitoring Dashboard**: `/dashboard` page with live cards (sessions, active, messages, tool calls, pending approvals), guardrail verdict breakdown, per-tool usage table, recent-session links to trace replay. Backend: `GET /api/stats` snapshot + `GET /api/stream` Server-Sent Events feed pushing fresh snapshots every 2s with automatic polling fallback. Verified streaming against real uvicorn.
2. **Visual Query Builder Fallback**: `/query-builder` page for when the agent struggles with a complex schema question — a human assembles the SELECT visually (table picker, column checkboxes, filter rows with fixed operators, order-by/direction/limit) and runs it through the same safety stack as agent queries.
   - Backend: `tools/query_builder.py` owns the contract (`QueryBuilderRequest` Pydantic model, schema introspection, SQL construction, guarded execution).
   - Safety: identifiers validated against live introspection of `ALLOWED_TABLES`; every filter value is a bound parameter; numeric values coerced by declared column type; LIKE restricted to non-numeric columns with contains semantics; generated SQL still passes `SQLGuardrail.check()` before execution; results PII-masked per cell like `sql_tool`.
   - Read-only by design: single SELECT only, no session/trace/tool-call writes, so builder runs never pollute agent metrics.
   - API: `GET /api/query-builder/schema`, `POST /api/query-builder/run`. Covered by 9 dedicated tests in `tests/test_query_builder.py` and verified against real uvicorn.

## Remaining
1. Optional builder extensions: FK-based joins, GROUP BY/aggregates, persisted audit rows for builder runs.
2. Optional: Redis service in CI; publish repo to GitHub to activate the workflow.

## v1.6.0 — Hardening & Quality (Sep 2026)
**Status:** ✅ Completed

- **Bounded thread-safe LRU SELECT cache** (`tools/sql_tool.py`):
  `_ThreadSafeLRUCache` keyed on the raw SQL string, sized by
  `config.SQL_QUERY_CACHE_SIZE` (default 128, 0 disables). Any
  successful write clears the cache so we never serve stale rows
  after an INSERT/UPDATE/DELETE.
- **`/api/chat` uses `WebApprovalHandler` by default** so the
  `/approval-queue` UI is actually reachable from the web frontend.
  Pass `auto_approve: true` in the request body to fall back to
  `AutoApproveHandler` for scripted demos.
- **In-process rate limiters** (`webapp_ratelimit.py`):
  `CHAT_RATE_PER_MIN` (default 30) on `/api/chat`,
  `SSE_MAX_PER_IP` (default 3) on `/api/stream`. No-ops when 0.
- **Extended `PIIGuardrail`**: Luhn-validated credit card numbers,
  US SSNs, IPv4 addresses, and E.164 international phone numbers
  are now masked alongside emails and US/Canada phones.
- **Structured logging** via `app_logging.configure_logging`; the
  webapp lifespan installs a stream handler on startup.
  Override with `LOG_LEVEL=DEBUG|INFO|WARNING|ERROR`.
- **`pyproject.toml`** with explicit package metadata, dev extras,
  and an `agentic-guardrails` console script.
- **`__all__` exports** in `agent`, `tools`, `approval`, `db`.
- **`/api/sessions` JSON list** with optional `user_id` filter and
  clamped `limit` (1–500).
- **Removed dead `Orchestrator._call_llm`** (unreachable after the
  supervisor refactor).
- **Test suite grew from 87 → 131 tests** (123 pass without PG,
  8 PG-gated, 1 deprecation warning).

## Phase 9 — Production Hardening (Aug 21, 2026)
**Status:** ✅ Completed

1. **SQLite→PostgreSQL migration script** (`db/migrate_sqlite_to_pg.py`): copies all app + demo tables parents-before-children, idempotent via `ON CONFLICT DO NOTHING` (re-runnable), `--truncate` flag for clean cutover, resyncs SERIAL sequences past migrated ids (only for tables that expose an integer `id` column — `app_*` tables use TEXT PKs), finishes with per-table source/target count verification and nonzero exit on mismatch.
   - CLI: `DATABASE_URL=postgres://... python -m db.migrate_sqlite_to_pg [--source data/guardrails.db] [--truncate]`
   - Tests: `tests/test_migration_script.py` — copy/round-trip values, idempotent re-run merge, truncate clears target-only rows, sequence advance (`RETURNING id` insert lands above migrated max), plus always-on refusal tests for non-PG targets and missing sources. Verified end-to-end against a real postgres:16 container (47 demo rows migrated, exit 0).
2. **CI workflow** (`.github/workflows/ci.yml`): Python 3.12 + `postgres:16` service container with `TEST_DATABASE_URL` exported, so the PG-gated integration and migration suites run on every push/PR instead of silently skipping.
3. **Latent PostgreSQL bugs fixed** (found the moment the PG tests could actually run — psycopg2-binary was missing from the venv so they had only ever skipped):
   - `PGConnectionWrapper.executemany` missing → `seed_demo_data()` crashed on PostgreSQL; added to `db/database.py`.
   - Two tests in `test_postgres_integration.py` referenced bare `initialize_db()` without importing it (NameError); imports added.
   - Reused disposable instances kept SERIAL sequences advanced after prior runs → both `pg_env` fixtures now reset state with `TRUNCATE ... RESTART IDENTITY`, making tests deterministic everywhere.
