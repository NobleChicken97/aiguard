# 10: Supervisor routing — spike and decision

**What to build:** resolve the grill-zone question about the LLM router, which costs one extra model call per message. Spike and compare two alternatives — a deterministic keyword/intent prefilter with LLM fallback, and collapsing the supervisor into a single worker that owns all guardrailed tools — then implement whichever wins with a short design-doc rationale. The safety invariant is unchanged either way: the guardrail, not the router, is the enforcement point.

**Blocked by:** None (can start immediately).

**Status:** done (Phase 4 follow-up) — spike measured, LLM router kept; rationale recorded as trade-off 10 in `docs/design.md`

- [x] Spike measures both alternatives (latency, routing accuracy on a fixed prompt set, cost per message)
- [x] Decision + rationale recorded in the design doc's trade-offs section
- [x] Chosen approach implemented with routing tests updated (fake router behavior preserved for tests)
- [x] Adversarial and budget suites stay green after the change
