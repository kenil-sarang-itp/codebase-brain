"""
Query-logs and health routes.

    * `GET /query-logs` — returns the authenticated developer's recent query
      audit log (powers the frontend's logs view).
    * `GET /health` — unauthenticated liveness/readiness probe used by Docker
      health checks and load balancers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config.settings import get_settings
from app.core.logging import get_logger
from app.db.models import User
from app.db.qdrant_store import get_qdrant_store
from app.db.repositories.memory_repository import MemoryRepository
from app.db.session import get_db_session
from app.external.provider_factory import (
    get_llm_provider,
    get_reranker_provider,
)
from app.schemas.api_schemas import (
    HealthResponse,
    QueryLogEntry,
    QueryLogResponse,
)

logger = get_logger(__name__)

router = APIRouter(tags=["logs-and-health"])


@router.get("/query-logs", response_model=QueryLogResponse)
async def query_logs(
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_db_session),
) -> QueryLogResponse:
    """Return the current developer's recent query log, newest first."""
    memory_repo = MemoryRepository(session)
    rows = await memory_repo.list_query_logs(
        developer_id=current_user.id, limit=limit, offset=offset
    )
    entries = [
        QueryLogEntry(
            id=row.id,
            question=row.question,
            query_type=row.query_type,
            answer_preview=row.answer_preview,
            num_sources=row.num_sources,
            latency_ms=row.latency_ms,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return QueryLogResponse(entries=entries, count=len(entries))


@router.get("/health", response_model=HealthResponse)
async def health(
    session: AsyncSession = Depends(get_db_session),
) -> HealthResponse:
    """Report service health — DB and Qdrant connectivity, active providers.

    Unauthenticated by design so infrastructure health checks can call it.
    Each dependency is probed defensively; one being down still yields a
    structured response rather than an exception.
    """
    # Database probe — a trivial round trip.
    db_ok = True
    try:
        from sqlalchemy import text

        await session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False

    # Qdrant probe.
    qdrant_ok = await get_qdrant_store().health_check()

    settings = get_settings()
    overall = "ok" if (db_ok and qdrant_ok) else "degraded"

    return HealthResponse(
        status=overall,
        database=db_ok,
        qdrant=qdrant_ok,
        llm_provider=get_llm_provider().model_name,
        reranker=type(get_reranker_provider()).__name__,
    )


@router.get("/admin/vector-stats")
async def vector_stats(
    _current_user: User = Depends(get_current_user),
) -> dict:
    """Return Qdrant collection stats — useful for diagnosing empty results."""
    qdrant = get_qdrant_store()
    count = await qdrant.count_points()
    return {
        "collection": "codebase_knowledge",
        "total_points": count,
        "message": (
            "No vectors stored — re-index the repository."
            if count == 0
            else f"{count} vectors indexed and searchable."
        ),
    }
