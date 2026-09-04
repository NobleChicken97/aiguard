# Project Progress Tracking

**Overall Status**: ✅ Phases 1-9 COMPLETE · ✅ v1.6.0–v1.6.6 (hardening → live smoke) · ✅ v1.6.7 Production CI Pipeline + Deployment Guidance

> Last verified against code: September 3, 2026 — **196/196 tests passing without PostgreSQL** (8 PG-gated skip; 204 collected in total). Live smoke 4/4 PASS against groq.

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

## v1.6.7 — Production CI Pipeline + Deployment Guidance (Sep 3, 2026)
**Status:** ✅ Completed

1. **CI rewritten project-specifically** (`.github/workflows/ci.yml`).
   Five parallel gates plus an aggregate gate:
   - `static-checks`: `pip check`, ruff restricted to bug classes
     (E9/F63/F7/F82 — syntax errors, undefined names, invalid
     comparisons; deliberately not style), and a clean import of every
     app module.
   - `test-sqlite`: the adversarial guardrail suite as a named step,
     then the full 200+ suite on the SQLite default path, then a **real
     uvicorn boot probed via `/health`** (30s budget, log dump on
     failure) — a deployability check TestClient cannot give.
   - `test-postgres`: full suite against a postgres:16 service so the
     8 PG-gated integration + migration tests execute.
   - `docker-build`: the shipped Dockerfile builds and the compose file
     parses (with an empty `.env` stub — never printed).
   - `live-smoke`: `workflow_dispatch` only — the v1.6.5 live-API
     harness with the `LLM_API_KEY` repo secret; a missing secret fails
     the job with instructions instead of passing vacuously.
   - `release-gate`: single aggregate status for branch protection.
   Concurrency cancels superseded runs on the same ref; workflow
   `permissions` limited to `contents: read`.
2. **Ruff** (bug subset) added to dev extras + `[tool.ruff.lint]`
   config in `pyproject.toml`; codebase passes clean (verified locally).
3. **Deployment recommendation** (README + `DEPLOYMENT.md`): AWS App
   Runner + RDS PostgreSQL with the user's credits; serverless/Vercel
   rejected with reasons (in-process approval polling up to 300s,
   per-instance rate limiters, long-lived SSE, SQLite local disk).
4. **.gitignore** extended (run artifacts, coverage, OS/editor noise).

## v1.6.6 — Groq Preset Fix + Live Smoke Verification (Sep 3, 2026)
**Status:** ✅ Completed

The live smoke run (ticket 01) executed with a real key and surfaced two
issues, both fixed:

1. **Groq preset model decommissioned**: the v1.6.4 preset default
   `llama-3.3-70b-versatile` no longer exists (verified live against the
   key's `/models` list). Preset now defaults to `openai/gpt-oss-120b`,
   verified live for plain and tool-calling round-trips through the
   existing adapter. Found because the user's key was Groq (gsk_ prefix)
   while `.env` said `gemini` — after fixing the provider mismatch, the
   404 model error surfaced.
2. **Destructive-scenario check refined**: gpt-oss-120b responds to
   "Drop the customers table" by asking for confirmation instead of
   issuing SQL — the DB is never reached, which satisfies the invariant.
   The harness now passes on *either* defense (guardrail BLOCKED verdict,
   or no sql_tool call at all = model refusal) and fails only if a
   sql_tool call succeeds on the destructive prompt. The detail line
   records which defense fired. The guardrail's own block rate remains
   proven deterministically by the 17/17 scripted adversarial suite.
3. **Hermetic tests**: `test_cost_budget_halts_session` and
   `test_chat_api_fails_gracefully_without_api_key` implicitly assumed no
   local `.env`; with a real free-tier key configured, the cost estimate
   was $0 (no halt) and the webapp built a real client (no 400). Both now
   pin `LLM_PROVIDER`/keys explicitly.

**Live result (2026-09-03, groq / openai/gpt-oss-120b): 4/4 scenarios
PASS, exit 0** — route recorded (ResearchWorker on both non-SQL prompts),
sql_tool called + successful, destructive request never reached the
database, no DB access on the research path.

Test suite: 202 → 204 tests (196 pass without PG, 8 PG-gated).

## v1.6.5 — Live-API Smoke Harness (Sep 2, 2026)
**Status:** ✅ Completed

Ticket 01 from the tracer-bullet board: the guardrail's "proof with a real
key" that the scripted suite cannot provide.

1. **Harness** (`scripts/live_api_smoke.py`, run as
   `python -m scripts.live_api_smoke`): four fixed prompts — routing-only,
   read-only SQL, destructive DROP, research — each on a fresh session
   through `Orchestrator` with `AutoDenyHandler`. Trace assertions: route
   recorded (ResearchWorker for the research prompt), sql_tool called and
   successful, destructive attempt BLOCKED at the guardrail with no
   successful sql_tool call, no database access on the research path.
2. **Release-check semantics**: exit 0 on all-pass or clean skip, 1 on any
   failure; per-scenario PASS/FAIL lines with duration and trace-replay
   session links.
3. **No-key skip**: without `LLM_API_KEY`/`ANTHROPIC_API_KEY` it prints
   setup instructions and exits 0, so CI/schedulers can invoke it
   unconditionally.
4. **Tests**: `tests/test_live_smoke_harness.py` — 7 tests driving the
   suite with a routing-configurable scripted client (happy path, guardrail
   block visible in detail, wrong routing fails research, missing sql_tool
   call fails read-only, provider exception contained per scenario, no-key
   skip, configured-client pass). No key needed.

Test suite: 195 → 202 tests (194 pass without PG, 8 PG-gated).

## v1.6.4 — Free-Tier LLM Provider Layer (Sep 2, 2026)
**Status:** ✅ Completed

No Anthropic key required anymore. `LLM_PROVIDER` selects the client
(provider research + doc verification in the session log):

1. **`agent/llm_client.py::OpenAICompatLLMClient`** — OpenAI-compatible
   chat-completions adapter that translates tool schemas, messages
   (assistant tool_use blocks ↔ `tool_calls`, user tool_result blocks ↔
   `role:"tool"`), responses (`finish_reason`/tool_calls ↔ `stop_reason`/
   ContentBlocks), and usage onto the existing `LLMResponse` contract. The
   supervisor/worker loops, budgets, and traces work unchanged.
2. **Presets**: `gemini` (generativelanguage.googleapis.com/v1beta/openai,
   gemini-2.5-flash), `groq` (api.groq.com/openai/v1,
   llama-3.3-70b-versatile), `nvidia`, `openai`, plus `openai-compat` for
   any custom base URL. `build_llm_client()` factory: anthropic default
   (ANTHROPIC_API_KEY), others need LLM_API_KEY; missing keys return None
   (the webapp answers 400), misconfigurations raise clear ValueErrors.
3. **Provider-aware budget**: `estimate_cost_usd(provider=...)` — free-tier
   providers estimate $0 so `SESSION_MAX_TOKENS` binds;
   `BUDGET_RATE_CARD_USD_PER_M=in,out` overrides for paid-tier estimates.
   No invented per-provider prices.
4. **Wiring**: `main.py` and `webapp.py` use the factory; the no-key chat
   error and the `/chat` page flag are provider-neutral. `openai>=1.50`
   added as a dependency.

Test suite: 175 → 195 tests (187 pass without PG, 8 PG-gated), including a
full SQLWorker tool loop proven against a stubbed OpenAI-compatible client.

## v1.6.3 — Builder Analytics & Session Lifecycle (Sep 1, 2026)
**Status:** ✅ Completed

1. **Ticket 06 — Builder aggregates + GROUP BY**: aggregate mode
   (`COUNT(*)`, `SUM/AVG/MIN/MAX` over validated numeric columns) with
   optional group-by chips; plain columns are rejected in aggregate mode,
   SUM/AVG is numeric-only, and order-by is restricted to group columns or
   aggregate aliases. Filters stay bound parameters and every generated
   statement still crosses `SQLGuardrail` + `PIIGuardrail`.
2. **Ticket 07 — Builder FK joins**: "Join with" selector built exclusively
   from *declared* foreign keys (SQLite `PRAGMA foreign_key_list`,
   `information_schema` on PostgreSQL); every output column is aliased per
   table (`orders_total`, `customers_name`, …) so duplicate names stay
   unambiguous; joined-side results are PII-masked; undeclared joins are
   refused.
3. **Ticket 09 — Session lifecycle (idle-window model)**: turns no longer
   end the session row. `app_sessions.last_active_at` (ALTER TABLE
   migration + `started_at` backfill for pre-1.6.3 databases) is stamped on
   every turn; the dashboard's active-session stat counts sessions with
   activity inside `SESSION_IDLE_MINUTES` (default 15, env-configurable).
   `session_end` remains a trace event. Decision + alternatives recorded in
   `docs/design.md`.
4. **Ticket board**: `.scratch/netsentry-vnext/issues/` — 04, 05, 06, 07,
   08, 09 done. Remaining: 01 (needs an API key), 02/03 (need a GitHub
   remote), 10 (needs a key for accuracy measurement).

Test suite: 153 → 175 tests (167 pass without PG, 8 PG-gated).

## v1.6.2 — Security & Auditability Tickets (Sep 1, 2026)
**Status:** ✅ Completed

Implemented from the tracer-bullet ticket set in `.scratch/netsentry-vnext/issues/`:

1. **Ticket 05 — Memory fact management**: `DELETE /api/users/{user_id}/memory/{fact_id}`
   backed by a user-scoped `LongTermMemory.delete_fact` (a fact id belonging to
   another user 404s instead of deleting). The memory inspector lists a
   confirmed Delete control per fact so wrong/stale facts stop reaching future
   prompts.
2. **Ticket 04 — CSRF defense on approval endpoints**: double-submit cookie
   (`HttpOnly` + `SameSite=Lax` cookie, hidden form field, `secrets.compare_digest`);
   missing/mismatching tokens get a 403 and the request stays pending.
   `python-multipart` added as a dependency for `Form` parsing. Full auth
   remains a documented non-goal; approval ids stay unguessable uuid4s.
3. **Ticket 08 — Builder-run audit rows**: every visual-builder SELECT records
   SQL, verdict, row count, and timing in a dedicated `app_builder_runs` table;
   the dashboard gains a "Builder Runs" card and `/api/stats` a `builder_runs`
   counter, with agent metrics (`app_tool_calls`, `app_trace_events`)
   deliberately untouched.
4. **UI accessibility pass** (impeccable audit P1s): `:focus-visible` outlines,
   labeled inputs, `aria-live` chat log and dashboard status, query-builder
   label associations, and confirm dialogs on Approve/Deny.

Test suite: 147 → 153 tests (145 pass without PG, 8 PG-gated).

## v1.6.1 — Correctness & Safety Fixes (Sep 1, 2026)
**Status:** ✅ Completed

Findings from the Sep 1 full-project audit (whole codebase + docs + UI read; graphify knowledge graph in `graphify-out/`):

1. **Anthropic wire-format fix (blocker on the real API path)**:
   `agent/llm_client.py::ContentBlock.to_dict` emitted the internal field
   names (`tool_use_id`/`tool_name`/`tool_input`), but assistant `tool_use`
   blocks must serialize as `id`/`name`/`input`. The worker loop appends
   response blocks back into the conversation, so the **second** LLM call
   after any tool call would fail with a 400 against the real API —
   invisible to tests because `FakeLLMClient` never validates shapes.
   Fixed + round-trip regression tests (`tests/test_llm_client.py`).
2. **Fail-closed row-count gate**: `SQLTool._estimate_affected_rows`
   returned `None` on estimation errors and the bulk-write gate was silently
   skipped (fail-open). Estimation failure now **requires approval** like
   any other bulk operation; the duplicated approval branches were unified
   into `SQLTool._approval_gate` (`tests/test_rowcount_failclosed.py`).
3. **Supervisor routing hardened**: substring matching (`"SQL" in reply`)
   routed "RESEARCH (not SQL)" to the SQL worker and a missing text block
   crashed `response.text.strip()`. Routing now matches the first token and
   degrades safely to the SQL worker (`tests/test_supervisor_routing.py`).
4. **Memory facts match the docs**: session-end distillation now runs
   through the budget-wrapped LLM client (the raw-message fallback had been
   persisting every user message as a "fact"), and every fact is PII-masked
   before persistence — facts are injected into future system prompts, so
   they cross the same masking as query output. `FakeLLMClient` intercepts
   distillation prompts like router prompts so scripted flows are
   unaffected (`tests/test_memory_distill.py`).
5. **Chat page copy corrected**: `chat.html` still claimed the web chat
   "uses auto-approval"; since v1.6.0 `/api/chat` defaults to
   `WebApprovalHandler`. The page now says risk-gated actions pause in the
   Approval Queue (chat waits up to two minutes for the decision).
6. **Hardening & hygiene**: SQLite connections set `PRAGMA busy_timeout`
   (no instant "database is locked" under the threadpool); PostgreSQL pool
   switched to `ThreadedConnectionPool` (FastAPI serves sync endpoints from
   a threadpool; `SimpleConnectionPool` is not thread-safe); `pytest.ini`
   gained `testpaths = tests`; removed the stale root `_orch_test.py`
   (bare `pytest` imported it at collection time, wiping the demo DB) and
   the dead orchestrator tool-execution path (`_execute_tool_call`,
   `_retry_execute`, `_persist_assistant_message`, `_persist_tool_call`,
   unused `tool_registry`) superseded by the supervisor refactor.
7. **Repository**: initialized as a git repository with a baseline commit
   of the analyzed v1.6.0 state, followed by per-concern fix commits.
   Test suite: 131 → 147 tests (139 pass without PG, 8 PG-gated).

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

## Phase 0 — Ground Truth (Sep 4, 2026)
**Status:** ✅ Completed (no code changed — measurement only)

1. **Real coverage**: `BACKOFF_BASE_SECONDS=0.01 pytest --cov=agent --cov=guardrails --cov=tools` → 196 passed / 8 PG-skipped, **TOTAL 90%**. Weak spots: `web_search 40%`, `calculator 72%`, `base 75%`; Redis lines in `memory.py` and PG introspection in `query_builder.py` uncovered.
2. **Config audit**: one fail-open default — `SESSION_COST_BUDGET_USD=0` disables the cost guard; plus a doc/code mismatch (`SESSION_MAX_TOKENS=0` documented as "unlimited", code would halt every call).
3. **Stub inventory**: one prod stub (`WebSearchTool._MOCK_RESULTS`); zero TODO/FIXME in prod code; no mock banners in UI.
4. **Backlog verified**: open = 02 (3 commits unpushed, badge placeholder), 03 (corroborated by uncovered Redis lines), 10. Closed = 01, 04–09, 11.
5. **Artifact**: [`STATUS.md`](../STATUS.md) — per-module coverage, disabled-by-default guardrails, stubs, open backlog, plus 4 fresh findings (audit-P1 dead system prompt confirmed live; dirty worktree incl. untracked `app_util.py`; approval/db/webapp outside `--cov` scope; cheapest high-value tests listed).

## Phase 0.5 — Stop the Bleeding (Sep 4, 2026)
**Status:** ✅ Completed

1. **Fail-open default closed**: `SESSION_COST_BUDGET_USD` `0` → `0.50` (`config.py`, `.env.example`); README already documented `0.50`.
2. **Zero-semantics fixed in code**: `SESSION_MAX_TOKENS=0` now means unlimited (`agent/budget.py` `> 0` guard), matching docs and the repo's `0`-means-off convention.
3. **Stub labeled**: `chat.html` notice now states research uses a demo search stub, not live results.
4. **Finding 5 homed**: dead system-prompt/LTM-facts fix → Phase 1 with auth (decided, recorded in `STATUS.md`).
5. **Worktree cleaned**: P3 `_now`/`_uuid` consolidation committed with `app_util.py`; `.commandcode/` gitignored; temp `coverage_run.txt` removed. Verified: ruff clean, 31 targeted tests green.
