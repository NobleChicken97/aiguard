"""Mock web-search contract (Phase 5): the stub is unmistakable everywhere.

No code path — class name, tool name, LLM-facing description, worker
instructions, system prompt, UI, README — may present canned results as
live research. These tests pin every label.
"""

import sys

sys.path.insert(0, ".")

import config
import tools
import tools.mock_web_search as web_search_module
from agent.workers import create_research_worker
from tools.mock_web_search import MockWebSearchTool


def test_class_and_tool_names_say_mock():
    assert MockWebSearchTool.__name__ == "MockWebSearchTool"
    assert MockWebSearchTool().get_name() == "mock_web_search"
    assert not hasattr(tools, "WebSearchTool")


def test_description_forbids_live_browsing_claims():
    description = MockWebSearchTool().get_description()
    assert "DEMO STUB" in description
    assert "canned" in description.lower()


def test_module_docstring_states_intentional_scope():
    doc = (web_search_module.__doc__ or "").lower()
    assert "mock" in doc
    assert "intentional" in doc


def test_canned_results_are_deterministic():
    tool = MockWebSearchTool()
    python_res = tool.execute(query="Tell me about Python")
    assert python_res.status == "success"
    assert "python.org" in python_res.output

    default_res = tool.execute(query="something entirely unrelated")
    assert default_res.status == "success"
    assert "example.com" in default_res.output

    missing = tool.execute()
    assert missing.status == "failed"


def test_research_worker_registers_mock_not_web_search():
    worker = create_research_worker()
    assert worker.tools.get("mock_web_search") is not None
    assert worker.tools.get("web_search") is None
    assert "never claim live browsing" in worker.description


def test_system_prompt_keeps_model_honest():
    assert "never claim live browsing" in config.SYSTEM_PROMPT
