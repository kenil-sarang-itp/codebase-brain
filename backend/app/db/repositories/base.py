"""
Base repository (Repository pattern).

Repositories are the *only* place that touches the database. Services and
agents depend on repositories, never on SQLAlchemy directly. Benefits:

    * Single Responsibility — persistence logic is isolated from business logic.
    * Testability — a fake repository can be injected in unit tests.
    * Consistency — all CRUD goes through the same audited code path.

`BaseRepository` is generic over the ORM model type so concrete repositories
inherit type-safe `get`, `list`, `add`, and `delete` for free and only add
their own specialised queries.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import Base

# A type variable bound to the ORM base — gives concrete repos precise types.
ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Generic async CRUD repository for a single ORM model.

    Concrete repositories subclass this and set `model`:

        class UserRepository(BaseRepository[User]):
            model = User
    """

    #: The ORM model this repository manages. Set by every subclass.
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        """Store the unit-of-work session this repository operates within.

        The session is owned by the caller (typically the FastAPI dependency),
        so the repository never commits — it only stages changes. This keeps a
        whole request inside one atomic transaction.
        """
        self._session = session

    async def get(self, primary_key: object) -> ModelT | None:
        """Fetch a single row by primary key, or None if absent."""
        return await self._session.get(self.model, primary_key)

    async def list(self, *, limit: int = 100, offset: int = 0) -> list[ModelT]:
        """Return a page of rows. Caller controls limit/offset."""
        stmt = select(self.model).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def add(self, instance: ModelT) -> ModelT:
        """Stage a new instance for insertion and flush to obtain its PK.

        `flush` (not `commit`) sends the INSERT so any DB-generated values are
        populated, while leaving the surrounding transaction open.
        """
        self._session.add(instance)
        await self._session.flush()
        return instance

    async def flush(self) -> None:
        """Flush pending changes to the database without committing."""
        await self._session.flush()

    async def delete(self, primary_key: object) -> bool:
        """Delete a row by primary key. Returns True if a row was removed."""
        stmt = sa_delete(self.model).where(
            self._pk_column() == primary_key
        )
        result = await self._session.execute(stmt)
        return bool(result.rowcount)

    # ----------------------------------------------------------- internals --
    def _pk_column(self):
        """Return the model's single primary-key column for WHERE clauses."""
        return list(self.model.__table__.primary_key.columns)[0]
