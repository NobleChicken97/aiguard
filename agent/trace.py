import json

from app_util import new_uuid as _uuid, now_utc as _now
from db.database import get_connection



class TraceLogger:
    """Append-only trace logger. Every plan, tool call, guardrail verdict,
    approval decision, and final answer is written as a JSON event."""

    def __init__(self, session_id):
        self.session_id = session_id
        self._conn = None

    @property
    def conn(self):
        if self._conn is None:
            self._conn = get_connection()
        return self._conn

    def log(self, event_type, data):
        self.conn.execute(
            """INSERT INTO app_trace_events (trace_id, session_id, event_type, data, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (_uuid(), self.session_id, event_type, json.dumps(data, default=str), _now()),
        )
        self.conn.commit()

    def log_tool_call(self, tool_name, tool_input, call_id=None):
        self.log("tool_call", {
            "call_id": call_id,
            "tool_name": tool_name,
            "input": tool_input,
        })

    def log_guardrail_verdict(self, call_id, sql, verdict, reason):
        self.log("guardrail_verdict", {
            "call_id": call_id,
            "sql": sql,
            "verdict": verdict,
            "reason": reason,
        })

    def log_tool_result(self, call_id, tool_name, status, output):
        self.log("tool_result", {
            "call_id": call_id,
            "tool_name": tool_name,
            "status": status,
            "output": output,
        })

    def log_approval_request(self, call_id, risk_reason):
        self.log("approval_request", {
            "call_id": call_id,
            "risk_reason": risk_reason,
        })

    def log_approval_decision(self, call_id, decision, decided_by="user"):
        self.log("approval_decision", {
            "call_id": call_id,
            "decision": decision,
            "decided_by": decided_by,
        })

    def log_final_answer(self, answer):
        self.log("final_answer", {"answer": answer})

    def log_error(self, error_message, context=None):
        self.log("error", {
            "error": error_message,
            "context": context,
        })

    def log_retry(self, call_id, attempt, reason):
        self.log("retry", {
            "call_id": call_id,
            "attempt": attempt,
            "reason": reason,
        })

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


def get_session_trace(session_id):
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT trace_id, event_type, data, timestamp FROM app_trace_events WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        ).fetchall()
        events = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data"])
            events.append(d)
        return events
    finally:
        conn.close()
