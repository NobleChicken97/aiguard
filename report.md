# AiGuard Status Report — Harsh-Critic Edition

*Date: 2026-09-05. Author: the owner's engineering reviewer, not its builder.
Standard of evidence: a claim is PROVEN only if a test or a measurement
names it; everything else is CLAIMED or MISSING. File references point at
`main` @ `d22af37` and later fix commits.*

> **Resolved since writing (2026-09-05, v1.7.1):** §3.1 login/register is
> now throttled (`AUTH_RATE_PER_MIN`); §2 row 3 the authz suite is now
> 23 tests incl. session-hijack; §2 row 8 the app **is deployed** at
> https://aiguard.noblechicken.me (EC2 free-tier + Caddy TLS — App Runner
> was unavailable: no service subscription) and survived a live battery +
> redeploy; §5 "must fix" login throttle is done. Audit-triggered
> production bugs also fixed: PG SELECT rendered headers as data, and
> `/api/chat` foreign-session hijack. The remaining-now: data-tenancy
> decision, retention, `Secure` cookie runbook, provider timeouts.

---

## 1. Verdict up front

This is a **genuinely good demo-grade system with production-grade habits
and demo-grade guarantees**. The safety engineering is real (AST guardrails,
fail-closed defaults, full audit trails), the test discipline is real (301
tests, CI with Postgres + Redis services, red-team suites that have actually
caught bugs), and the documentation is unusually honest. None of that makes
it production software, and several of its headline numbers measure the
wrong thing or measure it circularly. The single most important sentence in
this report: **every effectiveness number in this repo was graded by the
same hand that wrote the code.** Until the external adversarial-5 and an
independent router-eval rerun exist, treat any pre-external-5 number as self-graded history. (External-5 closed 2026-09-05: independent author, 5/5 no destructive execution.)

**Grade: A- as an engineering portfolio piece. C+ as a deployable system.
The minus and the plus are both load-bearing — read on.**

---

## 2. Claim-by-claim audit

| # | Claim | Evidence | Skeptic's note |
|---|---|---|---|
| 1 | 100% adversarial block rate | `test_prompt_adversarial.py` + `test_redteam_phase2.py` (20 bypass shapes), all green | **Self-graded.** Author wrote attacks against a guardrail whose rules they wrote. The battery is structurally good (case/quote/alias/subselect/UNION/write-paths) and it *did* catch a real bypass pre-merge (INSERT column lists) — so it's a real harness, but its ceiling is the author's imagination. External-5 closed 2026-09-05: independent author, 5/5 no destructive execution. |
| 2 | Router 97.5% live | `scripts/router_eval.py`, 40 cases, groq/gpt-oss-120b, 39/40 | **Self-graded set, uncalibrated confidences.** The one miss ("Who lives in Chicago?" → RESEARCH at high confidence) proves the confidence numbers are model-claimed, not calibrated — 0.99 confidence on a wrong answer means the 0.6 threshold is a vibes-based gate, not a measured operating point. No reliability diagram, no threshold sweep. Kept honest (miss documented, not tuned away) — credit for that. |
| 3 | Auth + multi-tenancy | 20-test `test_authz.py`, login-gated everything, 404-not-403 | **Session tenancy, not data tenancy.** Any authenticated user can `SELECT *` across all demo tables — tenants are isolated by *conversation*, not by *data*. Fine for a shared demo dataset, but don't call it multi-tenancy in front of anyone who runs SaaS. |
| 4 | Pause/resume: 202 in ~80ms, 30/30 in 1.9s | `test_pause_resume.py` (8), `scripts/load_approvals.py` | Numbers are real but the load rig is TestClient + SQLite: it proves *no thread is held*, not production throughput. Residual 1.2s/turn is SQLite write serialization, correctly attributed. No test against a real server, no slow-client behavior, no stampede past 30. |
| 5 | 303 tests green | Full suite + 5-gate CI (incl. PG + Redis services) | Count is real; coverage is not uniform (calculator 72%, `web_search` was 40% at last measurement). More importantly, ~everything state-changing runs through `FakeLLMClient` — the suite proves *plumbing*, not *intelligence*. The 4-scenario live smoke is the only end-to-end-with-a-brain check, and it's 4 prompts. |
| 6 | NER masking | Measured table (PERSON 10/10, GPE 8/8, FP documented) | Shipped **off by default** — correct call with numbers, but it means the out-of-box privacy story is regex-only, and regexes don't see names. A stranger demoing this gets name-leaking answers by default. |
| 7 | Observability | `/metrics`, JSON logs, `/health/detailed`, secrets docs | Hand-rolled exposition with zero deps (good), but aggregates are full DB scans per scrape and `app_trace_events` grows **unbounded** — no retention, no archival, no pagination on the trace API. At demo scale: fine. Past a few thousand turns: the monitoring *is* the load. In-memory latency counters reset on restart (documented). The promised Grafana screenshot never materialized — `/metrics` output stands in. |
| 8 | Deployment-ready | Dockerfile + compose + DEPLOYMENT.md + App Runner recommendation | **Never actually deployed.** No prod URL, no App Runner run, no RDS cutover ever executed outside CI's throwaway PG. The migration script is tested; the *deployment* is theory. The strongest CI gate (real uvicorn boot + `/health` probe) proves it *starts*, not that it *runs somewhere*. |

---

## 3. Structural weaknesses (ranked by how much they matter)

### 3.1 No rate limiting on login/register — the top unfixed security hole
`RL_STATE` guards `/api/chat` and `/api/stream`. `/login` and `/register`
have **no throttle at all**: unlimited password guessing against any account
and unlimited account creation. bcrypt slows each guess (~250ms here) but
that only linearly delays an attacker, and registration spam fills
`app_users` forever. CSRF was added to these forms (good), but brute force
was never gated. For a system whose entire isolation story is "login
required everywhere," the login endpoint is its least defended surface.
**Fix cost: ~10 lines** (reuse the TokenBucket keyed by IP + a tighter
register cap). No excuse left — this should have shipped with Phase 1.

### 3.2 Data tenancy doesn't exist
See claim 3. Any authenticated user exfiltrates the full dataset within the
30/min chat budget in a handful of requests. Mitigations that exist (PII
masking on emails/phones, column policy defaulting to empty) do not change
this: names, cities, order totals, and row counts all flow freely. If the
demo dataset ever becomes even slightly real, this is incident #1. The fix
is a per-user data scope (or explicit "shared demo dataset" framing in the
product contract, not just the docs).

### 3.3 Confidence is uncalibrated theater (until measured)
The 0.6 threshold was picked, not derived. One data point (Chicago at 0.99,
wrong) already falsifies "confidence ≈ correctness." What's missing: a
threshold sweep on the 40-case set (accuracy vs deferral curve), which takes
one scripted run per threshold value and zero model calls if replayed from
logged decisions. The infrastructure for this exists (`router_eval.py` +
traced confidences); nobody ran the sweep.

### 3.4 Unbounded growth with no retention story
`app_trace_events`, `app_tool_calls`, `app_messages`, `app_pending_resumes`
(resolved-but-unresumed), `app_builder_runs` — nothing is ever deleted
except the narrow resume janitor. Consequences compound: `/metrics` and
`/dashboard` scans slow linearly, the trace API returns unbounded payloads
per session, SQLite files grow until the disk incident of Phase 5 repeats
*in production*. A retention policy (e.g., roll trace events past N days to
cold storage, cap per-session replay) is a design doc away; its absence is
the difference between "ran fine in demo" and "runs fine in month six."

### 3.5 Single-process assumptions end to end
Rate limiters, latency counters, the Redis verdict cache, the approval flow
itself (resume must hit state the pausing instance wrote — fine on one box
with a shared DB, but SSE streams, in-memory buckets, and per-process
secrets don't follow to a second instance). The docs admit this every time
(good), but it means horizontal scaling is a rewrite of four subsystems,
not a dial. The deployment guide should say "scale UP only" explicitly.

### 3.6 Cookies and sessions: demo-grade edges
`SESSION_COOKIE` sets `HttpOnly` + `SameSite=Lax` but not `Secure` (correct
for localhost, wrong the day TLS terminates at the app instead of the LB —
flag it in the secrets doc). 7-day TTL with no sliding refresh and no
server-side revocation list: a stolen cookie is a 7-day pass, and rotating
`SESSION_SECRET` (the documented response) logs out *everyone*, which is a
fine incident response only if someone writes the runbook. No password
reset flow (acceptable — say so).

### 3.7 Untested failure modes
Covered: Redis down, PG absent (skip), LLM errors (scripted exception test),
approval denied/timeout-legacy. **Not covered:** Postgres *dying mid-run*
(pool exhaustion behavior?), LLM *slowness* (no timeout assertions on the
provider path — a hung provider holds a threadpool thread exactly the way
Phase 3 stopped approvals from doing), clock skew (expiry comparisons),
concurrent double-decide on one approval (actually safe — `WHERE decision
IS NULL` — credit), resume-after-deploy (in-flight resume rows across a
restart: messages JSON survives, budget restarts — untested combination).

### 3.8 Test-hygiene debts that will bite a contributor
- The suite **resets the real dev database** (`reset_db()` on
  `config.DB_PATH`, no isolation). Every full run wipes local demo state.
  Worked so far by luck + reseeding; first external contributor loses data.
- `FakeLLMClient` ubiquity means most "integration" tests never touch the
  behaviors that break in production (wire format, latency, provider
  quirks). The 4-scenario live smoke is thin coverage for the only thing
  users actually experience (a real model).
- Prompt text is load-bearing in several tests (router prompt wording,
  worker instructions). A well-meaning prompt tweak can silently shift
  behavior with no metric tripwire except the manual eval rerun — which
  nothing schedules. Wire `router_eval` into CI as a *manual+scheduled*
  job (weekly cron with the secret) and this goes away.

### 3.9 Small, specific, fixable nits (batch these)
- `ChatRequest.user_id` accepted-but-ignored: a lying field. Either remove
  it (breaking, version it) or keep with the comment (current) — but API
  consumers *will* misread it. Deprecation header or removal, pick one.
- `approval_timeout` was removed from the API with no changelog line
  outside a commit message. Keep a `docs/CHANGELOG`-style record if the API
  has any consumers at all.
- The `MockWebSearchTool` filename still says `web_search.py`. Deliberate
  (recorded), still slightly dishonest at a glance. Revisit if touching the
  file anyway.
- No request-id correlation between logs, traces, and metrics. One
  `X-Request-ID` middleware away from much faster incident debugging.
- `LOG_FORMAT=json` exists but nothing redacts secrets from log lines;
  the secrets doc warns "never log a config object" — a warning is not a
  guardrail (this codebase of all codebases should enforce that mechanically
  for known secret values).

---

## 4. What's genuinely excellent (the critic's credit ledger)

- **Fail-closed discipline is cultural, not incidental.** Unknown schema +
  denied columns → block. Unestimatable row counts → approval. Missing model
  → regex layer stands. Garbage router output → clarification question.
  Every one of these had a fail-open alternative and the code took the
  closed one, with tests pinning each.
- **The red-team loop actually works.** The INSERT-column-list bypass was
  caught by the project's own adversarial battery pre-merge — a process
  success, not just a test success.
- **Measurement before touching, every phase.** Coverage before hardening,
  profiling before the Redis verdict cache (2–4s stalls found by cProfile,
  not code review), spike numbers before the router verdict, load numbers
  before/after the poll kill. This is senior behavior throughout.
- **Traceability as a first-class feature.** Every safety decision is
  replayable from the DB. The approval queue shows exact SQL + risk reason.
  The router logs confidence + reasoning + tier. Debugging stories write
  themselves.
- **CI is better than most startups'.** Five specific gates, real Postgres
  + Redis services so "skipped" can't hide rot, a real uvicorn boot probe,
  dispatch-only live gates. The two CI-caught regressions (PG TRUNCATE
  lists, harness DB init) prove the pipeline earns its keep.
- **Docs admit weakness in writing.** The limitations table, the kept miss
  at 97.5%, the self-grading provenance headers — this is what makes the
  rest of the claims believable.

---

## 5. Production-readiness gap list (what stands between here and prod)

**Must fix:** login/register rate limiting · data-tenancy decision (scope or contract) · retention/archival policy · `Secure` cookie + session runbook · provider timeout assertions.
**Should fix:** threshold sweep for 0.6 · scheduled router-eval CI job · request-ID correlation · secret-value log redaction · test-DB isolation · `user_id` field removal.
**Needs a human:** demo video recording (external-5 closed 2026-09-05; deployment is live).

---

## 6. Bottom line

Ship the demo video with a clear conscience — the system does what the
script claims, and the README limitations table means no viewer is misled.
Do not ship this to real users or real data without §5's "must fix" list:
the guardrail layer would hold, and everything around it (login throttle,
data scope, retention, cookies) would be the incident instead. The team's
habits — measure first, fail closed, write down what's weak — are the
strongest predictor that those gaps *will* close cleanly. The code is
ready to be hardened; it is not yet hard.
