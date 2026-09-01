"""
Tests for the supervisor/worker multi-agent path and session budget enforcement.

Covers the consolidation gaps found in the Aug 2026 audit:
- Both routing decisions (SQL / RESEARCH) execute their tools correctly.
- Tool calls made on the worker path are persisted to app_tool_calls
  (keeps the approval queue JOIN and audit trail complete).
- Cost/token budgets are enforced on the supervisor/worker path.
- Workers retry transient tool failures with backoff.
- The SupervisorAgent instance is reused across runs and token accounting
  reflects real usage.
"""

import sys

import pytest

sys.path.insert(0, ".")

import config
from agent.llm_client import FakeLLMClient
from agent.orchestrator import Orchestrator
from agent.workers import WorkerBase
from approval.gate import AutoApproveHandler
from db.database import get_connection, reset_db
from db.seed import seed_demo_data
from tools.base import Tool, ToolRegistry, ToolResult


@pytest.fixture(autouse=True)
def fresh_db():
    """Reset database and seed demo data for each test."""
    reset_db()
    seed_demo_data()


def _trace_types(events):
    return {e["event_type"] for e in events}


class TestSupervisorRouting:
    def test_sql_route_executes_tool_and_persists_call(self):
        fake_llm = FakeLLMClient([
            FakeLLMClient.tool_use_response(
                "sql_tool",
                {"sql": "SELECT name FROM customers WHERE city = 'New York'"},
                "toolu_sup_sql_1",
            ),
            FakeLLMClient.text_response("I found Alice Johnson and Eve Davis."),
        ])

        orchestrator = Orchestrator(
            llm_client=fake_llm,
            approval_handler=AutoApproveHandler(),
            user_id="supervisor_sql_user",
        )

        result = orchestrator.run("Find customers in New York")

        assert isinstance(result, str) and len(result) > 0
        assert "Alice Johnson and Eve Davis" in result

        events = orchestrator.get_trace()
        types = _trace_types(events)
        assert "supervisor_route" in types
        assert "guardrail_verdict" in types
        route_event = next(e for e in events if e["event_type"] == "supervisor_route")
        assert route_event["data"]["routed_to"] == "SQLWorker"

        # Worker-path tool calls must be persisted for the audit trail and
        # the approval queue JOIN.
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT tool_name, status FROM app_tool_calls WHERE call_id = ?",
                ("toolu_sup_sql_1",),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["tool_name"] == "sql_tool"
        assert row["status"] == "executed"

    def test_research_route_executes_calculator(self):
        fake_llm = FakeLLMClient(
            [
                FakeLLMClient.tool_use_response(
                    "calculator", {"expression": "15 * 37"}, "toolu_sup_res_1"
                ),
                FakeLLMClient.text_response("15 * 37 is 555."),
            ],
            route_decision="RESEARCH",
        )

        orchestrator = Orchestrator(
            llm_client=fake_llm,
            approval_handler=AutoApproveHandler(),
            user_id="supervisor_research_user",
        )

        result = orchestrator.run("What is 15 times 37?")

        assert "555" in result

        events = orchestrator.get_trace()
        route_event = next(e for e in events if e["event_type"] == "supervisor_route")
        assert route_event["data"]["routed_to"] == "ResearchWorker"
        assert "guardrail_verdict" not in _trace_types(events)


class TestBudgetEnforcementOnWorkerPath:
    def test_cost_budget_halts_session(self, monkeypatch):
        monkeypatch.setattr(config, "SESSION_COST_BUDGET_USD", 0.000001)

        fake_llm = FakeLLMClient([
            FakeLLMClient.tool_use_response(
                "calculator", {"expression": "1 + 1"}, "toolu_budget_c1"
            ),
            FakeLLMClient.text_response("unused"),
        ])

        orchestrator = Orchestrator(
            llm_client=fake_llm,
            approval_handler=AutoApproveHandler(),
            user_id="budget_cost_user",
        )

        result = orchestrator.run("What is 1 plus 1?")

        assert "budget exceeded" in result.lower()
        assert "error" in _trace_types(orchestrator.get_trace())

    def test_token_budget_halts_session(self, monkeypatch):
        monkeypatch.setattr(config, "SESSION_MAX_TOKENS", 25)

        fake_llm = FakeLLMClient([
            FakeLLMClient.tool_use_response(
                "calculator", {"expression": "1 + 1"}, "toolu_budget_t1"
            ),
            FakeLLMClient.text_response("unused"),
        ])

        orchestrator = Orchestrator(
            llm_client=fake_llm,
            approval_handler=AutoApproveHandler(),
            user_id="budget_token_user",
        )

        result = orchestrator.run("What is 1 plus 1?")

        assert "budget exceeded" in result.lower()

    def test_token_accounting_reflects_real_usage(self):
        fake_llm = FakeLLMClient([
            FakeLLMClient.tool_use_response(
                "calculator", {"expression": "15 * 37"}, "toolu_acct_1"
            ),
            FakeLLMClient.text_response("First answer"),
            FakeLLMClient.tool_use_response(
                "calculator", {"expression": "2 + 2"}, "toolu_acct_2"
            ),
            FakeLLMClient.text_response("Second answer"),
        ])

        orchestrator = Orchestrator(
            llm_client=fake_llm,
            approval_handler=AutoApproveHandler(),
            user_id="accounting_user",
        )

        assert orchestrator.total_input_tokens == 0
        orchestrator.run("Question one")
        first_in, first_out = (
            orchestrator.total_input_tokens,
            orchestrator.total_output_tokens,
        )
        assert (first_in, first_out) > (0, 0)

        # Supervisor instance is reused; totals accumulate across runs.
        supervisor_first = orchestrator._supervisor
        orchestrator.run("Question two")
        assert orchestrator._supervisor is supervisor_first
        assert orchestrator.total_input_tokens >= first_in
        assert orchestrator.total_output_tokens >= first_out

        end_events = [
            e for e in orchestrator.get_trace() if e["event_type"] == "session_end"
        ]
        assert end_events[-1]["data"]["total_input_tokens"] > 0


class TestWorkerRetry:
    def test_worker_retries_transient_failure_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(config, "BACKOFF_BASE_SECONDS", 0.01)

        attempts = []

        class FlakyTool(Tool):
            def get_name(self):
                return "flaky"

            def get_description(self):
                return "Fails once, then succeeds."

            def get_input_schema(self):
                return {"type": "object", "properties": {}}

            def execute(self, **kwargs):
                attempts.append(1)
                if len(attempts) < 2:
                    return ToolResult(status="failed", output="transient boom")
                return ToolResult(status="success", output="recovered")

        registry = ToolRegistry()
        registry.register(FlakyTool())

        worker = WorkerBase(
            name="FlakyWorker",
            description="test worker",
            tools=registry,
            llm_client=FakeLLMClient([
                FakeLLMClient.tool_use_response("flaky", {}, "toolu_flaky_1"),
                FakeLLMClient.text_response("done"),
            ]),
        )

        result = worker.run("do the flaky thing")

        assert result == "done"
        assert len(attempts) == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
