"""
Chat route — the `/chat` endpoint.

The primary developer-facing endpoint: a developer asks a question in plain
English and receives a grounded, cited answer. All orchestration (memory,
classification, retrieval→answer or validation) is delegated to `ChatService`;
this route only handles HTTP concerns and session-id management.

Authentication is required — `get_current_user` both protects the endpoint and
identifies the developer, which is what makes the persistent-memory feature
per-developer.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.core.logging import get_logger, set_trace_id
from app.db.models import User
from app.db.session import get_db_session
from app.schemas.api_schemas import ChatRequest, ChatResponse
from app.services.service_factory import build_chat_service

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
) -> ChatResponse:
    """Answer a developer's question about the indexed codebase.

    The request may include a `session_id` to continue an existing
    conversation; if omitted, a new session id is generated and returned so the
    client can keep using it for follow-ups (which is what gives the assistant
    short-term memory).
    """
    # Continue the given session, or start a new one.
    session_id = payload.session_id or f"sess-{uuid.uuid4()}"
    # Correlate all logs for this request under one trace id.
    set_trace_id(session_id)

    chat_service = build_chat_service(session)
    result = await chat_service.handle_chat(
        developer_id=current_user.id,
        session_id=session_id,
        question=payload.question,
    )

    return ChatResponse(
        answer=result.answer,
        query_type=result.query_type.value,
        citations=result.citations,
        session_id=session_id,
        latency_ms=result.latency_ms,
    )
