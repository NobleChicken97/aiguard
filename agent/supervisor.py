import json
from agent.llm_client import ClaudeLLMClient
from agent.workers import create_sql_worker, create_research_worker

class SupervisorAgent:
    def __init__(self, approval_handler=None, llm_client=None):
        self.llm = llm_client or ClaudeLLMClient()
        self.sql_worker = create_sql_worker(approval_handler=approval_handler, llm_client=self.llm)
        self.research_worker = create_research_worker(llm_client=self.llm)

    def route(self, task: str) -> str:
        """Route the task to the appropriate worker based on intent."""
        # Simple routing via LLM
        prompt = f"""You are a routing agent. You must decide if the user's task requires accessing the SQL database or if it is a general research/calculation task.
Reply ONLY with 'SQL' or 'RESEARCH'. Do not explain.

Task: {task}
"""
        response = self.llm.call(
            system="You are a router. Reply only with 'SQL' or 'RESEARCH'.",
            messages=[{"role": "user", "content": prompt}]
        )
        route_decision = response.text.strip().upper()
        if "SQL" in route_decision:
            return "SQLWorker"
        return "ResearchWorker"

    def run(self, task: str, context: str = "", session_id=None, trace=None) -> str:
        worker_name = self.route(task)
        if trace:
            trace.log("supervisor_route", {"routed_to": worker_name, "task": task})
            
        if worker_name == "SQLWorker":
            return self.sql_worker.run(task, context=context, session_id=session_id, trace=trace)
        else:
            return self.research_worker.run(task, context=context, session_id=session_id, trace=trace)
