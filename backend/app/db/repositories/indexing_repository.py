"""
Indexing persistence — sessions, jobs, and progress.

Encapsulates every query needed to drive and observe the indexing pipeline:
creating sessions, enqueuing job rows, advancing status, and reading live
progress for the `/index-status` endpoint.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import IndexingStatus, JobStatus
from app.db.models import IndexingJob, IndexingSession
from app.db.repositories.base import BaseRepository


class IndexingRepository(BaseRepository[IndexingSession]):
    """Repository for `indexing_sessions` and `indexing_jobs`."""

    model = IndexingSession

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ------------------------------------------------------------ sessions --
    async def create_session(self, repo_url: str) -> IndexingSession:
        """Create a fresh indexing session in the DISCOVERING state."""
        session = IndexingSession(
            repo_url=repo_url,
            status=IndexingStatus.DISCOVERING.value,
        )
        return await self.add(session)

    async def update_status(
        self,
        session_id: str,
        status: IndexingStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        """Advance a session to a new lifecycle status.

        On a terminal status (COMPLETE/FAILED) the `completed_at` timestamp is
        stamped automatically.
        """
        values: dict = {"status": status.value}
        if error_message is not None:
            values["error_message"] = error_message
        if status in (IndexingStatus.COMPLETE, IndexingStatus.FAILED):
            values["completed_at"] = datetime.now(timezone.utc)

        await self._session.execute(
            update(IndexingSession)
            .where(IndexingSession.session_id == session_id)
            .values(**values)
        )

    async def update_counters(
        self,
        session_id: str,
        *,
        total_files: int | None = None,
        processed_files: int | None = None,
        total_functions: int | None = None,
        processed_functions: int | None = None,
    ) -> None:
        """Patch any subset of the progress counters on a session."""
        values = {
            k: v
            for k, v in {
                "total_files": total_files,
                "processed_files": processed_files,
                "total_functions": total_functions,
                "processed_functions": processed_functions,
            }.items()
            if v is not None
        }
        if not values:
            return
        await self._session.execute(
            update(IndexingSession)
            .where(IndexingSession.session_id == session_id)
            .values(**values)
        )

    async def increment_processed_files(self, session_id: str) -> None:
        """Atomically add 1 to `processed_files` (safe under concurrent workers)."""
        await self._session.execute(
            update(IndexingSession)
            .where(IndexingSession.session_id == session_id)
            .values(processed_files=IndexingSession.processed_files + 1)
        )

    async def increment_processed_functions(self, session_id: str) -> None:
        """Atomically add 1 to `processed_functions`."""
        await self._session.execute(
            update(IndexingSession)
            .where(IndexingSession.session_id == session_id)
            .values(processed_functions=IndexingSession.processed_functions + 1)
        )

    # ---------------------------------------------------------------- jobs --
    async def add_job(self, job: IndexingJob) -> IndexingJob:
        """Persist a single indexing job row."""
        return await self.add(job)

    async def add_jobs(self, jobs: list[IndexingJob]) -> None:
        """Bulk-persist many job rows in one round trip."""
        self._session.add_all(jobs)
        await self._session.flush()

    async def set_job_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        error_message: str | None = None,
    ) -> None:
        """Update a job's status, stamping completion time when terminal."""
        values: dict = {"status": status.value}
        if error_message is not None:
            values["error_message"] = error_message
        if status in (JobStatus.COMPLETE, JobStatus.FAILED):
            values["completed_at"] = datetime.now(timezone.utc)
        await self._session.execute(
            update(IndexingJob)
            .where(IndexingJob.job_id == job_id)
            .values(**values)
        )

    async def job_counts_by_status(self, session_id: str) -> dict[str, int]:
        """Return {status: count} for all jobs in a session.

        The `/index-status` endpoint uses this to render a live breakdown.
        """
        stmt = (
            select(IndexingJob.status, func.count())
            .where(IndexingJob.session_id == session_id)
            .group_by(IndexingJob.status)
        )
        result = await self._session.execute(stmt)
        return {status: count for status, count in result.all()}
