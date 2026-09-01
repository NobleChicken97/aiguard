"""Session-level budget enforcement for all LLM call paths.

The multi-agent refactor (SupervisorAgent -> WorkerBase) moved LLM calls out
of ``Orchestrator._call_llm``, which silently bypassed the documented cost
and token budgets (``SESSION_COST_BUDGET_USD`` / ``SESSION_MAX_TOKENS``).
Wrapping every LLM client handed to the supervisor/workers with
``BudgetGuardedLLMClient`` restores that guarantee regardless of which agent
issues the call.
"""

import config


class BudgetExceededError(RuntimeError):
    """Raised when a session exceeds its configured cost or token budget."""


def estimate_cost_usd(input_tokens, output_tokens):
    """Sonnet-class rate card: $3 / M input tokens, $15 / M output tokens.

    Shared by ``BudgetGuardedLLMClient`` and ``Orchestrator._estimate_cost``
    so both paths can never drift apart.
    """
    return (input_tokens * 3.0 + output_tokens * 15.0) / 1_000_000


class BudgetGuardedLLMClient:
    """Transparent wrapper around an LLM client enforcing session budgets.

    Accumulates real usage from every response and raises
    ``BudgetExceededError`` as soon as a configured limit is crossed.
    """

    def __init__(self, inner):
        self._inner = inner
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @property
    def total_tokens(self):
        return self.total_input_tokens + self.total_output_tokens

    def call(self, system, messages, tools=None):
        response = self._inner.call(system, messages, tools)

        self.total_input_tokens += getattr(response, "input_tokens", 0) or 0
        self.total_output_tokens += getattr(response, "output_tokens", 0) or 0

        if config.SESSION_COST_BUDGET_USD > 0:
            cost = estimate_cost_usd(self.total_input_tokens, self.total_output_tokens)
            if cost > config.SESSION_COST_BUDGET_USD:
                raise BudgetExceededError(
                    f"Session cost budget exceeded: ${cost:.4f} > ${config.SESSION_COST_BUDGET_USD:.2f}"
                )

        if self.total_tokens > config.SESSION_MAX_TOKENS:
            raise BudgetExceededError(
                f"Session token budget exceeded: {self.total_tokens} > {config.SESSION_MAX_TOKENS}"
            )

        return response
