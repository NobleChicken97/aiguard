from abc import ABC, abstractmethod
from dataclasses import dataclass

from app_util import new_uuid as _uuid, now_utc as _now
from db.database import get_connection



class ApprovalHandler(ABC):
    @abstractmethod
    def request_approval(self, call_id, session_id, risk_reason, tool_name, tool_input):
        ...


class CLIApprovalHandler(ApprovalHandler):
    def request_approval(self, call_id, session_id, risk_reason, tool_name, tool_input):
        print("\n" + "=" * 60)
        print("APPROVAL REQUIRED")
        print(f"  Tool:       {tool_name}")
        print(f"  Input:      {tool_input}")
        print(f"  Risk:       {risk_reason}")
        print("=" * 60)
        while True:
            choice = input("Approve this action? (y/n): ").strip().lower()
            if choice in ("y", "yes"):
                self._record(call_id, session_id, risk_reason, True)
                return True
            if choice in ("n", "no"):
                self._record(call_id, session_id, risk_reason, False)
                return False
            print("Please enter 'y' or 'n'.")

    def _record(self, call_id, session_id, risk_reason, approved):
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO app_approval_requests
                   (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    _uuid(), call_id, session_id, risk_reason,
                    "cli_user", "approved" if approved else "denied",
                    _now(), _now(),
                ),
            )
            conn.commit()
        finally:
            conn.close()


class AutoApproveHandler(ApprovalHandler):
    """Auto-approves everything. For testing only."""

    def request_approval(self, call_id, session_id, risk_reason, tool_name, tool_input):
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO app_approval_requests
                   (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (_uuid(), call_id, session_id, risk_reason, "auto", "approved", _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()
        return True


class AutoDenyHandler(ApprovalHandler):
    """Auto-denies everything. For testing only."""

    def request_approval(self, call_id, session_id, risk_reason, tool_name, tool_input):
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO app_approval_requests
                   (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (_uuid(), call_id, session_id, risk_reason, "auto", "denied", _now(), _now()),
            )
            conn.commit()
        finally:
            conn.close()
        return False


@dataclass
class PendingApproval:
    """A paused turn: the worker thread has been released; resume later
    via Orchestrator.resume once the approval row has a decision."""

    approval_id: str
    call_id: str
    session_id: str
    risk_reason: str


class ApprovalPending(Exception):
    """Unwind signal from non-blocking handlers up to the chat endpoint.

    Raised inside tool execution; the worker attaches its loop state
    (``worker_name`` + ``messages`` snapshot, which already contains the
    assistant tool_use block) before re-raising; the orchestrator persists
    the resume row and returns a PendingApproval to the caller.
    """

    def __init__(self, approval_id, call_id, session_id, risk_reason, tool_name, tool_input):
        super().__init__(f"Approval pending for {tool_name}: {risk_reason}")
        self.approval_id = approval_id
        self.call_id = call_id
        self.session_id = session_id
        self.risk_reason = risk_reason
        self.tool_name = tool_name
        self.tool_input = tool_input
        self.worker_name = None
        self.messages = None


class AsyncApprovalHandler(ApprovalHandler):
    """Non-blocking web handler (Phase 3): creates the pending approval row
    and returns its id. SQLTool raises ApprovalPending right after, which
    unwinds the worker loop and releases the thread in milliseconds.

    Resolution arrives later through the queue endpoints; resume reads the
    approval row as the gate decision (no second human prompt).
    """

    non_blocking = True

    def create_pending(self, call_id, session_id, risk_reason, tool_name, tool_input):
        approval_id = _uuid()
        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO app_approval_requests
                   (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (approval_id, call_id, session_id, risk_reason, None, None, None, _now()),
            )
            conn.commit()
        finally:
            conn.close()
        return approval_id

    def request_approval(self, *args, **kwargs):
        raise RuntimeError(
            "AsyncApprovalHandler never blocks: SQLTool raises ApprovalPending instead."
        )


def save_pending_resume(call_id, approval_id, session_id, worker_name, messages, tool_name, tool_input, input_tokens=0, output_tokens=0):
    """Persist one resume row per paused turn (DELETE+INSERT: dialect-free
    upsert, mirroring the codebase's SQLite/PG discipline)."""
    import json as _json

    conn = get_connection()
    try:
        conn.execute("DELETE FROM app_pending_resumes WHERE call_id = ?", (call_id,))
        conn.execute(
            """INSERT INTO app_pending_resumes
               (call_id, approval_id, session_id, worker_name, messages, tool_name, tool_input,
                input_tokens, output_tokens, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                call_id,
                approval_id,
                session_id,
                worker_name,
                _json.dumps(messages, default=str),
                tool_name,
                _json.dumps(tool_input, default=str),
                int(input_tokens or 0),
                int(output_tokens or 0),
                _now(),
            ),
        )
        _janitor_stale_resumes(conn)
        conn.commit()
    finally:
        conn.close()


#: Resume rows outlive their usefulness once the human has decided AND the
#: user never came back: a day-old decided row means an abandoned turn
#: (resuming it after purge 404s honestly instead of working). Pending
#: (undecided) rows are never purged — the human may still decide.
PENDING_RESUME_TTL_HOURS = 24


def _janitor_stale_resumes(conn):
    """Opportunistic purge of abandoned resume rows (decided + older than
    TTL). Runs inside save_pending_resume's transaction: no scheduler, no
    background thread, negligible cost (one indexed-range delete)."""
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=PENDING_RESUME_TTL_HOURS)).isoformat()
    conn.execute(
        """DELETE FROM app_pending_resumes
           WHERE created_at < ?
           AND EXISTS (SELECT 1 FROM app_approval_requests ar
                       WHERE ar.approval_id = app_pending_resumes.approval_id
                       AND ar.decision IS NOT NULL)""",
        (cutoff,),
    )


def load_pending_resume(session_id):
    """Latest pending resume row for the session (parsed), or None."""
    import json as _json

    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT call_id, approval_id, session_id, worker_name, messages,
                      tool_name, tool_input, input_tokens, output_tokens, created_at
               FROM app_pending_resumes WHERE session_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (session_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    data = dict(row)
    data["messages"] = _json.loads(data["messages"])
    data["tool_input"] = _json.loads(data["tool_input"])
    return data


def delete_pending_resume(call_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM app_pending_resumes WHERE call_id = ?", (call_id,))
        conn.commit()
    finally:
        conn.close()


def get_approval_status(approval_id):
    """(decision|None, session_id|None, risk_reason|"") for an approval id."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT decision, session_id, risk_reason FROM app_approval_requests WHERE approval_id = ?",
            (approval_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None, None, ""
    return row["decision"], row["session_id"], row["risk_reason"] or ""


def get_pending_approvals(for_user_id=None):
    """Pending approvals, optionally scoped to one user's sessions.

    ``for_user_id=None`` returns everything (dashboard aggregate counter,
    admin queue view). Otherwise only approvals whose session row belongs
    to that user — the per-user isolation rule for the approval queue.
    """
    conn = get_connection()
    try:
        if for_user_id is None:
            rows = conn.execute(
                """SELECT ar.approval_id, ar.call_id, ar.session_id, ar.risk_reason,
                          ar.created_at, tc.tool_name, tc.input
                   FROM app_approval_requests ar
                   JOIN app_tool_calls tc ON ar.call_id = tc.call_id
                   WHERE ar.decision IS NULL
                   ORDER BY ar.created_at ASC""",
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT ar.approval_id, ar.call_id, ar.session_id, ar.risk_reason,
                          ar.created_at, tc.tool_name, tc.input
                   FROM app_approval_requests ar
                   JOIN app_tool_calls tc ON ar.call_id = tc.call_id
                   JOIN app_sessions s ON ar.session_id = s.session_id
                   WHERE ar.decision IS NULL AND s.user_id = ?
                   ORDER BY ar.created_at ASC""",
                (for_user_id,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def owns_approval(user_id, approval_id):
    """True when the approval's session belongs to the user."""
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT 1 AS ok FROM app_approval_requests ar
               JOIN app_sessions s ON ar.session_id = s.session_id
               WHERE ar.approval_id = ? AND s.user_id = ?""",
            (approval_id, user_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def resolve_approval(approval_id, decision, decided_by="web_user"):
    conn = get_connection()
    try:
        # cursor.rowcount works on both sqlite3 and psycopg2 wrappers;
        # conn.total_changes is a SQLite-only attribute.
        cursor = conn.execute(
            """UPDATE app_approval_requests
               SET decision = ?, decided_by = ?, decided_at = ?
               WHERE approval_id = ? AND decision IS NULL""",
            (decision, decided_by, _now(), approval_id),
        )
        updated = (cursor.rowcount or 0) > 0
        conn.commit()
        return updated
    finally:
        conn.close()
