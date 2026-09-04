# NetSentry Technical Design

## High-level architecture

```text
User / Browser
    |
    v
FastAPI Web App (webapp.py)
    |  - /chat
    |  - /dashboard
    |  - /query-builder
    |  - /approval-queue
    v
Orchestrator
    |
    +--> SupervisorAgent
    |       |
    |       +--> SQLWorker
    |       |       +--> SQLTool
    |       |             +--> SQLGuardrail
    |       |             +--> DB (SQLite by default, Postgres optional)
    |       |
    |       +--> ResearchWorker
    |               +--> MockWebSearchTool / CalculatorTool
    |
    +--> Short-term memory + Long-term memory
    +--> Trace logger
    +--> Approval handler
```

The system is intentionally built as a small, explicit state machine rather than a heavyweight agent framework. That keeps the control flow understandable, auditable, and easy to reason about during interviews or debugging.

## Core data model / schema
The app uses a lightweight relational model with two layers:

1. Application state tables
   - app_sessions: one row per conversation
   - app_messages: persisted user/assistant/tool messages
   - app_tool_calls: persisted tool invocations with input payloads
   - app_approval_requests: pending or resolved user approvals
   - app_memory_facts: distilled long-term user facts
   - app_trace_events: append-only session trace log

2. Demo data tables
   - customers
   - products
   - orders
   - order_items

This design separates operational records from business data so the system can both answer user requests and audit the decision path afterward.

## Major components and responsibilities

### Orchestrator
Owns the top-level conversation loop. It persists user input, creates a session, coordinates the supervisor, writes the final answer, and closes the session cleanly.

### SupervisorAgent
Determines whether a task is best handled by the SQL worker or the research worker. This keeps database tasks isolated from general-purpose reasoning while still allowing the system to answer non-database questions.

### SQLWorker
Responsible for database-oriented tasks. It uses the SQL tool and stays within the approved schema and policy envelope.

### ResearchWorker
Handles general reasoning tasks using calculator and mock web-search stub tools. It is intentionally prevented from touching the database path.

### SQLTool
The execution layer for database access. It validates the generated SQL against the policy layer, runs the actual query, and formats the result to keep it safe and readable.

### SQLGuardrail
A static SQL analyzer built with sqlglot. It parses SQL into an AST and blocks or approves based on statement type and table allow-list.

### Approval handlers
- CLIApprovalHandler: interactive terminal approval
- AutoApproveHandler: testing convenience
- AutoDenyHandler: strict safety mode
- AsyncApprovalHandler: creates the pending approval row and unwinds the
  worker (Phase 3 pause/resume — no thread is ever held waiting); the
  approval row is the gate decision at resume time

### Memory and trace layers
- Short-term memory keeps the active session context
- Long-term memory persists distilled facts across sessions
- Trace logger records events for replay and debugging

## Technical decisions and trade-offs

### 1) Chosen: explicit state-machine orchestration instead of a major framework
What was chosen: custom orchestration logic and worker routing in Python with transparent control flow.

Alternatives:
- Full agent framework abstraction such as LangChain or other orchestration frameworks
- A single monolithic LLM call that tries to do everything at once

Why this won: the project is about safety and explicit decision boundaries. A custom orchestration loop makes approval, retries, and trace logging easier to inspect and harden than a black-box framework.

### 2) Chosen: AST-based SQL validation with sqlglot instead of regex or string matching
What was chosen: parse SQL via sqlglot and evaluate statement types and table access through an AST.

Alternatives:
- Regex-based deny-lists
- Query normalization without parsing

Why this won: AST validation handles real SQL structure more reliably and is harder to bypass with obfuscation or malformed strings. That is the core safety mechanism the project is built around.

### 3) Chosen: allow-listed table model
What was chosen: only customers, products, orders, and order_items are allowed.

Alternatives:
- No table restrictions
- More complex policy engine with per-table permissions

Why this won: keep the system understandable and demoable. The project’s value is proving the principle before scaling to more complex policy layers.

### 4) Chosen: human approval before risky mutations
What was chosen: execute-only-after-approval path for high-risk SQL operations.

Alternatives:
- Auto-approve all writes
- Auto-deny all writes

Why this won: the project demonstrates a practical middle ground. High-impact actions are not executed silently, and the approval record becomes part of the audit trail.

### 5) Chosen: SQLite-first local database with optional Postgres compatibility
What was chosen: SQLite for local development and a Postgres-compatible code path via DATABASE_URL detection.

Alternatives:
- Only Postgres from day one
- Only SQLite with no migration path

Why this won: SQLite simplifies local demos and tests, while the Postgres path gives the project a realistic scaling story without requiring a cloud dependency for every run.

### 6) Chosen: session trace persistence in app_trace_events
What was chosen: append-only trace events for user messages, tool calls, violations, approvals, and final answers.

Alternatives:
- In-memory logs only
- No visible trace system

Why this won: traceability is a major part of the product’s safety story. The project is not just “safe by accident”; it proves why a decision was allowed or blocked.

### 7) Chosen: PII masking as a second-layer safety check
What was chosen: mask email and phone values in query output before returning them to the user.

Alternatives:
- Return raw data without filtering
- Only rely on the SQL guardrail

Why this won: the project intentionally treats data minimization as part of the safety contract. Guardrails protect the database, and output filtering helps protect user privacy.

### 8) Chosen: idle-window session lifecycle (v1.6.3)
What was chosen: a session row stays `active` for its whole life; "active right now" is derived from a `last_active_at` timestamp (stamped on every turn) falling inside `SESSION_IDLE_MINUTES` (default 15). Legacy databases are migrated in place with a `started_at` backfill.

Alternatives:
- End the session after every agent turn (the original behavior) — the dashboard's active-session stat read ~zero and resuming silently reopened an "ended" row.
- End only on explicit close — there is no close signal in a web chat, so sessions would never actually end.

Why this won: the dashboard stat reflects reality during and shortly after a conversation, resume is never contradictory, and stale rows age out of the active count without a cleanup job. `session_end` remains a trace event marking the end of a turn, not a row state.

### 9) Chosen: provider-agnostic LLM client (v1.6.4)
What was chosen: an OpenAI-compatible chat-completions adapter (`agent/llm_client.py::OpenAICompatLLMClient`) behind the existing `LLMResponse` contract, selected by a `build_llm_client()` factory from `LLM_PROVIDER` — `anthropic` (default, legacy), `gemini`/`groq`/`nvidia`/`openai` presets, or `openai-compat` with an explicit `LLM_BASE_URL`.

Alternatives:
- Keep the Anthropic-only client — caps the project behind one paid key.
- One bespoke SDK client per provider — N clients to maintain for an identical wire shape.

Why this won: Gemini, Groq, and NVIDIA all expose OpenAI-compatible chat endpoints with function calling (verified against provider docs, Sep 2026), so a single translation layer (tool schemas, messages incl. tool calls/results, usage) serves every free-tier provider, and the supervisor/worker loops, budgets, and traces work unchanged. Budget note: free-tier providers estimate $0 cost, so `SESSION_MAX_TOKENS` is the binding limit unless `BUDGET_RATE_CARD_USD_PER_M` supplies list prices.

## Known limitations and deferred hardening
- The policy engine is intentionally conservative and schema-specific; it is not a generic SQL security product.
- The project does not attempt deep semantic validation of every business rule; it focuses on execution boundaries and explicit approvals.
- The approval system is simple and useful for demos, but it would need stronger identity, audit, and escalation flows for real multi-user deployment.
- The architecture is local-first and deliberately lightweight; it is not yet designed for large multi-tenant workloads or high concurrency.
- Some Postgres compatibility work is present, but this is still a demo-grade path rather than a full production-grade database abstraction.

## External dependency notes

### Anthropic Claude API
Used for LLM-driven routing and reasoning. The system accepts a real Claude key and falls back to a demo path when configured with a local test client. This makes the project portable for demonstration and evaluation.

### sqlglot
Used for SQL parsing and AST validation. This is the single most critical dependency for the safety layer.

### FastAPI + Jinja2
Powers the web interface, APIs, and HTML templates for monitoring and approval workflows.

### SQLite / optional Postgres
Used for persistence and local evaluation. PostgreSQL support exists for migration and compatibility testing but is not the default path for local development.

### Optional Redis
The project includes Redis support when configured, mainly for distributed short-term memory patterns. The code gracefully falls back when Redis is unavailable.
