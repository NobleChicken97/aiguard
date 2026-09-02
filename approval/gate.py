import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from db.database import get_connection


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


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


class WebApprovalHandler(ApprovalHandler):
    """Polls the DB for an approval decision. Used by the FastAPI web app.

    The orchestrator creates a pending approval row, then polls until a
    decision appears. The web UI writes the decision via the /api/approve
    or /api/deny endpoints.
    """

    def __init__(self, poll_interval=1.0, timeout=300):
        self.poll_interval = poll_interval
        self.timeout = timeout

    def request_approval(self, call_id, session_id, risk_reason, tool_name, tool_input):
        import time

        conn = get_connection()
        try:
            conn.execute(
                """INSERT INTO app_approval_requests
                   (approval_id, call_id, session_id, risk_reason, decided_by, decision, decided_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (_uuid(), call_id, session_id, risk_reason, None, None, None, _now()),
            )
            conn.commit()

            elapsed = 0
            while elapsed < self.timeout:
                row = conn.execute(
                    "SELECT decision FROM app_approval_requests WHERE call_id = ?",
                    (call_id,),
                ).fetchone()
                if row and row["decision"] is not None:
                    return row["decision"] == "approved"
                time.sleep(self.poll_interval)
                elapsed += self.poll_interval

            return False
        finally:
            conn.close()


def get_pending_approvals():
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT ar.approval_id, ar.call_id, ar.session_id, ar.risk_reason,
                      ar.created_at, tc.tool_name, tc.input
               FROM app_approval_requests ar
               JOIN app_tool_calls tc ON ar.call_id = tc.call_id
               WHERE ar.decision IS NULL
               ORDER BY ar.created_at ASC""",
        ).fetchall()
        return [dict(r) for r in rows]
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
