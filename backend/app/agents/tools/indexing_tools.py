"""
ADK tools — indexing pipeline control.

Exposes the indexing pipeline's phases as ADK tools so the indexing agent can
drive a run. The agent decides *when* each phase runs and reports progress; the
heavy lifting lives in the pipeline modules and the indexing service.

As with the retrieval tools, collaborators are supplied via a module-level
context bound once before the agent runs.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class _IndexingToolContext:
    """Holds the indexing service the indexing tools delegate to."""

    indexing_service: object  # IndexingService — typed loosely to avoid a cycle


_context: _IndexingToolContext | None = None


def bind_indexing_tools(indexing_service: object) -> None:
    """Bind the indexing service used by the indexing tools."""
    global _context
    _context = _IndexingToolContext(indexing_service=indexing_service)


def _require_context() -> _IndexingToolContext:
    """Return the bound context or fail loudly if misconfigured."""
    if _context is None:
        raise RuntimeError(
            "Indexing tools used before bind_indexing_tools() was called."
        )
    return _context


async def get_indexing_status(session_id: str) -> dict:
    """Return the live status and progress of an indexing session.

    Args:
        session_id: The id of the indexing session to inspect.

    Returns:
        A dict with the session status, phase, and file/function counters.
    """
    ctx = _require_context()
    status = await ctx.indexing_service.get_status(session_id)  # type: ignore[attr-defined]
    return status


async def summarise_indexing_result(session_id: str) -> dict:
    """Summarise a completed indexing session for the developer.

    Args:
        session_id: The id of the indexing session to summarise.

    Returns:
        A dict describing how many files and functions were documented.
    """
    ctx = _require_context()
    status = await ctx.indexing_service.get_status(session_id)  # type: ignore[attr-defined]
    return {
        "session_id": session_id,
        "status": status.get("status"),
        "files_documented": status.get("processed_files"),
        "functions_documented": status.get("processed_functions"),
    }
