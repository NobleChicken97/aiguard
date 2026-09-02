"""Tests for the live-API smoke harness (ticket 01, v1.6.5).

The harness logic runs against a scripted fake client so these tests need no
API key; the real-key run is the harness's own job (python -m scripts.live_api_smoke).
"""

import sys

import pytest

sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient, LLMResponse, ContentBlock
from db.database import reset_db
from db.seed import seed_demo_data
from scripts import live_api_smoke


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


class RoutingFakeLLMClient(FakeLLMClient):
    """FakeLLMClient whose router decision is computed per task."""

    def __init__(self, responses, route_fn):
        super().__init__(responses)
        self._route_fn = route_fn

    def call(self, system, messages, tools=None):
        if "router" in system.lower():
            task = messages[-1]["content"] if messages else ""
            decision = self._route_fn(task)
            return LLMResponse(
                stop_reason="end_turn",
                content=[ContentBlock(type="text", text=decision)],
                input_tokens=10,
                output_tokens=10,
            )
        return super().call(system, messages, tools)


def _research_router(task):
    # The routing prompt embeds the task after a "Task:" marker; the fixed
    # prompt text itself mentions "research", so match on the task body only.
    body = task.split("Task:")[-1].lower()
    return "RESEARCH" if "research" in body else "SQL"


def _client_factory(scripts_by_name):
    def factory(scenario):
        return RoutingFakeLLMClient(
            scripts_by_name[scenario.name], route_fn=_research_router
        )

    return factory


def _happy_path_scripts():
    return {
        "routing-only": [
            FakeLLMClient.text_response("Hello! I can help with database and research questions."),
        ],
        "read-only-sql": [
            FakeLLMClient.tool_use_response(
                "sql_tool",
                {"sql": "SELECT COUNT(*) AS n FROM customers"},
                "toolu_smoke_sql",
            ),
            FakeLLMClient.text_response("There are 5 customers."),
        ],
        "destructive-blocked": [
            FakeLLMClient.tool_use_response(
                "sql_tool",
                {"sql": "DROP TABLE customers"},
                "toolu_smoke_drop",
            ),
            FakeLLMClient.text_response("I could not do that — the request was blocked."),
        ],
        "research": [
            FakeLLMClient.text_response("Python was created by Guido van Rossum."),
        ],
    }


class TestSmokeSuite:
    def test_all_scenarios_pass_on_well_behaved_agent(self):
        results = live_api_smoke.run_smoke_suite(_client_factory(_happy_path_scripts()))

        assert [r.name for r in results] == [s.name for s in live_api_smoke.SCENARIOS]
        for r in results:
            assert r.passed, f"{r.name}: {r.detail}"
        assert live_api_smoke.overall_exit_code(results) == 0

    def test_destructive_attempt_is_blocked_at_guardrail(self):
        results = live_api_smoke.run_smoke_suite(_client_factory(_happy_path_scripts()))
        destructive = next(r for r in results if r.name == "destructive-blocked")

        assert destructive.passed
        assert "blocked" in destructive.detail.lower()

    def test_model_refusal_counts_as_pass(self):
        # Real models may decline to issue destructive SQL instead of emitting
        # it for the guardrail — the invariant is "never reaches the database".
        scripts = _happy_path_scripts()
        scripts["destructive-blocked"] = [
            FakeLLMClient.text_response("I won't drop the customers table."),
        ]

        results = live_api_smoke.run_smoke_suite(_client_factory(scripts))
        destructive = next(r for r in results if r.name == "destructive-blocked")

        assert destructive.passed, destructive.detail
        assert "never reached the database" in destructive.detail

    def test_successful_sql_on_destructive_prompt_fails(self):
        scripts = _happy_path_scripts()
        scripts["destructive-blocked"] = [
            FakeLLMClient.tool_use_response(
                "sql_tool",
                {"sql": "SELECT COUNT(*) AS n FROM customers"},
                "toolu_smoke_sneaky",
            ),
            FakeLLMClient.text_response("There are 5 customers."),
        ]

        results = live_api_smoke.run_smoke_suite(_client_factory(scripts))
        destructive = next(r for r in results if r.name == "destructive-blocked")

        assert destructive.passed is False
        assert "succeeded" in destructive.detail

    def test_wrong_routing_fails_the_research_scenario(self):
        scripts = _happy_path_scripts()

        def always_sql(task):
            return "SQL"

        def factory(scenario):
            return RoutingFakeLLMClient(scripts[scenario.name], route_fn=always_sql)

        results = live_api_smoke.run_smoke_suite(factory)
        by_name = {r.name: r for r in results}

        assert by_name["research"].passed is False
        assert "routed to SQLWorker" in by_name["research"].detail
        assert live_api_smoke.overall_exit_code(results) == 1

    def test_missing_sql_call_fails_the_readonly_scenario(self):
        scripts = _happy_path_scripts()
        scripts["read-only-sql"] = [
            FakeLLMClient.text_response("There are 5 customers (from memory)."),
        ]

        results = live_api_smoke.run_smoke_suite(_client_factory(scripts))
        readonly = next(r for r in results if r.name == "read-only-sql")

        assert readonly.passed is False
        assert "never called" in readonly.detail

    def test_scenario_exception_is_contained_and_fails(self):
        def broken_factory(scenario):
            raise RuntimeError("simulated provider outage")

        results = live_api_smoke.run_smoke_suite(broken_factory)

        assert len(results) == len(live_api_smoke.SCENARIOS)
        for r in results:
            assert r.passed is False
            assert "RuntimeError" in r.detail
        assert live_api_smoke.overall_exit_code(results) == 1


class TestMainEntryPoint:
    def test_skips_cleanly_without_a_key(self, monkeypatch, capsys):
        monkeypatch.setattr(live_api_smoke, "build_llm_client", lambda: None)

        exit_code = live_api_smoke.main()

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "SKIPPED" in out
        assert "LLM_API_KEY" in out

    def test_reports_pass_with_configured_client(self, monkeypatch, capsys):
        # main() reuses one client across scenarios; script all worker turns in
        # sequence (router + distillation prompts are intercepted by the fake).
        client = RoutingFakeLLMClient(
            [
                FakeLLMClient.text_response("Hello! I can help with database and research questions."),
                FakeLLMClient.tool_use_response(
                    "sql_tool", {"sql": "SELECT COUNT(*) AS n FROM customers"}, "t1"
                ),
                FakeLLMClient.text_response("There are 5 customers."),
                FakeLLMClient.tool_use_response(
                    "sql_tool", {"sql": "DROP TABLE customers"}, "t2"
                ),
                FakeLLMClient.text_response("Blocked, thankfully."),
                FakeLLMClient.text_response("Python was created by Guido van Rossum."),
            ],
            route_fn=_research_router,
        )
        monkeypatch.setattr(live_api_smoke, "build_llm_client", lambda: client)

        exit_code = live_api_smoke.main()

        assert exit_code == 0
        out = capsys.readouterr().out
        assert "4/4 scenarios passed" in out
