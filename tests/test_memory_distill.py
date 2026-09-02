"""Long-term memory facts: LLM distillation + PII masking.

Session facts used to be saved by a raw-message fallback (every user
message became a "fact") even though the docs promised LLM distillation,
and nothing masked them before persistence. The orchestrator now distills
through the budget-wrapped LLM client and masks every fact.
"""

from agent.llm_client import FakeLLMClient
from agent.memory import LongTermMemory, distill_facts_from_session
from agent.orchestrator import Orchestrator
from db.database import reset_db
from db.seed import seed_demo_data


def test_fallback_without_llm_still_extracts_user_messages():
    msgs = [{"role": "user", "content": "I like pineapple on pizza."}]
    assert distill_facts_from_session(msgs) == ["I like pineapple on pizza."]


def test_llm_distillation_used_when_client_provided():
    msgs = [{"role": "user", "content": "Tell me about Berlin."}]
    fake = FakeLLMClient([], distill_facts=["Lives in Berlin", "Prefers short answers"])
    facts = distill_facts_from_session(msgs, llm_client=fake)
    assert facts == ["Lives in Berlin", "Prefers short answers"]


def test_distill_prompts_do_not_consume_scripted_responses():
    scripted = FakeLLMClient.text_response("the real answer")
    fake = FakeLLMClient([scripted])
    distill_call = fake.call(
        system="Extract 1-3 concise factual statements about the user.",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert distill_call.text == ""
    next_response = fake.call(system="normal system", messages=[{"role": "user", "content": "go"}])
    assert next_response.text == "the real answer"


def test_masked_fact_persisted_by_orchestrator_session():
    reset_db()
    seed_demo_data()
    fake = FakeLLMClient(
        [FakeLLMClient.text_response("Noted.")],
        distill_facts=["Email is jane@example.com"],
    )
    orchestrator = Orchestrator(llm_client=fake, user_id="distill_user")
    orchestrator.run("Hello there")

    facts = LongTermMemory().get_all_facts("distill_user")
    assert len(facts) == 1
    assert facts[0]["fact_text"] == "Email is ***@example.com"
