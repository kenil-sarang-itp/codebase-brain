"""
Indexing routes — `/index` and `/index-status`.

    * `POST /index` — start indexing a repository. The heavy six-phase pipeline
      must NOT run on the request thread (spec: FastAPI delegates heavy work),
      so this endpoint creates a session row, enqueues an RQ job, and returns
      immediately with the session id.
    * `GET /index-status/{session_id}` — poll live progress.

The RQ enqueue is what decouples the API from the long-running pipeline; an RQ
worker picks the job up and runs `IndexingService`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.api.dependencies import get_current_user
from app.db.models import User
from app.db.repositories.indexing_repository import IndexingRepository
from app.db.session import get_db_session
from app.schemas.api_schemas import (
    IndexRequest,
    IndexResponse,
    IndexStatusResponse,
)
from app.services.service_factory import build_indexing_service
from app.workers.queue import enqueue_initial_indexing

logger = get_logger(__name__)

router = APIRouter(tags=["indexing"])


@router.post("/index", response_model=IndexResponse)
async def start_indexing(
    payload: IndexRequest,
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> IndexResponse:
    """Queue a repository for indexing and return its session id.

    Returns immediately — indexing runs asynchronously in an RQ worker. Poll
    `GET /index-status/{session_id}` to watch progress.
    """
    # Create the session row first so the client has something to poll.
    indexing_repo = IndexingRepository(session)
    indexing_session = await indexing_repo.create_session(repo_url=payload.repo)
    # Flush so the generated session_id is available before the response.
    await session.flush()
    session_id = indexing_session.session_id

    # Enqueue the heavy work onto the RQ job queue.
    enqueue_initial_indexing(
        session_id=session_id, repo=payload.repo, ref=payload.ref
    )

    logger.info("Queued indexing session %s for repo %s", session_id, payload.repo)
    return IndexResponse(
        session_id=session_id,
        status="queued",
        message=(
            "Indexing has been queued. Poll /index-status/"
            f"{session_id} for progress."
        ),
    )


@router.get(
    "/index-status/{session_id}", response_model=IndexStatusResponse
)
async def index_status(
    session_id: str,
    _current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> IndexStatusResponse:
    """Return the live status and progress of an indexing session."""
    # The status read needs an IndexingService; build it without a repo source
    # (status only touches the database).
    indexing_service = build_indexing_service(session)
    status_dict = await indexing_service.get_status(session_id)
    return IndexStatusResponse(**status_dict)
