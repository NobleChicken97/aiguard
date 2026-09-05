"""Prometheus-style metrics (Phase 6) with zero new dependencies.

Two sources, stated honestly in every number's HELP text:
- Business aggregates (sessions, tool calls, guardrail verdicts, approvals,
  builder runs, cost/token totals) are computed LIVE FROM THE DB on each
  scrape — the same queries as the dashboard, always consistent and
  restart-safe. Single-process demo scope (like the rate limiters): for
  horizontal scale these move to a real aggregator.
- Request latency/counts for the watched endpoints are in-process counters
  fed by a tiny middleware. They reset on restart — same documented caveat.
"""

import json
import threading
import time

_PROCESS_START_MONO = time.monotonic()

_lock = threading.Lock()
_http = {}  # (method, path, status) -> [count, total_seconds]

WATCHED_PATHS = frozenset({"/api/chat", "/api/chat/resume", "/api/query-builder/run"})


def record_http(method, path, status, seconds):
    if path not in WATCHED_PATHS:
        return
    key = (method or "", path, str(status))
    with _lock:
        cell = _http.setdefault(key, [0, 0.0])
        cell[0] += 1
        cell[1] += float(seconds or 0.0)


def _http_snapshot():
    with _lock:
        return sorted(
            ((m, p, s, n, tot) for (m, p, s), (n, tot) in _http.items())
        )


def _db_aggregates():
    """Rollups for /metrics. Best-effort: any failure yields zeros, and the
    endpoint still answers (monitoring must never 500 because stats broke)."""
    from db.database import get_connection

    agg = {
        "sessions": 0,
        "messages": 0,
        "tool_calls": 0,
        "tools": {},
        "verdicts": {"ALLOWED": 0, "BLOCKED": 0, "REQUIRES_APPROVAL": 0},
        "approvals_pending": 0,
        "approvals_decided": {},
        "approval_resolve_avg_seconds": 0.0,
        "builder_runs": 0,
        "cost_usd_total": 0.0,
        "input_tokens_total": 0,
        "output_tokens_total": 0,
    }
    try:
        conn = get_connection()
        try:
            count = lambda sql, params=(): conn.execute(sql, params).fetchone()["cnt"]  # noqa: E731
            agg["sessions"] = count("SELECT COUNT(*) AS cnt FROM app_sessions")
            agg["messages"] = count("SELECT COUNT(*) AS cnt FROM app_messages")
            agg["tool_calls"] = count("SELECT COUNT(*) AS cnt FROM app_tool_calls")
            agg["builder_runs"] = count("SELECT COUNT(*) AS cnt FROM app_builder_runs")
            agg["approvals_pending"] = count(
                "SELECT COUNT(*) AS cnt FROM app_approval_requests WHERE decision IS NULL"
            )
            for row in conn.execute(
                """SELECT decision, COUNT(*) AS cnt FROM app_approval_requests
                   WHERE decision IS NOT NULL GROUP BY decision"""
            ).fetchall():
                agg["approvals_decided"][row["decision"] or "unknown"] = row["cnt"]
            for row in conn.execute(
                """SELECT tool_name, COUNT(*) AS cnt FROM app_tool_calls
                   GROUP BY tool_name"""
            ).fetchall():
                agg["tools"][row["tool_name"] or "unknown"] = row["cnt"]
            for row in conn.execute(
                """SELECT data FROM app_trace_events
                   WHERE event_type = 'guardrail_verdict'
                   ORDER BY timestamp DESC LIMIT 2000"""
            ).fetchall():
                try:
                    verdict = (json.loads(row["data"]) or {}).get("verdict", "")
                except Exception:
                    continue
                if verdict in agg["verdicts"]:
                    agg["verdicts"][verdict] += 1

            # Mean approval resolution time over recent decided rows.
            spans = []
            for row in conn.execute(
                """SELECT created_at, decided_at FROM app_approval_requests
                   WHERE decision IS NOT NULL AND decided_at IS NOT NULL
                   ORDER BY decided_at DESC LIMIT 500"""
            ).fetchall():
                try:
                    from datetime import datetime

                    start = datetime.fromisoformat(row["created_at"])
                    end = datetime.fromisoformat(row["decided_at"])
                    spans.append(max(0.0, (end - start).total_seconds()))
                except Exception:
                    continue
            if spans:
                agg["approval_resolve_avg_seconds"] = sum(spans) / len(spans)

            # Lifetime cost/token estimates from session_end trace payloads.
            for row in conn.execute(
                """SELECT data FROM app_trace_events
                   WHERE event_type = 'session_end'
                   ORDER BY timestamp DESC LIMIT 1000"""
            ).fetchall():
                try:
                    data = json.loads(row["data"]) or {}
                except Exception:
                    continue
                try:
                    agg["cost_usd_total"] += float(data.get("estimated_cost_usd") or 0.0)
                    agg["input_tokens_total"] += int(data.get("total_input_tokens") or 0)
                    agg["output_tokens_total"] += int(data.get("total_output_tokens") or 0)
                except (TypeError, ValueError):
                    continue
        finally:
            conn.close()
    except Exception:
        pass
    return agg


def _q(value):
    return '"%s"' % str(value).replace("\\", "\\\\").replace('"', '\\"')


def render_prometheus():
    """Full exposition text for GET /metrics."""
    agg = _db_aggregates()
    lines = []
    add = lines.append

    def gauge(name, help_text, value, labels=None):
        add(f"# HELP {name} {help_text}")
        add("# TYPE %s gauge" % name)
        if labels:
            lab = ",".join(f"{k}={_q(v)}" for k, v in labels.items())
            add(f"{name}{{{lab}}} {value}")
        else:
            add(f"{name} {value}")

    def counter(name, help_text, value, labels=None):
        add(f"# HELP {name} {help_text}")
        add("# TYPE %s counter" % name)
        if labels:
            lab = ",".join(f"{k}={_q(v)}" for k, v in labels.items())
            add(f"{name}{{{lab}}} {value}")
        else:
            add(f"{name} {value}")

    uptime = round(time.monotonic() - _PROCESS_START_MONO, 3)
    gauge("aiguard_uptime_seconds", "Process uptime in seconds (resets on restart).", uptime)

    gauge("aiguard_sessions_total", "Rows in app_sessions (all time, DB source).", agg["sessions"])
    gauge("aiguard_messages_total", "Rows in app_messages (all time, DB source).", agg["messages"])
    gauge("aiguard_tool_calls_total", "Rows in app_tool_calls (all time, DB source).", agg["tool_calls"])
    for tool, count in sorted(agg["tools"].items()):
        counter("aiguard_tool_calls_by_tool", "Tool calls split by tool name (DB source).", count, {"tool": tool})
    for verdict, count in sorted(agg["verdicts"].items()):
        counter("aiguard_guardrail_verdicts", "Guardrail verdicts seen in recent trace events (DB source).", count, {"verdict": verdict})
    gauge("aiguard_approvals_pending", "Approval rows awaiting a decision (DB source).", agg["approvals_pending"])
    for decision, count in sorted(agg["approvals_decided"].items()):
        counter("aiguard_approvals_decided", "Approval decisions by outcome (DB source).", count, {"decision": decision})
    gauge("aiguard_approval_resolve_avg_seconds", "Mean pending-to-decided time over recent approvals (DB source).", round(agg["approval_resolve_avg_seconds"], 3))
    gauge("aiguard_builder_runs_total", "Rows in app_builder_runs (DB source).", agg["builder_runs"])
    counter("aiguard_session_cost_usd_total", "Sum of session_end cost estimates (DB source).", round(agg["cost_usd_total"], 6))
    counter("aiguard_session_tokens_total", "Sum of session_end token counts (DB source).", agg["input_tokens_total"], {"kind": "input"})
    counter("aiguard_session_tokens_total", "Sum of session_end token counts (DB source).", agg["output_tokens_total"], {"kind": "output"})

    for method, path, status, count, total in _http_snapshot():
        labels = {"method": method, "path": path, "status": status}
        counter("aiguard_http_requests", "Endpoint hits (in-process, resets on restart).", count, labels)
        counter("aiguard_http_request_seconds", "Summed endpoint latency in seconds (in-process).", round(total, 6), labels)

    return "\n".join(lines) + "\n"
