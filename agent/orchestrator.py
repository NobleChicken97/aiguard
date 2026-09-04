import json

import config
from agent.memory import ShortTermMemory, LongTermMemory, distill_facts_from_session
from agent.trace import TraceLogger
from agent.llm_client import ClaudeLLMClient
from app_util import new_uuid as _uuid, now_utc as _now
from guardrails.pii_guardrail import PIIGuardrail


class Orchestrator:
    """Hand-built plan → act → observe orchestration loop.

    The loop:
      1. PLAN:   Call LLM with conversation history + tool schemas
      2. ACT:    If LLM returns tool_use, execute the tool (through guardrail if SQL)
      3. OBSERVE: Feed tool result back to LLM as context
      4. Repeat until LLM returns end_turn or max iterations reached
    """

    def __init__(
        self,
        llm_client=None,
        approval_handler=None,
        user_id="default",
    ):
        self.llm = llm_client or ClaudeLLMClient()
        # Tools live on the supervisor's workers (SQLWorker / ResearchWorker),
        # which build their own guarded registries — the orchestrator itself
        # no longer executes tools.
        self.approval_handler = approval_handler
        self.user_id = user_id
        self.session_id = None
        self.memory = ShortTermMemory()
        self.trace = None
        self.long_term = LongTermMemory()
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._system_prompt = None
        self._ltm_context = ""
        self._supervisor = None
        self._budget_client = None

    def start_session(self):
        from db.database import get_connection

        self.session_id = _uuid()
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO app_sessions (session_id, user_id, started_at, status, last_active_at)
                   VALUES (?, ?, ?, 'active', ?)""",
                (self.session_id, self.user_id, _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()

        self.trace = TraceLogger(self.session_id)
        self.memory.set_session_id(self.session_id)

        facts = self.long_term.retrieve_facts(self.user_id)
        if facts:
            self._ltm_context = "\n\nPreviously known facts about this user:\n" + "\n".join(
                f"- {f['fact_text']}" for f in facts
            )
        else:
            self._ltm_context = ""

        self._system_prompt = config.SYSTEM_PROMPT.format(
            schema=config.DEMO_SCHEMA_DESCRIPTION
        ) + self._ltm_context

        self.trace.log("session_start", {"user_id": self.user_id, "facts_loaded": len(facts)})
        return self.session_id

    def load_session(self, session_id):
        """Resume an existing session from the database.

        Loads persisted user/assistant/tool messages into short-term memory and
        re-attaches long-term memory so the agent can continue the conversation.
        """
        from db.database import get_connection

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT session_id, user_id FROM app_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Session {session_id} not found")
            self.session_id = session_id
            self.user_id = row["user_id"]
        finally:
            conn.close()

        self.trace = TraceLogger(self.session_id)

        facts = self.long_term.retrieve_facts(self.user_id)
        if facts:
            self._ltm_context = "\n\nPreviously known facts about this user:\n" + "\n".join(
                f"- {f['fact_text']}" for f in facts
            )
        else:
            self._ltm_context = ""

        self._system_prompt = config.SYSTEM_PROMPT.format(
            schema=config.DEMO_SCHEMA_DESCRIPTION
        ) + self._ltm_context

        self.memory = ShortTermMemory()
        self.memory.set_session_id(self.session_id)
        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT role, content FROM app_messages WHERE session_id = ? ORDER BY timestamp ASC",
                (session_id,),
            ).fetchall()
            for row in rows:
                role = row["role"]
                content = row["content"]
                if role == "user":
                    self.memory.add_user_message(content)
                elif role == "assistant":
                    self.memory.add_assistant_message([{"type": "text", "text": content or ""}])
                elif role == "tool":
                    try:
                        parsed = json.loads(content)
                        self.memory.add_tool_result(
                            parsed["tool_use_id"],
                            parsed["output"],
                            is_error=parsed.get("is_error", False),
                        )
                    except Exception:
                        pass
        finally:
            conn.close()

        self.trace.log("session_resume", {"user_id": self.user_id, "messages_loaded": len(rows)})
        return self.session_id

    def run(self, user_message):
        if self.session_id is None:
            self.start_session()

        # Touch the session's activity timestamp: a session counts as
        # "active" (dashboard stat) while turns keep landing within the
        # configured idle window, regardless of how long the conversation
        # has existed.
        from db.database import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "UPDATE app_sessions SET last_active_at = ? WHERE session_id = ?",
                (_now(), self.session_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.memory.add_user_message(user_message)
        self._persist_message("user", user_message)
        self.trace.log("user_message", {"content": user_message})

        # Lazily build (and reuse) the supervisor with a budget-guarded LLM
        # client so cost/token limits hold on every call path, not just the
        # orchestrator's own loop.
        if self._supervisor is None:
            from agent.supervisor import SupervisorAgent
            from agent.budget import BudgetGuardedLLMClient

            self._budget_client = BudgetGuardedLLMClient(self.llm)
            self._supervisor = SupervisorAgent(
                approval_handler=self.approval_handler,
                llm_client=self._budget_client,
            )

        # Build context from previous messages
        messages = self.memory.get_messages()
        # Keep it simple for context: json serialization of history (omitting the very last user message which is the task)
        history = [m for m in messages[:-1] if m["role"] in ("user", "assistant")]
        context_str = json.dumps(history) if history else ""

        try:
            final_text = self._supervisor.run(
                user_message, context=context_str, session_id=self.session_id, trace=self.trace
            )
        except Exception as e:
            from agent.budget import BudgetExceededError

            if isinstance(e, BudgetExceededError):
                # Halt the session but surface a clear, honest reason.
                self.trace.log_error(str(e))
                final_text = f"I had to stop early: {e}"
            else:
                self.trace.log_error(f"Supervisor failed: {e}")
                final_text = f"I encountered an error while processing your request: {e}"
        finally:
            # Keep orchestrator token accounting in sync with real usage from
            # every supervisor/worker LLM call so session_end traces are true.
            if self._budget_client is not None:
                self.total_input_tokens = self._budget_client.total_input_tokens
                self.total_output_tokens = self._budget_client.total_output_tokens

        self.memory.add_assistant_message([{"type": "text", "text": final_text}])
        self._persist_message("assistant", final_text)

        self.trace.log_final_answer(final_text)
        self._end_session()
        return final_text

    def _estimate_cost(self):
        from agent.budget import estimate_cost_usd

        provider = getattr(self._budget_client, "provider", None)
        return estimate_cost_usd(
            self.total_input_tokens, self.total_output_tokens, provider=provider
        )

    def _persist_message(self, role, content):
        from db.database import get_connection

        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO app_messages (message_id, session_id, role, content, timestamp) VALUES (?, ?, ?, ?, ?)",
                (_uuid(), self.session_id, role, content, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    def _end_session(self):

        self.trace.log("session_end", {
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "estimated_cost_usd": self._estimate_cost(),
        })

        try:
            facts = distill_facts_from_session(
                self.memory.get_messages(), llm_client=self._budget_client
            )
            # Facts persist indefinitely and are injected into future system
            # prompts, so they cross the same PII masking as query output.
            facts = [PIIGuardrail.mask_pii(f) for f in facts]
            if facts:
                self.long_term.save_facts(self.user_id, facts, self.session_id)
                self.trace.log("facts_saved", {"count": len(facts), "facts": facts})
        except Exception as e:
            self.trace.log_error(f"Failed to save long-term memory: {e}")

        # Status stays 'active' on purpose: active-ness is derived from
        # last_active_at vs the idle window (SESSION_IDLE_MINUTES), not
        # from a row flag flipped at the end of every turn. The
        # session_end trace event above marks the end of this run().
        self.long_term.close()
        self.trace.close()

    def get_trace(self):
        if self.session_id is None:
            return []
        from agent.trace import get_session_trace

        return get_session_trace(self.session_id)
