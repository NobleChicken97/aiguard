import asyncio
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import config
from agent.llm_client import build_llm_client
from agent.memory import LongTermMemory
from agent.orchestrator import Orchestrator
from agent.trace import get_session_trace
from approval.gate import (
    AsyncApprovalHandler,
    AutoApproveHandler,
    PendingApproval,
    get_approval_status,
    get_pending_approvals,
    owns_approval,
    resolve_approval,
)
from auth import (
    SESSION_COOKIE,
    authenticate,
    create_user,
    get_optional_user,
    owns_session,
    require_user,
    sign_session,
)
from db.database import get_connection, initialize_db
from tools.query_builder import (
    QueryBuilderError,
    QueryBuilderRequest,
    get_builder_schema,
    run_builder_query,
)
from webapp_ratelimit import RL_STATE, configure as configure_ratelimit
from webapp_metrics import record_http, render_prometheus
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
    # Legacy client field — IGNORED. Identity always comes from the session
    # cookie (Phase 1): trusting a client-supplied user_id would let any
    # caller read another user's sessions.
    user_id: str = "web_user"
    session_id: str | None = None
    # When true, use AutoApproveHandler so the chat returns immediately
    # (useful for scripted demos and tests). Default false: risky actions
    # pause with a 202 + approval id (Phase 3 — no thread is ever held
    # waiting); the frontend polls and resumes via /api/chat/resume.
    auto_approve: bool = False


class ResumeRequest(BaseModel):
    session_id: str


def _pending_response(pending: PendingApproval):
    """202 payload for a paused turn (shared by chat and resume)."""
    return JSONResponse(
        {
            "status": "pending_approval",
            "approval_id": pending.approval_id,
            "session_id": pending.session_id,
            "detail": (
                "Approval needed before continuing: "
                f"{pending.risk_reason} Approve in the Approval Queue — "
                "this chat resumes automatically once decided."
            ),
        },
        status_code=202,
    )


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


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """Time watched endpoints into the in-process /metrics counters."""
    started = time.monotonic()
    response = await call_next(request)
    record_http(
        request.method,
        request.url.path,
        getattr(response, "status_code", 0),
        time.monotonic() - started,
    )
    return response


@app.get("/health")
def health_check():
    """Lightweight health endpoint used by Docker and load balancers."""
    conn = get_connection()
    try:
        rows = conn.execute("SELECT COUNT(*) AS cnt FROM app_sessions").fetchone()
        db_ok = True
    except Exception:
        rows = {"cnt": 0}
        db_ok = False
    finally:
        conn.close()

    return JSONResponse({
        "status": "ok" if db_ok else "unhealthy",
        "db_connected": db_ok,
        "session_count": rows["cnt"] if rows else 0,
    })


@app.get("/health/detailed")
def health_detailed(request: Request):
    """Readiness-style deep check for monitoring (scrape the `status` field).

    Always answers 200 by design — load balancers keep using the fast
    `/health`; humans and dashboards read per-check detail here. Redis is
    informational (optional by design, never degrades status); the LLM
    check reports configuration only, never burning quota on a probe call.
    """
    require_user(request)
    checks: dict = {}
    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1 AS ok").fetchone()
            checks["db"] = {"ok": True}
        finally:
            conn.close()
    except Exception as e:
        checks["db"] = {"ok": False, "error": str(e)[:120]}
    try:
        import redis

        rc = redis.Redis.from_url(
            config.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        rc.ping()
        checks["redis"] = {"ok": True, "mode": "optional"}
    except Exception:
        checks["redis"] = {"ok": False, "mode": "optional-fallback"}
    checks["llm"] = {"configured": _llm_configured()}
    status = "ok" if (checks["db"].get("ok") and checks["llm"].get("configured")) else "degraded"
    return JSONResponse({"status": status, "checks": checks})


@app.get("/metrics")
def metrics_endpoint(request: Request):
    """Prometheus exposition (DB aggregates + in-process latency)."""
    require_user(request)
    return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4")


def _compute_stats(user_id=None, is_admin=False):
    """Aggregate live system statistics for the monitoring dashboard.

    All queries are dialect-safe (SQLite + PostgreSQL). Guardrail verdicts
    are counted in Python from recent trace events because verdicts live
    inside JSON payloads, and JSON extraction functions differ per dialect.

    Counters are global aggregates; row-level data (recent sessions) is
    scoped to the requesting user unless they are an admin.
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

            recent_sql = """SELECT s.session_id, s.user_id, s.started_at, s.status,
                          COUNT(t.trace_id) AS event_count
                   FROM app_sessions s
                   LEFT JOIN app_trace_events t ON t.session_id = s.session_id
                   {where}
                   GROUP BY s.session_id, s.user_id, s.started_at, s.status
                   ORDER BY s.started_at DESC
                   LIMIT 10"""
            if is_admin or user_id is None:
                recent_rows = conn.execute(recent_sql.format(where="")).fetchall()
            else:
                recent_rows = conn.execute(
                    recent_sql.format(where="WHERE s.user_id = ?"), (user_id,)
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
            "pending_approvals": len(
                get_pending_approvals()
                if is_admin
                else get_pending_approvals(for_user_id=user_id)
            ),
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
def stats_api(request: Request):
    """One-shot snapshot of system statistics (row-level data scoped)."""
    user = require_user(request)
    return JSONResponse(_compute_stats(user["user_id"], user["role"] == "admin"))


@app.get("/api/stream")
async def stats_stream(request: Request):
    """Server-Sent Events stream pushing a fresh stats snapshot every 2s.

    DB aggregation runs in the threadpool so the event loop is never
    blocked while clients stay connected. The per-user concurrent stream
    cap keeps one client from pinning a worker per request.
    """
    user = require_user(request)
    stream_key = f"user:{user['user_id']}"
    try:
        slot = RL_STATE["stream_guard"].acquire(stream_key)
    except RL_STATE["stream_guard"].StreamLimitExceeded as e:
        logger.info("sse stream limit hit user=%s", user["user_id"])
        raise HTTPException(status_code=429, detail=str(e))
    with slot:
        async def event_generator():
            while True:
                stats = await run_in_threadpool(
                    _compute_stats, user["user_id"], user["role"] == "admin"
                )
                yield f"data: {json.dumps(stats)}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    user = get_optional_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"request": request, "current_user": user},
    )


@app.get("/query-builder", response_class=HTMLResponse)
def query_builder_page(request: Request):
    user = get_optional_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="query_builder.html",
        context={"request": request, "current_user": user},
    )


@app.get("/api/query-builder/schema")
def query_builder_schema(request: Request):
    """Live schema metadata (allowed tables + columns) for the builder UI."""
    require_user(request)  # metadata only, but stateful surface stays login-gated
    return JSONResponse(get_builder_schema())


@app.post("/api/query-builder/run")
def query_builder_run(req: QueryBuilderRequest, request: Request):
    """Execute a visually-built SELECT through the guardrail layer.

    Read-only by construction; validation failures surface as 400s with the
    same operator-friendly messages shown in the UI. The run is attributed
    to the logged-in user in the audit table.
    """
    user = require_user(request)
    try:
        result = run_builder_query(req, user_id=user["user_id"])
    except QueryBuilderError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return JSONResponse(result)


def _load_sessions(limit=50, user_id=None, is_admin=False):
    conn = get_connection()
    try:
        base_sql = """SELECT s.session_id, s.user_id, s.started_at, s.status,
                      COUNT(t.trace_id) AS event_count
               FROM app_sessions s
               LEFT JOIN app_trace_events t ON t.session_id = s.session_id
               {where}
               GROUP BY s.session_id, s.user_id, s.started_at, s.status
               ORDER BY s.started_at DESC
               LIMIT ?"""
        if is_admin or user_id is None:
            rows = conn.execute(base_sql.format(where=""), (limit,)).fetchall()
        else:
            rows = conn.execute(
                base_sql.format(where="WHERE s.user_id = ?"), (user_id, limit)
            ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _build_pending_rows(user=None):
    scope = None if (user is None or user.get("role") == "admin") else user["user_id"]
    rows = []
    for item in get_pending_approvals(for_user_id=scope):
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


def _rate_key(request: Request, user) -> str:
    """Per-user rate-limit identity, IP fallback for pre-auth paths."""
    if user is not None:
        return f"user:{user['user_id']}"
    return f"ip:{_client_ip(request)}"


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


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_optional_user(request) is not None:
        return RedirectResponse(url="/chat", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"request": request, "error": None},
    )


@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, email: str = Form(""), password: str = Form("")):
    # Identical failure shape for unknown-email vs wrong-password (see
    # auth.authenticate) so login cannot enumerate accounts.
    user = authenticate(email, password)
    if user is None:
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"request": request, "error": "Invalid email or password."},
            status_code=401,
        )
    response = RedirectResponse(url="/chat", status_code=303)
    response.set_cookie(SESSION_COOKIE, sign_session(user["user_id"]), httponly=True, samesite="lax")
    return response


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    if get_optional_user(request) is not None:
        return RedirectResponse(url="/chat", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="register.html",
        context={"request": request, "error": None},
    )


@app.post("/register", response_class=HTMLResponse)
def register_submit(request: Request, email: str = Form(""), password: str = Form("")):
    try:
        user_id = create_user(email, password)
    except ValueError as e:
        return templates.TemplateResponse(
            request=request,
            name="register.html",
            context={"request": request, "error": str(e)},
            status_code=400,
        )
    response = RedirectResponse(url="/chat", status_code=303)
    response.set_cookie(SESSION_COOKIE, sign_session(user_id), httponly=True, samesite="lax")
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
def root():
    return RedirectResponse(url="/chat", status_code=303)


@app.get("/chat", response_class=HTMLResponse)
def chat_page(request: Request):
    user = get_optional_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="chat.html",
        context={"request": request, "llm_configured": _llm_configured(), "current_user": user},
    )


@app.post("/api/chat")
async def chat_api(req: ChatRequest, request: Request):
    user = require_user(request)
    rate_key = _rate_key(request, user)
    if not RL_STATE["chat_bucket"].allow(rate_key):
        logger.info("chat rate-limited key=%s", rate_key)
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
        else AsyncApprovalHandler()
    )
    orchestrator = Orchestrator(
        llm_client=llm_client,
        approval_handler=approval_handler,
        user_id=user["user_id"],
    )

    if req.session_id:
        try:
            orchestrator.load_session(req.session_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    else:
        orchestrator.start_session()

    result = await run_in_threadpool(orchestrator.run, req.message)
    if isinstance(result, PendingApproval):
        # Phase 3: the worker thread is already released; the frontend
        # polls the approval status and resumes when decided.
        return _pending_response(result)
    return JSONResponse({
        "session_id": orchestrator.session_id,
        "response": result,
    })


@app.get("/api/approval/{approval_id}/status")
def approval_status(approval_id: str, request: Request):
    """Instant pending/approved/denied lookup for frontend short-polling.

    An indexed PK read — no worker thread is ever held waiting for this.
    """
    user = require_user(request)
    decision, session_id, _ = get_approval_status(approval_id)
    if session_id is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    if user["role"] != "admin" and not owns_session(user["user_id"], session_id):
        raise HTTPException(status_code=404, detail="Approval request not found.")
    return JSONResponse({
        "approval_id": approval_id,
        "status": decision or "pending",
        "session_id": session_id,
    })


@app.post("/api/chat/resume")
async def chat_resume(req: ResumeRequest, request: Request):
    """Continue a paused turn after its approval was decided."""
    user = require_user(request)
    if user["role"] != "admin" and not owns_session(user["user_id"], req.session_id):
        raise HTTPException(status_code=404, detail="Session not found.")

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

    orchestrator = Orchestrator(
        llm_client=llm_client,
        approval_handler=AsyncApprovalHandler(),
        user_id=user["user_id"],
    )
    try:
        result = await run_in_threadpool(orchestrator.resume, req.session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if isinstance(result, PendingApproval):
        return _pending_response(result)
    return JSONResponse({
        "session_id": orchestrator.session_id,
        "response": result,
    })


@app.get("/api/sessions")
def sessions_list(request: Request, limit: int = 50, user_id: str | None = None):
    """JSON list of sessions for programmatic access (dashboards, scripts).

    Scoped to the caller: non-admins always see their own sessions (the
    ``user_id`` filter is admin-only, for the cross-user approval demo).
    ``limit`` is clamped to 1..500.
    """
    user = require_user(request)
    is_admin = user["role"] == "admin"
    user_id = user_id if (is_admin and user_id) else user["user_id"]
    safe_limit = max(1, min(int(limit), 500))
    conn = get_connection()
    try:
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
def session_messages(session_id: str, request: Request):
    user = require_user(request)
    if user["role"] != "admin" and not owns_session(user["user_id"], session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
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
    user = get_optional_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="memory_inspector.html",
        context={"request": request, "current_user": user},
    )


@app.get("/api/users/{user_id}/memory")
def user_memory_api(user_id: str, request: Request):
    caller = require_user(request)
    if caller["role"] != "admin" and user_id != caller["user_id"]:
        raise HTTPException(status_code=404, detail="User not found.")
    ltm = LongTermMemory()
    try:
        facts = ltm.get_all_facts(user_id)
        return JSONResponse({"user_id": user_id, "facts": facts})
    finally:
        ltm.close()


@app.delete("/api/users/{user_id}/memory/{fact_id}")
def delete_user_fact(user_id: str, fact_id: str, request: Request):
    """Delete one long-term fact so future sessions stop injecting it.

    Scoped to the caller: only the fact owner (or an admin) may delete.
    A fact id belonging to a different user resolves to 404 instead of
    deleting across users (or revealing the fact exists).
    """
    caller = require_user(request)
    if caller["role"] != "admin" and user_id != caller["user_id"]:
        raise HTTPException(status_code=404, detail="Fact not found for this user.")
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
    user = get_optional_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    csrf_token = secrets.token_urlsafe(32)
    is_admin = user["role"] == "admin"
    response = templates.TemplateResponse(
        request=request,
        name="approval_queue.html",
        context={
            "request": request,
            "pending_approvals": _build_pending_rows(user),
            "session_count": len(_load_sessions(user_id=user["user_id"], is_admin=is_admin)),
            "csrf_token": csrf_token,
            "current_user": user,
        },
    )
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=True, samesite="lax")
    return response


@app.post("/approvals/{approval_id}/approve")
def approve_action(approval_id: str, request: Request, csrf_token: str = Form("")):
    user = require_user(request)
    if not _csrf_ok(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    if user["role"] != "admin" and not owns_approval(user["user_id"], approval_id):
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved.")
    if not resolve_approval(approval_id, "approved"):
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved.")
    return RedirectResponse(url="/approval-queue", status_code=303)


@app.post("/approvals/{approval_id}/deny")
def deny_action(approval_id: str, request: Request, csrf_token: str = Form("")):
    user = require_user(request)
    if not _csrf_ok(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid or missing CSRF token.")
    if user["role"] != "admin" and not owns_approval(user["user_id"], approval_id):
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved.")
    if not resolve_approval(approval_id, "denied"):
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved.")
    return RedirectResponse(url="/approval-queue", status_code=303)


@app.get("/traces", response_class=HTMLResponse)
def traces_index(request: Request):
    user = get_optional_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    is_admin = user["role"] == "admin"
    sessions = _load_sessions(user_id=user["user_id"], is_admin=is_admin)
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
            "current_user": user,
        },
    )


@app.get("/traces/{session_id}", response_class=HTMLResponse)
def trace_replay(request: Request, session_id: str):
    user = get_optional_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    is_admin = user["role"] == "admin"
    sessions = _load_sessions(user_id=user["user_id"], is_admin=is_admin)
    if not is_admin and not owns_session(user["user_id"], session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
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
            "current_user": user,
        },
    )


@app.get("/api/traces/{session_id}")
def trace_api(session_id: str, request: Request):
    user = require_user(request)
    if user["role"] != "admin" and not owns_session(user["user_id"], session_id):
        raise HTTPException(status_code=404, detail="Session not found.")
    return JSONResponse({"session_id": session_id, "events": get_session_trace(session_id)})
