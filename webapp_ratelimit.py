"""In-memory rate limiting for the webapp.

Two complementary guards:

* ``TokenBucket``  - a sliding-window-per-minute cap on POST ``/api/chat``
  calls (configurable via ``CHAT_RATE_PER_MIN``). Disabled when 0.
* ``ConcurrentStreamGuard`` - a per-IP cap on simultaneously open SSE
  streams on ``/api/stream`` so a client cannot pin a worker per request
  (configurable via ``SSE_MAX_PER_IP``). Disabled when 0.

Both keep state in-process (one ``RL_STATE`` dict). That is fine for the
single-process demo deployment described in the docs; for horizontal scale
the limiter would need to move to Redis (the project already depends on it).
"""

from collections import deque
import threading
import time


class TokenBucket:
    """Per-key sliding-window-per-minute cap.

    Records timestamps of recent calls and rejects when more than
    ``max_calls`` have landed in the last ``window_seconds``.
    """

    def __init__(self, max_calls, window_seconds=60):
        self._max = max(0, int(max_calls))
        self._window = float(window_seconds)
        self._lock = threading.Lock()
        self._calls = {}

    def allow(self, key):
        if self._max == 0:
            return True
        now = time.time()
        cutoff = now - self._window
        with self._lock:
            dq = self._calls.setdefault(key, deque())
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) >= self._max:
                return False
            dq.append(now)
            return True

    def reset(self, key=None):
        with self._lock:
            if key is None:
                self._calls.clear()
            else:
                self._calls.pop(key, None)


class ConcurrentStreamGuard:
    """Per-key cap on simultaneously open streams.

    Use ``with guard.acquire(key):`` to fail-soft when the cap is hit. The
    body of the ``with`` is only executed when the slot was obtained;
    otherwise the ``acquire`` raises ``StreamLimitExceeded``.
    """

    class StreamLimitExceeded(Exception):
        pass

    def __init__(self, max_streams):
        self._max = max(0, int(max_streams))
        self._lock = threading.Lock()
        self._open = {}
        self._cvs = {}

    def _cv_for(self, key):
        cv = self._cvs.get(key)
        if cv is None:
            cv = threading.Condition(self._lock)
            self._cvs[key] = cv
        return cv

    def acquire(self, key):
        if self._max == 0:
            return _NullCtx()
        cv = self._cv_for(key)
        with cv:
            while self._open.get(key, 0) >= self._max:
                cv.wait(timeout=0.1)
                if self._open.get(key, 0) >= self._max:
                    raise self.StreamLimitExceeded(
                        f"Too many concurrent streams for client (limit {self._max})"
                    )
            self._open[key] = self._open.get(key, 0) + 1
        return _StreamSlot(self, key)

    def release(self, key):
        cv = self._cvs.get(key)
        with self._lock:
            self._open[key] = max(0, self._open.get(key, 0) - 1)
        if cv is not None:
            with cv:
                cv.notify()


class _StreamSlot:
    def __init__(self, guard, key):
        self._guard = guard
        self._key = key

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self._guard.release(self._key)
        return False


class _NullCtx:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


# Process-wide state. Imported by webapp.py.
RL_STATE = {
    "chat_bucket": TokenBucket(0),       # configured at import-time below
    "stream_guard": ConcurrentStreamGuard(0),
}


def configure(chat_per_min, sse_max_per_ip):
    """Reset process-wide rate limiters to the current config values.

    Called once on webapp startup. Idempotent.
    """
    RL_STATE["chat_bucket"] = TokenBucket(chat_per_min)
    RL_STATE["stream_guard"] = ConcurrentStreamGuard(sse_max_per_ip)
