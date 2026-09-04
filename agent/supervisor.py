import json as _json
import re as _re
from dataclasses import dataclass

import config
from agent.llm_client import ClaudeLLMClient
from agent.workers import create_sql_worker, create_research_worker


CLARIFY_TEXT = (
    "I wasn't sure whether that needs the database or general research. "
    "Could you clarify — for example, mention customers, orders, or products "
    "for a database question, or ask me to calculate or look something up "
    "otherwise?"
)

_JSON_OBJ_RE = _re.compile(r"\{.*\}", _re.DOTALL)


@dataclass
class RouteDecision:
    """Structured router verdict (Phase 4).

    ``worker`` is "SQLWorker" | "ResearchWorker" | "clarify".
    ``confidence`` is model-claimed (structured tiers) or assigned:
    clean legacy tokens get 1.0 (backward compatible), anything
    unparseable gets 0.0 and routes to clarification, never a guess.
    """

    worker: str
    confidence: float
    reasoning: str
    tier: str  # "structured" | "extracted" | "legacy" | "unparseable"

    @property
    def needs_clarification(self):
        return (
            self.worker == "clarify"
            or self.confidence < config.ROUTER_CONFIDENCE_THRESHOLD
        )


def _extract_json(raw):
    match = _JSON_OBJ_RE.search(raw or "")
    return match.group(0) if match else None


def _parse_route_reply(text):
    """Layered parse: strict JSON -> embedded JSON -> clean legacy token.

    Anything else (empty, chatter, "RESEARCH (not SQL)") is unparseable:
    Phase 4 asks the user instead of guessing (see STATUS.md).
    """
    raw = (text or "").strip()
    for tier, candidate in (("structured", raw), ("extracted", _extract_json(raw))):
        if not candidate:
            continue
        try:
            data = _json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict):
            continue
        route = str(data.get("route", "")).strip().upper()
        try:
            confidence = float(data.get("confidence", 0.0))
        except (TypeError, ValueError):
            continue
        reasoning = str(data.get("reasoning", "") or "")
        if route in ("SQL", "RESEARCH") and 0.0 <= confidence <= 1.0:
            worker = "SQLWorker" if route == "SQL" else "ResearchWorker"
            return RouteDecision(worker, confidence, reasoning, tier)
    legacy = raw.rstrip(".").strip().upper()
    if legacy in ("SQL", "RESEARCH"):
        worker = "SQLWorker" if legacy == "SQL" else "ResearchWorker"
        return RouteDecision(worker, 1.0, "", "legacy")
    return RouteDecision("clarify", 0.0, raw[:200], "unparseable")


class SupervisorAgent:
    def __init__(self, approval_handler=None, llm_client=None):
        self.llm = llm_client or ClaudeLLMClient()
        self.sql_worker = create_sql_worker(approval_handler=approval_handler, llm_client=self.llm)
        self.research_worker = create_research_worker(llm_client=self.llm)

    def route(self, task: str) -> RouteDecision:
        """Route the task to a worker, with claimed confidence.

        The model is asked for strict JSON; parsing degrades through
        embedded-JSON and clean-legacy-token tiers before giving up to
        clarification. Every tier is trace-logged by run(), so a misroute
        is always debuggable after the fact.
        """
        prompt = (
            "You are a routing agent. Decide if the user's task requires "
            "accessing the SQL database or general research/calculation.\n"
            'Reply with JSON only, exactly: {"route": "SQL" or "RESEARCH", '
            '"confidence": 0.0-1.0, "reasoning": "one short sentence"}.'
            f"\n\nTask: {task}\n"
        )
        response = self.llm.call(
            system=(
                "You are a router. Reply with a single JSON object and nothing "
                "else: {\"route\": \"SQL\" or \"RESEARCH\", \"confidence\": "
                "0.0-1.0, \"reasoning\": \"one short sentence\"}."
            ),
            messages=[{"role": "user", "content": prompt}]
        )
        return _parse_route_reply(response.text)

    def run(self, task: str, context: str = "", session_id=None, trace=None, system_prompt="") -> str:
        decision = self.route(task)
        if decision.needs_clarification:
            if trace:
                trace.log("supervisor_route", {
                    "routed_to": "clarify",
                    "route_requested": decision.worker,
                    "confidence": decision.confidence,
                    "reasoning": decision.reasoning,
                    "tier": decision.tier,
                    "task": task,
                })
            return CLARIFY_TEXT
        if trace:
            trace.log("supervisor_route", {
                "routed_to": decision.worker,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "tier": decision.tier,
                "task": task,
            })

        if decision.worker == "SQLWorker":
            return self.sql_worker.run(task, context=context, session_id=session_id, trace=trace, system_prompt=system_prompt)
        else:
            return self.research_worker.run(task, context=context, session_id=session_id, trace=trace, system_prompt=system_prompt)

    def resume(self, worker_name, messages, tool_use_id, output, is_error, session_id=None, trace=None, system_prompt="") -> str:
        """Re-enter the paused worker's loop after its tool resolved."""
        worker = self.sql_worker if worker_name == "SQLWorker" else self.research_worker
        if trace:
            trace.log("supervisor_resume", {"resumed_worker": worker.name})
        return worker.resume(messages, tool_use_id, output, is_error, session_id=session_id, trace=trace, system_prompt=system_prompt)
