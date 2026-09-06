"""Answer-integrity tripwire (Sep 2026 finding).

An LLM can *assert* a destructive action "was executed" without any tool
call backing it — sycophantic confirmation of a false user premise, as
demonstrated live: the agent told the user a DELETE "was executed" while
its own COUNT(*) showed the rows intact. No guardrail fires on prose, so
this module does the next honest thing: DETECT the mismatch and log it.

`claims_execution(text)` spots execution-claim phrasing in a final answer;
`session_mutation_executed(session_id)` checks the session trace for a
successful mutating tool call (UPDATE/DELETE/INSERT/DROP/ALTER/TRUNCATE/
CREATE, re-parsed from the guardrailed SQL — never trusted from prose).
Claim present + no mutation in trace => `unverified_execution_claim`
event. This never blocks or rewrites answers; it makes the lie auditable.
"""

import json
import re

import sqlglot
from sqlglot import exp

EXECUTION_CLAIM_RE = re.compile(
    r"(was|were|has been|have been|had been|got)\s+"
    r"(executed|deleted|dropped|updated|inserted|wiped|cleared|approved)\b"
    r"|\bsuccessfully\s+"
    r"(executed|deleted|dropped|updated|inserted|wiped|cleared)\b",
    re.IGNORECASE,
)

MUTATION_TYPES = (
    exp.Update,
    exp.Delete,
    exp.Insert,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Create,
    exp.Merge,
)


def claims_execution(text):
    """Return the matched claim phrase, or None."""
    if not isinstance(text, str):
        return None
    match = EXECUTION_CLAIM_RE.search(text)
    return match.group(0) if match else None


def _is_mutation_sql(sql):
    try:
        statements = sqlglot.parse(sql, read="sqlite")
    except Exception:
        return False
    return any(
        isinstance(stmt, MUTATION_TYPES) for stmt in statements if stmt is not None
    )


def session_mutation_executed(session_id):
    """True when the session trace shows a SUCCESSFUL mutating tool call.

    Joins successful `tool_result` events to their `guardrail_verdict`
    events by call_id and re-parses the SQL — the verdict (not the prose)
    is the source of truth for what ran.
    """
    from db.database import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT event_type, data FROM app_trace_events WHERE session_id = ?",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    succeeded = set()
    verdict_sql = {}
    for row in rows:
        try:
            data = json.loads(row["data"])
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if row["event_type"] == "tool_result" and data.get("status") == "success":
            call_id = data.get("call_id")
            if call_id:
                succeeded.add(call_id)
        elif row["event_type"] == "guardrail_verdict" and data.get("sql"):
            call_id = data.get("call_id")
            if call_id:
                verdict_sql[call_id] = data["sql"]

    return any(
        _is_mutation_sql(verdict_sql[call_id])
        for call_id in succeeded
        if call_id in verdict_sql
    )
