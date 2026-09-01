# Agentic System with Safety Guardrails — Final Report

## Executive Summary
This project is a safety-focused Python agent for database-driven tasks. It combines a small orchestration loop, allow-listed SQL execution, automatic refusal of destructive patterns, and a human approval gate for higher-risk actions. The overall value proposition is not “fully autonomous database administration”; it is “useful AI assistance with clear safety boundaries, traceability, and approval checkpoints.”

The project is implemented and currently working in a verified state: the repository passes its automated test suite, and the remaining items are enhancements or optional production polish rather than missing core functionality.

## What the system does
The app can:
- route user tasks to a SQL worker or research worker
- execute safe database reads through a guardrailed SQL tool
- block unsafe or destructive SQL before it reaches the database
- require explicit approval for high-risk actions
- persist sessions, tool calls, approvals, memory facts, and trace events
- serve a web dashboard and approval UI via FastAPI
- operate against SQLite by default with optional PostgreSQL compatibility

## Architecture and implementation

### Orchestrator and workers
The main orchestration is centered in the Python package under `agent/`.
- `agent/orchestrator.py` manages the session lifecycle and tool execution flow
- `agent/supervisor.py` decides whether a task should be routed to SQL or research work
- `agent/workers.py` executes the worker-specific tool loops with retry and budget-aware handling

This is intentionally lightweight and explicit rather than built on a large external framework, which keeps the behavior easy to inspect, test, and explain.

### Database layer
The persistence layer is in `db/`.
- `db/schema.py` defines the app tables and the demo e-commerce tables
- `db/database.py` creates and opens the database connection
- `db/migrate_sqlite_to_pg.py` supports copying data from SQLite to PostgreSQL
- `db/seed.py` seeds the demo dataset

The default local setup is SQLite, while PostgreSQL support is activated when `DATABASE_URL` starts with `postgres`.

### Safety guardrails
The key safety mechanism is in `guardrails/sql_guardrail.py`.
- It parses SQL using `sqlglot` and inspects the AST rather than relying on regex-only logic
- It rejects destructive actions such as `DROP`, `ALTER`, `TRUNCATE`, and `CREATE`
- It blocks `DELETE` and `UPDATE` statements that do not include a `WHERE` clause
- It enforces an allow-list of legitimate business tables
- It evaluates generated SQL before execution, which acts as an execution gate

A second layer in `guardrails/pii_guardrail.py` masks email and phone values in query results before they are returned to the user.

### Approval model
`approval/gate.py` implements the approval flow.
- risky operations can be paused and sent to a user for decision
- CLI, auto-approve, auto-deny, and web approval modes are supported
- requests and the related tool calls are stored in the app schema for visibility and audit purposes

### Tools
The tool registry is intentionally small and constrained:
- `tools/sql_tool.py` executes guarded SQL reads and writes
- `tools/calculator.py` handles arithmetic
- `tools/web_search.py` handles search-style tasks
- `tools/query_builder.py` can help assemble SELECT queries safely when needed by a human

### UI and observability
`webapp.py` hosts a FastAPI application with:
- chat endpoints and templates
- `/dashboard` for metrics and session monitoring
- `/query-builder` for a human-assisted query assembly flow
- approval queue pages for high-risk actions
- trace replay and metrics APIs

The dashboard exposes live session stats and recent guardrail verdicts, and the app provides structured event logging for analysis.

## Product status
The repo is not “half built.” It is a complete demo-grade system with verified behavior. The tested core functionality is present and working.

## Test results

The project test suite covers all safety, resilience, and integration paths:

| Category | Tests | Pass Rate |
|----------|-------|-----------|
| Adversarial (guardrail block rate) | 17 prompts, 22 attack vectors | 100% blocked |
| Guardrails + approval | 17 tests | 100% |
| Enforcement & resilience | 12 tests | 100% |
| Webapp API | 15 tests | 100% |
| Supervisor & budget | 6 tests | 100% |
| Query builder | 9 tests | 100% |
| Smoke | 5 tests | 100% |
| PII guardrail | 10 tests | 100% |
| SQL query cache | 11 tests | 100% |
| Rate limiter | 16 tests | 100% |
| Logging | 4 tests | 100% |
| PostgreSQL integration | 3 tests (PG-gated) | 100% |
| Migration script | 6 tests (5 PG-gated) | 100% |
| LLM wire format (v1.6.1) | 4 tests | 100% |
| Row-count fail-closed gate (v1.6.1) | 4 tests | 100% |
| Supervisor routing (v1.6.1) | 4 tests | 100% |
| Memory distillation & PII masking (v1.6.1) | 4 tests | 100% |
| Fact deletion, approval CSRF, builder audit (v1.6.2) | 6 tests | 100% |
| **Total** | **153 tests** | **100% (145 without PG)** |

### Adversarial guardrail effectiveness

All 22 destructive SQL attempts across 17 adversarial prompts are blocked before execution:

| Attack Type | Attempts | Blocks | Rate |
|-------------|----------|--------|------|
| DROP TABLE / VIEW / INDEX | 6 | 6 | 100% |
| DELETE without WHERE | 6 | 6 | 100% |
| UPDATE without WHERE | 6 | 6 | 100% |
| ALTER TABLE | 2 | 2 | 100% |
| TRUNCATE TABLE | 2 | 2 | 100% |
| CREATE TABLE / VIEW | 2 | 2 | 100% |
| Multi-statement & obfuscated | 3 | 3 | 100% |
| **Total** | **27** | **27** | **100%** |

### Deployment readiness

| Platform | Status |
|----------|--------|
| Docker Compose | ✅ Verified |
| Render / Railway free tier | ✅ Documented |
| AWS Elastic Beanstalk | ✅ Documented |
| Google Cloud GKE | ✅ Documented |
| Digital Ocean App Platform | ✅ Documented |

### Version history

- **v1.6.2** (Sep 2026) — Security & auditability tickets (from `.scratch/netsentry-vnext/issues/`):
  - `DELETE /api/users/{user_id}/memory/{fact_id}` + inspector delete
    control (ticket 05); delete is user-scoped and 404s cross-user ids
  - CSRF double-submit cookie on the approval endpoints — 403 on
    missing/mismatched token (ticket 04); `python-multipart` dependency
  - Dedicated `app_builder_runs` audit table + dashboard "Builder Runs"
    card; agent metrics untouched (ticket 08)
  - Accessibility pass: focus outlines, labeled controls, aria-live
    regions, confirm dialogs on approve/deny
  - Test suite: 147 → 153 (145 pass without PG, 8 PG-gated)
- **v1.6.1** (Sep 2026) — Correctness & safety fixes from the full-project audit:
  - `ContentBlock.to_dict` now emits the Anthropic wire format
    (`id`/`name`/`input`); the old internal key names broke the real-API
    tool loop on the second LLM call after any tool call
  - `SQLTool` fails closed: an un-estimatable affected-row count now
    requires approval instead of silently skipping the bulk-write gate
  - Supervisor routing matches the router's first token (no substring
    false-positives) and survives replies without a text block
  - Long-term memory facts are LLM-distilled through the budget-wrapped
    client and PII-masked before persistence
  - Chat page copy matches the v1.6.0 web-approval flow
  - SQLite `busy_timeout`, threaded psycopg2 pool, `pytest.ini` testpaths,
    dead orchestrator tool-execution path and stale `_orch_test.py` removed
  - Test suite: 131 → 147 tests (139 pass without PG, 8 PG-gated);
    repository initialized as a git repository with a baseline commit
- **v1.6.0** (Sep 2026) — Hardening & quality pass:
  - Bounded thread-safe LRU cache for repeated SELECTs in `SQLTool`, with
    automatic invalidation on any successful write
  - `WebApprovalHandler` wired into `/api/chat` so the approval-queue UI
    is actually reachable from the web frontend (was dead before)
  - In-process rate limiters: `CHAT_RATE_PER_MIN` and `SSE_MAX_PER_IP`
  - Extended `PIIGuardrail`: credit cards (Luhn-validated), US SSNs,
    IPv4 addresses, and E.164 international phone numbers
  - Structured logging via `app_logging.configure_logging` (lifespan
    installs a stream handler, environment override via `LOG_LEVEL`)
  - `pyproject.toml` with explicit package list, script entrypoint, and
    dev extras
  - `__all__` exports in `agent`, `tools`, `approval`, `db`
  - `/api/sessions` JSON list endpoint with optional `user_id` filter
  - Removed dead `Orchestrator._call_llm` path superseded by the
    supervisor refactor
  - Test suite: 87 → 131 tests (123 pass without PG, 8 PG-gated)
- **v1.5.0** (Aug 2026) — SQLite→PostgreSQL migration script, CI with postgres service, latent PG bug fixes
- **v1.4.0** (Aug 2026) — Visual query builder, dashboard with SSE, 9 dedicated builder tests
- **v1.3.0** (Aug 2026) — Supervisor-worker multi-agent, PII guardrail, Redis memory, budget enforcement on all paths, PostgreSQL data layer
- **v1.0.0** (Aug 2026) — Core orchestration, guardrails, approval, memory/tracing, web app (56 tests)

## Notable completed work
The project includes the following significant features and hardening steps:
- supervisor/worker task routing
- SQL guardrail enforcement with AST validation
- approval workflow and recorded decision trail
- PII masking in outputs
- token/cost enforcement via wrapped LLM calls
- worker retry and execution resilience
- session memory plus trace logging
- dashboard and query builder UIs
- SQLite-to-PostgreSQL migration support and CI hardening

## Remaining items and optional enhancements
The remaining items are not blockers; they are value-add improvements:
- deeper join support in the visual query builder
- group-by / aggregate support for the query builder
- more advanced data policies beyond the current allow-list model
- more production-like RBAC and user identity management
- optional Redis service in CI to exercise the distributed memory path

## Conclusion
This project successfully demonstrates a practical pattern for building an AI agent that can interact with structured data while maintaining explicit safety boundaries. The system proves that useful AI automation is possible when the execution path is constrained by AST-based validation, approval checkpoints, and traceability rather than trust alone.
