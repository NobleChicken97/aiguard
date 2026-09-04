"""Agent orchestration, multi-agent routing, memory, traces, and LLM clients.

Public surface (per ``__all__``): the orchestrator, supervisor, worker
factories, LLM clients, and memory helpers. Lower-level helpers (tracing,
budgeting) live in their own modules and are imported internally.
"""

from agent.llm_client import (
    ClaudeLLMClient,
    ContentBlock,
    FakeLLMClient,
    LLMResponse,
)
from agent.memory import (
    LongTermMemory,
    ShortTermMemory,
    distill_facts_from_session,
)
from agent.orchestrator import Orchestrator
from agent.supervisor import CLARIFY_TEXT, RouteDecision, SupervisorAgent
from agent.workers import (
    WorkerBase,
    create_research_worker,
    create_sql_worker,
)

__all__ = [
    "Orchestrator",
    "SupervisorAgent",
    "RouteDecision",
    "CLARIFY_TEXT",
    "WorkerBase",
    "create_sql_worker",
    "create_research_worker",
    "ClaudeLLMClient",
    "FakeLLMClient",
    "ContentBlock",
    "LLMResponse",
    "ShortTermMemory",
    "LongTermMemory",
    "distill_facts_from_session",
]
