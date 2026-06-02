"""User persistence — all user-related database access."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.db.repositories.base import BaseRepository


class UserRepository(BaseRepository[User]):
    """Repository for the `users` table.

    Adds lookups by the unique natural keys (username, email) on top of the
    generic primary-key CRUD from `BaseRepository`.
    """

    model = User

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_by_username(self, username: str) -> User | None:
        """Return the user with this username, or None."""
        stmt = select(User).where(User.username == username)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Return the user with this email, or None."""
        stmt = select(User).where(User.email == email)
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def exists(self, *, username: str, email: str) -> bool:
        """True if any user already uses this username OR email.

        Used during registration to give a clean 409 instead of relying on a
        raw database integrity error.
        """
        stmt = select(User.id).where(
            (User.username == username) | (User.email == email)
        )
        result = await self._session.execute(stmt)
        return result.first() is not None
