from datetime import datetime
import sys

import pytest

sys.path.insert(0, ".")

from agent.llm_client import FakeLLMClient
from agent.memory import _now as memory_now
from agent.orchestrator import Orchestrator, _now as orchestrator_now
from agent.trace import _now as trace_now
from approval.gate import AutoApproveHandler, _now as approval_now
from db.database import reset_db
from db.seed import seed_demo_data


@pytest.fixture(autouse=True)
def fresh_db():
    reset_db()
    seed_demo_data()


@pytest.mark.parametrize(
    "now_func",
    [orchestrator_now, memory_now, trace_now, approval_now],
)
def test_timestamp_helpers_return_parseable_utc_iso8601(now_func):
    timestamp = now_func()
    parsed = datetime.fromisoformat(timestamp)

    assert parsed.tzinfo is not None


def test_orchestrator_smoke_flow_runs_to_completion():
    fake_llm = FakeLLMClient([
        FakeLLMClient.tool_use_response("calculator", {"expression": "15 * 37"}, "toolu_1"),
        FakeLLMClient.text_response("15 * 37 = 555. Is there anything else?"),
    ])

    orchestrator = Orchestrator(
        llm_client=fake_llm,
        approval_handler=AutoApproveHandler(),
        user_id="test_user",
    )

    result = orchestrator.run("What is 15 times 37?")
    trace_events = orchestrator.get_trace()

    assert result == "15 * 37 = 555. Is there anything else?"
    assert any(event["event_type"] == "final_answer" for event in trace_events)
    assert any(event["event_type"] == "session_end" for event in trace_events)