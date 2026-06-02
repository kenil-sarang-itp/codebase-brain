"""
Database session management.

Provides a single async SQLAlchemy engine and a session factory for the whole
application. The `get_db_session` dependency yields a session per request and
guarantees commit-on-success / rollback-on-error / close-always semantics, so
no route handler ever has to manage transactions by hand.

A separate *synchronous* engine is exposed for RQ workers, which run plain
functions outside an event loop.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config.settings import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)
_settings = get_settings()


class Base(DeclarativeBase):
    """Declarative base class shared by every ORM model."""


# --------------------------------------------------------------------------- #
# Async engine — used by the FastAPI request path.                            #
# --------------------------------------------------------------------------- #
# pool_pre_ping handles connections dropped by Postgres/idle timeouts: the pool
# transparently discards a dead connection instead of surfacing an error.
_async_engine = create_async_engine(
    _settings.database_url,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

_AsyncSessionFactory = async_sessionmaker(
    bind=_async_engine,
    class_=AsyncSession,
    expire_on_commit=False,  # objects stay usable after commit
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency that yields a transactional async DB session.

    Usage:
        async def route(db: AsyncSession = Depends(get_db_session)): ...

    The session is committed if the handler returns normally and rolled back
    if it raises. Either way the connection is returned to the pool.
    """
    session = _AsyncSessionFactory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# --------------------------------------------------------------------------- #
# Sync engine — used by RQ workers (no event loop available there).           #
#                                                                             #
# Created lazily: the API process never needs the synchronous (psycopg2)       #
# driver, so importing this module in the API does not require psycopg2 to be  #
# installed. The worker process triggers creation on first use.                #
# --------------------------------------------------------------------------- #
_sync_engine = None
_SyncSessionFactory = None


def _ensure_sync_engine() -> None:
    """Build the synchronous engine + session factory on first use."""
    global _sync_engine, _SyncSessionFactory
    if _sync_engine is None:
        _sync_engine = create_engine(
            _settings.sync_database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        _SyncSessionFactory = sessionmaker(
            bind=_sync_engine, expire_on_commit=False
        )


@contextmanager
def sync_session_scope() -> Generator[Session, None, None]:
    """Context manager yielding a transactional *synchronous* session.

    Used inside RQ worker tasks:

        with sync_session_scope() as db:
            repo = SomeRepository(db)
            ...
    """
    _ensure_sync_engine()
    session = _SyncSessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


async def dispose_engines() -> None:
    """Cleanly dispose the database engines on application shutdown."""
    await _async_engine.dispose()
    if _sync_engine is not None:
        _sync_engine.dispose()
    logger.info("Database engines disposed.")
