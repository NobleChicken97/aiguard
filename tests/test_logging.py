"""Tests for the centralized logging setup."""

import io
import logging

from app_logging import configure_logging, get_logger


def test_configure_logging_idempotent():
    """Calling configure_logging twice does not stack handlers."""
    configure_logging(level="INFO")
    n1 = len(logging.getLogger().handlers)
    configure_logging(level="DEBUG")
    n2 = len(logging.getLogger().handlers)
    assert n1 == n2, "configure_logging should be idempotent"


def test_configure_logging_writes_to_stream():
    buf = io.StringIO()
    configure_logging(level="INFO", stream=buf)
    log = get_logger("test.module")
    log.info("hello world")
    text = buf.getvalue()
    assert "hello world" in text
    assert "INFO" in text
    assert "test.module" in text


def test_configure_logging_respects_level():
    buf = io.StringIO()
    configure_logging(level="WARNING", stream=buf)
    log = get_logger("test.warn")
    log.info("should not appear")
    log.warning("should appear")
    text = buf.getvalue()
    assert "should not appear" not in text
    assert "should appear" in text


def test_get_logger_returns_named_logger():
    log1 = get_logger("alpha")
    log2 = get_logger("alpha")
    log3 = get_logger("beta")
    assert log1 is log2
    assert log1 is not log3
    assert log1.name == "alpha"
    assert log3.name == "beta"
