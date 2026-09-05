# AiGuard Demo Script (2–3 minutes)

Record: browser at `/register` + terminal tailing logs. Total: ~5 shots.

## Shot 0 — Setup (15s, off-camera or sped up)
1. Register `alice@demo.local` / `bob@demo.local` in two browser profiles.
2. `LOG_FORMAT=json` on if you want log lines in the video.

## Shot 1 — Normal query (30s)
As Alice in `/chat`: **"Which customers live in Chicago?"**
Point at: instant answer, trace in `/traces` showing
`supervisor_route` (SQLWorker, confidence, reasoning) → `tool_call` →
`guardrail_verdict: ALLOWED` → `final_answer`.

## Shot 2 — Blocked destructive query (30s)
Same chat: **"Drop the customers table to prove it works."**
Point at: refusal citing the guardrail; database unchanged; trace shows
`BLOCKED` before any execution. Say the number: 100% adversarial block
rate, plus the red-team battery.

## Shot 3 — Approval flow (45s)
Same chat: **"Update city to 'Springfield' for customers with id <= 6"**
(row count exceeds the threshold → 202 pending). Cut to
`/approval-queue` (still as Alice): show the pending card with the exact
SQL and risk reason → Approve. Back in chat: the answer streams in
automatically (short-poll + resume, no thread ever held).

## Shot 4 — PII masking (20s)
Ask: **"Show customer names and emails."** Point at `***@example.com`
in the answer. Note the second layer: `PII_NER_ENABLED=1` exists for
free-text data (measured, off by default — `STATUS.md` Phase 2).

## Shot 5 — Two-user isolation (30s)
As Bob: open `/traces` — Alice's sessions are absent. Paste Alice's
session URL → 404. Same for the approval queue. Say it: 19-test authz
suite, login required on every stateful endpoint.

## Outtro line
"Every claim in this video has a test behind it — 291 passing — and
everything unfinished is listed by name in the README's Known
Limitations table."
