"""
Memory persistence — short-term history, long-term profiles, query logs.

Implements the spec's persistent-memory feature:
    * Short-term: `conversation_history` — recent messages per session.
    * Long-term:  `developer_profiles` — durable personalisation per developer.
    * Audit:      `query_logs` — every query, powering the /query-logs view.
"""

from __future__ import annotations

from sqlalchemy import desc, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ConversationHistory, DeveloperProfile, QueryLog
from app.db.repositories.base import BaseRepository


class MemoryRepository(BaseRepository[ConversationHistory]):
    """Repository for conversation history, developer profiles, and query logs."""

    model = ConversationHistory

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------- conversation history
    async def add_message(
        self,
        *,
        session_id: str,
        developer_id: str,
        role: str,
        message: str,
        tokens_used: int = 0,
    ) -> ConversationHistory:
        """Append one message to a session's short-term memory."""
        row = ConversationHistory(
            session_id=session_id,
            developer_id=developer_id,
            role=role,
            message=message,
            tokens_used=tokens_used,
        )
        return await self.add(row)

    async def get_recent_history(
        self, session_id: str, limit: int = 10
    ) -> list[ConversationHistory]:
        """Return the last `limit` messages for a session in chronological order.

        We fetch newest-first (indexed) for efficiency, then reverse so the
        caller receives natural oldest→newest conversation order.
        """
        stmt = (
            select(ConversationHistory)
            .where(ConversationHistory.session_id == session_id)
            .order_by(desc(ConversationHistory.timestamp))
            .limit(limit)
        )
        result = await self._session.execute(stmt)
        rows = list(result.scalars().all())
        rows.reverse()
        return rows

    # --------------------------------------------------- developer profiles
    async def get_profile(self, developer_id: str) -> DeveloperProfile | None:
        """Return a developer's long-term profile, or None if not yet built."""
        return await self._session.get(DeveloperProfile, developer_id)

    async def upsert_profile(
        self,
        *,
        developer_id: str,
        common_modules: list[str] | None = None,
        question_style: str | None = None,
        preferred_depth: str | None = None,
        profile_summary: str | None = None,
    ) -> None:
        """Create or update a developer profile and bump the interaction count.

        Uses ON CONFLICT so the very first interaction creates the row and
        every later one updates it — no separate "exists?" check needed.
        """
        stmt = pg_insert(DeveloperProfile).values(
            developer_id=developer_id,
            common_modules=common_modules or [],
            question_style=question_style,
            preferred_depth=preferred_depth,
            profile_summary=profile_summary,
            interaction_count=1,
        )
        # On conflict, only overwrite non-null incoming fields; always +1 count.
        update_set: dict = {
            "interaction_count": DeveloperProfile.interaction_count + 1,
        }
        if common_modules is not None:
            update_set["common_modules"] = stmt.excluded.common_modules
        if question_style is not None:
            update_set["question_style"] = stmt.excluded.question_style
        if preferred_depth is not None:
            update_set["preferred_depth"] = stmt.excluded.preferred_depth
        if profile_summary is not None:
            update_set["profile_summary"] = stmt.excluded.profile_summary

        stmt = stmt.on_conflict_do_update(
            index_elements=[DeveloperProfile.developer_id],
            set_=update_set,
        )
        await self._session.execute(stmt)

    # ----------------------------------------------------------- query logs
    async def add_query_log(self, log: QueryLog) -> QueryLog:
        """Persist one query-log entry."""
        return await self.add(log)

    async def list_query_logs(
        self,
        *,
        developer_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[QueryLog]:
        """Return recent query logs, optionally filtered to one developer."""
        stmt = select(QueryLog).order_by(desc(QueryLog.created_at))
        if developer_id:
            stmt = stmt.where(QueryLog.developer_id == developer_id)
        stmt = stmt.limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
