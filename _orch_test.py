import sys
sys.path.insert(0, '.')
from db.database import reset_db
from db.seed import seed_demo_data
from agent.orchestrator import Orchestrator
from agent.llm_client import FakeLLMClient
from approval.gate import AutoApproveHandler

reset_db()
seed_demo_data()

fake = FakeLLMClient([
    FakeLLMClient.tool_use_response("calculator", {"expression": "15 * 37"}, "toolu_1"),
    FakeLLMClient.text_response("15 * 37 = 555. Is there anything else?"),
])

orch = Orchestrator(
    llm_client=fake,
    approval_handler=AutoApproveHandler(),
    user_id="test_user",
)

result = orch.run("What is 15 times 37?")
print("RESULT:", result)
print("TOKENS:", orch.total_input_tokens, orch.total_output_tokens)
print("TRACE EVENTS:", len(orch.get_trace()))
for e in orch.get_trace():
    print(f"  {e['event_type']}: {e['data']}")
