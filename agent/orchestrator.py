import json

import config
from agent.memory import ShortTermMemory, LongTermMemory, distill_facts_from_session
from agent.trace import TraceLogger
from agent.llm_client import ClaudeLLMClient
from app_util import new_uuid as _uuid, now_utc as _now
from approval.gate import (
    ApprovalPending,
    PendingApproval,
    delete_pending_resume,
    get_approval_status,
    load_pending_resume,
    save_pending_resume,
)
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

    def _ensure_supervisor(self):
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
        self._ensure_supervisor()

        # Build context from previous messages
        messages = self.memory.get_messages()
        # Keep it simple for context: json serialization of history (omitting the very last user message which is the task)
        history = [m for m in messages[:-1] if m["role"] in ("user", "assistant")]
        context_str = json.dumps(history) if history else ""

        try:
            final_text = self._supervisor.run(
                user_message, context=context_str, session_id=self.session_id, trace=self.trace,
                system_prompt=self._system_prompt or "",
            )
        except ApprovalPending as pending:
            # Pause, don't fail: persist resume state and release the thread.
            # The finally below still syncs token accounting on the way out.
            return self._pause(pending)
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

        return self._complete(final_text)

    def _complete(self, final_text):
        self.memory.add_assistant_message([{"type": "text", "text": final_text}])
        self._persist_message("assistant", final_text)

        self.trace.log_final_answer(final_text)
        self._end_session()
        return final_text

    def _pause(self, pending):
        """Persist resume state and release the worker thread (Phase 3).

        The session deliberately stays open (no _end_session, no final
        message, no fact distillation): resume() finishes the turn.
        """
        save_pending_resume(
            pending.call_id,
            pending.approval_id,
            self.session_id,
            pending.worker_name,
            pending.messages,
            pending.tool_name,
            pending.tool_input,
            int(getattr(self._budget_client, "total_input_tokens", 0) or 0),
            int(getattr(self._budget_client, "total_output_tokens", 0) or 0),
        )
        self.trace.log("approval_paused", {
            "approval_id": pending.approval_id,
            "call_id": pending.call_id,
            "reason": pending.risk_reason,
        })
        # Release DB connections now: a pause skips _end_session, and every
        # leaked handle made Windows reset_db's os.remove fail silently,
        # corrupting suite isolation (caught in the Sep 2026 audit). resume()
        # re-opens them via load_session.
        self.long_term.close()
        self.trace.close()
        return PendingApproval(
            approval_id=pending.approval_id,
            call_id=pending.call_id,
            session_id=self.session_id,
            risk_reason=pending.risk_reason,
        )

    def resume(self, session_id):
        """Continue a paused turn after the human decided.

        Returns the final answer (str), or PendingApproval again when the
        approval is still undecided. The approval row is the gate decision:
        approved executes now, denied feeds back as a refusal. The guardrail
        is re-checked first — policy may have changed while paused.
        """
        self.load_session(session_id)
        self._ensure_supervisor()

        saved = load_pending_resume(session_id)
        if saved is None:
            raise ValueError(f"No paused turn found for session {session_id}")

        # Continue (not restart) budget accounting across the pause: the
        # resume drive adds to the pre-pause totals saved above.
        if self._budget_client is not None:
            self._budget_client.total_input_tokens = int(saved.get("input_tokens") or 0)
            self._budget_client.total_output_tokens = int(saved.get("output_tokens") or 0)

        decision, _, risk_reason = get_approval_status(saved["approval_id"])
        if decision is None:
            self.trace.log("approval_still_pending", {"approval_id": saved["approval_id"]})
            return PendingApproval(
                approval_id=saved["approval_id"],
                call_id=saved["call_id"],
                session_id=session_id,
                risk_reason="still awaiting a decision",
            )

        # Consume the row before re-driving: a second pause saves fresh state.
        delete_pending_resume(saved["call_id"])

        output, is_error, status = self._resolve_pending_tool(saved, decision, risk_reason)

        self.trace.log_approval_decision(saved["call_id"], decision)
        self.trace.log_tool_result(saved["call_id"], saved["tool_name"], status, output)

        from agent.budget import BudgetExceededError

        try:
            final_text = self._supervisor.resume(
                saved["worker_name"],
                saved["messages"],
                saved["call_id"],
                output,
                is_error,
                session_id=self.session_id,
                trace=self.trace,
                system_prompt=self._system_prompt or "",
            )
        except ApprovalPending as pending:
            return self._pause(pending)
        except Exception as e:
            if isinstance(e, BudgetExceededError):
                self.trace.log_error(str(e))
                final_text = f"I had to stop early: {e}"
            else:
                self.trace.log_error(f"Supervisor failed: {e}")
                final_text = f"I encountered an error while processing your request: {e}"
        finally:
            if self._budget_client is not None:
                self.total_input_tokens = self._budget_client.total_input_tokens
                self.total_output_tokens = self._budget_client.total_output_tokens

        return self._complete(final_text)

    def _resolve_pending_tool(self, saved, decision, risk_reason):
        """Execute-or-refuse the paused tool call. Returns (output, is_error, status)."""
        if decision != "approved":
            return f"Action denied by human: {risk_reason}", True, "denied"
        if saved["tool_name"] != "sql_tool":
            return f"Cannot resume unknown tool '{saved['tool_name']}'.", True, "failed"
        from guardrails.sql_guardrail import SQLGuardrail
        from tools.sql_tool import SQLTool

        sql = (saved["tool_input"] or {}).get("sql", "")
        verdict = SQLGuardrail().check(sql)
        if verdict.blocked:
            return f"BLOCKED by guardrail on resume: {verdict.reason}", True, "blocked"
        tool = SQLTool(approval_handler=self.approval_handler)
        result = tool._execute_sql(sql, saved["call_id"], verdict)
        return result.output, result.status == "failed", result.status

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
            if config.PII_NER_ENABLED:
                facts = [PIIGuardrail.mask_pii_ner(f) for f in facts]
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
