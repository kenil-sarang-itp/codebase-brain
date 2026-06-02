"""
Database initialisation script.

Creates every table defined by the SQLAlchemy ORM models. The ORM is the single
source of truth for the schema, so running this script is all that is needed to
provision a fresh database — there is no separately-maintained `schema.sql` to
drift out of sync.

Run directly:

    python -m scripts.init_db

The backend Docker container runs this once on startup before serving traffic.
It is idempotent: `create_all` skips tables that already exist.
"""

from __future__ import annotations

import asyncio
import sys

# Ensure the app package is importable when run as a script.
sys.path.insert(0, "/app")

from app.core.logging import configure_logging, get_logger  # noqa: E402
from app.db.session import Base, _async_engine  # noqa: E402

# Importing the models module registers every table on `Base.metadata`.
import app.db.models  # noqa: F401,E402

logger = get_logger(__name__)


async def init_database() -> None:
    """Create all tables defined on the ORM metadata."""
    logger.info("Creating database tables from ORM metadata...")
    async with _async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _async_engine.dispose()

    table_names = ", ".join(sorted(Base.metadata.tables.keys()))
    logger.info("Database initialised. Tables: %s", table_names)


def main() -> None:
    """Script entry point."""
    configure_logging()
    try:
        asyncio.run(init_database())
    except Exception as exc:  # noqa: BLE001
        logger.exception("Database initialisation failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
