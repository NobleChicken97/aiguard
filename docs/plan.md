# AiGuard Build Plan

## Repo / project layout
- main.py — CLI entry point for interactive or single-prompt execution
- webapp.py — FastAPI app and monitoring endpoints
- config.py — runtime configuration and policy settings
- agent/ — orchestration, routing, memory, trace, and LLM wrappers
- approval/ — approval/request gate implementations
- db/ — database setup, schema, migrations, and seeding
- guardrails/ — SQL and PII policy enforcement
- tools/ — SQL, calculator, mock web search (demo stub), and query builder implementations
- ui/templates/ — dashboard, chat, approval, memory, and trace UIs
- tests/ — pytest coverage for guardrails, resilience, and integration
- docs/ — project summary and working logs

## Phase 1 — Core agent loop
Scope: basic plan → act → observe orchestration, tool registry, database access, demo e-commerce schema.
Status: Done.

## Phase 2 — SQL safety layer
Scope: static SQL validation, allow-list enforcement, destructive statement blocking, adversarial test coverage.
Status: Done.

## Phase 3 — Human approval workflow
Scope: approval demand for risky operations, CLI and web-based handling, recorded decisions.
Status: Done.

## Phase 4 — Memory and traceability
Scope: session memory, long-term fact capture, trace logging, replay support.
Status: Done.

## Phase 5 — Product polish and UI
Scope: dashboard, query builder, approval queue, monitoring pages, demo-friendly web app.
Status: Done.

## Phase 6 — Security hardening
Scope: PII masking, token and budget controls, additional adversarial SQL cases.
Status: Done.

## Phase 7 — Multi-agent and scalability improvements
Scope: supervisor-worker routing, redis memory, Postgres compatibility, audit trail cleanup, retry logic.
Status: Done.

## Phase 8 — Migration and CI hardening
Scope: SQLite-to-Postgres migration script, CI with Postgres service, latent PG bug fixes.
Status: Done.

## Dependencies between phases
- The orchestration loop had to exist before any guardrail or approval workflow could be wired in.
- Approval flow depended on the SQL tool and guardrail route being stable.
- Memory and traceability came after the base execution loop so there was a reliable session lifecycle to observe.
- The dashboard and query builder were built only after the underlying safety and data layers were working.
- Postgres and multi-agent improvements relied on the earlier core routes being stable and tested.

## Realistic MVP cut line
If time runs out, the minimum credible presentable version is:
- working agent conversation loop
- safe SQL query tool with allow-listed schema
- destructive query block rules
- approval required for high-risk actions
- trace logging and a working local demo database
- basic web UI showing chat and approval flow

That slice remains genuinely useful and is consistent with the project’s purpose: proving a safe, auditable AI agent in a data environment.

## Current status
The project is currently in a stable, tested state. All core safety and agent execution flows are in place, and the remaining items are enhancements or optional polish rather than missing core functionality.
