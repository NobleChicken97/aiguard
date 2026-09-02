import asyncio
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
from agent.llm_client import build_llm_client
from agent.memory import LongTermMemory
from agent.orchestrator import Orchestrator
from agent.trace import get_session_trace
from approval.gate import (
    AutoApproveHandler,
    WebApprovalHandler,
    get_pending_approvals,
    resolve_approval,
)
from db.database import get_connection, initialize_db
from tools.query_builder import (
    QueryBuilderError,
    QueryBuilderRequest,
    get_builder_schema,
    run_builder_query,
)
from webapp_ratelimit import RL_STATE, configure as configure_ratelimit
from app_logging import configure_logging, get_logger

logger = get_logger("webapp")


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "ui" / "templates"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Test-only override. Tests can set this to a FakeLLMClient so the chat endpoint
# works without a real Anthropic API key.
_chat_llm_client_override = None


class ChatRequest(BaseModel):
    message: str
    user_id: str = "web_user"
    session_id: str | None = None
    # When true, use AutoApproveHandler so the chat returns immediately
    # (useful for scripted demos and tests). Default false: gate risky
    # actions through /approval-queue so the human-in-the-loop flow is
    # actually reachable from the web UI.
    auto_approve: bool = False
    # Web approval poll timeout in seconds (ignored when auto_approve=true).
    approval_timeout: int = 120


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager handles startup and shutdown logic.

    On startup the SQLite schema is initialized so the demo database exists
    before the first request arrives, the in-process rate limiters are
    configured from the current settings, and structured logging is
    installed.
    """
    configure_logging()
    initialize_db()
    configure_ratelimit(
        chat_per_min=config.CHAT_RATE_PER_MIN,
        sse_max_per_ip=config.SSE_MAX_PER_IP,
    )
    logger.info(
        "agentic-guardrails started | chat_per_min=%s sse_max_per_ip=%s",
        config.CHAT_RATE_PER_MIN,
        config.SSE_MAX_PER_IP,
    )
    yield


app = FastAPI(
    title="Agentic Guardrails Demo",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
def health_check():
    """Lightweight health endpoint used by Docker and load balancers."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT COUNT(*) AS cnt FROM app_sessions").fetchone()
        db_ok = True
    except Exception as e:
        rows = {"cnt": 0}
        db_ok = False
    finally:
        conn.close()

    return JSONResponse({
        "status": "ok" if db_ok else "unhealthy",
        "db_connected": db_ok,
        "session_count": rows["cnt"] if rows else 0,
    })


def _compute_stats():
    """Aggregate live system statistics for the monitoring dashboard.

    All queries are dialect-safe (SQLite + PostgreSQL). Guardrail verdicts
    are counted in Python from recent trace events because verdicts live
    inside JSON payloads, and JSON extraction functions differ per dialect.
    """
    try:
        conn = get_connection()
        try:
            def _count(sql, params=()):
                return conn.execute(sql, params).fetchone()["cnt"]

            sessions = _count("SELECT COUNT(*) AS cnt FROM app_sessions")
            # "Active" = activity within the idle window (status stays
            # 'active' for the life of the session row).
            idle_cutoff = (
                datetime.now(timezone.utc)
                - timedelta(minutes=config.SESSION_IDLE_MINUTES)
            ).isoformat()
            active_sessions = _count(
                """SELECT COUNT(*) AS cnt FROM app_sessions
                   WHERE status = 'active' AND last_active_at >= ?""",
                (idle_cutoff,),
            )
            messages = _count("SELECT COUNT(*) AS cnt FROM app_messages")
            tool_calls = _count("SELECT COUNT(*) AS cnt FROM app_tool_calls")
            builder_runs = _count("SELECT COUNT(*) AS cnt FROM app_builder_runs")

            tool_rows = conn.execute(
                """SELECT tool_name, COUNT(*) AS cnt FROM app_tool_calls
                   GROUP BY tool_name ORDER BY cnt DESC"""
            ).fetchall()

            verdict_rows = conn.execute(
                """SELECT data FROM app_trace_events
                   WHERE event_type = 'guardrail_verdict'
                   ORDER BY timestamp DESC
                   LIMIT 500"""
            ).fetchall()

            recent_rows = conn.execute(
                """SELECT s.session_id, s.user_id, s.started_at, s.status,
                          COUNT(t.trace_id) AS event_count
                   FROM app_sessions s
                   LEFT JOIN app_trace_events t ON t.session_id = s.session_id
                   GROUP BY s.session_id, s.user_id, s.started_at, s.status
                   ORDER BY s.started_at DESC
                   LIMIT 10"""
            ).fetchall()
        finally:
            conn.close()

        allowed = blocked = approval_required = 0
        for row in verdict_rows:
            try:
                verdict = (json.loads(row["data"]) or {}).get("verdict", "")
            except Exception:
                continue
            if verdict == "ALLOWED":
                allowed += 1
            elif verdict == "BLOCKED":
                blocked += 1
            elif verdict == "REQUIRES_APPROVAL":
                approval_required += 1

        return {
            "db_connected": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sessions": sessions,
            "active_sessions": active_sessions,
            "messages": messages,
            "tool_calls": tool_calls,
            "builder_runs": builder_runs,
            "pending_approvals": len(get_pending_approvals()),
            "guardrail": {
                "allowed_recent": allowed,
                "blocked_recent": blocked,
                "approval_required_recent": approval_required,
            },
            "tools": {r["tool_name"]: r["cnt"] for r in tool_rows},
            "recent_sessions": [dict(r) for r in recent_rows],
        }
    except Exception:
        return {
            "db_connected": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sessions": 0,
            "active_sessions": 0,
            "messages": 0,
            "tool_calls": 0,
            "builder_runs": 0,
            "pending_approvals": 0,
            "guardrail": {"allowed_recent": 0, "blocked_recent": 0, "approval_required_recent": 0},
            "tools": {},
            "recent_sessions": [],
        }


@app.get("/api/stats")
def stats_api():
    """One-shot snapshot of system statistics."""
    return JSONResponse(_compute_stats())


@app.get("/api/stream")
async def stats_stream(request: Request):
    """Server-Sent Events stream pushing a fresh stats snapshot every 2s.

    DB aggregation runs in the threadpool so the event loop is never
    blocked while clients stay connected. The per-IP concurrent stream
    cap keeps one client from pinning a worker per request.
    """
    client_ip = _client_ip(request)
    try:
        slot = RL_STATE["stream_guard"].acquire(client_ip)
    except RL_STATE["stream_guard"].StreamLimitExceeded as e:
        logger.info("sse stream limit hit ip=%s", client_ip)
        raise HTTPException(status_code=429, detail=str(e))
    with slot:
        async def event_generator():
            while True:
                stats = await run_in_threadpool(_compute_stats)
                yield f"data: {json.dumps(stats)}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"request": request},
    )


@app.get("/query-builder", response_class=HTMLResponse)
def query_builder_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="query_builder.html",
        context={"request": request},
    )


@app.get("/api/query-builder/schema")
def query_builder_schema():
    """Live schema metadata (allowed tables + columns) for the builder UI."""
    return JSONResponse(get_builder_schema())


@app.post("/api/query-builder/run")
def query_builder_run(req: QueryBuilderRequest):
    """Execute a visually-built SELECT through the guardrail layer.

    Read-only by construction; validation failures surface as 400s with the
    same operator-friendly messages shown in the UI.
    """
    try:
        result = run_builder_query(req)
    except QueryBuilderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(result)


def _load_sessions(limit=50):
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT s.session_id, s.user_id, s.started_at, s.status,
                      COUNT(t.trace_id) AS event_count
               FROM app_sessions s
               LEFT JOIN app_trace_events t ON t.session_id = s.session_id
               GROUP BY s.session_id, s.user_id, s.started_at, s.status
               ORDER BY s.started_at DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _build_pending_rows():
    rows = []
    for item in get_pending_approvals():
        input_value = item.get("input")
        try:
            parsed_input = json.loads(input_value) if isinstance(input_value, str) else input_value
        except Exception:
            parsed_input = input_value
        rows.append({
            **item,
            "input_display": json.dumps(parsed_input, indent=2, sort_keys=True) if isinstance(parsed_input, (dict, list)) else str(parsed_input),
        })
    return rows


def _get_chat_llm_client():
    """Return the LLM client used by the chat endpoint.

    In tests, ``_chat_llm_client_override`` can be set to avoid hitting a
    real provider. Misconfigured providers surface as a 400 via ValueError.
    """
    if _chat_llm_client_override is not None:
        return _chat_llm_client_override
    try:
        return build_llm_client()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _llm_configured():
    try:
        return build_llm_client() is not None
    except ValueError:
        return False


def _client_ip(request: Request) -> str:
    """Best-effort client IP. ``X-Forwarded-For`` first hop if present,
    else the direct client. Good enough for an in-process rate limit.
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


CSRF_COOKIE = "approval_csrf"


def _csrf_ok(request: Request, submitted_token: str) -> bool:
    """Double-submit cookie check for the approval form posts.

    The queue page sets a random token both as an HttpOnly cookie and as a
    hidden form field; cross-site form posts cannot know the cookie value,
    so a mismatch (or either half missing) rejects the action. Approval
    ids are unguessable uuid4s, so this is defense in depth on top of an
    intentionally login-free demo — not a substitute for real auth.
    """
    cookie_token = request.cookies.get(CSRF_COOKIE, "")
    if not cookie_token or not submitted_token:
        return False
    return secrets.compare_digest(cookie_token, submitted_token)


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/chat", status_code=303)


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={"request": request, "llm_configured": _llm_configured()},
    )


@app.post("/api/chat")
async def chat_api(req: ChatRequest, request: Request):
    client_ip = _client_ip(request)
    if not RL_STATE["chat_bucket"].allow(client_ip):
        logger.info("chat rate-limited ip=%s", client_ip)
        raise HTTPException(
            status_code=429,
            detail="Too many chat requests. Slow down and retry shortly.",
        )

    llm_client = _get_chat_llm_client()
    if llm_client is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No LLM API key configured. Set LLM_PROVIDER (gemini, groq,"
                " nvidia, openai or openai-compat) with LLM_API_KEY — or"
                " ANTHROPIC_API_KEY for the Claude path."
            ),
        )

    approval_handler = (
        AutoApproveHandler()
        if req.auto_approve
        else WebApprovalHandler(timeout=req.approval_timeout)
    )
    orchestrator = Orchestrator(
        llm_client=llm_client,
        approval_handler=approval_handler,
        user_id=req.user_id,
    )

    if req.session_id:
        try:
            orchestrator.load_session(req.session_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    else:
        orchestrator.start_session()

    response_text = await run_in_threadpool(orchestrator.run, req.message)
    return JSONResponse({
        "session_id": orchestrator.session_id,
        "response": response_text,
    })


@app.get("/api/sessions")
def sessions_list(limit: int = 50, user_id: str | None = None):
    """JSON list of sessions for programmatic access (dashboards, scripts).

    The HTML ``/traces`` page already exposes a session list; this endpoint
    returns the same data as JSON. Optional ``user_id`` filter scopes the
    result to a single user. ``limit`` is clamped to 1..500.
    """
    safe_limit = max(1, min(int(limit), 500))
    conn = get_connection()
    try:
        if user_id:
            rows = conn.execute(
                """SELECT s.session_id, s.user_id, s.started_at, s.status,
                          COUNT(t.trace_id) AS event_count
                   FROM app_sessions s
                   LEFT JOIN app_trace_events t ON t.session_id = s.session_id
                   WHERE s.user_id = ?
                   GROUP BY s.session_id, s.user_id, s.started_at, s.status
                   ORDER BY s.started_at DESC
                   LIMIT ?""",
                (user_id, safe_limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT s.session_id, s.user_id, s.started_at, s.status,
                          COUNT(t.trace_id) AS event_count
                   FROM app_sessions s
                   LEFT JOIN app_trace_events t ON t.session_id = s.session_id
                   GROUP BY s.session_id, s.user_id, s.started_at, s.status
                   ORDER BY s.started_at DESC
                   LIMIT ?""",
                (safe_limit,),
            ).fetchall()
        return JSONResponse(
            {
                "limit": safe_limit,
                "user_id": user_id,
                "sessions": [dict(r) for r in rows],
            }
        )
    finally:
        conn.close()


@app.get("/api/sessions/{session_id}/messages")
def session_messages(session_id: str):
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT role, content, timestamp FROM app_messages
               WHERE session_id = ? ORDER BY timestamp ASC""",
            (session_id,),
        ).fetchall()
        return JSONResponse({"session_id": session_id, "messages": [dict(r) for r in rows]})
    finally:
        conn.close()


@app.get("/memory-inspector", response_class=HTMLResponse)
def memory_inspector_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="memory_inspector.html",
        context={"request": request},
    )


@app.get("/api/users/{user_id}/memory")
def user_memory_api(user_id: str):
    ltm = LongTermMemory()
    try:
        facts = ltm.get_all_facts(user_id)
        return JSONResponse({"user_id": user_id, "facts": facts})
    finally:
        ltm.close()


@app.delete("/api/users/{user_id}/memory/{fact_id}")
def delete_user_fact(user_id: str, fact_id: str):
    """Delete one long-term fact so future sessions stop injecting it.

    Scoped to ``user_id`` on purpose: a fact id belonging to a different
    user resolves to 404 instead of deleting across users.
    """
    ltm = LongTermMemory()
    try:
        deleted = ltm.delete_fact(fact_id, user_id=user_id)
    finally:
        ltm.close()
    if not deleted:
        raise HTTPException(status_code=404, detail="Fact not found for this user.")
    return JSONResponse({"deleted": fact_id, "user_id": user_id})


@app.get("/approval-queue", response_class=HTMLResponse)
def approval_queue(request: Request):
    csrf_token = secrets.token_urlsafe(32)
    response = templates.TemplateResponse(
        request=request,
        name="approval_queue.html",
        context={
            "request": request,
            "pending_approvals": _build_pending_rows(),
            "session_count": len(_load_sessions()),
            "csrf_token": csrf_token,
        },
    )
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=True, samesite="lax")
    return response


@app.post("/approvals/{approval_id}/approve")
def approve_action(approval_id: str, request: Request, csrf_token: str = Form("")):
    if not _csrf_ok(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    if not resolve_approval(approval_id, "approved"):
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved.")
    return RedirectResponse(url="/approval-queue", status_code=303)


@app.post("/approvals/{approval_id}/deny")
def deny_action(approval_id: str, request: Request, csrf_token: str = Form("")):
    if not _csrf_ok(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    if not resolve_approval(approval_id, "denied"):
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved.")
    return RedirectResponse(url="/approval-queue", status_code=303)


@app.get("/traces", response_class=HTMLResponse)
def traces_index(request: Request):
    sessions = _load_sessions()
    selected_session_id = sessions[0]["session_id"] if sessions else None
    selected_trace = get_session_trace(selected_session_id) if selected_session_id else []
    return templates.TemplateResponse(
        request=request,
        name="trace_replay.html",
        context={
            "request": request,
            "sessions": sessions,
            "selected_session_id": selected_session_id,
            "selected_trace": selected_trace,
        },
    )


@app.get("/traces/{session_id}", response_class=HTMLResponse)
def trace_replay(request: Request, session_id: str):
    sessions = _load_sessions()
    selected_trace = get_session_trace(session_id)
    if not selected_trace and not any(session["session_id"] == session_id for session in sessions):
        raise HTTPException(status_code=404, detail="Session not found.")
    return templates.TemplateResponse(
        request=request,
        name="trace_replay.html",
        context={
            "request": request,
            "sessions": sessions,
            "selected_session_id": session_id,
            "selected_trace": selected_trace,
        },
    )


@app.get("/api/traces/{session_id}")
def trace_api(session_id: str):
    return JSONResponse({"session_id": session_id, "events": get_session_trace(session_id)})
