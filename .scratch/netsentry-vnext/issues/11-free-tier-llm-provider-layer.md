# 11: Free-tier LLM provider layer (OpenAI-compatible)

**What to build:** swap the Claude-only client for a provider-agnostic layer: `LLM_PROVIDER` selects `anthropic` (legacy default) or an OpenAI-compatible provider (`gemini` / `groq` / `nvidia` / `openai` presets, or `openai-compat` + explicit base URL). One adapter translates tool schemas, messages (including tool calls/results), and usage onto the existing LLMResponse contract so the supervisor/worker loops, budgets, and traces work unchanged on free-tier keys — no Anthropic key required.

**Blocked by:** None (can start immediately).

**Status:** done (v1.6.4)

- [x] `LLM_PROVIDER=gemini|groq|nvidia|openai` + `LLM_API_KEY` runs the full agent end-to-end without an Anthropic key
- [x] `openai-compat` accepts any custom base URL; unknown providers fail fast with a clear error
- [x] Tool schema + message translation covered by round-trip tests; the SQLWorker loop is proven against a stubbed OpenAI-compatible client
- [x] Budget is provider-aware: free tiers estimate $0 (token budget binds) unless `BUDGET_RATE_CARD_USD_PER_M` overrides
