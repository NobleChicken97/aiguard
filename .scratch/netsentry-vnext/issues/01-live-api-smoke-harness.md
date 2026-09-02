# 01: Live-API smoke harness

**What to build:** a repeatable script (plus a docs section) that, given any configured provider key (`LLM_PROVIDER` + `LLM_API_KEY` — a free Gemini AI Studio or Groq key works, the provider layer shipped in v1.6.4), runs a fixed set of prompts end-to-end in auto-deny approval mode — one routing-only prompt, one read-only SQL prompt, one adversarial destructive prompt, one research prompt — and asserts the expected trace outcomes (route recorded, tool call recorded, destructive attempt blocked before execution). This is the guardrail's "proof with a real key" that the scripted test suite cannot provide, and it de-risks every later change to the LLM path.

**Blocked by:** None (can start immediately).

**Status:** done (v1.6.5) — **live-verified in v1.6.6: 4/4 scenarios PASS, exit 0** (2026-09-03, groq / openai/gpt-oss-120b)

- [x] Running the harness with a valid key completes all four prompts without a 400/5xx and prints a pass/fail summary *(logic verified against a scripted client; live run pending a real key — `python -m scripts.live_api_smoke`)*
- [x] The destructive prompt never reaches the database (blocked at the guardrail, visible in the trace)
- [x] Harness exits non-zero on any unexpected failure so it can run as a release check
- [x] Harness is skipped cleanly (with a clear message) when no key is configured

**Implementation (v1.6.5):** `scripts/live_api_smoke.py` — runs the four fixed prompts (routing-only, read-only SQL, destructive DROP, research) through `Orchestrator` with `AutoDenyHandler`, then asserts trace outcomes: `supervisor_route` recorded (ResearchWorker for the research prompt), `sql_tool` called + successful for the read-only prompt, a `BLOCKED` guardrail verdict / blocked tool result with *no* successful sql_tool call for the destructive prompt, and no database access on the research path. Exit 0 on pass or clean skip, 1 on any failure. Covered by `tests/test_live_smoke_harness.py` (7 tests, scripted client — no key needed).
