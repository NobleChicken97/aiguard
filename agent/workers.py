import config
from approval.gate import ApprovalPending
from tools.base import ToolRegistry, execute_with_retry
from tools.calculator import CalculatorTool
from tools.mock_web_search import MockWebSearchTool
from tools.sql_tool import SQLTool
from agent.llm_client import ClaudeLLMClient

class WorkerBase:
    """Generic worker that runs a tool-calling loop until a final answer.

    The supervisor hands a task to a concrete subclass (SQLWorker /
    ResearchWorker, both built via ``create_sql_worker`` /
    ``create_research_worker``). Each iteration calls the LLM with the
    worker's tool schema, executes any tool calls through
    ``execute_with_retry``, and feeds results back. The loop is bounded
    by ``config.WORKER_MAX_ITERATIONS`` to keep a runaway agent from
    pinning the orchestrator.
    """

    def __init__(self, name, description, tools, llm_client=None):
        self.name = name
        self.description = description
        self.tools = tools
        self.llm = llm_client or ClaudeLLMClient()

    def run(self, task: str, context: str = "", session_id=None, trace=None, system_prompt="") -> str:
        messages = [
            {"role": "user", "content": f"Context:\n{context}\n\nTask:\n{task}"}
        ]
        return self._drive(messages, session_id=session_id, trace=trace, system_prompt=system_prompt)

    def resume(self, messages, tool_use_id, output, is_error, session_id=None, trace=None, system_prompt="") -> str:
        """Continue a paused loop after its pending tool resolved (Phase 3).

        ``messages`` is the snapshot taken at pause time (it already ends
        with the assistant tool_use block); the resolved output is appended
        as the tool_result the loop was waiting for, then driving resumes.
        """
        continued = list(messages) + [{
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": output, "is_error": is_error}],
        }]
        return self._drive(continued, session_id=session_id, trace=trace, system_prompt=system_prompt)

    def _drive(self, messages, session_id=None, trace=None, system_prompt="") -> str:
        # The orchestrator's system prompt (schema + long-term memory facts)
        # prefixes the worker's own instructions so personalization actually
        # reaches the LLM — previously it was built but never sent (STATUS.md
        # Finding 5).
        worker_system = f"You are a specialized worker: {self.name}. {self.description}"
        if system_prompt:
            worker_system = f"{system_prompt}\n\n{worker_system}"

        # Limited loop for the worker (configurable; keeps runaway agents bounded)
        for _ in range(config.WORKER_MAX_ITERATIONS):
            response = self.llm.call(
                system=worker_system,
                messages=messages,
                tools=self.tools.get_schemas()
            )

            messages.append({"role": "assistant", "content": [b.to_dict() for b in response.content]})

            if response.stop_reason != "tool_use":
                return response.text

            for tool_call in response.tool_calls:
                tool = self.tools.get(tool_call.tool_name)
                if not tool:
                    messages.append({
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_call.tool_use_id, "content": "Tool not found", "is_error": True}]
                    })
                    continue

                # Execute tool with the same retry/backoff policy as the main loop
                kwargs = dict(tool_call.tool_input) if tool_call.tool_input else {}
                kwargs["_call_id"] = tool_call.tool_use_id
                kwargs["_session_id"] = session_id
                kwargs["_trace"] = trace

                if trace:
                    trace.log_tool_call(tool_call.tool_name, tool_call.tool_input, tool_call.tool_use_id)

                # Persist to app_tool_calls so the audit trail and the approval
                # queue's JOIN stay complete on the worker path too.
                from db.database import record_tool_call
                record_tool_call(session_id, tool_call.tool_use_id, tool_call.tool_name, tool_call.tool_input)

                try:
                    result = execute_with_retry(tool, kwargs, tool_call.tool_use_id, trace=trace)
                except ApprovalPending as pending:
                    # Pause, don't fail: attach the loop state so resume can
                    # continue exactly here, then unwind to release the thread.
                    pending.worker_name = self.name
                    pending.messages = list(messages)
                    raise

                if trace:
                    trace.log_tool_result(tool_call.tool_use_id, tool_call.tool_name, result.status, result.output)

                messages.append({
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": tool_call.tool_use_id, "content": result.output, "is_error": result.status in ("failed", "blocked", "denied")}]
                })

        return "Worker failed to complete task within iteration limit."

def create_sql_worker(approval_handler=None, llm_client=None):
    registry = ToolRegistry()
    registry.register(SQLTool(approval_handler=approval_handler))
    return WorkerBase(
        name="SQLWorker",
        description="You interact with the e-commerce database using sql_tool to extract information or make updates. Return clear factual answers based on the SQL data.",
        tools=registry,
        llm_client=llm_client
    )

def create_research_worker(llm_client=None):
    registry = ToolRegistry()
    registry.register(MockWebSearchTool())
    registry.register(CalculatorTool())
    return WorkerBase(
        name="ResearchWorker",
        description="You perform calculations and mock web searches (canned demo results — never claim live browsing) to answer general queries or do math. Do not mention the database.",
        tools=registry,
        llm_client=llm_client
    )
