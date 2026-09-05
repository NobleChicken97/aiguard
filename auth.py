"""Phase 1 authentication: bcrypt passwords + HMAC-signed session cookies.

Design (per the approved Phase 1 plan):
- Passwords are hashed with ``bcrypt`` directly, not via passlib:
  passlib 1.7.4's bcrypt handler is broken against bcrypt>=4.1 (its
  checksum path raises even for short passwords, verified live in this
  repo's env), so the extra layer buys nothing but a trap.
- Sessions are stateless HMAC cookies, ``user_id:expiry:signature`` —
  the same philosophy as the existing CSRF double-submit token: no
  server-side session store, tampering detected with compare_digest.
  The key comes from ``SESSION_SECRET``; when unset, an ephemeral
  per-process secret is generated (logins die on restart — safe for
  local dev, unusable for production Farms, and loudly logged).
- Cross-user reads resolve to 404 (never 403), matching the existing
  ``delete_fact`` convention, so account/session IDs cannot be probed.
"""

import hashlib
import hmac
import secrets
import time

import bcrypt
from fastapi import HTTPException, Request

import config
from app_logging import get_logger
from app_util import new_uuid as _uuid, now_utc as _now
from db.database import get_connection

logger = get_logger("auth")

SESSION_COOKIE = "aiguard_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600  # 7 days

_secret = None


def _secret_key():
    """Process-wide HMAC key, resolved lazily so tests can pin it."""
    global _secret
    if _secret is None:
        if config.SESSION_SECRET:
            _secret = config.SESSION_SECRET.encode()
        else:
            _secret = secrets.token_bytes(32)
            logger.warning(
                "SESSION_SECRET unset — using an ephemeral per-process secret. "
                "Logins will die on restart; set SESSION_SECRET in production."
            )
    return _secret


# --------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------

def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password, pw_hash):
    try:
        return bcrypt.checkpw(password.encode(), (pw_hash or "").encode())
    except Exception:
        return False


# --------------------------------------------------------------------------
# Session cookies
# --------------------------------------------------------------------------

def _sign_payload(payload):
    return hmac.new(_secret_key(), payload.encode(), hashlib.sha256).hexdigest()


def sign_session(user_id, ttl=SESSION_TTL_SECONDS):
    expiry = int(time.time()) + int(ttl)
    payload = f"{user_id}:{expiry}"
    return f"{payload}:{_sign_payload(payload)}"


def verify_session(value):
    """Return the user_id for a valid, unexpired cookie, else None."""
    try:
        user_id, expiry_s, sig = (value or "").split(":")
        if int(expiry_s) < int(time.time()):
            return None
        if not hmac.compare_digest(_sign_payload(f"{user_id}:{expiry_s}"), sig):
            return None
        return user_id
    except Exception:
        return None


# --------------------------------------------------------------------------
# Users (app_users table)
# --------------------------------------------------------------------------

def create_user(email, password, role="user"):
    """Insert a user; raises ValueError on bad input or duplicate email."""
    email = (email or "").strip().lower()
    if "@" not in email or not email:
        raise ValueError("Enter a valid email address.")
    if not password or len(password) < 8:
        raise ValueError("Password must be at least 8 characters.")
    if role not in ("user", "admin"):
        raise ValueError("Role must be 'user' or 'admin'.")

    user_id = _uuid()
    conn = get_connection()
    try:
        try:
            conn.execute(
                """INSERT INTO app_users (user_id, email, pw_hash, role, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (user_id, email, hash_password(password), role, _now()),
            )
        except Exception:
            # The only reachable constraint here (besides a uuid PK
            # collision, which will not happen) is the email UNIQUE.
            raise ValueError("That email is already registered.")
        conn.commit()
        return user_id
    finally:
        conn.close()


def authenticate(email, password):
    """Return the user row dict for valid credentials, else None.

    The failure shape is identical for unknown-email and wrong-password
    so login cannot be used to enumerate accounts.
    """
    email = (email or "").strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, email, pw_hash, role FROM app_users WHERE email = ?",
            (email,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    if not verify_password(password or "", row["pw_hash"]):
        return None
    return {"user_id": row["user_id"], "email": row["email"], "role": row["role"]}


def get_user(user_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT user_id, email, role FROM app_users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"user_id": row["user_id"], "email": row["email"], "role": row["role"]}


def owns_session(user_id, session_id):
    """True when the session row belongs to the user (admin bypass is the
    caller's decision, kept explicit at each endpoint)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 AS ok FROM app_sessions WHERE session_id = ? AND user_id = ?",
            (session_id, user_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# --------------------------------------------------------------------------
# FastAPI dependency
# --------------------------------------------------------------------------

def require_user(request: Request):
    """Resolve the session cookie to a user dict; 401 when missing/invalid.

    Use as ``user = require_user(request)`` at the top of every stateful
    endpoint. ``/health`` stays open (load-balancer probe).
    """
    user_id = verify_session(request.cookies.get(SESSION_COOKIE, ""))
    user = get_user(user_id) if user_id else None
    if user is None:
        raise HTTPException(status_code=401, detail="Login required.")
    return user


def get_optional_user(request: Request):
    """Same as require_user but returns None instead of raising 401.

    For HTML pages, which redirect to /login on their own; JSON APIs
    use require_user so clients get a machine-readable 401.
    """
    try:
        return require_user(request)
    except HTTPException:
        return None
