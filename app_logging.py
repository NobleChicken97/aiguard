"""Centralized logging setup.

The webapp and long-running tools use the standard ``logging`` module so
operators can route output to files, log aggregators, or stdout without
touching the application code. The CLI approval handler keeps its
``print()`` calls because those are intentionally user-facing prompts.

Call ``configure_logging(level=...)`` once at process start (the webapp
lifespan does this). A null-handler is installed by default so library
imports do not emit ``No handlers could be found`` warnings in tests.
"""

import json
import logging
import os
import sys


_DEFAULT_FORMAT = "%(asctime)s %(levelname)s %(name)s :: %(message)s"
_LOG_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


def _resolve_level(name):
    if not name:
        return logging.INFO
    return _LOG_LEVELS.get(name.upper(), logging.INFO)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: {"ts", "level", "logger", "msg"}.

    Selected with ``LOG_FORMAT=json`` for log aggregators; the default
    ``text`` format stays human-readable for local dev.
    """

    def format(self, record):
        return json.dumps({
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        })


def configure_logging(level=None, stream=None, format=None):
    """Install a single stream handler on the root logger.

    Idempotent: if a handler we've previously installed is still attached,
    it is removed first so callers can re-configure at runtime.
    """
    level_name = level or os.getenv("LOG_LEVEL", "INFO")
    format_name = (format or os.getenv("LOG_FORMAT", "text")).lower()
    handler = logging.StreamHandler(stream or sys.stderr)
    if format_name == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    handler.setLevel(_resolve_level(level_name))
    handler.name = "agentic_guardrails_default"

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "name", None) == "agentic_guardrails_default":
            root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(_resolve_level(level_name))

    # Quiet very chatty third-party loggers by default.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def get_logger(name):
    return logging.getLogger(name)
