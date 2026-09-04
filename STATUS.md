# NetSentry — Ground-Truth Status (Phase 0)

> Measured, not assumed. Generated 2026-09-04 from a real coverage run +
> config audit + stub grep + backlog verification. This file is the source
> of truth for prioritizing Phases 1–7. Update the tables (not the prose)
> as each phase lands.

Measurement conditions: `BACKOFF_BASE_SECONDS=0.01 pytest
--cov=agent --cov=guardrails --cov=tools --cov-report=term-missing -q`
→ **196 passed, 8 skipped (PG-gated), TOTAL 90%** in 337s.
Backoff was minimized to skip pure-sleep time only; coverage % is unaffected.
Full suite at default backoff takes noticeably longer (sleep-dominated).
`approval/`, `db/`, `webapp.py` were out of `--cov` scope — see §5.

## 1. Coverage % per module (agent / guardrails / tools)

| Module | Cover | Uncovered lines → what they are |
|---|---|---|
| `agent/__init__.py` | 100% | — |
| `agent/budget.py` | 100% | — |
| `agent/supervisor.py` | 100% | — |
| `agent/trace.py` | 100% | — |
| `guardrails/__init__.py` | 100% | — |
| `tools/__init__.py` | 100% | — |
| `agent/workers.py` | 98% | `77` — iteration-limit fallback return |
| `guardrails/pii_guardrail.py` | 94% | `47, 60, 70` — Luhn/intl-phone length-guard `return raw` branches |
| `tools/sql_tool.py` | 94% | `93` missing-`sql` param; `194` missing-table; `205–207` count-query error; `223–229` empty-SELECT cache path; `265` 50-row truncation line |
| `agent/llm_client.py` | 91% | `91–94, 97–109` real-Anthropic `client`/`call` path; `286–287, 303–304` OpenAI message/usage mapping edges |
| `agent/memory.py` | 91% | `24, 31–33, 37` **Redis paths (never exercised — cf. ticket 03)**; `48–59` `add_tool_result`; `65` `to_dict` |
| `agent/orchestrator.py` | 91% | `64, 102` facts-loaded join (no test ever loads facts); `92` resume-missing-session `ValueError`; `127–136` tool-message restore on resume; `198–199` generic supervisor-failure branch; `253–254` distill-failure log |
| `tools/query_builder.py` | 89% | `80–85, 104–119` **PostgreSQL introspection**; `466–467` guardrail-blocked builder early-return (+ scattered join/aggregate 400 branches) |
| `guardrails/sql_guardrail.py` | 89% | `75, 82–83, 90` empty/unparseable/no-statement; `130–133, 149` allow-listed-check on `DELETE`/`UPDATE` *with* `WHERE` |
| `tools/base.py` | 75% | `12` `ToolResult.to_dict`; `59` registry `TypeError`; `95–113` **exception-retry path** (`tool.execute` raising — only `failed`-status retries tested) |
| `tools/calculator.py` | 72% | `25, 31, 33–39` operator/node edge branches; `68, 75–76` missing-expression / eval-error returns |
| `tools/web_search.py` | **40%** | `49–72` — nearly all of `execute()`; only schema/init covered. Consistent with §3: the mock is barely tested because it barely matters. |
| **TOTAL** | **90%** | 1215 stmts, 126 missed |

## 2. Disabled-by-default guardrails (config audit)

| Env var | Default | Effect at default | Verdict |
|---|---|---|---|
| `SESSION_COST_BUDGET_USD` | `0` | cost guard **DISABLED** (`budget.py` only checks when `> 0`) | **DANGEROUS — the one real fail-open default** |
| `SESSION_MAX_TOKENS` | `8192` | token halt ON | safe; but `.env.example` comments "`0` = unlimited" while the code (`total > 0`) would halt *every* call at `0` — doc/code mismatch, fix comment |
| `CHAT_RATE_PER_MIN` / `SSE_MAX_PER_IP` | `30` / `3` | rate limits ON (`0` disables) | safe defaults; dangerous only if operator sets `0` |
| `SQL_QUERY_CACHE_SIZE` | `128` | SELECT cache ON (`0` disables) | safe default |
| `RISKY_ROW_THRESHOLD` | `5` | bulk-write gate ON | safe |
| `MAX_RETRIES` / `BACKOFF_BASE_SECONDS` / `WORKER_MAX_ITERATIONS` | `3` / `1.0` / `5` | bounded retries/loop | safe |
| LLM keys (`ANTHROPIC_API_KEY`, `LLM_API_KEY`) | empty | clients return `None` → webapp `400`, CLI exits `1` | safe fail-closed |
| `ALLOWED_TABLES` | hardcoded set | not env-overridable | safe |
| `DATABASE_URL` | `sqlite://` | local SQLite | safe default |
| `REDIS_URL` | `localhost:6379/0` | graceful fallback when unreachable | safe, but fallback means Redis path is *silently* untested (§1 memory.py) |
| `SESSION_IDLE_MINUTES` | `15` | idle-window active stat | safe |
| `LOG_LEVEL` | `INFO` | single stream handler | safe, JSON logging is Phase 6 work |

## 3. Every stub / mock (grep-verified)

| Item | Location | Type | Status |
|---|---|---|---|
| `WebSearchTool._MOCK_RESULTS` — canned Python/FastAPI results, *"Replace with a real search API for production use"* | `tools/web_search.py:4,22–23` | **PROD STUB** | open → Phase 5 |
| `FakeLLMClient` — scripted responses, intercepts router + distillation prompts | `agent/llm_client.py:117` | test double, never on prod path | fine as-is |
| `_StubClient` / `_stub_client` | `tests/test_supervisor_routing.py:12`, `tests/test_openai_compat_client.py:51` | test-only | fine as-is |
| `# Test seam` comment (`client` injection) | `agent/llm_client.py:336` | test hook in prod signature | fine, documented |
| `TODO` / `FIXME` / `XXX` / `HACK` in prod code | — | **zero matches** | clean |
| Mock/stub banners in UI (`ui/templates/*.html`) | — | **none found** (only input `placeholder=` text) | gap → Phase 5 must add banner if stub stays |

## 4. Backlog 01–11: closed vs actually open

Verified against `.scratch/netsentry-vnext/issues/` ticket files + `git`:

| Ticket | File status | Ground truth |
|---|---|---|
| 01 live smoke | done, live-verified 4/4 | **CLOSED** |
| 02 publish repo + CI | `ready-for-agent`; remote `origin` exists in git config **but local is 3 commits ahead** (`git rev-list origin/main..HEAD` = 3) and README badge is still `OWNER/REPO` placeholder | **OPEN** (push + green CI run + badge unconfirmed) |
| 03 redis in CI | `ready-for-agent`, blocked by 02 | **OPEN** — corroborated: `memory.py` Redis lines uncovered (§1) |
| 04 CSRF | done (v1.6.2) | **CLOSED** |
| 05 fact mgmt | done (v1.6.2) | **CLOSED** |
| 06 aggregates | done (v1.6.3) | **CLOSED** |
| 07 FK joins | done (v1.6.3) | **CLOSED** |
| 08 builder audit | done (v1.6.2) | **CLOSED** |
| 09 session lifecycle | done (v1.6.3) | **CLOSED** |
| 10 router spike | `ready-for-agent` | **OPEN** — first-token fallback-to-SQL still in `supervisor.py:31–35` |
| 11 provider layer | done (v1.6.4) | **CLOSED** |

**Open: 02, 03, 10. Closed: 01, 04–09, 11.** (`docs/todos.md` "Next Up" already says this — confirmed accurate, no correction needed.)

## 5. Fresh findings (caught while measuring — feed into phases)

1. **Audit P1 confirmed live**: `Orchestrator` builds `_system_prompt` (+ LTM facts) in `start_session`/`load_session`, but `run()` calls `supervisor.run(task, context, …)` without it, and workers use hardcoded `"You are a specialized worker…"` prompts (`workers.py:33–36`). **System prompt + LTM facts never reach the LLM.** Coverage corroborates: orchestrator lines 64/102 (facts join) never execute. → Fix belongs in Phase 1 (auth touches session/facts plumbing) or Phase 4 (router rework); do not silently fix — it's a behavior change needing an explicit decision + test.
2. **Dirty worktree at measurement time**: `M agent/memory.py, agent/orchestrator.py, agent/trace.py, approval/gate.py` + untracked `app_util.py` (which the whole app imports) and `.commandcode/`. Commit or stash before Phase 1 so blame stays clean. Untracked `app_util.py` is the riskiest — a fresh clone without it may not run.
3. **Out-of-scope modules unmeasured**: `approval/gate.py`, `db/*`, `webapp.py`, `webapp_ratelimit.py` were outside `--cov`. Phase 6 (or a Phase-0 follow-up) should re-run with `--cov=approval --cov=db --cov-report` including `webapp` for a true whole-repo number. The per-IP limiter and `WebApprovalHandler` poll loop are exactly the code Phase 3 rewrites — measure them before rewriting.
4. **Cheapest high-value tests** (from §1 gaps): `ToolResult.to_dict`/registry-`TypeError` (base), calculator error returns, guardrail empty/unparseable + WHERE-ful allow-list blocks, builder blocked-path audit row (`query_builder.py:466–467`). Each is a <10-line test closing a real hole.

## Phase 0.5 resolutions (2026-09-04)

Decisions from review — all implemented except where noted:

1. `SESSION_COST_BUDGET_USD` default `0` → `0.50` (`config.py`, `.env.example`). Fail-open default closed; free-tier cost estimates stay `$0` so demos are unaffected, paid keys get a guardrail. README already showed `0.50` — code now matches docs.
2. `SESSION_MAX_TOKENS=0` now means unlimited (one-line `> 0` guard in `agent/budget.py`). Code now matches the `.env.example` comment and the cost-budget/rate-limiter `0`-means-off convention. Existing token-budget test pins the live config value (8192) — unaffected. Verified: ruff clean + 31 targeted tests green (`test_supervisor_and_budget`, `test_openai_compat_client`, `test_smoke`).
3. Mock web-search banner added to the `chat.html` notice line ("Research answers currently use a built-in demo search stub, not live web results"). UI no longer presents stub research silently.
4. Finding 5 (dead system prompt / LTM facts never reach the LLM) → **Phase 1 with auth**. Decided, not yet fixed.
5. Worktree: P3 `_now`/`_uuid` consolidation committed (`app_util.py` added + 4 files); `.commandcode/` gitignored (harness config, not project source); `coverage_run.txt` (measurement temp file) deleted.

## Phase 1 — Auth + multi-tenancy + memory fix (2026-09-04)

Shipped, full suite green: **215 passed (196 + 19 new), 8 PG-skipped**; ruff + pip check clean.

1. `app_users` table (email UNIQUE, bcrypt hash, `user`/`admin` role) + `app_builder_runs.user_id` (nullable, auto-migrated like `last_active_at`; old rows stay NULL).
2. `auth.py`: stateless HMAC cookies (`user_id:expiry:sig`, 7-day TTL), `require_user` (401) / `get_optional_user` (pages → `/login`), `owns_session`, 404-not-403 cross-user convention.
3. Every stateful endpoint login-gated; `ChatRequest.user_id` now ignored (was client-trusted — the core hole); sessions/messages/traces/approvals scoped via session-JOIN ownership; rate limits keyed per-user (IP fallback pre-auth); `/health` stays open.
4. Finding 5 fixed: orchestrator `_system_prompt` threads through supervisor into both workers; regression test proves an LTM fact from session 1 reaches session 2's prompt.
5. `tests/test_authz.py`: 19 tests (register×4, login/logout, anon redirects, tamper/expiry 401s, 7-table isolation incl. builder attribution + admin cross-user demo, per-user throttle, fact persistence).
6. Deviation from plan, with reason: **bcrypt used directly, passlib dropped** — passlib 1.7.4's handler raises against bcrypt>=4.1 (verified live); same primitive, one less trap dependency.
7. Known follow-ups (not fixed): login/register POSTs have no CSRF (login-CSRF is marginal, queued); `/api/stats` counters stay global aggregates by design (row data scoped); test suite still resets the real dev DB (pre-existing pattern).

## Phase 2 — Guardrail depth (2026-09-04)

Shipped, full suite green: **266 passed (215 + 51 new), 8 PG-skipped**; ruff + pip check clean.

1. **COLUMN_POLICY** (`config.py`, empty default — demo schema has no truly sensitive column; enforcement machinery, not theater): any reference to a denied column (SELECT/WHERE/JOIN/ORDER/aggregates/INSERT targets/UPDATE targets, incl. `SELECT *` expansion against live schema, alias resolution, fail-closed on unknown schema) → BLOCKED. Decisions: BLOCK (not redact), framework-only (no schema change).
2. **INSERT volume gate** mirrors the bulk model: >RISKY_ROW_THRESHOLD rows → approval; INSERT..SELECT (unknown count) → approval (fail-closed); explicit lists checked per-column; no-list whole-row writes BLOCK when the table denies anything.
3. **Anomaly logging** (log-only, never blocks): `query_shape_anomaly` trace event when >2 tables, >8 columns, or star present.
4. **NER second pass** (spaCy en_core_web_sm 3.8.0, pinned; missing model fails safe to regex layer). Measured on seed:
   PERSON 10/10 names (FP 2/10 products: Wireless Mouse, Desk Lamp) · GPE 8/8 cities, 0 elsewhere · ORG 0 signal · obfuscation variants (caps/spacing/initials/hyphen) 7/7 masked · known slip: isolated uncommon name w/o context ("Henrietta Lacks") missed — regex + column policy are the backstops.
   Default OFF (`PII_NER_ENABLED=0`) with measured reason: on this schema PERSON+GPE would mask legitimate answers; wired into sql_tool/builder/memory-facts behind the flag.
5. **Tests**: `test_column_policy.py` + `test_pii_ner.py` + `test_redteam_phase2.py` (20 self-written bypass shapes, all blocked; benign neighbors allowed). Red-team caught a real gap pre-merge: explicit INSERT column lists (Schema identifiers, not Column nodes) bypassed the check — fixed.
6. **OPEN, needs a human**: 5 externally-written adversarial prompts (prove independence). Hand the repo to someone who has not read `sql_guardrail.py`; record their prompts + who/when here.
