"""Session-level budget enforcement for all LLM call paths.

The multi-agent refactor (SupervisorAgent -> WorkerBase) moved LLM calls out
of ``Orchestrator._call_llm``, which silently bypassed the documented cost
and token budgets (``SESSION_COST_BUDGET_USD`` / ``SESSION_MAX_TOKENS``).
Wrapping every LLM client handed to the supervisor/workers with
``BudgetGuardedLLMClient`` restores that guarantee regardless of which agent
issues the call — and regardless of which provider (Claude or a free-tier
OpenAI-compatible provider) backs the client.
"""

import os

import config


class BudgetExceededError(RuntimeError):
    """Raised when a session exceeds its configured cost or token budget."""


RATE_CARDS_USD_PER_M = {
    # Sonnet-class rate card for the legacy Anthropic path.
    "anthropic": (3.0, 15.0),
    # Free-tier providers (gemini/groq/nvidia on their free plans) cost $0,
    # so the cost estimate stays $0 and SESSION_MAX_TOKENS is the binding
    # budget. Set BUDGET_RATE_CARD_USD_PER_M="in,out" (USD per million
    # tokens) to estimate against paid list prices instead of inventing
    # per-provider numbers that drift.
}


def _rate_card(provider):
    override = os.getenv("BUDGET_RATE_CARD_USD_PER_M", "")
    if override:
        in_rate, out_rate = override.split(",")
        return float(in_rate), float(out_rate)
    return RATE_CARDS_USD_PER_M.get((provider or "anthropic").lower(), (0.0, 0.0))


def estimate_cost_usd(input_tokens, output_tokens, provider="anthropic"):
    """Best-effort session cost estimate for the configured provider.

    Shared by ``BudgetGuardedLLMClient`` and ``Orchestrator._estimate_cost``
    so both paths can never drift apart. Providers without a rate card
    (i.e. free tiers) estimate to $0.
    """
    in_rate, out_rate = _rate_card(provider)
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


class BudgetGuardedLLMClient:
    """Transparent wrapper around an LLM client enforcing session budgets.

    Accumulates real usage from every response and raises
    ``BudgetExceededError`` as soon as a configured limit is crossed.
    """

    def __init__(self, inner, provider=None):
        self._inner = inner
        self._provider = (provider or getattr(config, "LLM_PROVIDER", "") or "anthropic").lower()
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def provider(self):
        return self._provider

    @property
    def total_tokens(self):
        return self.total_input_tokens + self.total_output_tokens

    def call(self, system, messages, tools=None):
        response = self._inner.call(system, messages, tools)

        self.total_input_tokens += getattr(response, "input_tokens", 0) or 0
        self.total_output_tokens += getattr(response, "output_tokens", 0) or 0

        if config.SESSION_COST_BUDGET_USD > 0:
            cost = estimate_cost_usd(
                self.total_input_tokens, self.total_output_tokens, provider=self._provider
            )
            if cost > config.SESSION_COST_BUDGET_USD:
                raise BudgetExceededError(
                    f"Session cost budget exceeded: ${cost:.4f} > ${config.SESSION_COST_BUDGET_USD:.2f}"
                )

        if self.total_tokens > config.SESSION_MAX_TOKENS:
            raise BudgetExceededError(
                f"Session token budget exceeded: {self.total_tokens} > {config.SESSION_MAX_TOKENS}"
            )

        return response
