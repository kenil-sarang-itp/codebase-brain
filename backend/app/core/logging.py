"""
Structured logging setup.

Every process (API, workers) calls `configure_logging()` once at startup. Logs
are emitted as single-line key=value records which are easy to grep locally and
trivial to ship to a log aggregator in production.

A `ContextVar` carries a per-request / per-job `trace_id` so log lines from the
same logical operation can be correlated even across the async event loop.
"""

from __future__ import annotations

import logging
import sys
from contextvars import ContextVar

# Holds the current logical operation id (HTTP request id or RQ job id).
# ContextVar is async-safe: each task sees its own value.
_trace_id_ctx: ContextVar[str] = ContextVar("trace_id", default="-")


def set_trace_id(trace_id: str) -> None:
    """Bind a trace id to the current execution context."""
    _trace_id_ctx.set(trace_id)


def get_trace_id() -> str:
    """Return the trace id bound to the current execution context."""
    return _trace_id_ctx.get()


class _TraceIdFilter(logging.Filter):
    """Injects the current trace id into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        record.trace_id = get_trace_id()
        return True


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger. Idempotent — safe to call more than once.

    Args:
        level: Minimum log level name (e.g. "INFO", "DEBUG").
    """
    root = logging.getLogger()
    if getattr(root, "_codebase_brain_configured", False):
        return  # already configured in this process

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_TraceIdFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt=(
                "%(asctime)s level=%(levelname)s trace=%(trace_id)s "
                "logger=%(name)s | %(message)s"
            ),
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Tame noisy third-party loggers.
    for noisy in ("httpx", "urllib3", "asyncio", "qdrant_client"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root._codebase_brain_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """Return a named logger. Thin wrapper kept for a single import point."""
    return logging.getLogger(name)
