"""Small shared helpers used across agent, approval, and db modules.

One definition of the UTC-ISO-8601 timestamp and uuid4 id helpers that
every persistence layer (sessions, messages, traces, tool calls, approval
requests, memory facts) writes with.
"""

import uuid
from datetime import datetime, timezone


def now_utc():
    """Current time as a timezone-aware ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def new_uuid():
    return str(uuid.uuid4())
