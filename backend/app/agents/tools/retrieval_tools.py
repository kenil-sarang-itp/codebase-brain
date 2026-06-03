"""
ADK tools — knowledge-base retrieval.

Google ADK agents act by calling *tools*. This module exposes the retrieval
pipeline as ADK-compatible tool functions.

An ADK `FunctionTool` is built from a plain async function whose signature and
docstring the framework reads to build the tool schema the LLM sees. These
functions are therefore written with clear type hints and docstrings.

Because a tool function cannot easily receive injected services, the concrete
collaborators are supplied through a small module-level context object set up
once at agent-construction time. This keeps the tool functions themselves
thin and pure.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.logging import get_logger
from app.services.retrieval_service import RetrievalService

logger = get_logger(__name__)


@dataclass
class _RetrievalToolContext:
    """Holds the collaborators the retrieval tools need.

    Set once via `bind_retrieval_tools` before the agent runs. This is the
    pragmatic ADK pattern for giving stateless tool functions access to
    application services.
    """

    retrieval_service: RetrievalService


# Module-level context, populated at agent build time.
_context: _RetrievalToolContext | None = None


def bind_retrieval_tools(retrieval_service: RetrievalService) -> None:
    """Bind the retrieval service the tool functions will use."""
    global _context
    _context = _RetrievalToolContext(retrieval_service=retrieval_service)


def _require_context() -> _RetrievalToolContext:
    """Return the bound context or fail loudly if the agent was misconfigured."""
    if _context is None:
        raise RuntimeError(
            "Retrieval tools used before bind_retrieval_tools() was called."
        )
    return _context


async def search_knowledge_base(query: str) -> dict:
    """Search the codebase knowledge base for documentation relevant to a query.

    Use this to find functions, modules, or architecture docs that relate to
    the developer's question. Returns the most relevant sources with their file
    paths, line numbers, documentation, and code.

    Args:
        query: A natural-language description of what to look for.

    Returns:
        A dict with a "sources" list; each source has file, name, lines, the
        documentation text, the code, and a relevance score.
    """
    ctx = _require_context()
    sources = await ctx.retrieval_service.retrieve(query)
    return {
        "source_count": len(sources),
        "sources": [
            {
                "citation": s.citation,
                "file": s.file_path,
                "name": s.name,
                "level": s.level_label,
                "start_line": s.start_line,
                "end_line": s.end_line,
                "documentation": s.doc_text,
                "code": s.code_text,
                "relevance": round(s.score, 4),
            }
            for s in sources
        ],
    }


async def search_architecture(query: str) -> dict:
    """Search only the architecture / data-flow (Level-3) documentation.

    Use this for high-level "how does the system do X" questions where a
    data-flow overview is more useful than a single function's docs.

    Args:
        query: A natural-language description of the flow or behaviour.

    Returns:
        A dict with a "sources" list of Level-3 architecture documents.
    """
    ctx = _require_context()
    sources = await ctx.retrieval_service.retrieve(query, level_filter=3)
    return {
        "source_count": len(sources),
        "sources": [
            {
                "citation": s.citation,
                "file": s.file_path,
                "name": s.name,
                "documentation": s.doc_text,
                "relevance": round(s.score, 4),
            }
            for s in sources
        ],
    }
