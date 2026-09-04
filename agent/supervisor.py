from agent.llm_client import ClaudeLLMClient
from agent.workers import create_sql_worker, create_research_worker

class SupervisorAgent:
    def __init__(self, approval_handler=None, llm_client=None):
        self.llm = llm_client or ClaudeLLMClient()
        self.sql_worker = create_sql_worker(approval_handler=approval_handler, llm_client=self.llm)
        self.research_worker = create_research_worker(llm_client=self.llm)

    def route(self, task: str) -> str:
        """Route the task to the appropriate worker based on intent.

        The routing model is asked to reply with a single token. Matching is
        done on the first token, not a substring, so a reply like
        "RESEARCH (not SQL)" cannot be flipped to the SQL worker by the
        parenthetical. Unusable replies (empty text, or no text block at
        all) default to the SQL worker: it is the more capable path, every
        query it issues is guardrailed, and its only real loss is that
        research questions are answered without web search.
        """
        # Simple routing via LLM
        prompt = f"""You are a routing agent. You must decide if the user's task requires accessing the SQL database or if it is a general research/calculation task.
Reply ONLY with 'SQL' or 'RESEARCH'. Do not explain.

Task: {task}
"""
        response = self.llm.call(
            system="You are a router. Reply only with 'SQL' or 'RESEARCH'.",
            messages=[{"role": "user", "content": prompt}]
        )
        reply = (response.text or "").strip().upper()
        first_token = reply.split()[0] if reply.split() else ""
        if first_token.startswith("RESEARCH"):
            return "ResearchWorker"
        return "SQLWorker"

    def run(self, task: str, context: str = "", session_id=None, trace=None, system_prompt="") -> str:
        worker_name = self.route(task)
        if trace:
            trace.log("supervisor_route", {"routed_to": worker_name, "task": task})
            
        if worker_name == "SQLWorker":
            return self.sql_worker.run(task, context=context, session_id=session_id, trace=trace, system_prompt=system_prompt)
        else:
            return self.research_worker.run(task, context=context, session_id=session_id, trace=trace, system_prompt=system_prompt)

    def resume(self, worker_name, messages, tool_use_id, output, is_error, session_id=None, trace=None, system_prompt="") -> str:
        """Re-enter the paused worker's loop after its tool resolved."""
        worker = self.sql_worker if worker_name == "SQLWorker" else self.research_worker
        if trace:
            trace.log("supervisor_resume", {"resumed_worker": worker.name})
        return worker.resume(messages, tool_use_id, output, is_error, session_id=session_id, trace=trace, system_prompt=system_prompt)
