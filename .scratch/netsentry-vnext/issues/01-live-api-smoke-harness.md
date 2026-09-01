# 01: Live-API smoke harness

**What to build:** a repeatable script (plus a docs section) that, given a real Anthropic key, runs a fixed set of prompts end-to-end in auto-deny approval mode — one routing-only prompt, one read-only SQL prompt, one adversarial destructive prompt, one research prompt — and asserts the expected trace outcomes (route recorded, tool call recorded, destructive attempt blocked before execution). This is the guardrail's "proof with a real key" that the scripted test suite cannot provide, and it de-risks every later change to the LLM path.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] Running the harness with a valid key completes all four prompts without a 400/5xx and prints a pass/fail summary
- [ ] The destructive prompt never reaches the database (blocked at the guardrail, visible in the trace)
- [ ] Harness exits non-zero on any unexpected failure so it can run as a release check
- [ ] Harness is skipped cleanly (with a clear message) when no key is configured
